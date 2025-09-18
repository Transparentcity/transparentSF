"""
Legal Code Routes

FastAPI routes for San Francisco legal code and ordinance functionality.
Provides endpoints for ingestion, search, and analysis of municipal code and ordinances.
"""

import os
import sys
import json
import logging
import subprocess
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.legal_code_ingestion import LegalCodeDatabase
from tools.db_utils import get_postgres_connection
from tools.gcs_storage import GCSStorageManager

# Import vector processor optionally (requires OpenAI API key)
try:
    from tools.legal_vector_processor import LegalVectorProcessor
    VECTOR_PROCESSOR_AVAILABLE = True
except Exception as e:
    logger.warning(f"Vector processor not available: {e}")
    LegalVectorProcessor = None
    VECTOR_PROCESSOR_AVAILABLE = False

logger = logging.getLogger(__name__)

# Create router
router = APIRouter()

# Global templates variable (set by main app)
templates = None

def set_templates(t):
    """Set templates for this router"""
    global templates
    templates = t
    logger.info("Templates set in legal_code router")

# Pydantic models
class LegalSearchRequest(BaseModel):
    query: str = Field(..., description="Search query for legal code")
    document_type: Optional[str] = Field(None, description="Filter by document type (municipal_code, ordinance)")
    limit: int = Field(10, description="Maximum number of results", ge=1, le=50)

class LegalSearchResult(BaseModel):
    score: float
    document_id: str
    title: str
    document_type: str
    section_number: Optional[str] = None
    chapter: Optional[str] = None
    effective_date: Optional[str] = None
    enactment_number: Optional[str] = None
    content_preview: str
    url: Optional[str] = None
    source: str

class IngestionRequest(BaseModel):
    municipal_code: bool = Field(False, description="Ingest municipal code")
    ordinances: bool = Field(False, description="Ingest recent ordinances")
    ordinance_limit: int = Field(50, description="Limit for ordinances", ge=1, le=100)
    process_vectors: bool = Field(True, description="Process documents for vector search")

class LegalStats(BaseModel):
    total_documents: int
    document_types: Dict[str, int]
    recent_ordinances: int
    municipal_code_sections: int
    last_updated: Optional[str] = None

# Routes

@router.get("/legal/dashboard")
async def legal_dashboard(request: Request):
    """Serve the legal code management dashboard"""
    return templates.TemplateResponse("legal_dashboard.html", {"request": request})

@router.get("/backend/legal-dashboard")
async def backend_legal_dashboard(request: Request):
    """Serve the legal code management dashboard via backend route"""
    return templates.TemplateResponse("legal_dashboard.html", {"request": request})

@router.get("/legal/stats", response_model=LegalStats)
async def get_legal_stats():
    """Get statistics about the legal code database"""
    try:
        connection = get_postgres_connection()
        if not connection:
            raise HTTPException(status_code=500, detail="Database connection failed")
        
        with connection.cursor() as cursor:
            # Get total document count
            cursor.execute("SELECT COUNT(*) FROM legal_documents")
            total_docs = cursor.fetchone()[0]
            
            # Get document type breakdown
            cursor.execute("""
                SELECT document_type, COUNT(*) 
                FROM legal_documents 
                GROUP BY document_type
            """)
            doc_type_counts = dict(cursor.fetchall())
            
            # Get recent ordinances count (last 2 years)
            two_years_ago = datetime.now() - timedelta(days=730)
            cursor.execute("""
                SELECT COUNT(*) FROM legal_documents 
                WHERE document_type = 'ordinance' 
                AND effective_date >= %s
            """, (two_years_ago,))
            recent_ordinances = cursor.fetchone()[0]
            
            # Get municipal code sections count
            municipal_sections = doc_type_counts.get('municipal_code', 0)
            
            # Get last updated timestamp
            cursor.execute("""
                SELECT MAX(updated_at) FROM legal_documents
            """)
            last_updated_row = cursor.fetchone()
            last_updated = last_updated_row[0].isoformat() if last_updated_row[0] else None
            
        connection.close()
        
        return LegalStats(
            total_documents=total_docs,
            document_types=doc_type_counts,
            recent_ordinances=recent_ordinances,
            municipal_code_sections=municipal_sections,
            last_updated=last_updated
        )
        
    except Exception as e:
        logger.error(f"Error getting legal stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/legal/search")
