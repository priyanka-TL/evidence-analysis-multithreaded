"""
Live monitor — run in a separate terminal while the processor is running.
Refreshes every 30 seconds.

File counts run in a background thread (can take ~60s for 280 files) so the
display never freezes. Log-based throughput updates every refresh cycle.

Usage (Mode A / default):
  python monitor.py

Usage (Mode B — monitor a specific split):
  INPUT_DIR=parallel_input_split_2_files  OUTPUT_DIR=parallel_output_split_2_files  python monitor.py
"""
import os
import re
import time
import csv
import threading
from datetime import datetime, timedelta
from dotenv import load_dotenv

csv.field_size_limit(10_000_000)

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

INPUT_DIR  = os.environ.get("INPUT_DIR",  "parallel_input_split_1_files")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "parallel_output_split_1_files")
LOG        = os.path.join(OUTPUT_DIR, "processing.log")

if not os.path.isdir(INPUT_DIR):
    print(f"ERROR: Input directory not found: {INPUT_DIR}")
    print("  Run the csv-splitter first:  python pre-processor/2-csv-splitter.py")
    print("  Or run the orchestrator:     python run-split-pipeline.py")
    exit(1)
REFRESH    = 30          # display refresh interval (seconds)
COUNT_INTERVAL = 120     # how often to recount all files (seconds)


# ── Background file counter ───────────────────────────────────────────────────

_counts = {
    "total_in":    0,
    "total_out":   0,
    "done_files":  0,
    "total_files": 0,
    "updated_at":  None,
    "counting":    True,
}
_counts_lock = threading.Lock()


def _row_count(path: str) -> int:
    """Count data rows (header excluded). Returns -1 if file unreadable."""
    if not os.path.exists(path):
        return 0
    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in csv.reader(f)) - 1
    except Exception:
        return -1


def _count_all_files():
    """Run in background: discover all split files, count rows, update shared state."""
    while True:
        try:
            nums = sorted(
                int(os.path.splitext(f)[0])
                for f in os.listdir(INPUT_DIR)
                if f.endswith(".csv") and os.path.splitext(f)[0].isdigit()
            )
            ti = to = done = 0
            for n in nums:
                inp = os.path.join(INPUT_DIR,  f"{n}.csv")
                out = os.path.join(OUTPUT_DIR, f"processed_{n}.csv")
                a = _row_count(inp)
                b = _row_count(out)
                if a > 0:
                    ti += a
                if b >= 0:
                    to += b
                # Done = 100% OR gap ≤ 5 rows (non-processable rows never get output)
                if b >= 0 and (a - b) <= 5:
                    done += 1
            with _counts_lock:
                _counts.update({
                    "total_in":    ti,
                    "total_out":   to,
                    "done_files":  done,
                    "total_files": len(nums),
                    "updated_at":  datetime.now(),
                    "counting":    False,
                })
        except Exception as e:
            with _counts_lock:
                _counts["counting"] = False
        time.sleep(COUNT_INTERVAL)


# ── Log parsing ───────────────────────────────────────────────────────────────

