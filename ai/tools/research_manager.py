"""
Research Manager - Manages research reports and their execution.

This module provides functionality for creating, executing, and managing
research reports. Research reports are a hybrid between newsletters and
writeups that allow users to investigate specific questions about San Francisco
public data through AI-driven analysis.

Research Flow:
1. User provides a high-level research prompt
2. Seymour translates this into a research agenda (JSON structure)
3. The agenda specifies metrics to examine and questions to answer
4. When "run", each metric/anomaly is researched in parallel
5. Results are collected and synthesized into a final report
"""

import logging
import json
import uuid
import re
from typing import Dict, Any, List, Optional
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field

from tools.db_utils import execute_with_connection

logger = logging.getLogger(__name__)


def generate_slug(title: str, report_id: int) -> str:
    """
    Generate a URL-friendly slug from a title.
    
    Args:
        title: The report title
        report_id: The report ID (used as fallback)
        
    Returns:
        A URL-friendly slug
    """
    # Convert to lowercase and replace spaces with hyphens
    slug = title.lower()
    # Remove special characters, keep only alphanumeric, spaces, and hyphens
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    # Replace multiple spaces/hyphens with single hyphen
    slug = re.sub(r'[\s-]+', '-', slug)
    # Remove leading/trailing hyphens
    slug = slug.strip('-')
    # Limit length
    if len(slug) > 100:
        slug = slug[:100].rstrip('-')
    # If empty, use report ID
    if not slug:
        slug = f"research-{report_id}"
    # Ensure uniqueness by appending report_id if slug is too short
    if len(slug) < 5:
        slug = f"{slug}-{report_id}"
    return slug


class ResearchStatus(str, Enum):
    """Status of a research report."""
    DRAFT = "draft"  # Initial state, agenda being created
    AGENDA_READY = "agenda_ready"  # Agenda finalized, ready to run
    RUNNING = "running"  # Research in progress
    SYNTHESIZING = "synthesizing"  # Combining results
    COMPLETED = "completed"  # Research finished
    FAILED = "failed"  # Research failed
    CANCELLED = "cancelled"  # User cancelled


