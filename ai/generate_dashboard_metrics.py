import os
import json
import logging
import traceback
from datetime import datetime, date, timedelta
from tools.data_fetcher import set_dataset  # Fixed import path
import pandas as pd
import re
import uuid
from openai import OpenAI
import qdrant_client
from qdrant_client.http import models as rest
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http.models import Filter, FieldCondition, MatchValue
import time
import argparse
import sys

# Create logs directory if it doesn't exist
script_dir = os.path.dirname(os.path.abspath(__file__))
logs_dir = os.path.join(script_dir, 'logs')
os.makedirs(logs_dir, exist_ok=True)

# Configure logging with a single handler for both file and console
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# Remove any existing handlers
for handler in logger.handlers[:]:
    logger.removeHandler(handler)

# Add file handler
file_handler = logging.FileHandler(os.path.join(logs_dir, 'dashboard_metrics.log'))
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Add console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

def load_json_file(file_path):
    """Load a JSON file and return its contents."""
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading JSON file {file_path}: {e}")
        return None

def get_date_ranges(target_date=None):
    """Calculate the date ranges for YTD comparisons."""
    # If no target_date provided, use yesterday
    if target_date is None:
        target_date = date.today() - timedelta(days=1)
    elif isinstance(target_date, str):
        target_date = datetime.strptime(target_date, '%Y-%m-%d').date()
    
    # This year's range
    this_year = target_date.year
    this_year_start = f"{this_year}-01-01"
    this_year_end = target_date.strftime('%Y-%m-%d')
    
    # Last year's range - use same day-of-year
    last_year = this_year - 1
    last_year_start = f"{last_year}-01-01"
    
    # For last year's end date, we need to handle leap years correctly
    # If this year is a leap year and today is Feb 29, use Feb 28 for last year
    if target_date.month == 2 and target_date.day == 29:
        last_year_end = f"{last_year}-02-28"
    else:
        last_year_end = target_date.replace(year=last_year).strftime('%Y-%m-%d')
    
    logger.info(f"Date ranges: this_year={this_year_start} to {this_year_end}, last_year={last_year_start} to {last_year_end}")
    
    return {
        'this_year_start': this_year_start,
        'this_year_end': this_year_end,
        'last_year_start': last_year_start,
        'last_year_end': last_year_end
    }

def debug_query(query, endpoint, date_ranges, query_name=None):
    """Debug a query by printing detailed information about the query and its execution."""
    logger.info("=" * 80)
    logger.info(f"DEBUG QUERY: {query_name}")
    logger.info("-" * 80)
    logger.info(f"Endpoint: {endpoint}")
    logger.info(f"Date ranges: {date_ranges}")
    
    # Extract year from date ranges
    this_year = datetime.strptime(date_ranges['this_year_end'], '%Y-%m-%d').year
    last_year = this_year - 1
    
    # Check for hardcoded dates in the query
    this_year_pattern = re.compile(f"'{this_year}-\\d{{2}}-\\d{{2}}'")
    last_year_pattern = re.compile(f"'{last_year}-\\d{{2}}-\\d{{2}}'")
    
    this_year_dates = this_year_pattern.findall(query)
    last_year_dates = last_year_pattern.findall(query)
    
    if this_year_dates:
        logger.info(f"Found hardcoded this year dates: {this_year_dates}")
    if last_year_dates:
        logger.info(f"Found hardcoded last year dates: {last_year_dates}")
    
    # Check for date placeholders
    placeholders = ['this_year_start', 'this_year_end', 'last_year_start', 'last_year_end']
    for placeholder in placeholders:
        if placeholder in query:
            logger.info(f"Found placeholder: {placeholder} = {date_ranges.get(placeholder, 'Not in date_ranges')}")
    
    # Try to determine the actual last data date for this endpoint
    try:
        for date_col in ['date_issued', 'arrest_date', 'received_datetime', 'date']:
            last_date_query = f"SELECT max({date_col}) as last_data_date"
            context_variables = {}
            last_date_result = set_dataset(context_variables, endpoint=endpoint, query=last_date_query)
            
            if last_date_result.get('status') == 'success' and 'dataset' in context_variables and not context_variables['dataset'].empty:
                if not context_variables['dataset']['last_data_date'].iloc[0] is None:
                    actual_last_date = pd.to_datetime(context_variables['dataset']['last_data_date'].iloc[0]).date()
                    logger.info(f"Found actual last data date from {date_col}: {actual_last_date}")
                    break
    except Exception as e:
        logger.warning(f"Error determining last data date: {str(e)}")
    
    # Now modify the query with the updated date ranges
    modified_query = query
    
    # Replace date placeholders in the query
    for key, value in date_ranges.items():
        modified_query = modified_query.replace(key, f"'{value}'")
    
    # Check for hardcoded date patterns in the query and replace them
    this_year_pattern = re.compile(f"'{this_year}-\\d{{2}}-\\d{{2}}'")
    last_year_pattern = re.compile(f"'{last_year}-\\d{{2}}-\\d{{2}}'")
    
    # Find all hardcoded dates for this year and last year
    this_year_dates = this_year_pattern.findall(modified_query)
    last_year_dates = last_year_pattern.findall(modified_query)
    
    # Replace the latest this_year date with the actual max date
    if this_year_dates:
        latest_this_year_date = max(this_year_dates)
        modified_query = modified_query.replace(latest_this_year_date, f"'{date_ranges['this_year_end']}'")
        logger.info(f"Replaced hardcoded this year date {latest_this_year_date} with {date_ranges['this_year_end']}")
    
    # Replace the latest last_year date with the corresponding last year date
    if last_year_dates:
        # Filter out January 1st dates as these should remain as start dates
        jan_first = f"'{last_year}-01-01'"
        non_jan_first_dates = [date for date in last_year_dates if date != jan_first]
        
        if non_jan_first_dates:
            latest_last_year_date = max(non_jan_first_dates)
            modified_query = modified_query.replace(latest_last_year_date, f"'{date_ranges['last_year_end']}'")
            logger.info(f"Replaced hardcoded last year date {latest_last_year_date} with {date_ranges['last_year_end']}")
        else:
            logger.info(f"No non-January 1st last year dates to replace. Keeping {jan_first} as the start date.")
    
    logger.info(f"Modified query: {modified_query}")
    
    # Execute the query
    context_variables = {}
    result = set_dataset(context_variables, endpoint=endpoint, query=modified_query)
    logger.info(f"Query execution result status: {result.get('status')}")
    
    if result.get('status') == 'success' and 'dataset' in context_variables:
        df = context_variables['dataset']
        logger.info(f"Dataset retrieved successfully - Shape: {df.shape}")
        logger.info(f"Columns: {df.columns.tolist()}")
        
        # Print the first few rows
        if not df.empty:
            logger.info("First few rows:")
            logger.info(df.head().to_string())
            
            # Get the max date
            for date_col in ['received_datetime', 'max_date', 'arrest_date']:
                if date_col in df.columns:
                    df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
                    max_date = df[date_col].max()
                    if pd.notnull(max_date):
                        max_date_str = max_date.strftime('%Y-%m-%d')
                        logger.info(f"Max date determined from {date_col}: {max_date_str}")
                        break
    else:
        logger.error("Query failed or no data returned")
        if 'error' in result:
            logger.error(f"Error: {result['error']}")
        logger.error(f"Query URL: {result.get('queryURL')}")
    
    logger.info("=" * 80)
    return

