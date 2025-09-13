"""
Write-ups Scheduler
Handles scheduled execution of write-ups and automated processing.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any
import os

# Set up logging
logger = logging.getLogger(__name__)

class WriteupsScheduler:
    """Scheduler for automated write-up execution."""
    
    def __init__(self):
        """Initialize the write-ups scheduler."""
        self.running = False
        self.check_interval = 300  # Check every 5 minutes
    
    async def start(self):
        """Start the scheduler."""
        if self.running:
            logger.warning("Write-ups scheduler is already running")
            return
        
        self.running = True
        logger.info("Starting write-ups scheduler")
        
        while self.running:
            try:
                await self._check_scheduled_writeups()
                await asyncio.sleep(self.check_interval)
            except Exception as e:
                logger.error(f"Error in write-ups scheduler: {e}")
                await asyncio.sleep(60)  # Wait 1 minute before retrying
    
    def stop(self):
        """Stop the scheduler."""
        self.running = False
        logger.info("Stopping write-ups scheduler")
    
    async def _check_scheduled_writeups(self):
        """Check for write-ups that need to be executed."""
        try:
            from tools.writeups_manager import WriteupsManager
            
            writeup_manager = WriteupsManager()
            scheduled_writeups = writeup_manager.get_scheduled_writeups()
            
            if not scheduled_writeups:
                logger.debug("No scheduled write-ups found")
                return
            
            logger.info(f"Found {len(scheduled_writeups)} scheduled write-ups to process")
            
            for writeup in scheduled_writeups:
                await self._process_scheduled_writeup(writeup)
                
        except Exception as e:
            logger.error(f"Error checking scheduled write-ups: {e}")
    
    async def _process_scheduled_writeup(self, writeup: Dict[str, Any]):
        """Process a single scheduled write-up."""
        try:
            writeup_id = writeup['id']
            title = writeup['title']
            frequency = writeup['frequency']
            
            logger.info(f"Processing scheduled write-up {writeup_id}: {title}")
            
            # Import the writeup manager
            from tools.writeups_manager import WriteupsManager
            from background_jobs import job_manager
            
            writeup_manager = WriteupsManager()
            
            # Create a background job for execution
            job_id = job_manager.create_job(
                job_type="scheduled_writeup_execution",
                description=f"Executing scheduled write-up {writeup_id}: {title}"
            )
            
            # Start the execution in the background
            asyncio.create_task(
                job_manager.run_job(
                    job_id,
                    self._execute_scheduled_writeup,
                    writeup_id,
                    writeup_manager
                )
            )
            
            # Update the next scheduled time based on frequency
            await self._update_next_scheduled_time(writeup_id, frequency)
            
        except Exception as e:
            logger.error(f"Error processing scheduled write-up {writeup.get('id', 'unknown')}: {e}")
    
    def _execute_scheduled_writeup(self, writeup_id: int, writeup_manager):
        """Execute a scheduled write-up."""
        try:
            logger.info(f"Executing scheduled write-up {writeup_id}")
            
            # Execute the write-up
            result = writeup_manager.execute_writeup(writeup_id)
            
            if result.get("status") == "success":
                logger.info(f"Successfully executed scheduled write-up {writeup_id}")
                
                # Handle output delivery if configured
                self._deliver_writeup_output(writeup_id, result.get("content", ""))
                
            else:
                logger.error(f"Failed to execute scheduled write-up {writeup_id}: {result.get('message', 'Unknown error')}")
                
        except Exception as e:
            logger.error(f"Error executing scheduled write-up {writeup_id}: {e}")
    
    def _deliver_writeup_output(self, writeup_id: int, content: str):
        """Deliver the write-up output to the configured destination."""
        try:
            from tools.writeups_manager import WriteupsManager
            
            writeup_manager = WriteupsManager()
            writeup = writeup_manager.get_writeup(writeup_id)
            
            if not writeup:
                logger.error(f"Write-up {writeup_id} not found for output delivery")
                return
            
            output_format = writeup.get('output_format', 'html')
            output_destination = writeup.get('output_destination', '')
            
            if not output_destination:
                logger.info(f"No output destination configured for write-up {writeup_id}")
                return
            
            # Import output manager
            from tools.writeups_output_manager import WriteupsOutputManager
            
            output_manager = WriteupsOutputManager()
            
            if output_format == 'email':
                # Send email
                result = output_manager.send_email(
                    to=output_destination,
                    subject=f"Write-up: {writeup.get('title', 'Untitled')}",
                    content=content,
                    format='html'
                )
                
                if result.get("status") == "success":
                    logger.info(f"Successfully sent email for write-up {writeup_id}")
                else:
                    logger.error(f"Failed to send email for write-up {writeup_id}: {result.get('message')}")
                    
            elif output_format == 'file':
                # Save to file
                result = output_manager.save_to_file(
                    file_path=output_destination,
                    content=content,
                    format=output_format
                )
                
                if result.get("status") == "success":
                    logger.info(f"Successfully saved file for write-up {writeup_id}")
                else:
                    logger.error(f"Failed to save file for write-up {writeup_id}: {result.get('message')}")
                    
            else:
                logger.info(f"Output format {output_format} not yet implemented for delivery")
                
        except Exception as e:
            logger.error(f"Error delivering write-up output {writeup_id}: {e}")
    
    async def _update_next_scheduled_time(self, writeup_id: int, frequency: str):
        """Update the next scheduled time for a recurring write-up."""
        try:
            from tools.writeups_manager import WriteupsManager
            
            writeup_manager = WriteupsManager()
            writeup = writeup_manager.get_writeup(writeup_id)
            
            if not writeup:
                logger.error(f"Write-up {writeup_id} not found for scheduling update")
                return
            
            if frequency == 'one_time':
                # One-time write-ups don't need rescheduling
                return
            
            # Calculate next scheduled time
            now = datetime.now()
            next_scheduled = None
            
            if frequency == 'daily':
                next_scheduled = now + timedelta(days=1)
            elif frequency == 'weekly':
                next_scheduled = now + timedelta(weeks=1)
            elif frequency == 'monthly':
                # Add approximately one month
                next_scheduled = now + timedelta(days=30)
            
            if next_scheduled:
                # Update the scheduled time in the database
                import sqlite3
                import os
                
                script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                db_path = os.path.join(script_dir, 'metrics.db')
                
                with sqlite3.connect(db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE writeups 
                        SET scheduled_for = ?, status = 'planned'
                        WHERE id = ?
                    """, (next_scheduled, writeup_id))
                    conn.commit()
                
                logger.info(f"Updated next scheduled time for write-up {writeup_id} to {next_scheduled}")
                
        except Exception as e:
            logger.error(f"Error updating next scheduled time for write-up {writeup_id}: {e}")
    
    async def create_scheduled_writeup(self, title: str, prompt: str, frequency: str, 
                                     output_format: str = "html", output_destination: str = "",
                                     scheduled_for: datetime = None) -> Dict[str, Any]:
        """Create a new scheduled write-up."""
        try:
            from tools.writeups_manager import WriteupsManager
            
            writeup_manager = WriteupsManager()
            
            # If no specific time is provided, schedule for the next appropriate interval
            if not scheduled_for:
                now = datetime.now()
                if frequency == 'daily':
                    scheduled_for = now + timedelta(days=1)
                elif frequency == 'weekly':
                    scheduled_for = now + timedelta(weeks=1)
                elif frequency == 'monthly':
                    scheduled_for = now + timedelta(days=30)
                else:
                    scheduled_for = now + timedelta(minutes=5)  # Default to 5 minutes from now
            
            result = writeup_manager.create_writeup(
                title=title,
                original_prompt=prompt,
                output_format=output_format,
                output_destination=output_destination,
                frequency=frequency,
                scheduled_for=scheduled_for.isoformat()
            )
            
            if result.get("status") == "success":
                logger.info(f"Created scheduled write-up {result.get('writeup_id')}: {title}")
            
            return result
            
        except Exception as e:
            logger.error(f"Error creating scheduled write-up: {e}")
            return {"status": "error", "message": str(e)}
    
    async def get_scheduler_status(self) -> Dict[str, Any]:
        """Get the current status of the scheduler."""
        try:
            from tools.writeups_manager import WriteupsManager
            
            writeup_manager = WriteupsManager()
            scheduled_writeups = writeup_manager.get_scheduled_writeups()
            
            return {
                "status": "success",
                "running": self.running,
                "check_interval": self.check_interval,
                "scheduled_writeups_count": len(scheduled_writeups),
                "next_check_in": f"{self.check_interval} seconds"
            }
            
        except Exception as e:
            logger.error(f"Error getting scheduler status: {e}")
            return {"status": "error", "message": str(e)}

# Global scheduler instance
scheduler = WriteupsScheduler()

async def start_writeups_scheduler():
    """Start the write-ups scheduler."""
    await scheduler.start()

def stop_writeups_scheduler():
    """Stop the write-ups scheduler."""
    scheduler.stop()

async def create_scheduled_writeup(title: str, prompt: str, frequency: str, 
                                 output_format: str = "html", output_destination: str = "",
                                 scheduled_for: datetime = None) -> Dict[str, Any]:
    """Create a new scheduled write-up."""
    return await scheduler.create_scheduled_writeup(
        title, prompt, frequency, output_format, output_destination, scheduled_for
    )

async def get_scheduler_status() -> Dict[str, Any]:
    """Get the current status of the scheduler."""
    return await scheduler.get_scheduler_status()

