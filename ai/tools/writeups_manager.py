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
            # Read the PostgreSQL SQL file and execute it
            script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            sql_file = os.path.join(script_dir, '..', 'create_writeups_tables_postgres.sql')
            
            if os.path.exists(sql_file):
                with open(sql_file, 'r') as f:
                    sql_script = f.read()
                
                def create_tables(conn):
                    cursor = conn.cursor()
                    cursor.execute(sql_script)
                    conn.commit()
                    return "Tables created successfully"
                
                result = execute_with_connection(create_tables)
                if result['status'] == 'success':
                    logger.info("Write-ups tables ensured to exist in PostgreSQL")
                else:
                    logger.error(f"Error creating tables: {result['message']}")
            else:
                logger.warning(f"SQL file not found: {sql_file}")
        except Exception as e:
            logger.error(f"Error ensuring write-ups tables exist: {e}")
    
    def create_writeup(self, title: str, original_prompt: str, output_format: str = "html", 
                      output_destination: str = "", frequency: str = "one_time", 
                      scheduled_for: str = None, model_key: str = None) -> Dict[str, Any]:
        """Create a new write-up request."""
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
                     frequency, scheduled_timestamp, model_key, 'pending'))
                
                writeup_id = cursor.fetchone()[0]
                conn.commit()
                return writeup_id
            
            result = execute_with_connection(create_writeup_db)
            if result['status'] != 'success':
                return {"status": "error", "message": f"Database error: {result['message']}"}
            
            writeup_id = result['result']
            logger.info(f"Created write-up {writeup_id}: {title}")
            
            # Generate clarification questions
            clarification_result = self._generate_clarification_questions(writeup_id, original_prompt)
                
            return {
                "status": "success",
                "writeup_id": writeup_id,
                "clarification_questions": clarification_result.get("questions", [])
            }
                
        except Exception as e:
            logger.error(f"Error creating write-up: {e}")
            return {"status": "error", "message": str(e)}
    
    def _generate_clarification_questions(self, writeup_id: int, original_prompt: str) -> Dict[str, Any]:
        """Generate clarification questions using the LangChain agent."""
        try:
            from agents.langchain_agent.explainer_agent import LangChainExplainerAgent
            
            clarification_prompt = f"""
            TASK: Generate clarification questions for a write-up request.
            
            WRITE-UP REQUEST: "{original_prompt}"
            
            INSTRUCTIONS: 
            - Do NOT create the actual write-up content
            - Do NOT provide analysis or data
            - ONLY generate 3-5 clarifying questions
            
            Generate specific questions to clarify:
            1. Scope and focus of the write-up
            2. Target audience
            3. Specific data or metrics to include
            4. Desired format and structure
            5. Any specific requirements or constraints
            
            REQUIRED FORMAT: Return ONLY a JSON array of question strings:
            ["Question 1?", "Question 2?", "Question 3?"]
            
            Do not include any other text, analysis, or content.
            """
            
            # Create agent instance
            agent = LangChainExplainerAgent(
                model_key=None,  # Use default model
                tool_groups=[],  # No tools needed for clarification
                include_all_sections=False,
                enable_session_logging=True
            )
            
            # Get response from agent
            result = agent.explain_change_sync(clarification_prompt, metric_details={})
            
            # Extract session ID from the result
            session_id = result.get('session_id') if isinstance(result, dict) else None
            if session_id:
                logger.info(f"Captured session ID for clarification: {session_id}")
            
            # Extract the actual response text from the agent's result
            if isinstance(result, dict) and 'output' in result and result['output']:
                response = result['output'][0].get('text', '')
            elif isinstance(result, dict) and 'explanation' in result:
                response = result['explanation']
            else:
                response = str(result)
            
            logger.info(f"Agent response length: {len(response)}")
            logger.info(f"Agent response preview: {response[:200]}...")
            
            # Try to parse the response as JSON
            try:
                questions = json.loads(response)
                if isinstance(questions, list):
                    logger.info(f"Successfully parsed JSON questions: {questions}")
                    # Store the questions in the database
                    def store_questions_db(conn):
                        cursor = conn.cursor()
                        cursor.execute("""
                            UPDATE writeups 
                            SET clarification_questions = %s, status = 'clarification_needed', session_id = %s
                            WHERE id = %s
                        """, (json.dumps(questions), session_id, writeup_id))
                        conn.commit()
                        return "Questions stored"
                    
                    result = execute_with_connection(store_questions_db)
                    if result['status'] == 'success':
                        return {"status": "success", "questions": questions}
                    else:
                        logger.error(f"Error storing questions: {result['message']}")
                        return {"status": "error", "message": "Failed to store questions"}
            except json.JSONDecodeError as e:
                logger.info(f"JSON decode failed: {e}, trying text extraction")
                # If not JSON, try to extract questions from the response
                questions = self._extract_questions_from_text(response)
                logger.info(f"Extracted questions from text: {questions}")
                
                def store_questions_db(conn):
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE writeups 
                        SET clarification_questions = %s, status = 'clarification_needed', session_id = %s
                        WHERE id = %s
                    """, (json.dumps(questions), session_id, writeup_id))
                    conn.commit()
                    return "Questions stored"
                
                result = execute_with_connection(store_questions_db)
                if result['status'] == 'success':
                    return {"status": "success", "questions": questions}
                else:
                    logger.error(f"Error storing questions: {result['message']}")
                    return {"status": "error", "message": "Failed to store questions"}
                
        except Exception as e:
            logger.error(f"Error generating clarification questions: {e}")
            # Fallback to generic questions
            fallback_questions = [
                "What specific aspects of this topic should be covered?",
                "Who is the target audience for this write-up?",
                "What data sources or metrics should be included?",
                "What is the desired length and format?",
                "Are there any specific requirements or constraints?"
            ]
            
            def store_fallback_questions_db(conn):
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE writeups 
                    SET clarification_questions = %s, status = 'clarification_needed'
                    WHERE id = %s
                """, (json.dumps(fallback_questions), writeup_id))
                conn.commit()
                return "Fallback questions stored"
            
            result = execute_with_connection(store_fallback_questions_db)
            if result['status'] == 'success':
                return {"status": "success", "questions": fallback_questions}
            else:
                return {"status": "error", "message": "Failed to store fallback questions"}
    
    def _extract_questions_from_text(self, text: str) -> List[str]:
        """Extract questions from text response."""
        import re
        
        # First, check if the response looks like it's generating content instead of questions
        # Look for markdown headers and content indicators, but allow "write-up" in questions
        content_indicators = ['# ', '## ', '### ', 'analysis shows', 'data shows', 'based on the data', 'the data reveals']
        if any(indicator in text.lower() for indicator in content_indicators):
            logger.warning("Agent appears to be generating content instead of questions")
            return []
        
        # Look for JSON array pattern first
        json_match = re.search(r'\[(.*?)\]', text, re.DOTALL)
        if json_match:
            try:
                json_str = '[' + json_match.group(1) + ']'
                questions = json.loads(json_str)
                if isinstance(questions, list) and len(questions) > 0:
                    logger.info(f"Successfully extracted JSON questions: {questions}")
                    return questions
            except Exception as e:
                logger.info(f"JSON parsing failed: {e}")
        
        # Look for questions in various formats
        questions = []
        
        # Pattern 1: Look for lines that start with numbers and contain questions
        lines = text.split('\n')
        for line in lines:
            line = line.strip()
            if line and ('?' in line):
                # Remove numbering if present (1., 2., etc.)
                question = re.sub(r'^\d+\.?\s*', '', line)
                # Remove quotes if present
                question = question.strip('"\'')
                if question.endswith('?') and len(question) > 10:  # Ensure it's a real question
                    questions.append(question)
        
        # Pattern 2: Look for questions in quotes
        if not questions:
            quoted_questions = re.findall(r'["\']([^"\']*\?[^"\']*)["\']', text)
            for q in quoted_questions:
                if len(q.strip()) > 10:
                    questions.append(q.strip())
        
        # Pattern 3: Look for questions after "What", "How", "Should", etc.
        if not questions:
            question_patterns = [
                r'(What[^?]*\?)',
                r'(How[^?]*\?)',
                r'(Should[^?]*\?)',
                r'(Would[^?]*\?)',
                r'(Is[^?]*\?)',
                r'(Are[^?]*\?)',
                r'(Can[^?]*\?)',
                r'(Could[^?]*\?)'
            ]
            
            for pattern in question_patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                for match in matches:
                    if isinstance(match, tuple):
                        match = match[0]
                    if len(match.strip()) > 10:
                        questions.append(match.strip())
        
        # Remove duplicates while preserving order
        seen = set()
        unique_questions = []
        for q in questions:
            if q not in seen:
                seen.add(q)
                unique_questions.append(q)
        
        questions = unique_questions
        
        # If no questions found, create generic ones
        if not questions:
            logger.info("No questions found in response, using fallback questions")
            questions = [
                "What specific aspects should be covered?",
                "Who is the target audience?",
                "What data should be included?",
                "What format is preferred?",
                "Any specific requirements?"
            ]
        
        logger.info(f"Extracted {len(questions)} questions from text")
        return questions[:5]  # Limit to 5 questions
    
    def submit_clarification(self, writeup_id: int, answers: Dict[str, str]) -> Dict[str, Any]:
        """Submit clarification answers and generate execution plan."""
        try:
            # Store the answers
            def store_answers_db(conn):
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE writeups 
                    SET clarification_answers = %s, status = 'planned'
                    WHERE id = %s
                """, (json.dumps(answers), writeup_id))
                conn.commit()
                return "Answers stored"
            
            result = execute_with_connection(store_answers_db)
            if result['status'] != 'success':
                return {"status": "error", "message": f"Database error: {result['message']}"}
            
            # Generate execution plan
            plan_result = self._generate_execution_plan(writeup_id)
            
            if plan_result.get("status") == "success":
                return {"status": "success", "message": "Clarification submitted and plan generated"}
            else:
                return {"status": "error", "message": "Failed to generate execution plan"}
                
        except Exception as e:
            logger.error(f"Error submitting clarification: {e}")
            return {"status": "error", "message": str(e)}
    
    def _generate_execution_plan(self, writeup_id: int) -> Dict[str, Any]:
        """Generate an execution plan using the LangChain agent."""
        try:
            from agents.langchain_agent.explainer_agent import LangChainExplainerAgent
            
            # Get write-up details
            writeup = self.get_writeup(writeup_id)
            if not writeup:
                return {"status": "error", "message": "Write-up not found"}
            
            original_prompt = writeup.get('original_prompt', '')
            answers = writeup.get('clarification_answers', {})
            
            plan_prompt = f"""
            Based on this write-up request: "{original_prompt}"
            
            And these clarification answers: {json.dumps(answers, indent=2)}
            
            Create a detailed execution plan with the following structure:
            {{
                "overview": "Brief description of what will be accomplished",
                "steps": [
                    {{
                        "step_number": 1,
                        "step_type": "data_gathering",
                        "description": "Gather relevant data and information",
                        "estimated_time": "10 minutes"
                    }},
                    {{
                        "step_number": 2,
                        "step_type": "analysis",
                        "description": "Analyze the data and identify key insights",
                        "estimated_time": "15 minutes"
                    }},
                    {{
                        "step_number": 3,
                        "step_type": "content_generation",
                        "description": "Generate the initial content draft",
                        "estimated_time": "20 minutes"
                    }},
                    {{
                        "step_number": 4,
                        "step_type": "review",
                        "description": "Review and refine the content",
                        "estimated_time": "10 minutes"
                    }}
                ],
                "total_estimated_time": "55 minutes"
            }}
            """
            
            # Create agent instance
            agent = LangChainExplainerAgent(
                model_key=None,  # Use default model
                tool_groups=[],  # No tools needed for planning
                include_all_sections=False,
                enable_session_logging=True
            )
            
            # Get response from agent
            result = agent.explain_change_sync(plan_prompt, metric_details={})
            
            # Extract session ID from the result
            session_id = result.get('session_id') if isinstance(result, dict) else None
            if session_id:
                logger.info(f"Captured session ID for execution plan: {session_id}")
            
            # Extract the actual response text from the agent's result
            if isinstance(result, dict) and 'output' in result and result['output']:
                response = result['output'][0].get('text', '')
            elif isinstance(result, dict) and 'explanation' in result:
                response = result['explanation']
            else:
                response = str(result)
            
            try:
                # Extract JSON from the response (handle cases where there's text before/after JSON)
                import re
                json_match = re.search(r'```json\s*(\{.*?\})\s*```', response, re.DOTALL)
                if json_match:
                    plan_json = json_match.group(1)
                else:
                    # Try to find JSON without code blocks
                    json_match = re.search(r'(\{.*\})', response, re.DOTALL)
                    if json_match:
                        plan_json = json_match.group(1)
                    else:
                        plan_json = response
                
                plan = json.loads(plan_json)
                logger.info(f"Generated execution plan with {len(plan.get('steps', []))} steps")
                
                # Store the plan in the database
                def store_plan_db(conn):
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE writeups 
                        SET execution_plan = %s, status = 'ready_to_execute', session_id = %s
                        WHERE id = %s
                    """, (json.dumps(plan), session_id, writeup_id))
                    
                    # Create step records
                    if "steps" in plan:
                        for step in plan["steps"]:
                            cursor.execute("""
                                INSERT INTO writeup_steps (writeup_id, step_number, step_type, description, status)
                                VALUES (%s, %s, %s, %s, 'pending')
                            """, (writeup_id, step.get("step_number", 0), 
                                 step.get("step_type", "unknown"), 
                                 step.get("description", "")))
                    
                    conn.commit()
                    return "Plan stored"
                
                result = execute_with_connection(store_plan_db)
                if result['status'] == 'success':
                    return {"status": "success", "plan": plan, "session_id": session_id}
                else:
                    logger.error(f"Error storing plan: {result['message']}")
                    return {"status": "error", "message": "Failed to store execution plan"}
                    
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse plan JSON: {e}")
                logger.error(f"Response text: {response[:500]}...")
                return {"status": "error", "message": "Failed to parse execution plan"}
                
        except Exception as e:
            logger.error(f"Error generating execution plan: {e}")
            return {"status": "error", "message": str(e)}
    
    def execute_writeup(self, writeup_id: int) -> Dict[str, Any]:
        """Execute a write-up by running all steps in the plan."""
        try:
            writeup = self.get_writeup(writeup_id)
            if not writeup:
                return {"status": "error", "message": "Write-up not found"}
            
            if writeup.get('status') != 'ready_to_execute':
                return {"status": "error", "message": f"Write-up is not ready to execute (current: {writeup.get('status')})"}
            
            # Get all steps for this write-up
            def get_steps_db(conn):
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, step_number, step_type, description, status
                    FROM writeup_steps 
                    WHERE writeup_id = %s 
                    ORDER BY step_number
                """, (writeup_id,))
                return cursor.fetchall()
            
            result = execute_with_connection(get_steps_db)
            if result['status'] != 'success':
                return {"status": "error", "message": f"Database error: {result['message']}"}
            
            steps = result['result']
            if not steps:
                return {"status": "error", "message": "No execution steps found"}
            
            # Update status to in_progress
            def update_status_db(conn):
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE writeups 
                    SET status = 'in_progress'
                    WHERE id = %s
                """, (writeup_id,))
                conn.commit()
                return "Status updated"
            
            result = execute_with_connection(update_status_db)
            if result['status'] != 'success':
                return {"status": "error", "message": f"Database error: {result['message']}"}
            
            execution_log = []
            
            # Execute each step
            for step in steps:
                step_id, step_number, step_type, description, status = step
                
                def execute_step_db(conn):
                    cursor = conn.cursor()
                    
                    # Update step status to running
                    cursor.execute("""
                        UPDATE writeup_steps 
                        SET status = 'running', started_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (step_id,))
                    conn.commit()
                    
                    # Execute the step
                    step_result = self._execute_step(writeup_id, step_id, step_type, description, writeup)
                    
                    # Update step status
                    if step_result.get("status") == "success":
                        cursor.execute("""
                            UPDATE writeup_steps 
                            SET status = 'completed', completed_at = CURRENT_TIMESTAMP, output_data = %s, session_id = %s
                            WHERE id = %s
                        """, (json.dumps(step_result.get("output", {})), step_result.get("session_id"), step_id))
                        
                        execution_log.append({
                            "step_number": step_number,
                            "status": "completed",
                            "output": step_result.get("output", {})
                        })
                    else:
                        cursor.execute("""
                            UPDATE writeup_steps 
                            SET status = 'failed', completed_at = CURRENT_TIMESTAMP, error_message = %s
                            WHERE id = %s
                        """, (step_result.get("message", "Unknown error"), step_id))
                        
                        execution_log.append({
                            "step_number": step_number,
                            "status": "failed",
                            "error": step_result.get("message", "Unknown error")
                        })
                        
                        # If a step fails, mark the whole write-up as failed
                        cursor.execute("""
                            UPDATE writeups 
                            SET status = 'failed', error_message = %s, execution_log = %s
                            WHERE id = %s
                        """, (f"Step {step_number} failed: {step_result.get('message', 'Unknown error')}", 
                             json.dumps(execution_log), writeup_id))
                        conn.commit()
                        
                        return {"status": "error", "message": f"Step {step_number} failed"}
                    
                    conn.commit()
                    return "Step executed"
                
                result = execute_with_connection(execute_step_db)
                if result['status'] != 'success':
                    return {"status": "error", "message": f"Database error: {result['message']}"}
                
                # Check if the step failed
                if "failed" in result.get('message', ''):
                    return result
            
            # Generate final content
            final_result = self._generate_final_content(writeup_id, writeup, execution_log)
            
            def finalize_writeup_db(conn):
                cursor = conn.cursor()
                if final_result.get("status") == "success":
                    # Update write-up as completed
                    cursor.execute("""
                        UPDATE writeups 
                        SET status = 'completed', final_content = %s, execution_log = %s, session_id = %s
                        WHERE id = %s
                    """, (final_result.get("content", ""), json.dumps(execution_log), 
                         final_result.get("session_id"), writeup_id))
                    conn.commit()
                    
                    return {"status": "success", "content": final_result.get("content", "")}
                else:
                    # Mark as failed
                    cursor.execute("""
                        UPDATE writeups 
                        SET status = 'failed', error_message = %s, execution_log = %s
                        WHERE id = %s
                    """, (final_result.get("message", "Failed to generate final content"), 
                         json.dumps(execution_log), writeup_id))
                    conn.commit()
                    
                    return {"status": "error", "message": final_result.get("message", "Failed to generate final content")}
            
            result = execute_with_connection(finalize_writeup_db)
            return result['result'] if result['status'] == 'success' else result
                
        except Exception as e:
            logger.error(f"Error executing write-up: {e}")
            return {"status": "error", "message": str(e)}
    
    def _execute_step(self, writeup_id: int, step_id: int, step_type: str, description: str, writeup: tuple) -> Dict[str, Any]:
        """Execute a specific step based on its type."""
        if step_type == "data_gathering":
            return self._execute_data_gathering_step(writeup_id, step_id, description, writeup)
        elif step_type == "analysis":
            return self._execute_analysis_step(writeup_id, step_id, description, writeup)
        elif step_type == "content_generation":
            return self._execute_content_generation_step(writeup_id, step_id, description, writeup)
        elif step_type == "review":
            return self._execute_review_step(writeup_id, step_id, description, writeup)
        else:
            return {"status": "error", "message": f"Unknown step type: {step_type}"}
    
    def _execute_data_gathering_step(self, writeup_id: int, step_id: int, description: str, writeup: tuple) -> Dict[str, Any]:
        """Execute a data gathering step."""
        try:
            from agents.langchain_agent.explainer_agent import LangChainExplainerAgent
            from agents.langchain_agent.config.tool_config import ToolGroup
            
            # Create agent instance with data analysis tools
            agent = LangChainExplainerAgent(
                model_key=None,  # Use default model
                tool_groups=[ToolGroup.CORE, ToolGroup.DATA_ANALYSIS],  # Include data tools
                include_all_sections=False,
                enable_session_logging=True
            )
            
            # Get write-up details
            original_prompt = writeup[2]
            
            data_prompt = f"""
            Based on this write-up request: "{original_prompt}"
            
            Step: {description}
            
            Please gather relevant data and information needed for this write-up. Use the available tools to:
            1. Search for relevant datasets
            2. Query metrics and data
            3. Gather any other relevant information
            
            Provide a summary of what data was collected and how it relates to the write-up request.
            """
            
            result = agent.explain_change_sync(data_prompt, metric_details={})
            
            # Extract session ID from the result
            session_id = result.get('session_id') if isinstance(result, dict) else None
            if session_id:
                logger.info(f"Captured session ID for data gathering step: {session_id}")
            
            # Extract the actual response text from the agent's result
            if isinstance(result, dict) and 'output' in result and result['output']:
                response = result['output'][0].get('text', '')
            elif isinstance(result, dict) and 'explanation' in result:
                response = result['explanation']
            else:
                response = str(result)
            
            return {
                "status": "success",
                "output": {
                    "data_sources": ["metrics_db", "external_apis"],
                    "data_collected": response,
                    "timestamp": datetime.now().isoformat()
                },
                "session_id": session_id
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def _execute_analysis_step(self, writeup_id: int, step_id: int, description: str, writeup: tuple) -> Dict[str, Any]:
        """Execute an analysis step."""
        try:
            from agents.langchain_agent.explainer_agent import LangChainExplainerAgent
            from agents.langchain_agent.config.tool_config import ToolGroup
            
            # Create agent instance with analysis tools
            agent = LangChainExplainerAgent(
                model_key=None,  # Use default model
                tool_groups=[ToolGroup.CORE, ToolGroup.ANALYSIS],  # Include analysis tools
                include_all_sections=False,
                enable_session_logging=True
            )
            
            # Get write-up details
            original_prompt = writeup[2]
            
            analysis_prompt = f"""
            Based on this write-up request: "{original_prompt}"
            
            Step: {description}
            
            Please analyze the data and information gathered. Focus on:
            1. Identifying patterns and trends
            2. Identifying key insights
            3. Extracting meaningful findings
            4. Providing analytical context
            
            Provide a detailed analysis with key findings and insights.
            """
            
            result = agent.explain_change_sync(analysis_prompt, metric_details={})
            
            # Extract session ID from the result
            session_id = result.get('session_id') if isinstance(result, dict) else None
            if session_id:
                logger.info(f"Captured session ID for analysis step: {session_id}")
            
            # Extract the actual response text from the agent's result
            if isinstance(result, dict) and 'output' in result and result['output']:
                response = result['output'][0].get('text', '')
            elif isinstance(result, dict) and 'explanation' in result:
                response = result['explanation']
            else:
                response = str(result)
            
            return {
                "status": "success",
                "output": {
                    "analysis_results": response,
                    "key_findings": ["Analysis completed", "Insights extracted"],
                    "timestamp": datetime.now().isoformat()
                },
                "session_id": session_id
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def _execute_content_generation_step(self, writeup_id: int, step_id: int, description: str, writeup: tuple) -> Dict[str, Any]:
        """Execute a content generation step."""
        try:
            from agents.langchain_agent.explainer_agent import LangChainExplainerAgent
            from agents.langchain_agent.config.tool_config import ToolGroup
            
            # Create agent instance with content generation tools
            agent = LangChainExplainerAgent(
                model_key=None,  # Use default model
                tool_groups=[ToolGroup.CORE, ToolGroup.DATA_ANALYSIS],  # Include data tools
                include_all_sections=False,
                enable_session_logging=True
            )
            
            # Get write-up details
            original_prompt = writeup[2]
            output_format = writeup[7]
            
            content_prompt = f"""
            Based on this write-up request: "{original_prompt}"
            Output Format: {output_format}
            
            Step: {description}
            
            Please generate the initial draft content for this write-up. Use the available tools to:
            1. Access any additional data needed
            2. Structure the content appropriately
            3. Create a comprehensive draft
            
            Generate well-structured content that addresses the original prompt.
            """
            
            result = agent.explain_change_sync(content_prompt, metric_details={})
            
            # Extract session ID from the result
            session_id = result.get('session_id') if isinstance(result, dict) else None
            if session_id:
                logger.info(f"Captured session ID for content generation step: {session_id}")
            
            # Extract the actual response text from the agent's result
            if isinstance(result, dict) and 'output' in result and result['output']:
                response = result['output'][0].get('text', '')
            elif isinstance(result, dict) and 'explanation' in result:
                response = result['explanation']
            else:
                response = str(result)
            
            return {
                "status": "success",
                "output": {
                    "content_generated": response,
                    "word_count": len(response.split()),
                    "timestamp": datetime.now().isoformat()
                },
                "session_id": session_id
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def _execute_review_step(self, writeup_id: int, step_id: int, description: str, writeup: tuple) -> Dict[str, Any]:
        """Execute a review step."""
        try:
            from agents.langchain_agent.explainer_agent import LangChainExplainerAgent
            
            # Create agent instance
            agent = LangChainExplainerAgent(
                model_key=None,  # Use default model
                tool_groups=[],  # No tools needed for review
                include_all_sections=False,
                enable_session_logging=True
            )
            
            # Get write-up details
            original_prompt = writeup[2]
            
            review_prompt = f"""
            Based on this write-up request: "{original_prompt}"
            
            Step: {description}
            
            Please review the content generated so far and provide feedback on:
            1. Accuracy and completeness
            2. Grammar and style
            3. Structure and organization
            4. Completeness and accuracy
            5. Alignment with the original request
            
            Provide a summary of improvements made and any final recommendations.
            """
            
            result = agent.explain_change_sync(review_prompt, metric_details={})
            
            # Extract session ID from the result
            session_id = result.get('session_id') if isinstance(result, dict) else None
            if session_id:
                logger.info(f"Captured session ID for review step: {session_id}")
            
            # Extract the actual response text from the agent's result
            if isinstance(result, dict) and 'output' in result and result['output']:
                response = result['output'][0].get('text', '')
            elif isinstance(result, dict) and 'explanation' in result:
                response = result['explanation']
            else:
                response = str(result)
            
            return {
                "status": "success",
                "output": {
                    "review_completed": True,
                    "improvements_made": ["Review completed", "Content refined"],
                    "review_summary": response,
                    "timestamp": datetime.now().isoformat()
                },
                "session_id": session_id
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def _generate_final_content(self, writeup_id: int, writeup: Dict, execution_log: List[Dict]) -> Dict[str, Any]:
        """Generate the final write-up content."""
        try:
            from agents.langchain_agent.explainer_agent import LangChainExplainerAgent
            from agents.langchain_agent.config.tool_config import ToolGroup
            
            # Get write-up details from dictionary
            original_prompt = writeup.get('original_prompt', '')
            output_format = writeup.get('output_format', 'html')
            
            final_prompt = f"""
            Based on this original request: "{original_prompt}"
            
            Output Format: {output_format}
            
            Execution Log:
            {json.dumps(execution_log, indent=2)}
            
            Create a comprehensive, well-structured write-up that addresses the original prompt
            and incorporates all the insights from the execution steps.
            """
            
            # Create agent instance with data analysis tools for final content generation
            agent = LangChainExplainerAgent(
                model_key=None,  # Use default model
                tool_groups=[ToolGroup.CORE, ToolGroup.DATA_ANALYSIS],  # Include data tools
                include_all_sections=False,
                enable_session_logging=True
            )
            
            # Get response from agent
            result = agent.explain_change_sync(final_prompt, metric_details={})
            
            # Extract session ID from the result
            session_id = result.get('session_id') if isinstance(result, dict) else None
            if session_id:
                logger.info(f"Captured session ID for final content: {session_id}")
            
            # Extract the actual response text from the agent's result
            if isinstance(result, dict) and 'output' in result and result['output']:
                content = result['output'][0].get('text', '')
            elif isinstance(result, dict) and 'explanation' in result:
                content = result['explanation']
            else:
                content = str(result)
            
            return {
                "status": "success",
                "content": content,
                "session_id": session_id
            }
            
        except Exception as e:
            logger.error(f"Error generating final content: {e}")
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