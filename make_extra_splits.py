"""
make_extra_splits.py — Safely redistribute unprocessed rows to new input splits.

For each existing input file (1.csv–80.csv):
  - Identifies processable rows (non-null Task Evidence + Task Evidence Question)
    that have NOT yet appeared in any output file.
  - Keeps done + non-processable rows in the original input file so workers 1–80
    exit fast on restart (nothing left to process).

Splits remaining rows into N_EXTRA new input files (default 81.csv–130.csv),
preserving (UUID, Tasks) group integrity so the per-user-task Relevant cap
continues to work correctly.

Pre-seeds the corresponding new output files (processed_81.csv–processed_130.csv)
with Relevant rows from existing outputs for any (UUID, Tasks) groups that appear
in the new files.  This ensures the cap counter is correctly initialised.

Usage:
  python3 make_extra_splits.py --dry-run          # verify counts, NO writes
  python3 make_extra_splits.py                    # apply (run AFTER stopping processor)

Workflow:
  1. python3 make_extra_splits.py --dry-run   # inspect while processor still running
  2. Ctrl+C the processor
  3. python3 make_extra_splits.py             # backup + rewrite originals + create new splits
  4. Restart the processor — spawns 130 workers; 1–80 exit fast, 81–130 process remaining
"""

import csv
import os
import shutil
import argparse
from collections import defaultdict
from math import ceil

# Must match the processor — prevents crashes on large Gemini response fields
csv.field_size_limit(10_000_000)

_SCRIPT_DIR        = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR          = os.path.join(_SCRIPT_DIR, "parallel_input_split_1_files")
OUTPUT_DIR         = os.path.join(_SCRIPT_DIR, "parallel_output_split_1_files")
INPUT_BACKUP_DIR   = os.path.join(_SCRIPT_DIR, "input_backups")
OUTPUT_BACKUP_DIR  = os.path.join(_SCRIPT_DIR, "output_backups")

# Values treated as null (same conservative set as dedup_output.py)
_NULL_LOWER = {"", "null", "none", "nan"}


def _is_null(val: str) -> bool:
    return val.strip().lower() in _NULL_LOWER


# ── Phase 1: build done state from all existing output files ──────────────────

def build_done_state():
    """
    Scan every processed_N.csv in OUTPUT_DIR.

    Returns
    -------
    done_keys         : set of (uuid, task, evidence) — every row already written
    relevant_by_group : dict[(uuid, task)] → list[raw_row]  — Relevant rows for cap seeding
    output_header     : list[str] — column header of the output files
    """
    done_keys         = set()
    relevant_by_group = defaultdict(list)
    output_header     = None

    fnames = sorted(
        f for f in os.listdir(OUTPUT_DIR)
        if f.startswith("processed_") and f.endswith(".csv")
        and not f.endswith(".bak")
        and f != "merged_output_deduped.csv"
    )

    for fname in fnames:
        fpath = os.path.join(OUTPUT_DIR, fname)
        try:
            with open(fpath, newline="", encoding="utf-8") as fh:
                reader = csv.reader(fh)
                try:
                    hdr = next(reader)
                except StopIteration:
                    continue

                if output_header is None:
                    output_header = hdr

                try:
                    ui = hdr.index("UUID")
                    ti = hdr.index("Tasks")
                    ei = hdr.index("Task Evidence")
                    gi = hdr.index("Relevance Tag")
                except ValueError:
                    continue

                max_i = max(ui, ti, ei, gi)
                for row in reader:
                    if len(row) <= max_i:
                        continue
                    u   = row[ui].strip()
                    t   = row[ti].strip()
                    e   = row[ei].strip()
                    tag = row[gi].strip()
                    if not _is_null(e):
                        done_keys.add((u, t, e))
                    if tag == "Relevant":
                        relevant_by_group[(u, t)].append(row)

        except Exception as exc:
            print(f"  WARNING: could not read {fname}: {exc}")

    return done_keys, relevant_by_group, output_header


# ── Phase 2: scan original input files ───────────────────────────────────────

