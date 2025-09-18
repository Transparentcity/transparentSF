#!/usr/bin/env python3
"""
Legal Code Vector Processing

This module processes legal documents for vector storage and semantic search.
Integrates with the existing Qdrant infrastructure for legal code analysis.
"""

import os
import sys
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
import uuid

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.db_utils import get_postgres_connection
from prep_data import get_embedding
from qdrant_client import QdrantClient
from qdrant_client.http import models as rest

logger = logging.getLogger(__name__)

class LegalVectorProcessor:
    """Process legal documents for vector storage and analysis"""
    
    def __init__(self, qdrant_url: str = "localhost", qdrant_port: int = 6333):
        self.qdrant_client = QdrantClient(host=qdrant_url, port=qdrant_port)
        self.collection_name = "sf_legal_code"
        self.vector_size = 3072  # OpenAI text-embedding-3-large dimension
        self.db_connection = None
        
    def connect_db(self):
        """Connect to database"""
        self.db_connection = get_postgres_connection()
        
    def setup_collection(self):
        """Create or recreate the legal code collection"""
        try:
            # Delete existing collection if it exists
            try:
                self.qdrant_client.delete_collection(self.collection_name)
                logger.info(f"Deleted existing collection: {self.collection_name}")
            except Exception:
                pass  # Collection doesn't exist
            
            # Create new collection
            self.qdrant_client.create_collection(
                collection_name=self.collection_name,
                vectors_config=rest.VectorParams(
                    size=self.vector_size,
                    distance=rest.Distance.COSINE
                )
            )
            logger.info(f"Created collection: {self.collection_name}")
            
        except Exception as e:
            logger.error(f"Error setting up collection: {e}")
            raise
    
    def process_legal_documents(self, limit: Optional[int] = None) -> int:
        """Process legal documents from database into vector storage"""
        if not self.db_connection:
            self.connect_db()
            
        if not self.db_connection:
            logger.error("No database connection available")
            return 0
        
        try:
            # Fetch legal documents from database
            with self.db_connection.cursor() as cursor:
                query = """
                    SELECT id, title, content, document_type, source, url,
                           effective_date, enactment_number, file_number,
                           section_number, chapter, metadata
                    FROM legal_documents
                    WHERE content IS NOT NULL AND content != ''
                    ORDER BY effective_date DESC NULLS LAST
                """
                
                if limit:
                    query += f" LIMIT {limit}"
                
                cursor.execute(query)
                documents = cursor.fetchall()
                
                logger.info(f"Processing {len(documents)} legal documents for vector storage")
                
                return self._process_documents_batch(documents)
                
        except Exception as e:
            logger.error(f"Error processing legal documents: {e}")
            return 0
    
    def _process_documents_batch(self, documents: List[tuple]) -> int:
        """Process a batch of documents into vectors"""
        points_to_upsert = []
        processed_count = 0
        
        for doc_data in documents:
            try:
                (doc_id, title, content, doc_type, source, url,
                 effective_date, enactment_number, file_number,
                 section_number, chapter, metadata) = doc_data
                
                # Create combined text for embedding with all metadata
                combined_text = self._create_embedding_text(
                    title, content, doc_type, section_number, chapter,
                    enactment_number, file_number, 
                    effective_date.isoformat() if effective_date else None
                )
                
                # Generate embedding
                embedding = get_embedding(combined_text)
                if not embedding:
                    logger.warning(f"Failed to generate embedding for document {doc_id}")
                    continue
                
                # Extract additional metadata for enhanced search
                content_words = len(content.split()) if content else 0
                content_chars = len(content) if content else 0
                
                # Determine legal area/topic from title and chapter
                legal_topics = self._extract_legal_topics(title, chapter, content)
                
                # Create comprehensive payload for Qdrant
                payload = {
                    # Core document info
                    "document_id": doc_id,
                    "title": title,
                    "content": content[:15000],  # Increased content size for better context
                    "content_preview": content[:500] + "..." if len(content) > 500 else content,
                    
                    # Document classification
                    "document_type": doc_type,
                    "source": source,
                    "legal_topics": legal_topics,
                    
                    # Legal structure metadata
                    "chapter": chapter or "",
                    "section_number": section_number or "",
                    "enactment_number": enactment_number or "",
                    "file_number": file_number or "",
                    
                    # Temporal metadata
                    "effective_date": effective_date.isoformat() if effective_date else None,
                    "effective_year": effective_date.year if effective_date else None,
                    "effective_month": effective_date.month if effective_date else None,
                    
                    # Content analysis
                    "content_length": content_chars,
                    "content_words": content_words,
                    "has_substantial_content": content_chars > 100,
                    
                    # URLs and references
                    "url": url or "",
                    "has_url": bool(url),
                    
                    # Processing metadata
                    "processed_at": datetime.now().isoformat(),
                    "embedding_model": "text-embedding-3-large",
                    "vector_version": "1.0",
                    
                    # Original metadata (if any)
                    "original_metadata": metadata or {},
                    
                    # Search optimization fields
                    "searchable_text": f"{title} {chapter} {content}".lower(),
                    "is_municipal_code": doc_type == 'municipal_code',
                    "is_ordinance": doc_type == 'ordinance',
                    "is_recent": self._is_recent_document(effective_date) if effective_date else False
                }
                
                # Create point for Qdrant
                point = rest.PointStruct(
                    id=str(uuid.uuid4()),
                    vector=embedding,
                    payload=payload
                )
                
                points_to_upsert.append(point)
                processed_count += 1
                
                # Process in batches of 100
                if len(points_to_upsert) >= 100:
                    self._upsert_points(points_to_upsert)
                    points_to_upsert = []
                
            except Exception as e:
                logger.error(f"Error processing document {doc_data[0]}: {e}")
                continue
        
        # Process remaining points
        if points_to_upsert:
            self._upsert_points(points_to_upsert)
        
        logger.info(f"Successfully processed {processed_count} legal documents into vectors")
        return processed_count
    
    def _create_embedding_text(self, title: str, content: str, doc_type: str,
                              section_number: str = None, chapter: str = None, 
                              enactment_number: str = None, file_number: str = None,
                              effective_date: str = None) -> str:
        """Create optimized text for embedding generation with rich context"""
        parts = []
        
        # Add document type context with more detail
        if doc_type == 'municipal_code':
            parts.append("San Francisco Municipal Code Section")
            if chapter:
                parts.append(f"Code: {chapter}")
            if section_number:
                parts.append(f"Section: {section_number}")
        elif doc_type == 'ordinance':
            parts.append("San Francisco Board of Supervisors Ordinance")
            if enactment_number:
                parts.append(f"Ordinance Number: {enactment_number}")
            if file_number:
                parts.append(f"File Number: {file_number}")
            if effective_date:
                parts.append(f"Effective Date: {effective_date}")
        
        # Add title with emphasis
        if title:
            parts.append(f"Title: {title}")
        
        # Add content with legal context
        if content:
            # Clean and limit content for better embedding
            cleaned_content = content.replace('\n', ' ').strip()
            max_content_length = 5000  # Leave room for metadata
            
            if len(cleaned_content) > max_content_length:
                # Try to break at sentence boundaries
                truncated = cleaned_content[:max_content_length]
                last_period = truncated.rfind('.')
                if last_period > max_content_length * 0.8:  # If we find a period in the last 20%
                    cleaned_content = truncated[:last_period + 1]
                else:
                    cleaned_content = truncated + "..."
            
            parts.append(f"Legal Text: {cleaned_content}")
        
        return "\n\n".join(parts)
    
    def _extract_legal_topics(self, title: str, chapter: str, content: str) -> List[str]:
        """Extract legal topics/areas from document content for enhanced search"""
        topics = []
        
        # Combine text for analysis
        text_to_analyze = f"{title} {chapter} {content}".lower()
        
        # Define topic keywords mapping
        topic_keywords = {
            "housing": ["housing", "residential", "tenant", "landlord", "rent", "eviction", "habitability"],
            "zoning": ["zoning", "land use", "development", "planning", "building height", "density"],
            "business": ["business", "commercial", "license", "permit", "tax", "revenue", "registration"],
            "environment": ["environment", "green", "sustainability", "climate", "pollution", "waste", "energy"],
            "health": ["health", "safety", "sanitation", "food", "medical", "hospital", "public health"],
            "transportation": ["transportation", "traffic", "parking", "transit", "vehicle", "street", "road"],
            "public_safety": ["police", "fire", "emergency", "safety", "security", "crime", "enforcement"],
            "administration": ["administrative", "government", "department", "procedure", "process", "management"],
            "finance": ["budget", "finance", "fiscal", "revenue", "expenditure", "funding", "appropriation"],
            "employment": ["employment", "worker", "employee", "labor", "wage", "workplace", "job"],
            "utilities": ["utility", "water", "sewer", "electric", "gas", "infrastructure", "public works"],
            "social_services": ["social", "human services", "welfare", "assistance", "support", "community"]
        }
        
        # Check for topic matches
        for topic, keywords in topic_keywords.items():
            if any(keyword in text_to_analyze for keyword in keywords):
                topics.append(topic)
        
        # Add chapter-based topics
        if chapter:
            chapter_lower = chapter.lower()
            if "planning" in chapter_lower:
                topics.append("planning")
            if "health" in chapter_lower:
                topics.append("health")
            if "police" in chapter_lower:
                topics.append("public_safety")
            if "fire" in chapter_lower:
                topics.append("public_safety")
            if "environment" in chapter_lower:
                topics.append("environment")
            if "business" in chapter_lower or "tax" in chapter_lower:
                topics.append("business")
            if "transportation" in chapter_lower:
                topics.append("transportation")
            if "housing" in chapter_lower:
                topics.append("housing")
        
        return list(set(topics))  # Remove duplicates
    
    def _is_recent_document(self, effective_date) -> bool:
        """Determine if a document is recent (within last 2 years)"""
        if not effective_date:
            return False
        
        from datetime import timedelta
        two_years_ago = datetime.now() - timedelta(days=730)
        return effective_date >= two_years_ago
    
    def _upsert_points(self, points: List[rest.PointStruct]):
        """Upsert points to Qdrant collection"""
        try:
            self.qdrant_client.upsert(
                collection_name=self.collection_name,
                points=points
            )
            logger.debug(f"Upserted {len(points)} points to collection")
            
        except Exception as e:
            logger.error(f"Error upserting points to Qdrant: {e}")
            raise
    
    def search_legal_code(self, query: str, limit: int = 10, 
                         document_type: str = None, legal_topics: List[str] = None,
                         recent_only: bool = False, effective_year: int = None) -> List[Dict[str, Any]]:
        """Enhanced search with metadata filtering"""
        try:
            # Generate query embedding
            query_embedding = get_embedding(query)
            if not query_embedding:
                logger.error("Failed to generate query embedding")
                return []
            
            # Build advanced search filter
            filter_conditions = []
            
            if document_type:
                filter_conditions.append(
                    rest.FieldCondition(
                        key="document_type",
                        match=rest.MatchValue(value=document_type)
                    )
                )
            
            if legal_topics:
                # Search for documents that match any of the specified topics
                topic_conditions = [
                    rest.FieldCondition(
                        key="legal_topics",
                        match=rest.MatchAny(any=legal_topics)
                    )
                ]
                filter_conditions.extend(topic_conditions)
            
            if recent_only:
                filter_conditions.append(
                    rest.FieldCondition(
                        key="is_recent",
                        match=rest.MatchValue(value=True)
                    )
                )
            
            if effective_year:
                filter_conditions.append(
                    rest.FieldCondition(
                        key="effective_year",
                        match=rest.MatchValue(value=effective_year)
                    )
                )
            
            search_filter = None
            if filter_conditions:
                search_filter = rest.Filter(must=filter_conditions)
            
            # Search in Qdrant
            search_results = self.qdrant_client.search(
                collection_name=self.collection_name,
                query_vector=query_embedding,
                query_filter=search_filter,
                limit=limit,
                with_payload=True,
                with_vectors=False
            )
            
            # Format enhanced results
            results = []
            for result in search_results:
                payload = result.payload
                results.append({
                    "score": result.score,
                    "document_id": payload.get("document_id"),
                    "title": payload.get("title"),
                    "document_type": payload.get("document_type"),
                    "section_number": payload.get("section_number"),
                    "chapter": payload.get("chapter"),
                    "effective_date": payload.get("effective_date"),
                    "enactment_number": payload.get("enactment_number"),
                    "file_number": payload.get("file_number"),
                    "content_preview": payload.get("content_preview", ""),
                    "url": payload.get("url"),
                    "source": payload.get("source"),
                    "legal_topics": payload.get("legal_topics", []),
                    "content_length": payload.get("content_length", 0),
                    "content_words": payload.get("content_words", 0),
                    "is_recent": payload.get("is_recent", False),
                    "processed_at": payload.get("processed_at")
                })
            
            return results
            
        except Exception as e:
            logger.error(f"Error searching legal code: {e}")
            return []
    
    def search_by_topic(self, topic: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search legal documents by specific legal topic"""
        return self.search_legal_code(
            query=f"legal regulations about {topic}",
            limit=limit,
            legal_topics=[topic]
        )
    
    def search_recent_ordinances(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search only recent ordinances"""
        return self.search_legal_code(
            query=query,
            limit=limit,
            document_type="ordinance",
            recent_only=True
        )
    
    def get_documents_by_chapter(self, chapter: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get all documents from a specific code chapter"""
        try:
            search_filter = rest.Filter(
                must=[
                    rest.FieldCondition(
                        key="chapter",
                        match=rest.MatchValue(value=chapter)
                    )
                ]
            )
            
            # Use scroll to get all documents from a chapter
            scroll_result = self.qdrant_client.scroll(
                collection_name=self.collection_name,
                scroll_filter=search_filter,
                limit=limit,
                with_payload=True,
                with_vectors=False
            )
            
            results = []
            for point in scroll_result[0]:
                payload = point.payload
                results.append({
                    "document_id": payload.get("document_id"),
                    "title": payload.get("title"),
                    "section_number": payload.get("section_number"),
                    "content_preview": payload.get("content_preview", ""),
                    "url": payload.get("url"),
                    "legal_topics": payload.get("legal_topics", [])
                })
            
            return results
            
        except Exception as e:
            logger.error(f"Error getting documents by chapter: {e}")
            return []
    
    def get_collection_stats(self) -> Dict[str, Any]:
        """Get comprehensive statistics about the legal code collection with metadata analysis"""
        try:
            collection_info = self.qdrant_client.get_collection(self.collection_name)
            
            # Get comprehensive metadata breakdown
            scroll_result = self.qdrant_client.scroll(
                collection_name=self.collection_name,
                limit=10000,
                with_payload=True,
                with_vectors=False
            )
            
            # Analyze all documents
            doc_type_counts = {}
            chapter_counts = {}
            topic_counts = {}
            source_counts = {}
            recent_count = 0
            total_content_length = 0
            years_distribution = {}
            
            for point in scroll_result[0]:
                payload = point.payload
                
                # Document type breakdown
                doc_type = payload.get("document_type", "unknown")
                doc_type_counts[doc_type] = doc_type_counts.get(doc_type, 0) + 1
                
                # Chapter breakdown
                chapter = payload.get("chapter", "Unknown")
                chapter_counts[chapter] = chapter_counts.get(chapter, 0) + 1
                
                # Legal topics breakdown
                topics = payload.get("legal_topics", [])
                for topic in topics:
                    topic_counts[topic] = topic_counts.get(topic, 0) + 1
                
                # Source breakdown
                source = payload.get("source", "unknown")
                source_counts[source] = source_counts.get(source, 0) + 1
                
                # Recent documents count
                if payload.get("is_recent", False):
                    recent_count += 1
                
                # Content analysis
                content_length = payload.get("content_length", 0)
                total_content_length += content_length
                
                # Year distribution
                year = payload.get("effective_year")
                if year:
                    years_distribution[year] = years_distribution.get(year, 0) + 1
            
            # Calculate averages
            total_docs = collection_info.points_count
            avg_content_length = total_content_length / total_docs if total_docs > 0 else 0
            
            return {
                "total_documents": total_docs,
                "vector_size": collection_info.config.params.vectors.size,
                "distance_metric": collection_info.config.params.vectors.distance,
                "collection_status": collection_info.status,
                
                # Document classification
                "document_types": doc_type_counts,
                "chapters": dict(sorted(chapter_counts.items(), key=lambda x: x[1], reverse=True)[:10]),
                "sources": source_counts,
                
                # Content analysis
                "legal_topics": dict(sorted(topic_counts.items(), key=lambda x: x[1], reverse=True)[:15]),
                "recent_documents": recent_count,
                "years_distribution": dict(sorted(years_distribution.items(), reverse=True)),
                
                # Content statistics
                "average_content_length": int(avg_content_length),
                "total_content_length": total_content_length,
                
                # Processing info
                "last_updated": datetime.now().isoformat(),
                "embedding_model": "text-embedding-ada-002"
            }
            
        except Exception as e:
            logger.error(f"Error getting collection stats: {e}")
            return {}
    
    def close(self):
        """Close database connection"""
        if self.db_connection:
            self.db_connection.close()

def main():
    """Command line interface for legal vector processing"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Process legal documents for vector search')
    parser.add_argument('--setup', action='store_true', help='Setup/recreate collection')
    parser.add_argument('--process', action='store_true', help='Process documents into vectors')
    parser.add_argument('--limit', type=int, help='Limit number of documents to process')
    parser.add_argument('--search', type=str, help='Search query')
    parser.add_argument('--stats', action='store_true', help='Show collection statistics')
    parser.add_argument('--qdrant-url', default='localhost', help='Qdrant host')
    parser.add_argument('--qdrant-port', type=int, default=6333, help='Qdrant port')
    
    args = parser.parse_args()
    
    processor = LegalVectorProcessor(args.qdrant_url, args.qdrant_port)
    
    try:
        if args.setup:
            logger.info("Setting up legal code collection...")
            processor.setup_collection()
            
        if args.process:
            logger.info("Processing legal documents...")
            count = processor.process_legal_documents(args.limit)
            logger.info(f"Processed {count} documents")
            
        if args.search:
            logger.info(f"Searching for: {args.search}")
            results = processor.search_legal_code(args.search)
            for i, result in enumerate(results, 1):
                print(f"\n{i}. {result['title']} (Score: {result['score']:.3f})")
                print(f"   Type: {result['document_type']}")
                if result['section_number']:
                    print(f"   Section: {result['section_number']}")
                print(f"   Preview: {result['content_preview']}")
                
        if args.stats:
            stats = processor.get_collection_stats()
            print("\nLegal Code Collection Statistics:")
            print(json.dumps(stats, indent=2))
            
    finally:
        processor.close()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main()

