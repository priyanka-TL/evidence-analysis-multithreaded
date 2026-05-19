import os
import random
import concurrent.futures
import pandas as pd
import json
import httpx
import base64
import time
import csv
import logging
import threading
from collections import deque
from dotenv import load_dotenv

load_dotenv()

# Must be set before any csv.reader/writer call — prevents crashes on large Gemini fields
csv.field_size_limit(10_000_000)

# --- Project root ---
_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)

# --- Constants (all configurable via .env) ---
IMAGE_FORMATS              = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
MAX_PROCESSED_ROWS         = int(os.getenv("MAX_PROCESSED_ROWS",        "20000"))
INPUT_DIR                  = os.path.join(_PROJECT_ROOT, os.getenv("INPUT_DIR",  "parallel_input_split_1_files"))
OUTPUT_DIR                 = os.path.join(_PROJECT_ROOT, os.getenv("OUTPUT_DIR", "parallel_output_split_1_files"))
FINAL_OUTPUT_FILE          = os.path.join(OUTPUT_DIR,   os.getenv("FINAL_OUTPUT_FILENAME", "merged_output.csv"))
ENABLE_RELEVANT_CAP        = os.getenv("ENABLE_RELEVANT_CAP",        "true").strip().lower() == "true"
MAX_RELEVANT_PER_USER_TASK = int(os.getenv("MAX_RELEVANT_PER_USER_TASK", "2"))
GEMINI_MODEL               = os.getenv("GEMINI_MODEL",  "gemini-2.5-flash-lite")
# Per-token RPM limit — each API key gets its own independent quota bucket.
# Gemini 2.5 Flash Lite pay-as-you-go: 4000 RPM per project.
# Free tier: 30 RPM per project.
# Check your actual quota: Google Cloud Console → APIs & Services → Quotas → filter "Gemini".
MAX_RPM_PER_TOKEN          = int(os.getenv("MAX_RPM_PER_TOKEN", "4000"))
# Truncate huge Gemini reasoning strings before writing to CSV (prevents oversized-field crashes)
MAX_FIELD_CHARS            = int(os.getenv("MAX_FIELD_CHARS", "50000"))
# Flush output file every N rows instead of every row (reduces I/O pressure)
FLUSH_INTERVAL             = int(os.getenv("FLUSH_INTERVAL", "10"))

