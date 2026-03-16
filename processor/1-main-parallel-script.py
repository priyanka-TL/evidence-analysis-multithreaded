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
import io  # For BytesIO when processing Excel files from URLs
from dotenv import load_dotenv
load_dotenv()
import threading
import time
from collections import deque
import hashlib
from pathlib import Path

# === Constants ===
IMAGE_FORMATS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
PDF_FORMATS = {".pdf"}
EXCEL_FORMATS = {".xlsx", ".xls"}
ALL_VALID_FORMATS = IMAGE_FORMATS | PDF_FORMATS | EXCEL_FORMATS
MAX_PROCESSED_ROWS = 520
INPUT_DIR = "../pre-processor/parallel_input_split_1_files"
OUTPUT_DIR = "../pre-processor/parallel_output_split_1_files"
FINAL_OUTPUT_FILE = os.path.join(OUTPUT_DIR, "merged_output_1.csv")
CHECKPOINT_FILE = os.path.join(OUTPUT_DIR, ".processing_checkpoint.json")
API_USAGE_LOG_FILE = os.path.join(OUTPUT_DIR, "api_usage_log.csv")

# === CHECKPOINT CONFIGURATION (from .env) ===
RESUME_FROM_CHECKPOINT = os.getenv("RESUME_FROM_CHECKPOINT", "True").lower() == "true"
CHECKPOINT_SAVE_FREQUENCY = int(os.getenv("CHECKPOINT_SAVE_FREQUENCY", "10"))
CHECKPOINT_CLEANUP_ON_SUCCESS = os.getenv("CHECKPOINT_CLEANUP_ON_SUCCESS", "True").lower() == "true"

# === STATE CONFIGURATION (from .env) ===
STATE_NAME = os.getenv("STATE_NAME", "HARYANA")  # Default: HARYANA

# === QUESTIONS FILE CONFIGURATION (from .env) ===
# Path to the CSV containing standard task names and their evaluation questions.
# Relative to the processor/ directory. Defaults to the Haryana question sheet.
QUESTIONS_FILE = os.getenv("QUESTIONS_FILE", "../input/question.csv")

# === RELEVANCE SCORING CONFIGURATION ===
# BIHAR: Use "strict" mode (YES/NO answers only, descriptive content ignored)
# HARYANA: Use "mixed" mode (considers both YES/NO and descriptive quality)
# Options: "strict" (Bihar), "mixed" (Haryana), "descriptive" (only descriptive)
RELEVANCE_MODE = os.getenv("RELEVANCE_MODE", "mixed")  # Default: mixed for Haryana

# Thresholds for relevance scoring (configurable per state)
RELEVANT_THRESHOLD = float(os.getenv("RELEVANT_THRESHOLD", "0.7"))  # Score >= 0.7 = Relevant
PARTIALLY_RELEVANT_THRESHOLD = float(os.getenv("PARTIALLY_RELEVANT_THRESHOLD", "0.4"))  # Score >= 0.4 = Partially Relevant

# === ANSWER FORMAT CONFIGURATION ===
# Set to True for descriptive answers, False for YES/NO answers
USE_DESCRIPTIVE_ANSWERS = os.getenv("USE_DESCRIPTIVE_ANSWERS", True)

# ==== 🆕 ENROLLMENT CONFIGURATION ====
# Configure which task should be processed for enrollment data (loaded from .env)
ENROLLMENT_TASK_FILTER = os.getenv(
    "ENROLLMENT_TASK_FILTER",
    "5. Calculate percentage increase in enrolment from last year and create an enrolment report."
)

