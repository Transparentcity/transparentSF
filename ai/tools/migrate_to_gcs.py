#!/usr/bin/env python3
"""
Migration script to move existing output files to Google Cloud Storage.

This script migrates all existing output files from local storage to GCS,
maintaining the same directory structure and file organization.
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple
from datetime import datetime

# Add the parent directory to the path so we can import our modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.gcs_storage import get_storage_manager

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('migration.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class OutputMigrator:
    """Handles migration of output files to Google Cloud Storage."""
    
    def __init__(self, output_dir: str = None):
        """
        Initialize the migrator.
        
        Args:
            output_dir: Path to the output directory (defaults to ai/output)
        """
        if output_dir is None:
            script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            output_dir = os.path.join(script_dir, 'output')
        
        self.output_dir = output_dir
        self.storage_manager = get_storage_manager()
        self.migration_stats = {
            'total_files': 0,
            'successful_uploads': 0,
            'failed_uploads': 0,
            'skipped_files': 0,
            'file_types': {}
        }
    
    def scan_output_directory(self) -> Dict[str, List[str]]:
        """
        Scan the output directory and return a mapping of file types to file paths.
        
        Returns:
            Dictionary mapping file types to lists of file paths
        """
        file_mapping = {
            'dashboard': [],
            'monthly': [],
            'annual': [],
            'weekly': [],
            'reports': [],
            'notes': [],
            'ytd': []
        }
        
        if not os.path.exists(self.output_dir):
            logger.error(f"Output directory does not exist: {self.output_dir}")
            return file_mapping
        
        logger.info(f"Scanning output directory: {self.output_dir}")
        
        for root, dirs, files in os.walk(self.output_dir):
            rel_path = os.path.relpath(root, self.output_dir)
            
            # Determine file type based on directory structure
            if rel_path == '.':
                # Root level files
                for file in files:
                    if file.endswith('.html') or file.endswith('.txt'):
                        file_mapping['reports'].append(os.path.join(root, file))
            elif rel_path.startswith('dashboard'):
                for file in files:
                    if file.endswith('.json'):
                        file_mapping['dashboard'].append(os.path.join(root, file))
            elif rel_path.startswith('monthly'):
                for file in files:
                    if file.endswith('.md'):
                        file_mapping['monthly'].append(os.path.join(root, file))
            elif rel_path.startswith('annual'):
                for file in files:
                    if file.endswith('.md'):
                        file_mapping['annual'].append(os.path.join(root, file))
            elif rel_path.startswith('weekly'):
                for file in files:
                    if file.endswith('.md') or file.endswith('.json'):
                        file_mapping['weekly'].append(os.path.join(root, file))
            elif rel_path.startswith('reports'):
                for file in files:
                    file_mapping['reports'].append(os.path.join(root, file))
            elif rel_path.startswith('notes'):
                for file in files:
                    file_mapping['notes'].append(os.path.join(root, file))
            elif rel_path.startswith('ytd'):
                for file in files:
                    file_mapping['ytd'].append(os.path.join(root, file))
        
        # Log statistics
        total_files = sum(len(files) for files in file_mapping.values())
        logger.info(f"Found {total_files} files to migrate:")
        for file_type, files in file_mapping.items():
            if files:
                logger.info(f"  {file_type}: {len(files)} files")
        
        return file_mapping
    
    def extract_file_metadata(self, file_path: str) -> Tuple[str, str, str]:
        """
        Extract metadata from file path (district, metric_id, filename).
        
        Args:
            file_path: Full path to the file
            
        Returns:
            Tuple of (district, metric_id, filename)
        """
        rel_path = os.path.relpath(file_path, self.output_dir)
        path_parts = rel_path.split(os.sep)
        
        if len(path_parts) >= 3:
            # Format: type/district/metric_id.ext or type/district/filename
            file_type = path_parts[0]
            district = path_parts[1]
            
            if len(path_parts) >= 3:
                filename = path_parts[2]
                # Extract metric_id from filename (remove extension)
                metric_id = os.path.splitext(filename)[0]
                return district, metric_id, filename
            else:
                return district, None, None
        elif len(path_parts) == 2:
            # Format: type/filename
            file_type = path_parts[0]
            filename = path_parts[1]
            return "0", None, filename
        else:
            # Format: filename
            filename = path_parts[0]
            return "0", None, filename
    
    def migrate_file(self, file_path: str, file_type: str) -> bool:
        """
        Migrate a single file to GCS.
        
        Args:
            file_path: Path to the file to migrate
            file_type: Type of file (dashboard, monthly, etc.)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Read file content
            with open(file_path, 'rb') as f:
                content = f.read()
            
            # Determine content type
            if file_path.endswith('.json'):
                content_type = "application/json"
                # Try to parse as JSON to validate
                try:
                    json.loads(content.decode('utf-8'))
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON file: {file_path}")
            elif file_path.endswith('.html'):
                content_type = "text/html"
            elif file_path.endswith('.md'):
                content_type = "text/markdown"
            elif file_path.endswith('.txt'):
                content_type = "text/plain"
            else:
                content_type = "application/octet-stream"
            
            # Extract metadata
            district, metric_id, filename = self.extract_file_metadata(file_path)
            
            # Store in GCS
            success = self.storage_manager.store_file(
                content=content,
                file_type=file_type,
                district=district,
                metric_id=metric_id,
                filename=filename,
                content_type=content_type
            )
            
            if success:
                logger.info(f"Successfully migrated: {file_path}")
                self.migration_stats['successful_uploads'] += 1
            else:
                logger.error(f"Failed to migrate: {file_path}")
                self.migration_stats['failed_uploads'] += 1
            
            return success
            
        except Exception as e:
            logger.error(f"Error migrating {file_path}: {e}")
            self.migration_stats['failed_uploads'] += 1
            return False
    
    def migrate_all_files(self, dry_run: bool = False) -> Dict[str, int]:
        """
        Migrate all output files to GCS.
        
        Args:
            dry_run: If True, only log what would be migrated without actually doing it
            
        Returns:
            Migration statistics
        """
        logger.info("Starting migration process...")
        
        if dry_run:
            logger.info("DRY RUN MODE - No files will actually be migrated")
        
        # Scan for files
        file_mapping = self.scan_output_directory()
        
        # Reset stats
        self.migration_stats = {
            'total_files': 0,
            'successful_uploads': 0,
            'failed_uploads': 0,
            'skipped_files': 0,
            'file_types': {}
        }
        
        # Migrate files by type
        for file_type, files in file_mapping.items():
            if not files:
                continue
            
            logger.info(f"Migrating {file_type} files...")
            self.migration_stats['file_types'][file_type] = {
                'total': len(files),
                'successful': 0,
                'failed': 0
            }
            
            for file_path in files:
                self.migration_stats['total_files'] += 1
                
                if dry_run:
                    logger.info(f"Would migrate: {file_path}")
                    self.migration_stats['skipped_files'] += 1
                    continue
                
                success = self.migrate_file(file_path, file_type)
                
                if success:
                    self.migration_stats['file_types'][file_type]['successful'] += 1
                else:
                    self.migration_stats['file_types'][file_type]['failed'] += 1
        
        # Log final statistics
        self.log_migration_stats()
        
        return self.migration_stats
    
    def log_migration_stats(self):
        """Log migration statistics."""
        stats = self.migration_stats
        
        logger.info("=" * 50)
        logger.info("MIGRATION STATISTICS")
        logger.info("=" * 50)
        logger.info(f"Total files processed: {stats['total_files']}")
        logger.info(f"Successful uploads: {stats['successful_uploads']}")
        logger.info(f"Failed uploads: {stats['failed_uploads']}")
        logger.info(f"Skipped files: {stats['skipped_files']}")
        logger.info("")
        
        logger.info("By file type:")
        for file_type, type_stats in stats['file_types'].items():
            logger.info(f"  {file_type}: {type_stats['successful']}/{type_stats['total']} successful")
        
        logger.info("=" * 50)
    
    def verify_migration(self) -> bool:
        """
        Verify that files were successfully migrated by checking a sample.
        
        Returns:
            True if verification passes, False otherwise
        """
        logger.info("Verifying migration...")
        
        # Get a sample of files to verify
        file_mapping = self.scan_output_directory()
        verification_passed = True
        
        for file_type, files in file_mapping.items():
            if not files:
                continue
            
            # Test first few files of each type
            test_files = files[:3]  # Test up to 3 files per type
            
            for file_path in test_files:
                district, metric_id, filename = self.extract_file_metadata(file_path)
                
                # Try to retrieve from GCS
                retrieved_content = self.storage_manager.retrieve_file(
                    file_type, district, metric_id, filename
                )
                
                if retrieved_content is None:
                    logger.error(f"Verification failed: Could not retrieve {file_path}")
                    verification_passed = False
                else:
                    logger.info(f"Verification passed: {file_path}")
        
        if verification_passed:
            logger.info("Migration verification completed successfully!")
        else:
            logger.error("Migration verification failed!")
        
        return verification_passed


