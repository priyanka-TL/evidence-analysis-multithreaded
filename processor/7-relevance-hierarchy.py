#!/usr/bin/env python3
"""
Build a relevance-ranked hierarchy (District -> Block -> School -> Teacher UUID)
from a processed CSV file.

Relevance scoring (weighted):
  Relevant          = 1.0
  Partially Relevant = 0.5
  Irrelevant / Not Relevant / other / empty = 0.0

Main output (--output)
----------------------
A fully denormalised flat CSV with one row per teacher.  Every row carries the
complete District → Block → School → Teacher path so no context is ever lost.
Columns at each level include its rank within its parent (sorted highest
relevance % first), evidence count, and relevance percentage.

Top-chain output (--top-chain-output)
--------------------------------------
Summary CSV showing, for each district, the single best block, school, and
teacher by relevance percentage.

Designed to be streaming/efficient for large CSVs (no pandas required).
"""

import argparse
import csv
import os
import sys
from typing import Dict, Tuple

# Default paths (can be overridden via CLI args if desired)
INPUT_FILE = "../pre-processor/parallel_output_split_1_files/merged_output_1.csv"
OUTPUT_FILE = "../pre-processor/parallel_output_split_1_files/relevance_hierarchy_sorted_1.csv"
TOP_CHAIN_OUTPUT_FILE = (
    "../pre-processor/parallel_output_split_1_files/relevance_hierarchy_top_chain_1.csv"
)


def clean_value(value: str) -> str:
    
    if value is None:
        return "Unknown"
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "nan"}:
        return "Unknown"
    return text


def relevance_score(tag: str) -> float:
    tag = (tag or "").strip()
    if tag == "Relevant":
        return 1.0
    if tag == "Partially Relevant":
        return 0.5
    # Treat Irrelevant / Not Relevant / Skipped / empty as 0.0
    return 0.0


def new_stats() -> Dict[str, float]:
    return {"evidence_count": 0, "relevant_score": 0.0}


def add_stats(stats: Dict[str, float], score: float) -> None:
    stats["evidence_count"] += 1
    stats["relevant_score"] += score


def relevance_pct(stats: Dict[str, float]) -> float:
    if stats["evidence_count"] <= 0:
        return 0.0
    return (stats["relevant_score"] / stats["evidence_count"]) * 100.0


def sort_children(children: Dict[str, Dict]) -> Tuple[Tuple[str, Dict], ...]:
    def sort_key(item):
        name, node = item
        stats = node["stats"]
        return (-relevance_pct(stats), -stats["evidence_count"], str(name).lower())

    return tuple(sorted(children.items(), key=sort_key))