# Add any additional keys you want to extract here
EXTRA_KEYS = {
    'Enrollment_2024': {
        'description': 'Enrollment count for 2024',
        'extract_pattern': r'(?:total\s+enrolment\s+number\s+from\s+last\s+year|last\s+year.*?enrol(?:l|)ment.*?number|previous\s+year.*?enrol(?:l|)ment).*?[:\s]+(\d{1,4})|(?:enrol(?:l|)ment|नामांकन).*?(?:last\s+year|previous\s+year|2024).*?[:\s]+(\d{1,4})|(?:last\s+year|2024).*?[:\s]+(\d{1,4})(?!\s*%)',
        'data_type': 'int',
        'task_filter': True  # Only extract from specific task
    },
    'Enrollment_2025': {
        'description': 'Enrollment count for 2025',
        'extract_pattern': r'(?:total\s+enrolment\s+number\s+from\s+current\s+year|current\s+year.*?enrol(?:l|)ment.*?number|this\s+year.*?enrol(?:l|)ment).*?[:\s]+(\d{1,4})|(?:enrol(?:l|)ment|नामांकन).*?(?:current\s+year|this\s+year|2025).*?[:\s]+(\d{1,4})|(?:current\s+year|2025).*?[:\s]+(\d{1,4})(?!\s*%)',
        'data_type': 'int',
        'task_filter': True  # Only extract from specific task
    },
    'Enrollment_Increase_Percentage': {
        'description': 'Percentage increase in enrollment',
        'extract_pattern': r'(?:percentage\s+increase|%\s+increase|increase.*?percentage).*?(?:is\s+)?(-?\d+(?:\.\d+)?)\s*%|(-?\d+(?:\.\d+)?)\s*%\s*(?:increase|growth|rise|वृद्धि|decrease|decline)',
        'data_type': 'float',
        'task_filter': True  # Only extract from specific task
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

# === Thread-safe checkpoint lock ===
checkpoint_lock = threading.Lock()

# === Thread-safe API usage tracking lock ===
api_usage_lock = threading.Lock()

# === API PRICING CONFIGURATION (per 1M tokens) ===
# Gemini 2.0 Flash pricing as of Jan 2026
GEMINI_PRICING = {
    "gemini-2.0-flash": {
        "input_price_per_million": 0.075,   # $0.075 per 1M input tokens
        "output_price_per_million": 0.30,   # $0.30 per 1M output tokens
    },
    "gemini-1.5-flash": {
        "input_price_per_million": 0.075,
        "output_price_per_million": 0.30,
    },
    "gemini-1.5-pro": {
        "input_price_per_million": 1.25,
        "output_price_per_million": 5.00,
    }
}

# ===== CHECKPOINT MANAGEMENT FUNCTIONS =====

def generate_row_hash(row):
    """
    Generate unique hash for a row based on its key fields.
    Uses: School ID + Task + Task Evidence URL
    """
    try:
        school_id = str(row.get("School ID", "")).strip()
        task = str(row.get("Tasks", "")).strip()
        evidence = str(row.get("Task Evidence", "")).strip()
        
        # Create unique string
        unique_str = f"{school_id}|{task}|{evidence}"
        
        # Generate hash
        return hashlib.md5(unique_str.encode('utf-8')).hexdigest()
    except Exception as e:
        logger.error(f"Error generating row hash: {e}")
        return None

def load_checkpoint():
    """
    Load checkpoint file if it exists and RESUME_FROM_CHECKPOINT is enabled.
    Returns: dict with file-level checkpoint data
    """
    if not RESUME_FROM_CHECKPOINT:
        logger.info("[Checkpoint] Resume from checkpoint is DISABLED")
        return {}
    
    if not os.path.exists(CHECKPOINT_FILE):
        logger.info("[Checkpoint] No existing checkpoint found. Starting fresh.")
        return {}
    
    try:
        with open(CHECKPOINT_FILE, 'r') as f:
            checkpoint_data = json.load(f)
        
        # Calculate statistics
        total_processed = sum(
            len(file_data.get('processed_ids', {})) 
            for file_data in checkpoint_data.values()
        )
        
        logger.info(f"[Checkpoint] ✓ Loaded checkpoint with {total_processed} processed rows across {len(checkpoint_data)} files")
        
        for file_name, file_data in checkpoint_data.items():
            count = len(file_data.get('processed_ids', {}))
            logger.info(f"[Checkpoint]   - {file_name}: {count} rows already processed")
        
        return checkpoint_data
    except Exception as e:
        logger.error(f"[Checkpoint] Error loading checkpoint: {e}. Starting fresh.")
        return {}

def save_checkpoint(checkpoint_data):
    """
    Save checkpoint data to file (thread-safe).
    """
    try:
        with checkpoint_lock:
            # Add metadata
            checkpoint_data['_metadata'] = {
                'last_updated': time.strftime('%Y-%m-%d %H:%M:%S'),
                'total_files': len([k for k in checkpoint_data.keys() if not k.startswith('_')]),
                'total_processed': sum(
                    len(v.get('processed_ids', {})) 
                    for k, v in checkpoint_data.items() 
                    if not k.startswith('_')
                )
            }
            
            # Write to temp file first, then rename (atomic operation)
            temp_file = CHECKPOINT_FILE + '.tmp'
            with open(temp_file, 'w') as f:
                json.dump(checkpoint_data, f, indent=2)
            
            # Atomic rename
            os.replace(temp_file, CHECKPOINT_FILE)
            
    except Exception as e:
        logger.error(f"[Checkpoint] Error saving checkpoint: {e}")

def is_row_processed(file_name, row_hash, checkpoint_data):
    """
    Check if a specific row has already been processed.
    """
    if not RESUME_FROM_CHECKPOINT or not row_hash:
        return False
    
    file_data = checkpoint_data.get(file_name, {})
    processed_ids = file_data.get('processed_ids', {})
    
    return row_hash in processed_ids

def mark_row_processed(file_name, row_hash, checkpoint_data, row_result=None):
    """
    Mark a row as processed in the checkpoint data.
    """
    if not row_hash:
        return
    
    if file_name not in checkpoint_data:
        checkpoint_data[file_name] = {
            'processed_ids': {},
            'started_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'total_processed': 0
        }
    
    checkpoint_data[file_name]['processed_ids'][row_hash] = {
        'status': 'success',
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'result_summary': row_result if row_result else 'processed'
    }
    
    checkpoint_data[file_name]['total_processed'] = len(checkpoint_data[file_name]['processed_ids'])
    checkpoint_data[file_name]['last_updated'] = time.strftime('%Y-%m-%d %H:%M:%S')

def cleanup_checkpoint():
    """
    Remove checkpoint file after successful completion.
    """
    if CHECKPOINT_CLEANUP_ON_SUCCESS and os.path.exists(CHECKPOINT_FILE):
        try:
            os.remove(CHECKPOINT_FILE)
            logger.info("[Checkpoint] ✓ Checkpoint file cleaned up after successful completion")
        except Exception as e:
            logger.warning(f"[Checkpoint] Could not cleanup checkpoint file: {e}")

# ===== END OF CHECKPOINT FUNCTIONS =====

# ===== API USAGE TRACKING FUNCTIONS =====

def initialize_api_usage_log():
    """
    Initialize API usage log file with headers if it doesn't exist.
    """
    if not os.path.exists(API_USAGE_LOG_FILE):
        with open(API_USAGE_LOG_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                'Timestamp',
                'Worker_ID',
                'Input_File',
                'Row_Number',
                'School_ID',
                'Task',
                'Evidence_URL',
                'Model_Name',
                'API_Call_Type',
                'Input_Tokens',
                'Output_Tokens',
                'Total_Tokens',
                'Input_Cost_USD',
                'Output_Cost_USD',
                'Total_Cost_USD',
                'Status',
                'Error_Message'
            ])
        logging.info(f"[API Usage] Created new API usage log: {API_USAGE_LOG_FILE}")

def log_api_usage(worker_id, input_file, row_number, school_id, task, model_name, 
                  api_call_type, response=None, status='success', error_message='', evidence_url=''):
    """
    Log API usage with token counts and costs to CSV file (thread-safe).
    
    Args:
        worker_id: Worker/thread identifier
        input_file: Input CSV file being processed
        row_number: Row number in the input file
        school_id: School ID from the row
        task: Task name
        model_name: Gemini model name used
        api_call_type: Type of API call (e.g., 'image_analysis', 'pdf_analysis', 'enrollment_analysis')
        response: Gemini API response object (contains usage_metadata)
        status: 'success' or 'failure'
        error_message: Error message if status is 'failure'
        evidence_url: URL of the evidence being processed
    """
    try:
        # Extract token counts from response
        input_tokens = 0
        output_tokens = 0
        total_tokens = 0
        
        if response and hasattr(response, 'usage_metadata'):
            input_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0)
            output_tokens = getattr(response.usage_metadata, 'candidates_token_count', 0)
            total_tokens = getattr(response.usage_metadata, 'total_token_count', 0)
        
        # Calculate costs based on model pricing
        pricing = GEMINI_PRICING.get(model_name, GEMINI_PRICING.get("gemini-2.0-flash"))
        input_cost = (input_tokens / 1_000_000) * pricing["input_price_per_million"]
        output_cost = (output_tokens / 1_000_000) * pricing["output_price_per_million"]
        total_cost = input_cost + output_cost
        
        # Thread-safe write to CSV
        with api_usage_lock:
            with open(API_USAGE_LOG_FILE, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    time.strftime('%Y-%m-%d %H:%M:%S'),
                    worker_id,
                    os.path.basename(input_file),
                    row_number,
                    school_id,
                    task[:50] if task else '',  # Truncate task name to 50 chars
                    evidence_url[:200] if evidence_url else '',  # Truncate URL to 200 chars
                    model_name,
                    api_call_type,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    f"{input_cost:.6f}",
                    f"{output_cost:.6f}",
                    f"{total_cost:.6f}",
                    status,
                    error_message[:100] if error_message else ''  # Truncate error to 100 chars
                ])
    except Exception as e:
        logging.warning(f"[API Usage] Failed to log API usage: {e}")

def generate_api_usage_summary():
    """
    Generate summary statistics from API usage log.
    Returns dict with summary stats.
    """
    try:
        if not os.path.exists(API_USAGE_LOG_FILE):
            return None
        
        df = pd.read_csv(API_USAGE_LOG_FILE)
        
        if df.empty:
            return None
        
        summary = {
            'total_api_calls': len(df),
            'successful_calls': len(df[df['Status'] == 'success']),
            'failed_calls': len(df[df['Status'] == 'failure']),
            'total_input_tokens': df['Input_Tokens'].sum(),
            'total_output_tokens': df['Output_Tokens'].sum(),
            'total_tokens': df['Total_Tokens'].sum(),
            'total_cost_usd': df['Total_Cost_USD'].astype(float).sum(),
            'avg_input_tokens_per_call': df['Input_Tokens'].mean(),
            'avg_output_tokens_per_call': df['Output_Tokens'].mean(),
            'avg_cost_per_call': df['Total_Cost_USD'].astype(float).mean(),
        }
        
        # Per-model breakdown
        model_breakdown = df.groupby('Model_Name').agg({
            'Input_Tokens': 'sum',
            'Output_Tokens': 'sum',
            'Total_Tokens': 'sum',
            'Total_Cost_USD': lambda x: x.astype(float).sum()
        }).to_dict('index')
        
        summary['model_breakdown'] = model_breakdown
        
        # Per-worker breakdown
        worker_breakdown = df.groupby('Worker_ID').agg({
            'Input_Tokens': 'sum',
            'Output_Tokens': 'sum',
            'Total_Tokens': 'sum',
            'Total_Cost_USD': lambda x: x.astype(float).sum()
        }).to_dict('index')
        
        summary['worker_breakdown'] = worker_breakdown
        
        return summary
        
    except Exception as e:
        logging.error(f"[API Usage] Failed to generate summary: {e}")
        return None

# ===== END OF API USAGE TRACKING FUNCTIONS =====

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

class EnrollmentAnalysisResponse(typing.TypedDict):
    answers: list[str]
    reasonings: list[str]
    enrollment_2024: int | None
    enrollment_2025: int | None
    enrollment_increase_percentage: float | None

initial_token = get_next_gemini_token()
if not initial_token:
    raise ValueError("[Gemini] No valid Gemini tokens found!")

genai.configure(api_key=initial_token)

# Standard model for regular tasks
model = genai.GenerativeModel(
    model_name="gemini-2.0-flash",
    generation_config={
        "response_mime_type": "application/json",
        "response_schema": AnalysisResponse,
    },
)

# Enrollment model with enhanced schema
enrollment_model = genai.GenerativeModel(
    model_name="gemini-2.0-flash",
    generation_config={
        "response_mime_type": "application/json",
        "response_schema": EnrollmentAnalysisResponse,
    },
)

