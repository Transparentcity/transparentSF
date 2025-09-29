import os
import json
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
import logging
from fastapi import APIRouter, Request, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from ai.tools.db_utils import get_postgres_connection
import psycopg2
import psycopg2.extras
import io

# Initialize APIRouter
router = APIRouter()

# Templates will be set by the main app
templates = None

def set_templates(t):
    """Set the templates instance for this router"""
    global templates
    templates = t
    logging.info("Templates set in database admin router")

# Get logger
logger = logging.getLogger(__name__)

def check_pg_dump_version_compatibility():
    """
    Check if pg_dump is available and get its version.
    Returns tuple: (is_available, version_info, error_message)
    """
    try:
        result = subprocess.run(
            ['pg_dump', '--version'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            version_info = result.stdout.strip()
            logger.info(f"pg_dump version: {version_info}")
            return True, version_info, None
        else:
            return False, None, f"pg_dump --version failed: {result.stderr}"
            
    except FileNotFoundError:
        return False, None, "pg_dump not found in PATH"
    except subprocess.TimeoutExpired:
        return False, None, "pg_dump --version timed out"
    except Exception as e:
        return False, None, f"Error checking pg_dump version: {str(e)}"

def create_python_backup(backup_path):
    """
    Create a database backup using Python/psycopg2 instead of pg_dump.
    This is a fallback when pg_dump is not available in the environment.
    """
    logger.info("Creating Python-based database backup")
    
    connection = get_postgres_connection()
    if not connection:
        raise Exception("Could not connect to database")
    
    try:
        cursor = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        with open(backup_path, 'w', encoding='utf-8') as backup_file:
            # Write header
            backup_file.write("-- Database backup created with Python/psycopg2\n")
            backup_file.write(f"-- Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            backup_file.write("-- This backup includes schema and data for all tables\n\n")
            
            # Set search path
            backup_file.write("SET search_path = public;\n\n")
            
            # Get all tables in the public schema
            cursor.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_type = 'BASE TABLE'
                ORDER BY table_name;
            """)
            tables = cursor.fetchall()
            
            for table in tables:
                table_name = table['table_name']
                logger.info(f"Backing up table: {table_name}")
                
                # Get table structure
                cursor.execute(f"""
                    SELECT column_name, data_type, is_nullable, column_default
                    FROM information_schema.columns 
                    WHERE table_schema = 'public' 
                    AND table_name = '{table_name}'
                    ORDER BY ordinal_position;
                """)
                columns = cursor.fetchall()
                
                # Write DROP TABLE statement
                backup_file.write(f"DROP TABLE IF EXISTS \"{table_name}\" CASCADE;\n")
                
                # Write CREATE TABLE statement
                backup_file.write(f"CREATE TABLE \"{table_name}\" (\n")
                
                column_definitions = []
                for col in columns:
                    col_def = f'    "{col["column_name"]}" {col["data_type"]}'
                    if col["is_nullable"] == "NO":
                        col_def += " NOT NULL"
                    if col["column_default"]:
                        col_def += f" DEFAULT {col['column_default']}"
                    column_definitions.append(col_def)
                
                backup_file.write(",\n".join(column_definitions))
                backup_file.write("\n);\n\n")
                
                # Get and write table data
                cursor.execute(f'SELECT * FROM "{table_name}"')
                rows = cursor.fetchall()
                
                if rows:
                    # Get column names
                    column_names = [col['column_name'] for col in columns]
                    
                    # Write INSERT statements
                    for row in rows:
                        values = []
                        for col_name in column_names:
                            value = row[col_name]
                            if value is None:
                                values.append('NULL')
                            elif isinstance(value, str):
                                # Escape single quotes in strings
                                escaped_value = value.replace("'", "''")
                                values.append(f"'{escaped_value}'")
                            elif isinstance(value, (int, float)):
                                values.append(str(value))
                            elif isinstance(value, bool):
                                values.append('TRUE' if value else 'FALSE')
                            else:
                                # For other types, convert to string and escape
                                escaped_value = str(value).replace("'", "''")
                                values.append(f"'{escaped_value}'")
                        
                        column_list = ', '.join([f'"{name}"' for name in column_names])
                        value_list = ', '.join(values)
                        backup_file.write(f'INSERT INTO "{table_name}" ({column_list}) VALUES ({value_list});\n')
                    
                    backup_file.write("\n")
                
                # Get and write indexes
                cursor.execute(f"""
                    SELECT indexname, indexdef
                    FROM pg_indexes 
                    WHERE schemaname = 'public' 
                    AND tablename = '{table_name}'
                    AND indexname NOT LIKE '%_pkey';
                """)
                indexes = cursor.fetchall()
                
                for index in indexes:
                    backup_file.write(f"{index['indexdef']};\n")
                
                backup_file.write("\n")
            
            # Write footer
            backup_file.write("-- Backup completed\n")
        
        logger.info(f"Python backup completed: {backup_path}")
        
    finally:
        cursor.close()
        connection.close()

@router.get("/database-admin")
async def database_admin_page(request: Request):
    """Serve the database admin interface."""
    logger.debug("Database admin page route called")
    if templates is None:
        logger.error("Templates not initialized in database admin router")
        raise RuntimeError("Templates not initialized")
    
    logger.debug("Serving database_admin.html template")
    return templates.TemplateResponse("database_admin.html", {
        "request": request
    })

@router.get("/api/database-info")
async def get_database_info():
    """Get database information including status, size, and last backup time."""
    logger.debug("Get database info called")
    
    try:
        # Get database connection and basic info
        connection = get_postgres_connection()
        if not connection:
            raise Exception("Could not connect to database")
        
        cursor = connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # Get database size
        cursor.execute("""
            SELECT pg_size_pretty(pg_database_size(current_database())) as size;
        """)
        size_result = cursor.fetchone()
        db_size = size_result['size'] if size_result else 'Unknown'
        
        # Get table count
        cursor.execute("""
            SELECT count(*) as table_count 
            FROM information_schema.tables 
            WHERE table_schema = 'public';
        """)
        table_result = cursor.fetchone()
        table_count = table_result['table_count'] if table_result else 0
        
        cursor.close()
        connection.close()
        
        # Check for last backup file
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        backups_dir = os.path.join(script_dir, 'backups')
        last_backup = "Never"
        
        if os.path.exists(backups_dir):
            backup_files = [f for f in os.listdir(backups_dir) if f.endswith('.sql') or f.endswith('.gz')]
            if backup_files:
                # Get the most recent backup file
                backup_files.sort(key=lambda x: os.path.getmtime(os.path.join(backups_dir, x)), reverse=True)
                latest_backup = backup_files[0]
                backup_time = os.path.getmtime(os.path.join(backups_dir, latest_backup))
                last_backup = datetime.fromtimestamp(backup_time).strftime("%Y-%m-%d %H:%M:%S")
        
        return JSONResponse({
            "status": "success",
            "db_status": f"Connected ({table_count} tables)",
            "db_size": db_size,
            "last_backup": last_backup
        })
        
    except Exception as e:
        logger.error(f"Error getting database info: {str(e)}")
        return JSONResponse({
            "status": "error",
            "db_status": "Connection Error",
            "db_size": "Unknown",
            "last_backup": "Unknown",
            "message": str(e)
        })

@router.post("/api/database-backup")
async def create_database_backup():
    """Create a database backup and return download URL."""
    logger.debug("Create database backup called")
    
    try:
        # Get database connection info
        connection = get_postgres_connection()
        if not connection:
            raise Exception("Could not connect to database")
        
        # Get connection parameters
        conn_params = connection.get_dsn_parameters()
        connection.close()
        
        # Create backups directory if it doesn't exist
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        backups_dir = os.path.join(script_dir, 'backups')
        os.makedirs(backups_dir, exist_ok=True)
        
        # Generate backup filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"database_backup_{timestamp}.sql"
        backup_path = os.path.join(backups_dir, backup_filename)
        
        # Check pg_dump availability and version compatibility first
        pg_dump_available, pg_dump_version, pg_dump_error = check_pg_dump_version_compatibility()
        
        if not pg_dump_available:
            logger.warning(f"pg_dump not available: {pg_dump_error}")
            logger.info("Skipping pg_dump and using Python-based backup directly")
            
            try:
                create_python_backup(backup_path)
                
                # Verify backup file was created
                if not os.path.exists(backup_path):
                    raise Exception("Python backup file was not created")
                
                # Get file size for verification
                file_size = os.path.getsize(backup_path)
                if file_size == 0:
                    raise Exception("Python backup file is empty")
                
                logger.info(f"Database backup created successfully with Python: {backup_path} ({file_size} bytes)")
                
                # Return success with download URL
                return JSONResponse({
                    "status": "success",
                    "message": f"Database backup created successfully with Python fallback ({file_size:,} bytes)",
                    "filename": backup_filename,
                    "download_url": f"/backend/api/download-backup/{backup_filename}",
                    "file_size": file_size,
                    "backup_method": "python"
                })
                
            except Exception as python_backup_error:
                logger.error(f"Python backup failed: {str(python_backup_error)}")
                raise Exception(f"Database backup failed. pg_dump not available ({pg_dump_error}) and Python backup failed: {str(python_backup_error)}")
        
        # Check if DATABASE_URL is available (common for managed services like Replit PostgreSQL)
        database_url = os.getenv("DATABASE_URL")
        
        if database_url:
            # Use DATABASE_URL directly for pg_dump
            logger.info("Using DATABASE_URL for pg_dump")
            pg_dump_cmd = [
                'pg_dump',
                '--verbose',
                '--no-owner',
                '--no-privileges',
                '--clean',
                '--if-exists',
                '--format=plain',
                '--file', backup_path,
                database_url
            ]
            
            # Set environment variables
            env = os.environ.copy()
            
        else:
            # Use individual connection parameters (fallback for local setups)
            logger.info("Using individual connection parameters for pg_dump")
            pg_dump_cmd = [
                'pg_dump',
                '--verbose',
                '--no-owner',
                '--no-privileges',
                '--clean',
                '--if-exists',
                '--format=plain',
                '--file', backup_path
            ]
            
            # Add connection parameters
            if 'host' in conn_params:
                pg_dump_cmd.extend(['--host', conn_params['host']])
            if 'port' in conn_params:
                pg_dump_cmd.extend(['--port', conn_params['port']])
            if 'user' in conn_params:
                pg_dump_cmd.extend(['--username', conn_params['user']])
            if 'dbname' in conn_params:
                pg_dump_cmd.append(conn_params['dbname'])
            
            # Set environment variables for password
            env = os.environ.copy()
            if 'password' in conn_params:
                env['PGPASSWORD'] = conn_params['password']
        
        logger.info(f"Running pg_dump command: {' '.join(pg_dump_cmd[:-2])} [database/file]")
        
        # Try pg_dump first
        try:
            result = subprocess.run(
                pg_dump_cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )
            
            if result.returncode != 0:
                # Check if it's a version mismatch error
                stderr_lower = result.stderr.lower()
                if "version mismatch" in stderr_lower or "server version" in stderr_lower:
                    logger.warning(f"pg_dump version mismatch detected: {result.stderr}")
                    logger.info("Version mismatch between pg_dump client and PostgreSQL server - falling back to Python backup")
                    raise Exception(f"pg_dump version mismatch: {result.stderr}")
                else:
                    raise Exception(f"pg_dump failed: {result.stderr}")
            
            # Verify backup file was created
            if not os.path.exists(backup_path):
                raise Exception("Backup file was not created")
            
            # Get file size for verification
            file_size = os.path.getsize(backup_path)
            if file_size == 0:
                raise Exception("Backup file is empty")
            
            logger.info(f"Database backup created successfully with pg_dump: {backup_path} ({file_size} bytes)")
            
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError, Exception) as e:
            # pg_dump failed or is not available, try Python backup
            logger.warning(f"pg_dump failed or not available: {str(e)}")
            logger.info("Falling back to Python-based backup")
            
            try:
                create_python_backup(backup_path)
                
                # Verify backup file was created
                if not os.path.exists(backup_path):
                    raise Exception("Python backup file was not created")
                
                # Get file size for verification
                file_size = os.path.getsize(backup_path)
                if file_size == 0:
                    raise Exception("Python backup file is empty")
                
                logger.info(f"Database backup created successfully with Python: {backup_path} ({file_size} bytes)")
                
            except Exception as python_backup_error:
                logger.error(f"Python backup also failed: {str(python_backup_error)}")
                raise Exception(f"Both pg_dump and Python backup failed. pg_dump error: {str(e)}, Python backup error: {str(python_backup_error)}")
        
        # Return success with download URL
        return JSONResponse({
            "status": "success",
            "message": f"Database backup created successfully ({file_size:,} bytes)",
            "filename": backup_filename,
            "download_url": f"/backend/api/download-backup/{backup_filename}",
            "file_size": file_size,
            "backup_method": "pg_dump" if pg_dump_available else "python"
        })
        
    except subprocess.TimeoutExpired:
        logger.error("Database backup timed out")
        return JSONResponse({
            "status": "error",
            "message": "Database backup timed out (5 minute limit exceeded)"
        }, status_code=500)
    except Exception as e:
        logger.error(f"Error creating database backup: {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)

@router.get("/api/download-backup/{filename}")
async def download_backup(filename: str):
    """Download a backup file."""
    logger.debug(f"Download backup called for filename: {filename}")
    
    try:
        # Security check - ensure filename is safe
        if '..' in filename or '/' in filename or '\\' in filename:
            raise HTTPException(status_code=400, detail="Invalid filename")
        
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        backups_dir = os.path.join(script_dir, 'backups')
        backup_path = os.path.join(backups_dir, filename)
        
        if not os.path.exists(backup_path):
            raise HTTPException(status_code=404, detail="Backup file not found")
        
        return FileResponse(
            backup_path,
            media_type='application/octet-stream',
            filename=filename
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error downloading backup {filename}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/database-restore")
async def restore_database(backup_file: UploadFile = File(...)):
    """Restore database from uploaded backup file."""
    logger.debug(f"Database restore called with file: {backup_file.filename}")
    
    try:
        # Validate file
        if not backup_file.filename:
            raise Exception("No file provided")
        
        # Check file extension
        allowed_extensions = ['.sql', '.gz', '.tar', '.zip']
        file_ext = os.path.splitext(backup_file.filename)[1].lower()
        if file_ext not in allowed_extensions:
            raise Exception(f"Unsupported file type: {file_ext}. Allowed types: {', '.join(allowed_extensions)}")
        
        # Create temporary file for the upload
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as temp_file:
            # Read and save uploaded file
            contents = await backup_file.read()
            temp_file.write(contents)
            temp_file_path = temp_file.name
        
        logger.info(f"Uploaded file saved to temporary location: {temp_file_path}")
        
        try:
            # Get database connection info
            connection = get_postgres_connection()
            if not connection:
                raise Exception("Could not connect to database")
            
            conn_params = connection.get_dsn_parameters()
            connection.close()
            
            # Handle different file types
            if file_ext == '.sql':
                # Direct SQL file
                sql_file_path = temp_file_path
            elif file_ext == '.gz':
                # Gzipped SQL file - decompress it
                import gzip
                sql_file_path = temp_file_path + '_decompressed.sql'
                with gzip.open(temp_file_path, 'rb') as f_in:
                    with open(sql_file_path, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
            else:
                raise Exception(f"File type {file_ext} not yet supported for restore")
            
            # Verify SQL file exists and has content
            if not os.path.exists(sql_file_path):
                raise Exception("Could not process backup file")
            
            file_size = os.path.getsize(sql_file_path)
            if file_size == 0:
                raise Exception("Backup file appears to be empty")
            
            logger.info(f"Processing SQL file: {sql_file_path} ({file_size} bytes)")
            
            # Check if DATABASE_URL is available (common for managed services like Replit PostgreSQL)
            database_url = os.getenv("DATABASE_URL")
            
            if database_url:
                # Use DATABASE_URL directly for psql
                logger.info("Using DATABASE_URL for psql restore")
                psql_cmd = [
                    'psql',
                    '--quiet',
                    '--file', sql_file_path,
                    database_url
                ]
                
                # Set environment variables
                env = os.environ.copy()
                
            else:
                # Use individual connection parameters (fallback for local setups)
                logger.info("Using individual connection parameters for psql restore")
                psql_cmd = [
                    'psql',
                    '--quiet',
                    '--file', sql_file_path
                ]
                
                # Add connection parameters
                if 'host' in conn_params:
                    psql_cmd.extend(['--host', conn_params['host']])
                if 'port' in conn_params:
                    psql_cmd.extend(['--port', conn_params['port']])
                if 'user' in conn_params:
                    psql_cmd.extend(['--username', conn_params['user']])
                if 'dbname' in conn_params:
                    psql_cmd.extend(['--dbname', conn_params['dbname']])
                
                # Set environment variables for password
                env = os.environ.copy()
                if 'password' in conn_params:
                    env['PGPASSWORD'] = conn_params['password']
            
            logger.info(f"Running psql restore command")
            
            # Run psql restore
            result = subprocess.run(
                psql_cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=600  # 10 minute timeout
            )
            
            if result.returncode != 0:
                logger.error(f"Database restore failed: {result.stderr}")
                raise Exception(f"Database restore failed: {result.stderr}")
            
            logger.info("Database restore completed successfully")
            
            return JSONResponse({
                "status": "success",
                "message": f"Database restored successfully from {backup_file.filename}",
                "restored_size": file_size
            })
            
        finally:
            # Clean up temporary files
            try:
                if os.path.exists(temp_file_path):
                    os.unlink(temp_file_path)
                if file_ext == '.gz' and 'sql_file_path' in locals() and sql_file_path != temp_file_path:
                    if os.path.exists(sql_file_path):
                        os.unlink(sql_file_path)
            except Exception as cleanup_error:
                logger.warning(f"Error cleaning up temporary files: {cleanup_error}")
        
    except subprocess.TimeoutExpired:
        logger.error("Database restore timed out")
        return JSONResponse({
            "status": "error",
            "message": "Database restore timed out (10 minute limit exceeded)"
        }, status_code=500)
    except Exception as e:
        logger.error(f"Error restoring database: {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)

@router.get("/api/session-logs-info")
async def get_session_logs_info():
    """Get information about session logs including count and latest session."""
    logger.debug("Get session logs info called")
    
    try:
        # Get the path to the session logs directory
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        logs_dir = os.path.join(script_dir, 'logs', 'sessions')
        
        total_sessions = 0
        latest_session = None
        logs_directory = logs_dir
        
        if os.path.exists(logs_dir):
            # Count session log files (all JSON files in sessions directory)
            session_files = [f for f in os.listdir(logs_dir) if f.endswith('.json')]
            total_sessions = len(session_files)
            
            # Find the latest session
            if session_files:
                session_files.sort(key=lambda x: os.path.getmtime(os.path.join(logs_dir, x)), reverse=True)
                latest_file = session_files[0]
                latest_time = os.path.getmtime(os.path.join(logs_dir, latest_file))
                latest_session = datetime.fromtimestamp(latest_time).strftime("%Y-%m-%d %H:%M:%S")
        else:
            logs_directory = "Directory not found"
        
        return JSONResponse({
            "status": "success",
            "total_sessions": total_sessions,
            "latest_session": latest_session,
            "logs_directory": logs_directory
        })
        
    except Exception as e:
        logger.error(f"Error getting session logs info: {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)

@router.get("/api/download-session-logs")
async def download_session_logs():
    """Download all session logs as a ZIP file."""
    logger.debug("Download session logs called")
    
    try:
        # Get the path to the session logs directory
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        logs_dir = os.path.join(script_dir, 'logs', 'sessions')
        
        if not os.path.exists(logs_dir):
            return JSONResponse({
                "status": "error",
                "message": "Session logs directory not found"
            }, status_code=404)
        
        # Find all session log files (all JSON files in sessions directory)
        session_files = [f for f in os.listdir(logs_dir) if f.endswith('.json')]
        
        if not session_files:
            return JSONResponse({
                "status": "error",
                "message": "No session log files found"
            }, status_code=404)
        
        # Create a temporary ZIP file
        temp_zip_path = tempfile.mktemp(suffix='.zip')
        
        with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for session_file in session_files:
                file_path = os.path.join(logs_dir, session_file)
                # Add file to ZIP with just the filename (no path)
                zipf.write(file_path, session_file)
        
        # Return the ZIP file
        return FileResponse(
            path=temp_zip_path,
            filename=f"session_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename=session_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            }
        )
        
    except Exception as e:
        logger.error(f"Error downloading session logs: {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)

@router.post("/api/admin/reload-business-vacancy-table")
async def reload_business_vacancy_table(limit: int = None):
    """Reload the business-vacancy table with location-based business data as a background job"""
    try:
        from background_jobs import job_manager
        import asyncio
        
        # Create a background job for the table reload
        job_id = job_manager.create_job(
            "location_business_reload", 
            f"Reload location-based business data (limit: {limit or 'unlimited'})"
        )
        logger.info(f"Created job {job_id} for location-based business data reload")
        
        # Start the reload in the background
        task = asyncio.create_task(_run_location_business_reload_job(job_id, limit))
        
        # Add error handling for the task
        def task_done_callback(task):
            try:
                if task.exception():
                    logger.error(f"Location business reload task failed: {task.exception()}")
            except Exception as e:
                logger.error(f"Error in task callback: {e}")
        
        task.add_done_callback(task_done_callback)
        
        return JSONResponse({
            "status": "success",
            "message": "Location-based business data reload started as background job",
            "job_id": job_id
        })
        
    except Exception as e:
        logger.error(f"Error starting location business reload job: {e}")
        return JSONResponse({
            "status": "error",
            "message": f"Error starting reload job: {str(e)}"
        }, status_code=500)

async def _run_location_business_reload_job(job_id: str, limit: int = None):
    """Run the location-based business data reload as a background job."""
    try:
        from background_jobs import job_manager
        
        logger.info(f"=== STARTING LOCATION-BASED BUSINESS DATA RELOAD JOB ===")
        logger.info(f"Job ID: {job_id}")
        logger.info(f"Limit: {limit or 'unlimited'}")
        
        # Get the job and mark it as running
        job = job_manager.get_job(job_id)
        if not job:
            logger.error(f"Job {job_id} not found in job manager")
            return
            
        job.start()
        job.update_progress(5)
        logger.info(f"Job {job_id} started, progress: 5%")
        
        # Import the new SOQL-based processor
        from ai.tools.soql_business_processor import SOQLBusinessProcessor
        
        job.update_progress(10)
        logger.info(f"Job {job_id} progress: 10% - Initializing location-based processor")
        
        # Create processor instance
        processor = SOQLBusinessProcessor()
        
        job.update_progress(20)
        logger.info(f"Job {job_id} progress: 20% - Starting SOQL-based data processing")
        
        # Process data with progress updates
        def process_with_progress():
            try:
                # Use the SOQL-based approach
                result = processor.process_all_data(clear_existing=True)
                
                # Get final statistics
                job.update_progress(80)
                logger.info(f"Job {job_id} progress: 80% - Processing complete, getting statistics")
                
                # Return statistics
                stats = {
                    'total_locations': result.get('total_locations', 0),
                    'open_locations': result.get('open_locations', 0),
                    'closed_locations': result.get('closed_locations', 0),
                    'total_errors': result.get('total_errors', 0),
                    'duration_seconds': result.get('duration_seconds', 0),
                    'method': 'soql_based_approach'
                }
                
                logger.info(f"Job {job_id} - Final stats: {stats}")
                return stats
                
            except Exception as e:
                logger.error(f"Error in SOQL-based processor: {e}")
                import traceback
                logger.error(f"Full traceback: {traceback.format_exc()}")
                raise
        
        # Run the processor in a thread pool to avoid blocking
        import asyncio
        loop = asyncio.get_event_loop()
        
        try:
            result = await loop.run_in_executor(None, process_with_progress)
        except Exception as e:
            logger.error(f"Error in executor for job {job_id}: {e}")
            import traceback
            logger.error(f"Executor traceback: {traceback.format_exc()}")
            raise
        
        job.update_progress(95)
        logger.info(f"Job {job_id} progress: 95% - Processing complete, finalizing")
        
        # Complete the job
        job.complete(result)
        logger.info(f"Job {job_id} completed successfully")
        
    except Exception as e:
        logger.error(f"Error in location business reload job {job_id}: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        
        # Mark job as failed
        job = job_manager.get_job(job_id)
        if job:
            job.fail(str(e))

@router.get("/api/admin/business-vacancy-stats")
async def get_business_vacancy_stats():
    """Get statistics about the location_business table"""
    try:
        import psycopg2
        import os
        
        conn = psycopg2.connect(os.getenv('DATABASE_URL'))
        with conn.cursor() as cur:
            # Get total count
            cur.execute("SELECT COUNT(*) FROM location_business")
            total_count = cur.fetchone()[0]
            
            # Get count by open/closed status
            cur.execute("""
                SELECT is_open, COUNT(*) as count 
                FROM location_business 
                GROUP BY is_open 
                ORDER BY is_open
            """)
            status_stats = [{"status": "Open" if row[0] else "Closed", "count": row[1]} for row in cur.fetchall()]
            
            # Get count by district
            cur.execute("""
                SELECT supervisor_district, COUNT(*) as count 
                FROM location_business 
                WHERE supervisor_district IS NOT NULL 
                GROUP BY supervisor_district 
                ORDER BY supervisor_district
            """)
            district_stats = [{"district": row[0], "count": row[1]} for row in cur.fetchall()]
            
            # Get count by corridor
            cur.execute("""
                SELECT business_corridor, COUNT(*) as count 
                FROM location_business 
                WHERE business_corridor IS NOT NULL 
                GROUP BY business_corridor 
                ORDER BY count DESC 
                LIMIT 20
            """)
            corridor_stats = [{"corridor": row[0], "count": row[1]} for row in cur.fetchall()]
            
            # Get recent updates
            cur.execute("""
                SELECT MAX(updated_at) as last_updated, MIN(created_at) as first_created 
                FROM location_business
            """)
            update_info = cur.fetchone()
            
        conn.close()
        
        return JSONResponse({
            "status": "success",
            "stats": {
                "total_count": total_count,
                "status_stats": status_stats,
                "district_stats": district_stats,
                "corridor_stats": corridor_stats,
                "last_updated": update_info[0].isoformat() if update_info[0] else None,
                "first_created": update_info[1].isoformat() if update_info[1] else None
            }
        })
        
    except Exception as e:
        logger.error(f"Error getting business-vacancy stats: {e}")
        return JSONResponse({
            "status": "error",
            "message": f"Error getting business-vacancy stats: {str(e)}"
        }, status_code=500)

@router.post("/api/admin/reload-proven-vacancy-table")
async def reload_proven_vacancy_table(limit: int = None):
    """Reload the proven vacancy table using the proven logic from working real-time code"""
    try:
        from background_jobs import job_manager
        import asyncio
        
        # Create a background job for the table reload
        job_id = job_manager.create_job(
            "proven_vacancy_reload", 
            f"Reload proven vacancy data using working logic (limit: {limit or 'unlimited'})"
        )
        logger.info(f"Created job {job_id} for proven vacancy data reload")
        
        # Start the reload in the background
        task = asyncio.create_task(_run_proven_vacancy_reload_job(job_id, limit))
        
        # Add error handling for the task
        def task_done_callback(task):
            try:
                if task.exception():
                    logger.error(f"Proven vacancy reload task failed: {task.exception()}")
            except Exception as e:
                logger.error(f"Error in task callback: {e}")
        
        task.add_done_callback(task_done_callback)
        
        return JSONResponse({
            "status": "success",
            "message": "Proven vacancy data reload started as background job",
            "job_id": job_id
        })
        
    except Exception as e:
        logger.error(f"Error starting proven vacancy reload job: {e}")
        return JSONResponse({
            "status": "error",
            "message": f"Error starting proven vacancy reload job: {str(e)}"
        }, status_code=500)

async def _run_proven_vacancy_reload_job(job_id: str, limit: int = None):
    """Run the proven vacancy data reload as a background job."""
    try:
        from background_jobs import job_manager
        
        logger.info(f"=== STARTING PROVEN VACANCY DATA RELOAD JOB ===")
        logger.info(f"Job ID: {job_id}")
        logger.info(f"Limit: {limit or 'unlimited'}")
        
        # Get the job and mark it as running
        job = job_manager.get_job(job_id)
        if not job:
            logger.error(f"Job {job_id} not found in job manager")
            return
            
        job.start()
        job.update_progress(5)
        logger.info(f"Job {job_id} started, progress: 5%")
        
        # Import the proven processor
        from ai.tools.proven_vacancy_processor import ProvenVacancyProcessor
        
        job.update_progress(10)
        logger.info(f"Job {job_id} progress: 10% - Initializing proven processor")
        
        # Create processor instance
        processor = ProvenVacancyProcessor()
        
        job.update_progress(20)
        logger.info(f"Job {job_id} progress: 20% - Starting proven data processing")
        
        # Process data with progress updates
        def process_with_progress():
            try:
                # Use the proven approach
                result = processor.process_all_data(limit=limit, clear_existing=True)
                
                # Get final statistics
                job.update_progress(80)
                logger.info(f"Job {job_id} progress: 80% - Processing complete, getting statistics")
                
                # Return statistics
                stats = {
                    'total_locations': result.get('total_locations', 0),
                    'open_locations': result.get('open_locations', 0),
                    'closed_locations': result.get('closed_locations', 0),
                    'total_errors': result.get('total_errors', 0),
                    'duration_seconds': result.get('duration_seconds', 0),
                    'method': 'proven_approach'
                }
                
                logger.info(f"Job {job_id} - Final stats: {stats}")
                return stats
                
            except Exception as e:
                logger.error(f"Error in proven processor: {e}")
                import traceback
                logger.error(f"Full traceback: {traceback.format_exc()}")
                raise
        
        # Run the processor in a thread pool to avoid blocking
        import asyncio
        loop = asyncio.get_event_loop()
        
        try:
            result = await loop.run_in_executor(None, process_with_progress)
        except Exception as e:
            logger.error(f"Error in executor for job {job_id}: {e}")
            import traceback
            logger.error(f"Executor traceback: {traceback.format_exc()}")
            raise
        
        job.update_progress(95)
        logger.info(f"Job {job_id} progress: 95% - Processing complete, finalizing")
        
        # Complete the job
        job.complete(result)
        logger.info(f"Job {job_id} completed successfully")
        
    except Exception as e:
        logger.error(f"Error in proven vacancy reload job {job_id}: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        
        # Mark job as failed
        job = job_manager.get_job(job_id)
        if job:
            job.fail(str(e))

@router.get("/api/admin/proven-vacancy-stats")
async def get_proven_vacancy_stats():
    """Get statistics about the proven_vacancy table"""
    try:
        import psycopg2
        import os
        
        conn = psycopg2.connect(os.getenv('DATABASE_URL'))
        with conn.cursor() as cur:
            # Get total count
            cur.execute("SELECT COUNT(*) FROM proven_vacancy")
            total_count = cur.fetchone()[0]
            
            # Get status counts
            cur.execute("SELECT status, COUNT(*) FROM proven_vacancy GROUP BY status")
            status_counts = dict(cur.fetchall())
            
            # Get district counts
            cur.execute("""
                SELECT supervisor_district, COUNT(*) 
                FROM proven_vacancy 
                WHERE supervisor_district IS NOT NULL 
                GROUP BY supervisor_district 
                ORDER BY COUNT(*) DESC 
                LIMIT 10
            """)
            district_counts = dict(cur.fetchall())
            
            # Get corridor counts
            cur.execute("""
                SELECT business_corridor, COUNT(*) 
                FROM proven_vacancy 
                WHERE business_corridor IS NOT NULL AND business_corridor != ''
                GROUP BY business_corridor 
                ORDER BY COUNT(*) DESC 
                LIMIT 10
            """)
            corridor_counts = dict(cur.fetchall())
            
            # Get update info
            cur.execute("""
                SELECT 
                    MIN(created_at) as first_created,
                    MAX(updated_at) as last_updated
                FROM proven_vacancy
            """)
            update_info = cur.fetchone()
        
        conn.close()
        
        return JSONResponse({
            "status": "success",
            "stats": {
                "total_locations": total_count,
                "status_breakdown": status_counts,
                "top_districts": district_counts,
                "top_corridors": corridor_counts,
                "last_updated": update_info[0].isoformat() if update_info[0] else None,
                "first_created": update_info[1].isoformat() if update_info[1] else None
            }
        })
        
    except Exception as e:
        logger.error(f"Error getting proven vacancy stats: {e}")
        return JSONResponse({
            "status": "error",
            "message": f"Error getting proven vacancy stats: {str(e)}"
        }, status_code=500)

@router.post("/api/admin/reload-exact-realtime-vacancy-table")
async def reload_exact_realtime_vacancy_table(limit: int = None):
    """Reload the exact real-time vacancy table using the exact same logic as true real-time analysis"""
    try:
        from background_jobs import job_manager
        import asyncio
        
        # Create a background job
        job_id = job_manager.create_job(
            "exact_realtime_vacancy_reload", 
            f"Reload exact real-time vacancy table{' (limit: ' + str(limit) + ')' if limit else ''}"
        )
        
        async def process_job():
            try:
                from ai.tools.exact_realtime_processor import ExactRealtimeProcessor
                
                processor = ExactRealtimeProcessor()
                
                # Create table
                processor.create_table()
                
                # Process data
                result = processor.process_data(limit=limit)
                
                # Get the job and mark it as complete
                job = job_manager.get_job(job_id)
                if job:
                    job.complete({
                        "message": "Exact real-time vacancy data reload completed successfully",
                        "result": result
                    })
                
            except Exception as e:
                logger.error(f"Error in exact real-time vacancy reload job: {e}")
                # Mark job as failed
                job = job_manager.get_job(job_id)
                if job:
                    job.fail(str(e))
        
        # Start the job
        asyncio.create_task(process_job())
        
        return JSONResponse({
            "status": "success",
            "message": "Exact real-time vacancy data reload started as background job",
            "job_id": job_id
        })
        
    except Exception as e:
        logger.error(f"Error starting exact real-time vacancy reload job: {e}")
        return JSONResponse({
            "status": "error",
            "message": f"Error starting exact real-time vacancy reload job: {str(e)}"
        }, status_code=500)

@router.get("/api/admin/exact-realtime-vacancy-stats")
async def get_exact_realtime_vacancy_stats():
    """Get statistics about the exact_realtime_vacancy table"""
    try:
        from ai.tools.exact_realtime_processor import ExactRealtimeProcessor
        
        processor = ExactRealtimeProcessor()
        stats = processor.get_stats()
        
        return JSONResponse({
            "status": "success",
            "stats": stats
        })
        
    except Exception as e:
        logger.error(f"Error getting exact real-time vacancy stats: {e}")
        return JSONResponse({
            "status": "error",
            "message": f"Error getting exact real-time vacancy stats: {str(e)}"
        }, status_code=500)