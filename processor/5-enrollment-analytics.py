"""
Enhanced Enrollment Analytics Script with Extra Keys Support
Adds hierarchical enrollment counting and analytics to the merged output
Supports State → District → Block → School → Cluster hierarchy
Now handles extra keys from the enhanced parallel script
"""

import pandas as pd
import numpy as np
import json
from collections import defaultdict
import os
import re
from dotenv import load_dotenv
load_dotenv()

# ==== CONFIG ====
INPUT_FILE = "../pre-processor/parallel_output_split_1_files/merged_output_1.csv"  # Your merged output file
OUTPUT_FILE = "../pre-processor/parallel_output_split_1_files/enhanced_merged_output_1.csv"  # Output with enrollment data
HIERARCHY_FILE = "enrollment_hierarchy.json"  # Hierarchical enrollment data
HTML_OUTPUT = "../webpage/home.html"  # HTML dashboard output

# ==== STATE CONFIGURATION (from .env) ====
STATE_NAME = os.getenv("STATE_NAME", "HARYANA")
CHART_FOCUS = os.getenv("CHART_FOCUS", "enrollment_increase")
SHOW_ENROLLMENT_INCREASE = os.getenv("SHOW_ENROLLMENT_INCREASE", "true").lower() == "true"
PRIMARY_METRICS = os.getenv("PRIMARY_METRICS", "Enrollment Increase %,School Performance").split(",")

print(f"📍 State Configuration: {STATE_NAME}")
print(f"   Chart Focus: {CHART_FOCUS}")
print(f"   Show Enrollment Increase: {SHOW_ENROLLMENT_INCREASE}")
print(f"   Primary Metrics: {', '.join(PRIMARY_METRICS)}")

# State-specific chart configurations
STATE_CONFIG = {
    "BIHAR": {
        "chart_focus": "subject_class_distribution",
        "primary_metrics": ["Subject Distribution", "Class Distribution"],
        "show_enrollment_increase": False
    },
    "HARYANA": {
        "chart_focus": "enrollment_increase",
        "primary_metrics": ["Enrollment Increase %", "School Performance"],
        "show_enrollment_increase": True
    }
}

# Override with .env values if provided
CURRENT_CONFIG = {
    "chart_focus": CHART_FOCUS,
    "primary_metrics": PRIMARY_METRICS,
    "show_enrollment_increase": SHOW_ENROLLMENT_INCREASE
}

# ==== 🆕 AUTO-DETECT EXTRA KEYS ====
# The script will automatically detect columns starting with "Extra_"
AUTO_DETECT_EXTRA_KEYS = True

# ==== STEP 1: Read merged data ====
print("📖 Reading merged output file...")
df = pd.read_csv(INPUT_FILE)
print(f"✅ Loaded {len(df)} records")
print(f"   Columns: {', '.join(df.columns[:10])}{'...' if len(df.columns) > 10 else ''}")

# 🆕 Detect enrollment columns
enrollment_columns = ['Enrollment_2024', 'Enrollment_2025', 'Enrollment_Increase_Percentage']
extra_key_columns = [col for col in enrollment_columns if col in df.columns]

if extra_key_columns:
    print(f"\n🔑 Detected {len(extra_key_columns)} enrollment columns:")
    for col in extra_key_columns:
        non_null = df[col].notna().sum()
        print(f"   - {col}: {non_null}/{len(df)} values ({non_null/len(df)*100:.1f}%)")
else:
    print("\n🔑 No enrollment columns detected")

# ==== STEP 2: Build hierarchical enrollment structure ====
print("\n🏗️  Building enrollment hierarchy...")

# Create hierarchy levels
hierarchy_levels = ['Declared State', 'District', 'Block', 'School Name']

# Check if 'Cluster' column exists (optional)
if 'Cluster' in df.columns:
    hierarchy_levels.append('Cluster')

def extract_enrollment_data(text_fields):
    """Extract enrollment numbers from text fields"""
    if pd.isna(text_fields):
        return []
    
    text = str(text_fields).lower()
    # Pattern to find numbers that might be enrollment counts
    numbers = re.findall(r'\b(\d+)\b', text)
    return [int(n) for n in numbers if int(n) < 10000 and int(n) > 0]  # Reasonable enrollment range

def aggregate_extra_keys(group_df, extra_columns):
    """Aggregate enrollment values for a group - simplified version"""
    # This function is no longer needed as we're not adding aggregation columns
    # Keeping it for compatibility but it won't be used
    return {}

