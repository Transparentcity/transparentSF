-- Test Query for Active Businesses Analysis
-- Copy and paste this into Google Cloud SQL Studio or your PostgreSQL client
-- Modify the dates and restaurant_only flag as needed

-- ============================================
-- CONFIGURATION: Change these values to test
-- ============================================
-- Set your test month here (format: 'YYYY-MM-DD')
SET test_month_start = '2024-01-01';
SET test_month_end = '2024-01-31';
-- Set to true to test restaurant filter, false for all businesses
SET restaurant_only = true;

-- ============================================
-- QUERY 1: New Businesses in the Month
-- ============================================
-- This counts businesses that started within the month and are still open
SELECT 
    COUNT(*) as new_businesses,
    'New businesses started in month' as query_type
FROM business_registrations_cache
WHERE location_start_date >= '2024-01-01'  -- Change to your test_month_start
    AND location_start_date <= '2024-01-31'  -- Change to your test_month_end
    AND (
        location_end_date IS NULL 
        OR location_end_date > '2024-01-31'  -- Change to your test_month_end
    )
    -- Restaurant filter (remove this AND clause for all businesses)
    AND (
        lic_code IS NOT NULL AND (
            lic_code LIKE '%H24%' 
            OR lic_code LIKE '%H25%' 
            OR lic_code LIKE '%H26%'
        )
    );

-- ============================================
-- QUERY 2: Closed Businesses in the Month
-- ============================================
SELECT 
    COUNT(*) as closed_businesses,
    'Businesses closed in month' as query_type
FROM business_registrations_cache
WHERE location_end_date >= '2024-01-01'  -- Change to your test_month_start
    AND location_end_date <= '2024-01-31'  -- Change to your test_month_end
    -- Restaurant filter (remove this AND clause for all businesses)
    AND (
        lic_code IS NOT NULL AND (
            lic_code LIKE '%H24%' 
            OR lic_code LIKE '%H25%' 
            OR lic_code LIKE '%H26%'
        )
    );

-- ============================================
-- QUERY 3: Active Businesses at Start of Month
-- ============================================
-- This counts businesses that started before the month and are still open
SELECT 
    COUNT(*) as active_businesses,
    'Active businesses at start of month' as query_type
FROM business_registrations_cache
WHERE location_start_date < '2024-01-01'  -- Change to your test_month_start
    AND (
        location_end_date IS NULL 
        OR location_end_date > '2024-01-31'  -- Change to your test_month_end
    )
    -- Restaurant filter (remove this AND clause for all businesses)
    AND (
        lic_code IS NOT NULL AND (
            lic_code LIKE '%H24%' 
            OR lic_code LIKE '%H25%' 
            OR lic_code LIKE '%H26%'
        )
    );

-- ============================================
-- COMBINED TEST: All Three Metrics for One Month
-- ============================================
-- Run this to get all three metrics in one result set
WITH month_data AS (
    SELECT 
        '2024-01-01'::date as month_start,  -- Change date here
        '2024-01-31'::date as month_end      -- Change date here
),
new_businesses AS (
    SELECT COUNT(*) as count
    FROM business_registrations_cache, month_data
    WHERE location_start_date >= month_data.month_start
        AND location_start_date <= month_data.month_end
        AND (
            location_end_date IS NULL 
            OR location_end_date > month_data.month_end
        )
        -- Remove the restaurant filter below to test all businesses
        AND (
            lic_code IS NOT NULL AND (
                lic_code LIKE '%H24%' 
                OR lic_code LIKE '%H25%' 
                OR lic_code LIKE '%H26%'
            )
        )
),
closed_businesses AS (
    SELECT COUNT(*) as count
    FROM business_registrations_cache, month_data
    WHERE location_end_date >= month_data.month_start
        AND location_end_date <= month_data.month_end
        -- Remove the restaurant filter below to test all businesses
        AND (
            lic_code IS NOT NULL AND (
                lic_code LIKE '%H24%' 
                OR lic_code LIKE '%H25%' 
                OR lic_code LIKE '%H26%'
            )
        )
),
active_businesses AS (
    SELECT COUNT(*) as count
    FROM business_registrations_cache, month_data
    WHERE location_start_date < month_data.month_start
        AND (
            location_end_date IS NULL 
            OR location_end_date > month_data.month_end
        )
        -- Remove the restaurant filter below to test all businesses
        AND (
            lic_code IS NOT NULL AND (
                lic_code LIKE '%H24%' 
                OR lic_code LIKE '%H25%' 
                OR lic_code LIKE '%H26%'
            )
        )
)
SELECT 
    (SELECT count FROM active_businesses) as active_at_start,
    (SELECT count FROM new_businesses) as new_in_month,
    (SELECT count FROM closed_businesses) as closed_in_month,
    (SELECT count FROM active_businesses) + (SELECT count FROM new_businesses) - (SELECT count FROM closed_businesses) as active_at_end,
    (SELECT count FROM new_businesses) - (SELECT count FROM closed_businesses) as net_change;

-- ============================================
-- DEBUGGING: Sample Records
-- ============================================
-- Use this to see actual records that match the filter
SELECT 
    certificate_number,
    dba_name,
    location_start_date,
    location_end_date,
    lic_code,
    lic_code_description,
    naic_code_description
FROM business_registrations_cache
WHERE location_start_date >= '2024-01-01'  -- Change to your test_month_start
    AND location_start_date <= '2024-01-31'  -- Change to your test_month_end
    AND (
        location_end_date IS NULL 
        OR location_end_date > '2024-01-31'  -- Change to your test_month_end
    )
    -- Restaurant filter
    AND (
        lic_code IS NOT NULL AND (
            lic_code LIKE '%H24%' 
            OR lic_code LIKE '%H25%' 
            OR lic_code LIKE '%H26%'
        )
    )
LIMIT 10;

