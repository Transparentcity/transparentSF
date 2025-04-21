#!/usr/bin/env python3
"""
Test script for time series database functionality.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import argparse
import sys
import logging

from store_time_series import get_postgres_connection, store_chart_data, get_biggest_deltas

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def generate_sample_data(n_periods=24, n_groups=5, start_date='2022-01-01'):
    """Generate a sample DataFrame with time series data."""
    start_date = pd.to_datetime(start_date)
    dates = [start_date + timedelta(days=30*i) for i in range(n_periods)]
    
    groups = [f"Group {chr(65+i)}" for i in range(n_groups)]  # Group A, B, C, etc.
    
    data = []
    for date in dates:
        for group in groups:
            # Create some seasonal pattern with random noise
            month = date.month
            seasonal_factor = 1.0 + 0.3 * np.sin(month * np.pi / 6)  # Higher in summer
            
            # Add trend
            trend_factor = 1.0 + 0.02 * (date.year - start_date.year) * 12 + 0.02 * (date.month - start_date.month)
            
            # Add group-specific factor
            group_idx = ord(group[-1]) - 65
            group_factor = 1.0 + 0.2 * group_idx
            
            # Base value with randomness
            base_value = 100 * seasonal_factor * trend_factor * group_factor
            value = base_value * (1 + np.random.normal(0, 0.1))  # 10% random noise
            
            # Add some anomalies occasionally
            if np.random.random() < 0.05:  # 5% chance of anomaly
                value = value * (1.5 + np.random.random())
            
            data.append({
                'time_period': date,
                'group': group,
                'value': value
            })
    
    return pd.DataFrame(data)

def test_store_time_series():
    """Test storing time series data in the database."""
    logging.info("Generating sample data...")
    df = generate_sample_data()
    
    logging.info(f"Generated {len(df)} sample data points")
    
    # Prepare context variables
    context_variables = {
        "chart_title": "Test Time Series Chart",
        "y_axis_label": "Test Value",
        "object_type": "test",
        "object_id": "sample-1",
        "object_name": "Sample Test Data",
        "dataset": df  # Full dataset will be passed
    }
    
    # Convert 'time_period' column to datetime if it's not already
    df['time_period'] = pd.to_datetime(df['time_period'])
    
    # Store the data
    logging.info("Storing data in database...")
    result = store_chart_data(
        aggregated_df=df,  # We're using pre-aggregated data
        context_variables=context_variables,
        time_series_field='time_period',
        numeric_fields=['value'],
        group_field='group',
        period_type='month',
        filter_conditions=[]
    )
    
    logging.info(f"Result: {result}")
    
    # Query for biggest deltas
    current_month = "2023-12"
    prev_month = "2023-11"
    
    logging.info(f"Querying for biggest deltas between {prev_month} and {current_month}...")
    deltas = get_biggest_deltas(
        current_period=current_month,
        comparison_period=prev_month,
        limit=10
    )
    
    logging.info(f"Biggest deltas result: {deltas}")
    
    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test time series database functionality")
    args = parser.parse_args()
    
    test_store_time_series() 