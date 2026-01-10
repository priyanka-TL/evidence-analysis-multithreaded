import os
import concurrent.futures
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment
import json
import google.generativeai as genai
import httpx
import base64
import typing_extensions as typing
import time
import mimetypes
from urllib.request import urlopen
import re
import logging
import csv
from dotenv import load_dotenv
load_dotenv()
import threading
import time
from collections import deque

# === Constants ===
IMAGE_FORMATS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
MAX_PROCESSED_ROWS = 520
INPUT_DIR = "../pre-processor/parallel_input_split_1_files"
OUTPUT_DIR = "../pre-processor/parallel_output_split_1_files"
FINAL_OUTPUT_FILE = os.path.join(OUTPUT_DIR, "merged_output_1.csv")

# === STATE CONFIGURATION (from .env) ===
STATE_NAME = os.getenv("STATE_NAME", "HARYANA")  # Default: HARYANA

# === ANSWER FORMAT CONFIGURATION ===
# Set to True for descriptive answers, False for YES/NO answers
USE_DESCRIPTIVE_ANSWERS = True

# ==== 🆕 EXTRA KEYS CONFIGURATION ====
# Add any additional keys you want to extract here
EXTRA_KEYS = {
    'Enrollment_2024': {
        'description': 'Enrollment count for 2024',
        'extract_pattern': r'(?:last\s+year|previous\s+year|2024).*?enrolment.*?[\(\s]+(\d{1,4})\)?|enrolment.*?(?:last\s+year|previous\s+year).*?[\(\s]+(\d{1,4})\)?',
        'data_type': 'int'
    },
    'Enrollment_2025': {
        'description': 'Enrollment count for 2025',
        'extract_pattern': r'(?:current\s+year|this\s+year|2025).*?enrolment.*?[\(\s]+(\d{1,4})\)?|enrolment.*?(?:current\s+year|this\s+year).*?[\(\s]+(\d{1,4})\)?',
        'data_type': 'int'
    },
    'Enrollment_Increase_Percentage': {
        'description': 'Percentage increase in enrollment',
        'extract_pattern': r'(?:percentage\s+increase|increase).*?(?:is\s+)?(\d+(?:\.\d+)?)\s*%|(\d+(?:\.\d+)?)\s*%\s*(?:increase|growth|rise)',
        'data_type': 'float'
    }
}

# Enable/disable extra keys extraction
ENABLE_EXTRA_KEYS = True