def build_hierarchy(input_path: str) -> Dict[str, Dict]:
    hierarchy: Dict[str, Dict] = {}

    with open(input_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required_cols = {"District", "Block", "School Name", "UUID", "Relevance Tag"}
        missing = required_cols - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")

        for row in reader:
            district = clean_value(row.get("District"))
            block = clean_value(row.get("Block"))
            school = clean_value(row.get("School Name"))
            teacher_uuid = clean_value(row.get("UUID"))

            score = relevance_score(row.get("Relevance Tag"))

            dnode = hierarchy.setdefault(district, {"stats": new_stats(), "blocks": {}})
            add_stats(dnode["stats"], score)

            bnode = dnode["blocks"].setdefault(block, {"stats": new_stats(), "schools": {}})
            add_stats(bnode["stats"], score)

            snode = bnode["schools"].setdefault(school, {"stats": new_stats(), "teachers": {}})
            add_stats(snode["stats"], score)

            tnode = snode["teachers"].setdefault(teacher_uuid, {"stats": new_stats()})
            add_stats(tnode["stats"], score)

    return hierarchy


def write_hierarchy_csv(hierarchy: Dict[str, Dict], output_path: str) -> None:
    """
    Write a clean, filter-friendly flat CSV — one row per teacher.

    Eight columns only.  The hierarchy reads left-to-right (District → Block →
    School → Teacher) and rows are pre-sorted highest relevance first at every
    level, so the ranking is conveyed by row order rather than extra columns.

    How to use in Excel / Google Sheets
    ------------------------------------
    • Filter by "District"  → rows are already sorted best Block first
    • Filter by "Block"     → rows are already sorted best School first
    • Filter by "School"    → rows are already sorted best Teacher first
    • Sort by any "Relevance %" column for custom views
    """
    fieldnames = [
        "District",
        "District Relevance %",
        "Block",
        "Block Relevance %",
        "School",
        "School Relevance %",
        "Teacher UUID",
        "Teacher Relevance %",
    ]

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for _d_rank, (district_name, dnode) in enumerate(
            sort_children(hierarchy), start=1
        ):
            for _b_rank, (block_name, bnode) in enumerate(
                sort_children(dnode["blocks"]), start=1
            ):
                for _s_rank, (school_name, snode) in enumerate(
                    sort_children(bnode["schools"]), start=1
                ):
                    for _t_rank, (teacher_uuid, tnode) in enumerate(
                        sort_children(snode["teachers"]), start=1
                    ):
                        writer.writerow({
                            "District": district_name,
                            "District Relevance %": round(relevance_pct(dnode["stats"]), 1),
                            "Block": block_name,
                            "Block Relevance %": round(relevance_pct(bnode["stats"]), 1),
                            "School": school_name,
                            "School Relevance %": round(relevance_pct(snode["stats"]), 1),
                            "Teacher UUID": teacher_uuid,
                            "Teacher Relevance %": round(relevance_pct(tnode["stats"]), 1),
                        })


def write_top_chain_csv(hierarchy: Dict[str, Dict], output_path: str) -> None:
    """
    For each district write up to 5 rows — one per top teacher in the best
    school of the best block.  The "Teacher Rank" column (1-5) makes the order
    explicit while keeping the file flat and easy to filter.
    """
    TOP_N = 5

    fieldnames = [
        "District",
        "District Relevance %",
        "Top Block",
        "Block Relevance %",
        "Top School",
        "School Relevance %",
        "Teacher Rank",
        "Teacher UUID",
        "Teacher Relevance %",
    ]

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for district_name, dnode in sort_children(hierarchy):
            d_pct = round(relevance_pct(dnode["stats"]), 1)

            sorted_blocks = sort_children(dnode["blocks"])
            if not sorted_blocks:
                continue
            top_block_name, bnode = sorted_blocks[0]
            b_pct = round(relevance_pct(bnode["stats"]), 1)

            sorted_schools = sort_children(bnode["schools"])
            if not sorted_schools:
                continue
            top_school_name, snode = sorted_schools[0]
            s_pct = round(relevance_pct(snode["stats"]), 1)

            sorted_teachers = sort_children(snode["teachers"])
            for rank, (teacher_uuid, tnode) in enumerate(sorted_teachers[:TOP_N], start=1):
                writer.writerow({
                    "District": district_name,
                    "District Relevance %": d_pct,
                    "Top Block": top_block_name,
                    "Block Relevance %": b_pct,
                    "Top School": top_school_name,
                    "School Relevance %": s_pct,
                    "Teacher Rank": rank,
                    "Teacher UUID": teacher_uuid,
                    "Teacher Relevance %": round(relevance_pct(tnode["stats"]), 1),
                })


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate District -> Block -> School -> Teacher relevance hierarchy CSV"
    )
    parser.add_argument("--input", default=INPUT_FILE, help="Path to processed CSV")
    parser.add_argument("--output", default=OUTPUT_FILE, help="Path to output CSV")
    parser.add_argument(
        "--top-chain-output",
        default=TOP_CHAIN_OUTPUT_FILE,
        help="Path to top chain summary CSV",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not os.path.exists(args.input):
        print(f"Input file not found: {args.input}", file=sys.stderr)
        return 1

    try:
        hierarchy = build_hierarchy(args.input)
        write_hierarchy_csv(hierarchy, args.output)
        write_top_chain_csv(hierarchy, args.top_chain_output)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Saved relevance hierarchy to: {args.output}")
    print(f"Saved top chain summary to: {args.top_chain_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
