# Business Cache System

This system pre-processes all business data from DataSF API and stores it in the local PostgreSQL database for ultra-fast searching and display.

## Why Use Caching?

- **Speed**: Searches return in milliseconds instead of 30+ seconds
- **Reliability**: No dependency on DataSF API availability
- **Efficiency**: Reduces API calls and server load
- **Consistency**: All users see the same data until refresh

## How It Works

1. **Pre-processing**: Fetches all business data from DataSF API once
2. **Processing**: Groups businesses by building, determines status, normalizes addresses
3. **Storage**: Stores processed data in PostgreSQL with proper indexes
4. **Serving**: Frontend queries local database instead of API

## Database Schema

### `business_cache` Table
- Stores processed business data with all the complex logic pre-computed
- Indexed on business name, address, district, corridor for fast searching
- Includes JSON field for detailed address data

### `commercial_tax_cache` Table
- Stores commercial tax filing data for matching
- Indexed on BAN and address for fast lookups

## Usage

### Initial Setup
```bash
# Refresh the cache with all data (first time)
python refresh_business_cache.py

# Or with a limit for testing
python refresh_business_cache.py --limit 1000
```

### Regular Maintenance
```bash
# Check cache statistics
python refresh_business_cache.py --stats

# Refresh cache (run weekly or as needed)
python refresh_business_cache.py
```

### API Endpoints

- `GET /api/vacancy/cached` - Get cached business data (fast)
- `POST /api/vacancy/refresh-cache` - Refresh the cache
- `GET /api/vacancy/cache-stats` - Get cache statistics

## Performance

**Before (API-based):**
- Search time: 30+ seconds
- Data processing: Real-time on every request
- API dependency: High

**After (Cache-based):**
- Search time: < 1 second
- Data processing: Pre-computed once
- API dependency: None (except for refresh)

## Frontend Changes

The frontend now uses `/api/vacancy/cached` by default instead of `/api/vacancy/data`. This provides:

- Instant search results
- Fast filtering and sorting
- Reliable performance
- Better user experience

## Cache Refresh Strategy

### When to Refresh
- Weekly (recommended)
- After major DataSF updates
- When data seems stale
- Before important presentations

### How to Refresh
```bash
# Full refresh
python refresh_business_cache.py

# Or via API
curl -X POST http://localhost:8000/api/vacancy/refresh-cache
```

## Monitoring

Check cache health:
```bash
python refresh_business_cache.py --stats
```

This shows:
- Number of business records
- Open vs closed businesses
- Tax filing records
- Last update time

## Troubleshooting

### Cache Empty
```bash
python refresh_business_cache.py --limit 1000
```

### Slow Performance
Check database indexes:
```sql
SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'business_cache';
```

### Data Stale
Refresh the cache:
```bash
python refresh_business_cache.py
```

## File Structure

```
ai/tools/business_cache_processor.py  # Main cache processor
ai/routes/vacancy_analysis.py         # API endpoints
refresh_business_cache.py             # Command-line script
BUSINESS_CACHE_README.md              # This file
```

## Benefits

1. **Speed**: 30x faster searches
2. **Reliability**: No API timeouts
3. **Scalability**: Handles more concurrent users
4. **Cost**: Reduces API usage
5. **User Experience**: Instant results
