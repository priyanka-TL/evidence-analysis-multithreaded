import os
import csv
import math
from urllib.parse import urlparse
from tqdm import tqdm  # Import tqdm for the progress bar

# === Configuration ===
# INPUT_CSV = "/home/dell/workspace/EVIDENCE_ANALYSIS/evidence-analysis-multithreaded/input/sample_input.csv"
INPUT_CSV = "/home/dell/workspace/EVIDENCE_ANALYSIS/evidence-analysis-multithreaded/input/sample_haryana_custom_task.csv"
QUESTION_CSV = "/home/dell/workspace/EVIDENCE_ANALYSIS/evidence-analysis-multithreaded/input/questions.csv"
FILTER_CSV = "/home/dell/workspace/EVIDENCE_ANALYSIS/evidence-analysis-multithreaded/input/school_list.csv"
USE_SCHOOL_FILTER = os.getenv("USE_SCHOOL_FILTER", False)  # Set to True to filter by school_list.csv, False to skip this filter
OUTPUT_DIR = "output-pre-processor"

# === SPLIT CONFIGURATION ===
SPLIT_FILES = os.getenv("SPLIT_FILES", "yes")  # Set to "yes" to split into multiple files, "no" for single file
ROWS_PER_FILE = os.getenv("ROWS_PER_FILE", 10000)  # Only used if SPLIT_FILES = "yes"

# === IMAGE FORMATS ===
IMAGE_FORMATS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}

# Create output directory
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Counters for skipped rows
skip_task_start = 0
skip_evidence_null = 0
skip_school_mismatch = 0
skip_non_image = 0
total_input_rows = 0 # This will be set correctly below