class ResearchItemStatus(str, Enum):
    """Status of individual research items."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class ResearchAgendaItem(BaseModel):
    """A single item in the research agenda."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    metric_id: Optional[str] = Field(None, description="Metric ID to investigate")
    metric_name: Optional[str] = Field(None, description="Name of the metric")
    anomaly_id: Optional[str] = Field(None, description="Anomaly ID if investigating an anomaly")
    research_question: str = Field(..., description="Specific question to answer")
    reason: Optional[str] = Field(None, description="Why this item is in the agenda")
    priority: int = Field(1, ge=1, le=5, description="Priority (1=highest)")
    status: ResearchItemStatus = Field(ResearchItemStatus.PENDING)
    result: Optional[str] = Field(None, description="Research result content")
    session_id: Optional[str] = Field(None, description="Session ID for the research thread")
    error_message: Optional[str] = Field(None, description="Error message if failed")
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class ResearchAgenda(BaseModel):
    """The complete research agenda for a research report."""
    narrative_thread: str = Field(..., description="Overall narrative/theme of the research")
    research_questions: List[str] = Field(..., min_length=1, description="High-level questions")
    items: List[ResearchAgendaItem] = Field(default_factory=list)
    causal_inferences: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ResearchReport(BaseModel):
    """A complete research report."""
    id: Optional[int] = None
    title: str = Field(..., description="Title of the research report")
    original_prompt: str = Field(..., description="User's original research prompt")
    district: Optional[str] = Field("0", description="District focus (0=citywide)")
    status: ResearchStatus = Field(ResearchStatus.DRAFT)
    agenda: Optional[ResearchAgenda] = None
    agenda_json: Optional[str] = None  # Raw JSON if manually pasted
    final_report: Optional[str] = None
    final_report_html: Optional[str] = None
    model_key: Optional[str] = None
    session_id: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    # Progress tracking
    total_items: int = 0
    completed_items: int = 0
    progress_percent: int = 0
    
    # Public sharing
    is_public: bool = Field(False, description="Whether the report is publicly accessible")
    permalink_slug: Optional[str] = Field(None, description="Unique slug for permalink URL")
    social_media_content: Optional[Dict[str, Any]] = Field(None, description="Social media content (title, text, visuals)")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class ResearchManager:
    """Manages research reports including creation, execution, and storage."""
    
    def __init__(self):
        """Initialize the research manager."""
        self._ensure_tables_exist()
    
    def _ensure_tables_exist(self):
        """Ensure the research tables exist in the database."""
        try:
            def create_tables(conn):
                cursor = conn.cursor()
                
                # Create research_reports table
                # Note: We use CREATE TABLE IF NOT EXISTS and ALTER TABLE ADD COLUMN IF NOT EXISTS
                # to safely add new columns without dropping existing tables/data
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS research_reports (
                        id SERIAL PRIMARY KEY,
                        title TEXT NOT NULL,
                        original_prompt TEXT NOT NULL,
                        district VARCHAR(50) DEFAULT '0',
                        status VARCHAR(50) DEFAULT 'draft',
                        agenda JSONB,
                        agenda_json TEXT,
                        final_report TEXT,
                        final_report_html TEXT,
                        model_key VARCHAR(100),
                        session_id VARCHAR(255),
                        error_message TEXT,
                        total_items INTEGER DEFAULT 0,
                        completed_items INTEGER DEFAULT 0,
                        progress_percent INTEGER DEFAULT 0,
                        metadata JSONB DEFAULT '{}'::jsonb,
                        is_public BOOLEAN DEFAULT FALSE,
                        permalink_slug VARCHAR(255) UNIQUE,
                        social_media_content JSONB,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT valid_research_status CHECK (
                            status IN ('draft', 'agenda_ready', 'running', 'synthesizing', 
                                      'completed', 'failed', 'cancelled')
                        )
                    )
                """)
                
                # Add new columns if they don't exist (for existing databases)
                try:
                    cursor.execute("""
                        ALTER TABLE research_reports 
                        ADD COLUMN IF NOT EXISTS is_public BOOLEAN DEFAULT FALSE
                    """)
                    cursor.execute("""
                        ALTER TABLE research_reports 
                        ADD COLUMN IF NOT EXISTS permalink_slug VARCHAR(255)
                    """)
                    cursor.execute("""
                        ALTER TABLE research_reports 
                        ADD COLUMN IF NOT EXISTS social_media_content JSONB
                    """)
                    # Create unique index on permalink_slug
                    cursor.execute("""
                        CREATE UNIQUE INDEX IF NOT EXISTS research_reports_permalink_slug_idx 
                        ON research_reports (permalink_slug) 
                        WHERE permalink_slug IS NOT NULL
                    """)
                    # Create index on is_public for filtering
                    cursor.execute("""
                        CREATE INDEX IF NOT EXISTS research_reports_is_public_idx 
                        ON research_reports (is_public)
                    """)
                except Exception as e:
                    logger.debug(f"Note adding new columns (may already exist): {e}")
                
                # Create index on created_at for faster queries
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS research_reports_created_at_idx 
                    ON research_reports (created_at DESC)
                """)
                
                # Create index on status for filtering
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS research_reports_status_idx 
                    ON research_reports (status)
                """)
                
                # Create research_items table for individual research items
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS research_items (
                        id SERIAL PRIMARY KEY,
                        report_id INTEGER REFERENCES research_reports(id) ON DELETE CASCADE,
                        item_id VARCHAR(255) NOT NULL,
                        metric_id VARCHAR(50),
                        metric_name TEXT,
                        anomaly_id VARCHAR(50),
                        research_question TEXT NOT NULL,
                        reason TEXT,
                        priority INTEGER DEFAULT 1,
                        status VARCHAR(50) DEFAULT 'pending',
                        result TEXT,
                        session_id VARCHAR(255),
                        error_message TEXT,
                        started_at TIMESTAMP,
                        completed_at TIMESTAMP,
                        metadata JSONB DEFAULT '{}'::jsonb,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT valid_item_status CHECK (
                            status IN ('pending', 'in_progress', 'completed', 'failed')
                        )
                    )
                """)
                
                # Create unique index on item_id to prevent duplicates
                cursor.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS research_items_item_id_unique 
                    ON research_items(report_id, item_id)
                """)
                
                # Create indexes for research_items
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS research_items_report_id_idx 
                    ON research_items (report_id)
                """)
                
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS research_items_status_idx 
                    ON research_items (status)
                """)
                
                conn.commit()
                cursor.close()
                return True
                
            result = execute_with_connection(create_tables)
            if result.get("status") == "success":
                logger.info("Research tables created/verified successfully")
            else:
                logger.error(f"Failed to create research tables: {result.get('message')}")
                
        except Exception as e:
            logger.error(f"Error ensuring research tables exist: {e}")
    
    def create_report(
        self,
        title: str,
        original_prompt: str,
        district: str = "0",
        model_key: str = None,
        agenda: Optional[Dict[str, Any]] = None,
        agenda_json: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create a new research report.
        
        Args:
            title: Title of the research report
            original_prompt: User's original research prompt
            district: District focus (0=citywide)
            model_key: Model to use for AI operations
            agenda: Parsed agenda dict (optional)
            agenda_json: Raw agenda JSON string (optional, for manual paste)
            metadata: Additional metadata to store (optional)
            
        Returns:
            Dict with status and report_id
        """
        try:
            def create_operation(conn):
                cursor = conn.cursor()
                
                # Determine initial status
                if agenda or agenda_json:
                    status = ResearchStatus.AGENDA_READY.value
                else:
                    status = ResearchStatus.DRAFT.value
                
                # Prepare metadata
                metadata_json = json.dumps(metadata) if metadata else '{}'
                
                cursor.execute("""
                    INSERT INTO research_reports (
                        title, original_prompt, district, status, 
                        agenda, agenda_json, model_key, metadata,
                        created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    ) RETURNING id
                """, (
                    title,
                    original_prompt,
                    district,
                    status,
                    json.dumps(agenda) if agenda else None,
                    agenda_json,
                    model_key,
                    metadata_json
                ))
                
                report_id = cursor.fetchone()[0]
                conn.commit()
                cursor.close()
                
                return {"status": "success", "report_id": report_id}
                
            result = execute_with_connection(create_operation)
            
            if result.get("status") == "success":
                logger.info(f"Created research report {result.get('result', {}).get('report_id')}")
                return result.get("result")
            else:
                return {"status": "error", "message": result.get("message")}
                
        except Exception as e:
            logger.error(f"Error creating research report: {e}")
            return {"status": "error", "message": str(e)}
    
    def get_report(self, report_id: int) -> Optional[Dict[str, Any]]:
        """Get a research report by ID."""
        try:
            def get_operation(conn):
                from psycopg2.extras import RealDictCursor
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                
                cursor.execute("""
                    SELECT * FROM research_reports WHERE id = %s
                """, (report_id,))
                
                result = cursor.fetchone()
                cursor.close()
                
                if result:
                    return dict(result)
                return None
                
            result = execute_with_connection(get_operation)
            
            if result.get("status") == "success":
                return result.get("result")
            return None
            
        except Exception as e:
            logger.error(f"Error getting research report {report_id}: {e}")
            return None
    
    def list_reports(
        self,
        limit: int = 50,
        status: Optional[str] = None,
        district: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List research reports with optional filters."""
        try:
            def list_operation(conn):
                from psycopg2.extras import RealDictCursor
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                
                query = "SELECT * FROM research_reports WHERE 1=1"
                params = []
                
                if status:
                    query += " AND status = %s"
                    params.append(status)
                    
                if district:
                    query += " AND district = %s"
                    params.append(district)
                
                query += " ORDER BY created_at DESC LIMIT %s"
                params.append(limit)
                
                cursor.execute(query, params)
                results = cursor.fetchall()
                cursor.close()
                
                return [dict(row) for row in results]
                
            result = execute_with_connection(list_operation)
            
            if result.get("status") == "success":
                return result.get("result", [])
            return []
            
        except Exception as e:
            logger.error(f"Error listing research reports: {e}")
            return []
    
    def update_report(
        self,
        report_id: int,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Update a research report.
        
        Args:
            report_id: ID of the report to update
            **kwargs: Fields to update (status, agenda, final_report, etc.)
            
        Returns:
            Dict with status
        """
        try:
            def update_operation(conn):
                cursor = conn.cursor()
                
                # Build update query dynamically
                update_fields = []
                params = []
                
                allowed_fields = [
                    'title', 'status', 'agenda', 'agenda_json', 
                    'final_report', 'final_report_html', 'model_key',
                    'session_id', 'error_message', 'total_items',
                    'completed_items', 'progress_percent', 'metadata',
                    'is_public', 'permalink_slug', 'social_media_content'
                ]
                
                for field, value in kwargs.items():
                    if field in allowed_fields:
                        if field in ['agenda', 'metadata', 'social_media_content']:
                            update_fields.append(f"{field} = %s")
                            params.append(json.dumps(value) if value else None)
                        else:
                            update_fields.append(f"{field} = %s")
                            params.append(value)
                
                if not update_fields:
                    return {"status": "error", "message": "No valid fields to update"}
                
                update_fields.append("updated_at = CURRENT_TIMESTAMP")
                
                query = f"""
                    UPDATE research_reports 
                    SET {', '.join(update_fields)}
                    WHERE id = %s
                """
                params.append(report_id)
                
                cursor.execute(query, params)
                conn.commit()
                cursor.close()
                
                return {"status": "success"}
                
            result = execute_with_connection(update_operation)
            
            if result.get("status") == "success":
                logger.info(f"Updated research report {report_id}")
                return result.get("result")
            else:
                return {"status": "error", "message": result.get("message")}
                
        except Exception as e:
            logger.error(f"Error updating research report {report_id}: {e}")
            return {"status": "error", "message": str(e)}
    
    def delete_report(self, report_id: int) -> Dict[str, Any]:
        """Delete a research report."""
        try:
            def delete_operation(conn):
                cursor = conn.cursor()
                
                cursor.execute("""
                    DELETE FROM research_reports WHERE id = %s
                """, (report_id,))
                
                conn.commit()
                cursor.close()
                
                return {"status": "success"}
                
            result = execute_with_connection(delete_operation)
            
            if result.get("status") == "success":
                logger.info(f"Deleted research report {report_id}")
                return result.get("result")
            else:
                return {"status": "error", "message": result.get("message")}
                
        except Exception as e:
            logger.error(f"Error deleting research report {report_id}: {e}")
            return {"status": "error", "message": str(e)}
    
    def add_research_item(
        self,
        report_id: int,
        research_question: str,
        metric_id: Optional[str] = None,
        metric_name: Optional[str] = None,
        anomaly_id: Optional[str] = None,
        reason: Optional[str] = None,
        priority: int = 1,
        success_criteria: Optional[str] = None,
        builds_toward: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Add a research item to a report."""
        try:
            def add_operation(conn):
                cursor = conn.cursor()
                
                item_id = str(uuid.uuid4())
                
                # Store extra fields in metadata
                item_metadata = metadata.copy() if metadata else {}
                if success_criteria:
                    item_metadata["success_criteria"] = success_criteria
                if builds_toward:
                    item_metadata["builds_toward"] = builds_toward
                
                cursor.execute("""
                    INSERT INTO research_items (
                        report_id, item_id, metric_id, metric_name,
                        anomaly_id, research_question, reason, priority,
                        status, metadata, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, CURRENT_TIMESTAMP
                    ) RETURNING id
                """, (
                    report_id, item_id, metric_id, metric_name,
                    anomaly_id, research_question, reason, priority,
                    json.dumps(item_metadata) if item_metadata else '{}'
                ))
                
                db_id = cursor.fetchone()[0]
                
                # Update total_items count on report
                cursor.execute("""
                    UPDATE research_reports 
                    SET total_items = total_items + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (report_id,))
                
                conn.commit()
                cursor.close()
                
                return {"status": "success", "item_id": item_id, "db_id": db_id}
                
            result = execute_with_connection(add_operation)
            
            if result.get("status") == "success":
                return result.get("result")
            return {"status": "error", "message": result.get("message")}
                
        except Exception as e:
            logger.error(f"Error adding research item: {e}")
            return {"status": "error", "message": str(e)}
    
    def update_research_item(
        self,
        report_id: int,
        item_id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Update a research item."""
        try:
            def update_operation(conn):
                cursor = conn.cursor()
                
                update_fields = []
                params = []
                
                allowed_fields = [
                    'status', 'result', 'session_id', 'error_message',
                    'started_at', 'completed_at', 'metadata'
                ]
                
                for field, value in kwargs.items():
                    if field in allowed_fields:
                        if field == 'metadata':
                            update_fields.append(f"{field} = %s")
                            params.append(json.dumps(value) if value else None)
                        else:
                            update_fields.append(f"{field} = %s")
                            params.append(value)
                
                if not update_fields:
                    return {"status": "error", "message": "No valid fields to update"}
                
                query = f"""
                    UPDATE research_items 
                    SET {', '.join(update_fields)}
                    WHERE report_id = %s AND item_id = %s
                """
                params.extend([report_id, item_id])
                
                cursor.execute(query, params)
                
                # Update completed_items count if status changed to completed
                if kwargs.get('status') == 'completed':
                    cursor.execute("""
                        UPDATE research_reports 
                        SET completed_items = (
                            SELECT COUNT(*) FROM research_items 
                            WHERE report_id = %s AND status = 'completed'
                        ),
                        progress_percent = (
                            SELECT CASE 
                                WHEN total_items > 0 
                                THEN (COUNT(*) FILTER (WHERE status = 'completed') * 100 / total_items)
                                ELSE 0 
                            END
                            FROM research_items 
                            WHERE report_id = %s
                        ),
                        updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (report_id, report_id, report_id))
                
                conn.commit()
                cursor.close()
                
                return {"status": "success"}
                
            result = execute_with_connection(update_operation)
            
            if result.get("status") == "success":
                return result.get("result")
            return {"status": "error", "message": result.get("message")}
                
        except Exception as e:
            logger.error(f"Error updating research item: {e}")
            return {"status": "error", "message": str(e)}
    
    def get_research_items(self, report_id: int) -> List[Dict[str, Any]]:
        """Get all research items for a report."""
        try:
            def get_operation(conn):
                from psycopg2.extras import RealDictCursor
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                
                cursor.execute("""
                    SELECT * FROM research_items 
                    WHERE report_id = %s 
                    ORDER BY priority, created_at
                """, (report_id,))
                
                results = cursor.fetchall()
                cursor.close()
                
                return [dict(row) for row in results]
                
            result = execute_with_connection(get_operation)
            
            if result.get("status") == "success":
                return result.get("result", [])
            return []
            
        except Exception as e:
            logger.error(f"Error getting research items: {e}")
            return []
    
    def delete_research_items(self, report_id: int) -> Dict[str, Any]:
        """Delete all research items for a report."""
        try:
            def delete_operation(conn):
                cursor = conn.cursor()
                
                cursor.execute("""
                    DELETE FROM research_items WHERE report_id = %s
                """, (report_id,))
                
                deleted_count = cursor.rowcount
                conn.commit()
                cursor.close()
                
                return {"status": "success", "deleted_count": deleted_count}
                
            result = execute_with_connection(delete_operation)
            
            if result.get("status") == "success":
                logger.info(f"Deleted {result.get('result', {}).get('deleted_count', 0)} items for report {report_id}")
                return result.get("result", {"status": "success"})
            return {"status": "error", "message": result.get("message")}
            
        except Exception as e:
            logger.error(f"Error deleting research items: {e}")
            return {"status": "error", "message": str(e)}

    def delete_non_agenda_items(self, report_id: int) -> Dict[str, Any]:
        """
        Delete non-agenda research items (exploration + evaluation-generated).

        This is used to "start clean" on regenerate so the report begins with only
        the original agenda items, and any evaluation follow-ons are re-created
        later during the run.
        """
        try:
            def delete_operation(conn):
                cursor = conn.cursor()

                # Delete exploration + evaluation items by multiple signals:
                # - explicit metadata phase/added_by
                # - priority >= 100 (legacy exploration)
                # - reason string patterns (legacy)
                cursor.execute(
                    """
                    DELETE FROM research_items
                    WHERE report_id = %s
                      AND (
                        COALESCE(metadata->>'phase', '') IN ('exploration', 'evaluation')
                        OR COALESCE(metadata->>'added_by', '') IN ('exploration', 'evaluation')
                        OR priority >= 100
                        OR COALESCE(reason, '') ILIKE '%%breadth-first exploration%%'
                        OR COALESCE(reason, '') ILIKE '%%research question evaluation%%'
                      )
                    """,
                    (report_id,),
                )
                deleted_count = cursor.rowcount

                # Recompute progress stats since we removed items
                cursor.execute(
                    """
                    UPDATE research_reports
                    SET total_items = (
                            SELECT COUNT(*) FROM research_items WHERE report_id = %s
                        ),
                        completed_items = (
                            SELECT COUNT(*) FROM research_items
                            WHERE report_id = %s AND status = 'completed'
                        ),
                        progress_percent = (
                            SELECT CASE
                                WHEN COUNT(*) > 0
                                THEN (COUNT(*) FILTER (WHERE status = 'completed') * 100 / COUNT(*))
                                ELSE 0
                            END
                            FROM research_items
                            WHERE report_id = %s
                        ),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (report_id, report_id, report_id, report_id),
                )

                conn.commit()
                cursor.close()
                return {"status": "success", "deleted_count": deleted_count}

            result = execute_with_connection(delete_operation)
            if result.get("status") == "success":
                return result.get("result", {"status": "success", "deleted_count": 0})
            return {"status": "error", "message": result.get("message")}

        except Exception as e:
            logger.error(f"Error deleting non-agenda items: {e}")
            return {"status": "error", "message": str(e)}


# Global instance
_research_manager: Optional[ResearchManager] = None


def get_research_manager() -> ResearchManager:
    """Get the global research manager instance."""
    global _research_manager
    if _research_manager is None:
        _research_manager = ResearchManager()
    return _research_manager

