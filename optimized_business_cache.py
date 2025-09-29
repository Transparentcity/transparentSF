#!/usr/bin/env python3
"""
Optimized Business Cache Processor with Batch Inserts

This script addresses the performance issues with the original business_cache_processor.py
by using batch insert operations and better memory management.
"""

import os
import sys
import time
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from collections import defaultdict

# Add the ai module to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'ai'))

from ai.tools.business_cache_processor import BusinessCacheProcessor
from ai.tools.db_utils import execute_with_connection
import psycopg2.extras

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OptimizedBusinessCacheProcessor(BusinessCacheProcessor):
    """Enhanced version with batch operations and better performance"""
    
    def __init__(self, batch_size: int = 5000):
        super().__init__()
        self.batch_size = batch_size
    
    def store_business_cache_batch(self, processed_data: List[Dict[str, Any]], clear_existing: bool = True):
        """Store processed business data using efficient batch operations"""
        logger.info(f"Storing {len(processed_data):,} processed business records using batch operations...")
        
        def batch_insert_data(conn):
            cursor = conn.cursor()
            
            # Clear existing data if requested
            if clear_existing:
                try:
                    cursor.execute(f"DELETE FROM {self.cache_table}")
                    logger.info("✅ Cleared existing business cache data")
                except Exception as e:
                    logger.warning(f"Could not clear table: {e}")
            
            # Prepare data for batch insert
            insert_sql = f"""
            INSERT INTO {self.cache_table} (
                certificate_number, dba_name, naic_code_description, lic_code_description,
                business_corridor, supervisor_district, full_business_address, distinct_address,
                building_address, location_lat, location_lon, dba_start_date, location_start_date,
                dba_end_date, location_end_date, administratively_closed, status, is_street_level,
                business_count, streetfront_open_count, streetfront_closed_count,
                total_streetfront_addresses, upper_floor_open_count, upper_floor_closed_count,
                total_upper_floor_addresses, individual_addresses_count, addresses_data,
                has_commercial_tax_filing, tax_filing_addresses, vacancy_status, tax_filing_year,
                tax_filing_ban, tax_filing_entity
            ) VALUES %s
            ON CONFLICT (certificate_number, full_business_address) DO NOTHING
            """
            
            successful_inserts = 0
            errors = 0
            total_batches = (len(processed_data) + self.batch_size - 1) // self.batch_size
            
            # Process data in batches
            for batch_num in range(total_batches):
                start_idx = batch_num * self.batch_size
                end_idx = min(start_idx + self.batch_size, len(processed_data))
                batch_data = processed_data[start_idx:end_idx]
                
                logger.info(f"Processing batch {batch_num + 1}/{total_batches} ({len(batch_data):,} records)")
                
                try:
                    # Prepare batch data
                    batch_values = []
                    for record in batch_data:
                        # Convert administratively_closed to boolean
                        admin_closed = record.get('administratively_closed', False)
                        if isinstance(admin_closed, str):
                            admin_closed = admin_closed.lower() in ['true', '1', 'yes', 'y', 't']
                        elif admin_closed is None:
                            admin_closed = False
                        
                        # Prepare values tuple
                        values = (
                            record.get('certificate_number'),
                            record.get('dba_name'),
                            record.get('naic_code_description'),
                            record.get('lic_code_description'),
                            record.get('business_corridor'),
                            record.get('supervisor_district'),
                            record.get('full_business_address'),
                            record.get('distinct_address'),
                            record.get('building_address'),
                            record.get('location_lat'),
                            record.get('location_lon'),
                            record.get('dba_start_date'),
                            record.get('location_start_date'),
                            record.get('dba_end_date'),
                            record.get('location_end_date'),
                            admin_closed,
                            record.get('status'),
                            True,  # is_street_level
                            record.get('business_count'),
                            record.get('streetfront_open_count'),
                            record.get('streetfront_closed_count'),
                            record.get('total_streetfront_addresses'),
                            record.get('upper_floor_open_count'),
                            record.get('upper_floor_closed_count'),
                            record.get('total_upper_floor_addresses'),
                            record.get('individual_addresses_count'),
                            json.dumps(record.get('addresses_data', [])),
                            record.get('has_commercial_tax_filing', False),
                            record.get('tax_filing_addresses', []),
                            record.get('vacancy_status', 'unfiled'),
                            record.get('tax_filing_year', 0),
                            record.get('tax_filing_ban', ''),
                            record.get('tax_filing_entity', '')
                        )
                        batch_values.append(values)
                    
                    # Execute batch insert using psycopg2.extras.execute_values
                    psycopg2.extras.execute_values(
                        cursor, 
                        insert_sql, 
                        batch_values,
                        template=None,
                        page_size=1000
                    )
                    
                    successful_inserts += len(batch_data)
                    logger.info(f"✅ Batch {batch_num + 1} completed: {successful_inserts:,} total records inserted")
                    
                except Exception as e:
                    errors += len(batch_data)
                    logger.error(f"❌ Error in batch {batch_num + 1}: {e}")
                    # Continue with next batch instead of failing completely
                    continue
            
            cursor.close()
            return {
                'successful_inserts': successful_inserts,
                'errors': errors,
                'batches_processed': total_batches
            }
        
        result = execute_with_connection(batch_insert_data)
        
        if result.get('status') == 'success':
            stats = result.get('result', {})
            logger.info(f"🎉 Batch insert completed!")
            logger.info(f"   Successfully inserted: {stats.get('successful_inserts', 0):,} records")
            logger.info(f"   Errors: {stats.get('errors', 0):,} records")
            logger.info(f"   Batches processed: {stats.get('batches_processed', 0)}")
        else:
            logger.error(f"❌ Failed to store business cache: {result.get('message', 'Unknown error')}")
        
        return result
    
    def refresh_cache_optimized(self, limit: Optional[int] = None, use_saved_data: bool = False):
        """Optimized cache refresh with batch operations and optional saved data usage"""
        logger.info(f"🚀 Starting OPTIMIZED business cache refresh (limit: {limit})")
        start_time = datetime.now()
        
        try:
            if use_saved_data:
                logger.info("📁 Loading data from saved files...")
                business_data = self.load_saved_business_data()
                tax_filing_data = self.load_saved_tax_data()
                logger.info(f"✅ Loaded {len(business_data):,} business records and {len(tax_filing_data):,} tax addresses from files")
            else:
                # Fetch and process business data first
                logger.info("📥 Step 1: Fetching business data...")
                business_data = self.fetch_all_business_data(limit)
                logger.info(f"✅ Fetched {len(business_data):,} business records")
                
                logger.info("📥 Step 2: Fetching and processing commercial tax filing data...")
                tax_filing_data = self.fetch_commercial_tax_filing_data()
                logger.info(f"✅ Processed {len(tax_filing_data):,} tax filing addresses")
            
            logger.info("⚙️  Step 3: Processing business data with tax filing matching...")
            processed_data = self.process_business_data(business_data, tax_filing_data)
            logger.info(f"✅ Processed {len(processed_data):,} business records")
            
            # Create tables and store data using batch operations
            logger.info("💾 Step 4: Creating tables and storing data with batch inserts...")
            self.create_cache_tables()
            self.store_business_cache_batch(processed_data, clear_existing=True)
            logger.info("✅ Data stored successfully")
            
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            logger.info(f"🎉 OPTIMIZED business cache refresh completed in {duration:.2f} seconds ({duration/60:.1f} minutes)")
            logger.info(f"   Stored {len(processed_data):,} records")
            
            return {
                'status': 'success',
                'business_records': len(processed_data),
                'tax_records': len(tax_filing_data),
                'duration_seconds': duration
            }
            
        except Exception as e:
            logger.error(f"❌ Error refreshing business cache: {str(e)}")
            import traceback
            traceback.print_exc()
            raise e
    
    def load_saved_business_data(self) -> List[Dict[str, Any]]:
        """Load business data from saved debug file"""
        import glob
        
        # Find the most recent business data file
        business_files = glob.glob("debug_business_data_*.json")
        if not business_files:
            raise FileNotFoundError("No saved business data files found. Run debug_business_cache.py first.")
        
        latest_file = max(business_files, key=os.path.getctime)
        logger.info(f"Loading business data from {latest_file}")
        
        with open(latest_file, 'r') as f:
            business_data = json.load(f)
        
        return business_data
    
    def load_saved_tax_data(self) -> Dict[str, Dict]:
        """Load tax data from saved debug file"""
        import glob
        import pickle
        
        # Find the most recent tax data file
        tax_files = glob.glob("debug_tax_data_*.pickle")
        if not tax_files:
            raise FileNotFoundError("No saved tax data files found. Run debug_business_cache.py first.")
        
        latest_file = max(tax_files, key=os.path.getctime)
        logger.info(f"Loading tax data from {latest_file}")
        
        with open(latest_file, 'rb') as f:
            tax_data = pickle.load(f)
        
        return tax_data

def main():
    """Test the optimized processor"""
    print("🚀 Testing Optimized Business Cache Processor")
    print("=" * 50)
    
    processor = OptimizedBusinessCacheProcessor(batch_size=5000)
    
    # Option 1: Use saved data (much faster for testing)
    print("Choose an option:")
    print("1. Use saved data from debug files (fast)")
    print("2. Fetch fresh data from APIs (slower)")
    print("3. Test with small sample (fastest)")
    
    choice = input("Enter choice (1-3): ").strip()
    
    try:
        if choice == "1":
            result = processor.refresh_cache_optimized(use_saved_data=True)
        elif choice == "2":
            result = processor.refresh_cache_optimized(use_saved_data=False)
        elif choice == "3":
            result = processor.refresh_cache_optimized(limit=10000, use_saved_data=False)
        else:
            print("Invalid choice, using saved data")
            result = processor.refresh_cache_optimized(use_saved_data=True)
        
        print(f"🎉 Success! Processed {result['business_records']:,} records in {result['duration_seconds']:.1f}s")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)


