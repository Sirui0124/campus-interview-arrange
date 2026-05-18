from __future__ import annotations
from typing import Optional

from .models import Interviewer, Candidate, InterviewFormat


def format_compatible(i_fmt: InterviewFormat, c_fmt: InterviewFormat) -> bool:
    if i_fmt == InterviewFormat.ANY or c_fmt == InterviewFormat.ANY:
        return True
    return i_fmt == c_fmt


def city_compatible(i_city: str, c_city: Optional[str]) -> bool:
    if not i_city or i_city == "不限":
        return True
    if not c_city or c_city == "不限":
        return True
    return i_city == c_city


def position_compatible(interviewer: Interviewer, candidate: Candidate) -> bool:
    if not interviewer.position or not candidate.position:
        return True
    # The interviewer's position field may contain the candidate's position as substring
    # e.g. interviewer: "游戏研发工程师（客户端&服务端）" should match candidate: "游戏研发工程师（客户端&服务端）"
    if interviewer.position == candidate.position:
        return True
    if candidate.position in interviewer.position or interviewer.position in candidate.position:
        return True
    return False


def direction_compatible(interviewer: Interviewer, candidate: Candidate) -> bool:
    if not interviewer.direction or not candidate.direction:
        return True
    return interviewer.direction == candidate.direction


def is_eligible(interviewer: Interviewer, candidate: Candidate) -> bool:
    if interviewer.round != candidate.round:
        return False
    if not position_compatible(interviewer, candidate):
        return False
    if not direction_compatible(interviewer, candidate):
        return False
    if not format_compatible(interviewer.format, candidate.format):
        return False
    if not city_compatible(interviewer.city, candidate.interview_city):
        return False
    return True
