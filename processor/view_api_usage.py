#!/usr/bin/env python3
"""
API Usage Log Viewer

Analyzes and displays API usage statistics from the api_usage_log.csv file.
Provides detailed breakdown by model, worker, and time period.
"""

import pandas as pd
import os
import sys
from datetime import datetime

# Path to API usage log
API_USAGE_LOG = "../pre-processor/parallel_output_split_1_files/api_usage_log.csv"

def format_cost(cost):
    """Format cost with appropriate precision"""
    return f"${cost:.6f}" if cost < 0.01 else f"${cost:.4f}"

def load_api_log():
    """Load the API usage log"""
    if not os.path.exists(API_USAGE_LOG):
        print(f"❌ API usage log not found: {API_USAGE_LOG}")
        print("Run the main script first to generate usage data.")
        return None
    
    try:
        df = pd.read_csv(API_USAGE_LOG)
        if df.empty:
            print("⚠️  API usage log is empty")
            return None
        return df
    except Exception as e:
        print(f"❌ Error loading API usage log: {e}")
        return None

def print_summary(df):
    """Print overall summary"""
    print("\n" + "="*80)
    print("📊 API USAGE SUMMARY")
    print("="*80)
    
    total_calls = len(df)
    successful = len(df[df['Status'] == 'success'])
    failed = len(df[df['Status'] == 'failure'])
    
    print(f"\n🔢 Total API Calls: {total_calls:,}")
    print(f"   ✅ Successful: {successful:,} ({successful/total_calls*100:.1f}%)")
    print(f"   ❌ Failed: {failed:,} ({failed/total_calls*100:.1f}%)")
    
    print(f"\n💬 Token Usage:")
    print(f"   Input Tokens:  {df['Input_Tokens'].sum():,}")
    print(f"   Output Tokens: {df['Output_Tokens'].sum():,}")
    print(f"   Total Tokens:  {df['Total_Tokens'].sum():,}")
    
    print(f"\n💰 Cost Analysis:")
    total_cost = df['Total_Cost_USD'].astype(float).sum()
    print(f"   Total Cost: {format_cost(total_cost)} USD")
    print(f"   Avg Cost per Call: {format_cost(total_cost/total_calls)} USD")
    print(f"   Avg Input Tokens: {df['Input_Tokens'].mean():.0f}")
    print(f"   Avg Output Tokens: {df['Output_Tokens'].mean():.0f}")

def print_model_breakdown(df):
    """Print per-model breakdown"""
    print("\n" + "="*80)
    print("🤖 PER-MODEL BREAKDOWN")
    print("="*80)
    
    models = df.groupby('Model_Name').agg({
        'Input_Tokens': 'sum',
        'Output_Tokens': 'sum',
        'Total_Tokens': 'sum',
        'Total_Cost_USD': lambda x: x.astype(float).sum(),
        'Model_Name': 'count'
    }).rename(columns={'Model_Name': 'Call_Count'})
    
    for model_name, row in models.iterrows():
        print(f"\n📱 {model_name}")
        print(f"   Calls: {row['Call_Count']:,}")
        print(f"   Tokens: {int(row['Total_Tokens']):,} (In: {int(row['Input_Tokens']):,}, Out: {int(row['Output_Tokens']):,})")
        print(f"   Cost: {format_cost(row['Total_Cost_USD'])} USD")

def print_worker_breakdown(df):
    """Print per-worker breakdown"""
    print("\n" + "="*80)
    print("👷 PER-WORKER BREAKDOWN")
    print("="*80)
    
    workers = df.groupby('Worker_ID').agg({
        'Input_Tokens': 'sum',
        'Output_Tokens': 'sum',
        'Total_Tokens': 'sum',
        'Total_Cost_USD': lambda x: x.astype(float).sum(),
        'Worker_ID': 'count'
    }).rename(columns={'Worker_ID': 'Call_Count'}).sort_values('Total_Cost_USD', ascending=False)
    
    for worker_id, row in workers.iterrows():
        print(f"\n🔧 Worker-{worker_id}")
        print(f"   Calls: {row['Call_Count']:,}")
        print(f"   Tokens: {int(row['Total_Tokens']):,}")
        print(f"   Cost: {format_cost(row['Total_Cost_USD'])} USD")

