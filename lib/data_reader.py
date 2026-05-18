from __future__ import annotations
import json
import re
import subprocess
from datetime import date, time, datetime
from typing import Optional

from .models import (
    Interviewer, Candidate, TimeSlot,
    InterviewFormat, PairingMode, InterviewerRole,
)

CURRENT_YEAR = datetime.now().year


def extract_doc_id(url: str) -> str:
    path = url.split("?")[0]
    return path.rstrip("/").split("/")[-1]


def run_popo_cli(doc_id: str, command: dict) -> dict:
    cmd_json = json.dumps(command, ensure_ascii=False)
    result = subprocess.run(
        ["popo-cli", "popo", "doc_execute_table", f"docId={doc_id}",
         f"command={cmd_json}"],
        capture_output=True, text=True, timeout=60,
    )
    raw = json.loads(result.stdout)
    data = raw
    while isinstance(data, dict) and "data" in data:
        inner = data["data"]
        if isinstance(inner, dict) and ("sheets" in inner or "values" in inner):
            return inner
        data = inner
    return data


def get_full_data(doc_id: str) -> dict:
    return run_popo_cli(doc_id, {"type": "workbook.getFullData", "payload": {}})


def batch_get_cells(doc_id: str, sheet_id: str, row: int, col: int,
                    row_count: int, col_count: int) -> list[list]:
    result = run_popo_cli(doc_id, {
        "type": "sheet.batchGetCells",
        "payload": {
            "sheetId": sheet_id,
            "row": row, "col": col,
            "rowCount": row_count, "colCount": col_count,
        }
    })
    return result.get("values", [])


def find_sheet(sheets: list[dict], title_contains: str) -> Optional[dict]:
    # Prefer exact match first
    for s in sheets:
        if s.get("title", "") == title_contains:
            return s
    # Then prefer shortest title containing the substring (most specific match)
    matches = [s for s in sheets if title_contains in s.get("title", "")]
    if matches:
        matches.sort(key=lambda s: len(s.get("title", "")))
        return matches[0]
    return None


def parse_date_from_header(header: str) -> Optional[date]:
    """Parse date strings like '4.24 周五' or '5.6 周一' into date objects."""
    if not header:
        return None
    m = re.match(r"(\d{1,2})\.(\d{1,2})", header.strip())
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    return date(CURRENT_YEAR, month, day)


