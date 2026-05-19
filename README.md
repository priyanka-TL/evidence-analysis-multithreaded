# Evidence Analysis Multithreaded — MIP Evidence Pipeline

End-to-end pipeline for image-based evidence analysis: preprocess raw CSVs, split them for parallel processing, call the Gemini API on each image, and produce a final labelled output.

---

## Repository Structure

| Script | Description |
|--------|-------------|
| [pre-processor/1-pre-processor.py](pre-processor/1-pre-processor.py) | Filters raw `input.csv`, adds computed columns, writes preprocessed output |
| [pre-processor/2-csv-splitter.py](pre-processor/2-csv-splitter.py) | Splits a preprocessed CSV into N numbered sub-files for parallel workers |
| [run-split-pipeline.py](run-split-pipeline.py) | **Orchestrator** — runs the full pipeline after pre-processing (both modes) |
| [processor/1-main-parallel-script.py](processor/1-main-parallel-script.py) | Parallel Gemini image analysis; one worker thread per sub-split file |
| [processor/2-merge-processed-csv.py](processor/2-merge-processed-csv.py) | Merges per-worker output files into a single CSV (standalone use) |
| [processor/3-invalid-url-&-custom-task-remover.py](processor/3-invalid-url-&-custom-task-remover.py) | Removes rows with missing QA columns; extracts invalid URLs |
| [processor/4-url-validator.py](processor/4-url-validator.py) | Concurrent validation of extracted URLs |
| [processor/5-remove-notvalidated.py](processor/5-remove-notvalidated.py) | Removes rows tagged `notValidated` from merged output |
| [processor/validate-input-output-csv.py](processor/validate-input-output-csv.py) | Compares preprocessed input vs merged output; reports missing rows |
| [test_pipeline.py](test_pipeline.py) | Structural end-to-end tests (no API key needed) |
| [webpage/](webpage/) | Local web UI to visualise processed CSVs |

---

## Setup

```bash
pip install -r requirements.txt
cp .env.sample .env        # copy the template
# Edit .env and fill in your GEMINI_TOKEN1, GEMINI_TOKEN2, ...
```

---

## When to use SPLIT_FILES=no vs yes

| | `SPLIT_FILES=no` (default) | `SPLIT_FILES=yes` |
|---|---|---|
| **Output of pre-processor** | Single `preprocessed_data.csv` | `split_1.csv`, `split_2.csv`, ... |
| **Use when** | Input fits comfortably in memory; dataset is moderate sized | Input is very large (several GB / millions of rows); you want to process in batches |
| **Processing** | One batch through the pipeline | Each split is processed sequentially, one at a time |
| **Resume** | Re-run orchestrator — processor resumes per-file | Re-run orchestrator — completed splits are skipped automatically |
| **Configured by** | `.env`: `SPLIT_FILES=no` | `.env`: `SPLIT_FILES=yes`, `ROWS_PER_FILE=300000` |

---

## Running the pipeline — recommended (orchestrator)

### Step 1 — Configure `.env`

```bash
cp .env.sample .env
# Fill in GEMINI_TOKEN1, GEMINI_TOKEN2, ...
# Set SPLIT_FILES=no or yes
# Set ROWS_PER_FILE if using yes (default: 300000)
```

### Step 2 — Pre-process

```bash
python pre-processor/1-pre-processor.py
```

Reads `input.csv`, applies filters (school list, task rules, image format), enriches rows with task questions.

Output (depending on `SPLIT_FILES`):
- `output-pre-processor/preprocessed_data.csv` (if `SPLIT_FILES=no`)
- `output-pre-processor/split_1.csv`, `split_2.csv`, ... (if `SPLIT_FILES=yes`)

### Step 3 — Run the orchestrator

```bash
python run-split-pipeline.py
```

The orchestrator automatically detects the mode (A or B) and handles everything:

**Mode A** (`SPLIT_FILES=no`):
```
preprocessed_data.csv
  → parallel_input_split_1_files/  (sub-splits)
  → parallel_output_split_1_files/merged_output.csv  (parallel Gemini processing)
  → output/final_output.csv
```

**Mode B** (`SPLIT_FILES=yes`):
```
split_1.csv  →  parallel_input_split_1_files/  →  parallel_output_split_1_files/merged_output.csv  ─┐
split_2.csv  →  parallel_input_split_2_files/  →  parallel_output_split_2_files/merged_output.csv  ─┤
...                                                                                                   │
                                                              output/final_output.csv  ←──────────────┘
```

Each main split runs one at a time. Within each split, sub-files are processed in parallel.

Final output is always at `output/final_output.csv`.

---

## Resume and restart

The orchestrator has built-in resume support:

- **Per split**: if `parallel_output_split_N_files/merged_output.csv` already exists, that split is skipped.
- **Per sub-file**: the processor appends to existing `processed_N.csv` files and skips already-processed rows (via composite key: UUID + Tasks + Task Evidence URL).
- **Final merge**: if only the final output is missing, delete `output/final_output.csv` and re-run the orchestrator — all splits will be skipped and only the merge step runs.

To fully reprocess a split, delete its output directory:
```bash
rm -rf parallel_output_split_2_files/
python run-split-pipeline.py
```

---

## Running scripts manually (step-by-step)