def scan_inputs(done_keys: set):
    """
    Read every N.csv in INPUT_DIR.

    For each row:
      - Not processable (null evidence or question) → kept in original file
      - Processable AND in done_keys               → kept in original file
      - Processable AND NOT in done_keys           → added to 'remaining' list

    Returns
    -------
    input_header  : list[str]
    remaining     : list of ((uuid, task), row_list) — rows to redistribute
    keep_by_file  : dict[int → list[row_list]]       — rows to keep per original file
    orig_nums     : sorted list[int] of original split numbers
    """
    input_header = None
    remaining    = []   # [(group_key, row), ...]
    keep_by_file = {}

    orig_nums = sorted(
        int(os.path.splitext(f)[0])
        for f in os.listdir(INPUT_DIR)
        if f.endswith(".csv") and os.path.splitext(f)[0].isdigit()
    )

    for n in orig_nums:
        fpath = os.path.join(INPUT_DIR, f"{n}.csv")
        try:
            with open(fpath, newline="", encoding="utf-8") as fh:
                reader = csv.reader(fh)
                try:
                    hdr = next(reader)
                except StopIteration:
                    keep_by_file[n] = []
                    continue

                if input_header is None:
                    input_header = hdr

                try:
                    ui = hdr.index("UUID")
                    ti = hdr.index("Tasks")
                    ei = hdr.index("Task Evidence")
                    qi = hdr.index("Task Evidence Question")
                except ValueError as ve:
                    print(f"  ERROR: {n}.csv missing column: {ve}")
                    keep_by_file[n] = []
                    continue

                keep = []
                for row in reader:
                    ev = row[ei].strip() if ei < len(row) else ""
                    q  = row[qi].strip() if qi < len(row) else ""

                    # Mirror processor null filter
                    processable = not (_is_null(ev) or _is_null(q))
                    if not processable:
                        keep.append(row)
                        continue

                    u   = row[ui].strip() if ui < len(row) else ""
                    t   = row[ti].strip() if ti < len(row) else ""
                    key = (u, t, ev)

                    if key in done_keys:
                        keep.append(row)
                    else:
                        remaining.append(((u, t), row))

                keep_by_file[n] = keep

        except Exception as exc:
            print(f"  ERROR reading {n}.csv: {exc}")
            keep_by_file[n] = []

    return input_header, remaining, keep_by_file, orig_nums


# ── Phase 3: group-aware bin-pack remaining rows into new splits ──────────────

