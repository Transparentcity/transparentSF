# Database Safety Configuration

This document outlines the safeguards put in place to ensure the TransparentSF application always uses the remote database and never accidentally connects to a local database.

## Current Configuration

### Remote Database
- **Host**: 34.28.89.105
- **Port**: 5432
- **Database**: transparentsf
- **User**: transparentsf
- **Connection**: Configured via `DATABASE_URL` in `ai/.env`

### Local Database Prevention
- ✅ Local PostgreSQL services are stopped
- ✅ Application startup verification prevents localhost connections
- ✅ All database operations use centralized `db_utils` with remote connection
- ✅ Database connection verifier script available for testing

## Safeguards Implemented

### 1. Local PostgreSQL Services Disabled
```bash
# PostgreSQL services are stopped and set to not start automatically
brew services stop postgresql@14
brew services stop postgresql@16
```

### 2. Application Startup Verification
The main application (`ai/main.py`) now includes a `verify_database_config()` function that:
- Checks for `DATABASE_URL` environment variable
- Prevents startup if pointing to localhost
- Warns if not pointing to expected remote database
- Confirms connection to remote database

### 3. Database Connection Verifier
A standalone script (`ai/tools/db_connection_verifier.py`) can be run to verify:
- Environment configuration is correct
- Actual database connection works
- Connected to the right remote database

### 4. Centralized Database Access
All database operations use `ai/tools/db_utils.py` which:
- Prioritizes `DATABASE_URL` from environment
- Uses connection pooling for efficiency
- Automatically connects to remote database

## Testing Database Configuration

### Quick Verification
```bash
cd ai
python tools/db_connection_verifier.py
```

### Manual Connection Test
```bash
cd ai
python -c "
from tools.db_utils import execute_with_connection
def test(conn):
    cur = conn.cursor()
    cur.execute('SELECT inet_server_addr()')
    return cur.fetchone()[0]
result = execute_with_connection(test)
print(f'Connected to: {result}')
"
```

## Vacancies System

The vacancies system is fully configured to use the remote database:
- ✅ All queries use `execute_with_connection` from `db_utils`
- ✅ No hardcoded localhost connections
- ✅ Remote database schema is up-to-date with all required columns
- ✅ NULL value handling for supervisor_district in timeseries

## Environment Variables

The `ai/.env` file contains:
```env
DATABASE_URL=postgresql://transparentsf:0ffee68360f8c7b3a72ea6051a4d0fd0@34.28.89.105:5432/transparentsf
```

## Troubleshooting

### If you see localhost connection errors:
1. Check that `ai/.env` contains the correct `DATABASE_URL`
2. Verify no local PostgreSQL services are running: `brew services list | grep postgres`
3. Run the database verifier: `python tools/db_connection_verifier.py`

### If you need to restart local PostgreSQL (for other projects):
```bash
# Start PostgreSQL (for other projects)
brew services start postgresql@14

# Stop PostgreSQL (to prevent TransparentSF from using it)
brew services stop postgresql@14
```

## Files Modified

- `ai/main.py` - Added database verification on startup
- `ai/tools/db_connection_verifier.py` - New verification script
- `ai/tools/vacancies_loader.py` - Fixed NULL handling in timeseries
- Remote database schema - Added missing columns

## Verification Commands

```bash
# Check PostgreSQL services
brew services list | grep postgres

# Verify database connection
cd ai && python tools/db_connection_verifier.py

# Test vacancies functionality
cd ai && python -c "from tools.vacancies_loader import query_points; print(f'Found {len(query_points(limit=1))} records')"
```

This configuration ensures that the TransparentSF application will always use the remote database and never accidentally connect to a local database, preventing the schema mismatch issues that occurred previously.
