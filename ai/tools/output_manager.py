"""
Unified output manager for TransparentSF.

This module provides a unified interface for managing output files,
automatically using GCS when available and falling back to local storage.
"""

import os
import json
import logging
from typing import Optional, Dict, Any, List, Union
from datetime import datetime

from .gcs_storage import get_storage_manager

logger = logging.getLogger(__name__)


class OutputManager:
    """
    Unified output manager that handles all file output operations.
    
    This class provides a single interface for storing and retrieving
    all types of output files, automatically using GCS when available.
    """
    
    def __init__(self):
        """Initialize the output manager."""
        self.storage_manager = get_storage_manager()
        self.storage_info = self.storage_manager.get_storage_info()
        
        logger.info(f"OutputManager initialized with GCS: {self.storage_info['gcs_enabled']}")
    
    def store_dashboard_metric(self, data: Dict[str, Any], district: str, metric_id: str) -> bool:
        """
        Store dashboard metric data.
        
        Args:
            data: Metric data dictionary
            district: District ID (e.g., "0" for citywide)
            metric_id: Metric ID
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Add metadata
            data['_metadata'] = {
                'stored_at': datetime.now().isoformat(),
                'district': district,
                'metric_id': metric_id,
                'storage_type': 'gcs' if self.storage_info['gcs_enabled'] else 'local'
            }
            
            success = self.storage_manager.store_file(
                content=data,
                file_type="dashboard",
                district=district,
                metric_id=metric_id
            )
            
            if success:
                logger.info(f"Stored dashboard metric: {metric_id} for district {district}")
            else:
                logger.error(f"Failed to store dashboard metric: {metric_id} for district {district}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error storing dashboard metric {metric_id}: {e}")
            return False
    
    def retrieve_dashboard_metric(self, district: str, metric_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve dashboard metric data.
        
        Args:
            district: District ID
            metric_id: Metric ID
            
        Returns:
            Metric data dictionary or None if not found
        """
        try:
            data = self.storage_manager.retrieve_file("dashboard", district, metric_id)
            
            if data:
                logger.info(f"Retrieved dashboard metric: {metric_id} for district {district}")
            else:
                logger.warning(f"Dashboard metric not found: {metric_id} for district {district}")
            
            return data
            
        except Exception as e:
            logger.error(f"Error retrieving dashboard metric {metric_id}: {e}")
            return None
    
    def store_analysis_file(self, content: str, file_type: str, district: str, metric_id: str) -> bool:
        """
        Store analysis file (monthly, annual, weekly).
        
        Args:
            content: File content (markdown)
            file_type: Type of analysis (monthly, annual, weekly)
            district: District ID
            metric_id: Metric ID
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Add metadata header
            metadata_header = f"""---
stored_at: {datetime.now().isoformat()}
district: {district}
metric_id: {metric_id}
file_type: {file_type}
storage_type: {'gcs' if self.storage_info['gcs_enabled'] else 'local'}
---