def split_remaining(remaining: list, n_extra: int):
    """
    Group rows by (UUID, Tasks), then fill new splits sequentially,
    never cutting a group across two files.

    Returns list of n_extra lists of row_lists.
    """
    groups      = defaultdict(list)
    group_order = []
    seen        = set()

    for gk, row in remaining:
        if gk not in seen:
            group_order.append(gk)
            seen.add(gk)
        groups[gk].append(row)

    total  = len(remaining)
    target = ceil(total / n_extra) if n_extra > 0 else total

    splits = [[] for _ in range(n_extra)]
    idx    = 0

    for gk in group_order:
        grp = groups[gk]
        # Move to next bin when current is full (but never past the last bin)
        if len(splits[idx]) >= target and idx < n_extra - 1:
            idx += 1
        splits[idx].extend(grp)

    return splits


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Redistribute unprocessed rows to new input splits for faster parallel processing."
    )
    parser.add_argument("--dry-run",   action="store_true",
                        help="Report what would happen — write nothing.")
    parser.add_argument("--n-extra",   type=int, default=50,
                        help="Number of new split files to create (default: 50).")
    parser.add_argument("--start-num", type=int, default=81,
                        help="First file number for new splits (default: 81).")
    args = parser.parse_args()

    dry_run   = args.dry_run
    n_extra   = args.n_extra
    start_num = args.start_num

    if dry_run:
        print("=" * 65)
        print("  DRY RUN — no files will be written")
        print("=" * 65)

    # ── Safety guard: abort if any target input file already exists ───────────
    collisions = [
        os.path.join(INPUT_DIR, f"{start_num + i}.csv")
        for i in range(n_extra)
        if os.path.exists(os.path.join(INPUT_DIR, f"{start_num + i}.csv"))
    ]
    if collisions:
        print(f"\nERROR: {len(collisions)} target input file(s) already exist, e.g.:")
        for p in collisions[:5]:
            print(f"  {p}")
        print("Aborting — do not run twice without cleaning up first.")
        return

    # ── Phase 1 ───────────────────────────────────────────────────────────────
    print("\n[1/4] Building done state from existing output files...")
    done_keys, relevant_by_group, output_header = build_done_state()
    print(f"      Done keys        : {len(done_keys):,}")
    print(f"      Relevant groups  : {len(relevant_by_group):,}")

    # ── Phase 2 ───────────────────────────────────────────────────────────────
    print("\n[2/4] Scanning original input files...")
    input_header, remaining, keep_by_file, orig_nums = scan_inputs(done_keys)

    total_rem  = len(remaining)
    total_keep = sum(len(v) for v in keep_by_file.values())
    total_orig = total_rem + total_keep

    print(f"      Original files scanned    : {len(orig_nums)}")
    print(f"      Total rows in originals   : {total_orig:,}")
    print(f"      Done/non-processable kept : {total_keep:,}")
    print(f"      Remaining to redistribute : {total_rem:,}")

    if total_rem == 0:
        print("\n  All rows already processed — nothing to redistribute.")
        return

    # ── Phase 3 ───────────────────────────────────────────────────────────────
    print(f"\n[3/4] Group-aware split into {n_extra} new files "
          f"({start_num}.csv – {start_num + n_extra - 1}.csv)...")
    new_splits = split_remaining(remaining, n_extra)

    # Determine UUID/Tasks column indices in input header for group key lookup
    try:
        ui_in = input_header.index("UUID")
        ti_in = input_header.index("Tasks")
    except (ValueError, TypeError):
        ui_in = ti_in = None

    # Print per-file summary
    print(f"\n  {'File':>8}  {'Rows':>8}  {'Seed Relevant':>14}")
    print(f"  {'-'*8}  {'-'*8}  {'-'*14}")
    for i, split_rows in enumerate(new_splits):
        file_num = start_num + i
        # Count Relevant seed rows for groups in this split
        seed_count = 0
        if ui_in is not None and ti_in is not None:
            groups_here = set()
            for row in split_rows:
                u = row[ui_in].strip() if ui_in < len(row) else ""
                t = row[ti_in].strip() if ti_in < len(row) else ""
                groups_here.add((u, t))
            for gk in groups_here:
                seed_count += len(relevant_by_group.get(gk, []))
        print(f"  {file_num:>8}  {len(split_rows):>8,}  {seed_count:>14,}")

    print(f"\n  Total rows in new splits : {sum(len(s) for s in new_splits):,}  "
          f"(should equal {total_rem:,})")

    if dry_run:
        print(f"\n  Would backup  : {len(orig_nums)} input files   → {INPUT_BACKUP_DIR}/")
        print(f"  Would backup  : existing output files       → {OUTPUT_BACKUP_DIR}/")
        print(f"  Would rewrite : {len(orig_nums)} original input files (done-only rows)")
        print(f"  Would create  : {n_extra} new input files  ({start_num}.csv – {start_num+n_extra-1}.csv)")
        print(f"  Would create  : up to {n_extra} pre-seeded output files")
        print()
        print("Dry run complete. Verify the numbers above, then:")
        print("  1. Ctrl+C the running processor")
        print("  2. python3 make_extra_splits.py          (without --dry-run)")
        print("  3. Restart the processor")
        return

    # ── Phase 4: apply ────────────────────────────────────────────────────────
    print("\n[4/4] Applying changes...")

    # 4a. Backup original input files
    os.makedirs(INPUT_BACKUP_DIR, exist_ok=True)
    backed_up_in = 0
    for n in orig_nums:
        src = os.path.join(INPUT_DIR, f"{n}.csv")
        dst = os.path.join(INPUT_BACKUP_DIR, f"{n}.csv")
        if not os.path.exists(dst):
            shutil.copy2(src, dst)
            backed_up_in += 1
    print(f"  Backed up {backed_up_in} input files → {INPUT_BACKUP_DIR}/")

    # 4b. Backup existing output files (processed_N.csv, not .bak)
    os.makedirs(OUTPUT_BACKUP_DIR, exist_ok=True)
    backed_up_out = 0
    for fname in os.listdir(OUTPUT_DIR):
        if (fname.startswith("processed_") and fname.endswith(".csv")
                and not fname.endswith(".bak")
                and fname != "merged_output_deduped.csv"):
            src = os.path.join(OUTPUT_DIR, fname)
            dst = os.path.join(OUTPUT_BACKUP_DIR, fname)
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
                backed_up_out += 1
    print(f"  Backed up {backed_up_out} output files → {OUTPUT_BACKUP_DIR}/")

    # 4c. Rewrite original input files with done-only rows
    for n in orig_nums:
        fpath = os.path.join(INPUT_DIR, f"{n}.csv")
        with open(fpath, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(input_header)
            writer.writerows(keep_by_file[n])
    print(f"  Rewrote {len(orig_nums)} original input files (done-only rows)")

    # 4d. Write new input splits and pre-seed output files
    created_inputs  = 0
    created_outputs = 0
    skipped_outputs = 0

    for i, split_rows in enumerate(new_splits):
        file_num = start_num + i

        # Write new input file
        inp_path = os.path.join(INPUT_DIR, f"{file_num}.csv")
        with open(inp_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(input_header)
            writer.writerows(split_rows)
        created_inputs += 1

        # Pre-seed corresponding output file with Relevant rows for cap context
        out_path = os.path.join(OUTPUT_DIR, f"processed_{file_num}.csv")
        if os.path.exists(out_path):
            skipped_outputs += 1
            continue

        if output_header is None:
            continue

        # Collect groups present in this new split
        seed_rows = []
        if ui_in is not None and ti_in is not None:
            groups_here = set()
            for row in split_rows:
                u = row[ui_in].strip() if ui_in < len(row) else ""
                t = row[ti_in].strip() if ti_in < len(row) else ""
                groups_here.add((u, t))
            for gk in groups_here:
                seed_rows.extend(relevant_by_group.get(gk, []))

        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(output_header)
            writer.writerows(seed_rows)
        created_outputs += 1

    print(f"  Created {created_inputs} new input files  "
          f"({start_num}.csv – {start_num + n_extra - 1}.csv)")
    print(f"  Created {created_outputs} pre-seeded output files")
    if skipped_outputs:
        print(f"  Skipped {skipped_outputs} output files (already exist)")

    print()
    print("=" * 65)
    print("  Done.")
    print("=" * 65)
    print(f"  Total input files now : {len(orig_nums) + n_extra}  "
          f"(was {len(orig_nums)})")
    print(f"  Workers 1–{max(orig_nums)} : exit fast (all rows already done)")
    print(f"  Workers {start_num}–{start_num+n_extra-1} : process {total_rem:,} remaining rows")
    print()
    print("  Now restart the processor:")
    print("    python3 processor/1-main-parallel-script.py")


if __name__ == "__main__":
    main()
