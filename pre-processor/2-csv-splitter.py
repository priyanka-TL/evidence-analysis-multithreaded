import csv
import os
from math import ceil
from dotenv import load_dotenv

# Resolve paths relative to project root (one level above this script)
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)

load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

# Configuration
INPUT_CSV  = os.path.join(_PROJECT_ROOT, "output-pre-processor", "preprocessed_data.csv")
OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "parallel_input_split_1_files")
PARTS = int(os.getenv("PARTS", "80"))

# Create output directory if it doesn't exist
os.makedirs(OUTPUT_DIR, exist_ok=True)

print("Splitting CSV using Python script...")

# Read header and all rows
with open(INPUT_CSV, newline='', encoding='utf-8') as f:
    reader = csv.reader(f)
    header = next(reader)
    rows = list(reader)

total_rows = len(rows)
rows_per_file = ceil(total_rows / PARTS)

# Detect UUID and Tasks column indices for group-aware splitting.
# Group-aware splitting ensures all rows for the same (UUID, Tasks) pair
# land in the same output file, which is required for the per-user-task
# Relevant evidence cap to work correctly in the parallel processing step.
uuid_idx = header.index("UUID") if "UUID" in header else None
task_idx = header.index("Tasks") if "Tasks" in header else None
group_aware = uuid_idx is not None and task_idx is not None

if group_aware:
    print(f"✅ Group-aware splitting enabled (UUID col={uuid_idx}, Tasks col={task_idx})")
else:
    print("⚠️  UUID or Tasks column not found — falling back to fixed-size splitting.")

def get_key(row):
    if group_aware:
        return (row[uuid_idx], row[task_idx])
    return None

# Split the CSV into parts, never cutting in the middle of a (UUID, Tasks) group.
file_num = 1
chunk = []

for i, row in enumerate(rows):
    chunk.append(row)
    at_target = len(chunk) >= rows_per_file
    last_row = (i == len(rows) - 1)
    # Group boundary: next row belongs to a different (UUID, Tasks) pair
    group_boundary = last_row or (get_key(rows[i + 1]) != get_key(row))

    if at_target and (group_boundary or not group_aware):
        output_file = os.path.join(OUTPUT_DIR, f"{file_num}.csv")
        with open(output_file, "w", newline='', encoding='utf-8') as f_out:
            writer = csv.writer(f_out)
            writer.writerow(header)
            writer.writerows(chunk)
        print(f"Created {output_file} with {len(chunk)} rows.")
        file_num += 1
        chunk = []

# Write any remaining rows as the last file
if chunk:
    output_file = os.path.join(OUTPUT_DIR, f"{file_num}.csv")
    with open(output_file, "w", newline='', encoding='utf-8') as f_out:
        writer = csv.writer(f_out)
        writer.writerow(header)
        writer.writerows(chunk)
    print(f"Created {output_file} with {len(chunk)} rows.")

actual_files = file_num if not chunk else file_num
print(f"Total rows: {total_rows}. Split into {file_num} files (target was {PARTS} parts).")