# Create output directory if it doesn't exist
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s",
    handlers=[
        logging.FileHandler(f"{OUTPUT_DIR}/processing.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def load_questions_mapping(questions_file):
    """Load questions mapping from CSV file"""
    questions_map = {}
    try:
        df_questions = pd.read_csv(questions_file)
        for _, row in df_questions.iterrows():
            task_name_raw = str(row["TASK NAME"]).strip()
            question = str(row["Refined questions using tool and webpage"]).strip()
            
            # Store both raw and normalized versions for flexible matching
            questions_map[task_name_raw] = question
            
            # Also store normalized version (without trailing periods/quotes)
            task_name_normalized = task_name_raw.rstrip("'.").strip()
            if task_name_normalized != task_name_raw:
                questions_map[task_name_normalized] = question
                
        logging.info(f"[Questions] Loaded {len(df_questions)} standard tasks from questions.csv")
        logging.info(f"[Questions] Standard task variations: {len(questions_map)} (including normalized forms)")
    except Exception as e:
        logging.warning(f"Could not load questions file {questions_file}: {e}")
    return questions_map

def get_gemini_tokens_from_env():
    tokens = []
    for key in os.environ:
        if key.startswith("GEMINI_TOKEN"):
            tokens.append(os.environ[key])
    if not tokens:
        logging.error("[Gemini] No Gemini tokens found in environment variables!")
    return tokens

GEMINI_TOKENS = get_gemini_tokens_from_env()

current_token_index = 0

def get_next_gemini_token():
    global current_token_index
    if current_token_index < len(GEMINI_TOKENS):
        token = GEMINI_TOKENS[current_token_index]
        logging.info(f"[Gemini] Using token: -----") #{token}
        return token
    return None

def switch_to_next_token():
    global current_token_index
    current_token_index += 1
    if current_token_index >= len(GEMINI_TOKENS):
        logging.error("[Gemini] All tokens exhausted!")
        return None
    token = get_next_gemini_token()
    if token:
        genai.configure(api_key=token)
        global model
        model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            generation_config={
                "response_mime_type": "application/json",
                "response_schema": AnalysisResponse,
            },
        )
        return token
    return None

# === Gemini Model Setup ===
class AnalysisResponse(typing.TypedDict):
    answers: list[str]
    reasonings: list[str]

initial_token = get_next_gemini_token()
if not initial_token:
    raise ValueError("[Gemini] No valid Gemini tokens found!")

genai.configure(api_key=initial_token)
model = genai.GenerativeModel(
    model_name="gemini-2.0-flash",
    generation_config={
        "response_mime_type": "application/json",
        "response_schema": AnalysisResponse,
    },
)

# === 🆕 Extra Keys Extraction Function ===
def extract_extra_keys(text_fields):
    """
    Extract additional information based on EXTRA_KEYS configuration
    
    Args:
        text_fields: Dict or list of text fields to search
        
    Returns:
        Dict with extracted values for each extra key
    """
    if not ENABLE_EXTRA_KEYS:
        return {}
    
    # Combine all text fields into one string
    if isinstance(text_fields, dict):
        combined_text = ' '.join(str(v) for v in text_fields.values() if v)
    elif isinstance(text_fields, list):
        combined_text = ' '.join(str(v) for v in text_fields if v)
    else:
        combined_text = str(text_fields)
    
    combined_text = combined_text.lower()
    
    extracted = {}
    
    for key_name, config in EXTRA_KEYS.items():
        try:
            pattern = config['extract_pattern']
            data_type = config['data_type']
            
            matches = re.findall(pattern, combined_text, re.IGNORECASE)
            
            if matches:
                # Handle tuple results from multiple capture groups
                if isinstance(matches[0], tuple):
                    # Get first non-empty match from the tuple
                    value = next((m for m in matches[0] if m), None)
                    if value is None:
                        extracted[key_name] = None
                        continue
                else:
                    value = matches[0]
                
                # Convert to appropriate data type
                if data_type == 'int':
                    extracted[key_name] = int(value)
                elif data_type == 'float':
                    extracted[key_name] = float(value)
                else:
                    extracted[key_name] = str(value)
            else:
                extracted[key_name] = None
                
        except Exception as e:
            logging.warning(f"Error extracting {key_name}: {e}")
            extracted[key_name] = None
    
    return extracted

# === Utility functions ===
def calculate_relevance_tag(answers):
    """
    Calculate relevance tag based on answers.
    Automatically detects and handles mixed answer types (YES/NO vs descriptive).
    """
    if not answers or not isinstance(answers, list):
        return 'Irrelevant'
    
    total_answers = len(answers)
    yes_no_answers = []
    descriptive_answers = []
    
    # Categorize answers
    for answer in answers:
        answer_str = str(answer).strip().upper()
        if answer_str in ['YES', 'NO']:
            yes_no_answers.append(answer_str)
        else:
            # Consider it descriptive if it's not just YES/NO
            descriptive_answers.append(str(answer).strip())
    
    # Calculate relevance based on the dominant answer type
    yes_no_ratio = len(yes_no_answers) / total_answers if total_answers > 0 else 0
    descriptive_ratio = len(descriptive_answers) / total_answers if total_answers > 0 else 0
    
    if yes_no_ratio > descriptive_ratio:
        # Mostly YES/NO answers - use binary calculation
        yes_count = sum(1 for answer in yes_no_answers if answer == 'YES')
        total_binary = len(yes_no_answers)
        percentage = (yes_count / total_binary) * 100 if total_binary > 0 else 0
        if percentage >= 50:
            return 'Relevant'
        elif percentage > 0:
            return 'Partially Relevant'
        else:
            return 'Irrelevant'
    else:
        # Mostly descriptive answers - use descriptive analysis
        descriptive_score = 0
        for desc_answer in descriptive_answers:
            # Score based on length and content richness
            length_score = min(len(desc_answer) / 50, 1)  # Max score for 50+ chars
            # Bonus for containing specific educational terms
            education_terms = ['student', 'teacher', 'school', 'class', 'learning', 'activity', 'meeting', 'enrollment']
            term_count = sum(1 for term in education_terms if term.lower() in desc_answer.lower())
            term_score = min(term_count / 3, 1)  # Max score for 3+ terms
            descriptive_score += (length_score + term_score) / 2
        
        avg_descriptive_score = descriptive_score / len(descriptive_answers) if descriptive_answers else 0
        
        if avg_descriptive_score >= 0.7:
            return 'Highly Relevant'
        elif avg_descriptive_score >= 0.4:
            return 'Relevant'
        elif avg_descriptive_score > 0:
            return 'Partially Relevant'
        else:
            return 'Irrelevant'

def adjust_excel_formatting(output_file):
    # This function is for .xlsx, but the script now saves .csv
    # It won't be called by the current logic but is harmless to keep.
    try:
        wb = load_workbook(output_file)
        ws = wb.active
        for row in ws.iter_rows():
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical="top", horizontal="left")
        for col in ws.columns:
            max_length = 0
            col_letter = col[0].column_letter
            for cell in col:
                try:
                    if cell.value:
                        max_length = max(max_length, len(str(cell.value)))
                except:
                    pass
            ws.column_dimensions[col_letter].width = max_length + 2
        wb.save(output_file)
    except Exception as e:
        logging.warning(f"Could not apply Excel formatting to {output_file}: {e}")


