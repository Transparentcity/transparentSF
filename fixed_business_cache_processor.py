#!/usr/bin/env python3
"""
Fixed Business Cache Processor - Individual Unit Processing

This fixes the address grouping logic to:
1. Keep individual units (2139 A, 2139 B, 2139 C) as separate records  
2. Normalize addresses only for tax matching
3. Calculate vacancy at the individual unit level
"""

import os
import sys
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
import json
from collections import defaultdict

# Add the ai module to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'ai'))

from ai.tools.business_cache_processor import BusinessCacheProcessor

logger = logging.getLogger(__name__)

class FixedBusinessCacheProcessor(BusinessCacheProcessor):
    """Building-grouped version that shows one dot per building with majority-rule coloring"""
    
    def process_business_data_building_grouped(self, business_data: List[Dict[str, Any]], tax_filing_data: Dict[str, Dict] = None) -> List[Dict[str, Any]]:
        """Process business data grouped by building location with most recent business per address"""
        logger.info(f"Processing {len(business_data)} business records grouped by building location...")
        
        if tax_filing_data is None:
            tax_filing_data = {}
        
        # Step 1: Keep only most recent business per address
        most_recent_by_address = {}
        for record in business_data:
            address = record.get('full_business_address', '').strip()
            if not address:
                continue
            
            # Parse dates to find most recent
            dba_start = self._parse_date(record.get('dba_start_date'))
            location_start = self._parse_date(record.get('location_start_date'))
            most_recent_date = max([d for d in [dba_start, location_start] if d is not None] or [datetime(1900, 1, 1)])
            
            if address not in most_recent_by_address or most_recent_date > most_recent_by_address[address]['date']:
                most_recent_by_address[address] = {
                    'record': record,
                    'date': most_recent_date
                }
        
        logger.info(f"Filtered to {len(most_recent_by_address)} most recent businesses per address")
        
        # Step 2: Group by building location using street number and name  
        building_groups = defaultdict(list)
        for address, data in most_recent_by_address.items():
            record = data['record']
            
            # Extract street number and name for building grouping
            import re
            
            # Normalize address for building grouping (remove unit designations)
            building_key = self.normalize_address(address)
            
            # Get coordinates for validation
            lat = self._parse_location(record.get('location', {}), 'latitude')
            lon = self._parse_location(record.get('location', {}), 'longitude')
            
            if lat and lon and building_key:
                building_groups[building_key].append({
                    'address': address,
                    'record': record,
                    'coordinates': (lat, lon)
                })
        
        logger.info(f"Grouped into {len(building_groups)} building locations")
        
        # Step 3: Process each building group
        processed_data = []
        for building_key, address_list in building_groups.items():
            try:
                processed_building = self._process_building_group(building_key, address_list, tax_filing_data)
                processed_data.append(processed_building)
            except Exception as e:
                logger.warning(f"Error processing building group {building_key}: {e}")
                continue
        
        logger.info(f"Processed {len(processed_data)} building groups from {len(business_data)} business records")
        return processed_data
    
    def _process_building_group(self, building_key: str, address_list: List[Dict[str, Any]], tax_filing_data: Dict[str, Dict]) -> Dict[str, Any]:
        """Process a group of addresses at the same building location"""
        
        # Use the first record as the base for building-level info
        base_record = address_list[0]['record']
        building_address = address_list[0]['address']  # Primary building address
        
        # Analyze each unit in the building
        unit_data = []
        open_units = 0
        closed_units = 0
        street_level_units = 0
        upper_floor_units = 0
        
        for addr_data in address_list:
            record = addr_data['record'] 
            address = addr_data['address']
            
            # Determine unit status
            dba_start = self._parse_date(record.get('dba_start_date'))
            location_start = self._parse_date(record.get('location_start_date'))
            dba_end = self._parse_date(record.get('dba_end_date'))
            location_end = self._parse_date(record.get('location_end_date'))

            all_open_dates = [d for d in [dba_start, location_start] if d is not None]
            all_close_dates = [d for d in [dba_end, location_end] if d is not None]

            most_recent_open_date = max(all_open_dates) if all_open_dates else None
            most_recent_close_date = max(all_close_dates) if all_close_dates else None

            # Determine unit status
            if not most_recent_close_date:
                unit_status = 'Open'
            elif not most_recent_open_date:
                unit_status = 'Closed'
            elif most_recent_close_date > most_recent_open_date:
                unit_status = 'Closed'
            else:
                unit_status = 'Open'
            
            # Count by status and floor level
            is_street_level = not self._is_upper_floor_address(address)
            
            if unit_status == 'Open':
                open_units += 1
            else:
                closed_units += 1
                
            if is_street_level:
                street_level_units += 1
            else:
                upper_floor_units += 1
            
            # Add tax filing info for this unit
            normalized_address = self.normalize_address(address)
            tax_info = tax_filing_data.get(normalized_address, {})
            
            unit_data.append({
                'address': address,
                'status': unit_status,
                'is_street_level': is_street_level,
                'business_name': record.get('dba_name'),
                'certificate_number': record.get('certificate_number'),
                'has_tax_filing': normalized_address in tax_filing_data,
                'vacancy_status': tax_info.get('vacancy_status', 'unfiled'),
                'tax_filing_year': tax_info.get('year', 0)
            })
        
        total_units = len(address_list)
        
        # Determine building color based on majority rule
        if closed_units >= 3 and closed_units > open_units:
            building_status = 'Closed'
            building_color = 'red'
        elif open_units >= 3 and open_units > closed_units:
            building_status = 'Open' 
            building_color = 'green'
        else:
            building_status = 'Mixed'
            building_color = 'gray'
        
        # Create building-level record
        processed_record = {
            'certificate_number': base_record.get('certificate_number'),
            'dba_name': f"{total_units}-unit building",  # Building description
            'naic_code_description': base_record.get('naic_code_description'),
            'lic_code_description': base_record.get('lic_code_description'),
            'business_corridor': base_record.get('business_corridor'),
            'supervisor_district': base_record.get('supervisor_district'),
            'full_business_address': building_address,
            'distinct_address': building_address,
            'building_address': building_address,
            'location_lat': self._parse_location(base_record.get('location', {}), 'latitude'),
            'location_lon': self._parse_location(base_record.get('location', {}), 'longitude'),
            'dba_start_date': self._parse_date(base_record.get('dba_start_date')),
            'location_start_date': self._parse_date(base_record.get('location_start_date')),
            'dba_end_date': self._parse_date(base_record.get('dba_end_date')),
            'location_end_date': self._parse_date(base_record.get('location_end_date')),
            'administratively_closed': base_record.get('administratively_closed', False),
            'status': building_status,
            'is_street_level': True,  # Building level indicator
            
            # Building-level counts  
            'business_count': total_units,
            'streetfront_open_count': sum(1 for unit in unit_data if unit['is_street_level'] and unit['status'] == 'Open'),
            'streetfront_closed_count': sum(1 for unit in unit_data if unit['is_street_level'] and unit['status'] == 'Closed'),
            'total_streetfront_addresses': street_level_units,
            'upper_floor_open_count': sum(1 for unit in unit_data if not unit['is_street_level'] and unit['status'] == 'Open'),
            'upper_floor_closed_count': sum(1 for unit in unit_data if not unit['is_street_level'] and unit['status'] == 'Closed'),
            'total_upper_floor_addresses': upper_floor_units,
            'individual_addresses_count': total_units,
            'addresses_data': unit_data,
            
            # Building-level tax info (use primary address)
            'has_commercial_tax_filing': any(unit['has_tax_filing'] for unit in unit_data),
            'tax_filing_addresses': [self.normalize_address(building_address)],
            'vacancy_status': unit_data[0].get('vacancy_status', 'unfiled'),
            'tax_filing_year': unit_data[0].get('tax_filing_year', 0),
            'tax_filing_ban': '',
            'tax_filing_entity': '',
            
            # Custom fields for building visualization
            'building_color': building_color,
            'open_units': open_units,
            'closed_units': closed_units,
            'total_units': total_units
        }
        
        return processed_record
    
    def _process_individual_record(self, record: Dict[str, Any], tax_filing_data: Dict[str, Dict]) -> Dict[str, Any]:
        """Process a single business record without grouping"""
        
        # Get the full address (including unit designation)
        full_address = record.get('full_business_address', '').strip()
        
        # Determine if this is a street-level address
        is_street_level = not self._is_upper_floor_address(full_address)
        
        # Get status of this individual business based on dates
        dba_start = self._parse_date(record.get('dba_start_date'))
        location_start = self._parse_date(record.get('location_start_date'))
        dba_end = self._parse_date(record.get('dba_end_date'))
        location_end = self._parse_date(record.get('location_end_date'))

        all_open_dates = []
        all_close_dates = []
        if dba_start:
            all_open_dates.append(dba_start)
        if location_start:
            all_open_dates.append(location_start)
        if dba_end:
            all_close_dates.append(dba_end)
        if location_end:
            all_close_dates.append(location_end)

        most_recent_open_date = max(all_open_dates) if all_open_dates else None
        most_recent_close_date = max(all_close_dates) if all_close_dates else None

        # Determine individual unit status
        if not most_recent_close_date:
            status = 'Open'
        elif not most_recent_open_date:
            status = 'Closed'
        elif most_recent_close_date > most_recent_open_date:
            status = 'Closed'
        else:
            status = 'Open'
        
        # Create base processed record for this individual unit
        processed_record = {
            'certificate_number': record.get('certificate_number'),
            'dba_name': record.get('dba_name'),
            'naic_code_description': record.get('naic_code_description'),
            'lic_code_description': record.get('lic_code_description'),
            'business_corridor': record.get('business_corridor'),
            'supervisor_district': record.get('supervisor_district'),
            'full_business_address': full_address,
            'distinct_address': full_address,  # Keep the full address including unit
            'building_address': full_address,  # Individual unit address
            'location_lat': self._parse_location(record.get('location', {}), 'latitude'),
            'location_lon': self._parse_location(record.get('location', {}), 'longitude'),
            'dba_start_date': dba_start,
            'location_start_date': location_start,
            'dba_end_date': dba_end,
            'location_end_date': location_end,
            'administratively_closed': record.get('administratively_closed', False),
            'status': status,
            'is_street_level': is_street_level,
            
            # Individual unit counts (always 1)
            'business_count': 1,
            'individual_addresses_count': 1,
            
            # Street-level counts for this individual unit
            'streetfront_open_count': 1 if (is_street_level and status == 'Open') else 0,
            'streetfront_closed_count': 1 if (is_street_level and status == 'Closed') else 0,
            'total_streetfront_addresses': 1 if is_street_level else 0,
            
            # Upper-floor counts for this individual unit  
            'upper_floor_open_count': 1 if (not is_street_level and status == 'Open') else 0,
            'upper_floor_closed_count': 1 if (not is_street_level and status == 'Closed') else 0,
            'total_upper_floor_addresses': 1 if not is_street_level else 0,
            
            # Store individual record data
            'addresses_data': [record.copy()]
        }
        
        # Add tax filing matching using normalized address (but keep individual record)
        if tax_filing_data:
            # Normalize the address for tax matching only
            normalized_address = self.normalize_address(full_address)
            
            # Check if normalized address matches any tax filing data
            if normalized_address in tax_filing_data:
                tax_info = tax_filing_data[normalized_address]
                processed_record['has_commercial_tax_filing'] = True
                processed_record['tax_filing_addresses'] = [normalized_address]
                processed_record['vacancy_status'] = tax_info.get('vacancy_status', 'unfiled')
                processed_record['tax_filing_year'] = tax_info.get('year', 0)
                processed_record['tax_filing_ban'] = tax_info.get('ban', '')
                processed_record['tax_filing_entity'] = tax_info.get('entity', '')
            else:
                processed_record['has_commercial_tax_filing'] = False
                processed_record['tax_filing_addresses'] = []
                processed_record['vacancy_status'] = 'unfiled'
                processed_record['tax_filing_year'] = 0
                processed_record['tax_filing_ban'] = ''
                processed_record['tax_filing_entity'] = ''
        else:
            processed_record['has_commercial_tax_filing'] = False
            processed_record['tax_filing_addresses'] = []
            processed_record['vacancy_status'] = 'unfiled'
            processed_record['tax_filing_year'] = 0
            processed_record['tax_filing_ban'] = ''
            processed_record['tax_filing_entity'] = ''
        
        return processed_record
    
    def refresh_cache_fixed(self, limit: Optional[int] = None, use_saved_data: bool = False):
        """Refresh cache using individual unit processing"""
        logger.info(f"🚀 Starting FIXED business cache refresh (individual units, limit: {limit})")
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
            
            logger.info("⚙️  Step 3: Processing business data grouped by building location (most recent per address)...")
            processed_data = self.process_business_data_building_grouped(business_data, tax_filing_data)
            logger.info(f"✅ Processed {len(processed_data):,} building location records")
            
            # Create tables and store data using batch operations
            logger.info("💾 Step 4: Creating tables and storing building location data...")
            self.create_cache_tables()
            self.store_business_cache_batch(processed_data, clear_existing=True)
            logger.info("✅ Building location data stored successfully")
            
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            logger.info(f"🎉 FIXED business cache refresh completed in {duration:.2f} seconds ({duration/60:.1f} minutes)")
            logger.info(f"   Stored {len(processed_data):,} building location records")
            
            # Show before/after comparison
            compression_ratio = len(business_data) / len(processed_data) if processed_data else 1
            logger.info(f"📊 Data comparison:")
            logger.info(f"   Input business records: {len(business_data):,}")
            logger.info(f"   Output building locations: {len(processed_data):,}")  
            logger.info(f"   Compression ratio: {compression_ratio:.2f}x (consolidates multiple businesses per building)")
            
            return {
                'status': 'success',
                'business_records': len(processed_data),
                'tax_records': len(tax_filing_data),
                'duration_seconds': duration,
                'individual_units': True
            }
            
        except Exception as e:
            logger.error(f"❌ Error refreshing fixed business cache: {str(e)}")
            import traceback
            traceback.print_exc()
            raise e
    
    def store_business_cache_batch(self, processed_data: List[Dict[str, Any]], clear_existing: bool = True):
        """Store processed business data using efficient batch operations - same as optimized version"""
        from ai.tools.db_utils import execute_with_connection
        import psycopg2.extras
        
        logger.info(f"Storing {len(processed_data):,} individual unit records using batch operations...")
        
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
            batch_size = 5000
            total_batches = (len(processed_data) + batch_size - 1) // batch_size
            
            # Process data in batches
            for batch_num in range(total_batches):
                start_idx = batch_num * batch_size
                end_idx = min(start_idx + batch_size, len(processed_data))
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
                            record.get('is_street_level'),
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
            logger.info(f"   Successfully inserted: {stats.get('successful_inserts', 0):,} individual unit records")
            logger.info(f"   Errors: {stats.get('errors', 0):,} records")
            logger.info(f"   Batches processed: {stats.get('batches_processed', 0)}")
        else:
            logger.error(f"❌ Failed to store business cache: {result.get('message', 'Unknown error')}")
        
        return result
    
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
    """Test the fixed processor"""
    print("🔧 Testing FIXED Business Cache Processor (Individual Units)")
    print("=" * 60)
    
    processor = FixedBusinessCacheProcessor()
    
    print("Choose an option:")
    print("1. Use saved data from debug files (fast)")
    print("2. Fetch fresh data from APIs (slower)")
    print("3. Test with small sample (fastest)")
    
    choice = input("Enter choice (1-3): ").strip()
    
    try:
        if choice == "1":
            result = processor.refresh_cache_fixed(use_saved_data=True)
        elif choice == "2":
            result = processor.refresh_cache_fixed(use_saved_data=False)
        elif choice == "3":
            result = processor.refresh_cache_fixed(limit=5000, use_saved_data=False)
        else:
            print("Invalid choice, using saved data")
            result = processor.refresh_cache_fixed(use_saved_data=True)
        
        print(f"\n🎉 Success! Processed {result['business_records']:,} building locations in {result['duration_seconds']:.1f}s")
        print(f"📊 Now buildings show as single dots sized by unit count and colored by majority status!")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