# === 🆕 Extra Keys Extraction Function ===
def extract_extra_keys(text_fields, task_name=None):
    """
    Extract additional information based on EXTRA_KEYS configuration

    Args:
        text_fields: Dict or list of text fields to search
        task_name: Name of the task being processed (for task filtering)

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
            # Check if this key requires task filtering
            if config.get('task_filter', False):
                # Normalize both task names for comparison
                task_name_normalized = task_name.strip().rstrip("'.\"").strip() if task_name else None
                enrollment_filter_normalized = ENROLLMENT_TASK_FILTER.strip().rstrip("'.\"").strip()
                
                # Only extract if task matches ENROLLMENT_TASK_FILTER
                if task_name_normalized is None or task_name_normalized != enrollment_filter_normalized:
                    extracted[key_name] = None
                    continue

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

# === 🆕 Enrollment Data Validation Function ===
def validate_and_fix_enrollment_data(enr_2024, enr_2025, enr_pct, answers_text, reasonings_text):
    """
    Validate enrollment data from API response and apply sanity checks.
    
    Args:
        enr_2024: Enrollment count for 2024 (from API JSON)
        enr_2025: Enrollment count for 2025 (from API JSON)
        enr_pct: Percentage increase (from API JSON)
        answers_text: Combined answers text (for logging)
        reasonings_text: Combined reasonings text (for logging)
    
    Returns:
        Tuple of (validated_2024, validated_2025, validated_pct)
    """
    original_2024 = enr_2024
    original_2025 = enr_2025
    original_pct = enr_pct
    
    issues_found = []
    
    # ========== SANITY CHECK 1: Same value in all fields ==========
    if enr_2024 is not None and enr_2025 is not None and enr_pct is not None:
        if enr_2024 == enr_2025 == enr_pct:
            issues_found.append(f"Same value in all fields: {enr_2024}")
            # This is clearly wrong - keep only the one that makes sense
            if -100 <= enr_2024 <= 300:
                # Looks like a percentage, keep only that
                enr_pct = enr_2024
                enr_2024 = None
                enr_2025 = None
            else:
                # Doesn't look like percentage, nullify all
                enr_2024 = None
                enr_2025 = None
                enr_pct = None
    
    # ========== SANITY CHECK 2: Year numbers as counts ==========
    if enr_2024 is not None and int(enr_2024) in [2024, 2025]:
        issues_found.append(f"Year number {int(enr_2024)} extracted as 2024 count")
        enr_2024 = None
    
    if enr_2025 is not None and int(enr_2025) in [2024, 2025]:
        issues_found.append(f"Year number {int(enr_2025)} extracted as 2025 count")
        enr_2025 = None
    
    # ========== SANITY CHECK 3: Unrealistic enrollment counts ==========
    if enr_2024 is not None:
        if enr_2024 < 5 or enr_2024 > 10000:
            issues_found.append(f"2024 count {enr_2024} outside realistic range (5-10000)")
            enr_2024 = None
    
    if enr_2025 is not None:
        if enr_2025 < 5 or enr_2025 > 10000:
            issues_found.append(f"2025 count {enr_2025} outside realistic range (5-10000)")
            enr_2025 = None
    
    # ========== SANITY CHECK 4: Unrealistic percentage ==========
    if enr_pct is not None:
        if abs(enr_pct) > 500:  # More than 500% change is unrealistic
            issues_found.append(f"Percentage {enr_pct}% is unrealistic")
            enr_pct = None
    
    # ========== SANITY CHECK 5: Percentage as count or vice versa ==========
    # If a count looks like a typical percentage (single or double digit)
    if enr_2024 is not None and enr_2025 is not None:
        if (enr_2024 < 100 and enr_2025 < 100) and enr_pct is None:
            # Both counts are < 100 and no percentage - might be swapped
            issues_found.append(f"Both counts < 100 ({enr_2024}, {enr_2025}) - might be percentages")
    
    # ========== FALLBACK: Extract from text if API didn't provide counts ==========
    if (enr_2024 is None or enr_2025 is None) and answers_text:
        logging.info(f"[Validation] API didn't provide counts. Attempting text extraction...")
        combined_text = f"{answers_text} {reasonings_text}".lower()
        
        # Pattern 1: Look for "Total enrolment number from last year: 120"
        if enr_2024 is None:
            patterns_2024 = [
                r'total\s+enrol(?:l|)ment\s+(?:number\s+)?(?:from\s+)?last\s+year[:\s]+(\d{2,4})',
                r'last\s+year.*?enrol(?:l|)ment.*?[:\s](\d{2,4})',
                r'enrol(?:l|)ment.*?last\s+year.*?[:\s](\d{2,4})',
                r'previous\s+year.*?[:\s](\d{2,4})',
            ]
            for pattern in patterns_2024:
                matches = re.findall(pattern, combined_text, re.IGNORECASE)
                if matches:
                    for match in matches:
                        try:
                            val = int(match)
                            # Valid enrollment: not a year number, realistic range
                            if 5 <= val <= 9999 and val not in [2024, 2025]:
                                enr_2024 = val
                                logging.info(f"[Validation] Extracted 2024 count from text: {enr_2024}")
                                break
                        except:
                            continue
                if enr_2024:
                    break
        
        # Pattern 2: Look for "Total enrolment number from current year: 144"
        if enr_2025 is None:
            patterns_2025 = [
                r'total\s+enrol(?:l|)ment\s+(?:number\s+)?(?:from\s+)?current\s+year[:\s]+(\d{2,4})',
                r'current\s+year.*?enrol(?:l|)ment.*?[:\s](\d{2,4})',
                r'enrol(?:l|)ment.*?current\s+year.*?[:\s](\d{2,4})',
                r'this\s+year.*?[:\s](\d{2,4})',
            ]
            for pattern in patterns_2025:
                matches = re.findall(pattern, combined_text, re.IGNORECASE)
                if matches:
                    for match in matches:
                        try:
                            val = int(match)
                            if 5 <= val <= 9999 and val not in [2024, 2025]:
                                enr_2025 = val
                                logging.info(f"[Validation] Extracted 2025 count from text: {enr_2025}")
                                break
                        except:
                            continue
                if enr_2025:
                    break
    
    # ========== CALCULATION: If we have 2 values, calculate the 3rd ==========
    if enr_2024 is not None and enr_2025 is not None and enr_pct is None:
        # Calculate percentage from counts
        if enr_2024 > 0:
            enr_pct = round(((enr_2025 - enr_2024) / enr_2024) * 100, 2)
            logging.info(f"[Validation] Calculated percentage: {enr_pct}%")
    
    elif enr_2024 is not None and enr_pct is not None and enr_2025 is None:
        # Calculate 2025 from 2024 and percentage
        enr_2025 = int(round(enr_2024 * (1 + enr_pct / 100)))
        logging.info(f"[Validation] Calculated 2025 count: {enr_2025}")
    
    elif enr_2025 is not None and enr_pct is not None and enr_2024 is None:
        # Calculate 2024 from 2025 and percentage
        if enr_pct != -100:  # Avoid division by zero
            enr_2024 = int(round(enr_2025 / (1 + enr_pct / 100)))
            logging.info(f"[Validation] Calculated 2024 count: {enr_2024}")
    
    # ========== LOGGING ==========
    if issues_found:
        logging.warning(f"[Validation] Issues detected: {'; '.join(issues_found)}")
        logging.info(f"[Validation] BEFORE: 2024={original_2024}, 2025={original_2025}, %={original_pct}")
        logging.info(f"[Validation] AFTER:  2024={enr_2024}, 2025={enr_2025}, %={enr_pct}")
    else:
        logging.info(f"[Validation] Data looks good: 2024={enr_2024}, 2025={enr_2025}, %={enr_pct}")
    
    return enr_2024, enr_2025, enr_pct

# === Utility functions ===
def calculate_relevance_tag(answers, mode=None):
    """
    Calculate relevance tag based on answers with configurable scoring modes.
    
    Args:
        answers: List of answer strings from Gemini API
        mode: Scoring mode - "strict" (Bihar), "mixed" (Haryana), "descriptive" (only descriptive)
              If None, uses global RELEVANCE_MODE setting
    
    Modes:
        - "strict": Only YES/NO answers matter, descriptive content ignored (for Bihar)
        - "mixed": Both YES/NO and descriptive answers contribute (for Haryana)
        - "descriptive": Only descriptive answers matter, YES/NO ignored
    
    Returns:
        str: 'Relevant', 'Partially Relevant', or 'Irrelevant'
    """
    if not answers or not isinstance(answers, list):
        return 'Irrelevant'

    total_answers = len(answers)
    if total_answers == 0:
        return 'Irrelevant'

    # Use global mode if not specified
    if mode is None:
        mode = RELEVANCE_MODE

    yes_no_answers = []
    descriptive_answers = []

    # Categorize answers
    for answer in answers:
        if answer is None or str(answer).strip() == '':
            continue
        answer_str = str(answer).strip().upper()
        if answer_str in ['YES', 'NO']:
            yes_no_answers.append(answer_str)
        else:
            # Consider it descriptive if it's not just YES/NO
            descriptive_answers.append(str(answer).strip())

    # Calculate scores for each type
    yes_no_score = 0
    descriptive_score = 0

    # Score YES/NO answers
    if yes_no_answers:
        yes_count = sum(1 for answer in yes_no_answers if answer == 'YES')
        yes_no_score = (yes_count / len(yes_no_answers)) if yes_no_answers else 0

    # Score descriptive answers
    if descriptive_answers:
        total_desc_score = 0
        for desc_answer in descriptive_answers:
            # Score based on length and content richness
            length_score = min(len(desc_answer) / 50, 1)  # Max score for 50+ chars

            # Bonus for containing specific educational terms
            education_terms = ['student', 'teacher', 'school', 'class', 'learning',
                             'activity', 'meeting', 'enrollment', 'enrolment',
                             'छात्र', 'शिक्षक', 'विद्यालय', 'कक्षा']  # Added Hindi terms
            term_count = sum(1 for term in education_terms if term.lower() in desc_answer.lower())
            term_score = min(term_count / 3, 1)  # Max score for 3+ terms

            # Avoid very short or generic answers
            if len(desc_answer) < 10:
                total_desc_score += 0.2  # Low score for very short answers
            else:
                total_desc_score += (length_score * 0.6 + term_score * 0.4)

        descriptive_score = total_desc_score / len(descriptive_answers)

    # ============================================================
    # MODE-SPECIFIC SCORING LOGIC
    # ============================================================
    
    if mode == "strict":
        # BIHAR MODE: Only YES/NO answers count
        # Descriptive content is completely ignored
        if yes_no_answers:
            combined_score = yes_no_score
            logging.debug(f"[Relevance-Strict] YES/NO only: {yes_no_score:.2f} (YES: {sum(1 for a in yes_no_answers if a == 'YES')}/{len(yes_no_answers)})")
        else:
            # No YES/NO answers in strict mode = Irrelevant
            combined_score = 0
            logging.debug(f"[Relevance-Strict] No YES/NO answers found, marking as Irrelevant")
    
    elif mode == "descriptive":
        # DESCRIPTIVE MODE: Only descriptive answers count
        # YES/NO answers are ignored
        if descriptive_answers:
            combined_score = descriptive_score
            logging.debug(f"[Relevance-Descriptive] Descriptive only: {descriptive_score:.2f}")
        else:
            # No descriptive answers = Irrelevant
            combined_score = 0
            logging.debug(f"[Relevance-Descriptive] No descriptive answers found, marking as Irrelevant")
    
    else:  # mode == "mixed" (default for Haryana)
        # MIXED MODE: Both YES/NO and descriptive answers contribute
        if yes_no_answers and descriptive_answers:
            # Case 1: Mixed answers - weighted average based on count
            yes_no_weight = len(yes_no_answers) / total_answers
            descriptive_weight = len(descriptive_answers) / total_answers
            combined_score = (yes_no_score * yes_no_weight) + (descriptive_score * descriptive_weight)
            logging.debug(f"[Relevance-Mixed] YES/NO: {yes_no_score:.2f} (weight: {yes_no_weight:.2f}), Descriptive: {descriptive_score:.2f} (weight: {descriptive_weight:.2f}), Combined: {combined_score:.2f}")
        elif yes_no_answers:
            # Case 2: Only YES/NO answers
            combined_score = yes_no_score
            logging.debug(f"[Relevance-Mixed] YES/NO only: {combined_score:.2f}")
        elif descriptive_answers:
            # Case 3: Only descriptive answers
            combined_score = descriptive_score
            logging.debug(f"[Relevance-Mixed] Descriptive only: {combined_score:.2f}")
        else:
            # No valid answers
            combined_score = 0
            logging.debug(f"[Relevance-Mixed] No valid answers found")

    # Determine relevance tag based on combined score and configurable thresholds
    if combined_score >= RELEVANT_THRESHOLD:
        tag = 'Relevant'
    elif combined_score >= PARTIALLY_RELEVANT_THRESHOLD:
        tag = 'Partially Relevant'
    else:
        tag = 'Irrelevant'
    
    logging.debug(f"[Relevance-{mode.upper()}] Final score: {combined_score:.2f} → Tag: {tag}")
    return tag

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


def process_image(task_evidence_link, task_evidence_question, task_name=None, max_retries=3,
                  worker_id=None, input_file=None, row_number=None, school_id=None):
    global current_token_index
    retries = 0
    while retries < max_retries:
        try:
            rate_limiter()
            image = httpx.get(task_evidence_link)

            # Check if this is an enrollment-related task (normalize both sides)
            task_name_normalized_check = (task_name.strip().rstrip("'.\"").strip() if task_name else "")
            filter_normalized_check = ENROLLMENT_TASK_FILTER.strip().rstrip("'.\"").strip()
            is_enrollment_task = (task_name_normalized_check == filter_normalized_check)
            
            if is_enrollment_task:
                logging.info(f"[Enrollment] Model selection: Using enrollment_model for task '{task_name_normalized_check}'")

            # Flexible prompt that allows both YES/NO and descriptive answers
            prompt = f"""You are an educational evidence validator. Analyze the given image and answer these questions:

