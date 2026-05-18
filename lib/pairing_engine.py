from __future__ import annotations
import random
from typing import Optional

from .models import (
    Interviewer, Candidate, TimeSlot, PairingMode, InterviewerRole,
)
from .constraint_engine import is_eligible


def slot_available(interviewer: Interviewer, slot: TimeSlot) -> bool:
    return slot in interviewer.available_slots and slot not in interviewer.assigned_slots


def find_secondary(
    primary: Interviewer,
    candidate: Candidate,
    slot: TimeSlot,
    all_interviewers: list[Interviewer],
) -> Optional[Interviewer]:
    """Find a secondary interviewer based on the primary's pairing mode."""
    mode = primary.pairing_mode

    if mode == PairingMode.ONE_V_ONE:
        return None

    if mode == PairingMode.A_RANDOM_RANDOM:
        return _mode_a(primary, candidate, slot, all_interviewers)
    elif mode in (PairingMode.B_RANDOM_FIXED, PairingMode.B_PLUS_DEGRADABLE):
        return _mode_b(primary, candidate, slot, all_interviewers)
    elif mode == PairingMode.C_RANDOM_N_PICK_ONE:
        return _mode_c(primary, candidate, slot, all_interviewers)
    elif mode == PairingMode.D_ALL_PRIMARY:
        return _mode_d(primary, candidate, slot, all_interviewers)

    return None


def _mode_a(primary: Interviewer, candidate: Candidate, slot: TimeSlot,
            all_interviewers: list[Interviewer]) -> Optional[Interviewer]:
    """Mode A: random primary + random secondary from secondary pool."""
    eligible = [
        i for i in all_interviewers
        if i.email != primary.email
        and i.role == InterviewerRole.SECONDARY
        and is_eligible(i, candidate)
        and slot_available(i, slot)
        and _has_daily_capacity(i, slot)
    ]
    if not eligible:
        return None
    return random.choice(eligible)


def _mode_b(primary: Interviewer, candidate: Candidate, slot: TimeSlot,
            all_interviewers: list[Interviewer]) -> Optional[Interviewer]:
    """Mode B: fixed partner. Returns partner if available, None otherwise."""
    if not primary.partner_email:
        return None
    partner = next(
        (i for i in all_interviewers if i.email == primary.partner_email), None
    )
    if partner and slot_available(partner, slot) and _has_daily_capacity(partner, slot):
        return partner
    return None


def _mode_c(primary: Interviewer, candidate: Candidate, slot: TimeSlot,
            all_interviewers: list[Interviewer]) -> Optional[Interviewer]:
    """Mode C: pick one from N partner candidates."""
    partner_emails = set()
    if primary.partner_email:
        partner_emails.add(primary.partner_email)
    if primary.partner_names and primary.partner_names != "随机":
        for name in primary.partner_names.split(","):
            name = name.strip()
            match = next(
                (i for i in all_interviewers if i.name == name), None
            )
            if match:
                partner_emails.add(match.email)

    eligible = [
        i for i in all_interviewers
        if i.email in partner_emails
        and slot_available(i, slot)
        and _has_daily_capacity(i, slot)
    ]
    if not eligible:
        return None
    return random.choice(eligible)


def _mode_d(primary: Interviewer, candidate: Candidate, slot: TimeSlot,
            all_interviewers: list[Interviewer]) -> Optional[Interviewer]:
    """Mode D: all are primaries, pick another eligible primary."""
    eligible = [
        i for i in all_interviewers
        if i.email != primary.email
        and i.role == InterviewerRole.PRIMARY
        and is_eligible(i, candidate)
        and slot_available(i, slot)
        and _has_daily_capacity(i, slot)
    ]
    if not eligible:
        return None
    return random.choice(eligible)


def can_degrade_to_1v1(primary: Interviewer) -> bool:
    return primary.pairing_mode == PairingMode.B_PLUS_DEGRADABLE


def _has_daily_capacity(interviewer: Interviewer, slot: TimeSlot) -> bool:
    count = interviewer.assigned_count_by_date.get(slot.date, 0)
    return count < interviewer.daily_limit