Use this when you want fine-grained control or are debugging a specific stage.

### 1. Pre-processor

```bash
python pre-processor/1-pre-processor.py
```

Key config (in `.env`):
- `SPLIT_FILES` — `no` or `yes`
- `ROWS_PER_FILE` — rows per main split (only when `SPLIT_FILES=yes`)

### 2. CSV Splitter

Splits a single preprocessed file into sub-parts for parallel processing.

```bash
python pre-processor/2-csv-splitter.py
```

Reads `output-pre-processor/preprocessed_data.csv` by default.
To point at a different file (e.g. one of the main splits):
```bash
SPLITTER_INPUT_CSV=output-pre-processor/split_2.csv \
SPLITTER_OUTPUT_DIR=parallel_input_split_2_files \
python pre-processor/2-csv-splitter.py
```

Key config (in `.env`):
- `PARTS` — number of sub-split files (default: 80)

### 3. Parallel Processor

Processes all sub-split files concurrently, calling Gemini for each image.

```bash
python processor/1-main-parallel-script.py
```

Uses `INPUT_DIR=parallel_input_split_1_files` and `OUTPUT_DIR=parallel_output_split_1_files` by default.
To point at a different split:
```bash
INPUT_DIR=parallel_input_split_2_files \
OUTPUT_DIR=parallel_output_split_2_files \
python processor/1-main-parallel-script.py
```

Key config (in `.env`):
- `GEMINI_TOKEN1`, `GEMINI_TOKEN2`, ... — API keys (each is an independent rate-limit bucket)
- `GEMINI_MODEL` — model to use
- `MAX_RPM_PER_TOKEN` — requests per minute per token
- `MAX_PROCESSED_ROWS` — max rows per worker per run
- `ENABLE_RELEVANT_CAP` / `MAX_RELEVANT_PER_USER_TASK` — evidence cap

### 4. Merge processed outputs (standalone)

Only needed if you ran the processor manually and want to merge manually.
The orchestrator does this automatically.

```bash
python processor/2-merge-processed-csv.py
```

Reads from `parallel_output_split_1_files/` by default.
To change the source:
```bash
MERGE_INPUT_DIR=parallel_output_split_2_files python processor/2-merge-processed-csv.py
```

---

## Post-processing scripts

Run these after `output/final_output.csv` has been produced.

### Remove rows with missing QA

Removes rows where the QA columns are empty and extracts their URLs:
```bash
python processor/3-invalid-url-&-custom-task-remover.py
```
Output: `output/final_output.csv` (cleaned), `processor/url.txt` (extracted URLs)

### Remove notValidated rows

Removes rows tagged `notValidated` (rows that hit the Relevant cap):
```bash
python processor/5-remove-notvalidated.py
```
Output: `output/final_cleaned_output.csv`

### Validate extracted URLs

```bash
python processor/4-url-validator.py
```
Reads `processor/url.txt`, validates each URL concurrently, reports results.

---

## Monitoring processing progress

While the processor is running (step B of each split), open a **separate terminal** to watch progress.

### Snapshot — one-time progress report

```bash
# Mode A (default split 1):
python check_progress.py

# Mode B — monitor a specific main split:
INPUT_DIR=parallel_input_split_2_files  OUTPUT_DIR=parallel_output_split_2_files  python check_progress.py
```

Shows per-file progress, % complete, and token assignment for every sub-split file.

### Live monitor — refreshes every 30s

```bash
# Mode A (default):
python monitor.py

# Mode B — watch a specific main split:
INPUT_DIR=parallel_input_split_2_files  OUTPUT_DIR=parallel_output_split_2_files  python monitor.py
```

Shows live throughput (rows/min), ETA, response times, and error rates from the processing log.

> **When using the orchestrator** (`run-split-pipeline.py`), it prints the exact monitor command for each split as it starts, so you can copy-paste it into a second terminal.

---

## Merging

### Automatic (recommended)

The orchestrator handles all merging automatically:
- Each split's processor creates `parallel_output_split_N_files/merged_output.csv` internally.
- After all splits complete, the orchestrator merges them into `output/final_output.csv`.

### Manual (if you ran the processor directly)

**Merge sub-split outputs for one batch:**
```bash
# Default (split 1):
python processor/2-merge-processed-csv.py

# Specific split:
MERGE_INPUT_DIR=parallel_output_split_2_files python processor/2-merge-processed-csv.py
```

---

## Validation

Compare the preprocessed input against the merged output to find missing rows:

```bash
# Default (split 1):
python processor/validate-input-output-csv.py

# Specific split:
VALIDATE_INPUT=parallel_output_split_2_files/merged_output.csv python processor/validate-input-output-csv.py
```

Run structural tests (no API key required):
```bash
python test_pipeline.py
```

Tests cover: pre-processor output, splitter output, row count integrity, group-aware splitting, numeric sort order, and orchestrator mode detection.

---

## Notes

- The `webpage/` directory contains a local dashboard for visualising CSV outputs.
- All data directories (`output/`, `output-pre-processor/`, `parallel_input_split_*/`, `parallel_output_split_*/`) are `.gitignore`d — they contain large generated files.
- Config CSVs (`question.csv`, `school_list.csv`) are committed; `input.csv` is not.