{task_evidence_question}

IMPORTANT RESPONSE FORMAT:
- For each question, provide EXACTLY ONE answer in the "answers" array
- Put your reasoning/explanation in the "reasonings" array (NOT in answers)
- The answer can be either:
  1. A clear YES or NO
  2. A detailed descriptive answer (e.g., "The school has organized activities...")

Example for 1 question:
{{
  "answers": ["YES"],  // or ["The enrollment increased from 120 to 144"]
  "reasonings": ["The image clearly shows enrollment data with increasing trend"]
}}

DO NOT put both YES/NO and explanation in the answers array!

Focus on:
- Visual evidence in the image
- Relevance to the question
- Quality and clarity of the evidence
- Educational context and completeness"""

            # Select model and update prompt based on task type
            selected_model = model
            if is_enrollment_task:
                selected_model = enrollment_model
                # Update the example to show enrollment fields
                prompt = f"""You are an educational evidence validator. Analyze the given image and answer these questions:

{task_evidence_question}

IMPORTANT RESPONSE FORMAT:
- For each question, provide EXACTLY ONE answer in the "answers" array
- Put your reasoning/explanation in the "reasonings" array (NOT in answers)
- The answer can be either:
  1. A clear YES or NO
  2. A detailed descriptive answer (e.g., "The school has organized activities...")

Example for 1 question with enrollment data:
{{
  "answers": ["20%"],
  "reasonings": ["The image clearly shows enrollment data with increasing trend"],
  "enrollment_2024": 120,
  "enrollment_2025": 144,
  "enrollment_increase_percentage": 20.0
}}

DO NOT put both YES/NO and explanation in the answers array!

Focus on:
- Visual evidence in the image
- Relevance to the question
- Quality and clarity of the evidence
- Educational context and completeness"""
                prompt += """

====================================================================================
⚠️ CRITICAL: ENROLLMENT DATA EXTRACTION FROM REPORT IMAGE ⚠️
====================================================================================

You are analyzing an ENROLLMENT REPORT table/image. You MUST extract THREE DIFFERENT numerical values:

📊 VALUE 1: enrollment_2024 (INTEGER - Student Count)
   WHERE TO FIND: Look for column headers or labels like:
   - "Total enrolment number from last year"
   - "Last Year Enrolment" 
   - "Previous Year"
   - Near the year "2024"
   
   WHAT TO EXTRACT: The STUDENT COUNT (typically 10-9999 range)
   ❌ DO NOT extract: The year "2024" itself
   ❌ DO NOT extract: Percentages
   ✅ EXAMPLE: If table shows "Total enrolment from last year: 120" → return 120
   ✅ EXAMPLE: If table shows "2024: 85 students" → return 85
   
