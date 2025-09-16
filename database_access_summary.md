# Database Access Analysis for TransparentSF

## Overview
This document provides a comprehensive analysis of all endpoints in the TransparentSF application that access the database, including connection methods, tables accessed, and access patterns.

## Database Connection Methods

### 1. Pooled Connections (Recommended)
- **Method**: `execute_with_connection()` from `ai.tools.db_utils`
- **Usage**: Most modern endpoints use this method
- **Benefits**: Connection pooling, retry logic, automatic connection management
- **Examples**: Charts routes, map generator, writeups

### 2. Direct Connections
- **Method**: `get_postgres_connection()` from `ai.tools.db_utils`
- **Usage**: Legacy endpoints and administrative functions
- **Benefits**: Direct control over connection lifecycle
- **Examples**: Database admin routes, some chart routes

### 3. No Database Access
- **Usage**: File system operations, static content, agent sessions
- **Examples**: Session logs, eval logs, conversation routes

## Database Tables Accessed

### Core Tables
1. **metrics** - Metric definitions and configurations
2. **time_series_metadata** - Chart metadata and configuration
3. **time_series_data** - Individual data points for charts
4. **anomalies** - Anomaly detection results
5. **maps** - Map configurations and data

### Administrative Tables
6. **reports** - Monthly report records
7. **monthly_reporting** - Monthly reporting data
8. **datasets** - Dataset metadata and URLs
9. **cities** - City configuration

### Evaluation Tables
10. **eval_groups** - Evaluation group definitions
11. **evals** - Individual evaluation definitions
12. **eval_results** - Evaluation execution results

### Writeup Tables
13. **writeups** - Writeup definitions and status
14. **writeup_steps** - Writeup execution steps
15. **writeup_responses** - Writeup execution responses

### System Tables
16. **information_schema.tables** - PostgreSQL system catalog
17. **pg_database_size** - PostgreSQL system function

## Endpoint Categories

### 1. Chart Management (8 endpoints)
- **Primary Tables**: time_series_metadata, time_series_data
- **Connection Method**: Mostly pooled connections
- **Access Pattern**: Read-heavy with some metadata updates

### 2. Database Administration (6 endpoints)
- **Primary Tables**: All tables (backup/restore), system tables
- **Connection Method**: Direct connections
- **Access Pattern**: Administrative operations

### 3. Datawrapper Integration (6 endpoints)
- **Primary Tables**: time_series_metadata, anomalies
- **Connection Method**: Direct connections
- **Access Pattern**: Read and update metadata

### 4. Evaluation System (15 endpoints)
- **Primary Tables**: eval_groups, evals, eval_results
- **Connection Method**: Pooled connections via eval_manager
- **Access Pattern**: CRUD operations on evaluation data

### 5. Explainer Agent (12 endpoints)
- **Primary Tables**: None (session-based)
- **Connection Method**: No database access
- **Access Pattern**: File system for session logs

### 6. Map Generator (5 endpoints)
- **Primary Tables**: metrics, anomalies, maps
- **Connection Method**: Mixed (pooled and direct)
- **Access Pattern**: Read metrics, create maps

### 7. Monthly Reports (4 endpoints)
- **Primary Tables**: reports
- **Connection Method**: Direct connections
- **Access Pattern**: File system operations with database metadata

### 8. Weekly Analysis (3 endpoints)
- **Primary Tables**: None
- **Connection Method**: No database access
- **Access Pattern**: File system operations

### 9. Writeups (10 endpoints)
- **Primary Tables**: writeups, writeup_steps, writeup_responses
- **Connection Method**: Pooled connections via WriteupsManager
- **Access Pattern**: Full CRUD operations

## Connection Pool Configuration

The application uses SQLAlchemy connection pooling with the following configuration:
- **Pool Size**: 10 connections maintained in pool
- **Max Overflow**: 20 additional connections when needed
- **Pool Recycle**: 1 hour
- **Pool Timeout**: 30 seconds
- **Pre-ping**: Disabled to avoid transaction conflicts

## Security Considerations

1. **Connection Security**: All connections use SSL with "prefer" mode
2. **Parameterized Queries**: All database operations use parameterized queries to prevent SQL injection
3. **Connection Timeouts**: 30-second connection timeout configured
4. **Keepalive Settings**: Configured for long-running connections

## Performance Optimizations

1. **Connection Pooling**: Reduces connection overhead by 3-5x
2. **Retry Logic**: Automatic retry for connection failures
3. **Connection Reuse**: Pooled connections are reused across requests
4. **Efficient Queries**: Most endpoints use indexed columns for filtering

## Recommendations

1. **Migrate Legacy Endpoints**: Convert remaining direct connection endpoints to use pooled connections
2. **Add Connection Monitoring**: Implement connection pool monitoring and alerting
3. **Query Optimization**: Review and optimize slow queries identified in logs
4. **Database Indexing**: Ensure proper indexes on frequently queried columns
5. **Connection Limits**: Monitor and adjust connection pool settings based on load

## File System Operations

Several endpoints operate on file system rather than database:
- Session logs: `logs/sessions/*.json`
- Eval logs: `logs/evals/*.log`
- Weekly reports: `output/weekly/*.md`
- Monthly reports: `output/reports/*.html`
- Backup files: `backups/*.sql`

These operations are logged but don't require database access analysis.
