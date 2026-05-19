from __future__ import annotations
from collections import defaultdict
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from .models import (
    Interviewer, Candidate, TimeSlot, ScheduledSession,
    InterviewFormat, PairingMode, InterviewerRole, PriorityTier,
)
from .constraint_engine import is_eligible
from .pairing_engine import find_secondary, can_degrade_to_1v1, slot_available

CHINA_TZ = ZoneInfo("Asia/Shanghai")

CITY_TIMEZONES = {
    "伦敦": "Europe/London",
    "牛津": "Europe/London",
    "剑桥": "Europe/London",
    "波士顿": "America/New_York",
    "普林斯顿": "America/New_York",
    "纽约": "America/New_York",
    "费城": "America/New_York",
    "伊萨卡": "America/New_York",
    "旧金山": "America/Los_Angeles",
    "伯克利": "America/Los_Angeles",
    "帕罗奥图": "America/Los_Angeles",
    "帕洛阿尔托": "America/Los_Angeles",
    "paloalto": "America/Los_Angeles",
    "蒙特利尔": "America/Toronto",
    "悉尼": "Australia/Sydney",
    "慕尼黑": "Europe/Berlin",
    "苏黎世": "Europe/Zurich",
}

PROVINCE_TIMEZONES = {
    "加利福尼亚": "America/Los_Angeles",
    "california": "America/Los_Angeles",
    "ca": "America/Los_Angeles",
    "马萨诸塞": "America/New_York",
    "massachusetts": "America/New_York",
    "ma": "America/New_York",
    "纽约州": "America/New_York",
    "newyork": "America/New_York",
    "ny": "America/New_York",
    "新泽西": "America/New_York",
    "newjersey": "America/New_York",
    "nj": "America/New_York",
    "宾夕法尼亚": "America/New_York",
    "pennsylvania": "America/New_York",
    "pa": "America/New_York",
    "魁北克": "America/Toronto",
    "quebec": "America/Toronto",
    "新南威尔士": "Australia/Sydney",
    "newsouthwales": "Australia/Sydney",
    "nsw": "Australia/Sydney",
}

COUNTRY_TIMEZONES = {
    "中国": "Asia/Shanghai",
    "英国": "Europe/London",
    "德国": "Europe/Berlin",
    "瑞士": "Europe/Zurich",
    "澳大利亚": "Australia/Sydney",
    "加拿大": "America/Toronto",
    "美国": "America/New_York",
    "法国": "Europe/Paris",
    "日本": "Asia/Tokyo",
    "韩国": "Asia/Seoul",
    "新加坡": "Asia/Singapore",
    "马来西亚": "Asia/Kuala_Lumpur",
    "泰国": "Asia/Bangkok",
    "印度": "Asia/Kolkata",
    "俄罗斯": "Europe/Moscow",
}


def _normalize_location(value: Optional[str]) -> str:
    if not value:
        return ""
    return "".join(str(value).strip().lower().split())


def _match_timezone(value: Optional[str], mapping: dict[str, str]) -> Optional[ZoneInfo]:
    normalized = _normalize_location(value)
    if not normalized:
        return None
    for key, timezone_name in mapping.items():
        if _normalize_location(key) in normalized:
            return ZoneInfo(timezone_name)
    return None


def get_candidate_timezone(candidate: Candidate) -> ZoneInfo:
    return (
        _match_timezone(candidate.city_location, CITY_TIMEZONES)
        or _match_timezone(candidate.province, PROVINCE_TIMEZONES)
        or _match_timezone(candidate.country, COUNTRY_TIMEZONES)
        or CHINA_TZ
    )


