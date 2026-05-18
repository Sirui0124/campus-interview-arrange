from __future__ import annotations
from collections import defaultdict
from datetime import date
from typing import Optional

from .models import (
    Interviewer, Candidate, TimeSlot, ScheduledSession,
    InterviewFormat, PairingMode, InterviewerRole,
)
from .constraint_engine import is_eligible
from .pairing_engine import find_secondary, can_degrade_to_1v1, slot_available

TIMEZONE_OFFSETS = {
    "美国": -16, "加拿大": -16, "英国": -8, "法国": -7, "德国": -7,
    "澳大利亚": 2, "日本": 1, "韩国": 1, "新加坡": 0, "马来西亚": 0,
    "泰国": -1, "印度": -2.5, "俄罗斯": -5,
}


def get_timezone_offset(country: Optional[str]) -> float:
    if not country or country == "中国":
        return 0
    for key, offset in TIMEZONE_OFFSETS.items():
        if key in country:
            return offset
    return 0


def score_slot_for_overseas(candidate: Candidate, slot: TimeSlot) -> float:
    if not candidate.is_overseas:
        return 1.0
    offset = get_timezone_offset(candidate.country)
    local_hour = slot.start.hour + offset
    local_hour = local_hour % 24
    if 9 <= local_hour <= 18:
        return 1.0
    elif 7 <= local_hour <= 21:
        return 0.5
    return 0.1


def determine_format(interviewer: Interviewer, candidate: Candidate) -> InterviewFormat:
    if interviewer.format == InterviewFormat.ANY:
        return candidate.format if candidate.format != InterviewFormat.ANY else InterviewFormat.ONLINE
    return interviewer.format


def schedule(
    interviewers: list[Interviewer],
    candidates: list[Candidate],
) -> tuple[list[ScheduledSession], list[Candidate]]:
    """
    Core scheduling algorithm.
    Returns (scheduled_sessions, unscheduled_candidates).
    """
    # Filter out already-scheduled candidates
    pending = [c for c in candidates if c.status != "已安排"]
    pending.sort(key=lambda c: -c.effective_priority)

    # Separate primary interviewers (for greedy iteration)
    primaries = [i for i in interviewers if i.role == InterviewerRole.PRIMARY]

    results: list[ScheduledSession] = []
    unscheduled: list[Candidate] = []

    for candidate in pending:
        assigned = _try_assign(candidate, primaries, interviewers)
        if assigned:
            results.append(assigned)
        else:
            unscheduled.append(candidate)

    # Post-processing: apply alternation within same interviewer+date
    _apply_alternation(results)

    return results, unscheduled


def _try_assign(
    candidate: Candidate,
    primaries: list[Interviewer],
    all_interviewers: list[Interviewer],
) -> Optional[ScheduledSession]:
    """Try to assign a candidate to an interviewer slot."""
    eligible = [i for i in primaries if is_eligible(i, candidate)]

    # Sort by remaining capacity (prefer less loaded interviewers for balance)
    eligible.sort(key=lambda i: sum(i.assigned_count_by_date.values()))

    for primary in eligible:
        # Sort slots: overseas candidates prefer daytime in their timezone
        available = [s for s in primary.available_slots if slot_available(primary, s)]
        available.sort(key=lambda s: -score_slot_for_overseas(candidate, s))

        for slot in available:
            # Check daily capacity
            if primary.assigned_count_by_date.get(slot.date, 0) >= primary.daily_limit:
                continue

            # Handle 2V1 pairing
            if primary.pairing_mode == PairingMode.ONE_V_ONE:
                _commit_assignment(primary, None, slot)
                return ScheduledSession(
                    primary=primary,
                    secondary=None,
                    candidate=candidate,
                    slot=slot,
                    format=determine_format(primary, candidate),
                )

            secondary = find_secondary(primary, candidate, slot, all_interviewers)
            if secondary:
                _commit_assignment(primary, secondary, slot)
                return ScheduledSession(
                    primary=primary,
                    secondary=secondary,
                    candidate=candidate,
                    slot=slot,
                    format=determine_format(primary, candidate),
                )
            elif can_degrade_to_1v1(primary):
                _commit_assignment(primary, None, slot)
                return ScheduledSession(
                    primary=primary,
                    secondary=None,
                    candidate=candidate,
                    slot=slot,
                    format=determine_format(primary, candidate),
                )

    return None


def _commit_assignment(primary: Interviewer, secondary: Optional[Interviewer], slot: TimeSlot):
    primary.assigned_slots.add(slot)
    primary.assigned_count_by_date[slot.date] = primary.assigned_count_by_date.get(slot.date, 0) + 1
    if secondary:
        secondary.assigned_slots.add(slot)
        secondary.assigned_count_by_date[slot.date] = secondary.assigned_count_by_date.get(slot.date, 0) + 1


def _apply_alternation(results: list[ScheduledSession]):
    """
    Post-processing: for the same primary interviewer on the same day,
    interleave high and low priority candidates across time slots.
    """
    grouped: dict[tuple[str, date], list[ScheduledSession]] = defaultdict(list)
    for session in results:
        key = (session.primary.email, session.slot.date)
        grouped[key].append(session)

    for key, sessions in grouped.items():
        if len(sessions) <= 1:
            continue

        # Sort by priority descending
        by_priority = sorted(sessions, key=lambda s: -s.candidate.effective_priority)
        # Split into high and low halves
        mid = len(by_priority) // 2
        high = by_priority[:mid]
        low = by_priority[mid:]

        # Interleave: high, low, high, low, ...
        interleaved = []
        for i in range(max(len(high), len(low))):
            if i < len(high):
                interleaved.append(high[i])
            if i < len(low):
                interleaved.append(low[i])

        # Collect the time slots in chronological order
        chronological_slots = sorted(
            [s.slot for s in sessions], key=lambda sl: (sl.date, sl.start)
        )

        # Reassign slots
        for session, slot in zip(interleaved, chronological_slots):
            session.slot = slot
