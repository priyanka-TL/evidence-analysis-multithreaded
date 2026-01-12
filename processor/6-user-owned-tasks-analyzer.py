#!/usr/bin/env python3
"""
User-Owned Tasks Analyzer
Identifies user-owned tasks by comparing against standard tasks from questions CSV
Tasks NOT in the questions CSV = User-Owned Tasks
"""

import pandas as pd
import sys
import os
from datetime import datetime

# ============================================================================
# CONFIGURATION SECTION
# ============================================================================
#
# Modify these constants to customize the behavior of the script:
#
# INPUT_CSV_DEFAULT: Default path to the enhanced CSV file
# QUESTIONS_CSV_DEFAULT: Path to questions CSV containing standard tasks
# OUTPUT_CSV_DEFAULT: Default path for the user-owned tasks output CSV
#
# You can also override these using command-line arguments:
#   python 6-user-owned-tasks-analyzer.py <input_csv> <questions_csv> [output_csv]
#
# ============================================================================

# Input/Output Paths Configuration
INPUT_CSV_DEFAULT = "/home/dell/workspace/EVIDENCE_ANALYSIS/evidence-analysis-multithreaded/pre-processor/parallel_output_split_1_files/enhanced_merged_output_1.csv"
QUESTIONS_CSV_DEFAULT = "/home/dell/workspace/EVIDENCE_ANALYSIS/evidence-analysis-multithreaded/sample_questions_haryana.csv"
OUTPUT_CSV_DEFAULT = "/home/dell/workspace/EVIDENCE_ANALYSIS/evidence-analysis-multithreaded/pre-processor/parallel_output_split_1_files/user_owned_tasks_output.csv"

# CSV column configuration
TASKS_COLUMN = "Tasks"  # Column containing task names in the data
QUESTIONS_TASKS_COLUMN = "TASKS"  # Column containing standard tasks in questions CSV

# Output Configuration
CREATE_SUMMARY = True  # Set to False to skip summary CSV creation
TOP_TASKS_DISPLAY = 10  # Number of top tasks to display
TOP_DISTRICTS_DISPLAY = 5  # Number of top districts to display

# ============================================================================
# END CONFIGURATION SECTION
# ============================================================================

def load_standard_tasks(questions_csv):
    """Load standard tasks from questions CSV file"""
    print(f"\n📖 Loading standard tasks from: {questions_csv}")

    try:
        df_questions = pd.read_csv(questions_csv, encoding='utf-8')

        # Extract tasks from the TASKS column
        standard_tasks = set()
        if QUESTIONS_TASKS_COLUMN in df_questions.columns:
            for task in df_questions[QUESTIONS_TASKS_COLUMN]:
                if pd.notna(task) and str(task).strip():
                    task_clean = str(task).strip().strip("'\"")
                    if task_clean and task_clean != QUESTIONS_TASKS_COLUMN:
                        standard_tasks.add(task_clean)

        print(f"✓ Loaded {len(standard_tasks)} standard tasks:")
        for i, task in enumerate(sorted(standard_tasks), 1):
            print(f"   {i}. {task[:70]}{'...' if len(task) > 70 else ''}")

        return standard_tasks

    except FileNotFoundError:
        print(f"❌ Error: Questions CSV not found: {questions_csv}")
        return set()
    except Exception as e:
        print(f"❌ Error loading questions CSV: {e}")
        return set()