def score_slot_for_overseas(candidate: Candidate, slot: TimeSlot) -> float:
    candidate_tz = get_candidate_timezone(candidate)
    if candidate_tz == CHINA_TZ:
        return 1.0
    beijing_time = datetime.combine(slot.date, slot.start, tzinfo=CHINA_TZ)
    local_hour = beijing_time.astimezone(candidate_tz).hour
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
    Core scheduling algorithm with two phases:
      Phase 1: HIGH + MID tier candidates compete first (high tier always wins ties).
      Phase 2: LOW tier candidates only fill the remaining slots.
    Within each phase candidates are sorted by score descending, so a higher score
    always gets first pick at any contested slot.
    """
    # Filter out already-scheduled candidates
    pending = [c for c in candidates if c.status != "已安排"]

    _assign_tiers(pending)

    primaries = [i for i in interviewers if i.role == InterviewerRole.PRIMARY]
    results: list[ScheduledSession] = []
    unscheduled: list[Candidate] = []

    # Phase 1: HIGH then MID, both ordered by score desc
    phase1 = [c for c in pending if c.tier in (PriorityTier.HIGH, PriorityTier.MID)]
    phase1.sort(key=lambda c: (
        0 if c.tier == PriorityTier.HIGH else 1,
        -c.effective_priority,
    ))
    for candidate in phase1:
        assigned = _try_assign(candidate, primaries, interviewers)
        if assigned:
            results.append(assigned)
        else:
            unscheduled.append(candidate)

    # Phase 2: LOW tier only fills leftover slots
    phase2 = [c for c in pending if c.tier == PriorityTier.LOW]
    phase2.sort(key=lambda c: -c.effective_priority)
    for candidate in phase2:
        assigned = _try_assign(candidate, primaries, interviewers)
        if assigned:
            results.append(assigned)
        else:
            unscheduled.append(candidate)

    # Post-processing: same interviewer + same day → no back-to-back HIGH
    _apply_alternation(results)

    return results, unscheduled


def _assign_tiers(candidates: list[Candidate]) -> None:
    """
    Split candidates into HIGH / MID / LOW tiers by percentile.

    Rules:
      - Any score == 0 is forced into LOW (bottom-of-the-barrel fill only).
      - Remaining candidates are split into thirds by rank: top 1/3 = HIGH,
        middle 1/3 = MID, bottom 1/3 = LOW.
      - Ties at the tier boundary are broken in favor of the higher tier
        (e.g. a candidate with the same score as one in HIGH is also HIGH).
    """
    scored = [c for c in candidates if c.effective_priority > 0]
    zeros = [c for c in candidates if c.effective_priority <= 0]
    for c in zeros:
        c.tier = PriorityTier.LOW

    if not scored:
        return

    sorted_by_score = sorted(scored, key=lambda c: -c.effective_priority)
    n = len(sorted_by_score)
    high_cut = max(1, n // 3)
    mid_cut = max(high_cut + 1, (2 * n) // 3) if n >= 2 else high_cut

    high_threshold = sorted_by_score[high_cut - 1].effective_priority
    mid_threshold = (
        sorted_by_score[mid_cut - 1].effective_priority
        if mid_cut - 1 < n else sorted_by_score[-1].effective_priority
    )

    for c in sorted_by_score:
        score = c.effective_priority
        if score >= high_threshold:
            c.tier = PriorityTier.HIGH
        elif score >= mid_threshold:
            c.tier = PriorityTier.MID
        else:
            c.tier = PriorityTier.LOW


def _try_assign(
    candidate: Candidate,
    primaries: list[Interviewer],
    all_interviewers: list[Interviewer],
) -> Optional[ScheduledSession]:
    """Try to assign a candidate to an interviewer slot."""
    eligible = [i for i in primaries if is_eligible(i, candidate)]

    # Prefer interviewers whose remaining slots are better for the candidate's timezone,
    # then use current load as a balancing tie-breaker.
    eligible.sort(
        key=lambda i: (
            -_best_slot_score(candidate, i),
            sum(i.assigned_count_by_date.values()),
        )
    )

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


def _best_slot_score(candidate: Candidate, interviewer: Interviewer) -> float:
    available = [s for s in interviewer.available_slots if slot_available(interviewer, s)]
    if not available:
        return 0.0
    return max(score_slot_for_overseas(candidate, slot) for slot in available)


def _commit_assignment(primary: Interviewer, secondary: Optional[Interviewer], slot: TimeSlot):
    primary.assigned_slots.add(slot)
    primary.assigned_count_by_date[slot.date] = primary.assigned_count_by_date.get(slot.date, 0) + 1
    if secondary:
        secondary.assigned_slots.add(slot)
        secondary.assigned_count_by_date[slot.date] = secondary.assigned_count_by_date.get(slot.date, 0) + 1


def _apply_alternation(results: list[ScheduledSession]):
    """
    Post-processing: for the same primary interviewer on the same day,
    interleave high and low priority candidates across time slots while keeping
    every existing interviewer-slot constraint valid.
    """
    grouped: dict[tuple[str, date], list[ScheduledSession]] = defaultdict(list)
    for session in results:
        key = (session.primary.email, session.slot.date)
        grouped[key].append(session)

    for key, sessions in grouped.items():
        if len(sessions) <= 1:
            continue

        desired_order = _interleave_by_priority(sessions)
        chronological_slots = sorted(
            [s.slot for s in sessions], key=lambda sl: (sl.date, sl.start)
        )
        occupied = _occupied_slots(results, exclude=sessions)
        assignment = _find_feasible_slot_assignment(
            desired_order,
            chronological_slots,
            occupied,
        )
        if not assignment:
            continue

        for session, slot in assignment:
            session.slot = slot


def _interleave_by_priority(sessions: list[ScheduledSession]) -> list[ScheduledSession]:
    """
    Order sessions so that no two HIGH-tier candidates sit back-to-back when
    enough non-HIGH sessions exist to separate them. Within each tier the
    original score-desc order is preserved (highest score first).
    """
    by_score = sorted(sessions, key=lambda s: -s.candidate.effective_priority)
    highs = [s for s in by_score if s.candidate.tier == PriorityTier.HIGH]
    others = [s for s in by_score if s.candidate.tier != PriorityTier.HIGH]

    # If there are not enough non-HIGH to separate every HIGH pair, fall back
    # to score-desc and accept some HIGH adjacency (better than dropping people).
    if not highs or len(others) < len(highs) - 1:
        return by_score

    interleaved: list[ScheduledSession] = []
    others_iter = iter(others)
    for idx, high in enumerate(highs):
        interleaved.append(high)
        if idx < len(highs) - 1:
            interleaved.append(next(others_iter))
    # Append any leftover non-HIGH sessions at the end
    interleaved.extend(others_iter)
    return interleaved


def _occupied_slots(
    results: list[ScheduledSession],
    exclude: list[ScheduledSession],
) -> set[tuple[str, TimeSlot]]:
    excluded_ids = {id(session) for session in exclude}
    occupied = set()
    for session in results:
        if id(session) in excluded_ids:
            continue
        for email in _participant_emails(session):
            occupied.add((email, session.slot))
    return occupied


def _participant_emails(session: ScheduledSession) -> list[str]:
    emails = [session.primary.email]
    if session.secondary:
        emails.append(session.secondary.email)
    return emails


def _session_can_use_slot(
    session: ScheduledSession,
    slot: TimeSlot,
    occupied: set[tuple[str, TimeSlot]],
) -> bool:
    if slot not in session.primary.available_slots:
        return False
    if session.secondary and slot not in session.secondary.available_slots:
        return False
    return all((email, slot) not in occupied for email in _participant_emails(session))


def _find_feasible_slot_assignment(
    desired_order: list[ScheduledSession],
    chronological_slots: list[TimeSlot],
    occupied: set[tuple[str, TimeSlot]],
) -> Optional[list[tuple[ScheduledSession, TimeSlot]]]:
    assignment: list[tuple[ScheduledSession, TimeSlot]] = []

    def search(slot_index: int, remaining: list[ScheduledSession]) -> bool:
        if slot_index == len(chronological_slots):
            return True

        slot = chronological_slots[slot_index]
        preferred = desired_order[slot_index]
        remaining_ids = {id(session) for session in remaining}
        candidates = [preferred] + [
            session for session in desired_order
            if id(session) in remaining_ids and session is not preferred
        ]

        for session in candidates:
            if id(session) not in remaining_ids:
                continue
            if not _session_can_use_slot(session, slot, occupied):
                continue

            used = [(email, slot) for email in _participant_emails(session)]
            for item in used:
                occupied.add(item)
            assignment.append((session, slot))

            next_remaining = [s for s in remaining if s is not session]
            if search(slot_index + 1, next_remaining):
                return True

            assignment.pop()
            for item in used:
                occupied.remove(item)
        return False

    if search(0, desired_order[:]):
        return assignment
    return None