os.makedirs(OUTPUT_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s",
    handlers=[
        logging.FileHandler(f"{OUTPUT_DIR}/processing.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# --- Gemini pricing (per million tokens) ---
GEMINI_PRICING = {
    "gemini-2.5-flash":      {"input": 0.30,  "output": 2.50},
    "gemini-2.5-flash-lite": {"input": 0.10,  "output": 0.40},
    "gemini-2.5-pro":        {"input": 1.25,  "output": 10.00},
    "gemini-2.0-flash":      {"input": 0.10,  "output": 0.40},
    "gemini-2.0-flash-lite": {"input": 0.075, "output": 0.30},
    "gemini-1.5-flash":      {"input": 0.075, "output": 0.30},
    "gemini-1.5-pro":        {"input": 1.25,  "output": 5.00},
}

def estimate_cost(input_tokens, output_tokens, model_name=None):
    key     = (model_name or GEMINI_MODEL).replace("models/", "")
    pricing = GEMINI_PRICING.get(key, {"input": 0.0, "output": 0.0})
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000

def get_gemini_tokens_from_env():
    keys   = sorted(k for k in os.environ if k.startswith("GEMINI_TOKEN"))
    tokens = [os.environ[k] for k in keys]
    if not tokens:
        logging.error("[Gemini] No GEMINI_TOKEN* env vars found!")
    else:
        logging.info(f"[Gemini] Loaded {len(tokens)} token(s): {keys}")
    return tokens

GEMINI_TOKENS = get_gemini_tokens_from_env()
if not GEMINI_TOKENS:
    raise ValueError("[Gemini] No valid Gemini tokens found in environment!")

_GEMINI_REST_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "answers":    {"type": "ARRAY", "items": {"type": "STRING"}},
        "reasonings": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["answers", "reasonings"],
}

# --- Persistent HTTP clients (one per token) ---
# Uses HTTP/1.1 (not HTTP/2) intentionally: HTTP/2 multiplexes many requests over
# the same TCP connection, so when the Gemini server resets that connection under
# burst load, every multiplexed request fails simultaneously — causing the cascade
# of simultaneous write timeouts seen in logs. HTTP/1.1 keeps each request on its
# own connection, so one failure affects only one worker.
#
# Timeouts split per-phase:
#   connect=15  — fail fast if server unreachable
#   read=120    — wait up to 2 min for Gemini to respond
#   write=None  — no write cap; prevents write timeouts on large base64 payloads
#   pool=10     — wait up to 10s for a free connection slot from the pool
_GEMINI_TIMEOUT = httpx.Timeout(connect=15, read=60, write=None, pool=10)

_http_clients:      dict[str, httpx.Client] = {}
_http_clients_lock: threading.Lock          = threading.Lock()

def _get_http_client(token: str) -> httpx.Client:
    """Return the shared persistent httpx.Client for this token (created on first use)."""
    if token not in _http_clients:
        with _http_clients_lock:
            if token not in _http_clients:
                _http_clients[token] = httpx.Client(
                    timeout=_GEMINI_TIMEOUT,
                    limits=httpx.Limits(
                        max_connections=150,       # enough for all workers per token
                        max_keepalive_connections=100,
                        keepalive_expiry=60,
                    ),
                )
    return _http_clients[token]

def _call_gemini_rest(api_key: str, image_bytes: bytes, prompt: str) -> dict:
    url    = _GEMINI_REST_URL.format(model=GEMINI_MODEL)
    client = _get_http_client(api_key)
    payload = {
        "contents": [{"parts": [
            {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(image_bytes).decode()}},
            {"text": prompt},
        ]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema":    _RESPONSE_SCHEMA,
            "thinkingConfig":     {"thinkingBudget": 0},
        },
    }
    resp = client.post(url, json=payload, params={"key": api_key})
    resp.raise_for_status()
    return resp.json()

# --- Per-token rate limiter ---
# Each API key gets its own independent RPM bucket so a busy token never blocks others.
# Lock is released before sleeping — no deadlock possible.
_token_buckets: dict[str, deque]         = {}
_token_locks:   dict[str, threading.Lock] = {}
_buckets_init_lock = threading.Lock()

def _ensure_bucket(token: str):
    if token not in _token_buckets:
        with _buckets_init_lock:
            if token not in _token_buckets:          # double-checked under lock
                _token_buckets[token] = deque()
                _token_locks[token]   = threading.Lock()

def rate_limiter(token: str):
    """Block until this specific token is under MAX_RPM_PER_TOKEN. Lock released during sleep."""
    _ensure_bucket(token)
    lock = _token_locks[token]
    dq   = _token_buckets[token]
    while True:
        with lock:
            now = time.time()
            while dq and now - dq[0] > 60:
                dq.popleft()
            if len(dq) < MAX_RPM_PER_TOKEN:
                dq.append(now)
                return
            sleep_time = 61.0 - (now - dq[0])
        # Sleep OUTSIDE the lock — all other threads unblocked during wait.
        # Jitter (0–3s random) staggers wake-up so threads don't all hammer the
        # API simultaneously after a rate-limit window reset (thundering herd).
        if sleep_time > 0:
            jitter = random.uniform(0, 3)
            logging.info(f"[RateLimiter] Token ...{token[-6:]} throttled {sleep_time:.1f}s (+{jitter:.1f}s jitter)")
            time.sleep(sleep_time + jitter)

# --- Utilities ---
def calculate_relevance_tag(answers):
    if not answers or not isinstance(answers, list):
        return "Irrelevant"
    yes_count = sum(1 for a in answers if str(a).upper() == "YES")
    total     = len(answers)
    pct       = (yes_count / total * 100) if total else 0
    if pct >= 50: return "Relevant"
    if pct >  0:  return "Partially Relevant"
    return "Irrelevant"

def _truncate(value) -> str:
    """Truncate any value to MAX_FIELD_CHARS to prevent oversized CSV fields."""
    s = str(value)
    return s[:MAX_FIELD_CHARS] if len(s) > MAX_FIELD_CHARS else s

# --- Image download with hard total-time cap ---
# read=30 per-chunk is not a total timeout: a server trickling 3 MB over 30 s/chunk
# takes 100–280 s total.  This function streams the response and aborts as soon as
# the wall-clock total exceeds MAX_IMG_SECONDS, keeping each worker moving.
MAX_IMG_SECONDS = int(os.getenv("MAX_IMG_SECONDS", "120"))  # 120 seconds default

# Limit concurrent downloads so each connection gets a fair share of the Oracle Cloud
# bucket bandwidth. 200 simultaneous downloads starve every connection; 50 gives each
# ~4× more bandwidth, reducing 45s cap fires significantly.
_MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "50"))
_DOWNLOAD_SEM = threading.Semaphore(_MAX_CONCURRENT_DOWNLOADS)

