# 🧠 Evidence Analysis Multithreaded — MIP Evidence Pipeline

### Overview
This repository contains a lightweight pipeline to preprocess CSV files, split them for parallel execution, perform **image-based evidence analysis** (via a generative model), merge processed results, validate URLs, and clean invalid rows.

---

## 📁 Repository Structure

| Path | Description |
|------|--------------|
| **pre-processor/1-pre-processor.py** | Loads and filters raw CSVs, adds computed columns (`clean_cell`, `is_image_url`), and generates single or split preprocessed CSVs. |
| **pre-processor/2-csv-splitter.py** | Splits large CSVs into smaller chunks for parallel processing. |
| **processor/1-main-parallel-script.py** | Main orchestration script for parallel execution. Handles per-file processing via `google-generativeai`, `httpx`, `openpyxl`, and includes rate-limiting and token rotation (`get_gemini_tokens_from_env`). |
| **processor/2-merge-processed-csv.py** | Merges parallel outputs into a single `merged_output.csv`. |
| **processor/3-invalid-url-&-custom-task-remover.py** | Removes invalid rows (where QA columns are null) and extracts URLs from *Task Evidence* fields. |
| **processor/4-url-validator.py** | Concurrent URL validation tool (`load_urls_from_file`, `validate_url`, `validate_urls_concurrent`). |
| **processor/validate-input-output-csv.py** | Compares source vs. merged output CSVs and logs missing rows. |
| **webpage/** | Local web UI to visualize processed CSVs. |
| **.env** | Environment file to store GEMINI API tokens (e.g., `GEMINI_TOKEN1="..."`). |

---

## ⚙️ Prerequisites
- **Python** 3.8 or above
- Install dependencies:
  ```
  pip install -r requirements.txt
  ```

  Create a virtual environment
  python3 -m venv venv

  Activate the virtual environment
  source venv/bin/activate

## 🌍 Environment Setup
If using the generative model in processor/1-main-parallel-script.py, create a .env file and include your GEMINI tokens:
```
GEMINI_TOKEN1="your_token_here"
```
These tokens are fetched dynamically using the get_gemini_tokens_from_env function.

## 🚀 Quickstart
### 1. Preprocessing
Edit input/output paths in pre-processor/1-pre-processor.py.
Run:
```
python pre-processor/1-pre-processor.py
```
Output will be stored under output-pre-processor/ as either:
* preprocessed_data.csv, or
* multiple split_*.csv files (if splitting is enabled).

### 2. Split CSVs for Parallel Processing
#### Option A:
Run the splitter script manually:
```
python pre-processor/2-csv-splitter.py
```
#### Option B:
Enable the SPLIT_FILES=yes flag in the preprocessor to auto-generate split files.

### 3. Main Parallel Processing
Set the following variables at the top of processor/1-main-parallel-script.py:
* INPUT_DIR
* OUTPUT_DIR
* FINAL_OUTPUT_FILE

Ensure .env contains valid GEMINI tokens.
Run:
```
python processor/1-main-parallel-script.py
```
Key functions:
* main — processes a single input file
* process_file_parallel — wrapper for thread pool execution
* calculate_relevance_tag — maps responses to relevance tags

### 4. Merge Processed Outputs
After processing completes:
```
python processor/2-merge-processed-csv.py
```
Update INPUT_DIR in the script if necessary.

### 5. URL Validation and Data Cleaning

Clean invalid rows and extract URLs:
```
python processor/3-invalid-url-&-custom-task-remover.py
```

Validate URLs:
```
python processor/4-url-validator.py
```

Adjust constants (URLS_FILE, MAX_WORKERS, TIMEOUT) as needed.

### 6. Validate Input vs Output Consistency
Compare preprocessed vs merged outputs:
```
python processor/validate-input-output-csv.py
```

## 🧩 Notes & Tips

* If you’re not using the generative API, you can comment out or stub those sections in processor/1-main-parallel-script.py.
* The list of URLs to validate is located in processor/url.txt.
* The webpage/ directory contains dashboards for visualizing CSV outputs.