def build_hierarchy(df):
    """Build nested hierarchy with enrollment counts and extra keys"""
    hierarchy = {}
    
    for _, row in df.iterrows():
        # Start at top level
        current_level = hierarchy
        path = []
        
        for level_name in hierarchy_levels:
            if level_name not in df.columns:
                continue
                
            value = row.get(level_name, 'Unknown')
            if pd.isna(value):
                value = 'Unknown'
            else:
                value = str(value).strip()
            
            path.append(value)
            
            # Initialize level if not exists
            if value not in current_level:
                current_level[value] = {
                    '_children': {},
                    '_evidence_count': 0,
                    '_relevant_count': 0,
                    '_schools': set(),
                    '_enrollment_numbers': [],
                    '_uuid': row.get('UUID', 'N/A'),
                    '_level': level_name,
                    '_extra_keys': {col: [] for col in extra_key_columns}  # 🆕
                }
            
            # Update counts
            current_level[value]['_evidence_count'] += 1
            
            # Track relevance
            relevance = row.get('Relevance Tag', '')
            if relevance == 'Relevant':
                current_level[value]['_relevant_count'] += 1
            
            # Track schools
            if level_name == 'School Name':
                current_level[value]['_schools'].add(value)
            else:
                school = row.get('School Name', 'Unknown')
                if not pd.isna(school):
                    current_level[value]['_schools'].add(str(school))
            
            # Extract enrollment numbers from various fields
            for field in ['Task Remarks', 'Sub-Tasks', 'Descriptive Answer', 'Answer Reasoning']:
                if field in row:
                    numbers = extract_enrollment_data(row[field])
                    current_level[value]['_enrollment_numbers'].extend(numbers)
            
            # 🆕 Store extra keys values
            for col in extra_key_columns:
                if col in row and not pd.isna(row[col]):
                    current_level[value]['_extra_keys'][col].append(row[col])
            
            # Move to next level
            current_level = current_level[value]['_children']
    
    return hierarchy

# Build the hierarchy
enrollment_hierarchy = build_hierarchy(df)

# ==== STEP 3: Calculate enrollment statistics ====
print("\n📊 Calculating enrollment statistics...")

def calculate_stats(hierarchy_dict):
    """Recursively calculate enrollment statistics and aggregate extra keys"""
    for key, data in hierarchy_dict.items():
        if isinstance(data, dict) and '_children' in data:
            # Calculate for children first
            if data['_children']:
                calculate_stats(data['_children'])
            
            # Calculate average enrollment
            enrollment_nums = data.get('_enrollment_numbers', [])
            if enrollment_nums:
                data['_avg_enrollment'] = np.mean(enrollment_nums)
                data['_total_enrollment'] = sum(enrollment_nums)
                data['_min_enrollment'] = min(enrollment_nums)
                data['_max_enrollment'] = max(enrollment_nums)
                data['_enrollment_count'] = len(enrollment_nums)
            else:
                data['_avg_enrollment'] = 0
                data['_total_enrollment'] = 0
                data['_min_enrollment'] = 0
                data['_max_enrollment'] = 0
                data['_enrollment_count'] = 0
            
            # Count unique schools
            data['_school_count'] = len(data.get('_schools', set()))
            
            # 🆕 Aggregate extra keys - REMOVED: No longer adding _avg, _sum, _count columns
            # The original enrollment columns are already in the data

calculate_stats(enrollment_hierarchy)

# ==== STEP 4: Create flattened enrollment summary ====
print("\n📋 Creating enrollment summary...")

enrollment_summary = []

def flatten_hierarchy(hierarchy_dict, parent_path=None):
    """Flatten hierarchy for easier CSV export"""
    if parent_path is None:
        parent_path = []
    
    for key, data in hierarchy_dict.items():
        if isinstance(data, dict) and '_level' in data:
            current_path = parent_path + [key]
            
            record = {
                'Level': data['_level'],
                'Name': key,
                'Full_Path': ' > '.join(current_path),
                'Evidence_Count': data['_evidence_count'],
                'Relevant_Count': data['_relevant_count'],
                'School_Count': data['_school_count'],
                'Avg_Enrollment': round(data['_avg_enrollment'], 2),
                'Total_Enrollment': data['_total_enrollment'],
                'Min_Enrollment': data['_min_enrollment'],
                'Max_Enrollment': data['_max_enrollment'],
                'Enrollment_Data_Points': data['_enrollment_count']
            }
            
            # 🆕 No longer adding aggregated extra keys - they create redundant columns
            # The original enrollment columns are preserved in the main dataframe
            
            # Add hierarchical columns
            for i, level in enumerate(hierarchy_levels):
                if i < len(current_path):
                    record[level] = current_path[i]
                else:
                    record[level] = ''
            
            enrollment_summary.append(record)
            
            # Process children
            if data['_children']:
                flatten_hierarchy(data['_children'], current_path)