def _download_image(url: str) -> httpx.Response:
    """
    Download an image URL and return a mock Response-like object whose
    .content attribute holds the raw bytes.  Raises TimeoutError if the
    total download exceeds MAX_IMG_SECONDS.  No size limit is enforced.
    """
    with _DOWNLOAD_SEM:
        return _download_image_inner(url)


def _download_image_inner(url: str):
    t0     = time.time()
    chunks = []
    total  = 0

    with httpx.stream(
        "GET", url,
        timeout=httpx.Timeout(connect=10, read=15, write=None, pool=5),
        follow_redirects=True,
    ) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_bytes(chunk_size=65_536):
            elapsed = time.time() - t0
            if elapsed > MAX_IMG_SECONDS:
                raise TimeoutError(
                    f"Image download exceeded {MAX_IMG_SECONDS}s total "
                    f"({elapsed:.1f}s elapsed, {total:,} bytes so far): {url}"
                )
            total += len(chunk)
            chunks.append(chunk)

    # Return an object compatible with the existing `image.content` usage
    class _FakeResp:
        content = b"".join(chunks)

    return _FakeResp()


# --- Gemini call with exponential backoff ---
def process_image(task_evidence_link, task_evidence_question, worker_token, max_retries=3):
    for attempt in range(max_retries):
        try:
            rate_limiter(worker_token)
            t0    = time.time()
            image = _download_image(task_evidence_link)
            prompt = (
                "You are an educational evidence validator. "
                f"Analyse the given image... {task_evidence_question}"
            )
            raw     = _call_gemini_rest(worker_token, image.content, prompt)
            elapsed = round(time.time() - t0, 3)

            usage         = raw.get("usageMetadata", {})
            input_tokens  = usage.get("promptTokenCount",     0)
            output_tokens = usage.get("candidatesTokenCount", 0)
            total_tokens  = usage.get("totalTokenCount",      0)
            cost          = estimate_cost(input_tokens, output_tokens)

            text          = raw["candidates"][0]["content"]["parts"][0]["text"]
            response_json = json.loads(text)
            response_json["_meta"] = {
                "input_tokens":  input_tokens,
                "output_tokens": output_tokens,
                "total_tokens":  total_tokens,
                "cost":          cost,
                "response_time": elapsed,
            }
            return response_json

        except Exception as e:
            err           = str(e).lower()
            # TimeoutError/ValueError come from _download_image — never a Gemini rate limit.
            # Also guard "429" against Oracle Cloud UUIDs that contain the digits "429".
            is_img_err    = isinstance(e, (TimeoutError, ValueError))
            is_rate_limit = (not is_img_err) and any(k in err for k in ["rate limit", "quota", "resource_exhausted", "429 too many", "status 429", "'429'"])
            is_timeout    = is_img_err or any(k in err for k in ["timeout", "timed out", "write operation timed out"])
            is_bad_request = (not is_img_err) and ("400" in err or "bad request" in err)

            # 400 means the image/request itself is invalid — retrying will never succeed.
            if is_bad_request:
                logging.warning(f"[Gemini] Attempt {attempt+1}/{max_retries} [bad_request — skipping]: {e}")
                return {"error": str(e)}

            if attempt == max_retries - 1:
                logging.error(f"[Gemini] All {max_retries} attempts exhausted: {e}")
                result = {"error": str(e)}
                if isinstance(e, TimeoutError):
                    result["_img_timeout"] = True  # tells caller: skip write, retry on restart
                return result

            if is_rate_limit:
                wait = min(60 * (2 ** attempt), 300)   # 60 → 120 → 240 → 300s
            elif is_timeout:
                wait = min(2  * (2 ** attempt), 30)    # 2 → 4 → 8 → 16 → 30s
            else:
                wait = min(3  * (2 ** attempt), 30)    # 3 → 6 → 12 → 24 → 30s

            err_label = "rate_limit" if is_rate_limit else ("timeout" if is_timeout else "error")
            logging.warning(
                f"[Gemini] Attempt {attempt+1}/{max_retries} [{err_label}]: {e}. "
                f"Retrying in {wait:.0f}s ..."
            )
            time.sleep(wait)

    return {"error": "Max retries reached"}