def process_query_for_district(query, endpoint, date_ranges, query_name=None):
    """Process a single query and handle district-level aggregation from the same dataset."""
    try:
        logger.info(f"Processing query for endpoint {endpoint}, query_name: {query_name}")
        
        # Debug the query if it's for "Arrests Presented to DA"
        if query_name and "arrests presented" in query_name.lower():
            debug_query(query, endpoint, date_ranges, query_name)
        
        # Get date ranges if not provided
        if not date_ranges:
            date_ranges = get_date_ranges()
        
        # IMPORTANT FIX: For housing units, we need to get the actual last data date
        # This is to ensure we're using the actual last date, not just the first day of the month
        if query_name and "housing units" in query_name.lower():
            try:
                # Try to get the actual last data date from the endpoint
                actual_last_date_query = "SELECT max(date_issued) as last_data_date"
                actual_date_context = {}
                actual_date_result = set_dataset(actual_date_context, endpoint=endpoint, query=actual_last_date_query)
                
                if actual_date_result.get('status') == 'success' and 'dataset' in actual_date_context and not actual_date_context['dataset'].empty:
                    actual_last_date = pd.to_datetime(actual_date_context['dataset']['last_data_date'].iloc[0])
                    if pd.notnull(actual_last_date):
                        actual_last_date_str = actual_last_date.strftime('%Y-%m-%d')
                        logger.info(f"Found actual last data date for housing units: {actual_last_date_str}")
                        
                        # Update the date ranges with the actual last data date
                        date_ranges['this_year_end'] = actual_last_date_str
                        date_ranges['last_year_end'] = actual_last_date.replace(year=actual_last_date.year-1).strftime('%Y-%m-%d')
                        logger.info(f"Updated date ranges for housing units: this_year_end={date_ranges['this_year_end']}, last_year_end={date_ranges['last_year_end']}")
            except Exception as e:
                logger.warning(f"Error getting actual last data date for housing units: {str(e)}")
        
        # Now modify the query with the date ranges
        modified_query = query
        
        # Replace date placeholders in the query
        for key, value in date_ranges.items():
            modified_query = modified_query.replace(key, f"'{value}'")
        
        # Handle cases where the query uses direct year comparisons
        this_year = datetime.strptime(date_ranges['this_year_end'], '%Y-%m-%d').year
        last_year = this_year - 1
        
        logger.info(f"Processing years: this_year={this_year}, last_year={last_year}")
        
        # Check for hardcoded date patterns in the query and replace them
        # This is crucial for queries that have hardcoded dates like '2025-02-16'
        this_year_pattern = re.compile(f"'{this_year}-\\d{{2}}-\\d{{2}}'")
        last_year_pattern = re.compile(f"'{last_year}-\\d{{2}}-\\d{{2}}'")
        
        # Find all hardcoded dates for this year and last year
        this_year_dates = this_year_pattern.findall(modified_query)
        last_year_dates = last_year_pattern.findall(modified_query)
        
        # Replace the latest this_year date with the actual max date
        if this_year_dates:
            latest_this_year_date = max(this_year_dates)
            modified_query = modified_query.replace(latest_this_year_date, f"'{date_ranges['this_year_end']}'")
            logger.info(f"Replaced hardcoded this year date {latest_this_year_date} with {date_ranges['this_year_end']}")
        
        # Replace the latest last_year date with the corresponding last year date
        if last_year_dates:
            # Filter out January 1st dates as these should remain as start dates
            jan_first = f"'{last_year}-01-01'"
            non_jan_first_dates = [date for date in last_year_dates if date != jan_first]
            
            if non_jan_first_dates:
                latest_last_year_date = max(non_jan_first_dates)
                modified_query = modified_query.replace(latest_last_year_date, f"'{date_ranges['last_year_end']}'")
                logger.info(f"Replaced hardcoded last year date {latest_last_year_date} with {date_ranges['last_year_end']}")
            else:
                logger.info(f"No non-January 1st last year dates to replace. Keeping {jan_first} as the start date.")
        
        # Define all possible date patterns we need to fix
        date_patterns = [
            (f">= '{this_year}-01-01' AND < '{this_year}-01-01'",
             f">= '{this_year}-01-01' AND <= '{date_ranges['this_year_end']}'"),
            (f">= '{last_year}-01-01' AND < '{last_year}-01-01'",
             f">= '{last_year}-01-01' AND <= '{date_ranges['last_year_end']}'"),
            (f">= '{this_year}-01-01' AND <= '{this_year}-01-01'",
             f">= '{this_year}-01-01' AND <= '{date_ranges['this_year_end']}'"),
            (f">= '{last_year}-01-01' AND <= '{last_year}-01-01'",
             f">= '{last_year}-01-01' AND <= '{date_ranges['last_year_end']}'"),
            (f">= '{this_year}-01-01' AND date_issued < '{this_year}-01-01'",
             f">= '{this_year}-01-01' AND date_issued <= '{date_ranges['this_year_end']}'"),
            (f">= '{last_year}-01-01' AND date_issued < '{last_year}-01-01'",
             f">= '{last_year}-01-01' AND date_issued <= '{date_ranges['last_year_end']}'")
        ]
        
        # Apply all pattern replacements
        for pattern, replacement in date_patterns:
            if pattern in modified_query:
                logger.info(f"Replacing date pattern: {pattern} -> {replacement}")
                modified_query = modified_query.replace(pattern, replacement)
        
        logger.info(f"Modified query: {modified_query}")
        
        # Execute the query
        context_variables = {}
        result = set_dataset(context_variables, endpoint=endpoint, query=modified_query)
        logger.info(f"Query execution result status: {result.get('status')}")
        
        if result.get('status') == 'success' and 'dataset' in context_variables:
            query_info = {
                'original_query': query,
                'executed_query': modified_query
            }
            
            df = context_variables['dataset']
            logger.info(f"Dataset retrieved successfully - Shape: {df.shape}")
            
            results = {}
            has_district = 'supervisor_district' in df.columns
            logger.info(f"Query has district data: {has_district}")
            
            # Get the max date
            max_date = None
            for date_col in ['received_datetime', 'max_date', 'arrest_date']:
                if date_col in df.columns:
                    df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
                    max_date = df[date_col].max()
                    if pd.notnull(max_date):
                        max_date = max_date.strftime('%Y-%m-%d')
                        logger.info(f"Max date determined from {date_col}: {max_date}")
                        break
            
            if has_district:
                is_response_time = query_name and 'response time' in query_name.lower()
                logger.info(f"Processing as response time metric: {is_response_time}")
                
                if is_response_time:
                    if 'this_year' not in df.columns or 'last_year' not in df.columns:
                        logger.error(f"Required columns not found in dataset. Available columns: {df.columns.tolist()}")
                        return None
                    
                    df[['this_year', 'last_year']] = df[['this_year', 'last_year']].apply(pd.to_numeric, errors='coerce')
                    
                    # Calculate citywide and district averages
                    results['0'] = {
                        'lastYear': int(df['last_year'].mean()) if pd.notnull(df['last_year'].mean()) else 0,
                        'thisYear': int(df['this_year'].mean()) if pd.notnull(df['this_year'].mean()) else 0,
                        'lastDataDate': max_date
                    }
                    
                    for district in range(1, 12):
                        district_df = df[df['supervisor_district'] == str(district)]
                        if not district_df.empty:
                            results[str(district)] = {
                                'lastYear': int(district_df['last_year'].mean()) if pd.notnull(district_df['last_year'].mean()) else 0,
                                'thisYear': int(district_df['this_year'].mean()) if pd.notnull(district_df['this_year'].mean()) else 0,
                                'lastDataDate': max_date
                            }
                else:
                    # For non-response time metrics
                    if not df.empty and 'last_year' in df.columns and 'this_year' in df.columns:
                        df[['last_year', 'this_year']] = df[['last_year', 'this_year']].apply(pd.to_numeric, errors='coerce')
                        
                        results['0'] = {
                            'lastYear': int(df['last_year'].sum()),
                            'thisYear': int(df['this_year'].sum()),
                            'lastDataDate': max_date
                        }
                        
                        for district in range(1, 12):
                            district_df = df[df['supervisor_district'] == str(district)]
                            if not district_df.empty:
                                results[str(district)] = {
                                    'lastYear': int(district_df['last_year'].sum()),
                                    'thisYear': int(district_df['this_year'].sum()),
                                    'lastDataDate': max_date
                                }
            else:
                # For non-district queries, just return the total from first row
                if not df.empty:
                    row = df.iloc[0].to_dict()
                    results['0'] = {
                        'lastYear': int(float(row.get('last_year', 0))),
                        'thisYear': int(float(row.get('this_year', 0))),
                        'lastDataDate': max_date
                    }
            
            logger.info(f"Query processing completed successfully for {query_name}")
            
            # Print the final results if it's the specific metric we're debugging
            if query_name and "arrests presented" in query_name.lower():
                logger.info("=" * 80)
                logger.info(f"FINAL RESULTS FOR: {query_name}")
                logger.info("-" * 80)
                for district, district_results in results.items():
                    logger.info(f"District {district}: thisYear={district_results['thisYear']}, lastYear={district_results['lastYear']}, lastDataDate={district_results['lastDataDate']}")
                logger.info("=" * 80)
            
            return {
                'results': results,
                'queries': query_info
            }
            
        else:
            logger.error("Query failed or no data returned")
            if 'error' in result:
                logger.error(f"Error: {result['error']}")
            logger.error(f"Query URL: {result.get('queryURL')}")
    except Exception as e:
        logger.error(f"Error executing query: {str(e)}")
        logger.error(f"Error type: {type(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    
    return None

def process_ytd_trend_query(query, endpoint, date_ranges=None, target_date=None, query_name=None):
    """Process a YTD trend query to get historical daily counts."""
    try:
        logger.info(f"Processing YTD trend query for {query_name}")
        
        # Get date ranges if not provided
        if not date_ranges:
            date_ranges = get_date_ranges(target_date)
        
        # Replace date placeholders with actual dates
        modified_query = query.replace("date_trunc_y(date_sub_y(current_date, 1))", f"'{date_ranges['last_year_start']}'")
        modified_query = modified_query.replace("current_date", f"'{date_ranges['this_year_end']}'")
        modified_query = modified_query.replace("last_year_start", f"'{date_ranges['last_year_start']}'")
        
        # Check for hardcoded date patterns in the query and replace them
        this_year = datetime.strptime(date_ranges['this_year_end'], '%Y-%m-%d').year
        last_year = this_year - 1
        
        # Use regex to find hardcoded dates
        this_year_pattern = re.compile(f"'{this_year}-\\d{{2}}-\\d{{2}}'")
        last_year_pattern = re.compile(f"'{last_year}-\\d{{2}}-\\d{{2}}'")
        
        # Find all hardcoded dates for this year and last year
        this_year_dates = this_year_pattern.findall(modified_query)
        last_year_dates = last_year_pattern.findall(modified_query)
        
        # Replace the latest this_year date with the actual max date
        if this_year_dates:
            latest_this_year_date = max(this_year_dates)
            modified_query = modified_query.replace(latest_this_year_date, f"'{date_ranges['this_year_end']}'")
            logger.info(f"Replaced hardcoded this year date {latest_this_year_date} with {date_ranges['this_year_end']}")
        
        # Replace the latest last_year date with the corresponding last year date
        if last_year_dates:
            # Filter out January 1st dates as these should remain as start dates
            jan_first = f"'{last_year}-01-01'"
            non_jan_first_dates = [date for date in last_year_dates if date != jan_first]
            
            if non_jan_first_dates:
                latest_last_year_date = max(non_jan_first_dates)
                modified_query = modified_query.replace(latest_last_year_date, f"'{date_ranges['last_year_end']}'")
                logger.info(f"Replaced hardcoded last year date {latest_last_year_date} with {date_ranges['last_year_end']}")
            else:
                logger.info(f"No non-January 1st last year dates to replace. Keeping {jan_first} as the start date.")
        
        logger.info(f"Modified trend query: {modified_query}")
        
        # Execute the query
        context_variables = {}
        result = set_dataset(context_variables, endpoint=endpoint, query=modified_query)
        
        if result.get('status') == 'success' and 'dataset' in context_variables:
            df = context_variables['dataset']
            df['date'] = pd.to_datetime(df['date'])
            
            # Get the last data date from the trend data
            last_data_date = df['date'].max()
            if pd.notnull(last_data_date):
                last_data_date_str = last_data_date.strftime('%Y-%m-%d')
                logger.info(f"Found last data date from trend data: {last_data_date_str}")
                
                # Update date ranges if needed
                if last_data_date_str < date_ranges['this_year_end']:
                    logger.info(f"Updating date ranges to use last data date: {last_data_date_str}")
                    
                    # IMPORTANT FIX: For monthly data, we need to find the actual last data date
                    # Check if this is a monthly dataset (date_trunc_ym in the query)
                    is_monthly = 'date_trunc_ym' in query
                    if is_monthly:
                        # For monthly data, we need to find the actual last data date from the dataset
                        # This is to ensure we're using the actual last date, not just the first day of the month
                        try:
                            # Try to get the actual last data date from the endpoint
                            date_col = None
                            for possible_col in ['date_issued', 'arrest_date', 'received_datetime', 'date']:
                                if possible_col in endpoint:
                                    date_col = possible_col
                                    break
                            
                            if date_col:
                                actual_last_date_query = f"SELECT max({date_col}) as last_data_date"
                                actual_date_context = {}
                                actual_date_result = set_dataset(actual_date_context, endpoint=endpoint, query=actual_last_date_query)
                                
                                if actual_date_result.get('status') == 'success' and 'dataset' in actual_date_context and not actual_date_context['dataset'].empty:
                                    actual_last_date = pd.to_datetime(actual_date_context['dataset']['last_data_date'].iloc[0])
                                    if pd.notnull(actual_last_date):
                                        actual_last_date_str = actual_last_date.strftime('%Y-%m-%d')
                                        logger.info(f"Found actual last data date from endpoint: {actual_last_date_str}")
                                        
                                        # Use this actual date instead of the first day of the month
                                        last_data_date_str = actual_last_date_str
                                        last_data_date = actual_last_date
                        except Exception as e:
                            logger.warning(f"Error getting actual last data date: {str(e)}")
                            logger.warning("Falling back to trend data date")
                    
                    date_ranges.update({
                        'this_year_end': last_data_date_str,
                        'last_year_end': last_data_date.replace(year=last_data_date.year-1).strftime('%Y-%m-%d'),
                        'last_data_date': last_data_date_str
                    })
                    # Ensure last_year_start is always January 1st of the previous year
                    date_ranges['last_year_start'] = f"{last_data_date.year-1}-01-01"
                    logger.info(f"Ensuring last_year_start is January 1st: {date_ranges['last_year_start']}")
            
            # Sort by date and convert Timestamp keys to string dates
            trend_data = {
                date.strftime('%Y-%m-%d'): value 
                for date, value in df.sort_values('date').set_index('date')['value'].items()
            }
            
            logger.info(f"Processed {len(trend_data)} trend data points")
            
            return {
                'trend_data': trend_data,
                'last_updated': df['date'].max().strftime('%Y-%m-%d'),
                'original_query': query,
                'executed_query': modified_query
            }
            
        else:
            logger.error("YTD trend query failed or no data returned")
            if 'error' in result:
                logger.error(f"Error: {result['error']}")
            logger.error(f"Query URL: {result.get('queryURL')}")
            
    except Exception as e:
        logger.error(f"Error executing YTD trend query: {str(e)}")
        logger.error(traceback.format_exc())
    
    return None

def generate_ytd_metrics(queries_data, output_dir, target_date=None):
    """Generate YTD metrics files for each district."""
    
    # Initialize the metrics structure
    metrics = {
        "districts": {
            "0": {
                "name": "Citywide",
                "categories": []
            }
        },
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "data_as_of": None,  # Will be updated with actual data date
            "next_update": None  # Will be updated after processing
        }
    }
    
    # Process each top-level category (safety, economy, etc.)
    for top_category_name, top_category_data in queries_data.items():
        # Initialize category metrics for the top-level category
        top_category_metrics = {
            "category": top_category_name.title(),
            "metrics": []
        }
        
        # Process each subcategory
        for subcategory_name, subcategory_data in top_category_data.items():
            # Check if this is a valid subcategory with queries
            if isinstance(subcategory_data, dict) and 'queries' in subcategory_data:
                # Get the endpoint from the subcategory if it exists
                endpoint = subcategory_data.get('endpoint', None)
                
                # Format the endpoint if it exists
                if endpoint and not endpoint.startswith('http'):
                    endpoint = f"https://data.sfgov.org/resource/{endpoint}"
                if endpoint and not endpoint.endswith('.json'):
                    endpoint = f"{endpoint}.json"
                
                # Get initial date ranges using yesterday as target
                initial_date_ranges = get_date_ranges(target_date)
                
                # Now process each query in the subcategory
                for query_name, query_data in subcategory_data['queries'].items():
                    # Extract queries and metadata
                    if isinstance(query_data, str):
                        metric_query = query_data
                        ytd_query = None
                        metadata = {
                            "summary": "",
                            "definition": "",
                            "data_sf_url": "",
                            "ytd_query": ""
                        }
                        query_endpoint = endpoint
                    else:
                        metric_query = query_data.get('metric_query', '')
                        ytd_query = query_data.get('ytd_query', '')
                        metadata = {
                            "summary": query_data.get('summary', ''),
                            "definition": query_data.get('definition', ''),
                            "data_sf_url": query_data.get('data_sf_url', ''),
                            "ytd_query": query_data.get('ytd_query', '')
                        }
                        # Check if this query has its own endpoint
                        if 'endpoint' in query_data:
                            query_endpoint = query_data['endpoint']
                            if query_endpoint and not query_endpoint.startswith('http'):
                                query_endpoint = f"https://data.sfgov.org/resource/{query_endpoint}"
                            if query_endpoint and not query_endpoint.endswith('.json'):
                                query_endpoint = f"{query_endpoint}.json"
                            logger.info(f"Using query-specific endpoint for {query_name}: {query_endpoint}")
                        else:
                            query_endpoint = endpoint
                    
                    # Skip this query if we don't have an endpoint
                    if not query_endpoint:
                        logger.warning(f"Skipping query {query_name} because no endpoint is defined")
                        continue
                    
                    # Create a copy of initial date ranges for this metric
                    date_ranges = initial_date_ranges.copy()
                    trend_data = None
                    
                    # Process trend data to get truncated date
                    if ytd_query:
                        trend_data = process_ytd_trend_query(ytd_query, query_endpoint, date_ranges=date_ranges, query_name=query_name)
                        if trend_data and 'last_updated' in trend_data:
                            truncated_date = datetime.strptime(trend_data['last_updated'], '%Y-%m-%d').date()
                            logger.info(f"Found truncated date from trend data: {truncated_date}")
                            
                            # Use the truncated date from trend data
                            logger.info(f"Using truncated date from trend data: {truncated_date}")
                            
                            # Fix: Use the actual last data date instead of the first day of the month
                            # This ensures we capture all data for the month
                            date_ranges['this_year_end'] = truncated_date.strftime('%Y-%m-%d')
                            date_ranges['last_year_end'] = truncated_date.replace(year=truncated_date.year-1).strftime('%Y-%m-%d')
                            date_ranges['last_data_date'] = truncated_date.strftime('%Y-%m-%d')
                            # Ensure last_year_start is always January 1st of the previous year
                            date_ranges['last_year_start'] = f"{truncated_date.year-1}-01-01"
                            logger.info(f"Ensuring last_year_start is January 1st: {date_ranges['last_year_start']}")
                    
                    # Process metric query with the adjusted date ranges
                    query_results = process_query_for_district(metric_query, query_endpoint, date_ranges, query_name=query_name)
                    if query_results:
                        results = query_results['results']
                        queries = query_results['queries']
                        
                        # Create metric object with metadata
                        metric_base = {
                            "name": query_name.replace(" YTD", ""),
                            "id": query_name.lower().replace(" ", "_").replace("-", "_").replace("_ytd", "") + "_ytd",
                            "metadata": metadata,
                            "queries": {
                                "metric_query": queries['original_query'],
                                "executed_query": queries['executed_query']
                            }
                        }
                        
                        # Add location and category fields if available in the enhanced query data
                        if isinstance(query_data, dict):
                            if "location_fields" in query_data:
                                metric_base["location_fields"] = query_data.get("location_fields", [])
                            if "category_fields" in query_data:
                                metric_base["category_fields"] = query_data.get("category_fields", [])
                            # Also add the numeric ID if available
                            if "id" in query_data and isinstance(query_data["id"], int):
                                metric_base["numeric_id"] = query_data["id"]
                        
                        # Get the last data date from metric results if available
                        metric_last_data_date = None
                        if '0' in results and results['0'].get('lastDataDate'):
                            metric_last_data_date = results['0']['lastDataDate']
                        
                        # Add trend data if it was processed
                        if trend_data:
                            metric_base["trend_data"] = trend_data["trend_data"]
                            # Use metric's last data date if available (for monthly/truncated data),
                            # otherwise use trend's last updated date
                            metric_base["trend_last_updated"] = metric_last_data_date or trend_data["last_updated"]
                            metric_base["queries"]["ytd_query"] = trend_data["original_query"]
                            metric_base["queries"]["executed_ytd_query"] = trend_data["executed_query"]
                        
                        # Update metadata with the most recent data date
                        if metrics['metadata']['data_as_of'] is None or (metric_last_data_date and metric_last_data_date > metrics['metadata']['data_as_of']):
                            metrics['metadata']['data_as_of'] = metric_last_data_date
                        
                        # Add citywide metric
                        if '0' in results:
                            citywide_metric = metric_base.copy()
                            citywide_metric.update({
                                "lastYear": results['0']['lastYear'],
                                "thisYear": results['0']['thisYear'],
                                "lastDataDate": metric_last_data_date or results['0'].get('lastDataDate')
                            })
                            top_category_metrics['metrics'].append(citywide_metric)
                        
                        # Add district metrics
                        for district_num in range(1, 12):
                            district_str = str(district_num)
                            if district_str in results:
                                # Initialize district if not exists BEFORE accessing it
                                if district_str not in metrics['districts']:
                                    metrics['districts'][district_str] = {
                                        "name": f"District {district_str}",
                                        "categories": []
                                    }
                                
                                district_data = metrics['districts'][district_str]
                                district_metric = metric_base.copy()
                                district_metric.update({
                                    "lastYear": results[district_str]['lastYear'],
                                    "thisYear": results[district_str]['thisYear'],
                                    "lastDataDate": metric_last_data_date or results[district_str].get('lastDataDate')
                                })
                                
                                # Find or create category for this district
                                district_category = next(
                                    (cat for cat in metrics['districts'][district_str]['categories'] 
                                     if cat['category'] == top_category_name.title()),
                                    None
                                )
                                if district_category is None:
                                    district_category = {
                                        "category": top_category_name.title(),
                                        "metrics": []
                                    }
                                    metrics['districts'][district_str]['categories'].append(district_category)
                                district_category['metrics'].append(district_metric)
        
        if top_category_metrics['metrics']:
            metrics['districts']['0']['categories'].append(top_category_metrics)
    
    # Calculate the next update time (either 5 AM or 11 AM)
    now = datetime.now()
    next_5am = now.replace(hour=5, minute=0, second=0, microsecond=0)
    next_11am = now.replace(hour=11, minute=0, second=0, microsecond=0)
    
    # If we're past 11 AM, set targets to next day
    if now.hour >= 11:
        next_5am += timedelta(days=1)
        next_11am += timedelta(days=1)
    # If we're past 5 AM but before 11 AM, only adjust the 5 AM target
    elif now.hour >= 5:
        next_5am += timedelta(days=1)
    
    # Find the next closest update time
    next_update = min(next_5am, next_11am)
    metrics['metadata']['next_update'] = next_update.isoformat()
    
    # Create output directories
    dashboard_dir = output_dir  # Changed: Use output_dir directly as dashboard_dir
    history_dir = os.path.join(output_dir, 'history')
    os.makedirs(dashboard_dir, exist_ok=True)
    os.makedirs(history_dir, exist_ok=True)
    
    # Save individual district files
    for district_num in range(12):  # 0-11 for citywide and districts 1-11
        district_str = str(district_num)
        if district_str in metrics['districts']:
            # Create district subfolder
            district_dir = os.path.join(dashboard_dir, district_str)
            os.makedirs(district_dir, exist_ok=True)
            
            # Create a copy of district data without trend data for top_level.json
            district_data = {
                "metadata": metrics['metadata'],
                "name": metrics['districts'][district_str]['name'],
                "categories": []
            }
            
            # Process each category
            for category in metrics['districts'][district_str]['categories']:
                category_copy = {
                    "category": category['category'],
                    "metrics": []
                }
                
                # Process each metric
                for metric in category['metrics']:
                    # Create a copy of the metric without trend data for top_level.json
                    metric_copy = metric.copy()
                    if 'trend_data' in metric_copy:
                        del metric_copy['trend_data']
                    category_copy['metrics'].append(metric_copy)
                    
                    # Save individual metric file with trend data
                    if 'trend_data' in metric and metric['trend_data']:
                        # Use numeric_id for filename if available, otherwise use string id
                        file_id = str(metric['numeric_id']) if 'numeric_id' in metric else metric['id']
                        metric_file = os.path.join(district_dir, f"{file_id}.json")
                        metric_data = {
                            "metadata": metrics['metadata'],
                            "metric_id": metric['id'],
                            "metric_name": metric['name'],
                            "category": category['category'],
                            "lastYear": metric['lastYear'],
                            "thisYear": metric['thisYear'],
                            "lastDataDate": metric['lastDataDate'],
                            "trend_data": metric['trend_data'],
                            "trend_last_updated": metric.get('trend_last_updated'),
                            # Add summary, definition, and URL from metadata
                            "summary": metric['metadata'].get('summary', ''),
                            "definition": metric['metadata'].get('definition', ''),
                            "data_sf_url": metric['metadata'].get('data_sf_url', ''),
                            # Add executed queries for transparency and debugging
                            "queries": {
                                "metric_query": metric['queries'].get('metric_query', ''),
                                "executed_metric_query": metric['queries'].get('executed_query', ''),
                                "ytd_query": metric['queries'].get('ytd_query', ''),
                                "executed_ytd_query": metric['queries'].get('executed_ytd_query', '')
                            }
                        }

                        # Add location_fields, category_fields, and numeric_id if they exist in the metric
                        if 'location_fields' in metric:
                            metric_data['location_fields'] = metric['location_fields']
                        if 'category_fields' in metric:
                            metric_data['category_fields'] = metric['category_fields']
                        if 'numeric_id' in metric:
                            metric_data['numeric_id'] = metric['numeric_id']

                        # Add district breakdown for citywide metrics (district 0)
                        if district_str == '0':
                            district_breakdown = {}
                            # Look for this metric in each district's data
                            for d_num in range(1, 12):
                                d_str = str(d_num)
                                if d_str in metrics['districts']:
                                    # Find matching metric in district data
                                    for d_cat in metrics['districts'][d_str]['categories']:
                                        if d_cat['category'] == category['category']:
                                            for d_metric in d_cat['metrics']:
                                                if d_metric['id'] == metric['id']:
                                                    district_breakdown[d_str] = {
                                                        "thisYear": d_metric['thisYear'],
                                                        "lastYear": d_metric['lastYear'],
                                                        "lastDataDate": d_metric['lastDataDate']
                                                    }
                                                    break
                            if district_breakdown:
                                metric_data["district_breakdown"] = district_breakdown

                        with open(metric_file, 'w', encoding='utf-8') as f:
                            json.dump(metric_data, f, indent=2)
                        logger.info(f"Metric {file_id} (original id: {metric['id']}) saved to {metric_file}")
                    
                    # Remove summary, definition, and URL from the top-level copy
                    if 'metadata' in metric_copy:
                        metric_copy['metadata'].pop('summary', None)
                        metric_copy['metadata'].pop('definition', None)
                        metric_copy['metadata'].pop('data_sf_url', None)
                
                district_data['categories'].append(category_copy)
            
            # Save top_level.json to district subfolder
            top_level_file = os.path.join(district_dir, 'top_level.json')
            with open(top_level_file, 'w', encoding='utf-8') as f:
                json.dump(district_data, f, indent=2)
            logger.info(f"District {district_str} top_level metrics saved to {top_level_file}")
            
            # For backward compatibility, also save the district file in the old location
            district_file = os.path.join(dashboard_dir, f'district_{district_str}.json')
            with open(district_file, 'w', encoding='utf-8') as f:
                json.dump(district_data, f, indent=2)
            logger.info(f"District {district_str} metrics saved to {district_file} (for backward compatibility)")
            
            # Save to history directory with timestamp
            history_file = os.path.join(history_dir, f'district_{district_str}_{datetime.now().strftime("%Y%m%d")}.json')
            with open(history_file, 'w', encoding='utf-8') as f:
                json.dump(district_data, f, indent=2)
            logger.info(f"District {district_str} metrics history saved to {history_file}")
    
    return metrics

