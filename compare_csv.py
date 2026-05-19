import csv
import os
from collections import Counter

def analyze_csv(input_file, output_file):
    print(f"--- Comparing {input_file} and {output_file} ---")
    
    # Read Input
    input_rows = []
    input_keys = set()
    with open(input_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            input_rows.append(row)
            input_keys.add((row['UUID'], row['Tasks'], row['Task Evidence']))
    
    # Read Output and check malformed rows
    output_rows = []
    malformed_rows = 0
    header = None
    with open(output_file, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
            for i, row in enumerate(reader):
                if len(row) != len(header):
                    malformed_rows += 1
                else:
                    output_rows.append(dict(zip(header, row)))
        except StopIteration:
            pass

    # Analysis
    input_count = len(input_rows)
    output_count = len(output_rows)
    
    # Exact duplicate rows in output
    output_raw_lines = []
    with open(output_file, 'r', encoding='utf-8') as f:
        next(f) # skip header
        output_raw_lines = f.readlines()
    exact_duplicates = len(output_raw_lines) - len(set(output_raw_lines))
    
    # Duplicate Task Evidence
    task_evidence_counts = Counter(row['Task Evidence'] for row in output_rows)
    dup_task_evidence_count = sum(1 for count in task_evidence_counts.values() if count > 1)
    
    # Key not in input
    not_in_input = 0
    for row in output_rows:
        key = (row['UUID'], row['Tasks'], row['Task Evidence'])
        if key not in input_keys:
            not_in_input += 1
            
    # Top 10 duplicated Task Evidence URLs
    top_10_dups = sorted([(url, count) for url, count in task_evidence_counts.items() if count > 1], key=lambda x: x[1], reverse=True)[:10]

    print(f"Input row count: {input_count}")
    print(f"Output row count: {output_count}")
    print(f"Malformed row lengths: {malformed_rows}")
    print(f"Exact duplicate rows in output: {exact_duplicates}")
    print(f"Distinct Task Evidence values with >1 occurrence: {dup_task_evidence_count}")
    print(f"Output rows (UUID, Tasks, Task Evidence) not in input: {not_in_input}")
    print("Top 10 duplicated Task Evidence URLs:")
    for url, count in top_10_dups:
        print(f"  {count}: {url}")
    print("\n")

files = [6, 30]
for f in files:
    in_f = f"parallel_input_split_1_files/{f}.csv"
    out_f = f"parallel_output_split_1_files/processed_{f}.csv"
    if os.path.exists(in_f) and os.path.exists(out_f):
        analyze_csv(in_f, out_f)
    else:
        print(f"Missing file(s) for {f}")