# --- Resume state reader ---
def _read_resume_state(output_filename: str, worker_id: int):
    """
    Returns (processed_keys: set[tuple], relevant_count_per_key: dict).

    processed_keys contains (uuid, task, task_evidence) tuples — a composite key
    that uniquely identifies each input row.  Using the composite key (instead of
    URL alone) prevents two things:
      1. Different users who uploaded the same image URL from being incorrectly
         treated as the same row.
      2. Already-processed rows from being re-run when the resume state is only
         partially loaded (e.g. on_bad_lines silently dropped some rows).

    Uses the Python csv engine so csv.field_size_limit applies, preventing crashes
    on the same oversized fields that broke the old code.
    Reads only the 4 lightweight columns needed; skips the bulk reasoning columns.
    """
    processed_keys      = set()   # set of (uuid, task, task_evidence) tuples
    relevant_count_dict = {}

    if not os.path.isfile(output_filename):
        return processed_keys, relevant_count_dict

    try:
        # Count physical data lines first so we can detect silently-skipped rows.
        total_data_lines = 0
        try:
            with open(output_filename, "r", encoding="utf-8") as _f:
                total_data_lines = sum(1 for _ in _f) - 1   # subtract header
        except Exception:
            pass

        needed = {"Task Evidence", "Relevance Tag", "UUID", "Tasks"}
        df = pd.read_csv(
            output_filename,
            usecols=lambda c: c in needed,
            engine="python",        # honours csv.field_size_limit
            on_bad_lines="skip",    # skip any truly malformed rows
        )

        loaded_rows = len(df)
        if total_data_lines > 0 and loaded_rows < total_data_lines:
            logging.warning(
                f"[Worker {worker_id}] Resume state: {total_data_lines - loaded_rows} rows "
                f"could not be parsed from {output_filename} (malformed CSV). "
                "Those rows will be reprocessed — this is safe, they will be deduplicated "
                "by the composite-key check."
            )

        if {"UUID", "Tasks", "Task Evidence"}.issubset(df.columns):
            for _, r in df.iterrows():
                uuid = str(r["UUID"]).strip()
                task = str(r["Tasks"]).strip()
                url  = str(r["Task Evidence"]).strip()
                if url and url.lower() not in ("nan", "null", "none", ""):
                    processed_keys.add((uuid, task, url))

        if {"UUID", "Tasks", "Relevance Tag"}.issubset(df.columns):
            rel = df[df["Relevance Tag"] == "Relevant"]
            for _, r in rel.iterrows():
                key = (str(r["UUID"]).strip(), str(r["Tasks"]).strip())
                relevant_count_dict[key] = relevant_count_dict.get(key, 0) + 1

        logging.info(
            f"[Worker {worker_id}] Resume: {len(processed_keys):,} rows already done "
            f"(parsed {loaded_rows}/{total_data_lines} lines), "
            f"{len(relevant_count_dict)} (UUID,Tasks) keys with Relevant count"
        )
    except Exception as e:
        logging.warning(
            f"[Worker {worker_id}] Could not read resume state from {output_filename}: {e}. "
            "Starting fresh."
        )

    return processed_keys, relevant_count_dict