"""
            
            full_content = metadata_header + content
            
            success = self.storage_manager.store_file(
                content=full_content,
                file_type=file_type,
                district=district,
                metric_id=metric_id,
                content_type="text/markdown"
            )
            
            if success:
                logger.info(f"Stored {file_type} analysis: {metric_id} for district {district}")
            else:
                logger.error(f"Failed to store {file_type} analysis: {metric_id} for district {district}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error storing {file_type} analysis {metric_id}: {e}")
            return False
    
    def retrieve_analysis_file(self, file_type: str, district: str, metric_id: str) -> Optional[str]:
        """
        Retrieve analysis file (monthly, annual, weekly).
        
        Args:
            file_type: Type of analysis (monthly, annual, weekly)
            district: District ID
            metric_id: Metric ID
            
        Returns:
            File content or None if not found
        """
        try:
            content = self.storage_manager.retrieve_file(file_type, district, metric_id)
            
            if content:
                logger.info(f"Retrieved {file_type} analysis: {metric_id} for district {district}")
            else:
                logger.warning(f"{file_type.capitalize()} analysis not found: {metric_id} for district {district}")
            
            return content
            
        except Exception as e:
            logger.error(f"Error retrieving {file_type} analysis {metric_id}: {e}")
            return None
    
    def store_report(self, content: str, filename: str, report_type: str = "html") -> bool:
        """
        Store report file.
        
        Args:
            content: Report content
            filename: Filename for the report
            report_type: Type of report (html, txt, etc.)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Add metadata to HTML reports
            if report_type == "html" and content.strip().startswith('<'):
                metadata_comment = f"""<!--
Metadata:
- stored_at: {datetime.now().isoformat()}
- filename: {filename}
- report_type: {report_type}
- storage_type: {'gcs' if self.storage_info['gcs_enabled'] else 'local'}
-->

"""
                content = metadata_comment + content
            
            content_type = "text/html" if report_type == "html" else "text/plain"
            
            success = self.storage_manager.store_file(
                content=content,
                file_type="reports",
                filename=filename,
                content_type=content_type
            )
            
            if success:
                logger.info(f"Stored report: {filename}")
            else:
                logger.error(f"Failed to store report: {filename}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error storing report {filename}: {e}")
            return False
    
    def retrieve_report(self, filename: str) -> Optional[str]:
        """
        Retrieve report file.
        
        Args:
            filename: Report filename
            
        Returns:
            Report content or None if not found
        """
        try:
            content = self.storage_manager.retrieve_file("reports", filename=filename)
            
            if content:
                logger.info(f"Retrieved report: {filename}")
            else:
                logger.warning(f"Report not found: {filename}")
            
            return content
            
        except Exception as e:
            logger.error(f"Error retrieving report {filename}: {e}")
            return None
    
    def store_notes(self, content: str, filename: str = "combined_notes.txt") -> bool:
        """
        Store notes file.
        
        Args:
            content: Notes content
            filename: Notes filename
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Add metadata header
            metadata_header = f"""# Notes - {filename}
Generated: {datetime.now().isoformat()}
Storage: {'GCS' if self.storage_info['gcs_enabled'] else 'Local'}

