"""
Google Cloud Storage integration for TransparentSF output files.

This module provides a unified interface for storing and retrieving output files
from Google Cloud Storage, with fallback to local storage when GCS is not available.
"""

import os
import json
import logging
from typing import Optional, Dict, Any, List, Union
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


class GCSStorageManager:
    """
    Manages file storage operations with Google Cloud Storage.
    
    Provides a unified interface for storing and retrieving files from GCS,
    with automatic fallback to local storage when GCS is not available.
    """
    
    def __init__(self, bucket_name: Optional[str] = None, project_id: Optional[str] = None):
        """
        Initialize the GCS storage manager.
        
        Args:
            bucket_name: GCS bucket name (defaults to env var GCS_BUCKET_NAME)
            project_id: Google Cloud project ID (defaults to env var GCP_PROJECT_ID)
        """
        self.bucket_name = bucket_name or os.getenv('GCS_BUCKET_NAME')
        self.project_id = project_id or os.getenv('GCP_PROJECT_ID')
        self.client = None
        self.bucket = None
        self.gcs_enabled = False
        
        # Local fallback directory
        self.local_fallback_dir = os.path.join(os.path.dirname(__file__), '..', 'output')
        
        self._initialize_gcs()
    
    def _initialize_gcs(self):
        """Initialize Google Cloud Storage client and bucket."""
        if not GCS_AVAILABLE:
            logger.warning("Google Cloud Storage libraries not available. Using local storage only.")
            return
        
        if not self.bucket_name:
            logger.warning("GCS_BUCKET_NAME not set. Using local storage only.")
            return
        
        try:
            # Initialize the client
            self.client = storage.Client(project=self.project_id)
            self.bucket = self.client.bucket(self.bucket_name)
            
            # Test bucket access
            if self.bucket.exists():
                self.gcs_enabled = True
                logger.info(f"GCS initialized successfully with bucket: {self.bucket_name}")
            else:
                logger.error(f"GCS bucket '{self.bucket_name}' does not exist or is not accessible")
                
        except DefaultCredentialsError:
            logger.warning("Google Cloud credentials not found. Using local storage only.")
        except Exception as e:
            logger.error(f"Failed to initialize GCS: {e}. Using local storage only.")
    
    def _get_gcs_path(self, file_type: str, district: str = "0", metric_id: Optional[str] = None, 
                     filename: Optional[str] = None) -> str:
        """
        Generate GCS object path based on file type and parameters.
        
        Args:
            file_type: Type of file (dashboard, monthly, annual, weekly, reports, notes)
            district: District ID (defaults to "0" for citywide)
            metric_id: Metric ID for metric-specific files
            filename: Specific filename
            
        Returns:
            GCS object path
        """
        base_path = f"transparentsf/{file_type}"
        
        if file_type in ['dashboard', 'monthly', 'annual', 'weekly']:
            if metric_id:
                return f"{base_path}/{district}/{metric_id}.json"
            else:
                return f"{base_path}/{district}/"
        elif file_type == 'reports':
            if filename:
                return f"{base_path}/{filename}"
            else:
                return f"{base_path}/"
        elif file_type == 'notes':
            if filename:
                return f"{base_path}/{filename}"
            else:
                return f"{base_path}/"
        else:
            if filename:
                return f"{base_path}/{filename}"
            else:
                return f"{base_path}/"
    
    def _get_local_path(self, file_type: str, district: str = "0", metric_id: Optional[str] = None,
                       filename: Optional[str] = None) -> str:
        """
        Generate local file path based on file type and parameters.
        
        Args:
            file_type: Type of file (dashboard, monthly, annual, weekly, reports, notes)
            district: District ID (defaults to "0" for citywide)
            metric_id: Metric ID for metric-specific files
            filename: Specific filename
            
        Returns:
            Local file path
        """
        if file_type in ['dashboard', 'monthly', 'annual', 'weekly']:
            if metric_id:
                return os.path.join(self.local_fallback_dir, file_type, district, f"{metric_id}.json")
            else:
                return os.path.join(self.local_fallback_dir, file_type, district)
        elif file_type == 'reports':
            if filename:
                return os.path.join(self.local_fallback_dir, 'reports', filename)
            else:
                return os.path.join(self.local_fallback_dir, 'reports')
        elif file_type == 'notes':
            if filename:
                return os.path.join(self.local_fallback_dir, 'notes', filename)
            else:
                return os.path.join(self.local_fallback_dir, 'notes')
        else:
            if filename:
                return os.path.join(self.local_fallback_dir, file_type, filename)
            else:
                return os.path.join(self.local_fallback_dir, file_type)
    
    def store_file(self, content: Union[str, bytes, Dict], file_type: str, 
                   district: str = "0", metric_id: Optional[str] = None,
                   filename: Optional[str] = None, content_type: str = "application/json") -> bool:
        """
        Store a file in GCS or local storage.
        
        Args:
            content: File content (string, bytes, or dict for JSON)
            file_type: Type of file (dashboard, monthly, annual, weekly, reports, notes)
            district: District ID (defaults to "0" for citywide)
            metric_id: Metric ID for metric-specific files
            filename: Specific filename
            content_type: MIME type of the content
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Prepare content
            if isinstance(content, dict):
                content_str = json.dumps(content, indent=2)
                content_bytes = content_str.encode('utf-8')
            elif isinstance(content, str):
                content_bytes = content.encode('utf-8')
            else:
                content_bytes = content
            
            # Try GCS first
            if self.gcs_enabled:
                gcs_path = self._get_gcs_path(file_type, district, metric_id, filename)
                blob = self.bucket.blob(gcs_path)
                
                blob.upload_from_string(
                    content_bytes,
                    content_type=content_type
                )
                
                logger.info(f"Successfully stored file in GCS: {gcs_path}")
                return True
            
            # Fallback to local storage
            local_path = self._get_local_path(file_type, district, metric_id, filename)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            
            with open(local_path, 'wb') as f:
                f.write(content_bytes)
            
            logger.info(f"Successfully stored file locally: {local_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to store file: {e}")
            return False
    
    def retrieve_file(self, file_type: str, district: str = "0", 
                     metric_id: Optional[str] = None, filename: Optional[str] = None) -> Optional[Union[str, Dict]]:
        """
        Retrieve a file from GCS or local storage.
        
        Args:
            file_type: Type of file (dashboard, monthly, annual, weekly, reports, notes)
            district: District ID (defaults to "0" for citywide)
            metric_id: Metric ID for metric-specific files
            filename: Specific filename
            
        Returns:
            File content (string or dict for JSON) or None if not found
        """
        try:
            # Try GCS first
            if self.gcs_enabled:
                gcs_path = self._get_gcs_path(file_type, district, metric_id, filename)
                blob = self.bucket.blob(gcs_path)
                
                if blob.exists():
                    content = blob.download_as_text()
                    
                    # Try to parse as JSON if it looks like JSON
                    if content.strip().startswith('{') or content.strip().startswith('['):
                        try:
                            return json.loads(content)
                        except json.JSONDecodeError:
                            pass
                    
                    return content
            
            # Fallback to local storage
            local_path = self._get_local_path(file_type, district, metric_id, filename)
            
            if os.path.exists(local_path):
                with open(local_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Try to parse as JSON if it looks like JSON
                if content.strip().startswith('{') or content.strip().startswith('['):
                    try:
                        return json.loads(content)
                    except json.JSONDecodeError:
                        pass
                
                return content
            
            logger.warning(f"File not found: {file_type}/{district}/{metric_id or filename}")
            return None
            
        except Exception as e:
            logger.error(f"Failed to retrieve file: {e}")
            return None
    
    def list_files(self, file_type: str, district: str = "0", prefix: Optional[str] = None) -> List[str]:
        """
        List files in GCS or local storage.
        
        Args:
            file_type: Type of file (dashboard, monthly, annual, weekly, reports, notes)
            district: District ID (defaults to "0" for citywide)
            prefix: Optional prefix to filter files
            
        Returns:
            List of file paths/names
        """
        try:
            files = []
            
            # Try GCS first
            if self.gcs_enabled:
                gcs_prefix = self._get_gcs_path(file_type, district)
                if prefix:
                    gcs_prefix = f"{gcs_prefix}/{prefix}"
                
                blobs = self.bucket.list_blobs(prefix=gcs_prefix)
                for blob in blobs:
                    files.append(blob.name)
            
            # Fallback to local storage
            local_path = self._get_local_path(file_type, district)
            
            if os.path.exists(local_path):
                for root, dirs, filenames in os.walk(local_path):
                    for filename in filenames:
                        if not prefix or prefix in filename:
                            rel_path = os.path.relpath(os.path.join(root, filename), local_path)
                            files.append(rel_path)
            
            return files
            
        except Exception as e:
            logger.error(f"Failed to list files: {e}")
            return []
    
    def delete_file(self, file_type: str, district: str = "0", 
                   metric_id: Optional[str] = None, filename: Optional[str] = None) -> bool:
        """
        Delete a file from GCS or local storage.
        
        Args:
            file_type: Type of file (dashboard, monthly, annual, weekly, reports, notes)
            district: District ID (defaults to "0" for citywide)
            metric_id: Metric ID for metric-specific files
            filename: Specific filename
            
        Returns:
            True if successful, False otherwise
        """
        try:
            success = False
            
            # Try GCS first
            if self.gcs_enabled:
                gcs_path = self._get_gcs_path(file_type, district, metric_id, filename)
                blob = self.bucket.blob(gcs_path)
                
                if blob.exists():
                    blob.delete()
                    logger.info(f"Successfully deleted file from GCS: {gcs_path}")
                    success = True
            
            # Also delete from local storage if it exists
            local_path = self._get_local_path(file_type, district, metric_id, filename)
            
            if os.path.exists(local_path):
                os.remove(local_path)
                logger.info(f"Successfully deleted local file: {local_path}")
                success = True
            
            return success
            
        except Exception as e:
            logger.error(f"Failed to delete file: {e}")
            return False
    
    def get_storage_info(self) -> Dict[str, Any]:
        """
        Get information about the storage configuration.
        
        Returns:
            Dictionary with storage configuration details
        """
        return {
            "gcs_enabled": self.gcs_enabled,
            "bucket_name": self.bucket_name,
            "project_id": self.project_id,
            "local_fallback_dir": self.local_fallback_dir,
            "gcs_available": GCS_AVAILABLE
        }


# Global storage manager instance
_storage_manager = None


def get_storage_manager() -> GCSStorageManager:
    """
    Get the global storage manager instance.
    
    Returns:
        GCSStorageManager instance
    """
    global _storage_manager
    if _storage_manager is None:
        _storage_manager = GCSStorageManager()
    return _storage_manager


# Convenience functions for common operations
def store_dashboard_metric(data: Dict, district: str, metric_id: str) -> bool:
    """Store dashboard metric data."""
    return get_storage_manager().store_file(data, "dashboard", district, metric_id)


def retrieve_dashboard_metric(district: str, metric_id: str) -> Optional[Dict]:
    """Retrieve dashboard metric data."""
    return get_storage_manager().retrieve_file("dashboard", district, metric_id)


def store_analysis_file(content: str, file_type: str, district: str, metric_id: str) -> bool:
    """Store analysis file (monthly, annual, weekly)."""
    return get_storage_manager().store_file(content, file_type, district, metric_id, content_type="text/markdown")


def retrieve_analysis_file(file_type: str, district: str, metric_id: str) -> Optional[str]:
    """Retrieve analysis file (monthly, annual, weekly)."""
    return get_storage_manager().retrieve_file(file_type, district, metric_id)


def store_report(content: str, filename: str) -> bool:
    """Store report file."""
    return get_storage_manager().store_file(content, "reports", filename=filename, content_type="text/html")


def retrieve_report(filename: str) -> Optional[str]:
    """Retrieve report file."""
    return get_storage_manager().retrieve_file("reports", filename=filename)


def store_notes(content: str, filename: str = "combined_notes.txt") -> bool:
    """Store notes file."""
    return get_storage_manager().store_file(content, "notes", filename=filename, content_type="text/plain")


def retrieve_notes(filename: str = "combined_notes.txt") -> Optional[str]:
    """Retrieve notes file."""
    return get_storage_manager().retrieve_file("notes", filename=filename)