def create_ytd_vector_collection(metrics):
    """Create a vector collection for YTD metrics data."""
    try:
        collection_name = 'YTD'
        logger.info("Starting YTD vector collection creation")

        # Initialize OpenAI client
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if not openai_api_key:
            raise ValueError("OpenAI API key not found in environment variables.")
        
        client = OpenAI(api_key=openai_api_key)
        EMBEDDING_MODEL = "text-embedding-3-large"
        BATCH_SIZE = 100
        MAX_RETRIES = 3

        # Initialize Qdrant client
        qdrant = QdrantClient(host='localhost', port=6333)
        
        # Get sample embedding to determine vector size
        sample_response = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input="Sample text for vector size determination"
        )
        vector_size = len(sample_response.data[0].embedding)
        
        # Create or recreate collection
        def create_collection():
            if qdrant.collection_exists(collection_name):
                qdrant.delete_collection(collection_name)
            qdrant.create_collection(
                collection_name=collection_name,
                vectors_config=rest.VectorParams(
                    distance=rest.Distance.COSINE,
                    size=vector_size
                )
            )
        
        # Attempt to create collection with retries
        for attempt in range(MAX_RETRIES):
            try:
                create_collection()
                break
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed: {str(e)}")
                if attempt == MAX_RETRIES - 1:
                    raise Exception(f"Failed to create collection after {MAX_RETRIES} attempts")
                time.sleep(2 ** attempt)

        # Prepare data for embedding
        texts_to_embed = []
        metadata_batch = []
        
        # Process citywide metrics
        citywide_data = metrics['districts']['0']
        for category in citywide_data['categories']:
            category_name = category['category']
            for metric in category['metrics']:
                try:
                    # Validate required fields exist
                    required_fields = ['name', 'id', 'thisYear', 'lastYear', 'lastDataDate', 'queries']
                    missing_fields = [field for field in required_fields if field not in metric]
                    if missing_fields:
                        logger.warning(f"Skipping metric due to missing required fields: {missing_fields}")
                        continue
                        
                    metric_text = create_metric_text(category_name, metric, citywide_data['name'])
                    texts_to_embed.append(metric_text)
                    metadata_batch.append(create_metadata_dict(category_name, metric, '0', citywide_data['name']))
                except Exception as e:
                    logger.error(f"Error processing citywide metric {metric.get('name', 'unknown')}: {str(e)}")
                    continue

        # Process district metrics
        for district_num in range(1, 12):
            district_str = str(district_num)
            if district_str in metrics['districts']:
                district_data = metrics['districts'][district_str]
                for category in district_data['categories']:
                    category_name = category['category']
                    for metric in category['metrics']:
                        try:
                            # Validate required fields exist
                            required_fields = ['name', 'id', 'thisYear', 'lastYear', 'lastDataDate', 'queries']
                            missing_fields = [field for field in required_fields if field not in metric]
                            if missing_fields:
                                logger.warning(f"Skipping district {district_str} metric due to missing required fields: {missing_fields}")
                                continue
                                
                            metric_text = create_metric_text(category_name, metric, district_data['name'])
                            texts_to_embed.append(metric_text)
                            metadata_batch.append(create_metadata_dict(category_name, metric, district_str, district_data['name']))
                        except Exception as e:
                            logger.error(f"Error processing district {district_str} metric {metric.get('name', 'unknown')}: {str(e)}")
                            continue

        # Skip if no valid metrics were found
        if not texts_to_embed:
            logger.warning("No valid metrics found to embed. Skipping vector collection creation.")
            return

        # Process in batches
        total_points = len(texts_to_embed)
        points_to_upsert = []
        
        for i in range(0, total_points, BATCH_SIZE):
            batch_texts = texts_to_embed[i:i + BATCH_SIZE]
            batch_metadata = metadata_batch[i:i + BATCH_SIZE]
            
            # Get embeddings for the batch
            response = client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=batch_texts
            )
            embeddings = [data.embedding for data in response.data]
            
            # Create points for the batch
            batch_points = [
                rest.PointStruct(
                    id=str(uuid.uuid4()),
                    vector=embedding,
                    payload=metadata
                )
                for embedding, metadata in zip(embeddings, batch_metadata)
            ]
            points_to_upsert.extend(batch_points)
            
            # Upsert when batch is full or on last batch
            if len(points_to_upsert) >= BATCH_SIZE or i + BATCH_SIZE >= total_points:
                for upsert_attempt in range(MAX_RETRIES):
                    try:
                        if not qdrant.collection_exists(collection_name):
                            create_collection()
                        qdrant.upsert(
                            collection_name=collection_name,
                            points=points_to_upsert
                        )
                        points_to_upsert = []
                        break
                    except Exception as e:
                        logger.error(f"Upsert attempt {upsert_attempt + 1} failed: {str(e)}")
                        if upsert_attempt == MAX_RETRIES - 1:
                            raise
                        time.sleep(2 ** upsert_attempt)
        
        logger.info("Successfully created YTD vector collection")

    except Exception as e:
        logger.error(f"Error creating YTD vector collection: {str(e)}")
        logger.error(traceback.format_exc())
        # Don't re-raise the exception to allow the script to continue with other operations