📊 VALUE 2: enrollment_2025 (INTEGER - Student Count)  
   WHERE TO FIND: Look for column headers or labels like:
   - "Total enrolment number from current year"
   - "Current Year Enrolment"
   - "This Year"
   - Near the year "2025"
   
   WHAT TO EXTRACT: The STUDENT COUNT (typically 10-9999 range)
   ❌ DO NOT extract: The year "2025" itself
   ❌ DO NOT extract: Percentages
   ✅ EXAMPLE: If table shows "Total enrolment from current year: 144" → return 144
   ✅ EXAMPLE: If table shows "2025: 96 students" → return 96

📊 VALUE 3: enrollment_increase_percentage (FLOAT - Percentage Value)
   WHERE TO FIND: Look for column headers or labels like:
   - "% increase"
   - "Percentage increase" 
   - "Growth %"
   - Usually has a "%" symbol
   
   WHAT TO EXTRACT: The PERCENTAGE number (can be negative)
   ✅ EXAMPLE: If shows "% increase: 8%" → return 8.0
   ✅ EXAMPLE: If shows "-20%" → return -20.0
   ✅ EXAMPLE: If shows "20% growth" → return 20.0

====================================================================================
❌ COMMON MISTAKES TO AVOID:
====================================================================================
1. ❌ Putting the SAME value in all three fields (e.g., all = 8.0)
2. ❌ Extracting year numbers as counts (2024 as enrollment count)
3. ❌ Extracting counts as percentages (120 as percentage)
4. ❌ Extracting percentages as counts (8% as enrollment count)

====================================================================================
✅ CORRECT EXAMPLE FROM A TABLE:
====================================================================================
Table shows:
| School | Last Year | Current Year | % Increase |
| ABC    | 120       | 144          | 20%        |

CORRECT JSON Response:
{
  "answers": ["20%"],
  "reasonings": ["The table shows clear enrollment data"],
  "enrollment_2024": 120,     ← Last Year count
  "enrollment_2025": 144,     ← Current Year count  
  "enrollment_increase_percentage": 20.0   ← Percentage
}

====================================================================================
✅ ANOTHER CORRECT EXAMPLE:
====================================================================================
Table shows:
| District | 2024 | 2025 | Growth |
| XYZ      | 85   | 96   | -20%   |

CORRECT JSON Response:
{
  "answers": ["The enrollment decreased by 20%"],
  "reasonings": ["Based on the table data"],
  "enrollment_2024": 85,      ← 2024 count
  "enrollment_2025": 96,      ← 2025 count
  "enrollment_increase_percentage": -20.0  ← Negative percentage
}

====================================================================================
⚠️ If you cannot find a value clearly, set it to null. Do NOT guess!
====================================================================================
"""

            response = selected_model.generate_content([
                {"mime_type": "image/jpeg", "data": base64.b64encode(image.content).decode("utf-8")},
                prompt,
            ])
            response_json = json.loads(response.text)
            
            # Log API usage
            api_call_type = "enrollment_analysis" if is_enrollment_task else "image_analysis"
            log_api_usage(
                worker_id=worker_id or "unknown",
                input_file=input_file or "unknown",
                row_number=row_number or 0,
                school_id=school_id or "unknown",
                task=task_name or "unknown",
                model_name="gemini-2.0-flash",
                api_call_type=api_call_type,
                response=response,
                status='success',
                evidence_url=task_evidence_link or ""
            )
            
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


# === Helper function to determine evidence type ===
def get_evidence_type(url):
    """Determine evidence type from URL. Returns: 'image', 'pdf', 'excel', or None"""
    url = str(url).strip().lower()
    for ext in IMAGE_FORMATS:
        if url.endswith(ext):
            return "image"
    for ext in PDF_FORMATS:
        if url.endswith(ext):
            return "pdf"
    for ext in EXCEL_FORMATS:
        if url.endswith(ext):
            return "excel"
    return None


# === Enrollment prompt suffix (shared between image/pdf/excel processors) ===
ENROLLMENT_PROMPT_SUFFIX = """

====================================================================================
⚠️ CRITICAL: ENROLLMENT DATA EXTRACTION FROM REPORT ⚠️
====================================================================================

You are analyzing an ENROLLMENT REPORT. You MUST extract THREE DIFFERENT numerical values:

📊 VALUE 1: enrollment_2024 (INTEGER - Student Count)
   WHERE TO FIND: Look for labels like:
   - "Total enrolment number from last year"
   - "Last Year Enrolment" 
   - "Previous Year"
   - Near the year "2024"
   
   WHAT TO EXTRACT: The STUDENT COUNT (typically 10-9999 range)
   ❌ DO NOT extract: The year "2024" itself
   ❌ DO NOT extract: Percentages
   
📊 VALUE 2: enrollment_2025 (INTEGER - Student Count)  
   WHERE TO FIND: Look for labels like:
   - "Total enrolment number from current year"
   - "Current Year Enrolment"
   - "This Year"
   - Near the year "2025"
   
   WHAT TO EXTRACT: The STUDENT COUNT (typically 10-9999 range)
   ❌ DO NOT extract: The year "2025" itself
   ❌ DO NOT extract: Percentages

📊 VALUE 3: enrollment_increase_percentage (FLOAT - Percentage Value)
   WHERE TO FIND: Look for labels like:
   - "% increase"
   - "Percentage increase" 
   - "Growth %"
   - Usually has a "%" symbol
   
   WHAT TO EXTRACT: The PERCENTAGE number (can be negative)

====================================================================================
⚠️ If you cannot find a value clearly, set it to null. Do NOT guess!
====================================================================================
"""


def process_pdf(task_evidence_link, task_evidence_question, task_name=None, max_retries=3,
                worker_id=None, input_file=None, row_number=None, school_id=None):
    """Process PDF evidence using Gemini API with usage tracking"""
    global current_token_index
    retries = 0
    while retries < max_retries:
        try:
            rate_limiter()
            # Download PDF
            pdf_response = httpx.get(task_evidence_link)
            pdf_data = pdf_response.content
            
            # Check if this is an enrollment-related task
            task_name_normalized_check = (task_name.strip().rstrip("'\".").strip() if task_name else "")
            filter_normalized_check = ENROLLMENT_TASK_FILTER.strip().rstrip("'\".").strip()
            is_enrollment_task = (task_name_normalized_check == filter_normalized_check)
            
            if is_enrollment_task:
                logging.info(f"[PDF] Using enrollment_model for task '{task_name_normalized_check}'")
            
            prompt = f"""You are an educational evidence validator. Analyze the given PDF document and answer these questions:

{task_evidence_question}

IMPORTANT RESPONSE FORMAT:
- For each question, provide EXACTLY ONE answer in the "answers" array
- Put your reasoning/explanation in the "reasonings" array (NOT in answers)
- The answer can be either:
  1. A clear YES or NO
  2. A detailed descriptive answer (e.g., "The school has organized activities...")

Example for 1 question:
{{
  "answers": ["YES"],  // or ["The enrollment increased from 120 to 144"]
  "reasonings": ["The document clearly shows enrollment data with increasing trend"]
}}

DO NOT put both YES/NO and explanation in the answers array!

Focus on:
- Content evidence in the document
- Relevance to the question
- Quality and clarity of the evidence
- Educational context and completeness"""
            
            selected_model = model
            if is_enrollment_task:
                selected_model = enrollment_model
                # Update the example to show enrollment fields
                prompt = f"""You are an educational evidence validator. Analyze the given PDF document and answer these questions:

{task_evidence_question}

IMPORTANT RESPONSE FORMAT:
- For each question, provide EXACTLY ONE answer in the "answers" array
- Put your reasoning/explanation in the "reasonings" array (NOT in answers)
- The answer can be either:
  1. A clear YES or NO
  2. A detailed descriptive answer (e.g., "The school has organized activities...")

Example for 1 question with enrollment data:
{{
  "answers": ["20%"],
  "reasonings": ["The document clearly shows enrollment data with increasing trend"],
  "enrollment_2024": 120,
  "enrollment_2025": 144,
  "enrollment_increase_percentage": 20.0
}}

DO NOT put both YES/NO and explanation in the answers array!