# === Step 1: Load FILTER_CSV school codes into a set ===
valid_school_codes = set()
if USE_SCHOOL_FILTER and os.path.exists(FILTER_CSV):
    with open(FILTER_CSV, newline='', encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            school_code = row.get("UDISE+ SCHOOL CODE", "").strip()
            if school_code:
                valid_school_codes.add(school_code)
    print(f"✅ Loaded {len(valid_school_codes)} school codes from '{FILTER_CSV}'")
elif USE_SCHOOL_FILTER and not os.path.exists(FILTER_CSV):
    print(f"⚠️  USE_SCHOOL_FILTER is True but FILTER_CSV '{FILTER_CSV}' not found. Skipping school filter.")
    USE_SCHOOL_FILTER = False
else:
    print(f"⏭️  School filtering disabled (USE_SCHOOL_FILTER={USE_SCHOOL_FILTER})")

# === Helper function for cleaning cell values ===
def clean_cell(value):
    """Strips whitespace AND common quote characters from the ends."""
    if not isinstance(value, str):
        return ""
    # Strip whitespace, then strip both single and double quotes
    return value.strip().strip("'\"")

# === Helper function to check if URL is an image ===
def is_image_url(url):
    """Check if URL points to an image file"""
    url = clean_cell(url) # Clean the URL string first for *checking*
    if not url or url.lower() == "null":
        return False
    try:
        parsed = urlparse(url)
        path = parsed.path.lower()
        return any(path.endswith(ext) for ext in IMAGE_FORMATS)
    except:
        return False

# === Step 2: Load QUESTION_CSV into dictionary (TASK NAME → Refined Question) ===
lookup_dict = {}
with open(QUESTION_CSV, newline='', encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        task_name = clean_cell(row.get("TASK NAME", ""))
        refined_question = row.get("Refined questions using tool and webpage", "").strip()
        if task_name:  # only add valid rows
            lookup_dict[task_name] = refined_question

# === District Renaming Map ===
DISTRICT_REPLACEMENTS = {
    "W Champaran": "West Champaran",
    "E. Champaran": "East Champaran",
    "Kaimur (Bhabua)": "Kaimur",
    "Aurangabad (Bihar)": "Aurangabad"
}

# === Step 3: Load INPUT_CSV and filter ===

# --- NEW: Load all rows into a list first to get the *correct* count ---
print(f"Loading data from {INPUT_CSV}...")
all_rows = []
try:
    with open(INPUT_CSV, newline='', encoding="utf-8") as infile:
        reader = csv.DictReader(infile)
        all_rows = list(reader)
        total_input_rows = len(all_rows) # This is the CORRECT row count
        header = reader.fieldnames
except FileNotFoundError:
    print(f"Error: INPUT_CSV '{INPUT_CSV}' not found.")
    exit()
except Exception as e:
    print(f"Error reading {INPUT_CSV}: {e}")
    exit()

if header is None:
    print("Error: CSV Header is empty. Cannot proceed.")
    exit()
print(f"Loaded {total_input_rows} data rows to process.")
# --- END NEW ---


filtered_rows = []

# Add new columns
new_columns = [
    "Task Evidence Question",
    "Task evidence Q and A",
    "Task evidence Q and A Reason",
    "Relevance Tag",
    "Image Preview"
]

final_header = list(header)
for col in new_columns:
    if col not in final_header:
        final_header.append(col)

# --- NEW: Iterate over the list 'all_rows' instead of the 'reader' object ---
for row in tqdm(all_rows, total=total_input_rows, desc="Processing input CSV"):
    
    school_id = row.get("School ID", "").strip()
    task = clean_cell(row.get("Tasks", "")) # Clean task for lookup
    evidence = row.get("Task Evidence", "") # Get raw evidence

    # Rule 0: Skip if School ID not in FILTER_CSV (only if USE_SCHOOL_FILTER is enabled)
    if USE_SCHOOL_FILTER and school_id not in valid_school_codes:
        skip_school_mismatch += 1
        continue

    # Rule 1: Skip if task starts with 1 or 8
    # if task.startswith("1") or task.startswith("8"):
    #     skip_task_start += 1
    #     continue

    # Rule 2: Skip if evidence is empty or "null" (after cleaning for check)
    cleaned_evidence = clean_cell(evidence)
    if cleaned_evidence == "" or cleaned_evidence.lower() == "null":
        skip_evidence_null += 1
        continue

    # Rule 3: Skip if evidence URL is not an image format
    if not is_image_url(evidence): # Send the raw evidence to be checked
        skip_non_image += 1
        continue

    # === Step 4: Fill additional columns & Clean District ===
    row["Task Evidence Question"] = lookup_dict.get(task, "Null")
    row["Task evidence Q and A"] = ""
    row["Task evidence Q and A Reason"] = ""
    row["Relevance Tag"] = ""
    row["Image Preview"] = ""
    
    # Apply District replacement
    current_district = row.get("District", "")
    row["District"] = DISTRICT_REPLACEMENTS.get(current_district, current_district)

    # Row passes all checks
    filtered_rows.append([row.get(h, "") for h in final_header])

# === Step 5: Output - Single file or Multiple files based on configuration ===
if SPLIT_FILES.lower() == "no":
    # Single file output
    output_file = os.path.join(OUTPUT_DIR, "preprocessed_data.csv")
    with open(output_file, "w", newline='', encoding="utf-8") as outfile:
        writer = csv.writer(outfile)
        writer.writerow(final_header)
        writer.writerows(filtered_rows)
    
    print(f"✅ Created: {output_file} ({len(filtered_rows)} rows)")
    print(f"Mode: Single file output")
    
else:
    # Split into multiple files
    total_files = math.ceil(len(filtered_rows) / ROWS_PER_FILE)
    
    for i in range(total_files):
        start_index = i * ROWS_PER_FILE
        end_index = start_index + ROWS_PER_FILE
        chunk = filtered_rows[start_index:end_index]

        output_file = os.path.join(OUTPUT_DIR, f"split_{i+1}.csv")
        with open(output_file, "w", newline='', encoding="utf-8") as outfile:
            writer = csv.writer(outfile)
            writer.writerow(final_header)
            writer.writerows(chunk)

        print(f"✅ Created: {output_file} ({len(chunk)} rows)")
    
    print(f"Mode: Split into {total_files} files ({ROWS_PER_FILE} rows per file)")

# === Summary ===
print(f"\n{'='*70}")
print(f"{'PREPROCESSING SUMMARY':^70}")
print(f"{'='*70}")
# --- This 'total_input_rows' variable is now CORRECT ---
print(f"\nTotal CSV rows: {total_input_rows}") 
print(f"\n{'Filter Stage':<50} {'Removed':<10} {'Remaining'}")
print(f"{'-'*70}")

remaining_after_school = total_input_rows - skip_school_mismatch
if USE_SCHOOL_FILTER:
    print(f"{'School ID not in filter list':<50} {skip_school_mismatch:<10} {remaining_after_school}")
else:
    print(f"{'School ID filtering':<50} {'SKIPPED':<10} {remaining_after_school}")

remaining_after_task = remaining_after_school - skip_task_start
print(f"{'Task starts with 1 or 8':<50} {skip_task_start:<10} {remaining_after_task}")

remaining_after_evidence = remaining_after_task - skip_evidence_null
print(f"{'Task Evidence empty or null':<50} {skip_evidence_null:<10} {remaining_after_evidence}")

remaining_after_non_image = remaining_after_evidence - skip_non_image
print(f"{'Task Evidence is not an image (video/other)':<50} {skip_non_image:<10} {remaining_after_non_image}")

print(f"\n{'='*70}")
print(f"Final output CSV rows: {len(filtered_rows)}")
print(f"{'='*70}")

# === Script Checkpoints Section ===
print(f"\n{'='*70}")
print(f"{'SCRIPT CHECKPOINTS':^70}")
print(f"{'='*70}")
print("This script performed the following actions:")

print("\n--- 1. PRE-LOADING ---")
print(f"✅ Loaded valid school codes from '{FILTER_CSV}'")
print(f"✅ Loaded task/question map from '{QUESTION_CSV}'")
print("✅ Defined district name replacements (e.g., 'W. Champaran' -> 'West Champaran')")

print("\n--- 2. MAIN PROCESSING (Row-by-Row) ---")
print(f"✅ Loaded all {total_input_rows} rows from '{INPUT_CSV}' (This is the correct count).")
print("✅ Iterated through all rows with a progress bar.")
print("\n  For EACH row, the following filters were applied (in order):")
print("  ➡️ 1. SKIPPED if 'School ID' was not in the valid school list.")
print("  ➡️ 2. SKIPPED if 'Tasks' value (after cleaning) started with '1' or '8'.")
print("  ➡️ 3. SKIPPED if 'Task Evidence' (after cleaning) was empty or 'null'.")
print("  ➡️ 4. SKIPPED if 'Task Evidence' URL was not an image (e.g., .mp4, .pdf).")

print("\n  For EACH row that PASSED all filters:")
print("  ➡️ Cleaned and matched 'Tasks' to populate 'Task Evidence Question'.")
print("  ➡️ Cleaned 'District' names (e.g., 'Kaimur (Bhabua)' -> 'Kaimur').")
print("  ➡️ Set the 'Image Preview' column to be empty.")
print("  ➡️ Kept the original 'Task Evidence' value.")
print("  ➡️ Added row to the final output list.")

print("\n--- 3. FINAL OUTPUT ---")
print(f"✅ Wrote {len(filtered_rows)} passed rows to the final CSV file.")
print("✅ Printed the final summary report with skip/remaining counts.")
print(f"{'='*70}")





# import os
# import csv
# import math
# from urllib.parse import urlparse
# from tqdm import tqdm 

# # === Configuration ===
# INPUT_CSV = "/Users/user/Documents/AI/parallel-process/input/017F35E575D87A3FB5ED3D90A3E69355_20250904.csv"
# QUESTION_CSV = "/Users/user/Documents/AI/parallel-process/input/aug_sample_questions.csv"
# FILTER_CSV = "/Users/user/Documents/AI/parallel-process/input/school_list.csv"
# OUTPUT_DIR = "pre_split_csvs"

# # === SPLIT CONFIGURATION ===
# SPLIT_FILES = "no"  # Set to "yes" to split into multiple files, "no" for single file
# ROWS_PER_FILE = 10000  # Only used if SPLIT_FILES = "yes"

# # === IMAGE FORMATS ===
# IMAGE_FORMATS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}

# # Create output directory
# os.makedirs(OUTPUT_DIR, exist_ok=True)

# # Counters for skipped rows
# skip_task_start = 0
# skip_evidence_null = 0
# skip_school_mismatch = 0
# skip_non_image = 0

# # Get total row count for progress bar
# print(f"Calculating total rows in {INPUT_CSV}...")
# try:
#     with open(INPUT_CSV, 'r', encoding="utf-8") as f:
#         # -1 to exclude the header row
#         total_input_rows = sum(1 for _ in f) - 1
# except FileNotFoundError:
#     print(f"Error: INPUT_CSV '{INPUT_CSV}' not found.")
#     exit()
# except Exception as e:
#     print(f"Error reading {INPUT_CSV}: {e}")
#     exit()
# print(f"Found {total_input_rows} data rows to process.")

# # === Step 1: Load FILTER_CSV school codes into a set ===
# valid_school_codes = set()
# with open(FILTER_CSV, newline='', encoding="utf-8") as f:
#     reader = csv.DictReader(f)
#     for row in reader:
#         school_code = row.get("UDISE+ SCHOOL CODE", "").strip()
#         if school_code:
#             valid_school_codes.add(school_code)

# # === Helper function for cleaning cell values ===
# def clean_cell(value):
#     """Strips whitespace AND common quote characters from the ends."""
#     if not isinstance(value, str):
#         return ""
#     # Strip whitespace, then strip both single and double quotes
#     return value.strip().strip("'\"")

# # === Helper function to check if URL is an image ===
# def is_image_url(url):
#     """Check if URL points to an image file"""
#     url = clean_cell(url) # Clean the URL string first for *checking*
#     if not url or url.lower() == "null":
#         return False
#     try:
#         parsed = urlparse(url)
#         path = parsed.path.lower()
#         return any(path.endswith(ext) for ext in IMAGE_FORMATS)
#     except:
#         return False

# # === Step 2: Load QUESTION_CSV into dictionary (TASK NAME → Refined Question) ===
# lookup_dict = {}
# with open(QUESTION_CSV, newline='', encoding="utf-8") as f:
#     reader = csv.DictReader(f)
#     for row in reader:
#         task_name = clean_cell(row.get("TASK NAME", ""))
#         refined_question = row.get("Refined questions using tool and webpage", "").strip()
#         if task_name:  # only add valid rows
#             lookup_dict[task_name] = refined_question

# # === District Renaming Map ===
# DISTRICT_REPLACEMENTS = {
#     "W. Champaran": "West Champaran",
#     "E. Champaran": "East Champaran",
#     "Kaimur (Bhabua)": "Kaimur",
#     "Aurangabad (Bihar)": "Aurangabad"
# }

# # === Step 3: Load INPUT_CSV and filter ===
# filtered_rows = []
# with open(INPUT_CSV, newline='', encoding="utf-8") as infile:
#     reader = csv.DictReader(infile)
    
#     header = reader.fieldnames
#     if header is None:
#         print("Error: CSV Header is empty. Cannot proceed.")
#         exit()

#     # Add new columns
#     new_columns = [
#         "Task Evidence Question",
#         "Task evidence Q and A",
#         "Task evidence Q and A Reason",
#         "Relevance Tag",
#         "Image Preview"
#     ]
    
#     final_header = list(header)
#     for col in new_columns:
#         if col not in final_header:
#             final_header.append(col)

#     # Wrap the reader with tqdm for the progress bar
#     for row in tqdm(reader, total=total_input_rows, desc="Processing input CSV"):
        
#         school_id = row.get("School ID", "").strip()
#         task = clean_cell(row.get("Tasks", "")) # Clean task for lookup
#         evidence = row.get("Task Evidence", "") # Get raw evidence

#         # Rule 0: Skip if School ID not in FILTER_CSV
#         if school_id not in valid_school_codes:
#             skip_school_mismatch += 1
#             continue

#         # Rule 1: Skip if task starts with 1 or 8
#         if task.startswith("1") or task.startswith("8"):
#             skip_task_start += 1
#             continue

#         # Rule 2: Skip if evidence is empty or "null" (after cleaning for check)
#         cleaned_evidence = clean_cell(evidence)
#         if cleaned_evidence == "" or cleaned_evidence.lower() == "null":
#             skip_evidence_null += 1
#             continue

#         # Rule 3: Skip if evidence URL is not an image format
#         if not is_image_url(evidence): # Send the raw evidence to be checked
#             skip_non_image += 1
#             continue

#         # === Step 4: Fill additional columns & Clean District ===
#         row["Task Evidence Question"] = lookup_dict.get(task, "Null")
#         row["Task evidence Q and A"] = ""
#         row["Task evidence Q and A Reason"] = ""
#         row["Relevance Tag"] = ""
#         row["Image Preview"] = ""
        
#         # --- NOTE: The "Task Evidence" column is NO longer overwritten ---

#         # Apply District replacement
#         current_district = row.get("District", "")
#         row["District"] = DISTRICT_REPLACEMENTS.get(current_district, current_district)

#         # Row passes all checks
#         filtered_rows.append([row.get(h, "") for h in final_header])

# # === Step 5: Output - Single file or Multiple files based on configuration ===
# if SPLIT_FILES.lower() == "no":
#     # Single file output
#     output_file = os.path.join(OUTPUT_DIR, "preprocessed_data.csv")
#     with open(output_file, "w", newline='', encoding="utf-8") as outfile:
#         writer = csv.writer(outfile)
#         writer.writerow(final_header)
#         writer.writerows(filtered_rows)
    
#     print(f"✅ Created: {output_file} ({len(filtered_rows)} rows)")
#     print(f"Mode: Single file output")
    
# else:
#     # Split into multiple files
#     total_files = math.ceil(len(filtered_rows) / ROWS_PER_FILE)
    
#     for i in range(total_files):
#         start_index = i * ROWS_PER_FILE
#         end_index = start_index + ROWS_PER_FILE
#         chunk = filtered_rows[start_index:end_index]

#         output_file = os.path.join(OUTPUT_DIR, f"split_{i+1}.csv")
#         with open(output_file, "w", newline='', encoding="utf-8") as outfile:
#             writer = csv.writer(outfile)
#             writer.writerow(final_header)
#             writer.writerows(chunk)

#         print(f"✅ Created: {output_file} ({len(chunk)} rows)")
    
#     print(f"Mode: Split into {total_files} files ({ROWS_PER_FILE} rows per file)")

# # === Summary ===
# print(f"\n{'='*70}")
# print(f"{'PREPROCESSING SUMMARY':^70}")
# print(f"{'='*70}")
# print(f"\nTotal CSV rows: {total_input_rows}")
# print(f"\n{'Filter Stage':<50} {'Removed':<10} {'Remaining'}")
# print(f"{'-'*70}")

# remaining_after_school = total_input_rows - skip_school_mismatch
# print(f"{'School ID not in filter list':<50} {skip_school_mismatch:<10} {remaining_after_school}")

# remaining_after_task = remaining_after_school - skip_task_start
# print(f"{'Task starts with 1 or 8':<50} {skip_task_start:<10} {remaining_after_task}")

# remaining_after_evidence = remaining_after_task - skip_evidence_null
# print(f"{'Task Evidence empty or null':<50} {skip_evidence_null:<10} {remaining_after_evidence}")

# remaining_after_non_image = remaining_after_evidence - skip_non_image
# print(f"{'Task Evidence is not an image (video/other)':<50} {skip_non_image:<10} {remaining_after_non_image}")

# print(f"\n{'='*70}")
# print(f"Final output CSV rows: {len(filtered_rows)}")
# print(f"{'='*70}")

# # === Script Checkpoints Section ===
# print(f"\n{'='*70}")
# print(f"{'SCRIPT CHECKPOINTS':^70}")
# print(f"{'='*70}")
# print("This script performed the following actions:")

# print("\n--- 1. PRE-LOADING ---")
# print(f"✅ Loaded valid school codes from '{FILTER_CSV}'")
# print(f"✅ Loaded task/question map from '{QUESTION_CSV}'")
# print("✅ Defined district name replacements (e.g., 'W. Champaran' -> 'West Champaran')")

# print("\n--- 2. MAIN PROCESSING (Row-by-Row) ---")
# print(f"✅ Iterated through all {total_input_rows} rows in '{INPUT_CSV}' with a progress bar.")
# print("\n  For EACH row, the following filters were applied (in order):")
# print("  ➡️ 1. SKIPPED if 'School ID' was not in the valid school list.")
# print("  ➡️ 2. SKIPPED if 'Tasks' value (after cleaning) started with '1' or '8'.")
# print("  ➡️ 3. SKIPPED if 'Task Evidence' (after cleaning) was empty or 'null'.")
# print("  ➡️ 4. SKIPPED if 'Task Evidence' URL was not an image (e.g., .mp4, .pdf).")

# print("\n  For EACH row that PASSED all filters:")
# print("  ➡️ Cleaned and matched 'Tasks' to populate 'Task Evidence Question'.")
# print("  ➡️ Cleaned 'District' names (e.g., 'Kaimur (Bhabua)' -> 'Kaimur').")
# print("  ➡️ Added some extra rows to the final output list.")

# print("\n--- 3. FINAL OUTPUT ---")
# print(f"✅ Wrote {len(filtered_rows)} passed rows to the final CSV file.")
# print("✅ Printed the final summary report with skip/remaining counts.")
# print(f"{'='*70}")