from __future__ import annotations
import json
import subprocess
from typing import Optional

from .models import ScheduledSession, InterviewFormat
from .data_reader import find_sheet

OUTPUT_HEADERS = [
    "主面", "主面邮箱", "副面", "副面邮箱",
    "面试官日期", "面试官时间", "面试形式",
    "候选人姓名", "候选人手机", "面试岗位", "面试岗位方向",
    "面试城市", "优先级评分",
    "会议室园区", "会议室楼层-名称",
    "面试官是否日历冲突", "会邀状态", "calendar_id",
]


def format_session(session: ScheduledSession) -> list:
    fmt_str = session.format.value if session.format != InterviewFormat.ANY else "线上面试"
    return [
        session.primary.name,
        session.primary.email,
        session.secondary.name if session.secondary else "",
        session.secondary.email if session.secondary else "",
        session.slot.date.strftime("%m/%d"),
        session.slot.time_str,
        fmt_str,
        session.candidate.name,
        session.candidate.phone,
        session.candidate.position,
        session.candidate.direction or "",
        session.candidate.interview_city or "",
        session.candidate.priority_score,
        session.room_campus or "",
        session.room_floor_name or "",
        "",  # 面试官是否日历冲突 (TBD)
        session.invite_status,
        session.calendar_id or "",
    ]


def batch_set_cells(doc_id: str, sheet_id: str, cells: list[dict]):
    cmd = json.dumps({
        "type": "sheet.batchSetCells",
        "payload": {"sheetId": sheet_id, "cells": cells}
    }, ensure_ascii=False)
    subprocess.run(
        ["popo-cli", "popo", "doc_execute_table", f"docId={doc_id}",
         f"command={cmd}"],
        capture_output=True, text=True, timeout=60,
    )


def write_results(
    doc_id: str,
    sheets: list[dict],
    results: list[ScheduledSession],
    target_sheet_title: str = "约面结果",
):
    sheet = find_sheet(sheets, target_sheet_title)
    if not sheet:
        raise ValueError(f"Cannot find sheet with title containing '{target_sheet_title}'")

    sheet_id = sheet["sheetId"]
    all_cells = []

    # Header row
    for col, header in enumerate(OUTPUT_HEADERS):
        all_cells.append({"row": 0, "col": col, "value": header})

    # Data rows
    for row_idx, session in enumerate(results, start=1):
        row_data = format_session(session)
        for col_idx, value in enumerate(row_data):
            all_cells.append({"row": row_idx, "col": col_idx, "value": value})

    # Batch write in groups of 50
    for i in range(0, len(all_cells), 50):
        batch = all_cells[i:i + 50]
        batch_set_cells(doc_id, sheet_id, batch)

    return len(results)


def print_results_summary(results: list[ScheduledSession], unscheduled: list):
    print(f"\n{'='*60}")
    print(f"排面完成：已安排 {len(results)} 人，未排上 {len(unscheduled)} 人")
    print(f"{'='*60}")

    if results:
        print(f"\n{'─'*60}")
        print(f"{'主面':<8} {'副面':<8} {'日期':<6} {'时间':<12} {'候选人':<8} {'岗位':<20} {'优先级'}")
        print(f"{'─'*60}")
        for s in results[:30]:
            print(
                f"{s.primary.name:<8} "
                f"{(s.secondary.name if s.secondary else '-'):<8} "
                f"{s.slot.date_str:<6} "
                f"{s.slot.time_str:<12} "
                f"{s.candidate.name:<8} "
                f"{s.candidate.position[:18]:<20} "
                f"{s.candidate.effective_priority}"
            )
        if len(results) > 30:
            print(f"  ... 还有 {len(results) - 30} 条")

    if unscheduled:
        print(f"\n未排上的候选人 ({len(unscheduled)}):")
        for c in unscheduled[:10]:
            print(f"  {c.name} | {c.position} | {c.round} | 优先级={c.effective_priority}")
        if len(unscheduled) > 10:
            print(f"  ... 还有 {len(unscheduled) - 10} 人")