async def search_legal_code(request: LegalSearchRequest) -> List[LegalSearchResult]:
    """Search legal code using semantic similarity"""
    if not VECTOR_PROCESSOR_AVAILABLE:
        raise HTTPException(status_code=503, detail="Vector search not available - OpenAI API key required")
    
    try:
        processor = LegalVectorProcessor()
        results = processor.search_legal_code(
            query=request.query,
            limit=request.limit,
            document_type=request.document_type
        )
        
        return [LegalSearchResult(**result) for result in results]
        
    except Exception as e:
        logger.error(f"Error searching legal code: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/legal/document/{document_id}")
async def get_legal_document(document_id: str):
    """Get full legal document by ID"""
    try:
        connection = get_postgres_connection()
        if not connection:
            raise HTTPException(status_code=500, detail="Database connection failed")
        
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT id, title, content, document_type, source, url,
                       effective_date, enactment_number, file_number,
                       section_number, chapter, metadata, ingested_at
                FROM legal_documents 
                WHERE id = %s
            """, (document_id,))
            
            doc = cursor.fetchone()
            if not doc:
                raise HTTPException(status_code=404, detail="Document not found")
            
            (doc_id, title, content, doc_type, source, url,
             effective_date, enactment_number, file_number,
             section_number, chapter, metadata, ingested_at) = doc
            
        connection.close()
        
        return {
            "id": doc_id,
            "title": title,
            "content": content,
            "document_type": doc_type,
            "source": source,
            "url": url,
            "effective_date": effective_date.isoformat() if effective_date else None,
            "enactment_number": enactment_number,
            "file_number": file_number,
            "section_number": section_number,
            "chapter": chapter,
            "metadata": metadata,
            "ingested_at": ingested_at.isoformat() if ingested_at else None
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting document {document_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/legal/ingest")
async def ingest_legal_code(request: IngestionRequest, background_tasks: BackgroundTasks):
    """Trigger legal code ingestion process"""
    if not request.municipal_code and not request.ordinances:
        raise HTTPException(
            status_code=400, 
            detail="Must specify either municipal_code or ordinances (or both)"
        )
    
    try:
        # Run ingestion in background
        background_tasks.add_task(
            _run_ingestion_task,
            request.municipal_code,
            request.ordinances,
            request.ordinance_limit,
            request.process_vectors
        )
        
        return JSONResponse({
            "status": "started",
            "message": "Legal code ingestion started in background",
            "municipal_code": request.municipal_code,
            "ordinances": request.ordinances,
            "ordinance_limit": request.ordinance_limit,
            "process_vectors": request.process_vectors
        })
        
    except Exception as e:
        logger.error(f"Error starting ingestion: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/legal/process-vectors")
async def process_legal_vectors(
    background_tasks: BackgroundTasks,
    limit: Optional[int] = Query(None, description="Limit number of documents to process")
):
    """Process legal documents for vector search"""
    try:
        background_tasks.add_task(_run_vector_processing_task, limit)
        
        return JSONResponse({
            "status": "started",
            "message": "Legal document vector processing started in background",
            "limit": limit
        })
        
    except Exception as e:
        logger.error(f"Error starting vector processing: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/legal/documents")
async def get_all_legal_documents(
    limit: int = Query(50, description="Number of documents to return", ge=1, le=200),
    document_type: Optional[str] = Query(None, description="Filter by document type"),
    offset: int = Query(0, description="Offset for pagination", ge=0)
):
    """Get all legal documents with pagination"""
    try:
        connection = get_postgres_connection()
        if not connection:
            raise HTTPException(status_code=500, detail="Database connection failed")
        
        # Build query with optional filtering
        where_clause = ""
        params = []
        
        if document_type:
            where_clause = "WHERE document_type = %s"
            params.append(document_type)
        
        with connection.cursor() as cursor:
            cursor.execute(f"""
                SELECT id, title, document_type, chapter, effective_date, 
                       enactment_number, file_number, section_number, url, ingested_at
                FROM legal_documents 
                {where_clause}
                ORDER BY effective_date DESC NULLS LAST, ingested_at DESC
                LIMIT %s OFFSET %s
            """, params + [limit, offset])
            
            documents = []
            for row in cursor.fetchall():
                (doc_id, title, doc_type, chapter, effective_date, 
                 enactment_number, file_number, section_number, url, ingested_at) = row
                
                documents.append({
                    "id": doc_id,
                    "title": title,
                    "document_type": doc_type,
                    "chapter": chapter,
                    "effective_date": effective_date.isoformat() if effective_date else None,
                    "enactment_number": enactment_number,
                    "file_number": file_number,
                    "section_number": section_number,
                    "url": url,
                    "ingested_at": ingested_at.isoformat() if ingested_at else None
                })
        
        connection.close()
        return documents
        
    except Exception as e:
        logger.error(f"Error getting legal documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/legal/recent-ordinances")
async def get_recent_ordinances(
    limit: int = Query(20, description="Number of ordinances to return", ge=1, le=100),
    days: int = Query(365, description="Days back to search", ge=1, le=1095)
):
    """Get recent ordinances"""
    try:
        connection = get_postgres_connection()
        if not connection:
            raise HTTPException(status_code=500, detail="Database connection failed")
        
        cutoff_date = datetime.now() - timedelta(days=days)
        
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT id, title, effective_date, enactment_number, 
                       file_number, url, metadata
                FROM legal_documents 
                WHERE document_type = 'ordinance' 
                AND (effective_date >= %s OR effective_date IS NULL)
                ORDER BY effective_date DESC NULLS LAST, ingested_at DESC
                LIMIT %s
            """, (cutoff_date, limit))
            
            ordinances = []
            for row in cursor.fetchall():
                (doc_id, title, effective_date, enactment_number, 
                 file_number, url, metadata) = row
                
                ordinances.append({
                    "id": doc_id,
                    "title": title,
                    "effective_date": effective_date.isoformat() if effective_date else None,
                    "enactment_number": enactment_number,
                    "file_number": file_number,
                    "url": url,
                    "metadata": metadata
                })
        
        connection.close()
        return ordinances
        
    except Exception as e:
        logger.error(f"Error getting recent ordinances: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/legal/vector-stats")