Focus on:
- Content evidence in the document
- Relevance to the question
- Quality and clarity of the evidence
- Educational context and completeness"""
                prompt += ENROLLMENT_PROMPT_SUFFIX
            
            response = selected_model.generate_content([
                {"mime_type": "application/pdf", "data": base64.b64encode(pdf_data).decode("utf-8")},
                prompt,
            ])
            response_json = json.loads(response.text)
            
            # Log API usage
            api_call_type = "enrollment_analysis" if is_enrollment_task else "pdf_analysis"
            log_api_usage(
                worker_id=worker_id or "unknown",
                input_file=input_file or "unknown",
                row_number=row_number or 0,
                school_id=school_id or "unknown",
                task=task_name or "unknown",
                model_name="gemini-2.0-flash",
                api_call_type=api_call_type,
                response=response,
                status='success',
                evidence_url=task_evidence_link or ""
            )
            
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
                logging.error(f"[Gemini] PDF processing error: {e}")
                retries += 1
    logging.error("[Gemini] Max retries reached for PDF processing.")
    return {"error": "Max retries reached"}


def process_excel(task_evidence_link, task_evidence_question, task_name=None, max_retries=3,
                  worker_id=None, input_file=None, row_number=None, school_id=None):
    """Process Excel evidence - download and convert to text for Gemini with usage tracking"""
    global current_token_index
    retries = 0
    while retries < max_retries:
        try:
            rate_limiter()
            # Download Excel file
            excel_response = httpx.get(task_evidence_link)
            
            # Read Excel into DataFrame
            df_excel = pd.read_excel(io.BytesIO(excel_response.content))
            excel_text = df_excel.to_string()
            
            # Check if this is an enrollment-related task
            task_name_normalized_check = (task_name.strip().rstrip("'\".").strip() if task_name else "")
            filter_normalized_check = ENROLLMENT_TASK_FILTER.strip().rstrip("'\".").strip()
            is_enrollment_task = (task_name_normalized_check == filter_normalized_check)
            
            if is_enrollment_task:
                logging.info(f"[Excel] Using enrollment_model for task '{task_name_normalized_check}'")
            
            prompt = f"""You are an educational evidence validator. Analyze the following Excel spreadsheet data and answer these questions:

{task_evidence_question}

EXCEL DATA:
{excel_text[:10000]}

IMPORTANT RESPONSE FORMAT:
- For each question, provide EXACTLY ONE answer in the "answers" array
- Put your reasoning/explanation in the "reasonings" array (NOT in answers)
- The answer can be either:
  1. A clear YES or NO
  2. A detailed descriptive answer (e.g., "The enrollment increased from 120 to 144")

Example for 1 question:
{{
  "answers": ["YES"],  // or ["The data shows increasing enrollment trend"]
  "reasonings": ["The spreadsheet clearly shows enrollment data with upward trend"]
}}

DO NOT put both YES/NO and explanation in the answers array!

Focus on:
- Data evidence in the spreadsheet
- Relevance to the question
- Quality and completeness of the data
- Educational context"""
            
            selected_model = model
            if is_enrollment_task:
                selected_model = enrollment_model
                # Update the example to show enrollment fields
                prompt = f"""You are an educational evidence validator. Analyze the following Excel spreadsheet data and answer these questions:

{task_evidence_question}

EXCEL DATA:
{excel_text[:10000]}

IMPORTANT RESPONSE FORMAT:
- For each question, provide EXACTLY ONE answer in the "answers" array
- Put your reasoning/explanation in the "reasonings" array (NOT in answers)
- The answer can be either:
  1. A clear YES or NO
  2. A detailed descriptive answer (e.g., "The enrollment increased from 120 to 144")

Example for 1 question with enrollment data:
{{
  "answers": ["20%"],
  "reasonings": ["The spreadsheet clearly shows enrollment data with upward trend"],
  "enrollment_2024": 120,
  "enrollment_2025": 144,
  "enrollment_increase_percentage": 20.0
}}

DO NOT put both YES/NO and explanation in the answers array!