# --- Per-file worker ---
def main(input_file: str, worker_id: int = None):
    token_idx    = (worker_id - 1) % len(GEMINI_TOKENS)
    worker_token = GEMINI_TOKENS[token_idx]
    logging.info(f"[Worker {worker_id}] Starting — token index {token_idx+1}/{len(GEMINI_TOKENS)}")

    # Stats
    api_calls = api_successes = api_failures = not_validated_count = 0
    total_input_tokens = total_output_tokens = total_tokens = 0
    total_cost = total_response_time = 0.0
    api_call_count = 0
    success_list: list[str] = []
    failed_list:  list[str] = []

    out_f = None
    try:
        if not os.path.exists(input_file):
            logging.error(f"[Worker {worker_id}] File not found: {input_file}")
            return None

        output_filename = os.path.join(
            OUTPUT_DIR,
            f"processed_{os.path.basename(input_file).split('.')[0]}.csv",
        )

        # Resume: load already-processed composite keys and reconstruct cap state.
        # resume_mode is determined by whether the output FILE EXISTS — not by whether
        # any keys were loaded.  This prevents the file from being truncated (write mode)
        # if the output exists but every row failed to parse.
        processed_evidence_urls, relevant_count_per_key = _read_resume_state(output_filename, worker_id)
        resume_mode = os.path.isfile(output_filename)
        if resume_mode and not processed_evidence_urls:
            logging.warning(
                f"[Worker {worker_id}] {output_filename} exists but no resume keys were loaded "
                "(file may be empty or all rows failed to parse) — will append to be safe."
            )

        df = (
            pd.read_excel(input_file)
            if input_file.endswith(".xlsx")
            else pd.read_csv(input_file, engine="python")
        )
        df_filtered = df[
            ~df["Task Evidence"].isin([None, "Null"])
            & ~df["Task Evidence Question"].isin([None, "Null"])
        ].dropna(subset=["Task Evidence", "Task Evidence Question"])

        if {"UUID", "Tasks"}.issubset(df_filtered.columns):
            df_filtered = df_filtered.sort_values(["UUID", "Tasks"], kind="stable").reset_index(drop=True)

        output_columns = list(df_filtered.columns)
        for extra in ["Task evidence Q and A", "Task evidence Q and A Reason", "Relevance Tag", "Image Preview"]:
            if extra not in output_columns:
                output_columns.append(extra)

        csv_file_mode = "a" if resume_mode else "w"
        out_f  = open(output_filename, csv_file_mode, newline="", encoding="utf-8")
        writer = csv.DictWriter(out_f, fieldnames=output_columns, extrasaction="ignore")
        if not resume_mode:
            writer.writeheader()
            out_f.flush()

        processed_count  = 0
        rows_since_flush = 0

        for idx, row in df_filtered.iterrows():
            task_evidence = str(row["Task Evidence"]).strip()
            task_question = str(row["Task Evidence Question"]).strip()
            uuid  = str(row.get("UUID",  "")).strip()
            task  = str(row.get("Tasks", "")).strip()
            key   = (uuid, task)

            # Skip already-written rows using composite key (UUID, Tasks, Task Evidence).
            # URL-only matching would incorrectly skip rows where two different users
            # uploaded the same image for the same task.
            if (uuid, task, task_evidence) in processed_evidence_urls:
                continue

            # --- Classify and process ---
            if ENABLE_RELEVANT_CAP and relevant_count_per_key.get(key, 0) >= MAX_RELEVANT_PER_USER_TASK:
                logging.info(f"[Worker {worker_id}] Row {idx+1} — cap reached for (UUID, Tasks); marking notValidated")
                qa = qa_reason = None
                tag = "notValidated"
                not_validated_count += 1

            elif any(task_evidence.lower().endswith(ext) for ext in IMAGE_FORMATS):
                logging.info(f"[Worker {worker_id}] Processing image row {idx+1}/{len(df_filtered)}")
                api_calls += 1
                response = process_image(task_evidence, task_question, worker_token)

                meta = response.pop("_meta", {}) if isinstance(response, dict) else {}
                inp  = meta.get("input_tokens",  0)
                out  = meta.get("output_tokens", 0)
                tot  = meta.get("total_tokens",  0)
                cost = meta.get("cost",          0.0)
                rt   = meta.get("response_time", 0.0)

                if isinstance(response, dict) and "answers" in response and "reasonings" in response:
                    answers    = response["answers"]
                    reasonings = response["reasonings"]
                    tag        = calculate_relevance_tag(answers)
                    # Truncate before writing — prevents oversized CSV field crashes
                    qa         = _truncate(answers)
                    qa_reason  = _truncate(reasonings)

                    if tag == "Relevant":
                        relevant_count_per_key[key] = relevant_count_per_key.get(key, 0) + 1

                    api_successes       += 1
                    total_input_tokens  += inp
                    total_output_tokens += out
                    total_tokens        += tot
                    total_cost          += cost
                    total_response_time += rt
                    api_call_count      += 1
                    success_list.append(task_evidence)

                    logging.info(
                        f"[Worker {worker_id}] #{api_calls} | in={inp} out={out} "
                        f"cost=${cost:.6f} time={rt}s | {tag}"
                    )
                elif response.get("_img_timeout"):
                    # Image download failed on all retries — write row so the resume key
                    # is saved and the worker never loops on this row across restarts.
                    qa = qa_reason = None
                    tag = "imageError"
                    api_failures += 1
                    logging.warning(
                        f"[Worker {worker_id}] #{api_calls} image download exhausted all retries "
                        f"— marking imageError: {response.get('error', '')}"
                    )
                else:
                    qa = qa_reason = None
                    tag = "Irrelevant"
                    api_failures += 1
                    failed_list.append(task_evidence)
                    logging.warning(
                        f"[Worker {worker_id}] #{api_calls} failed | response: {response}"
                    )

            else:
                logging.debug(f"[Worker {worker_id}] Row {idx+1} — non-image, skipping")
                qa = qa_reason = None
                tag = "Irrelevant"

            # Write row immediately (fault-tolerance: no data lost on crash)
            out_row = row.to_dict()
            out_row["Task evidence Q and A"]        = qa
            out_row["Task evidence Q and A Reason"] = qa_reason
            out_row["Relevance Tag"]                = tag
            out_row["Image Preview"] = (
                task_evidence
                if any(task_evidence.lower().endswith(ext) for ext in IMAGE_FORMATS)
                else ""
            )
            writer.writerow(out_row)
            # Mark as processed immediately so this row is never re-written within
            # the same run, even if the resume state file was only partially loaded.
            processed_evidence_urls.add((uuid, task, task_evidence))

            rows_since_flush += 1
            if rows_since_flush >= FLUSH_INTERVAL:
                out_f.flush()
                rows_since_flush = 0

            processed_count += 1
            if processed_count >= MAX_PROCESSED_ROWS:
                logging.info(f"[Worker {worker_id}] Reached MAX_PROCESSED_ROWS ({MAX_PROCESSED_ROWS})")
                break

        out_f.flush()
        out_f.close()
        out_f = None

        logging.info(
            f"[Worker {worker_id}] Finished {input_file} — "
            f"{processed_count:,} rows | {api_successes} success | {api_failures} fail | "
            f"{not_validated_count} not-validated"
        )

        return {
            "output_file":         output_filename,
            "rows_attempted":      processed_count,
            "api_calls":           api_calls,
            "api_successes":       api_successes,
            "api_failures":        api_failures,
            "not_validated":       not_validated_count,
            "success_list":        success_list,
            "failed_list":         failed_list,
            "total_input_tokens":  total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens":        total_tokens,
            "total_cost":          total_cost,
            "total_response_time": total_response_time,
            "api_call_count":      api_call_count,
        }

    except Exception as e:
        logging.exception(f"[Worker {worker_id}] Fatal error processing {input_file}: {e}")
        if out_f and not out_f.closed:
            out_f.flush()
            out_f.close()
        return None