flatten_hierarchy(enrollment_hierarchy)

# Convert to DataFrame
summary_df = pd.DataFrame(enrollment_summary)

# ==== STEP 5: Add enrollment columns to original dataframe ====
print("\n🔗 Adding enrollment analytics to original data...")

# Create lookup dictionary for fast access
enrollment_lookup = {}
for _, row in summary_df.iterrows():
    key = row['Full_Path']
    lookup_data = {
        'School_Count': row['School_Count'],
        'Avg_Enrollment': row['Avg_Enrollment'],
        'Evidence_Count': row['Evidence_Count'],
        'Relevant_Count': row['Relevant_Count']
    }
    
    # 🆕 REMOVED: No longer adding aggregation columns (_avg, _sum, _count)
    # Original enrollment columns are already in the data
    
    enrollment_lookup[key] = lookup_data

# Add enrollment analytics columns to original dataframe
def add_enrollment_columns(row):
    """Add enrollment analytics for each row - simplified version"""
    path_parts = []
    for level in hierarchy_levels:
        if level in row and not pd.isna(row[level]):
            path_parts.append(str(row[level]).strip())
    
    path = ' > '.join(path_parts)
    
    if path in enrollment_lookup:
        return pd.Series(enrollment_lookup[path])
    else:
        # Return default values only for the 4 analytics columns
        default_data = {
            'School_Count': 0,
            'Avg_Enrollment': 0,
            'Evidence_Count': 0,
            'Relevant_Count': 0
        }
        return pd.Series(default_data)

# Apply enrollment data
enrollment_cols = df.apply(add_enrollment_columns, axis=1)
df = pd.concat([df, enrollment_cols], axis=1)

# ==== STEP 6: Save outputs ====
print("\n💾 Saving outputs...")

# Save enhanced merged output
df.to_csv(OUTPUT_FILE, index=False)
print(f"✅ Enhanced output saved: {OUTPUT_FILE}")

# Save enrollment summary
summary_output = "enrollment_summary_by_level.csv"
summary_df.to_csv(summary_output, index=False)
print(f"✅ Enrollment summary saved: {summary_output}")

# Save hierarchy as JSON for visualization
def clean_hierarchy_for_json(hierarchy_dict):
    """Clean hierarchy dict for JSON serialization"""
    cleaned = {}
    for key, data in hierarchy_dict.items():
        if isinstance(data, dict):
            cleaned_data = {
                'level': data.get('_level', 'unknown'),
                'evidence_count': data.get('_evidence_count', 0),
                'relevant_count': data.get('_relevant_count', 0),
                'school_count': data.get('_school_count', 0),
                'avg_enrollment': round(data.get('_avg_enrollment', 0), 2),
                'total_enrollment': data.get('_total_enrollment', 0),
                'enrollment_count': data.get('_enrollment_count', 0),
                'children': clean_hierarchy_for_json(data.get('_children', {}))
            }
            
            # 🆕 Add extra keys aggregations
            extra_keys_agg = data.get('_extra_keys_agg', {})
            if extra_keys_agg:
                cleaned_data['extra_keys'] = {k: (round(v, 2) if isinstance(v, float) else v) 
                                              for k, v in extra_keys_agg.items()}
            
            cleaned[key] = cleaned_data
    return cleaned

hierarchy_json = clean_hierarchy_for_json(enrollment_hierarchy)
with open(HIERARCHY_FILE, 'w') as f:
    json.dump(hierarchy_json, f, indent=2)
print(f"✅ Hierarchy JSON saved: {HIERARCHY_FILE}")

# ==== STEP 7: Generate report ====
print("\n" + "="*80)
print(" ENROLLMENT ANALYTICS REPORT ".center(80, "="))
print("="*80)