"""
            
            full_content = metadata_header + content
            
            success = self.storage_manager.store_file(
                content=full_content,
                file_type="notes",
                filename=filename,
                content_type="text/plain"
            )
            
            if success:
                logger.info(f"Stored notes: {filename}")
            else:
                logger.error(f"Failed to store notes: {filename}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error storing notes {filename}: {e}")
            return False
    
    def retrieve_notes(self, filename: str = "combined_notes.txt") -> Optional[str]:
        """
        Retrieve notes file.
        
        Args:
            filename: Notes filename
            
        Returns:
            Notes content or None if not found
        """
        try:
            content = self.storage_manager.retrieve_file("notes", filename=filename)
            
            if content:
                logger.info(f"Retrieved notes: {filename}")
            else:
                logger.warning(f"Notes not found: {filename}")
            
            return content
            
        except Exception as e:
            logger.error(f"Error retrieving notes {filename}: {e}")
            return None
    
    def list_files(self, file_type: str, district: str = "0", prefix: Optional[str] = None) -> List[str]:
        """
        List files of a specific type.
        
        Args:
            file_type: Type of files to list (dashboard, monthly, annual, weekly, reports, notes)
            district: District ID (for district-specific file types)
            prefix: Optional prefix to filter files
            
        Returns:
            List of file paths/names
        """
        try:
            files = self.storage_manager.list_files(file_type, district, prefix)
            logger.info(f"Listed {len(files)} {file_type} files for district {district}")
            return files
            
        except Exception as e:
            logger.error(f"Error listing {file_type} files: {e}")
            return []
    
    def delete_file(self, file_type: str, district: str = "0", 
                   metric_id: Optional[str] = None, filename: Optional[str] = None) -> bool:
        """
        Delete a file.
        
        Args:
            file_type: Type of file (dashboard, monthly, annual, weekly, reports, notes)
            district: District ID
            metric_id: Metric ID (for metric-specific files)
            filename: Filename (for reports and notes)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            success = self.storage_manager.delete_file(file_type, district, metric_id, filename)
            
            if success:
                logger.info(f"Deleted {file_type} file: {metric_id or filename}")
            else:
                logger.warning(f"Failed to delete {file_type} file: {metric_id or filename}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error deleting {file_type} file: {e}")
            return False
    
    def get_storage_info(self) -> Dict[str, Any]:
        """
        Get storage configuration information.
        
        Returns:
            Dictionary with storage configuration details
        """
        return self.storage_info
    
    def health_check(self) -> Dict[str, Any]:
        """
        Perform a health check on the storage system.
        
        Returns:
            Dictionary with health check results
        """
        health_info = {
            'timestamp': datetime.now().isoformat(),
            'storage_info': self.storage_info,
            'gcs_accessible': False,
            'local_accessible': False,
            'test_file_success': False
        }
        
        try:
            # Test GCS access
            if self.storage_info['gcs_enabled']:
                # Try to list files (this will test GCS connectivity)
                test_files = self.storage_manager.list_files("reports")
                health_info['gcs_accessible'] = True
                logger.info("GCS health check: PASSED")
            else:
                logger.info("GCS health check: SKIPPED (not enabled)")
            
            # Test local access
            local_dir = self.storage_info['local_fallback_dir']
            if os.path.exists(local_dir):
                health_info['local_accessible'] = True
                logger.info("Local storage health check: PASSED")
            else:
                logger.warning("Local storage health check: FAILED (directory not accessible)")
            
            # Test file operations
            test_content = f"Health check test - {datetime.now().isoformat()}"
            test_filename = "health_check_test.txt"
            
            # Try to store and retrieve a test file
            store_success = self.storage_manager.store_file(
                content=test_content,
                file_type="reports",
                filename=test_filename,
                content_type="text/plain"
            )
            
            if store_success:
                retrieved_content = self.storage_manager.retrieve_file("reports", filename=test_filename)
                if retrieved_content and test_content in retrieved_content:
                    health_info['test_file_success'] = True
                    logger.info("File operations health check: PASSED")
                    
                    # Clean up test file
                    self.storage_manager.delete_file("reports", filename=test_filename)
                else:
                    logger.warning("File operations health check: FAILED (retrieval)")
            else:
                logger.warning("File operations health check: FAILED (storage)")
            
        except Exception as e:
            logger.error(f"Health check error: {e}")
        
        return health_info


# Global output manager instance
_output_manager = None


def get_output_manager() -> OutputManager:
    """
    Get the global output manager instance.
    
    Returns:
        OutputManager instance
    """
    global _output_manager
    if _output_manager is None:
        _output_manager = OutputManager()
    return _output_manager


# Convenience functions for backward compatibility
def store_dashboard_metric(data: Dict[str, Any], district: str, metric_id: str) -> bool:
    """Store dashboard metric data."""
    return get_output_manager().store_dashboard_metric(data, district, metric_id)


def retrieve_dashboard_metric(district: str, metric_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve dashboard metric data."""
    return get_output_manager().retrieve_dashboard_metric(district, metric_id)


def store_analysis_file(content: str, file_type: str, district: str, metric_id: str) -> bool:
    """Store analysis file (monthly, annual, weekly)."""
    return get_output_manager().store_analysis_file(content, file_type, district, metric_id)


def retrieve_analysis_file(file_type: str, district: str, metric_id: str) -> Optional[str]:
    """Retrieve analysis file (monthly, annual, weekly)."""
    return get_output_manager().retrieve_analysis_file(file_type, district, metric_id)


def store_report(content: str, filename: str, report_type: str = "html") -> bool:
    """Store report file."""
    return get_output_manager().store_report(content, filename, report_type)


def retrieve_report(filename: str) -> Optional[str]:
    """Retrieve report file."""
    return get_output_manager().retrieve_report(filename)


def store_notes(content: str, filename: str = "combined_notes.txt") -> bool:
    """Store notes file."""
    return get_output_manager().store_notes(content, filename)


def retrieve_notes(filename: str = "combined_notes.txt") -> Optional[str]:
    """Retrieve notes file."""
    return get_output_manager().retrieve_notes(filename)