def get_image_as_base64(url: str) -> str:
    with urlopen(url) as response:
        image_data = response.read()
    mime_type, _ = mimetypes.guess_type(url)
    if not mime_type:
        mime_type = "image/jpeg"
    base64_data = base64.b64encode(image_data).decode("utf-8")
    return f"data:{mime_type};base64,{base64_data}"

# Track timestamps of recent requests
_request_times = deque()
_request_lock = threading.Lock()
MAX_REQUESTS_PER_MINUTE = 2000

def rate_limiter():
    """Block until we are under the 2000 req/min limit."""
    global _request_times
    with _request_lock:
        now = time.time()
        # Remove requests older than 60 seconds
        while _request_times and now - _request_times[0] > 60:
            _request_times.popleft()

        if len(_request_times) >= MAX_REQUESTS_PER_MINUTE:
            sleep_time = 60 - (now - _request_times[0])
            if sleep_time > 0:
                logging.info(f"[RateLimiter] Throttling for {sleep_time:.2f} seconds to stay under 2000 req/min...")
                time.sleep(sleep_time)
                return rate_limiter()  # Recheck after sleep

        _request_times.append(time.time())


def process_image(task_evidence_link, task_evidence_question, max_retries=3):
    global current_token_index
    retries = 0
    while retries < max_retries:
        try:
            rate_limiter()
            image = httpx.get(task_evidence_link)
            
            # Flexible prompt that allows both YES/NO and descriptive answers
            prompt = f"""You are an educational evidence validator. Analyze the given image and answer these questions:

{task_evidence_question}

For each question, you can provide either:
1. A clear YES or NO answer with brief reasoning, OR
2. A detailed, descriptive answer that thoroughly explains what you observe

Choose the response format that best fits the question and provides the most valuable assessment of the evidence.

Focus on:
- Visual evidence in the image
- Relevance to the question
- Quality and clarity of the evidence
- Educational context and completeness"""
            
            response = model.generate_content([
                {"mime_type": "image/jpeg", "data": base64.b64encode(image.content).decode("utf-8")},
                prompt,
            ])
            response_json = json.loads(response.text)
            return response_json
        except Exception as e:
            error_str = str(e).lower()
            if any(k in error_str for k in ["rate limit", "quota", "429", "resource_exhausted"]):
                logging.warning("[Gemini] Rate limit or quota exceeded. Switching token...")
                if switch_to_next_token():
                    continue
                else:
                    logging.warning("[Gemini] No more tokens. Retrying in 60 seconds...")
                    time.sleep(60)
                    retries += 1
            else:
                logging.error(f"[Gemini] Error: {e}")
                retries += 1
    logging.error("[Gemini] Max retries reached.")
    return {"error": "Max retries reached"}


