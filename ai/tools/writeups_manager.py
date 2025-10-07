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
                logger.debug("Write-ups tables ensured to exist in PostgreSQL")
            else:
                logger.error(f"Error creating tables: {result['message']}")
                
        except Exception as e:
            logger.error(f"Error ensuring write-ups tables exist: {e}")
    
    async def create_writeup(self, title: str, original_prompt: str, output_format: str = "html", 
                      output_destination: str = "", frequency: str = "one_time", 
                      scheduled_for: str = None, model_key: str = None, generate_title: bool = True) -> Dict[str, Any]:
        """Create a new write-up request and generate title if needed."""
        try:
            # Use placeholder title if generating title asynchronously
            final_title = title if title and title.strip() else "Generating title..."
            
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
                """, (final_title, original_prompt, output_format, output_destination, 
                     frequency, scheduled_timestamp, model_key, 'pending'))
                
                writeup_id = cursor.fetchone()[0]
                conn.commit()
                return writeup_id
            
            result = execute_with_connection(create_writeup_db)
            if result['status'] != 'success':
                return {"status": "error", "message": f"Database error: {result['message']}"}
            
            writeup_id = result['result']
            logger.info(f"Created write-up {writeup_id}: {final_title}")
            
            # Generate title asynchronously if requested and not provided
            if generate_title and (not title or title.strip() == ""):
                logger.info("Starting async title generation for writeup...")
                import asyncio
                asyncio.create_task(self._generate_title_async(writeup_id, original_prompt, model_key))
            
            # Note: Write-up created but not executed immediately
            # Execution should be triggered via the API endpoint which uses the job system
            return {
                "status": "success",
                "writeup_id": writeup_id,
                "title": final_title,
                "message": "Write-up created successfully. Use the execute endpoint to run it."
            }
                
        except Exception as e:
            logger.error(f"Error creating write-up: {e}")
            return {"status": "error", "message": str(e)}
    
    async def _generate_title(self, original_prompt: str, model_key: str = None) -> str:
        """Generate a title for the writeup using the explainer agent with no tools."""
        try:
            from agents.langchain_agent.explainer_agent import LangChainExplainerAgent
            
            # Create agent instance with no tools for title generation
            agent = LangChainExplainerAgent(
                model_key=model_key,
                tool_groups=[],  # No tools for title generation
                include_all_sections=False,
                enable_session_logging=True  # Enable logging for debugging
            )
            
            # Create a simple prompt for title generation
            title_prompt = f"""
            You are a title generator. Your ONLY task is to create a concise, descriptive title based on the writeup request below.
            
            IMPORTANT: 
            - Do NOT use any tools or search for data
            - Do NOT analyze datasets or query information
            - Simply read the request and generate a title
            - Respond with ONLY the title, nothing else
            
            Title requirements:
            - Maximum 60 characters
            - Clear and specific about what the writeup will cover
            - Use title case
            - Be engaging but professional
            - Avoid jargon or overly technical terms
            - Capture the main topic or question being addressed
            
            Writeup request: "{original_prompt}"
            
            Title:
            """
            
            # Get response from agent
            response_content = ""
            async for chunk in agent.explain_change_streaming(title_prompt, metric_details={}):
                if chunk.startswith("data: "):
                    try:
                        import json
                        data = json.loads(chunk[6:])  # Remove "data: " prefix
                        
                        if 'content' in data:
                            response_content += data['content']
                        elif 'completion' in data:
                            break
                    except json.JSONDecodeError:
                        continue
            
            # Clean up the title
            title = response_content.strip()
            # Remove any quotes or extra formatting
            title = title.strip('"\'')
            # Ensure it's not too long
            if len(title) > 60:
                title = title[:57] + "..."
            
            logger.info(f"Generated title: '{title}' for prompt: '{original_prompt[:50]}...'")
            return title
            
        except Exception as e:
            logger.error(f"Error generating title: {e}")
            # Fallback to a generic title
            return "Writeup Analysis"
    
    async def _generate_title_async(self, writeup_id: int, original_prompt: str, model_key: str = None):
        """Generate a title for the writeup asynchronously and update the database."""
        try:
            logger.info(f"Starting async title generation for writeup {writeup_id}")
            
            # Generate the title using the existing method
            generated_title = await self._generate_title(original_prompt, model_key)
            
            # Update the writeup with the generated title
            def update_title_db(conn):
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE writeups 
                    SET title = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (generated_title, writeup_id))
                conn.commit()
                return "Title updated"
            
            result = execute_with_connection(update_title_db)
            if result['status'] == 'success':
                logger.info(f"Updated writeup {writeup_id} title to: {generated_title}")
            else:
                logger.error(f"Failed to update title for writeup {writeup_id}: {result['message']}")
                
        except Exception as e:
            logger.error(f"Error in async title generation for writeup {writeup_id}: {e}")
            
            # Update with fallback title
            def update_fallback_title_db(conn):
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE writeups 
                    SET title = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, ("Writeup Analysis", writeup_id))
                conn.commit()
                return "Fallback title updated"
            
            execute_with_connection(update_fallback_title_db)

    async def _execute_writeup_directly(self, writeup_id: int, original_prompt: str, output_format: str, model_key: str = None) -> Dict[str, Any]:
        """Execute a write-up directly using the LangChain agent."""
        try:
            from agents.langchain_agent.explainer_agent import LangChainExplainerAgent
            from agents.langchain_agent.config.tool_config import ToolGroup
            
            # Create agent instance with all necessary tools
            agent = LangChainExplainerAgent(
                model_key=model_key,  # Use specified model or default
                tool_groups=[ToolGroup.CORE, ToolGroup.DATA_ANALYSIS, ToolGroup.ANALYSIS, ToolGroup.VISUALIZATION],  # Include all tools including mapping
                include_all_sections=False,
                enable_session_logging=True
            )
            
            # Create a comprehensive prompt for the write-up
            writeup_prompt = f"""
            TASK: Create a comprehensive write-up based on the following request.
            
            REQUEST: "{original_prompt}"
            
            OUTPUT FORMAT: {output_format}
            
            INSTRUCTIONS:
            1. Use available tools strategically to gather relevant data - be targeted and specific in your queries
            2. ALWAYS include LIMIT clauses in DataSF queries (recommended: 500-1000 records max per query)
            3. For large datasets, make multiple focused queries rather than one large query
            4. Prioritize recent data and filter by relevant categories, districts, or time periods
            5. Analyze the data thoroughly to identify key insights and patterns. 
            6. When asked about changes in data, always try to identify the root cause of the change. 
            7. Create maps and visualizations when geographic data is relevant using generate_map_with_query
            8. Create a well-structured, comprehensive write-up that addresses the request
            9. If data sampling was applied, acknowledge this limitation in your analysis
            10. Ensure the content is accurate, informative, and well-organized
            11. Include a brief executive summary at the top of the write-up with a statement of the main finding or findings.
            12. Format the output according to the specified format ({output_format})
            
            CONTEXT WINDOW MANAGEMENT:
            - The system will automatically limit data to prevent context overflow
            - If you receive sampling warnings, adjust your queries to be more targeted
            - Focus on quality insights from representative data rather than exhaustive data collection
            
            Please create a complete, professional write-up that thoroughly addresses the request.
            """
            
            # Get response from agent using async streaming
            response_content = ""
            session_id = None
            
            async for chunk in agent.explain_change_streaming(writeup_prompt, metric_details={}):
                # Process streaming chunks
                if chunk.startswith("data: "):
                    try:
                        import json
                        data = json.loads(chunk[6:])  # Remove "data: " prefix
                        
                        if 'content' in data:
                            response_content += data['content']
                        elif 'session_id' in data:
                            session_id = data['session_id']
                        elif 'completion' in data:
                            # Stream completed
                            break
                    except json.JSONDecodeError:
                        continue
            
            # Create result object similar to the sync version
            result = {
                'status': 'success',
                'content': response_content,
                'session_id': session_id
            }
            
            if session_id:
                logger.info(f"Captured session ID for write-up execution: {session_id}")
            
            # Extract the actual response text from the agent's result
            if isinstance(result, dict) and 'content' in result:
                content = result['content']
            elif isinstance(result, dict) and 'output' in result and result['output']:
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
    
    
    async def execute_writeup(self, writeup_id: int) -> Dict[str, Any]:
        """Execute a write-up using the job system."""
        try:
            writeup = self.get_writeup(writeup_id)
            if not writeup:
                return {"status": "error", "message": "Write-up not found"}
            
            # Import job manager
            from background_jobs import job_manager
            import asyncio
            
            # Create a background job for the writeup execution
            job_id = job_manager.create_job("writeup_execution", f"Execute writeup {writeup_id}")
            logger.info(f"Created job {job_id} for writeup {writeup_id}")
            
            # Import the job execution function
            from routes.writeups import _run_writeup_execution_job
            
            # Start the writeup execution in the background
            model_key = writeup.get('model_key')
            asyncio.create_task(_run_writeup_execution_job(job_id, writeup_id, model_key))
            
            return {
                "status": "success",
                "message": "Write-up execution started",
                "job_id": job_id
            }
                
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
    
    def get_all_writeups(self, lightweight: bool = False) -> List[Dict[str, Any]]:
        """
        Get all write-ups.
        
        Args:
            lightweight: If True, only fetches essential fields for faster loading
        """
        try:
            def get_all_writeups_db(conn):
                cursor = conn.cursor()
                if lightweight:
                    # Only select essential fields for faster loading
                    cursor.execute("""
                        SELECT id, title, status, created_at, updated_at, 
                               original_prompt, model_key, session_id
                        FROM writeups 
                        ORDER BY created_at DESC
                    """)
                else:
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
                    
                    # Parse JSON fields only if not in lightweight mode
                    if not lightweight:
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
    
    async def regenerate_writeup(self, writeup_id: int, model_key: str = None) -> Dict[str, Any]:
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
            
            # Use the job system for regeneration
            from background_jobs import job_manager
            import asyncio
            
            # Create a background job for the writeup regeneration
            job_id = job_manager.create_job("writeup_regeneration", f"Regenerate writeup {writeup_id}")
            logger.info(f"Created regeneration job {job_id} for writeup {writeup_id}")
            
            # Import the job execution function
            from routes.writeups import _run_writeup_execution_job
            
            # Start the writeup regeneration in the background
            asyncio.create_task(_run_writeup_execution_job(job_id, writeup_id, final_model_key))
            
            return {
                "status": "success", 
                "message": "Write-up regeneration started",
                "writeup_id": writeup_id,
                "job_id": job_id
            }
                
        except Exception as e:
            logger.error(f"Error regenerating write-up: {e}")
            return {"status": "error", "message": str(e)}