def print_api_type_breakdown(df):
    """Print per-API-call-type breakdown"""
    print("\n" + "="*80)
    print("📑 PER-API-CALL-TYPE BREAKDOWN")
    print("="*80)
    
    api_types = df.groupby('API_Call_Type').agg({
        'Input_Tokens': 'sum',
        'Output_Tokens': 'sum',
        'Total_Tokens': 'sum',
        'Total_Cost_USD': lambda x: x.astype(float).sum(),
        'API_Call_Type': 'count'
    }).rename(columns={'API_Call_Type': 'Call_Count'}).sort_values('Total_Cost_USD', ascending=False)
    
    for api_type, row in api_types.iterrows():
        print(f"\n📝 {api_type}")
        print(f"   Calls: {row['Call_Count']:,}")
        print(f"   Tokens: {int(row['Total_Tokens']):,}")
        print(f"   Cost: {format_cost(row['Total_Cost_USD'])} USD")
        print(f"   Avg Tokens per Call: {int(row['Total_Tokens']/row['Call_Count']):,}")

def print_time_analysis(df):
    """Print time-based analysis"""
    print("\n" + "="*80)
    print("⏰ TIME-BASED ANALYSIS")
    print("="*80)
    
    df['Timestamp'] = pd.to_datetime(df['Timestamp'])
    df['Hour'] = df['Timestamp'].dt.hour
    
    first_call = df['Timestamp'].min()
    last_call = df['Timestamp'].max()
    duration = last_call - first_call
    
    print(f"\n📅 Time Range:")
    print(f"   First Call: {first_call}")
    print(f"   Last Call:  {last_call}")
    print(f"   Duration:   {duration}")
    
    if duration.total_seconds() > 0:
        calls_per_minute = len(df) / (duration.total_seconds() / 60)
        print(f"   Rate:       {calls_per_minute:.2f} calls/minute")

def print_top_expensive_calls(df, n=10):
    """Print top N most expensive API calls"""
    print("\n" + "="*80)
    print(f"💸 TOP {n} MOST EXPENSIVE API CALLS")
    print("="*80)
    
    top_calls = df.nlargest(n, 'Total_Cost_USD')[
        ['Timestamp', 'Worker_ID', 'School_ID', 'Task', 'API_Call_Type', 
         'Total_Tokens', 'Total_Cost_USD', 'Status']
    ]
    
    for idx, row in top_calls.iterrows():
        print(f"\n{idx+1}. {format_cost(float(row['Total_Cost_USD']))} USD - {row['API_Call_Type']}")
        print(f"   School: {row['School_ID']}")
        print(f"   Task: {row['Task'][:50]}...")
        print(f"   Tokens: {row['Total_Tokens']:,} | Status: {row['Status']}")

def print_failures(df):
    """Print failed API calls"""
    failures = df[df['Status'] == 'failure']
    
    if failures.empty:
        return
    
    print("\n" + "="*80)
    print(f"❌ FAILED API CALLS ({len(failures)} total)")
    print("="*80)
    
    for idx, row in failures.iterrows():
        print(f"\n🔴 {row['Timestamp']}")
        print(f"   Worker: {row['Worker_ID']}")
        print(f"   School: {row['School_ID']}")
        print(f"   Task: {row['Task'][:50]}...")
        print(f"   Error: {row['Error_Message'][:100]}")

def export_summary_csv(df):
    """Export summary to separate CSV"""
    output_file = API_USAGE_LOG.replace('.csv', '_summary.csv')
    
    summary_data = {
        'Metric': [
            'Total API Calls',
            'Successful Calls',
            'Failed Calls',
            'Total Input Tokens',
            'Total Output Tokens',
            'Total Tokens',
            'Total Cost (USD)',
            'Avg Cost per Call (USD)',
            'Avg Input Tokens per Call',
            'Avg Output Tokens per Call'
        ],
        'Value': [
            len(df),
            len(df[df['Status'] == 'success']),
            len(df[df['Status'] == 'failure']),
            df['Input_Tokens'].sum(),
            df['Output_Tokens'].sum(),
            df['Total_Tokens'].sum(),
            f"{df['Total_Cost_USD'].astype(float).sum():.6f}",
            f"{df['Total_Cost_USD'].astype(float).mean():.6f}",
            f"{df['Input_Tokens'].mean():.0f}",
            f"{df['Output_Tokens'].mean():.0f}"
        ]
    }
    
    summary_df = pd.DataFrame(summary_data)
    summary_df.to_csv(output_file, index=False)
    print(f"\n💾 Summary exported to: {output_file}")

def main():
    """Main function"""
    print("\n" + "="*80)
    print("📊 API USAGE LOG ANALYZER")
    print("="*80)
    
    df = load_api_log()
    if df is None:
        return
    
    # Print all analyses
    print_summary(df)
    print_model_breakdown(df)
    print_worker_breakdown(df)
    print_api_type_breakdown(df)
    print_time_analysis(df)
    print_top_expensive_calls(df, n=10)
    print_failures(df)
    
    # Export summary
    export_summary_csv(df)
    
    print("\n" + "="*80)
    print("✅ Analysis Complete!")
    print("="*80 + "\n")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Analysis interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
