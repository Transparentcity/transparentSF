"""
Google Cloud Storage logging utility for sessions and evaluations.

This module provides a unified interface for logging session data and evaluation
results to Google Cloud Storage, with automatic fallback to local storage.
"""

import os
import json
import logging
from typing import Optional, Dict, Any, Union
from pathlib import Path
from datetime import datetime
import tempfile

try:
    from google.cloud import storage
    from google.auth.exceptions import DefaultCredentialsError
    GCS_AVAILABLE = True
except ImportError:
    GCS_AVAILABLE = False
    storage = None

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


class GCSLogger:
    """
    Manages logging of sessions and evaluations to Google Cloud Storage.
    
    Provides a unified interface for storing session logs and evaluation results
    to GCS, with automatic fallback to local storage when GCS is not available.
    """
    
    def __init__(self, bucket_name: Optional[str] = None, project_id: Optional[str] = None):
        """
        Initialize the GCS logger.
        
        Args:
            bucket_name: GCS bucket name (defaults to env var GCS_BUCKET_NAME)
            project_id: Google Cloud project ID (defaults to env var GCP_PROJECT_ID)
        """
        self.bucket_name = bucket_name or os.getenv('GCS_BUCKET_NAME')
        self.project_id = project_id or os.getenv('GCP_PROJECT_ID')
        self.client = None
        self.bucket = None
        self.gcs_enabled = False
        
        # Check if GCS logging is enabled
        self.gcs_logging_enabled = os.getenv('ENABLE_GCS_LOGGING', 'true').lower() == 'true'
        self.log_retention_days = int(os.getenv('LOG_RETENTION_DAYS', '30'))
        
        # Local fallback directories
        self.local_logs_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
        self.local_sessions_dir = os.path.join(self.local_logs_dir, 'sessions')
        self.local_evals_dir = os.path.join(self.local_logs_dir, 'evals')
        
        # Ensure local directories exist
        os.makedirs(self.local_sessions_dir, exist_ok=True)
        os.makedirs(self.local_evals_dir, exist_ok=True)
        
        if self.gcs_logging_enabled:
            self._initialize_gcs()
        else:
            logger.info("GCS logging disabled via configuration")
    
    def _initialize_gcs(self):
        """Initialize Google Cloud Storage client and bucket."""
        if not GCS_AVAILABLE:
            logger.warning("Google Cloud Storage libraries not available. Using local logging only.")
            return
        
        if not self.bucket_name:
            logger.warning("GCS_BUCKET_NAME not set. Using local logging only.")
            return
        
        try:
            # Initialize the client
            self.client = storage.Client(project=self.project_id)
            self.bucket = self.client.bucket(self.bucket_name)
            
            # Test bucket access
            if self.bucket.exists():
                self.gcs_enabled = True
                logger.info(f"GCS logging initialized successfully with bucket: {self.bucket_name}")
            else:
                logger.error(f"GCS bucket '{self.bucket_name}' does not exist or is not accessible")
                
        except DefaultCredentialsError:
            logger.warning("Google Cloud credentials not found. Using local logging only.")
        except Exception as e:
            logger.error(f"Failed to initialize GCS logging: {e}. Using local logging only.")
    
    def _get_gcs_path(self, log_type: str, filename: str, year: Optional[str] = None, month: Optional[str] = None) -> str:
        """
        Generate GCS path for log files with organized structure.
        
        Args:
            log_type: Type of log ('sessions' or 'evals')
            filename: The filename to store
            year: Year for organization (defaults to current year)
            month: Month for organization (defaults to current month)
        
        Returns:
            GCS path string
        """
        now = datetime.now()
        year = year or str(now.year)
        month = month or f"{now.month:02d}"
        
        return f"logs/{log_type}/{year}/{month}/{filename}"
    
    def log_session(self, session_data: Dict[str, Any], session_id: str) -> bool:
        """
        Log session data to GCS and local storage.
        
        Args:
            session_data: The session data to log
            session_id: Unique session identifier
        
        Returns:
            True if logging was successful, False otherwise
        """
        filename = f"{session_id}.json"
        success = True
        
        # Always log locally first
        local_path = os.path.join(self.local_sessions_dir, filename)
        try:
            with open(local_path, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, indent=2, ensure_ascii=False, default=str)
            logger.debug(f"Session logged locally: {local_path}")
        except Exception as e:
            logger.error(f"Failed to log session locally: {e}")
            success = False
        
        # Log to GCS if available
        if self.gcs_enabled:
            try:
                gcs_path = self._get_gcs_path('sessions', filename)
                blob = self.bucket.blob(gcs_path)
                
                # Upload the session data
                blob.upload_from_string(
                    json.dumps(session_data, indent=2, ensure_ascii=False, default=str),
                    content_type='application/json'
                )
                
                logger.info(f"Session logged to GCS: {gcs_path}")
                
            except Exception as e:
                logger.error(f"Failed to log session to GCS: {e}")
                # Don't mark as failed since local logging succeeded
        
        return success
    
    def log_evaluation(self, eval_data: Dict[str, Any], eval_id: Optional[str] = None) -> bool:
        """
        Log evaluation data to GCS and local storage.
        
        Args:
            eval_data: The evaluation data to log
            eval_id: Optional evaluation identifier (generates timestamp-based ID if not provided)
        
        Returns:
            True if logging was successful, False otherwise
        """
        if not eval_id:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]
            eval_id = f"eval_{timestamp}"
        
        filename = f"{eval_id}.json"
        success = True
        
        # Always log locally first
        local_path = os.path.join(self.local_evals_dir, filename)
        try:
            with open(local_path, 'w', encoding='utf-8') as f:
                json.dump(eval_data, f, indent=2, ensure_ascii=False, default=str)
            logger.debug(f"Evaluation logged locally: {local_path}")
        except Exception as e:
            logger.error(f"Failed to log evaluation locally: {e}")
            success = False
        
        # Log to GCS if available
        if self.gcs_enabled:
            try:
                gcs_path = self._get_gcs_path('evals', filename)
                blob = self.bucket.blob(gcs_path)
                
                # Upload the evaluation data
                blob.upload_from_string(
                    json.dumps(eval_data, indent=2, ensure_ascii=False, default=str),
                    content_type='application/json'
                )
                
                logger.info(f"Evaluation logged to GCS: {gcs_path}")
                
            except Exception as e:
                logger.error(f"Failed to log evaluation to GCS: {e}")
                # Don't mark as failed since local logging succeeded
        
        return success
    
    def retrieve_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve session data from GCS or local storage.
        
        Args:
            session_id: The session identifier
        
        Returns:
            Session data dictionary or None if not found
        """
        filename = f"{session_id}.json"
        
        # Try GCS first if available
        if self.gcs_enabled:
            try:
                gcs_path = self._get_gcs_path('sessions', filename)
                blob = self.bucket.blob(gcs_path)
                
                if blob.exists():
                    content = blob.download_as_text()
                    return json.loads(content)
                    
            except Exception as e:
                logger.warning(f"Failed to retrieve session from GCS: {e}")
        
        # Fallback to local storage
        local_path = os.path.join(self.local_sessions_dir, filename)
        try:
            if os.path.exists(local_path):
                with open(local_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Failed to retrieve session from local storage: {e}")
        
        return None
    
    def retrieve_evaluation(self, eval_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve evaluation data from GCS or local storage.
        
        Args:
            eval_id: The evaluation identifier
        
        Returns:
            Evaluation data dictionary or None if not found
        """
        filename = f"{eval_id}.json"
        
        # Try GCS first if available
        if self.gcs_enabled:
            try:
                gcs_path = self._get_gcs_path('evals', filename)
                blob = self.bucket.blob(gcs_path)
                
                if blob.exists():
                    content = blob.download_as_text()
                    return json.loads(content)
                    
            except Exception as e:
                logger.warning(f"Failed to retrieve evaluation from GCS: {e}")
        
        # Fallback to local storage
        local_path = os.path.join(self.local_evals_dir, filename)
        try:
            if os.path.exists(local_path):
                with open(local_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Failed to retrieve evaluation from local storage: {e}")
        
        return None
    
    def list_sessions(self, year: Optional[str] = None, month: Optional[str] = None) -> list:
        """
        List available sessions from GCS or local storage.
        
        Args:
            year: Year to filter by (defaults to current year)
            month: Month to filter by (defaults to current month)
        
        Returns:
            List of session IDs
        """
        sessions = []
        
        # Try GCS first if available
        if self.gcs_enabled:
            try:
                now = datetime.now()
                year = year or str(now.year)
                month = month or f"{now.month:02d}"
                
                prefix = f"logs/sessions/{year}/{month}/"
                blobs = self.bucket.list_blobs(prefix=prefix)
                
                for blob in blobs:
                    if blob.name.endswith('.json'):
                        # Extract session ID from filename
                        filename = os.path.basename(blob.name)
                        session_id = filename.replace('.json', '')
                        sessions.append(session_id)
                        
            except Exception as e:
                logger.warning(f"Failed to list sessions from GCS: {e}")
        
        # If no GCS results, fallback to local storage
        if not sessions:
            try:
                for filename in os.listdir(self.local_sessions_dir):
                    if filename.endswith('.json'):
                        session_id = filename.replace('.json', '')
                        sessions.append(session_id)
            except Exception as e:
                logger.error(f"Failed to list sessions from local storage: {e}")
        
        return sorted(sessions)
    
    def list_evaluations(self, year: Optional[str] = None, month: Optional[str] = None) -> list:
        """
        List available evaluations from GCS or local storage.
        
        Args:
            year: Year to filter by (defaults to current year)
            month: Month to filter by (defaults to current month)
        
        Returns:
            List of evaluation IDs
        """
        evaluations = []
        
        # Try GCS first if available
        if self.gcs_enabled:
            try:
                now = datetime.now()
                year = year or str(now.year)
                month = month or f"{now.month:02d}"
                
                prefix = f"logs/evals/{year}/{month}/"
                blobs = self.bucket.list_blobs(prefix=prefix)
                
                for blob in blobs:
                    if blob.name.endswith('.json'):
                        # Extract evaluation ID from filename
                        filename = os.path.basename(blob.name)
                        eval_id = filename.replace('.json', '')
                        evaluations.append(eval_id)
                        
            except Exception as e:
                logger.warning(f"Failed to list evaluations from GCS: {e}")
        
        # If no GCS results, fallback to local storage
        if not evaluations:
            try:
                for filename in os.listdir(self.local_evals_dir):
                    if filename.endswith('.json'):
                        eval_id = filename.replace('.json', '')
                        evaluations.append(eval_id)
            except Exception as e:
                logger.error(f"Failed to list evaluations from local storage: {e}")
        
        return sorted(evaluations)
    
    def cleanup_old_logs(self, days_to_keep: Optional[int] = None) -> Dict[str, int]:
        """
        Clean up old log files from local storage.
        
        Args:
            days_to_keep: Number of days to keep logs (defaults to configured value)
        
        Returns:
            Dictionary with cleanup statistics
        """
        if days_to_keep is None:
            days_to_keep = self.log_retention_days
            
        stats = {"sessions_removed": 0, "evals_removed": 0}
        cutoff_time = datetime.now().timestamp() - (days_to_keep * 24 * 60 * 60)
        
        # Clean up session logs
        try:
            for filename in os.listdir(self.local_sessions_dir):
                filepath = os.path.join(self.local_sessions_dir, filename)
                if os.path.isfile(filepath) and os.path.getmtime(filepath) < cutoff_time:
                    os.remove(filepath)
                    stats["sessions_removed"] += 1
        except Exception as e:
            logger.error(f"Failed to cleanup session logs: {e}")
        
        # Clean up evaluation logs
        try:
            for filename in os.listdir(self.local_evals_dir):
                filepath = os.path.join(self.local_evals_dir, filename)
                if os.path.isfile(filepath) and os.path.getmtime(filepath) < cutoff_time:
                    os.remove(filepath)
                    stats["evals_removed"] += 1
        except Exception as e:
            logger.error(f"Failed to cleanup evaluation logs: {e}")
        
        logger.info(f"Cleanup completed: {stats}")
        return stats


# Global instance for easy access
_gcs_logger = None

def get_gcs_logger() -> GCSLogger:
    """Get the global GCS logger instance."""
    global _gcs_logger
    if _gcs_logger is None:
        _gcs_logger = GCSLogger()
    return _gcs_logger
