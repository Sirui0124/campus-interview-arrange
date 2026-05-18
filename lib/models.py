from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, time
from enum import Enum
from typing import Optional


class InterviewFormat(Enum):
    ONLINE = "线上面试"
    OFFLINE = "线下面试"
    ANY = "不限"


class PairingMode(Enum):
    ONE_V_ONE = "1V1"
    A_RANDOM_RANDOM = "主面随机，副面随机"
    B_RANDOM_FIXED = "主面随机，副面固定搭配"
    B_PLUS_DEGRADABLE = "主面随机，副面固定搭配,主面可单独面试"
    C_RANDOM_N_PICK_ONE = "主面随机，副面N选一"
    D_ALL_PRIMARY = "均为主面，主面随机"


class InterviewerRole(Enum):
    PRIMARY = "主面"
    SECONDARY = "副面"


@dataclass
class TimeSlot:
    date: date
    start: time
    end: time

    @property
    def date_str(self) -> str:
        return self.date.strftime("%m.%d")

    @property
    def time_str(self) -> str:
        return f"{self.start.strftime('%H:%M')}-{self.end.strftime('%H:%M')}"

    def __hash__(self):
        return hash((self.date, self.start, self.end))

    def __eq__(self, other):
        if not isinstance(other, TimeSlot):
            return False
        return self.date == other.date and self.start == other.start and self.end == other.end


@dataclass
class Interviewer:
    seq: int
    name: str
    email: str
    round: str
    format: InterviewFormat
    pairing_rule: str  # "1V1" or "2V1"
    pairing_mode: PairingMode
    partner_names: str  # "随机" or specific name(s)
    partner_email: Optional[str]
    role: InterviewerRole
    group: str
    position: str
    direction: Optional[str]
    session_duration: int  # minutes
    interval: int  # minutes
    city: str
    need_room: bool
    daily_limit: int
    available_slots: list[TimeSlot] = field(default_factory=list)
    # Runtime state
    assigned_count_by_date: dict = field(default_factory=dict)
    assigned_slots: set = field(default_factory=set)


@dataclass
class Candidate:
    name: str
    phone: str
    priority_score: float
    position: str
    direction: Optional[str]
    school: Optional[str]
    country: Optional[str]
    province: Optional[str]
    city_location: Optional[str]
    round: str
    interview_city: Optional[str]
    format: InterviewFormat
    status: Optional[str]  # "已安排" or None
    invite_status: Optional[str]
    row_index: int  # original row in spreadsheet (for write-back)

    @property
    def is_overseas(self) -> bool:
        return self.country is not None and self.country != "" and self.country != "中国"

    @property
    def effective_priority(self) -> float:
        if self.priority_score == 0:
            return 2.5  # mid-value for unscored
        return self.priority_score


@dataclass
class ScheduledSession:
    primary: Interviewer
    secondary: Optional[Interviewer]
    candidate: Candidate
    slot: TimeSlot
    format: InterviewFormat
    room_campus: Optional[str] = None
    room_floor_name: Optional[str] = None
    calendar_conflict: bool = False
    invite_status: str = "NA"
    calendar_id: Optional[str] = None
