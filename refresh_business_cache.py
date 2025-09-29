#!/usr/bin/env python3
"""
Refresh Business Cache Script

This script refreshes the business cache by fetching all data from DataSF API
and storing it in the local database for fast searching.

Usage:
    python refresh_business_cache.py [--limit N] [--stats]

Examples:
    python refresh_business_cache.py                    # Refresh all data
    python refresh_business_cache.py --limit 1000       # Refresh first 1000 records
    python refresh_business_cache.py --stats            # Show cache statistics
"""

import sys
import os
import argparse
import logging

# Add the ai directory to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'ai'))

from ai.tools.business_cache_processor import BusinessCacheProcessor

def main():
    parser = argparse.ArgumentParser(description='Refresh business cache')
    parser.add_argument('--limit', type=int, help='Limit number of business records to process')
    parser.add_argument('--stats', action='store_true', help='Show cache statistics')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')
    
    args = parser.parse_args()
    
    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    processor = BusinessCacheProcessor()
    
    if args.stats:
        print("Business Cache Statistics:")
        print("=" * 50)
        try:
            stats = processor.get_cache_stats()
            print(f"Business Records: {stats['business_records']:,}")
            print(f"Open Businesses: {stats['open_businesses']:,}")
            print(f"Closed Businesses: {stats['closed_businesses']:,}")
            print(f"Tax Records: {stats['tax_records']:,}")
            print(f"Last Updated: {stats['last_updated']}")
        except Exception as e:
            print(f"Error getting cache stats: {e}")
            sys.exit(1)
    else:
        print("Refreshing business cache...")
        print("=" * 50)
        
        try:
            result = processor.refresh_cache(args.limit)
            print(f"\nCache refresh completed successfully!")
            print(f"Business Records: {result['business_records']:,}")
            print(f"Tax Records: {result['tax_records']:,}")
            print(f"Duration: {result['duration_seconds']:.2f} seconds")
        except Exception as e:
            print(f"Error refreshing cache: {e}")
            sys.exit(1)

if __name__ == "__main__":
    main()
