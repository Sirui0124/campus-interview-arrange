#!/usr/bin/env python3
from __future__ import annotations
"""
Campus Interview Scheduling CLI
Usage: python run_schedule.py <popo_doc_url_or_id> [--dry-run] [--write-to=<sheet_title>]
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.data_reader import load_all_data, extract_doc_id, get_full_data
from lib.scheduler import schedule
from lib.result_writer import write_results, print_results_summary, update_candidate_status


def main():
    if len(sys.argv) < 2:
        print("Usage: python run_schedule.py <popo_doc_url_or_id> [--dry-run] [--write-to=<sheet_title>]")
        sys.exit(1)

    doc_input = sys.argv[1]
    dry_run = "--dry-run" in sys.argv
    write_target = "约面结果"
    for arg in sys.argv[2:]:
        if arg.startswith("--write-to="):
            write_target = arg.split("=", 1)[1]

    print(f"读取数据: {doc_input}")
    interviewers, candidates = load_all_data(doc_input)

    print(f"  面试官: {len(interviewers)} 人")
    print(f"  候选人: {len(candidates)} 人 (待排: {sum(1 for c in candidates if c.status != '已安排')})")

    print("\n执行排面策略...")
    results, unscheduled = schedule(interviewers, candidates)

    print_results_summary(results, unscheduled)

    if dry_run:
        print("\n[DRY RUN] 不写入表格")
    else:
        if not results:
            print("\n无排面结果，跳过写入")
            return

        doc_id = extract_doc_id(doc_input) if "docs.popo" in doc_input else doc_input
        full_data = get_full_data(doc_id)
        sheets = full_data["sheets"]

        print(f"\n写入到 sheet: {write_target}")
        count = write_results(doc_id, sheets, results, target_sheet_title=write_target)
        print(f"写入完成: {count} 条记录")

        print("更新候选人清单安排状态...")
        update_candidate_status(doc_id, sheets, results)
        print("安排状态已更新")


if __name__ == "__main__":
    main()
