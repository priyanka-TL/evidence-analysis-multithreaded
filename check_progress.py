import os
import csv
from datetime import datetime

# Must be raised before any csv.reader usage; prevents crashes on large Gemini reasoning fields
csv.field_size_limit(10_000_000)

INPUT_DIR  = "/Users/priyankapradeep/Desktop/evidence-analysis-multithreaded/parallel_input_split_1_files"
OUTPUT_DIR = "/Users/priyankapradeep/Desktop/evidence-analysis-multithreaded/parallel_output_split_1_files"

def row_count(path):
    if not os.path.exists(path):
        return 0
    try:
        with open(path, newline='', encoding='utf-8', errors='replace') as f:
            return sum(1 for _ in csv.reader(f)) - 1
    except Exception as e:
        print(f"  [WARN] Cannot read {os.path.basename(path)}: {e}")
        return -1   # Negative = unreadable, distinct from 0 rows

# Discover all numeric input files dynamically (handles 1–80 and 81–130 after split)
split_nums = sorted(
    int(os.path.splitext(f)[0])
    for f in os.listdir(INPUT_DIR)
    if f.endswith(".csv") and os.path.splitext(f)[0].isdigit()
)

# Read GEMINI_TOKEN count from env so token assignment stays accurate
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(INPUT_DIR), ".env"))
except Exception:
    pass
n_tokens = sum(1 for k in os.environ if k.startswith("GEMINI_TOKEN"))
if n_tokens == 0:
    n_tokens = 3  # fallback

rows = []
for idx, i in enumerate(split_nums):
    inp       = os.path.join(INPUT_DIR,  f"{i}.csv")
    out       = os.path.join(OUTPUT_DIR, f"processed_{i}.csv")
    total     = row_count(inp)
    processed = row_count(out)
    pct       = (processed / total * 100) if (total > 0 and processed >= 0) else 0
    token     = (idx % n_tokens) + 1   # worker_id = idx+1, same formula as processor
    rows.append((i, total, processed, pct, token))

total_files = len(split_nums)
# A file is effectively done if 100% OR the gap is ≤5 rows (non-processable rows
# are kept in the input but never produce output rows — they are skipped by design).
done_files  = sum(1 for r in rows if r[3] >= 100.0 or (r[1] - r[2] <= 5 and r[2] >= 0))
error_files = sum(1 for r in rows if r[2] < 0)
total_in    = sum(r[1] for r in rows if r[1] > 0)
total_out   = sum(r[2] for r in rows if r[2] >= 0)
overall     = total_out / total_in * 100 if total_in else 0

print(f"\n{'='*72}")
print(f"  Checked at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"  OVERALL: {total_out:,} / {total_in:,} rows  ({overall:.1f}%)  |  {done_files}/{total_files} complete  |  {error_files} unreadable")
print(f"{'='*72}")
print(f"{'File':<8} {'Token':<7} {'Total':>7} {'Done':>7} {'%':>6}  {'Bar':<22} {'Status'}")
print(f"{'-'*72}")

for worker, total, done, pct, tok in sorted(rows, key=lambda x: -x[3]):
    if done < 0:
        bar    = "?" * 20
        status = "UNREADABLE"
        done_s = "   ?"
    else:
        filled = int(pct / 5)
        bar    = "#" * filled + "." * (20 - filled)
        gap    = total - done
        status = "DONE" if pct >= 100 or (gap <= 5 and done >= 0) else ""
        done_s = f"{done:>7,}"
    print(f"{worker:<8} T{tok:<6} {total:>7,} {done_s} {pct:>5.1f}%  [{bar}] {status}")

print(f"{'-'*72}")
for t in range(1, n_tokens + 1):
    tr = [r for r in rows if r[4] == t and r[2] >= 0]
    ti = sum(r[1] for r in tr)
    to = sum(r[2] for r in tr)
    tp = to / ti * 100 if ti else 0
    print(f"Token {t}: {to:>7,} / {ti:,}  ({tp:.1f}%)")
print(f"{'='*72}\n")
