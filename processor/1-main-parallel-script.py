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

import io

# === Constants ===
IMAGE_FORMATS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
PDF_FORMATS = {".pdf"}
EXCEL_FORMATS = {".xlsx", ".xls"}
SUPPORTED_EVIDENCE_FORMATS = IMAGE_FORMATS | PDF_FORMATS | EXCEL_FORMATS
MAX_PROCESSED_ROWS = 520
INPUT_DIR = "../pre-processor/parallel_input_split_1_files"
OUTPUT_DIR = "../pre-processor/parallel_output_split_1_files"
FINAL_OUTPUT_FILE = os.path.join(OUTPUT_DIR, "merged_output_1.csv")

# === STATE CONFIGURATION (from .env) ===
STATE_NAME = os.getenv("STATE_NAME", "HARYANA")  # Default: HARYANA

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

# === 🆕 ANSWER EXTRACTION FUNCTION ===
def extract_yes_no_from_answer(answer_text):
    """
    Extract YES/NO from answer text if present at the start.
    Returns tuple: (clean_answer, full_reasoning)
    
    Examples:
    - "YES. The evidence shows..." → ("YES", "YES. The evidence shows...")
    - "NO. Cannot find..." → ("NO", "NO. Cannot find...")
    - "YES, the data..." → ("YES", "YES, the data...")
    - "NO! Missing..." → ("NO", "NO! Missing...")
    - "The enrollment is 120" → ("The enrollment is 120", "The enrollment is 120")
    """
    if not answer_text or not isinstance(answer_text, str):
        return (answer_text, answer_text)
    
    answer_stripped = answer_text.strip()
    
    # Check if answer starts with YES or NO followed by punctuation or whitespace
    # This pattern handles: period, comma, exclamation, ellipsis, newline, space
    yes_no_patterns = [
        r'^(YES|NO)[.,!;:\s]+(.+)$',  # "YES. text" or "NO, text" or "YES! text" etc
        r'^(YES|NO)\.{2,}',            # "YES..." or "NO..."
        r'^(YES|NO)\.?$',              # Just "YES" or "NO" or "YES." or "NO."
    ]
    
    for pattern in yes_no_patterns:
        match = re.match(pattern, answer_stripped, re.IGNORECASE | re.DOTALL)
        if match:
            yes_no_part = match.group(1).upper()  # Extract YES or NO
            # Full reasoning is the original answer
            return (yes_no_part, answer_stripped)
    
    # If no YES/NO found, return as-is
    return (answer_stripped, answer_stripped)

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
# ========================================
# IMPROVED RELEVANCE CALCULATOR
# ========================================

