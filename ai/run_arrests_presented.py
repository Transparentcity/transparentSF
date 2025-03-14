#!/usr/bin/env python3
"""
Script to run the dashboard metrics generation for just the "Arrests Presented to DA" metric.
This is useful for debugging and testing the metric.
"""

import os
import sys
import logging
from generate_dashboard_metrics import main

if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    # Run the main function with the --metric argument
    sys.argv = [sys.argv[0], "--metric", "Arrests Presented to DA"]
    main() 