import os
import pandas as pd
from pathlib import Path

# Resolve paths relative to project root (one level above this script)
_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)

# ==== CONFIG ====
INPUT_DIR   = os.environ.get("MERGE_INPUT_DIR",
                  os.path.join(_PROJECT_ROOT, "parallel_output_split_1_files"))
OUTPUT_FILE = os.environ.get("MERGE_OUTPUT_FILE", "merged_output.csv")
SORT_FILES = True  # Set to True to sort files before merging (useful for numbered files)

# ==== DUPLICATE HANDLING ====
# Options: "keep_all", "remove_duplicates", "remove_duplicates_keep_first", "remove_duplicates_keep_last"
DUPLICATE_HANDLING = "keep_all"  # Change this to control how duplicates are handled

# ==== STEP 1: Find all CSV files ====
print("🔍 Searching for CSV files...")
csv_files = [
    os.path.join(INPUT_DIR, f) 
    for f in os.listdir(INPUT_DIR) 
    if f.endswith('.csv')
]

if not csv_files:
    print(f"❌ No CSV files found in '{INPUT_DIR}'")
    exit(1)

# Sort files if enabled (useful for files like 1.csv, 2.csv, etc.)
if SORT_FILES:
    csv_files.sort(key=lambda x: int(''.join(filter(str.isdigit, os.path.basename(x)))) if any(c.isdigit() for c in os.path.basename(x)) else os.path.basename(x))

print(f"✅ Found {len(csv_files)} CSV files")

# ==== STEP 2: Read and merge CSVs ====
print("\n📊 Reading CSV files...")
dataframes = []
file_stats = []

for idx, csv_file in enumerate(csv_files, 1):
    try:
        df = pd.read_csv(csv_file)
        rows = len(df)
        dataframes.append(df)
        file_stats.append({
            'file': os.path.basename(csv_file),
            'rows': rows,
            'columns': len(df.columns)
        })
        print(f"   [{idx}/{len(csv_files)}] {os.path.basename(csv_file)}: {rows} rows, {len(df.columns)} columns")
    except Exception as e:
        print(f"   ⚠️  Error reading {csv_file}: {e}")

if not dataframes:
    print("❌ No CSV files could be read successfully")
    exit(1)

# ==== STEP 3: Merge all dataframes ====
print("\n🔗 Merging CSV files...")
merged_df = pd.concat(dataframes, ignore_index=True)

# ==== STEP 4: Handle Duplicates ====
original_row_count = len(merged_df)
duplicates_before = merged_df.duplicated().sum()

if DUPLICATE_HANDLING == "remove_duplicates":
    merged_df = merged_df.drop_duplicates(keep=False)
    print(f"   ℹ️  Removed ALL duplicate rows (both original and copies)")
elif DUPLICATE_HANDLING == "remove_duplicates_keep_first":
    merged_df = merged_df.drop_duplicates(keep='first')
    print(f"   ℹ️  Removed duplicate rows (kept first occurrence)")
elif DUPLICATE_HANDLING == "remove_duplicates_keep_last":
    merged_df = merged_df.drop_duplicates(keep='last')
    print(f"   ℹ️  Removed duplicate rows (kept last occurrence)")
else:  # keep_all
    print(f"   ℹ️  Keeping all rows including duplicates")

rows_removed = original_row_count - len(merged_df)

# ==== STEP 5: Save merged CSV ====
output_path = os.path.join(INPUT_DIR, OUTPUT_FILE)
merged_df.to_csv(output_path, index=False)
output_size = os.path.getsize(output_path) / 1024  # Size in KB

print(f"💾 Merged CSV saved to: {output_path}")

# ==== STEP 6: Generate Merge Report ====
print("\n" + "="*80)
print(" MERGE REPORT ".center(80, "="))
print("="*80)

print(f"\n📁 INPUT:")
print(f"   • Source directory: {INPUT_DIR}")
print(f"   • Files merged: {len(dataframes)}")

print(f"\n📊 FILE DETAILS:")
for stat in file_stats:
    print(f"   • {stat['file']}: {stat['rows']} rows, {stat['columns']} columns")

print(f"\n📈 MERGED DATA:")
print(f"   • Total rows before dedup: {original_row_count}")
print(f"   • Duplicate rows found: {duplicates_before}")
print(f"   • Rows removed: {rows_removed}")
print(f"   • Final row count: {len(merged_df)}")
print(f"   • Total columns: {len(merged_df.columns)}")
print(f"   • File size: {output_size:.2f} KB")

print(f"\n🔧 DUPLICATE HANDLING:")
print(f"   • Method: {DUPLICATE_HANDLING}")
if duplicates_before > 0:
    if DUPLICATE_HANDLING == "keep_all":
        print(f"   • ✅ All {duplicates_before} duplicate rows were kept in output")
    else:
        print(f"   • ✅ {rows_removed} duplicate rows were removed")
else:
    print(f"   • ✅ No duplicate rows detected")

# Show column names
print(f"\n📋 COLUMNS ({len(merged_df.columns)}):")
if len(merged_df.columns) <= 10:
    for col in merged_df.columns:
        print(f"   • {col}")
else:
    for col in list(merged_df.columns[:5]):
        print(f"   • {col}")
    print(f"   ... and {len(merged_df.columns) - 5} more columns")

print(f"\n💾 OUTPUT:")
print(f"   • File: {output_path}")
print(f"   • Size: {output_size:.2f} KB")

print("\n" + "="*80)
print(" MERGE COMPLETED SUCCESSFULLY ".center(80, "="))
print("="*80 + "\n")