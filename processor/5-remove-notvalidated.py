import os
import pandas as pd

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ==== CONFIG ====
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
INPUT_CSV = os.path.join(_PROJECT_ROOT, "merged_output.csv") # Path to the merged output CSV to clean
OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "output")             # Directory to write the cleaned file
OUTPUT_FILE = "final_cleaned_output.csv"  # Name of the cleaned output file
NOT_VALIDATED_TAG = "notValidated"        # Relevance Tag value to remove

# ==== STEP 1: Create output directory ====
os.makedirs(OUTPUT_DIR, exist_ok=True)
print(f"📁 Output directory: {OUTPUT_DIR}\n")

# ==== STEP 2: Read CSV ====
print(f"📖 Reading CSV: {INPUT_CSV}")
try:
    df = pd.read_csv(INPUT_CSV)
    print(f"✅ Loaded {len(df):,} rows, {len(df.columns)} columns\n")
except FileNotFoundError:
    print(f"❌ File not found: {INPUT_CSV}")
    exit(1)
except Exception as e:
    print(f"❌ Error reading CSV: {e}")
    exit(1)

# ==== STEP 3: Verify Relevance Tag column exists ====
if "Relevance Tag" not in df.columns:
    print("❌ 'Relevance Tag' column not found in CSV.")
    print(f"\n📋 Available columns:")
    for col in df.columns:
        print(f"   • {col}")
    exit(1)

# ==== STEP 4: Separate notValidated rows from valid rows ====
original_count = len(df)
not_validated_mask = df["Relevance Tag"] == NOT_VALIDATED_TAG
not_validated_count = int(not_validated_mask.sum())
cleaned_df = df[~not_validated_mask].copy()

print(f"🔍 Rows with '{NOT_VALIDATED_TAG}' tag: {not_validated_count:,}")
print(f"✅ Rows to keep: {len(cleaned_df):,}\n")

# ==== STEP 5: Save cleaned CSV ====
output_path = os.path.join(OUTPUT_DIR, OUTPUT_FILE)
cleaned_df.to_csv(output_path, index=False)
output_size = os.path.getsize(output_path) / 1024

# ==== STEP 6: Print Report ====
print("=" * 60)
print(" CLEANUP REPORT ".center(60, "="))
print("=" * 60)

print(f"\n📁 INPUT:")
print(f"   • File: {INPUT_CSV}")
print(f"   • Original rows: {original_count:,}")

print(f"\n🗑️  REMOVED:")
print(f"   • Tag matched: '{NOT_VALIDATED_TAG}'")
print(f"   • Rows removed: {not_validated_count:,}")

print(f"\n💾 OUTPUT:")
print(f"   • File: {output_path}")
print(f"   • Final rows: {len(cleaned_df):,}")
print(f"   • Size: {output_size:.2f} KB")

print(f"\n📊 SUMMARY:")
removed_pct = (not_validated_count / original_count * 100) if original_count > 0 else 0
kept_pct = 100 - removed_pct
print(f"   • Removed: {not_validated_count:,} ({removed_pct:.1f}%)")
print(f"   • Kept:    {len(cleaned_df):,} ({kept_pct:.1f}%)")

print("\n" + "=" * 60)
print(" CLEANUP COMPLETED SUCCESSFULLY ".center(60, "="))
print("=" * 60 + "\n")
