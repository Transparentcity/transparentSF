# GCS Logging for TransparentSF

This document describes the Google Cloud Storage (GCS) logging functionality for sessions and evaluations in TransparentSF.

## Overview

The GCS logging system provides a unified interface for storing session logs and evaluation results in Google Cloud Storage, with automatic fallback to local storage when GCS is not available. This ensures that all interactions and evaluations are preserved for analysis and debugging.

## Features

### Dual Storage Strategy
- **Primary**: Google Cloud Storage for long-term preservation and analysis
- **Fallback**: Local storage for immediate access and reliability
- **Automatic failover**: If GCS is unavailable, logs are still stored locally

### Organized Storage Structure
```
GCS Bucket/
├── logs/
│   ├── sessions/
│   │   ├── 2024/
│   │   │   ├── 01/
│   │   │   │   ├── session-id-1.json
│   │   │   │   └── session-id-2.json
│   │   │   └── 02/
│   │   └── 2025/
│   └── evals/
│       ├── 2024/
│       │   ├── 01/
│       │   │   ├── eval-id-1.json
│       │   │   └── eval-id-2.json
│       │   └── 02/
│       └── 2025/
```

### Session Logging
- Complete conversation history
- Tool call details and results
- Performance metrics
- Error tracking and analysis
- Model configuration and metadata

### Evaluation Logging
- Evaluation test cases and results
- Model performance metrics
- Success/failure analysis
- Execution traces and timing
- Comparison data across models

## Configuration

### Environment Variables

Add these to your `.env` file:

```bash
# Google Cloud Configuration
GCP_PROJECT_ID=your-project-id
GCS_BUCKET_NAME=your-bucket-name
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json

# GCS Logging Configuration
ENABLE_GCS_LOGGING=true
LOG_RETENTION_DAYS=30
```

### Configuration Options

- `ENABLE_GCS_LOGGING`: Enable/disable GCS logging (default: true)
- `LOG_RETENTION_DAYS`: Days to keep logs in local storage (default: 30)
- `GCS_BUCKET_NAME`: GCS bucket for log storage
- `GCP_PROJECT_ID`: Google Cloud project ID

## Usage

### Automatic Logging

Sessions and evaluations are automatically logged to GCS when:

1. **Sessions**: A user interacts with the explainer agent
2. **Evaluations**: Test cases are run via the evaluation system

No code changes are required - logging happens transparently.

### Manual Log Management

Use the `gcs_log_manager.py` script for log management:

```bash
# List all logs
python ai/tools/gcs_log_manager.py list

# List only sessions
python ai/tools/gcs_log_manager.py list --type sessions

# Retrieve a specific session
python ai/tools/gcs_log_manager.py retrieve session-id-123 --type session

# Clean up old local logs
python ai/tools/gcs_log_manager.py cleanup --days 7

# Migrate local logs to GCS
python ai/tools/gcs_log_manager.py migrate --type both

# Analyze logs for insights
python ai/tools/gcs_log_manager.py analyze --type sessions --days 30
```

### Programmatic Access

```python
from ai.tools.gcs_logger import get_gcs_logger

# Get the GCS logger instance
gcs_logger = get_gcs_logger()

# Log a session
session_data = {...}
success = gcs_logger.log_session(session_data, "session-id")

# Log an evaluation
eval_data = {...}
success = gcs_logger.log_evaluation(eval_data, "eval-id")

# Retrieve logs
session = gcs_logger.retrieve_session("session-id")
evaluation = gcs_logger.retrieve_evaluation("eval-id")

# List available logs
sessions = gcs_logger.list_sessions()
evaluations = gcs_logger.list_evaluations()
```

## Testing

Run the test suite to verify GCS logging functionality:

```bash
python ai/tools/test_gcs_logging.py
```

The test suite will:
- Test session logging and retrieval
- Test evaluation logging and retrieval
- Test log listing functionality
- Verify configuration settings
- Clean up test data

## Log Structure

### Session Log Format

```json
{
  "session_id": "uuid-string",
  "timestamp": "2024-01-15T10:30:00Z",
  "start_time": "2024-01-15T10:30:00Z",
  "end_time": "2024-01-15T10:30:05Z",
  "model": "gpt-4",
  "model_config": {...},
  "user_input": "What is the crime rate in SF?",
  "conversation_history": [...],
  "tool_calls": [
    {
      "tool_name": "query_metrics",
      "arguments": {...},
      "result": {...},
      "success": true,
      "execution_time_ms": 150,
      "timestamp": "2024-01-15T10:30:01Z"
    }
  ],
  "success": true,
  "total_execution_time_ms": 5000,
  "error_summary": null,
  "intermediate_responses": [...]
}
```

### Evaluation Log Format

```json
{
  "timestamp": "2024-01-15T10:30:00Z",
  "type": "eval_run",
  "eval_id": "eval-123",
  "eval_name": "Crime Rate Query Test",
  "model_name": "gpt-4",
  "prompt": "What is the crime rate in SF?",
  "execution_result": {
    "success": true,
    "response": "The crime rate in SF is...",
    "tool_calls": [...],
    "success_score": 0.95,
    "execution_trace": [...]
  },
  "execution_time_seconds": 2.5,
  "result_id": "result-456",
  "database_save_status": "success"
}
```

## Benefits

### For Development
- **Debugging**: Complete session traces for troubleshooting
- **Performance Analysis**: Execution times and tool call patterns
- **Error Tracking**: Detailed error information and categorization

### For Analysis
- **Model Comparison**: Performance across different models
- **Usage Patterns**: Common queries and tool usage
- **Success Rates**: Overall system performance metrics

### For Operations
- **Long-term Storage**: Logs preserved in cloud storage
- **Scalability**: No local storage limitations
- **Backup**: Automatic redundancy with local fallback

## Troubleshooting

### Common Issues

1. **GCS Not Available**
   - Check Google Cloud credentials
   - Verify bucket exists and is accessible
   - Check network connectivity
   - Logs will still be stored locally

2. **Permission Errors**
   - Ensure service account has Storage Object Admin role
   - Verify bucket permissions
   - Check GOOGLE_APPLICATION_CREDENTIALS path

3. **Large Log Files**
   - Logs are automatically organized by date
   - Use cleanup commands to manage local storage
   - GCS provides unlimited storage

### Debug Mode

Enable debug logging to see detailed GCS operations:

```bash
export LOG_LEVEL=DEBUG
python ai/tools/test_gcs_logging.py
```

## Integration Points

The GCS logging system integrates with:

- **SessionLogger**: Automatic session logging in explainer agent
- **Evaluation System**: Automatic evaluation result logging
- **EvalRunner**: Comprehensive evaluation logging with database integration
- **Main Application**: Transparent integration with existing logging

## Future Enhancements

- **Log Analytics**: Built-in analysis and reporting tools
- **Alerting**: Notifications for error patterns or performance issues
- **Compression**: Automatic log compression for storage efficiency
- **Retention Policies**: Configurable GCS lifecycle policies
- **Search**: Full-text search across log contents
