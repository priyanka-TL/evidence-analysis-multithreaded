import os
import pandas as pd

# Resolve paths relative to project root (one level above this script)
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)

# ==== CONFIG ====
input_path  = os.path.join(_PROJECT_ROOT, "parallel_output_split_1_files", "merged_output.csv")
output_path = os.path.join(_PROJECT_ROOT, "parallel_output_split_1_files", "final_output.csv")

# ==== LOAD ====
df = pd.read_csv(input_path, dtype=str)
total_rows = len(df)

if "Relevance Tag" not in df.columns:
    raise ValueError("'Relevance Tag' column not found in merged_output.csv")

# ==== FILTER ====
not_validated_mask = df["Relevance Tag"].str.strip() == "notValidated"
not_validated_count = not_validated_mask.sum()
df_filtered = df[~not_validated_mask]

# ==== WRITE ====
df_filtered.to_csv(output_path, index=False)

# ==== REPORT ====
print("=" * 70)
print(" OUTPUT FILTER REPORT ".center(70, "="))
print("=" * 70)
print(f"\n  Input  : {input_path}")
print(f"  Output : {output_path}")
print(f"\n  Total rows in merged output : {total_rows}")
print(f"  Removed (notValidated)      : {not_validated_count}")
print(f"  Rows written to final output: {len(df_filtered)}")

print(f"\n  Relevance Tag breakdown (final output):")
for tag, count in df_filtered["Relevance Tag"].value_counts().items():
    print(f"    • {tag:<20}: {count}")

print("\n" + "=" * 70)
print(" DONE ".center(70, "="))
print("=" * 70 + "\n")
