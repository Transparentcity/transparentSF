#!/usr/bin/env python3
"""
Test script for weekly metric analysis.
This script tests the weekly analysis functionality with a single metric.
"""

import os
import sys
import logging
from datetime import datetime

# Add the parent directory to the path so we can import the module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.generate_weekly_analysis import run_weekly_analysis, generate_weekly_newsletter

# Configure basic logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def run_test():
    """Run a test of the weekly analysis with a single metric."""
    logger.info("Starting test of weekly analysis")
    
    # Choose a metric that is likely to have good data
    test_metric = "police_reported_incidents"
    
    logger.info(f"Testing weekly analysis with metric: {test_metric}")
    
    # Run the analysis for just this metric
    results = run_weekly_analysis(metrics_list=[test_metric], process_districts=False)
    
    if results and len(results) > 0:
        logger.info(f"Successfully generated weekly analysis for {test_metric}")
        
        # Generate a test newsletter
        newsletter_path = generate_weekly_newsletter(results)
        
        if newsletter_path:
            logger.info(f"Successfully generated newsletter at {newsletter_path}")
        else:
            logger.error("Failed to generate newsletter")
    else:
        logger.error(f"Failed to generate weekly analysis for {test_metric}")

if __name__ == "__main__":
    run_test() 