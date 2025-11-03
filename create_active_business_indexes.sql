-- Create indexes to speed up active businesses queries
-- Run this script to add the necessary indexes

-- Index on location_start_date for filtering by start date
CREATE INDEX IF NOT EXISTS idx_business_location_start_date 
ON business_registrations_cache(location_start_date);

-- Index on location_end_date for filtering by end date  
CREATE INDEX IF NOT EXISTS idx_business_location_end_date 
ON business_registrations_cache(location_end_date);

-- Composite index for date range queries (start < X AND (end IS NULL OR end > Y))
CREATE INDEX IF NOT EXISTS idx_business_location_dates 
ON business_registrations_cache(location_start_date, location_end_date);

-- Index on lic_code for restaurant filtering (case-insensitive search)
-- Note: This helps but LIKE '%H24%' still can't use prefix index
CREATE INDEX IF NOT EXISTS idx_business_lic_code 
ON business_registrations_cache(lic_code);

-- Analyze the table to update statistics for query planner
ANALYZE business_registrations_cache;