Focus on:
- Data evidence in the spreadsheet
- Relevance to the question
- Quality and completeness of the data
- Educational context"""
                prompt += ENROLLMENT_PROMPT_SUFFIX
            
            response = selected_model.generate_content([prompt])
            response_json = json.loads(response.text)
            
            # Log API usage
            api_call_type = "enrollment_analysis" if is_enrollment_task else "excel_analysis"
            log_api_usage(
                worker_id=worker_id or "unknown",
                input_file=input_file or "unknown",
                row_number=row_number or 0,
                school_id=school_id or "unknown",
                task=task_name or "unknown",
                model_name="gemini-2.0-flash",
                api_call_type=api_call_type,
                response=response,
                status='success',
                evidence_url=task_evidence_link or ""
            )
            
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
                logging.error(f"[Gemini] Excel processing error: {e}")
                retries += 1
    logging.error("[Gemini] Max retries reached for Excel processing.")
    return {"error": "Max retries reached"}


# === Main processing ===
def main(input_file, worker_id=None, checkpoint_data=None):
    try:
        logging.info(f"[Worker {worker_id}] Starting processing for {input_file}")

        if not os.path.exists(input_file):
            logging.error(f"[Worker {worker_id}] File not found: {input_file}")
            return None
        
        # Get base filename for checkpoint tracking
        input_filename = os.path.basename(input_file)
        
        # Initialize checkpoint for this file if not exists
        if checkpoint_data is None:
            checkpoint_data = {}
        
        if input_filename not in checkpoint_data and RESUME_FROM_CHECKPOINT:
            checkpoint_data[input_filename] = {
                'processed_ids': {},
                'started_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                'total_processed': 0
            }
        
        # Track checkpoint stats
        rows_skipped_from_checkpoint = 0
        rows_processed_new = 0
        checkpoint_save_counter = 0

        # Load questions mapping
        questions_file = QUESTIONS_FILE
        questions_map = load_questions_mapping(questions_file)

        df = pd.read_excel(input_file) if input_file.endswith(".xlsx") else pd.read_csv(input_file)

        # Filter: Keep rows with Task Evidence, but allow Null Task Evidence Question for user-owned tasks
        df_filtered = df[
            ~df["Task Evidence"].isin([None, "Null"])
        ].dropna(subset=["Task Evidence"])

        # Don't filter out rows with Null Task Evidence Question - they might be user-owned tasks
        logging.info(f"[Worker {worker_id}] Total rows after filtering: {len(df_filtered)}")

        # ===== CHECKPOINT: Filter out already-processed rows BEFORE processing =====
        if RESUME_FROM_CHECKPOINT and input_filename in checkpoint_data:
            processed_ids = set(checkpoint_data[input_filename].get('processed_ids', {}).keys())
            if processed_ids:
                # Generate hashes for all rows
                row_hashes = df_filtered.apply(generate_row_hash, axis=1)
                # Keep only unprocessed rows
                rows_before = len(df_filtered)
                df_filtered = df_filtered[~row_hashes.isin(processed_ids)].copy()
                df_filtered.reset_index(drop=True, inplace=True)
                rows_skipped_from_checkpoint = rows_before - len(df_filtered)
                if rows_skipped_from_checkpoint > 0:
                    logging.info(f"[Worker {worker_id}] [Checkpoint] Filtered out {rows_skipped_from_checkpoint} already-processed rows")

        # 🆕 Add extra key columns if enabled
        if ENABLE_EXTRA_KEYS:
            for key_name in EXTRA_KEYS.keys():
                if key_name not in df_filtered.columns:
                    df_filtered[key_name] = ""
                    logging.info(f"[Worker {worker_id}] Added extra key column: {key_name}")

        processed_count = 0
        task_evidence_qa = []
        task_evidence_qa_reason = []
        relevance_tags = []
        task_types = []  # Track if task is standard or user-owned
        
        # 🆕 Initialize extra keys columns
        extra_keys_data = {key: [] for key in EXTRA_KEYS.keys()}

        for idx, row in df_filtered.iterrows():
            # ===== CHECKPOINT: Generate row hash for marking as processed =====
            row_hash = generate_row_hash(row)
            
            # ===== PROCESS ROW (all rows here need processing) =====
            task_evidence = str(row["Task Evidence"]).strip()
            task_question_raw = row.get("Task Evidence Question", "")
            task_question = str(task_question_raw).strip() if pd.notna(task_question_raw) and task_question_raw != "Null" else ""
            task_name_raw = str(row.get("Tasks", "")).strip()
            school_id = str(row.get("School ID", "unknown")).strip()

            # Normalize task name for matching (remove trailing quotes, periods, etc.)
            task_name = task_name_raw.rstrip("'.").strip()

            # Check if task is user-owned (not in questions mapping)
            # Try exact match first, then normalized match
            is_user_owned = (task_name_raw not in questions_map and task_name not in questions_map)

            # For user-owned tasks, use a generic question if no question is provided
            if is_user_owned and not task_question:
                task_question = f"Describe the evidence provided for the task: {task_name}"
                logging.info(f"[Worker {worker_id}] USER-OWNED task '{task_name}' - using generic question")

            if idx == 0 or idx % 10 == 0:  # Log every 10th row for debugging
                logging.debug(f"[Worker {worker_id}] Task name: '{task_name_raw}' -> normalized: '{task_name}' -> {'USER-OWNED' if is_user_owned else 'STANDARD'}")

            task_types.append("User-Owned" if is_user_owned else "Standard")

            # Determine evidence type and route to appropriate processor
            evidence_type = get_evidence_type(task_evidence)
            if evidence_type:
                logging.info(f"[Worker {worker_id}] Processing {evidence_type} {'user-owned' if is_user_owned else 'standard'} task row {idx+1}/{len(df_filtered)}")
                
                # Route to appropriate processor based on evidence type
                if evidence_type == "image":
                    response = process_image(
                        task_evidence, task_question, task_name_raw,
                        worker_id=worker_id, input_file=input_file, 
                        row_number=idx+1, school_id=school_id
                    )
                elif evidence_type == "pdf":
                    response = process_pdf(
                        task_evidence, task_question, task_name_raw,
                        worker_id=worker_id, input_file=input_file,
                        row_number=idx+1, school_id=school_id
                    )
                elif evidence_type == "excel":
                    response = process_excel(
                        task_evidence, task_question, task_name_raw,
                        worker_id=worker_id, input_file=input_file,
                        row_number=idx+1, school_id=school_id
                    )
                else:
                    response = None
                if isinstance(response, dict) and "answers" in response and "reasonings" in response:
                    answers = response["answers"]
                    reasonings = response["reasonings"]
                    task_evidence_qa.append(answers)
                    task_evidence_qa_reason.append(reasonings)
                    relevance_tag = calculate_relevance_tag(answers)
                    relevance_tags.append(relevance_tag)
                    
                    # ===== CHECKPOINT: Mark row as processed =====
                    rows_processed_new += 1
                    mark_row_processed(input_filename, row_hash, checkpoint_data, relevance_tag)
                    
                    # ===== CHECKPOINT: Save periodically =====
                    checkpoint_save_counter += 1
                    if checkpoint_save_counter >= CHECKPOINT_SAVE_FREQUENCY:
                        save_checkpoint(checkpoint_data)
                        checkpoint_save_counter = 0
                        logging.info(f"[Worker {worker_id}] [Checkpoint] Saved progress: {rows_processed_new} new rows processed")

                    # 🆕 Extract enrollment data from JSON response or use regex fallback
                    if ENABLE_EXTRA_KEYS:
                        # Normalize both task names for comparison (remove trailing quotes, periods, spaces)
                        task_name_normalized = task_name_raw.strip().rstrip("'.\"").strip()
                        enrollment_filter_normalized = ENROLLMENT_TASK_FILTER.strip().rstrip("'.\"").strip()
                        
                        # Debug logging for enrollment task matching
                        if "enrolment" in task_name_normalized.lower():
                            logging.info(f"[Enrollment] Checking task: '{task_name_normalized}'")
                            logging.info(f"[Enrollment] Expected filter: '{enrollment_filter_normalized}'")
                            logging.info(f"[Enrollment] Match: {task_name_normalized == enrollment_filter_normalized}")
                        
                        # Check if this is an enrollment task response with enrollment fields
                        # Check if this is an enrollment task (regardless of whether API returned enrollment keys)
                        is_enrollment_task = (task_name_normalized == enrollment_filter_normalized)
                        
                        # Check if API actually returned enrollment data
                        has_enrollment_data = 'enrollment_2024' in response or 'enrollment_2025' in response or 'enrollment_increase_percentage' in response

                        if is_enrollment_task:
                            # Log raw API response for debugging
                            logging.info(f"[Enrollment] Task matched! API returned enrollment data: {has_enrollment_data}")
                            logging.info(f"[Enrollment] Raw API response: enrollment_2024={response.get('enrollment_2024')}, enrollment_2025={response.get('enrollment_2025')}, percentage={response.get('enrollment_increase_percentage')}")
                            
                            # Extract directly from JSON response
                            raw_2024 = response.get('enrollment_2024')
                            raw_2025 = response.get('enrollment_2025')
                            raw_pct = response.get('enrollment_increase_percentage')
                            
                            # Validate and fix enrollment data
                            answers_text = ' '.join(str(a) for a in answers)
                            reasonings_text = ' '.join(str(r) for r in reasonings)
                            
                            validated_2024, validated_2025, validated_pct = validate_and_fix_enrollment_data(
                                raw_2024, raw_2025, raw_pct, answers_text, reasonings_text
                            )
                            
                            extra_keys_data['Enrollment_2024'].append(validated_2024)
                            extra_keys_data['Enrollment_2025'].append(validated_2025)
                            extra_keys_data['Enrollment_Increase_Percentage'].append(validated_pct)
                            
                            logging.info(f"[Worker {worker_id}] Enrollment data (validated): 2024={validated_2024}, 2025={validated_2025}, %={validated_pct}")
                        else:
                            # Fallback to regex extraction for non-enrollment tasks or older responses
                            text_to_analyze = {
                                'Task Evidence': row.get('Task Evidence', ''),
                                'Task Remarks': row.get('Task Remarks', ''),
                                'Sub-Tasks': row.get('Sub-Tasks', ''),
                                'Answers': ' '.join(str(a) for a in answers),
                                'Reasonings': ' '.join(str(r) for r in reasonings)
                            }
                            extracted = extract_extra_keys(text_to_analyze, task_name_normalized)
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
                logging.info(f"[Worker {worker_id}] Skipping unsupported evidence type at row {idx+1}")
                task_evidence_qa.append(None)
                task_evidence_qa_reason.append(None)
                relevance_tags.append('Irrelevant')
                task_types.append("Unsupported")
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
        
        # 🆕 Add extra keys columns (without "Extra_" prefix)
        if ENABLE_EXTRA_KEYS:
            for key_name, values in extra_keys_data.items():
                df_filtered[key_name] = values
                logging.info(f"[Worker {worker_id}] Added extra key column: {key_name}")
        
        # ✅ Remove IMAGE() formula for CSV - it's Excel-specific
        df_filtered["Image Preview"] = df_filtered["Task Evidence"].apply(
            lambda x: str(x) if str(x).lower().endswith(tuple(IMAGE_FORMATS)) else ""
        )

        # ✅ Changed to save as CSV instead of XLSX
        output_filename = os.path.join(OUTPUT_DIR, f"processed_{os.path.basename(input_file).split('.')[0]}.csv")
        
        # ===== CHECKPOINT: Filter out checkpoint-skipped rows before saving =====
        df_to_save = df_filtered[df_filtered["Relevance Tag"] != "Skipped-Checkpoint"].copy()
        df_to_save.to_csv(output_filename, index=False)
        
        # ===== CHECKPOINT: Final save for this file =====
        if RESUME_FROM_CHECKPOINT:
            save_checkpoint(checkpoint_data)
            logging.info(f"[Worker {worker_id}] [Checkpoint] Final save - Total: {rows_skipped_from_checkpoint + rows_processed_new} rows ({rows_skipped_from_checkpoint} from checkpoint, {rows_processed_new} newly processed)")
        
        logging.info(f"[Worker {worker_id}] Finished processing {input_file}. Output: {output_filename}")
        
        # Separate user-owned tasks for reporting
        user_owned_df = df_to_save[df_to_save["Task Type"] == "User-Owned"]
        if not user_owned_df.empty:
            user_owned_filename = os.path.join(OUTPUT_DIR, f"user_owned_tasks_{os.path.basename(input_file).split('.')[0]}.csv")
            user_owned_df.to_csv(user_owned_filename, index=False)
            logging.info(f"[Worker {worker_id}] User-owned tasks saved to: {user_owned_filename}")
            return {
                "output_file": output_filename,
                "user_owned_file": user_owned_filename,
                "rows_attempted": processed_count,
                "api_calls": rows_processed_new,  # Only count new API calls
                "api_successes": sum(1 for tag in df_to_save["Relevance Tag"] if tag != 'Irrelevant'),
                "api_failures": sum(1 for tag in df_to_save["Relevance Tag"] if tag == 'Irrelevant'),
                "success_list": [task_evidence for task_evidence, tag in zip(df_to_save["Task Evidence"], df_to_save["Relevance Tag"]) if tag != 'Irrelevant'],
                "failed_list": [task_evidence for task_evidence, tag in zip(df_to_save["Task Evidence"], df_to_save["Relevance Tag"]) if tag == 'Irrelevant'],
                "user_owned_count": len(user_owned_df),
                "standard_count": len(df_to_save) - len(user_owned_df),
                "checkpoint_skipped": rows_skipped_from_checkpoint,
                "checkpoint_new": rows_processed_new
            }
        else:
            return {
                "output_file": output_filename,
                "rows_attempted": processed_count,
                "api_calls": rows_processed_new,  # Only count new API calls
                "api_successes": sum(1 for tag in df_to_save["Relevance Tag"] if tag != 'Irrelevant'),
                "api_failures": sum(1 for tag in df_to_save["Relevance Tag"] if tag == 'Irrelevant'),
                "success_list": [task_evidence for task_evidence, tag in zip(df_to_save["Task Evidence"], df_to_save["Relevance Tag"]) if tag != 'Irrelevant'],
                "failed_list": [task_evidence for task_evidence, tag in zip(df_to_save["Task Evidence"], df_to_save["Relevance Tag"]) if tag == 'Irrelevant'],
                "user_owned_count": 0,
                "standard_count": len(df_to_save),
                "checkpoint_skipped": rows_skipped_from_checkpoint,
                "checkpoint_new": rows_processed_new
            }

    except Exception as e:
        logging.exception(f"[Worker {worker_id}] Failed to process {input_file}: {e}")
        # ===== CHECKPOINT: Save on error too =====
        if RESUME_FROM_CHECKPOINT and checkpoint_data:
            save_checkpoint(checkpoint_data)
            logging.info(f"[Worker {worker_id}] [Checkpoint] Saved progress before error exit")
        return None


def process_file_parallel(file_path, worker_id, checkpoint_data=None):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    result = main(file_path, worker_id, checkpoint_data)  # Get stats dictionary
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
    
    # ===== CHECKPOINT: Load existing checkpoint =====
    global_checkpoint = load_checkpoint()
    
    # ===== API USAGE: Initialize tracking log =====
    initialize_api_usage_log()
    logging.info(f"[Main] API usage log: {API_USAGE_LOG_FILE}")
    
    # Log checkpoint configuration
    logging.info(f"[Main] ===== CHECKPOINT CONFIGURATION =====")
    logging.info(f"[Main] Resume from checkpoint: {RESUME_FROM_CHECKPOINT}")
    logging.info(f"[Main] Checkpoint save frequency: Every {CHECKPOINT_SAVE_FREQUENCY} rows")
    logging.info(f"[Main] Checkpoint cleanup on success: {CHECKPOINT_CLEANUP_ON_SUCCESS}")
    if RESUME_FROM_CHECKPOINT and global_checkpoint:
        total_existing = sum(len(v.get('processed_ids', {})) for k, v in global_checkpoint.items() if not k.startswith('_'))
        logging.info(f"[Main] Found existing checkpoint with {total_existing} processed rows")
    logging.info(f"[Main] ===============================================")
    
    # Log relevance scoring configuration
    logging.info(f"[Main] ===== RELEVANCE SCORING CONFIGURATION =====")
    logging.info(f"[Main] State: {STATE_NAME}")
    logging.info(f"[Main] Relevance Mode: {RELEVANCE_MODE}")
    logging.info(f"[Main]   - 'strict': Only YES/NO answers (Bihar)")
    logging.info(f"[Main]   - 'mixed': Both YES/NO and descriptive (Haryana)")
    logging.info(f"[Main]   - 'descriptive': Only descriptive answers")
    logging.info(f"[Main] Relevant Threshold: >= {RELEVANT_THRESHOLD}")
    logging.info(f"[Main] Partially Relevant Threshold: >= {PARTIALLY_RELEVANT_THRESHOLD}")
    logging.info(f"[Main] ===============================================")
    
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
    total_checkpoint_skipped_all = 0
    total_checkpoint_new_all = 0
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
            executor.submit(process_file_parallel, f, idx + 1, global_checkpoint): f
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
                total_checkpoint_skipped_all += result_stats.get("checkpoint_skipped", 0)
                total_checkpoint_new_all += result_stats.get("checkpoint_new", 0)
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
            
            # Clean up checkpoint after successful completion
            if CHECKPOINT_CLEANUP_ON_SUCCESS and RESUME_FROM_CHECKPOINT:
                cleanup_checkpoint()
                logging.info(f"[Main] Checkpoint cleaned up successfully")
            
            # Create separate user-owned tasks summary if any exist
            if user_owned_files:
                user_owned_summary_file = os.path.join(OUTPUT_DIR, "user_owned_tasks_summary.csv")
                user_owned_df = pd.concat([pd.read_csv(f) for f in user_owned_files], ignore_index=True)
                user_owned_df.to_csv(user_owned_summary_file, index=False)
                logging.info(f"✅ User-owned tasks merged into: {user_owned_summary_file}")
            
            # 🆕 Log final extra keys statistics
            if ENABLE_EXTRA_KEYS:
                logging.info(f"[Main] Final enrollment statistics:")
                for key_name in EXTRA_KEYS.keys():
                    if key_name in merged_df.columns:
                        non_null = merged_df[key_name].notna().sum()
                        total = len(merged_df)
                        logging.info(f"   - {key_name}: {non_null}/{total} ({non_null/total*100:.1f}%)")
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
        
        # Checkpoint statistics
        if RESUME_FROM_CHECKPOINT:
            logging.info("")
            logging.info(f"===== CHECKPOINT STATISTICS =====")
            logging.info(f"Rows skipped (from checkpoint): {total_checkpoint_skipped_all}")
            logging.info(f"New API calls made: {total_checkpoint_new_all}")
            logging.info(f"API calls saved: {total_checkpoint_skipped_all}")
            if total_checkpoint_skipped_all > 0:
                # Rough estimate: $0.01 per API call (adjust based on your API pricing)
                estimated_savings = total_checkpoint_skipped_all * 0.01
                logging.info(f"💰 Estimated cost saved: ${estimated_savings:.2f}")
        
        # API Usage Statistics
        logging.info("")
        logging.info(f"===== API USAGE & COST STATISTICS =====")
        api_summary = generate_api_usage_summary()
        if api_summary:
            logging.info(f"Total API Calls Logged: {api_summary['total_api_calls']}")
            logging.info(f"  - ✅ Successful: {api_summary['successful_calls']}")
            logging.info(f"  - ❌ Failed: {api_summary['failed_calls']}")
            logging.info(f"")
            logging.info(f"Token Usage:")
            logging.info(f"  - Input Tokens: {api_summary['total_input_tokens']:,}")
            logging.info(f"  - Output Tokens: {api_summary['total_output_tokens']:,}")
            logging.info(f"  - Total Tokens: {api_summary['total_tokens']:,}")
            logging.info(f"")
            logging.info(f"Cost Breakdown:")
            logging.info(f"  - Total Cost: ${api_summary['total_cost_usd']:.4f} USD")
            logging.info(f"  - Avg Cost per Call: ${api_summary['avg_cost_per_call']:.6f} USD")
            logging.info(f"  - Avg Input Tokens per Call: {api_summary['avg_input_tokens_per_call']:.0f}")
            logging.info(f"  - Avg Output Tokens per Call: {api_summary['avg_output_tokens_per_call']:.0f}")
            
            if api_summary.get('model_breakdown'):
                logging.info(f"")
                logging.info(f"Per-Model Breakdown:")
                for model, stats in api_summary['model_breakdown'].items():
                    logging.info(f"  {model}:")
                    logging.info(f"    - Tokens: {stats['Total_Tokens']:,}")
                    logging.info(f"    - Cost: ${stats['Total_Cost_USD']:.4f} USD")
            
            if api_summary.get('worker_breakdown'):
                logging.info(f"")
                logging.info(f"Per-Worker Breakdown:")
                for worker, stats in api_summary['worker_breakdown'].items():
                    logging.info(f"  Worker-{worker}:")
                    logging.info(f"    - Tokens: {stats['Total_Tokens']:,}")
                    logging.info(f"    - Cost: ${stats['Total_Cost_USD']:.4f} USD")
            
            logging.info(f"")
            logging.info(f"📊 Detailed API usage log saved to: {API_USAGE_LOG_FILE}")
        else:
            logging.info(f"No API usage data recorded")
        
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