class RelevanceCalculator:
    """Enhanced relevance calculator with semantic matching and rejection detection"""
    
    STOP_WORDS = {
        'the', 'is', 'are', 'was', 'were', 'has', 'have', 'had', 'be', 'been', 'being',
        'check', 'verify', 'look', 'see', 'find', 'tell', 'whether', 'if', 'does',
        'of', 'in', 'at', 'to', 'for', 'with', 'from', 'by', 'on', 'an', 'a', 'as',
        'and', 'or', 'but', 'not', 'this', 'that', 'these', 'those', 'there',
        'do', 'does', 'did', 'will', 'would', 'should', 'could', 'can', 'may', 'might',
        'what', 'which', 'who', 'where', 'when', 'why', 'how', 'any', 'all', 'each',
        'every', 'some', 'such', 'only', 'own', 'same', 'so', 'than', 'too', 'very'
    }
    
    REJECTION_PATTERNS = [
        r'\bunable to (determine|find|locate|identify|verify)',
        r'\bcannot (determine|find|locate|identify|verify|be determined)',
        r'\bno (information|data|evidence|details|mention)',
        r'\bnot (mentioned|found|available|provided|present|visible|shown)',
        r'\binsufficient (data|information|evidence|details)',
        r'\bdata (not available|unavailable|missing)',
        r'\binformation (not available|unavailable|missing)',
        r'\bevidence (not found|unavailable|missing)',
        r'\bnot clear(ly)? (visible|shown|stated|mentioned)',
        r'\bcannot confirm',
        r'\bunavailable',
        r'\bnone found',
        r'\bno clear',
    ]
    
    def __init__(self):
        self.compiled_rejection_patterns = [re.compile(p, re.IGNORECASE) for p in self.REJECTION_PATTERNS]
    
    def extract_keywords(self, text):
        if not text:
            return []
        words = re.findall(r'\b[a-z]{3,}\b', text.lower())
        return [w for w in words if w not in self.STOP_WORDS]
    
    def is_rejection_answer(self, answer):
        if not answer:
            return True
        answer_lower = answer.lower().strip()
        for pattern in self.compiled_rejection_patterns:
            if pattern.search(answer_lower):
                return True
        return False
    
    def detect_answer_type(self, answer):
        if not answer or not answer.strip():
            return 'REJECTION'
        
        answer_lower = answer.lower().strip()
        
        if self.is_rejection_answer(answer):
            return 'REJECTION'
        
        # Check for YES
        if re.search(r'\byes\b', answer_lower):
            if not re.search(r'\b(not|no|n\'t)\s+yes', answer_lower):
                return 'YES'
        
        # Check for NO
        if re.search(r'\bno\b', answer_lower):
            return 'NO'
        
        return 'DESCRIPTIVE'
    
    def calculate_keyword_overlap(self, question, answer):
        if not question or not answer:
            return 0.0
        
        question_keywords = set(self.extract_keywords(question))
        answer_keywords = set(self.extract_keywords(answer))
        
        if not question_keywords:
            return 0.0
        
        common_keywords = question_keywords & answer_keywords
        overlap_ratio = len(common_keywords) / len(question_keywords)
        
        return min(overlap_ratio, 1.0)
    
    def calculate_evidence_quality(self, answer):
        if not answer or not answer.strip():
            return 0.0
        
        answer_text = answer.strip()
        score = 0.0
        
        # Check for years
        year_matches = re.findall(r'\b20[0-2]\d\b', answer_text)
        if len(year_matches) >= 2:
            score += 0.25
        elif len(year_matches) >= 1:
            score += 0.15
        
        # Check for numbers
        number_matches = re.findall(r'\b\d{1,4}\b', answer_text)
        number_matches = [n for n in number_matches if n not in year_matches]
        if len(number_matches) >= 5:
            score += 0.25
        elif len(number_matches) >= 3:
            score += 0.15
        elif len(number_matches) >= 1:
            score += 0.05
        
        # Check for named entities
        named_entities = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', answer_text)
        if len(named_entities) >= 3:
            score += 0.15
        elif len(named_entities) >= 1:
            score += 0.08
        
        # Answer length
        word_count = len(answer_text.split())
        if word_count >= 50:
            score += 0.20
        elif word_count >= 20:
            score += 0.12
        elif word_count >= 10:
            score += 0.05
        
        # Multiple sentences
        sentences = re.split(r'[.!?]+', answer_text)
        sentences = [s.strip() for s in sentences if s.strip()]
        if len(sentences) >= 3:
            score += 0.15
        elif len(sentences) >= 2:
            score += 0.08
        
        return min(score, 1.0)
    
    def score_single_answer(self, question, answer, question_type='auto'):
        result = {
            'is_rejection': False,
            'keyword_overlap': 0.0,
            'evidence_quality': 0.0,
            'final_score': 0.0
        }
        
        if not answer or not answer.strip():
            result['is_rejection'] = True
            return result
        
        answer_type = self.detect_answer_type(answer)
        
        if answer_type == 'REJECTION':
            result['is_rejection'] = True
            return result
        
        # Auto-detect question type
        if question_type == 'auto':
            question_lower = question.lower() if question else ''
            # Check for percentage/number questions (descriptive)
            if any(p in question_lower for p in ['what is the percentage', 'calculate percentage', 'how much', 'how many', 'what are', 'list', 'describe', 'explain']):
                question_type = 'descriptive'
            # Check for yes/no questions
            elif any(p in question_lower for p in ['whether', 'if ', 'is there', 'are there', 'does ', 'do ', 'has ', 'have ', 'is the', 'are the']):
                question_type = 'yes_no'
            else:
                question_type = 'descriptive'  # Default to descriptive
        
        keyword_overlap = self.calculate_keyword_overlap(question, answer)
        evidence_quality = self.calculate_evidence_quality(answer)
        
        result['keyword_overlap'] = keyword_overlap
        result['evidence_quality'] = evidence_quality
        
        # Scoring logic
        if question_type == 'yes_no':
            if answer_type in ['YES', 'NO']:
                base_score = 0.9
                if keyword_overlap >= 0.5:
                    base_score += 0.1
                result['final_score'] = min(base_score, 1.0)
            else:
                if keyword_overlap >= 0.6:
                    base_score = 0.6 + (keyword_overlap * 0.2)
                elif keyword_overlap >= 0.3:
                    base_score = 0.4 + (keyword_overlap * 0.2)
                else:
                    base_score = keyword_overlap * 0.3
                base_score += evidence_quality * 0.2
                result['final_score'] = min(base_score, 1.0)
        else:  # descriptive
            # CRITICAL: If question asks for specific data (percentage, number) 
            # but answer is just YES/NO, penalize heavily
            if answer_type in ['YES', 'NO']:
                # Check if answer provides actual data despite starting with YES/NO
                word_count = len(answer.split())
                if word_count <= 5:  # Just "YES" or "NO" with minimal text
                    base_score = 0.2  # Low score - didn't answer the question
                else:
                    # Has additional content, score based on that content
                    if keyword_overlap >= 0.6:
                        base_score = 0.4 + (keyword_overlap * 0.2)
                    elif keyword_overlap >= 0.3:
                        base_score = 0.3 + (keyword_overlap * 0.1)
                    else:
                        base_score = 0.2
                    base_score += evidence_quality * 0.3
            else:
                # Normal descriptive answer scoring
                if keyword_overlap >= 0.6:
                    base_score = 0.5 + (keyword_overlap * 0.3)
                elif keyword_overlap >= 0.4:
                    base_score = 0.3 + (keyword_overlap * 0.3)
                elif keyword_overlap >= 0.2:
                    base_score = 0.1 + (keyword_overlap * 0.3)
                else:
                    base_score = 0.0
                
                evidence_bonus = evidence_quality * 0.4
                base_score += evidence_bonus
            
            result['final_score'] = min(base_score, 1.0)
        
        return result
    
    def calculate_task_relevance(self, questions, answers):
        if not questions or not answers:
            return {
                'relevance_tag': 'Irrelevant',
                'relevance_percentage': 0.0,
                'total_questions': 0,
                'answered_questions': 0
            }
        
        if len(questions) != len(answers):
            min_len = min(len(questions), len(answers))
            questions = questions[:min_len]
            answers = answers[:min_len]
        
        question_scores = []
        for question, answer in zip(questions, answers):
            score_result = self.score_single_answer(question, answer)
            question_scores.append(score_result)
        
        total_questions = len(questions)
        rejected_questions = sum(1 for s in question_scores if s['is_rejection'])
        answered_questions = total_questions - rejected_questions
        
        non_rejected_scores = [s['final_score'] for s in question_scores if not s['is_rejection']]
        
        if non_rejected_scores:
            average_score = sum(non_rejected_scores) / len(non_rejected_scores)
        else:
            average_score = 0.0
        
        if total_questions > 0:
            answer_rate = answered_questions / total_questions
            relevance_percentage = (average_score * 0.7 + answer_rate * 0.3) * 100
        else:
            relevance_percentage = 0.0
        
        if relevance_percentage >= 70:
            relevance_tag = 'Relevant'
        elif relevance_percentage >= 40:
            relevance_tag = 'Partially Relevant'
        else:
            relevance_tag = 'Irrelevant'
        
        return {
            'relevance_tag': relevance_tag,
            'relevance_percentage': round(relevance_percentage, 2),
            'total_questions': total_questions,
            'answered_questions': answered_questions
        }