async def get_vector_stats():
    """Get vector collection statistics"""
    try:
        processor = LegalVectorProcessor()
        stats = processor.get_collection_stats()
        return stats
        
    except Exception as e:
        logger.error(f"Error getting vector stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Background task functions

async def _run_ingestion_task(municipal_code: bool, ordinances: bool, 
                             ordinance_limit: int, process_vectors: bool):
    """Background task for legal code ingestion"""
    try:
        script_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "tools", "legal_code_ingestion.py"
        )
        
        cmd = [sys.executable, script_path]
        
        if municipal_code:
            cmd.append("--municipal-code")
        if ordinances:
            cmd.append("--ordinances")
            cmd.extend(["--limit", str(ordinance_limit)])
        
        logger.info(f"Running ingestion command: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600  # 1 hour timeout
        )
        
        if result.returncode == 0:
            logger.info("Legal code ingestion completed successfully")
            if process_vectors:
                await _run_vector_processing_task(None)
        else:
            logger.error(f"Legal code ingestion failed: {result.stderr}")
            
    except Exception as e:
        logger.error(f"Error in ingestion task: {e}")

async def _run_vector_processing_task(limit: Optional[int]):
    """Background task for vector processing"""
    try:
        script_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "tools", "legal_vector_processor.py"
        )
        
        cmd = [sys.executable, script_path, "--setup", "--process"]
        
        if limit:
            cmd.extend(["--limit", str(limit)])
        
        logger.info(f"Running vector processing command: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600  # 1 hour timeout
        )
        
        if result.returncode == 0:
            logger.info("Legal vector processing completed successfully")
        else:
            logger.error(f"Legal vector processing failed: {result.stderr}")
            
    except Exception as e:
        logger.error(f"Error in vector processing task: {e}")

