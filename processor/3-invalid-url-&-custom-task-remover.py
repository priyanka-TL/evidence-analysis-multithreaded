import os
import pandas as pd
import re
from pathlib import Path

# Resolve paths relative to project root (one level above this script)
_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)

# ==== CONFIG ====
INPUT_CSV  = os.path.join(_PROJECT_ROOT,
                 os.environ.get("CLEANER_INPUT_CSV",
                     "parallel_output_split_1_files/merged_output.csv"))
OUTPUT_DIR = os.path.join(_PROJECT_ROOT,
                 os.environ.get("CLEANER_OUTPUT_DIR", "output"))
OUTPUT_FILE = "final_output.csv"

# Column names to check for null/empty values
CHECK_COLUMNS = ["Task evidence Q and A", "Task evidence Q and A Reason"]
URL_COLUMN = "Task Evidence"  # Column to extract URLs from

# ==== STEP 1: Create output directory ====
os.makedirs(OUTPUT_DIR, exist_ok=True)
print(f"📁 Output directory: {OUTPUT_DIR}\n")

# ==== STEP 2: Read CSV ====
print(f"📖 Reading CSV: {INPUT_CSV}")
try:
    df = pd.read_csv(INPUT_CSV)
    print(f"✅ Loaded {len(df)} rows, {len(df.columns)} columns\n")
except Exception as e:
    print(f"❌ Error reading CSV: {e}")
    exit(1)

# ==== STEP 3: Verify columns exist ====
missing_cols = []
for col in CHECK_COLUMNS + [URL_COLUMN]:
    if col not in df.columns:
        missing_cols.append(col)

if missing_cols:
    print(f"❌ Missing columns in CSV: {', '.join(missing_cols)}")
    print(f"\n📋 Available columns:")
    for col in df.columns:
        print(f"   • {col}")
    exit(1)

# ==== STEP 4: Identify rows with null/empty values ====
print(f"🔍 Checking for null/empty values in:")
print(f"   • {CHECK_COLUMNS[0]}")
print(f"   • {CHECK_COLUMNS[1]}\n")

# Create mask for rows with null or empty values in either column
mask = df[CHECK_COLUMNS[0]].isna() | df[CHECK_COLUMNS[1]].isna() | \
       (df[CHECK_COLUMNS[0]].astype(str).str.strip() == '') | \
       (df[CHECK_COLUMNS[1]].astype(str).str.strip() == '')

rows_with_nulls = df[mask].copy()
rows_to_keep = df[~mask].copy()

null_count = len(rows_with_nulls)
print(f"📊 Found {null_count} rows with null/empty values")
print(f"✅ {len(rows_to_keep)} rows are valid and will be kept\n")

# ==== STEP 5: Extract URLs from null rows ====
extracted_urls = []

if null_count > 0:
    print(f"🔗 Extracting URLs from '{URL_COLUMN}' column:\n")
    print("=" * 80)
    
    for idx, row in rows_with_nulls.iterrows():
        url_value = row[URL_COLUMN]
        
        # Extract URL using regex (handles various formats)
        if pd.notna(url_value):
            url_str = str(url_value)
            # Find URLs in the text
            url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
            found_urls = re.findall(url_pattern, url_str)
            
            if found_urls:
                for url in found_urls:
                    extracted_urls.append(url)
                    print(f"   • {url}")
            else:
                # If no URL pattern found, print the raw value
                extracted_urls.append(url_str)
                print(f"   • {url_str}")
        else:
            print(f"   • [No URL - Cell is empty]")
    
    print("=" * 80)
    print(f"\n📝 Total URLs extracted: {len(extracted_urls)}\n")
else:
    print("✅ No null/empty rows found - nothing to extract\n")

# ==== STEP 6: Save cleaned CSV ====
output_path = os.path.join(OUTPUT_DIR, OUTPUT_FILE)
rows_to_keep.to_csv(output_path, index=False)
output_size = os.path.getsize(output_path) / 1024

print(f"💾 Cleaned CSV saved to: {output_path}")
print(f"   • Size: {output_size:.2f} KB\n")

# ==== STEP 7: Generate Report ====
print("=" * 80)
print(" CLEANING REPORT ".center(80, "="))
print("=" * 80)

print(f"\n📁 INPUT:")
print(f"   • File: {INPUT_CSV}")
print(f"   • Original rows: {len(df)}")

print(f"\n🔍 CHECKED COLUMNS:")
print(f"   • {CHECK_COLUMNS[0]}")
print(f"   • {CHECK_COLUMNS[1]}")

print(f"\n📊 RESULTS:")
print(f"   • Rows with null/empty values: {null_count}")
print(f"   • Rows removed: {null_count}")
print(f"   • Rows kept: {len(rows_to_keep)}")
print(f"   • URLs extracted: {len(extracted_urls)}")

print(f"\n💾 OUTPUT:")
print(f"   • File: {output_path}")
print(f"   • Final row count: {len(rows_to_keep)}")
print(f"   • Size: {output_size:.2f} KB")

if len(extracted_urls) > 0:
    print(f"\n🔗 EXTRACTED URLS ({len(extracted_urls)}):")
    print("=" * 80)
    for i, url in enumerate(extracted_urls, 1):
        print(f"{i}. {url}")
    print("=" * 80)

print("\n" + "=" * 80)
print(" CLEANING COMPLETED SUCCESSFULLY ".center(80, "="))
print("=" * 80 + "\n")