def process_file_parallel(file_path: str, worker_id: int):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stats = main(file_path, worker_id)
    if stats:
        logging.info(f"[Worker {worker_id}] Saved → {stats['output_file']}")
    else:
        logging.warning(f"[Worker {worker_id}] Processing failed for {file_path}")
    return stats


# --- Memory-efficient chunked merge ---
def _merge_files_chunked(processed_files: list[str], output_file: str, chunksize: int = 2000):
    first = True
    for fpath in processed_files:
        try:
            for chunk in pd.read_csv(fpath, chunksize=chunksize, engine="python", on_bad_lines="skip"):
                chunk.to_csv(output_file, mode="w" if first else "a", header=first, index=False)
                first = False
        except Exception as e:
            logging.error(f"[Merge] Skipping {fpath}: {e}")
    if not first:
        logging.info(f"[Merge] Written → {output_file}")
    else:
        logging.error("[Merge] No data written — all files failed to read")


# --- Summary formatter ---
def _generate_summary(
    total_api_calls, api_successes, api_failures, not_validated,
    input_tokens, output_tokens, total_tok, total_cost, total_rt, api_call_count,
):
    avg_rt = (total_rt / api_call_count) if api_call_count else 0.0
    lines = [
        "=" * 70,
        "  API USAGE SUMMARY".center(70),
        "=" * 70,
        f"  Model                   : {GEMINI_MODEL}",
        f"  Total API calls         : {total_api_calls:,}",
        f"    Successful            : {api_successes:,}",
        f"    Failed                : {api_failures:,}",
        f"    Not Validated (cap)   : {not_validated:,}",
        f"  Input tokens            : {input_tokens:,}",
        f"  Output tokens           : {output_tokens:,}",
        f"  Total tokens            : {total_tok:,}",
        f"  Estimated cost          : ${total_cost:.6f}",
        f"  Avg API response time   : {avg_rt:.3f}s",
        "=" * 70,
    ]
    return "\n".join(lines)