def create_metric_text(category_name, metric, district_name):
    """Create text representation for embedding."""
    category_descriptions = {
        'Safety': 'public safety, emergency response, and first responders',
        'Crime': 'law enforcement, police activity, and criminal statistics',
        'Economy': 'business and economic development'
    }
    
    category_help_text = {
        'Safety': 'This metric helps track emergency response, public safety incidents, and first responder activity.',
        'Crime': 'This metric helps track law enforcement activity, police incidents, and crime statistics.',
        'Economy': 'This metric helps track business and economic activity.'
    }
    
    # Safely access metadata fields with defaults if they don't exist
    metadata = metric.get('metadata', {})
    summary = metadata.get('summary', 'No summary available')
    definition = metadata.get('definition', 'No detailed definition available')
    data_sf_url = metadata.get('data_sf_url', 'No data source URL available')
    
    return (
        f"Category: {category_name}\n"
        f"Metric Name: {metric['name']}\n"
        f"Description: {summary}\n"
        f"Detailed Definition: {definition}\n"
        f"This metric shows {metric['thisYear']} incidents in the current year"
        f" compared to {metric['lastYear']} incidents last year for {district_name}.\n"
        f"The data is current as of {metric['lastDataDate']}.\n"
        f"This is a {category_name.lower()} metric related to {category_descriptions.get(category_name, '')} in San Francisco.\n"
        f"{category_help_text.get(category_name, '')}\n"
        f"Data Source: {data_sf_url}\n"
        f"Query Context: {metric['queries'].get('metric_query', '')}\n"
        f"This data is specific to {district_name} in San Francisco."
    ).strip()