# === Main processing ===
def main(input_file, worker_id=None):
    try:
        logging.info(f"[Worker {worker_id}] Starting processing for {input_file}")

        if not os.path.exists(input_file):
            logging.error(f"[Worker {worker_id}] File not found: {input_file}")
            return None

        # Load questions mapping
        questions_file = "../input/questions.csv"
        questions_map = load_questions_mapping(questions_file)

        df = pd.read_excel(input_file) if input_file.endswith(".xlsx") else pd.read_csv(input_file)
        df_filtered = df[
            ~df["Task Evidence"].isin([None, "Null"])
            & ~df["Task Evidence Question"].isin([None, "Null"])
        ].dropna(subset=["Task Evidence", "Task Evidence Question"])

        processed_count = 0
        task_evidence_qa = []
        task_evidence_qa_reason = []
        relevance_tags = []
        task_types = []  # Track if task is standard or user-owned
        
        # 🆕 Initialize extra keys columns
        extra_keys_data = {key: [] for key in EXTRA_KEYS.keys()}

        for idx, row in df_filtered.iterrows():
            task_evidence = str(row["Task Evidence"]).strip()
            task_question = str(row["Task Evidence Question"]).strip()
            task_name_raw = str(row.get("Tasks", "")).strip()
            
            # Normalize task name for matching (remove trailing quotes, periods, etc.)
            task_name = task_name_raw.rstrip("'.").strip()
            
            # Check if task is user-owned (not in questions mapping)
            # Try exact match first, then normalized match
            is_user_owned = (task_name_raw not in questions_map and task_name not in questions_map)
            
            if idx == 0 or idx % 10 == 0:  # Log every 10th row for debugging
                logging.debug(f"[Worker {worker_id}] Task name: '{task_name_raw}' -> normalized: '{task_name}' -> {'USER-OWNED' if is_user_owned else 'STANDARD'}")
            
            task_types.append("User-Owned" if is_user_owned else "Standard")

            if any(task_evidence.lower().endswith(ext) for ext in IMAGE_FORMATS):
                logging.info(f"[Worker {worker_id}] Processing {'user-owned' if is_user_owned else 'standard'} task row {idx+1}/{len(df_filtered)}")
                response = process_image(task_evidence, task_question)
                if isinstance(response, dict) and "answers" in response and "reasonings" in response:
                    answers = response["answers"]
                    reasonings = response["reasonings"]
                    task_evidence_qa.append(answers)
                    task_evidence_qa_reason.append(reasonings)
                    relevance_tags.append(calculate_relevance_tag(answers))
                    
                    # 🆕 Extract extra keys from row data
                    if ENABLE_EXTRA_KEYS:
                        text_to_analyze = {
                            'Task Evidence': row.get('Task Evidence', ''),
                            'Task Remarks': row.get('Task Remarks', ''),
                            'Sub-Tasks': row.get('Sub-Tasks', ''),
                            'Answers': ' '.join(str(a) for a in answers),
                            'Reasonings': ' '.join(str(r) for r in reasonings)
                        }
                        extracted = extract_extra_keys(text_to_analyze)
                        for key in EXTRA_KEYS.keys():
                            extra_keys_data[key].append(extracted.get(key))
                    else:
                        for key in EXTRA_KEYS.keys():
                            extra_keys_data[key].append(None)
                else:
                    logging.warning(f"[Worker {worker_id}] Invalid response at row {idx+1}")
                    task_evidence_qa.append(None)
                    task_evidence_qa_reason.append(None)
                    relevance_tags.append('Irrelevant')
                    task_types[-1] = "Failed"  # Update the last task type
                    for key in EXTRA_KEYS.keys():
                        extra_keys_data[key].append(None)
            else:
                logging.info(f"[Worker {worker_id}] Skipping non-image row {idx+1}")
                task_evidence_qa.append(None)
                task_evidence_qa_reason.append(None)
                relevance_tags.append('Irrelevant')
                task_types.append("Non-Image")
                for key in EXTRA_KEYS.keys():
                    extra_keys_data[key].append(None)

            processed_count += 1
            if processed_count >= MAX_PROCESSED_ROWS:
                logging.info(f"[Worker {worker_id}] Reached max processed rows ({MAX_PROCESSED_ROWS})")
                break

        df_filtered = df_filtered.head(processed_count)
        df_filtered["Task evidence Q and A"] = task_evidence_qa
        df_filtered["Task evidence Q and A Reason"] = task_evidence_qa_reason
        df_filtered["Relevance Tag"] = relevance_tags
        df_filtered["Task Type"] = task_types
        
        # 🆕 Add extra keys columns
        if ENABLE_EXTRA_KEYS:
            for key_name, values in extra_keys_data.items():
                df_filtered[f"Extra_{key_name}"] = values
                logging.info(f"[Worker {worker_id}] Added extra key column: Extra_{key_name}")
        
        # ✅ Remove IMAGE() formula for CSV - it's Excel-specific
        df_filtered["Image Preview"] = df_filtered["Task Evidence"].apply(
            lambda x: str(x) if str(x).lower().endswith(tuple(IMAGE_FORMATS)) else ""
        )

        # ✅ Changed to save as CSV instead of XLSX
        output_filename = os.path.join(OUTPUT_DIR, f"processed_{os.path.basename(input_file).split('.')[0]}.csv")
        df_filtered.to_csv(output_filename, index=False)
        
        logging.info(f"[Worker {worker_id}] Finished processing {input_file}. Output: {output_filename}")
        
        # Separate user-owned tasks for reporting
        user_owned_df = df_filtered[df_filtered["Task Type"] == "User-Owned"]
        if not user_owned_df.empty:
            user_owned_filename = os.path.join(OUTPUT_DIR, f"user_owned_tasks_{os.path.basename(input_file).split('.')[0]}.csv")
            user_owned_df.to_csv(user_owned_filename, index=False)
            logging.info(f"[Worker {worker_id}] User-owned tasks saved to: {user_owned_filename}")
            return {
                "output_file": output_filename,
                "user_owned_file": user_owned_filename,
                "rows_attempted": processed_count,
                "api_calls": processed_count,
                "api_successes": sum(1 for tag in relevance_tags if tag != 'Irrelevant'),
                "api_failures": sum(1 for tag in relevance_tags if tag == 'Irrelevant'),
                "success_list": [task_evidence for task_evidence, tag in zip(df_filtered["Task Evidence"], relevance_tags) if tag != 'Irrelevant'],
                "failed_list": [task_evidence for task_evidence, tag in zip(df_filtered["Task Evidence"], relevance_tags) if tag == 'Irrelevant'],
                "user_owned_count": len(user_owned_df),
                "standard_count": len(df_filtered) - len(user_owned_df)
            }
        else:
            return {
                "output_file": output_filename,
                "rows_attempted": processed_count,
                "api_calls": processed_count,
                "api_successes": sum(1 for tag in relevance_tags if tag != 'Irrelevant'),
                "api_failures": sum(1 for tag in relevance_tags if tag == 'Irrelevant'),
                "success_list": [task_evidence for task_evidence, tag in zip(df_filtered["Task Evidence"], relevance_tags) if tag != 'Irrelevant'],
                "failed_list": [task_evidence for task_evidence, tag in zip(df_filtered["Task Evidence"], relevance_tags) if tag == 'Irrelevant'],
                "user_owned_count": 0,
                "standard_count": len(df_filtered)
            }

    except Exception as e:
        logging.exception(f"[Worker {worker_id}] Failed to process {input_file}: {e}")
        return None


