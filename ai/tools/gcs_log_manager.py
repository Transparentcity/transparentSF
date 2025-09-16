#!/usr/bin/env python3
"""
GCS Log Manager for TransparentSF

This script provides utilities for managing session and evaluation logs
stored in Google Cloud Storage, including cleanup, migration, and analysis.
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

# Add the parent directory to the path so we can import our modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.gcs_logger import get_gcs_logger

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class GCSLogManager:
    """Manages GCS logs for sessions and evaluations."""
    
    def __init__(self):
        """Initialize the GCS log manager."""
        self.gcs_logger = get_gcs_logger()
    
    def list_logs(self, log_type: str = "both", year: Optional[str] = None, month: Optional[str] = None):
        """
        List available logs.
        
        Args:
            log_type: Type of logs to list ("sessions", "evals", or "both")
            year: Year to filter by
            month: Month to filter by
        """
        if log_type in ["sessions", "both"]:
            sessions = self.gcs_logger.list_sessions(year, month)
            print(f"\nSessions ({len(sessions)} found):")
            for session_id in sessions[:10]:  # Show first 10
                print(f"  - {session_id}")
            if len(sessions) > 10:
                print(f"  ... and {len(sessions) - 10} more")
        
        if log_type in ["evals", "both"]:
            evals = self.gcs_logger.list_evaluations(year, month)
            print(f"\nEvaluations ({len(evals)} found):")
            for eval_id in evals[:10]:  # Show first 10
                print(f"  - {eval_id}")
            if len(evals) > 10:
                print(f"  ... and {len(evals) - 10} more")
    
    def retrieve_log(self, log_id: str, log_type: str = "session"):
        """
        Retrieve and display a specific log.
        
        Args:
            log_id: The log identifier
            log_type: Type of log ("session" or "eval")
        """
        if log_type == "session":
            data = self.gcs_logger.retrieve_session(log_id)
        elif log_type == "eval":
            data = self.gcs_logger.retrieve_evaluation(log_id)
        else:
            print(f"Invalid log type: {log_type}")
            return
        
        if data:
            print(f"\n{log_type.title()} Log: {log_id}")
            print("=" * 50)
            print(json.dumps(data, indent=2, default=str))
        else:
            print(f"Log not found: {log_id}")
    
    def cleanup_local_logs(self, days_to_keep: Optional[int] = None, dry_run: bool = False):
        """
        Clean up old local log files.
        
        Args:
            days_to_keep: Number of days to keep logs
            dry_run: If True, only show what would be deleted
        """
        if dry_run:
            print("DRY RUN - No files will be deleted")
        
        stats = self.gcs_logger.cleanup_old_logs(days_to_keep)
        
        if dry_run:
            print(f"Would remove {stats['sessions_removed']} session logs")
            print(f"Would remove {stats['evals_removed']} evaluation logs")
        else:
            print(f"Removed {stats['sessions_removed']} session logs")
            print(f"Removed {stats['evals_removed']} evaluation logs")
    
    def migrate_local_to_gcs(self, log_type: str = "both", dry_run: bool = False):
        """
        Migrate local logs to GCS.
        
        Args:
            log_type: Type of logs to migrate ("sessions", "evals", or "both")
            dry_run: If True, only show what would be migrated
        """
        if dry_run:
            print("DRY RUN - No files will be migrated")
        
        migrated_count = 0
        
        if log_type in ["sessions", "both"]:
            sessions_dir = self.gcs_logger.local_sessions_dir
            if os.path.exists(sessions_dir):
                for filename in os.listdir(sessions_dir):
                    if filename.endswith('.json'):
                        session_id = filename.replace('.json', '')
                        if dry_run:
                            print(f"Would migrate session: {session_id}")
                        else:
                            # Read local file and upload to GCS
                            local_path = os.path.join(sessions_dir, filename)
                            with open(local_path, 'r') as f:
                                session_data = json.load(f)
                            success = self.gcs_logger.log_session(session_data, session_id)
                            if success:
                                migrated_count += 1
                                print(f"Migrated session: {session_id}")
                            else:
                                print(f"Failed to migrate session: {session_id}")
        
        if log_type in ["evals", "both"]:
            evals_dir = self.gcs_logger.local_evals_dir
            if os.path.exists(evals_dir):
                for filename in os.listdir(evals_dir):
                    if filename.endswith('.json'):
                        eval_id = filename.replace('.json', '')
                        if dry_run:
                            print(f"Would migrate evaluation: {eval_id}")
                        else:
                            # Read local file and upload to GCS
                            local_path = os.path.join(evals_dir, filename)
                            with open(local_path, 'r') as f:
                                eval_data = json.load(f)
                            success = self.gcs_logger.log_evaluation(eval_data, eval_id)
                            if success:
                                migrated_count += 1
                                print(f"Migrated evaluation: {eval_id}")
                            else:
                                print(f"Failed to migrate evaluation: {eval_id}")
        
        if not dry_run:
            print(f"Successfully migrated {migrated_count} logs to GCS")
    
    def analyze_logs(self, log_type: str = "sessions", days: int = 7):
        """
        Analyze logs for insights.
        
        Args:
            log_type: Type of logs to analyze ("sessions" or "evals")
            days: Number of days to analyze
        """
        cutoff_date = datetime.now() - timedelta(days=days)
        
        if log_type == "sessions":
            sessions = self.gcs_logger.list_sessions()
            print(f"\nAnalyzing {len(sessions)} sessions from the last {days} days...")
            
            # Basic statistics
            total_tool_calls = 0
            successful_sessions = 0
            failed_sessions = 0
            total_execution_time = 0
            
            for session_id in sessions:
                session_data = self.gcs_logger.retrieve_session(session_id)
                if session_data:
                    # Check if session is within the time range
                    session_time = datetime.fromisoformat(session_data.get('timestamp', '').replace('Z', '+00:00'))
                    if session_time >= cutoff_date:
                        total_tool_calls += len(session_data.get('tool_calls', []))
                        if session_data.get('success', False):
                            successful_sessions += 1
                        else:
                            failed_sessions += 1
                        total_execution_time += session_data.get('total_execution_time_ms', 0)
            
            print(f"Total sessions analyzed: {successful_sessions + failed_sessions}")
            print(f"Successful sessions: {successful_sessions}")
            print(f"Failed sessions: {failed_sessions}")
            print(f"Success rate: {(successful_sessions / (successful_sessions + failed_sessions) * 100):.1f}%" if (successful_sessions + failed_sessions) > 0 else "N/A")
            print(f"Total tool calls: {total_tool_calls}")
            print(f"Average execution time: {(total_execution_time / (successful_sessions + failed_sessions) / 1000):.2f}s" if (successful_sessions + failed_sessions) > 0 else "N/A")
        
        elif log_type == "evals":
            evals = self.gcs_logger.list_evaluations()
            print(f"\nAnalyzing {len(evals)} evaluations from the last {days} days...")
            
            # Basic statistics
            total_evals = 0
            successful_evals = 0
            failed_evals = 0
            
            for eval_id in evals:
                eval_data = self.gcs_logger.retrieve_evaluation(eval_id)
                if eval_data:
                    # Check if eval is within the time range
                    eval_time = datetime.fromisoformat(eval_data.get('timestamp', '').replace('Z', '+00:00'))
                    if eval_time >= cutoff_date:
                        total_evals += 1
                        if eval_data.get('success', False):
                            successful_evals += 1
                        else:
                            failed_evals += 1
            
            print(f"Total evaluations analyzed: {total_evals}")
            print(f"Successful evaluations: {successful_evals}")
            print(f"Failed evaluations: {failed_evals}")
            print(f"Success rate: {(successful_evals / total_evals * 100):.1f}%" if total_evals > 0 else "N/A")


def main():
    """Main entry point for the GCS log manager."""
    parser = argparse.ArgumentParser(description="Manage GCS logs for TransparentSF")
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # List command
    list_parser = subparsers.add_parser('list', help='List available logs')
    list_parser.add_argument('--type', choices=['sessions', 'evals', 'both'], default='both',
                           help='Type of logs to list')
    list_parser.add_argument('--year', help='Year to filter by')
    list_parser.add_argument('--month', help='Month to filter by')
    
    # Retrieve command
    retrieve_parser = subparsers.add_parser('retrieve', help='Retrieve a specific log')
    retrieve_parser.add_argument('log_id', help='Log identifier')
    retrieve_parser.add_argument('--type', choices=['session', 'eval'], default='session',
                               help='Type of log to retrieve')
    
    # Cleanup command
    cleanup_parser = subparsers.add_parser('cleanup', help='Clean up old local logs')
    cleanup_parser.add_argument('--days', type=int, help='Number of days to keep logs')
    cleanup_parser.add_argument('--dry-run', action='store_true', help='Show what would be deleted')
    
    # Migrate command
    migrate_parser = subparsers.add_parser('migrate', help='Migrate local logs to GCS')
    migrate_parser.add_argument('--type', choices=['sessions', 'evals', 'both'], default='both',
                              help='Type of logs to migrate')
    migrate_parser.add_argument('--dry-run', action='store_true', help='Show what would be migrated')
    
    # Analyze command
    analyze_parser = subparsers.add_parser('analyze', help='Analyze logs for insights')
    analyze_parser.add_argument('--type', choices=['sessions', 'evals'], default='sessions',
                              help='Type of logs to analyze')
    analyze_parser.add_argument('--days', type=int, default=7, help='Number of days to analyze')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    manager = GCSLogManager()
    
    try:
        if args.command == 'list':
            manager.list_logs(args.type, args.year, args.month)
        elif args.command == 'retrieve':
            manager.retrieve_log(args.log_id, args.type)
        elif args.command == 'cleanup':
            manager.cleanup_local_logs(args.days, args.dry_run)
        elif args.command == 'migrate':
            manager.migrate_local_to_gcs(args.type, args.dry_run)
        elif args.command == 'analyze':
            manager.analyze_logs(args.type, args.days)
    except Exception as e:
        logger.error(f"Error executing command: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