def analyze_user_owned_tasks_from_enhanced_csv(input_csv, questions_csv, output_csv):
    """Analyze and export user-owned tasks by comparing against standard tasks"""

    print("=" * 80)
    print("USER-OWNED TASKS ANALYZER")
    print("Identifying tasks NOT in the questions CSV")
    print("=" * 80)

    # Load standard tasks
    standard_tasks = load_standard_tasks(questions_csv)
    if not standard_tasks:
        print("❌ No standard tasks loaded. Cannot continue.")
        return

    # Load input data
    print(f"\n📖 Loading enhanced CSV from: {input_csv}")
    df = pd.read_csv(input_csv, encoding='utf-8')
    print(f"✓ Loaded {len(df)} records")

    # Check if Tasks column exists
    if TASKS_COLUMN not in df.columns:
        print(f"❌ Error: '{TASKS_COLUMN}' column not found in CSV!")
        print(f"Available columns: {', '.join(df.columns)}")
        return

    # Identify user-owned tasks by checking if task is NOT in standard_tasks
    def is_user_owned_task(task):
        if pd.isna(task):
            return False
        task_clean = str(task).strip().strip("'\"")
        return task_clean not in standard_tasks and task_clean != ""

    # Filter for user-owned tasks
    user_owned_df = df[df[TASKS_COLUMN].apply(is_user_owned_task)].copy()

    print(f"\n📊 ANALYSIS RESULTS:")
    print(f"━" * 80)
    print(f"Total Records: {len(df)}")
    print(f"Standard Task Records: {len(df) - len(user_owned_df)}")
    print(f"User-Owned Task Records: {len(user_owned_df)}")

    if len(user_owned_df) == 0:
        print("\n✅ No user-owned tasks found in the data")
        print("   All tasks match the standard questions CSV")

        # Create empty output file
        user_owned_df.to_csv(output_csv, index=False, encoding='utf-8')
        print(f"\n💾 Empty CSV created at: {output_csv}")
        return

    # Analyze user-owned tasks
    task_counts = user_owned_df[TASKS_COLUMN].value_counts().to_dict()
    district_counts = user_owned_df['District'].value_counts().to_dict()
    user_counts = user_owned_df['UUID'].value_counts()

    print(f"\nUnique User-Owned Task Types: {len(task_counts)}")
    print(f"Unique Users Creating Custom Tasks: {len(user_counts)}")
    print(f"Districts with Custom Tasks: {len(district_counts)}")

    # Top custom tasks
    print(f"\n🏆 TOP {TOP_TASKS_DISPLAY} USER-OWNED TASKS:")
    print(f"━" * 80)
    sorted_tasks = sorted(task_counts.items(), key=lambda x: x[1], reverse=True)[:TOP_TASKS_DISPLAY]
    for i, (task, count) in enumerate(sorted_tasks, 1):
        task_display = task[:60] if isinstance(task, str) else str(task)[:60]
        print(f"{i:2d}. {task_display:<60} ({count} occurrences)")

    # Top districts
    print(f"\n📍 TOP {TOP_DISTRICTS_DISPLAY} DISTRICTS BY CUSTOM TASK COUNT:")
    print(f"━" * 80)
    sorted_districts = sorted(district_counts.items(), key=lambda x: x[1], reverse=True)[:TOP_DISTRICTS_DISPLAY]
    for i, (district, count) in enumerate(sorted_districts, 1):
        print(f"{i}. {district:<30} - {count} custom tasks")

    # Export to CSV
    print(f"\n💾 Exporting user-owned tasks to: {output_csv}")
    user_owned_df.to_csv(output_csv, index=False, encoding='utf-8')
    print(f"✓ Successfully exported {len(user_owned_df)} records")

    # Create summary CSV (if enabled)
    if CREATE_SUMMARY:
        summary_csv = output_csv.replace('.csv', '_summary.csv')
        summary_data = []

        for task, count in sorted_tasks:
            task_records = user_owned_df[user_owned_df[TASKS_COLUMN] == task]
            unique_users = task_records['UUID'].nunique()
            unique_schools = task_records['School Name'].nunique()
            unique_districts = task_records['District'].nunique()

            summary_data.append({
                'Custom_Task_Name': task,
                'Total_Occurrences': count,
                'Unique_Users': unique_users,
                'Unique_Schools': unique_schools,
                'Unique_Districts': unique_districts
            })

        if summary_data:
            summary_df = pd.DataFrame(summary_data)
            summary_df.to_csv(summary_csv, index=False, encoding='utf-8')
            print(f"✓ Summary saved to: {summary_csv}")
    else:
        print("ℹ️  Summary CSV creation is disabled (CREATE_SUMMARY=False)")

    print(f"\n{'=' * 80}")
    print("✓ ANALYSIS COMPLETE")
    print(f"{'=' * 80}\n")

    return {
        'total_custom_tasks': len(user_owned_df),
        'unique_task_types': len(task_counts),
        'unique_users': len(user_counts),
        'task_counts': task_counts,
        'district_counts': district_counts
    }

def main():
    """Main execution"""

    # Default paths - use configuration constants
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Use default paths
    input_csv = INPUT_CSV_DEFAULT
    questions_csv = QUESTIONS_CSV_DEFAULT
    output_csv = OUTPUT_CSV_DEFAULT

    # Allow command-line arguments
    if len(sys.argv) > 1:
        input_csv = sys.argv[1]
    if len(sys.argv) > 2:
        questions_csv = sys.argv[2]
    if len(sys.argv) > 3:
        output_csv = sys.argv[3]

    # Check if files exist
    if not os.path.exists(input_csv):
        print(f"❌ Error: Input file not found: {input_csv}")
        print(f"\n💡 Usage: python {os.path.basename(__file__)} [input_csv] [questions_csv] [output_csv]")
        print(f"   Example: python {os.path.basename(__file__)} enhanced_merged_output_1.csv questions.csv user_owned_tasks.csv")
        sys.exit(1)

    if not os.path.exists(questions_csv):
        print(f"❌ Error: Questions CSV not found: {questions_csv}")
        sys.exit(1)

    # Create output directory if needed
    output_dir = os.path.dirname(output_csv)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # Run analysis
    results = analyze_user_owned_tasks_from_enhanced_csv(input_csv, questions_csv, output_csv)

if __name__ == '__main__':
    main()