print(f"\n📊 OVERALL STATISTICS:")
print(f"   • Total records: {len(df)}")
print(f"   • Unique states: {df['Declared State'].nunique()}")
print(f"   • Unique districts: {df['District'].nunique()}")
print(f"   • Unique blocks: {df['Block'].nunique()}")
print(f"   • Unique schools: {df['School Name'].nunique()}")

# 🆕 Extra keys summary
if extra_key_columns:
    print(f"\n🔑 EXTRA KEYS SUMMARY:")
    for col in extra_key_columns:
        non_null = df[col].notna().sum()
        print(f"   • {col}: {non_null} values")
        
        # Try to show statistics if numeric
        try:
            numeric_values = pd.to_numeric(df[col], errors='coerce').dropna()
            if len(numeric_values) > 0:
                print(f"     - Average: {numeric_values.mean():.2f}")
                print(f"     - Min: {numeric_values.min():.2f}")
                print(f"     - Max: {numeric_values.max():.2f}")
        except:
            pass

print(f"\n🎯 ENROLLMENT HIERARCHY:")
print(f"   • Levels tracked: {len(hierarchy_levels)}")
print(f"   • Total hierarchy nodes: {len(enrollment_summary)}")

# Top states by evidence
print(f"\n🏆 TOP STATES BY EVIDENCE:")
state_summary = summary_df[summary_df['Level'] == 'Declared State'].sort_values('Evidence_Count', ascending=False).head(5)
for _, row in state_summary.iterrows():
    print(f"   • {row['Name']}: {row['Evidence_Count']} evidence, {row['School_Count']} schools")

# Top districts by enrollment
print(f"\n📈 TOP DISTRICTS BY ENROLLMENT DATA:")
district_summary = summary_df[summary_df['Level'] == 'District'].sort_values('Total_Enrollment', ascending=False).head(5)
for _, row in district_summary.iterrows():
    if row['Total_Enrollment'] > 0:
        print(f"   • {row['Name']}: Total={row['Total_Enrollment']}, Avg={row['Avg_Enrollment']:.1f}")

# Schools needing attention (low relevant evidence)
print(f"\n⚠️  SCHOOLS NEEDING ATTENTION:")
school_summary = summary_df[summary_df['Level'] == 'School Name']
low_relevance = school_summary[school_summary['Relevant_Count'] < school_summary['Evidence_Count'] * 0.3]
for _, row in low_relevance.head(5).iterrows():
    relevance_pct = (row['Relevant_Count'] / row['Evidence_Count'] * 100) if row['Evidence_Count'] > 0 else 0
    print(f"   • {row['Name']}: {relevance_pct:.1f}% relevant ({row['Relevant_Count']}/{row['Evidence_Count']})")

print(f"\n💾 OUTPUT FILES:")
print(f"   • Enhanced data: {OUTPUT_FILE}")
print(f"   • Summary by level: {summary_output}")
print(f"   • Hierarchy JSON: {HIERARCHY_FILE}")

print("\n" + "="*80)
print(" PROCESSING COMPLETE ".center(80, "="))
print("="*80 + "\n")

# ==== STEP 8: Create sample hierarchy display ====
print("📊 Sample Hierarchy View:\n")

def print_hierarchy(hierarchy_dict, level=0, max_depth=3):
    """Print hierarchy in tree format"""
    if level >= max_depth:
        return
    
    for key, data in list(hierarchy_dict.items())[:3]:  # Show first 3 at each level
        if isinstance(data, dict) and '_level' in data:
            indent = "  " * level
            icon = "📍" if level == 0 else "📊" if level == 1 else "🏫" if level == 2 else "📁"
            
            extra_keys_display = ""
            if data.get('_extra_keys_agg'):
                # Show first 2 extra keys
                extra_items = list(data['_extra_keys_agg'].items())[:2]
                if extra_items:
                    extra_keys_display = ", " + ", ".join(f"{k.split('_')[-1]}: {v:.1f}" if isinstance(v, float) else f"{k}: {v}" 
                                                          for k, v in extra_items)
            
            print(f"{indent}{icon} {key}")
            print(f"{indent}   Evidence: {data['_evidence_count']}, "
                  f"Schools: {data['_school_count']}, "
                  f"Avg Enrollment: {data['_avg_enrollment']:.1f}"
                  f"{extra_keys_display}")
            
            if data['_children']:
                print_hierarchy(data['_children'], level + 1, max_depth)

print_hierarchy(enrollment_hierarchy)
print("\n(Showing first 3 items at each level for brevity...)\n")