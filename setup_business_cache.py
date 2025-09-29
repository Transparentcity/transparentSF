#!/usr/bin/env python3
"""
Setup Business Cache

This script initializes the business cache system by creating the database tables
and optionally populating them with data.

Usage:
    python setup_business_cache.py [--populate] [--limit N]

Examples:
    python setup_business_cache.py                    # Just create tables
    python setup_business_cache.py --populate         # Create tables and populate with all data
    python setup_business_cache.py --populate --limit 1000  # Create tables and populate with 1000 records
"""

import sys
import os
import argparse
import logging

# Add the ai directory to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'ai'))

from ai.tools.business_cache_processor import BusinessCacheProcessor

def main():
    parser = argparse.ArgumentParser(description='Setup business cache system')
    parser.add_argument('--populate', action='store_true', help='Populate cache with data after creating tables')
    parser.add_argument('--limit', type=int, help='Limit number of business records to process when populating')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')
    
    args = parser.parse_args()
    
    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    processor = BusinessCacheProcessor()
    
    print("Setting up business cache system...")
    print("=" * 50)
    
    try:
        # Create tables
        print("Creating database tables...")
        processor.create_cache_tables()
        print("✅ Database tables created successfully")
        
        if args.populate:
            print("\nPopulating cache with business data...")
            print("This may take several minutes for the full dataset...")
            
            result = processor.refresh_cache(args.limit)
            print(f"\n✅ Cache populated successfully!")
            print(f"Business Records: {result['business_records']:,}")
            print(f"Tax Records: {result['tax_records']:,}")
            print(f"Duration: {result['duration_seconds']:.2f} seconds")
        else:
            print("\n✅ Tables created. To populate with data, run:")
            print("   python setup_business_cache.py --populate")
            print("\nOr for a quick test with limited data:")
            print("   python setup_business_cache.py --populate --limit 1000")
        
        print("\n🎉 Business cache system is ready!")
        print("\nNext steps:")
        print("1. Your vacancy map will now use cached data for fast searches")
        print("2. Run 'python refresh_business_cache.py' periodically to update the cache")
        print("3. Check cache status with 'python refresh_business_cache.py --stats'")
        
    except Exception as e:
        print(f"❌ Error setting up business cache: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