def _parse_window(lines: list, minutes: int):
    """Stats from the last `minutes` minutes of the log."""
    cutoff = (datetime.now() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M")
    recent = [l for l in lines if l[:16] >= cutoff]

    successes  = sum(1 for l in recent if "| in=" in l and "cost=$" in l)
    # [timeout] label — exclude image download cap fires (counted separately as img_cap)
    timeouts   = sum(1 for l in recent if "[timeout]" in l and "retrying" in l.lower()
                     and "image download exceeded" not in l.lower())
    img_cap    = sum(1 for l in recent if "image download exceeded" in l.lower() and "retrying" in l.lower())
    img_errors = sum(1 for l in recent if
                     "peer closed connection" in l.lower() or
                     "handshake" in l.lower() or
                     ("received" in l and "expected" in l))
    # Match Gemini rate-limit responses specifically — avoid Oracle Cloud URL UUID fragments
    rate_lim   = sum(1 for l in recent if
                     "resource_exhausted" in l.lower() or
                     "rate limit" in l.lower() or
                     "throttled" in l.lower() or
                     "[rate_limit]" in l)
    not_val    = sum(1 for l in recent if "cap reached" in l.lower())

    return successes, timeouts, img_cap, img_errors, rate_lim, not_val


def _response_times(lines: list, last_n: int = 200):
    """
    Returns (fast, slow) lists split at 20 s.
    Fast = Gemini replied quickly (thinkingBudget=0 working).
    Slow = image download dominated the call.
    """
    times = []
    for l in reversed(lines):
        m = re.search(r"time=([\d.]+)s \|", l)
        if m and "| in=" in l:
            times.append(float(m.group(1)))
        if len(times) >= last_n:
            break
    fast = [t for t in times if t <= 20]
    slow = [t for t in times if t > 20]
    return fast, slow


# ── Display ───────────────────────────────────────────────────────────────────

def _bar(pct: float, width: int = 24) -> str:
    filled = int(pct / 100 * width)
    return "#" * filled + "." * (width - filled)


def main():
    # Start background file counter immediately
    t = threading.Thread(target=_count_all_files, daemon=True)
    t.start()

    # Snapshot state for rows/min calculation
    prev_out  = None
    prev_time = None

    print("Live monitor starting — Ctrl+C to stop.\n")

    while True:
        os.system("clear")
        now     = datetime.now()
        now_str = now.strftime("%H:%M:%S")

        # ── Log ──────────────────────────────────────────────────────────────
        try:
            with open(LOG, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            print(f"[{now_str}] Waiting for log file …")
            time.sleep(REFRESH)
            continue

        succ_2m, tout_2m, imgcap_2m, img_2m, rl_2m, nv_2m = _parse_window(lines, 2)
        succ_5m, tout_5m, imgcap_5m, img_5m, rl_5m, nv_5m = _parse_window(lines, 5)
        fast_t, slow_t = _response_times(lines, 200)

        log_rate_2m = succ_2m / 2   # rows/min from log, 2-min window
        log_rate_5m = succ_5m / 5   # rows/min from log, 5-min window

        # ── File counts (from background thread) ─────────────────────────────
        with _counts_lock:
            total_in    = _counts["total_in"]
            total_out   = _counts["total_out"]
            done_files  = _counts["done_files"]
            total_files = _counts["total_files"]
            updated_at  = _counts["updated_at"]
            counting    = _counts["counting"]

        pct       = total_out / total_in * 100 if total_in else 0
        remaining = max(total_in - total_out, 0)

        # Rows/min from snapshots (more accurate than log counting)
        snap_rate = 0.0
        if prev_out is not None and prev_time is not None:
            elapsed_min = (now - prev_time).total_seconds() / 60
            if elapsed_min > 0:
                snap_rate = (total_out - prev_out) / elapsed_min
        prev_out  = total_out
        prev_time = now

        # Best rate estimate: prefer snapshot if we have a prior reading
        rate = snap_rate if snap_rate > 0 else log_rate_5m

        # ── ETA ──────────────────────────────────────────────────────────────
        eta_str = "calculating …"
        if rate > 0 and remaining > 0:
            eta_min = remaining / rate
            eta     = now + timedelta(minutes=eta_min)
            eta_str = f"{eta.strftime('%H:%M')}  (~{int(eta_min//60)}h {int(eta_min%60)}m)"
        elif remaining == 0:
            eta_str = "COMPLETE"

        # ── Response time stats ───────────────────────────────────────────────
        avg_fast = sum(fast_t) / len(fast_t) if fast_t else 0
        avg_slow = sum(slow_t) / len(slow_t) if slow_t else 0
        pct_fast = len(fast_t) / (len(fast_t) + len(slow_t)) * 100 if (fast_t or slow_t) else 0

        # ── Print ─────────────────────────────────────────────────────────────
        count_tag = "counting …" if counting else (
            f"as of {updated_at.strftime('%H:%M:%S')}" if updated_at else "pending"
        )

        print(f"{'='*66}")
        print(f"  Live Monitor  {now_str}   (refresh {REFRESH}s)")
        print(f"{'='*66}")
        print(f"  Files    : {done_files}/{total_files} done   ({total_files - done_files} still active)")
        print(f"  Progress : {total_out:,} / {total_in:,} rows   ({pct:.1f}%)   [{count_tag}]")
        print(f"  [{_bar(pct)}]  {pct:.1f}%")
        print(f"  Remaining: {remaining:,} rows")
        print(f"  ETA      : {eta_str}")
        print(f"{'-'*66}")
        print(f"  Throughput  (last 2 min) : {log_rate_2m:6.0f} rows/min  (log-based)")
        print(f"  Throughput  (last 5 min) : {log_rate_5m:6.0f} rows/min  (log-based)")
        print(f"  Throughput  (snapshot)   : {snap_rate:6.0f} rows/min  (file counts)")
        print(f"{'-'*66}")
        print(f"  Response times — last 200 calls:")
        print(f"    Fast ≤20s  : {len(fast_t):4d} calls  avg {avg_fast:5.1f}s   ({pct_fast:.0f}%)")
        print(f"    Slow >20s  : {len(slow_t):4d} calls  avg {avg_slow:5.1f}s   ({100-pct_fast:.0f}%)")
        print(f"{'-'*66}")
        print(f"  Errors — last 5 min:")
        print(f"    API timeouts   : {tout_5m:5d}  {'⚠  high' if tout_5m > 20  else 'OK'}")
        print(f"    Img 45s cap    : {imgcap_5m:5d}  (slow Oracle images — normal, retried)")
        print(f"    Image errors   : {img_5m:5d}  {'⚠  high' if img_5m  > 20  else 'OK'}")
        print(f"    Rate limits    : {rl_5m:5d}  {'⚠  high' if rl_5m   > 10  else 'OK'}")
        print(f"    notValidated   : {nv_5m:5d}")
        print(f"{'='*66}")
        print(f"  File counts refresh every {COUNT_INTERVAL}s  |  Ctrl+C to stop")

        time.sleep(REFRESH)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nMonitor stopped.")