# Initialize global calculator instance
_relevance_calculator = RelevanceCalculator()

def calculate_relevance_tag(answers, task_question=None):
    """
    Enhanced relevance calculation with proper rejection detection and semantic matching.
    Backward compatible wrapper for the improved calculator.
    """
    if not answers or not isinstance(answers, list):
        return 'Irrelevant'
    
    # Handle multiple questions (pipe-separated)
    if task_question and '|' in task_question:
        questions = [q.strip() for q in task_question.split('|') if q.strip()]
    elif task_question:
        questions = [task_question]
    else:
        questions = [f"Question {i+1}" for i in range(len(answers))]
    
    # Ensure matching lengths
    if len(questions) < len(answers):
        questions.extend([questions[-1]] * (len(answers) - len(questions)))
    elif len(answers) < len(questions):
        questions = questions[:len(answers)]
    
    result = _relevance_calculator.calculate_task_relevance(questions, answers)
    return result['relevance_tag']

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


def process_evidence(task_evidence_link, task_evidence_question, task_name=None, max_retries=3):
    global current_token_index
    retries = 0
    
    # Determine file type from URL
    lower_url = task_evidence_link.lower()
    is_pdf = any(lower_url.endswith(ext) for ext in PDF_FORMATS)
    is_excel = any(lower_url.endswith(ext) for ext in EXCEL_FORMATS)
    is_image = any(lower_url.endswith(ext) for ext in IMAGE_FORMATS)
    
    # MIME type for Gemini
    mime_type = "image/jpeg" # Default
    if is_pdf:
        mime_type = "application/pdf"
    
    while retries < max_retries:
        try:
            rate_limiter()
            response_data = httpx.get(task_evidence_link, follow_redirects=True)
            response_data.raise_for_status()
            
            # Content processing variables
            content_parts = []
            
            # Check if this is an enrollment-related task
            task_name_normalized_check = (task_name.strip().rstrip("'.\"").strip() if task_name else "")
            filter_normalized_check = ENROLLMENT_TASK_FILTER.strip().rstrip("'.\"").strip()
            is_enrollment_task = (task_name_normalized_check == filter_normalized_check)
            
            if is_enrollment_task:
                logging.info(f"[Enrollment] Model selection: Using enrollment_model for task '{task_name_normalized_check}'")

            # Base Prompt construction
            prompt_intro = "You are an educational evidence validator. Analyze the given evidence file and answer these questions:"
            if is_excel:
                prompt_intro = "You are an educational evidence validator. Analyze the given EXCEL DATA (provided below as text) and answer these questions:"
                
            prompt = f"""{prompt_intro}

{task_evidence_question}


IMPORTANT: Provide **EXACTLY ONE** answer for **EACH NUMBERED** question above.

- If a question contains "or" (e.g., "sitting or talking"), treat it as a SINGLE question with multiple possibilities, NOT as separate questions.
- Each answer should follow this format: "[YES/NO/PARTIAL]. [Detailed reasoning and evidence from the file]"
- Start with a clear judgment (YES, NO, or PARTIAL) if possible.
- Immediately follow with the reasoning in the same sentence or paragraph.
- Do NOT split the answer into multiple parts.
- Do NOT provide a separate "Reasoning" field. Put all reasoning in the answer text itself.

⚠️ IMPORTANT FOR MULTI-CHOICE QUESTIONS:
If a question asks "...whether X, Y, or Z", your answer should be:
- "X. [explanation]" if X is true
- "Y. [explanation]" if Y is true  
- "Z. [explanation]" if Z is true
DO NOT start with YES/NO for these questions - use the actual option (X, Y, or Z).

Example of GOOD output for "whether consistently increased, decreased, or inconsistently":
["INCONSISTENTLY. The enrollment numbers changed from 31 to 38 to 28 to 27 to 16, showing no consistent pattern."]

Example of BAD output (Do NOT do this):
["NO. The enrollment changed inconsistently..."]  ❌ Contradictory!

Example of GOOD output for yes/no question:
["NO. The enrolment number has decreased over the period as shown by..."]

Example for question with "or" clause:
Question: "Does the picture show two or more people sitting or talking?"
CORRECT: ["NO. The picture shows only one person."]
WRONG: ["NO", "NO"] ❌ (This treats "sitting" and "talking" as separate questions!)

Focus on:
- Evidence quality and relevance
- Educational context
- Answer the EXACT question asked
- ONE answer per NUMBERED question (not per "or" clause)
"""
            # Handle Excel Content (Convert to Text)
            if is_excel:
                try:
                    excel_data = pd.read_excel(io.BytesIO(response_data.content))
                    # Convert to reasonable markdown/string representation
                    # Limiting rows/cols to avoid token explosion if file is huge
                    text_representation = excel_data.head(50).to_string(index=False) 
                    prompt += f"\n\n=== EXCEL DATA CONTENT (First 50 rows) ===\n{text_representation}\n==============================\n"
                    # For Excel, we only send text prompts
                    content_parts = [prompt]
                except Exception as e:
                    logging.error(f"Failed to parse Excel file: {e}")
                    return {"error": f"Failed to parse Excel file: {e}"}

            # Handle Binary Content (Image or PDF)
            else:
                # Add binary part
                content_parts = [
                    {"mime_type": mime_type, "data": base64.b64encode(response_data.content).decode("utf-8")},
                    prompt
                ]

            # Append Enrollment Instructions if needed
            if is_enrollment_task:
                prompt += """

====================================================================================
⚠️ CRITICAL: ENROLLMENT DATA EXTRACTION ⚠️
====================================================================================

You are analyzing an ENROLLMENT REPORT. You MUST extract THREE DIFFERENT numerical values:

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
⚠️ If you cannot find a value clearly, set it to null. Do NOT guess!
====================================================================================
""" 
                # Be careful: for Excel, 'prompt' is already in content_parts[0], so we need to update it.
                # For binary, 'prompt' is content_parts[1].
                if is_excel:
                    content_parts[0] += prompt.split("==============================\n")[-1] # Append only the new instruction part? No, prompt variable was updated locally but not inside the list yet? 
                    # Actually, simply rebuilding content_parts is safer
                    content_parts = [prompt]
                else:
                    content_parts[1] = prompt

            # Select model
            selected_model = model
            if is_enrollment_task:
                selected_model = enrollment_model

            response = selected_model.generate_content(content_parts)
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
                logging.error(f"[Gemini] Error processing {task_evidence_link}: {e}")
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

        # Filter: Keep rows with Task Evidence, but allow Null Task Evidence Question for user-owned tasks
        df_filtered = df[
            ~df["Task Evidence"].isin([None, "Null"])
        ].dropna(subset=["Task Evidence"])

        # Don't filter out rows with Null Task Evidence Question - they might be user-owned tasks
        logging.info(f"[Worker {worker_id}] Total rows after filtering: {len(df_filtered)}")

        processed_count = 0
        task_evidence_qa = []
        task_evidence_qa_reason = []
        relevance_tags = []
        relevance_percentages = []  # 🆕 Track relevance percentage
        task_types = []  # Track if task is standard or user-owned
        
        # 🆕 Initialize extra keys columns
        extra_keys_data = {key: [] for key in EXTRA_KEYS.keys()}

        for idx, row in df_filtered.iterrows():
            task_evidence = str(row["Task Evidence"]).strip()
            task_question_raw = row.get("Task Evidence Question", "")
            task_question = str(task_question_raw).strip() if pd.notna(task_question_raw) and task_question_raw != "Null" else ""
            task_name_raw = str(row.get("Tasks", "")).strip()

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

            if any(task_evidence.lower().endswith(ext) for ext in SUPPORTED_EVIDENCE_FORMATS):
                logging.info(f"[Worker {worker_id}] Processing {'user-owned' if is_user_owned else 'standard'} task row {idx+1}/{len(df_filtered)}")
                response = process_evidence(task_evidence, task_question, task_name_raw)
                if isinstance(response, dict) and "answers" in response:
                    answers = response["answers"]
                    
                    # 🆕 SMART DEDUPLICATION: Ensure exactly one answer per question
                    # Count actual number of questions
                    def count_questions(q_string):
                        if not q_string:
                            return 1
                        # Method 1: Pipe-separated questions
                        if '|' in q_string:
                            return len([q.strip() for q in q_string.split('|') if q.strip()])
                        # Method 2: Multiple numbered questions (e.g., "1. ... 2. ...")
                        numbered = re.findall(r'\d+\.\s+', q_string)
                        if len(numbered) > 1:
                            return len(numbered)
                        # Default: Single question
                        return 1
                    
                    expected_answer_count = count_questions(task_question)
                    actual_answer_count = len(answers)
                    
                    # Only deduplicate if we got MORE answers than expected
                    if actual_answer_count > expected_answer_count:
                        logging.warning(f"[Worker {worker_id}] Row {idx+1}: Expected {expected_answer_count} answer(s) but got {actual_answer_count}")
                        logging.warning(f"[Worker {worker_id}] Question: {task_question}")
                        logging.warning(f"[Worker {worker_id}] Original answers: {answers}")
                        
                        # Remove duplicate answers while preserving order
                        unique_answers = []
                        seen = set()
                        for ans in answers:
                            ans_normalized = str(ans).strip().upper()
                            if ans_normalized not in seen:
                                unique_answers.append(ans)
                                seen.add(ans_normalized)
                        
                        # If deduplication gives us the right count, use it
                        if len(unique_answers) == expected_answer_count:
                            answers = unique_answers
                            logging.info(f"[Worker {worker_id}] ✅ Deduplicated to {len(answers)} unique answers: {answers}")
                        # If still too many, keep first N
                        elif len(unique_answers) > expected_answer_count:
                            answers = unique_answers[:expected_answer_count]
                            logging.info(f"[Worker {worker_id}] ✅ Kept first {expected_answer_count} answers: {answers}")
                        # If fewer unique than expected, keep what we have
                        else:
                            answers = unique_answers
                            logging.warning(f"[Worker {worker_id}] ⚠️ Only {len(answers)} unique answers for {expected_answer_count} questions")
                    
                    # 🆕 NEW LOGIC: Extract YES/NO from answers and create clean columns
                    clean_answers = []
                    full_reasonings = []
                    
                    for ans in answers:
                        clean_ans, full_reason = extract_yes_no_from_answer(ans)
                        clean_answers.append(clean_ans)
                        full_reasonings.append(full_reason)
                    
                    # Store clean answers (just YES/NO if applicable) in Q&A column
                    task_evidence_qa.append(clean_answers)
                    # Store full reasoning in Q&A Reason column
                    task_evidence_qa_reason.append(full_reasonings)
                    
                    # 🆕 Calculate relevance with percentage (using full reasoning for better accuracy)
                    relevance_result = _relevance_calculator.calculate_task_relevance(
                        questions=[task_question] if task_question else [f"Question {i+1}" for i in range(len(answers))],
                        answers=full_reasonings  # Use full reasoning for relevance calculation
                    )
                    relevance_tags.append(relevance_result['relevance_tag'])
                    relevance_percentages.append(relevance_result['relevance_percentage'])

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
                            reasonings_text = ' '.join(str(r) for r in full_reasonings)
                            
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
                                'Reasonings': ' '.join(str(r) for r in full_reasonings)
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
                    relevance_percentages.append(0.0)  # 🆕 Add percentage
                    task_types[-1] = "Failed"  # Update the last task type
                    for key in EXTRA_KEYS.keys():
                        extra_keys_data[key].append(None)
            else:
                logging.info(f"[Worker {worker_id}] Skipping non-supported evidence row {idx+1}")
                task_evidence_qa.append(None)
                task_evidence_qa_reason.append(None)
                relevance_tags.append('Irrelevant')
                relevance_percentages.append(0.0)  # 🆕 Add percentage
                task_types.append("Non-Supported")
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
        df_filtered["Relevance Percentage"] = relevance_percentages  # 🆕 Add percentage column
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