# Health check
@router.get("/legal/files")
async def list_legal_files():
    """List stored legal files from GCS and local storage"""
    try:
        gcs_manager = GCSStorageManager()
        files = []
        
        # Try to list files from GCS first
        if gcs_manager.gcs_enabled:
            try:
                blobs = gcs_manager.client.list_blobs(
                    gcs_manager.bucket_name, 
                    prefix="legal/"
                )
                
                for blob in blobs:
                    if blob.name.endswith('.json'):
                        files.append({
                            "name": blob.name.split('/')[-1],
                            "path": blob.name,
                            "size": blob.size,
                            "created": blob.time_created.isoformat() if blob.time_created else None,
                            "updated": blob.updated.isoformat() if blob.updated else None,
                            "storage": "gcs"
                        })
            except Exception as e:
                logger.warning(f"Could not list GCS files: {e}")
        
        # Also check local storage
        local_legal_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "legal"
        )
        
        if os.path.exists(local_legal_dir):
            for filename in os.listdir(local_legal_dir):
                if filename.endswith('.json'):
                    filepath = os.path.join(local_legal_dir, filename)
                    stat = os.stat(filepath)
                    
                    files.append({
                        "name": filename,
                        "path": filepath,
                        "size": stat.st_size,
                        "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                        "updated": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "storage": "local"
                    })
        
        # Sort by creation time (newest first)
        files.sort(key=lambda x: x['created'] or '', reverse=True)
        
        return {
            "files": files,
            "total_count": len(files),
            "gcs_enabled": gcs_manager.gcs_enabled
        }
        
    except Exception as e:
        logger.error(f"Error listing legal files: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/legal/files/{filename}")
async def get_legal_file(filename: str):
    """Retrieve a specific legal file from storage"""
    try:
        gcs_manager = GCSStorageManager()
        
        # Try GCS first
        if gcs_manager.gcs_enabled:
            try:
                content = gcs_manager.retrieve_file("legal", filename=filename)
                if content:
                    return {
                        "filename": filename,
                        "content": content,
                        "source": "gcs"
                    }
            except Exception as e:
                logger.warning(f"Could not retrieve from GCS: {e}")
        
        # Try local storage
        local_legal_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "legal"
        )
        
        filepath = os.path.join(local_legal_dir, filename)
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                content = json.load(f)
            
            return {
                "filename": filename,
                "content": content,
                "source": "local"
            }
        
        raise HTTPException(status_code=404, detail="File not found")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving legal file {filename}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/legal/health")
async def legal_health_check():
    """Health check for legal code system"""
    try:
        # Check database connection
        db_status = "ok"
        try:
            connection = get_postgres_connection()
            if connection:
                connection.close()
            else:
                db_status = "failed"
        except Exception:
            db_status = "failed"
        
        # Check vector collection
        vector_status = "ok"
        try:
            processor = LegalVectorProcessor()
            stats = processor.get_collection_stats()
            if not stats:
                vector_status = "no_collection"
        except Exception:
            vector_status = "failed"
        
        # Check GCS connection
        gcs_status = "disabled"
        try:
            gcs_manager = GCSStorageManager()
            if gcs_manager.gcs_enabled:
                gcs_status = "ok"
            else:
                gcs_status = "not_configured"
        except Exception:
            gcs_status = "failed"
        
        return {
            "status": "healthy" if db_status == "ok" else "degraded",
            "database": db_status,
            "vectors": vector_status,
            "gcs": gcs_status,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error in health check: {e}")
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }
