import csv
import os
from math import ceil
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables from .env file (look in parent directory)
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

# Configuration
INPUT_CSV = "/home/dell/workspace/EVIDENCE_ANALYSIS/evidence-analysis-multithreaded/pre-processor/output-pre-processor/split_1.csv"
OUTPUT_DIR = "parallel_input_split_1_files"
# no of rows to split into
PARTS = int(os.getenv("ROWS_PER_FILE", 5))
print(PARTS, os.getenv("ROWS_PER_FILE"))
# Create output directory if it doesn't exist
os.makedirs(OUTPUT_DIR, exist_ok=True)

print("Splitting CSV using Python script...")

# Count total data rows (excluding header)
with open(INPUT_CSV, newline='', encoding='utf-8') as f:
    reader = csv.reader(f)
    header = next(reader)
    rows = list(reader)

total_rows = len(rows)
rows_per_file = ceil(total_rows / PARTS)

# Split the CSV into parts
for i in range(PARTS):
    start = i * rows_per_file
    end = start + rows_per_file
    output_file = os.path.join(OUTPUT_DIR, f"{i+1}.csv")
    
    with open(output_file, "w", newline='', encoding='utf-8') as f_out:
        writer = csv.writer(f_out)
        writer.writerow(header)
        writer.writerows(rows[start:end])
    
    print(f"Created {output_file} with {len(rows[start:end])} rows.")

print(f"Total rows: {total_rows}. Split into {PARTS} parts.")