# === Entry point ===
if __name__ == "__main__":
    # Sort files so worker_id → token assignment is deterministic across restarts
    input_files = sorted(
        (
            os.path.join(INPUT_DIR, f)
            for f in os.listdir(INPUT_DIR)
            if f.endswith((".xlsx", ".csv"))
        ),
        key=lambda p: int(os.path.splitext(os.path.basename(p))[0])
        if os.path.splitext(os.path.basename(p))[0].isdigit()
        else os.path.basename(p),
    )
    logging.info(f"[Main] {len(input_files)} input files found in {INPUT_DIR}")

    # Aggregators
    total_rows = total_calls = total_success = total_fail = total_not_val = 0
    all_success: list[str] = []
    all_failed:  list[str] = []
    processed_files: list[str] = []
    failed_files:    list[str] = []
    grand_in = grand_out = grand_tok = 0
    grand_cost = grand_rt = 0.0
    grand_call_count = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(input_files)) as executor:
        futures = {
            executor.submit(process_file_parallel, f, idx + 1): f
            for idx, f in enumerate(input_files)
        }
        for future in concurrent.futures.as_completed(futures):
            src  = futures[future]
            stat = future.result()
            if stat:
                processed_files.append(stat["output_file"])
                total_rows      += stat["rows_attempted"]
                total_calls     += stat["api_calls"]
                total_success   += stat["api_successes"]
                total_fail      += stat["api_failures"]
                total_not_val   += stat["not_validated"]
                all_success.extend(stat["success_list"])
                all_failed.extend(stat["failed_list"])
                grand_in         += stat.get("total_input_tokens",  0)
                grand_out        += stat.get("total_output_tokens", 0)
                grand_tok        += stat.get("total_tokens",        0)
                grand_cost       += stat.get("total_cost",          0.0)
                grand_rt         += stat.get("total_response_time", 0.0)
                grand_call_count += stat.get("api_call_count",      0)
                logging.info(f"[Main] Worker completed: {src}")
            else:
                failed_files.append(src)
                logging.warning(f"[Main] Worker failed: {src}")

    if processed_files:
        logging.info(f"[Main] Merging {len(processed_files)} files → {FINAL_OUTPUT_FILE}")
        _merge_files_chunked(processed_files, FINAL_OUTPUT_FILE)
    else:
        logging.error("[Main] No files processed successfully — nothing to merge")

    logging.info(
        "\n" + _generate_summary(
            total_calls, total_success, total_fail, total_not_val,
            grand_in, grand_out, grand_tok, grand_cost, grand_rt, grand_call_count,
        )
    )

    logging.info(f"[Main] Files succeeded: {len(processed_files)} | failed: {len(failed_files)}")
    if failed_files:
        for f in failed_files:
            logging.warning(f"[Main] FAILED: {f}")
    if all_failed:
        logging.warning(f"[Main] {len(all_failed)} individual API failures recorded")