def parse_time_slots(time_str: str, slot_date: date) -> list[TimeSlot]:
    """Parse '10:10-11:10,11:10-12:10' into TimeSlot list."""
    if not time_str or not isinstance(time_str, str):
        return []
    slots = []
    for segment in time_str.split(","):
        segment = segment.strip()
        m = re.match(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", segment)
        if m:
            start = time(int(m.group(1)), int(m.group(2)))
            end = time(int(m.group(3)), int(m.group(4)))
            slots.append(TimeSlot(date=slot_date, start=start, end=end))
    return slots


def parse_format(val: Optional[str]) -> InterviewFormat:
    if not val:
        return InterviewFormat.ANY
    val = val.strip()
    if "线上" in val:
        return InterviewFormat.ONLINE
    if "线下" in val:
        return InterviewFormat.OFFLINE
    return InterviewFormat.ANY


def parse_daily_limit(val: Optional[str]) -> int:
    if not val:
        return 4
    m = re.search(r"(\d+)", str(val))
    return int(m.group(1)) if m else 4


def parse_duration(val: Optional[str]) -> int:
    if not val:
        return 30
    m = re.search(r"(\d+)", str(val))
    return int(m.group(1)) if m else 30


def parse_pairing_mode(rule: Optional[str], desc: Optional[str]) -> PairingMode:
    if not rule or rule.strip() == "1V1":
        return PairingMode.ONE_V_ONE
    if not desc:
        return PairingMode.A_RANDOM_RANDOM
    desc = desc.strip()
    if "均为主面" in desc:
        return PairingMode.D_ALL_PRIMARY
    if "副面N选一" in desc:
        return PairingMode.C_RANDOM_N_PICK_ONE
    if "副面固定搭配" in desc:
        if "主面可单独面试" in desc:
            return PairingMode.B_PLUS_DEGRADABLE
        return PairingMode.B_RANDOM_FIXED
    return PairingMode.A_RANDOM_RANDOM


def read_interviewers(doc_id: str, sheets: list[dict]) -> list[Interviewer]:
    tab1 = find_sheet(sheets, "面试官时间收集")
    tab2 = find_sheet(sheets, "面试官时间")
    if not tab1 or not tab2:
        raise ValueError("Cannot find 面试官时间收集 or 面试官时间 sheets")

    tab1_data = batch_get_cells(doc_id, tab1["sheetId"], row=2, col=0,
                                row_count=min(tab1["rowCount"] - 2, 200), col_count=18)
    tab2_data = batch_get_cells(doc_id, tab2["sheetId"], row=0, col=0,
                                row_count=min(tab2["rowCount"], 200),
                                col_count=min(tab2["colCount"], 50))

    # Parse Tab2 date headers
    date_row = tab2_data[0] if tab2_data else []
    dates = []
    col_idx = 3
    while col_idx < len(date_row):
        d = parse_date_from_header(str(date_row[col_idx]) if date_row[col_idx] else "")
        if d:
            dates.append((col_idx, d))
        col_idx += 3

    # Build email → time slots mapping from Tab2
    email_to_slots: dict[str, list[TimeSlot]] = {}
    for row in tab2_data[2:]:  # skip header rows
        if not row or not row[2]:
            continue
        email = str(row[2]).strip()
        slots = []
        for start_col, slot_date in dates:
            for offset in range(3):  # 上午/下午/晚上
                cell_idx = start_col + offset
                if cell_idx < len(row) and row[cell_idx]:
                    slots.extend(parse_time_slots(str(row[cell_idx]), slot_date))
        email_to_slots[email] = slots

    # Parse Tab1 interviewer attributes
    interviewers = []
    header = tab1_data[0] if tab1_data else []
    for row in tab1_data[2:]:  # skip header + empty row
        if not row or not row[1]:
            continue
        seq_val = row[0]
        if not seq_val or (isinstance(seq_val, str) and not seq_val.strip().isdigit()):
            continue

        email = str(row[2]).strip() if row[2] else ""
        interviewer = Interviewer(
            seq=int(seq_val) if seq_val else 0,
            name=str(row[1]).strip(),
            email=email,
            round=str(row[3]).strip() if row[3] else "",
            format=parse_format(row[4]),
            pairing_rule=str(row[5]).strip() if row[5] else "1V1",
            pairing_mode=parse_pairing_mode(
                str(row[5]).strip() if row[5] else None,
                str(row[6]).strip() if row[6] else None,
            ),
            partner_names=str(row[7]).strip() if row[7] else "",
            partner_email=str(row[8]).strip() if row[8] else None,
            role=InterviewerRole.SECONDARY if row[9] and "副" in str(row[9]) else InterviewerRole.PRIMARY,
            group=str(row[10]).strip() if row[10] else "",
            position=str(row[11]).strip() if row[11] else "",
            direction=str(row[12]).strip() if row[12] else None,
            session_duration=parse_duration(row[13]),
            interval=parse_duration(row[14]),
            city=str(row[15]).strip() if row[15] else "不限",
            need_room=row[16] == "需要" if row[16] else False,
            daily_limit=parse_daily_limit(row[17]),
            available_slots=email_to_slots.get(email, []),
        )
        interviewers.append(interviewer)

    return interviewers


def read_candidates(doc_id: str, sheets: list[dict]) -> list[Candidate]:
    tab3 = find_sheet(sheets, "候选人清单")
    if not tab3:
        raise ValueError("Cannot find 候选人清单 sheet")

    data = batch_get_cells(doc_id, tab3["sheetId"], row=1, col=0,
                           row_count=min(tab3["rowCount"] - 1, 1600), col_count=14)

    candidates = []
    for i, row in enumerate(data[1:], start=2):  # skip header row
        if not row or not row[0]:
            continue
        name = str(row[0]).strip()
        if not name:
            continue

        priority = 0.0
        if row[2] is not None:
            try:
                priority = float(row[2])
            except (ValueError, TypeError):
                pass

        candidates.append(Candidate(
            name=name,
            phone=str(row[1]).strip() if row[1] else "",
            priority_score=priority,
            position=str(row[3]).strip() if row[3] else "",
            direction=str(row[4]).strip() if row[4] else None,
            school=str(row[5]).strip() if row[5] else None,
            country=str(row[6]).strip() if row[6] else None,
            province=str(row[7]).strip() if row[7] else None,
            city_location=str(row[8]).strip() if row[8] else None,
            round=str(row[9]).strip() if row[9] else "",
            interview_city=str(row[10]).strip() if row[10] else None,
            format=parse_format(row[11]),
            status=str(row[12]).strip() if row[12] else None,
            invite_status=str(row[13]).strip() if row[13] else None,
            row_index=i,
        ))

    return candidates


def load_all_data(doc_url_or_id: str) -> tuple[list[Interviewer], list[Candidate]]:
    if "docs.popo.netease.com" in doc_url_or_id:
        doc_id = extract_doc_id(doc_url_or_id)
    else:
        doc_id = doc_url_or_id

    full_data = get_full_data(doc_id)
    sheets = full_data["sheets"]

    interviewers = read_interviewers(doc_id, sheets)
    candidates = read_candidates(doc_id, sheets)

    return interviewers, candidates