def main():
    """Main function to run the migration."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Migrate TransparentSF output files to Google Cloud Storage')
    parser.add_argument('--output-dir', help='Path to output directory (defaults to ai/output)')
    parser.add_argument('--dry-run', action='store_true', help='Perform a dry run without actually migrating files')
    parser.add_argument('--verify', action='store_true', help='Verify migration after completion')
    
    args = parser.parse_args()
    
    # Initialize migrator
    migrator = OutputMigrator(args.output_dir)
    
    # Check storage configuration
    storage_info = migrator.storage_manager.get_storage_info()
    logger.info("Storage configuration:")
    for key, value in storage_info.items():
        logger.info(f"  {key}: {value}")
    
    if not storage_info['gcs_enabled']:
        logger.warning("GCS is not enabled. Files will be stored locally only.")
    
    # Run migration
    stats = migrator.migrate_all_files(dry_run=args.dry_run)
    
    # Verify if requested
    if args.verify and not args.dry_run:
        migrator.verify_migration()
    
    # Exit with appropriate code
    if stats['failed_uploads'] > 0:
        logger.error(f"Migration completed with {stats['failed_uploads']} failures")
        sys.exit(1)
    else:
        logger.info("Migration completed successfully!")
        sys.exit(0)


if __name__ == "__main__":
    main()
