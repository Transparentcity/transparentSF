"""
Write-ups Manager
Core logic for managing write-ups, including prompt processing, clarification requests, 
plan generation, and execution. Uses PostgreSQL database through db_utils.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path
from tools.db_utils import execute_with_connection

# Set up logging
logger = logging.getLogger(__name__)

class WriteupsManager:
    """Manages write-ups including creation, clarification, planning, and execution."""
    
    def __init__(self):
        """Initialize the write-ups manager."""
        self._ensure_tables_exist()
    
    def _ensure_tables_exist(self):
        """Ensure the write-ups tables exist in the database."""
        try:
            def create_tables(conn):
                cursor = conn.cursor()
                
                # Create writeups table if it doesn't exist
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS writeups (
                        id SERIAL PRIMARY KEY,
                        title TEXT NOT NULL,
                        original_prompt TEXT NOT NULL,
                        output_format VARCHAR(50) DEFAULT 'html',
                        output_destination TEXT DEFAULT '',
                        frequency VARCHAR(50) DEFAULT 'one_time',
                        scheduled_for TIMESTAMP,
                        model_key VARCHAR(100),
                        status VARCHAR(50) DEFAULT 'pending',
                        clarification_questions JSONB,
                        clarification_answers JSONB,
                        execution_plan JSONB,
                        execution_log JSONB,
                        result_content TEXT,
                        result_file_path TEXT,
                        final_content TEXT,
                        session_id VARCHAR(255),
                        error_message TEXT,
                        metadata JSONB DEFAULT '{}'::jsonb,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT valid_status CHECK (status IN ('pending', 'awaiting_clarification', 'ready_to_execute', 'in_progress', 'completed', 'failed', 'cancelled')),
                        CONSTRAINT valid_frequency CHECK (frequency IN ('one_time', 'daily', 'weekly', 'monthly', 'quarterly', 'yearly')),
                        CONSTRAINT valid_output_format CHECK (output_format IN ('html', 'markdown', 'text', 'pdf', 'json'))
                    )
                """)
                
                # Add missing columns if they don't exist (migration)
                try:
                    cursor.execute("ALTER TABLE writeups ADD COLUMN IF NOT EXISTS final_content TEXT")
                    cursor.execute("ALTER TABLE writeups ADD COLUMN IF NOT EXISTS session_id VARCHAR(255)")
                except Exception as e:
                    logger.warning(f"Migration warning (likely columns already exist): {e}")
                
                # Create indexes for better performance
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_writeups_status 
                    ON writeups(status)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_writeups_frequency 
                    ON writeups(frequency)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_writeups_scheduled_for 
                    ON writeups(scheduled_for)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_writeups_created_at 
                    ON writeups(created_at)
                """)
                
                conn.commit()
                return "Tables created successfully"
            
            result = execute_with_connection(create_tables)
            if result['status'] == 'success':
                logger.info("Write-ups tables ensured to exist in PostgreSQL")
            else:
                logger.error(f"Error creating tables: {result['message']}")
                
        except Exception as e:
            logger.error(f"Error ensuring write-ups tables exist: {e}")
    
    def create_writeup(self, title: str, original_prompt: str, output_format: str = "html", 
                      output_destination: str = "", frequency: str = "one_time", 
                      scheduled_for: str = None, model_key: str = None) -> Dict[str, Any]:
        """Create a new write-up request and execute it immediately."""
        try:
            def create_writeup_db(conn):
                cursor = conn.cursor()
                
                # Parse scheduled_for if provided
                scheduled_timestamp = None
                if scheduled_for:
                    try:
                        scheduled_timestamp = datetime.fromisoformat(scheduled_for.replace('Z', '+00:00'))
                    except:
                        scheduled_timestamp = None
                
                cursor.execute("""
                    INSERT INTO writeups (title, original_prompt, output_format, output_destination, 
                                        frequency, scheduled_for, model_key, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (title, original_prompt, output_format, output_destination, 
                     frequency, scheduled_timestamp, model_key, 'in_progress'))
                
                writeup_id = cursor.fetchone()[0]
                conn.commit()
                return writeup_id
            
            result = execute_with_connection(create_writeup_db)
            if result['status'] != 'success':
                return {"status": "error", "message": f"Database error: {result['message']}"}
            
            writeup_id = result['result']
            logger.info(f"Created write-up {writeup_id}: {title}")
            
            # Execute the write-up immediately
            execution_result = self._execute_writeup_directly(writeup_id, original_prompt, output_format, model_key)
            
            if execution_result.get("status") == "success":
                return {
                    "status": "success",
                    "writeup_id": writeup_id,
                    "content": execution_result.get("content", ""),
                    "message": "Write-up created and executed successfully"
                }
            else:
                return {
                    "status": "error",
                    "writeup_id": writeup_id,
                    "message": f"Write-up created but execution failed: {execution_result.get('message', 'Unknown error')}"
                }
                
        except Exception as e:
            logger.error(f"Error creating write-up: {e}")
            return {"status": "error", "message": str(e)}
    
    def _execute_writeup_directly(self, writeup_id: int, original_prompt: str, output_format: str, model_key: str = None) -> Dict[str, Any]:
        """Execute a write-up directly using the LangChain agent."""
        try:
            from agents.langchain_agent.explainer_agent import LangChainExplainerAgent
            from agents.langchain_agent.config.tool_config import ToolGroup
            
            # Create agent instance with all necessary tools
            agent = LangChainExplainerAgent(
                model_key=model_key,  # Use specified model or default
                tool_groups=[ToolGroup.CORE, ToolGroup.DATA_ANALYSIS, ToolGroup.ANALYSIS],  # Include all tools
                include_all_sections=False,
                enable_session_logging=True
            )
            
            # Create a comprehensive prompt for the write-up
            writeup_prompt = f"""
            TASK: Create a comprehensive write-up based on the following request.
            
            REQUEST: "{original_prompt}"
            
            OUTPUT FORMAT: {output_format}
            
            INSTRUCTIONS:
            1. Use all available tools to gather relevant data and information
            2. Analyze the data thoroughly to identify key insights and patterns
            3. Create a well-structured, comprehensive write-up that addresses the request
            4. Ensure the content is accurate, informative, and well-organized
            5. Format the output according to the specified format ({output_format})
            
            Please create a complete, professional write-up that thoroughly addresses the request.
            """
            
            # Get response from agent
            result = agent.explain_change_sync(writeup_prompt, metric_details={})
            
            # Extract session ID from the result
            session_id = result.get('session_id') if isinstance(result, dict) else None
            if session_id:
                logger.info(f"Captured session ID for write-up execution: {session_id}")
            
            # Extract the actual response text from the agent's result
            if isinstance(result, dict) and 'output' in result and result['output']:
                content = result['output'][0].get('text', '')
            elif isinstance(result, dict) and 'explanation' in result:
                content = result['explanation']
            else:
                content = str(result)
            
            # Update the write-up with the final content
            def update_writeup_db(conn):
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE writeups 
                    SET status = 'completed', 
                        final_content = %s, 
                        session_id = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (content, session_id, writeup_id))
                conn.commit()
                return "Write-up completed"
            
            result = execute_with_connection(update_writeup_db)
            if result['status'] == 'success':
                return {
                    "status": "success",
                    "content": content,
                    "session_id": session_id
                }
            else:
                logger.error(f"Error updating write-up: {result['message']}")
                return {"status": "error", "message": "Failed to save write-up content"}
                
        except Exception as e:
            logger.error(f"Error executing write-up directly: {e}")
            
            # Mark the write-up as failed
            def mark_failed_db(conn):
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE writeups 
                    SET status = 'failed', 
                        error_message = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (str(e), writeup_id))
                conn.commit()
                return "Write-up marked as failed"
            
            execute_with_connection(mark_failed_db)
            return {"status": "error", "message": str(e)}
    
    
    def execute_writeup(self, writeup_id: int) -> Dict[str, Any]:
        """Execute a write-up directly (simplified approach)."""
        try:
            writeup = self.get_writeup(writeup_id)
            if not writeup:
                return {"status": "error", "message": "Write-up not found"}
            
            original_prompt = writeup.get('original_prompt', '')
            output_format = writeup.get('output_format', 'html')
            model_key = writeup.get('model_key')
            
            # Execute the write-up directly
            execution_result = self._execute_writeup_directly(writeup_id, original_prompt, output_format, model_key)
            
            return execution_result
                
        except Exception as e:
            logger.error(f"Error executing write-up: {e}")
            return {"status": "error", "message": str(e)}
    
    
    def get_writeup(self, writeup_id: int) -> Optional[Dict[str, Any]]:
        """Get a specific write-up by ID."""
        try:
            def get_writeup_db(conn):
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM writeups WHERE id = %s
                """, (writeup_id,))
                row = cursor.fetchone()
                if row:
                    # Convert to dict
                    columns = [desc[0] for desc in cursor.description]
                    return dict(zip(columns, row))
                return None
            
            result = execute_with_connection(get_writeup_db)
            if result['status'] == 'success':
                writeup = result['result']
                if writeup:
                    # Convert datetime objects to strings for JSON serialization
                    from datetime import datetime
                    for key, value in writeup.items():
                        if isinstance(value, datetime):
                            writeup[key] = value.isoformat()
                    
                    # Parse JSON fields (handle both string and already-parsed JSON)
                    if writeup.get('clarification_questions'):
                        if isinstance(writeup['clarification_questions'], str):
                            writeup['clarification_questions'] = json.loads(writeup['clarification_questions'])
                    if writeup.get('clarification_answers'):
                        if isinstance(writeup['clarification_answers'], str):
                            writeup['clarification_answers'] = json.loads(writeup['clarification_answers'])
                    if writeup.get('execution_plan'):
                        if isinstance(writeup['execution_plan'], str):
                            writeup['execution_plan'] = json.loads(writeup['execution_plan'])
                    if writeup.get('execution_log'):
                        if isinstance(writeup['execution_log'], str):
                            writeup['execution_log'] = json.loads(writeup['execution_log'])
                    if writeup.get('metadata'):
                        if isinstance(writeup['metadata'], str):
                            writeup['metadata'] = json.loads(writeup['metadata'])
                return writeup
            else:
                logger.error(f"Database error: {result['message']}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting write-up: {e}")
            return None
    
    def get_all_writeups(self) -> List[Dict[str, Any]]:
        """Get all write-ups."""
        try:
            def get_all_writeups_db(conn):
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM writeups 
                    ORDER BY created_at DESC
                """)
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in rows]
            
            result = execute_with_connection(get_all_writeups_db)
            if result['status'] == 'success':
                writeups = result['result']
                for writeup in writeups:
                    # Convert datetime objects to strings for JSON serialization
                    from datetime import datetime
                    for key, value in writeup.items():
                        if isinstance(value, datetime):
                            writeup[key] = value.isoformat()
                    
                    # Parse JSON fields (handle both string and already-parsed JSON)
                    if writeup.get('clarification_questions'):
                        if isinstance(writeup['clarification_questions'], str):
                            writeup['clarification_questions'] = json.loads(writeup['clarification_questions'])
                    if writeup.get('clarification_answers'):
                        if isinstance(writeup['clarification_answers'], str):
                            writeup['clarification_answers'] = json.loads(writeup['clarification_answers'])
                    if writeup.get('execution_plan'):
                        if isinstance(writeup['execution_plan'], str):
                            writeup['execution_plan'] = json.loads(writeup['execution_plan'])
                    if writeup.get('execution_log'):
                        if isinstance(writeup['execution_log'], str):
                            writeup['execution_log'] = json.loads(writeup['execution_log'])
                    if writeup.get('metadata'):
                        if isinstance(writeup['metadata'], str):
                            writeup['metadata'] = json.loads(writeup['metadata'])
                return writeups
            else:
                logger.error(f"Database error: {result['message']}")
                return []
                
        except Exception as e:
            logger.error(f"Error getting all write-ups: {e}")
            return []

    def get_scheduled_writeups(self) -> List[Dict[str, Any]]:
        """Get write-ups that are scheduled for execution."""
        try:
            def get_scheduled_writeups_db(conn):
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM writeups 
                    WHERE status = 'ready_to_execute' AND frequency != 'one_time'
                    ORDER BY created_at DESC
                """)
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in rows]
            
            result = execute_with_connection(get_scheduled_writeups_db)
            if result['status'] == 'success':
                writeups = result['result']
                for writeup in writeups:
                    # Convert datetime objects to strings for JSON serialization
                    from datetime import datetime
                    for key, value in writeup.items():
                        if isinstance(value, datetime):
                            writeup[key] = value.isoformat()
                    
                    # Parse JSON fields
                    if writeup.get('clarification_questions'):
                        if isinstance(writeup['clarification_questions'], str):
                            writeup['clarification_questions'] = json.loads(writeup['clarification_questions'])
                    if writeup.get('clarification_answers'):
                        if isinstance(writeup['clarification_answers'], str):
                            writeup['clarification_answers'] = json.loads(writeup['clarification_answers'])
                    if writeup.get('execution_plan'):
                        if isinstance(writeup['execution_plan'], str):
                            writeup['execution_plan'] = json.loads(writeup['execution_plan'])
                    if writeup.get('execution_log'):
                        if isinstance(writeup['execution_log'], str):
                            writeup['execution_log'] = json.loads(writeup['execution_log'])
                    if writeup.get('metadata'):
                        if isinstance(writeup['metadata'], str):
                            writeup['metadata'] = json.loads(writeup['metadata'])
                return writeups
            else:
                logger.error(f"Database error: {result['message']}")
                return []
                
        except Exception as e:
            logger.error(f"Error getting scheduled write-ups: {e}")
            return []
    
    def delete_writeup(self, writeup_id: int) -> Dict[str, Any]:
        """Delete a write-up and all associated data."""
        try:
            def delete_writeup_db(conn):
                cursor = conn.cursor()
                
                # Delete associated steps first
                cursor.execute("DELETE FROM writeup_steps WHERE writeup_id = %s", (writeup_id,))
                
                # Delete associated responses
                cursor.execute("DELETE FROM writeup_responses WHERE writeup_id = %s", (writeup_id,))
                
                # Delete the write-up itself
                cursor.execute("DELETE FROM writeups WHERE id = %s", (writeup_id,))
                
                conn.commit()
                return "Write-up deleted"
            
            result = execute_with_connection(delete_writeup_db)
            if result['status'] == 'success':
                logger.info(f"Deleted write-up {writeup_id}")
                return {"status": "success", "message": "Write-up deleted successfully"}
            else:
                return {"status": "error", "message": f"Database error: {result['message']}"}
                
        except Exception as e:
            logger.error(f"Error deleting write-up: {e}")
            return {"status": "error", "message": str(e)}
    
    def get_writeup_steps(self, writeup_id: int) -> List[Dict[str, Any]]:
        """Get execution steps for a write-up (simplified - returns empty list)."""
        # Since we're using direct execution, we don't have steps anymore
        return []
    
    def get_writeup_responses(self, writeup_id: int) -> List[Dict[str, Any]]:
        """Get all responses for a write-up."""
        try:
            def get_responses_db(conn):
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM writeup_responses 
                    WHERE writeup_id = %s 
                    ORDER BY created_at DESC
                """, (writeup_id,))
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in rows]
            
            result = execute_with_connection(get_responses_db)
            if result['status'] == 'success':
                return result['result']
            else:
                logger.error(f"Database error: {result['message']}")
                return []
                
        except Exception as e:
            logger.error(f"Error getting write-up responses: {e}")
            return []
    
    def regenerate_writeup(self, writeup_id: int, model_key: str = None) -> Dict[str, Any]:
        """Regenerate a write-up by re-executing it with the same or new parameters."""
        try:
            # Get the existing write-up
            writeup = self.get_writeup(writeup_id)
            if not writeup:
                return {"status": "error", "message": "Write-up not found"}
            
            original_prompt = writeup.get('original_prompt', '')
            output_format = writeup.get('output_format', 'html')
            
            # Use the provided model_key or keep the existing one
            final_model_key = model_key if model_key else writeup.get('model_key')
            
            # Reset the write-up status to in_progress and clear previous results
            def reset_writeup_db(conn):
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE writeups 
                    SET status = 'in_progress', 
                        final_content = NULL,
                        error_message = NULL,
                        session_id = NULL,
                        model_key = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (final_model_key, writeup_id))
                conn.commit()
                return "Write-up reset"
            
            result = execute_with_connection(reset_writeup_db)
            if result['status'] != 'success':
                return {"status": "error", "message": f"Database error: {result['message']}"}
            
            logger.info(f"Reset write-up {writeup_id} for regeneration")
            
            # Execute the write-up directly
            execution_result = self._execute_writeup_directly(writeup_id, original_prompt, output_format, final_model_key)
            
            if execution_result.get("status") == "success":
                return {
                    "status": "success", 
                    "message": "Write-up regenerated successfully",
                    "writeup_id": writeup_id,
                    "content": execution_result.get("content", "")
                }
            else:
                return {
                    "status": "error", 
                    "message": f"Failed to regenerate write-up: {execution_result.get('message', 'Unknown error')}"
                }
                
        except Exception as e:
            logger.error(f"Error regenerating write-up: {e}")
            return {"status": "error", "message": str(e)}