def create_metadata_dict(category_name, metric, district, district_name):
    """Create metadata dictionary for vector storage."""
    # Safely access metadata fields with defaults if they don't exist
    metadata = metric.get('metadata', {})
    summary = metadata.get('summary', 'No summary available')
    definition = metadata.get('definition', 'No detailed definition available')
    data_url = metadata.get('data_sf_url', 'No data source URL available')
    
    # Create the base metadata dictionary
    metadata_dict = {
        'category': category_name,
        'metric_name': metric['name'],
        'metric_id': metric['id'],
        'this_year': metric['thisYear'],
        'last_year': metric['lastYear'],
        'last_data_date': metric['lastDataDate'],
        'summary': summary,
        'definition': definition,
        'data_url': data_url,
        'district': district,
        'district_name': district_name,
        'trend_data': metric.get('trend_data', {}),
        'queries': metric['queries']
    }
    
    # Add location and category fields if available
    if 'location_fields' in metric:
        metadata_dict['location_fields'] = metric['location_fields']
    if 'category_fields' in metric:
        metadata_dict['category_fields'] = metric['category_fields']
    if 'numeric_id' in metric:
        metadata_dict['numeric_id'] = metric['numeric_id']
    
    return metadata_dict

def setup_logging():
    """Configure logging for the dashboard metrics generation."""
    # Create logs directory if it doesn't exist
    script_dir = os.path.dirname(os.path.abspath(__file__))
    logs_dir = os.path.join(script_dir, 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    
    # Configure logging with a single handler for both file and console
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    
    # Remove any existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # Add file handler
    file_handler = logging.FileHandler(os.path.join(logs_dir, 'dashboard_metrics.log'))
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(file_handler)
    
    # Add console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(console_handler)
    
    return logger

def main():
    """Main function to generate dashboard metrics."""
    # Set up logging
    setup_logging()
    
    # Define output directory
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "dashboard")
    os.makedirs(output_dir, exist_ok=True)
    
    # Load dashboard queries
    dashboard_queries_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "dashboard", "dashboard_queries.json")
    enhanced_queries_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "dashboard", "dashboard_queries_enhanced.json")
    
    # Try to load enhanced queries first, fall back to regular queries
    if os.path.exists(enhanced_queries_path):
        logging.info("Using enhanced dashboard queries")
        dashboard_queries = load_json_file(enhanced_queries_path)
    else:
        logging.info("Using standard dashboard queries")
        dashboard_queries = load_json_file(dashboard_queries_path)
    
    if not dashboard_queries:
        logging.error("Failed to load dashboard queries")
        return
    
    # Generate metrics
    generate_ytd_metrics(dashboard_queries, output_dir)
    
    logging.info("Dashboard metrics generation complete")

if __name__ == '__main__':
    main() 