# Zoning Polygon Spatial Join - Issue Analysis & Fix

## Problem Identified

Businesses at addresses like **2139 Polk Street** were showing `zoning_district = NULL` even though they are clearly inside zoning polygons (specifically, NCD-POLK).

## Root Cause

The spatial join logic in `update_zoning_districts()` is **correct and working**, but:

1. **The method may not have been run** since businesses were added to the cache
2. **The UPDATE query works** when tested directly (confirmed by `test_zoning_update_query.py`)
3. **Spatial matching works** - `ST_Contains` correctly identifies that 2139 Polk Street is inside NCD-POLK polygon

## Diagnostic Results

Running `diagnose_zoning_join.py` on "2139 POLK" showed:
- ✅ 10 business records found at this address
- ✅ All have valid coordinates: `lat=37.79649, lon=-122.422185`
- ✅ `ST_Contains` query successfully finds matching polygon: **NCD-POLK**
- ⚠️ All have `zoning_district = NULL` (should be NCD-POLK)

## Solution

### 1. Improved UPDATE Query

The UPDATE query in `update_zoning_districts()` has been improved to:
- Add check for empty strings (`z.zoning != ''`) in addition to NULL checks
- Use `DISTINCT` to handle edge cases where multiple polygons might overlap
- Keep the efficient batch processing structure

**Location**: `ai/tools/simple_business_cache.py` lines 1474-1501

### 2. Manual Update Script

Created `update_zoning_now.py` to manually trigger the zoning update:

```bash
source venv/bin/activate
python update_zoning_now.py
```

This will:
- Update all businesses with valid coordinates
- Match them to zoning polygons using PostGIS spatial functions
- Process in batches of 5000 to avoid timeouts

### 3. Diagnostic Tools

Created two diagnostic scripts:

**`diagnose_zoning_join.py`**: Tests specific addresses
```bash
python diagnose_zoning_join.py "2139 POLK"
```

**`test_zoning_update_query.py`**: Tests the actual UPDATE query on a single record

## How the Spatial Join Works

The join uses PostGIS spatial functions:

1. **Business coordinates**: Stored in `location_lat` and `location_lon` columns
2. **Zoning polygons**: Stored in `zoning_polygons_cache.geometry` (PostGIS MULTIPOLYGON, SRID 4326)
3. **Spatial matching**: Uses `ST_Contains(polygon, point)` to check if point is inside polygon

```sql
ST_Contains(
    z.geometry,
    ST_SetSRID(ST_MakePoint(b.location_lon, b.location_lat), 4326)
)
```

**Note**: PostGIS `ST_MakePoint` takes (longitude, latitude) in that order.

## When to Run Update

Run `update_zoning_districts()` when:
- New businesses are added to the cache
- Zoning polygon data is refreshed
- You notice businesses with `zoning_district = NULL` that should have values

The method is automatically called by:
- `refresh_cache()` - Full cache refresh
- `refresh_zoning_only()` - Zoning-only update

## API Endpoint

You can trigger a zoning-only update via the API:

```
GET /api/vacancy/refresh-zoning-only?limit=<optional_limit>
```

## Verification

After running the update, verify results:

```sql
-- Check how many businesses now have zoning
SELECT COUNT(*) FROM business_registrations_cache WHERE zoning_district IS NOT NULL;

-- Check a specific address
SELECT full_business_address, zoning_district, location_lat, location_lon
FROM business_registrations_cache
WHERE UPPER(full_business_address) LIKE '%2139 POLK%';
```

## Next Steps

1. **Run the update**: Execute `update_zoning_now.py` or use the API endpoint
2. **Verify**: Check that 2139 Polk Street and other addresses now have zoning districts
3. **Monitor**: Ensure `update_zoning_districts()` is called after future cache refreshes


