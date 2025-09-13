# Google Cloud Storage Integration for TransparentSF

This document describes the Google Cloud Storage (GCS) integration for TransparentSF, which provides cloud-based storage for all output files with automatic fallback to local storage.

## Overview

The GCS integration consists of several components:

1. **GCS Storage Manager** (`tools/gcs_storage.py`) - Core GCS operations
2. **Output Manager** (`tools/output_manager.py`) - Unified interface for all file operations
3. **Migration Script** (`tools/migrate_to_gcs.py`) - Migrate existing files to GCS
4. **Setup Script** (`tools/setup_gcs.py`) - Configure and test GCS integration

## Features

- **Automatic Fallback**: Uses GCS when available, falls back to local storage
- **Unified Interface**: Single API for all file operations regardless of storage backend
- **Metadata Support**: Automatic metadata injection for tracking and debugging
- **Health Monitoring**: Built-in health checks and connectivity testing
- **Migration Support**: Easy migration of existing files to cloud storage

## File Organization

Files are organized in GCS with the following structure:

```
transparentsf/
├── dashboard/
│   ├── 0/          # Citywide metrics
│   │   ├── 1.json
│   │   ├── 2.json
│   │   └── ...
│   ├── 1/          # District 1 metrics
│   └── ...
├── monthly/
│   ├── 0/
│   │   ├── 1.md
│   │   └── ...
│   └── ...
├── annual/
│   ├── 0/
│   │   ├── 1.md
│   │   └── ...
│   └── ...
├── weekly/
│   ├── 0/
│   │   ├── 1.md
│   │   └── ...
│   └── ...
├── reports/
│   ├── report1.html
│   ├── report2.txt
│   └── ...
└── notes/
    ├── combined_notes.txt
    └── ...
```

## Setup Instructions

### 1. Install Dependencies

```bash
# Activate your virtual environment
source venv/bin/activate

# Install GCS dependencies
pip install google-cloud-storage google-auth google-auth-oauthlib google-auth-httplib2
```

### 2. Set Up Google Cloud Credentials

#### Option A: Service Account (Recommended for Production)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Navigate to IAM & Admin > Service Accounts
3. Create a new service account
4. Download the JSON key file
5. Set the environment variable:
   ```bash
   export GOOGLE_APPLICATION_CREDENTIALS="/path/to/your/service-account-key.json"
   ```

#### Option B: Default Credentials (For Development)

```bash
# Install Google Cloud CLI
# https://cloud.google.com/sdk/docs/install

# Authenticate
gcloud auth application-default login
```

### 3. Create GCS Bucket

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Navigate to Cloud Storage > Buckets
3. Create a new bucket
4. Note the bucket name for configuration

### 4. Configure Environment Variables

```bash
# Copy the example configuration
cp ai/gcs_config_example.env ai/.env

# Edit the .env file with your values
nano ai/.env
```

Required variables:
- `GCP_PROJECT_ID`: Your Google Cloud project ID
- `GCS_BUCKET_NAME`: Your GCS bucket name
- `GOOGLE_APPLICATION_CREDENTIALS`: Path to service account key (if using service account)

### 5. Test the Setup

```bash
# Run the setup test
python ai/tools/setup_gcs.py --test
```

### 6. Migrate Existing Files

```bash
# Test migration (dry run)
python ai/tools/migrate_to_gcs.py --dry-run

# Run actual migration
python ai/tools/migrate_to_gcs.py

# Verify migration
python ai/tools/migrate_to_gcs.py --verify
```

## Usage

### Basic Usage

```python
from ai.tools.output_manager import get_output_manager

# Get the output manager
output_manager = get_output_manager()

# Store a dashboard metric
data = {"metric": "value", "timestamp": "2024-01-01"}
success = output_manager.store_dashboard_metric(data, district="0", metric_id="1")

# Retrieve a dashboard metric
data = output_manager.retrieve_dashboard_metric(district="0", metric_id="1")

# Store an analysis file
content = "# Analysis\nThis is the analysis content."
success = output_manager.store_analysis_file(content, "monthly", "0", "1")

# Store a report
html_content = "<html><body>Report content</body></html>"
success = output_manager.store_report(html_content, "report.html")
```

### Convenience Functions

```python
from ai.tools.output_manager import (
    store_dashboard_metric,
    retrieve_dashboard_metric,
    store_analysis_file,
    retrieve_analysis_file,
    store_report,
    retrieve_report,
    store_notes,
    retrieve_notes
)

# Use convenience functions
store_dashboard_metric(data, "0", "1")
data = retrieve_dashboard_metric("0", "1")
```

### Health Monitoring

```python
from ai.tools.output_manager import get_output_manager

output_manager = get_output_manager()

# Get storage information
storage_info = output_manager.get_storage_info()
print(f"GCS enabled: {storage_info['gcs_enabled']}")

# Run health check
health = output_manager.health_check()
print(f"GCS accessible: {health['gcs_accessible']}")
print(f"Local accessible: {health['local_accessible']}")
print(f"File operations: {health['test_file_success']}")
```

## Integration with Existing Code

The GCS integration is designed to be backward compatible. Existing code can be gradually migrated:

### Example: Updating notes_manager.py

```python
# Old code
def save_notes_to_file(notes_text, filename="combined_notes.txt"):
    script_dir = Path(__file__).parent.parent
    notes_dir = script_dir / 'output' / 'notes'
    # ... local file operations

# New code with GCS support
def save_notes_to_file(notes_text, filename="combined_notes.txt"):
    try:
        from .output_manager import get_output_manager
        output_manager = get_output_manager()
        return output_manager.store_notes(notes_text, filename)
    except ImportError:
        # Fallback to local storage
        # ... original local file operations
```

## Configuration Options

### Environment Variables

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `GCP_PROJECT_ID` | Google Cloud project ID | Yes | - |
| `GCS_BUCKET_NAME` | GCS bucket name | Yes | - |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to service account key | No | Uses default credentials |
| `ENABLE_GCS` | Enable GCS (true/false) | No | true |
| `LOCAL_OUTPUT_DIR` | Local fallback directory | No | output |
| `LOG_LEVEL` | Logging level | No | INFO |

### Storage Manager Options

```python
from ai.tools.gcs_storage import GCSStorageManager

# Custom configuration
storage_manager = GCSStorageManager(
    bucket_name="my-custom-bucket",
    project_id="my-project-id"
)
```

## Troubleshooting

### Common Issues

1. **Credentials Error**
   ```
   DefaultCredentialsError: Could not automatically determine credentials
   ```
   - Solution: Set `GOOGLE_APPLICATION_CREDENTIALS` or run `gcloud auth application-default login`

2. **Bucket Not Found**
   ```
   Bucket 'my-bucket' does not exist
   ```
   - Solution: Create the bucket in Google Cloud Console or check the bucket name

3. **Permission Denied**
   ```
   Permission denied on bucket
   ```
   - Solution: Ensure your service account has Storage Admin or Storage Object Admin role

4. **Import Errors**
   ```
   ImportError: No module named 'google.cloud'
   ```
   - Solution: Install GCS dependencies: `pip install google-cloud-storage`

### Debug Mode

Enable debug logging to see detailed information:

```bash
export LOG_LEVEL=DEBUG
python ai/tools/setup_gcs.py --test
```

### Health Check

Run a comprehensive health check:

```python
from ai.tools.output_manager import get_output_manager

output_manager = get_output_manager()
health = output_manager.health_check()
print(json.dumps(health, indent=2))
```

## Migration Guide

### Step 1: Test Current Setup

```bash
python ai/tools/setup_gcs.py --test
```

### Step 2: Backup Existing Files

```bash
# Create backup of current output directory
cp -r ai/output ai/output_backup_$(date +%Y%m%d)
```

### Step 3: Test Migration

```bash
# Dry run to see what would be migrated
python ai/tools/migrate_to_gcs.py --dry-run
```

### Step 4: Run Migration

```bash
# Actual migration
python ai/tools/migrate_to_gcs.py
```

### Step 5: Verify Migration

```bash
# Verify files were migrated correctly
python ai/tools/migrate_to_gcs.py --verify
```

### Step 6: Update Code

Gradually update your code to use the new output manager:

```python
# Replace direct file operations with output manager calls
# Old: open(file_path, 'w').write(content)
# New: output_manager.store_file(content, file_type, ...)
```

## Performance Considerations

- **Batch Operations**: For large migrations, consider batching operations
- **Concurrent Uploads**: GCS supports concurrent uploads for better performance
- **Local Caching**: Consider implementing local caching for frequently accessed files
- **Compression**: Large files can be compressed before upload

## Security Best Practices

1. **Service Account Permissions**: Use least-privilege principle
2. **Bucket Access**: Restrict bucket access to necessary users
3. **Credentials**: Never commit service account keys to version control
4. **Environment Variables**: Use environment variables for sensitive configuration
5. **Audit Logging**: Enable audit logs for GCS operations

## Monitoring and Alerting

Consider setting up monitoring for:

- GCS API quotas and limits
- Storage costs
- Failed operations
- Health check failures

## Support

For issues or questions:

1. Check the troubleshooting section above
2. Run the setup test: `python ai/tools/setup_gcs.py --test`
3. Check logs for detailed error messages
4. Verify Google Cloud Console for bucket and permissions

## Future Enhancements

Potential future improvements:

- **CDN Integration**: Use Cloud CDN for faster file access
- **Versioning**: Enable object versioning for file history
- **Lifecycle Management**: Automatic cleanup of old files
- **Encryption**: Customer-managed encryption keys
- **Multi-Region**: Cross-region replication for disaster recovery