def process_file_parallel(file_path, worker_id):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    result = main(file_path, worker_id)  # Get stats dictionary
    if result and isinstance(result, dict):
        logging.info(f"[Worker {worker_id}] Output saved as {result['output_file']}")
        if 'user_owned_file' in result:
            logging.info(f"[Worker {worker_id}] User-owned tasks saved as {result['user_owned_file']}")
    else:
        logging.warning(f"[Worker {worker_id}] Processing failed for {file_path}")
    return result  # Return the entire stats dictionary (or None)

# === Entry point ===
if __name__ == "__main__":
    input_files = [
        os.path.join(INPUT_DIR, file)
        for file in os.listdir(INPUT_DIR)
        if file.endswith((".xlsx", ".csv"))
    ]

    logging.info(f"[Main] Found {len(input_files)} input files to process.")
    
    # 🆕 Log extra keys configuration
    if ENABLE_EXTRA_KEYS:
        logging.info(f"[Main] Extra keys extraction ENABLED. Keys to extract:")
        for key_name, config in EXTRA_KEYS.items():
            logging.info(f"   - {key_name}: {config['description']}")
    else:
        logging.info(f"[Main] Extra keys extraction DISABLED.")

    # ✅ --- Global Stats Aggregators ---
    total_rows_processed_all = 0
    total_api_calls_all = 0
    total_api_success_all = 0
    total_api_failure_all = 0
    all_success_lists = []
    all_failed_lists = []
    processed_files = [] # List of successful output file paths
    user_owned_files = [] # List of user-owned task files
    failed_files = [] # List of input files that failed to process
    total_user_owned_count = 0
    total_standard_count = 0
    # --- End Aggregators ---

    processed_files = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(input_files)) as executor:
        futures = {
            executor.submit(process_file_parallel, f, idx + 1): f
            for idx, f in enumerate(input_files)
        }
        for future in concurrent.futures.as_completed(futures):
            original_file = futures[future]
            result_stats = future.result()
            
            if result_stats and isinstance(result_stats, dict):  # ✅ Check if processing was successful
                processed_files.append(result_stats["output_file"])
                total_rows_processed_all += result_stats["rows_attempted"]
                total_api_calls_all += result_stats["api_calls"]
                total_api_success_all += result_stats["api_successes"]
                total_api_failure_all += result_stats["api_failures"]
                all_success_lists.extend(result_stats["success_list"])
                all_failed_lists.extend(result_stats["failed_list"])
                total_user_owned_count += result_stats.get("user_owned_count", 0)
                total_standard_count += result_stats.get("standard_count", 0)
                
                if "user_owned_file" in result_stats:
                    user_owned_files.append(result_stats["user_owned_file"])
                    
                logging.info(f"[Main] Worker finished processing: {original_file}")
            else:
                logging.warning(f"[Main] File {original_file} failed to process.")
                failed_files.append(original_file)

    if not processed_files:
        logging.error("[Main] No files processed successfully. Exiting.")
        # ✅ Still log the summary even if exiting
    else:
        try:
            logging.info(f"[Main] Merging {len(processed_files)} files into {FINAL_OUTPUT_FILE}")
            merged_df = pd.concat([pd.read_csv(f) for f in processed_files], ignore_index=True)
            merged_df.to_csv(FINAL_OUTPUT_FILE, index=False)
            logging.info(f"✅ All files processed and merged into: {FINAL_OUTPUT_FILE}")
            
            # Create separate user-owned tasks summary if any exist
            if user_owned_files:
                user_owned_summary_file = os.path.join(OUTPUT_DIR, "user_owned_tasks_summary.csv")
                user_owned_df = pd.concat([pd.read_csv(f) for f in user_owned_files], ignore_index=True)
                user_owned_df.to_csv(user_owned_summary_file, index=False)
                logging.info(f"✅ User-owned tasks merged into: {user_owned_summary_file}")
            
            # 🆕 Log final extra keys statistics
            if ENABLE_EXTRA_KEYS:
                logging.info(f"[Main] Final extra keys statistics:")
                for key_name in EXTRA_KEYS.keys():
                    col_name = f"Extra_{key_name}"
                    if col_name in merged_df.columns:
                        non_null = merged_df[col_name].notna().sum()
                        total = len(merged_df)
                        logging.info(f"   - {col_name}: {non_null}/{total} ({non_null/total*100:.1f}%)")
        except Exception as e:
            logging.exception(f"[Main] Error during merging: {e}")
            exit(1)

    # ✅ --- Log the Final Summary ---
    try:
        logging.info("="*80)
        logging.info("===== 🚀 PROCESSING RUN SUMMARY =====")
        logging.info("="*80)
        
        logging.info(f"Total Rows Processed (sum of attempts): {total_rows_processed_all}")
        logging.info(f"Total API Calls (image rows attempted): {total_api_calls_all}")
        logging.info(f"  - ✅ Success: {total_api_success_all}")
        logging.info(f"  - ❌ Failed: {total_api_failure_all}")
        
        logging.info("")
        logging.info(f"Total Input Files Processed Successfully: {len(processed_files)}")
        logging.info(f"Total Input Files Failed to Process: {len(failed_files)}")
        if failed_files:
            logging.warning("Failed Input Files:")
            for f in failed_files:
                logging.warning(f"  - {f}")

        logging.info("")
        logging.info(f"Task Type Breakdown:")
        logging.info(f"  - Standard Tasks: {total_standard_count}")
        logging.info(f"  - User-Owned Tasks: {total_user_owned_count}")
        logging.info(f"  - Total Tasks: {total_standard_count + total_user_owned_count}")

        if all_failed_lists:
            logging.warning(f"List of Failed API Calls ({len(all_failed_lists)}):")
            for item in all_failed_lists:
                logging.warning(f"  - {item}")
        else:
            logging.info("✅ No API call failures recorded.")

        if all_success_lists:
            logging.info(f"List of Successful API Calls ({len(all_success_lists)}):")
            for item in all_success_lists:
                logging.info(f"  - {item}")
        else:
            logging.info("No API call successes recorded.")
            
        logging.info("="*80)
        logging.info("===== 🏁 END OF SUMMARY =====")
        logging.info("="*80 + "\n")
    except Exception as e:
        logging.exception(f"[Main] Failed to write summary to log: {e}")