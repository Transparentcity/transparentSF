import os
import sys
import json
import re
import time
import uuid
import logging
from openai import OpenAI
import qdrant_client
from qdrant_client.http import models as rest
from dotenv import load_dotenv
import tiktoken  # For counting tokens
from tools.data_processing import format_columns, serialize_columns, convert_to_timestamp
import shutil

# ------------------------------
# Configure Logging
# ------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("logs/vector_loader.log", mode='w'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

# ------------------------------
# Load environment variables
# ------------------------------
load_dotenv()

openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    logger.error("OpenAI API key not found in environment variables.")
    raise ValueError("OpenAI API key not found in environment variables.")

client = OpenAI(api_key=openai_api_key)

# ------------------------------
# Model Configuration
# ------------------------------
EMBEDDING_MODEL = "text-embedding-3-large"

# ------------------------------
# Qdrant Setup
# ------------------------------
try:
    qdrant = qdrant_client.QdrantClient(host='localhost', port=6333)
    logger.info("Connected to Qdrant at localhost:6333")
except Exception as e:
    logger.error(f"Failed to connect to Qdrant: {e}")
    raise

def split_text_into_chunks(text, max_tokens=8192):
    """
    Splits text into chunks that fit within the token limit.
    Uses tiktoken to count tokens accurately.
    """
    tokenizer = tiktoken.encoding_for_model(EMBEDDING_MODEL)
    tokens = tokenizer.encode(text)
    total_tokens = len(tokens)
    logger.debug(f"Total tokens in text: {total_tokens}")

    chunks = []
    for i in range(0, total_tokens, max_tokens):
        chunk_tokens = tokens[i:i + max_tokens]
        chunk_text = tokenizer.decode(chunk_tokens)
        chunks.append(chunk_text)
        logger.debug(f"Created chunk {len(chunks)} with tokens {i} to {i + len(chunk_tokens)}")

    logger.info(f"Split text into {len(chunks)} chunks.")
    return chunks

def get_embedding(text, retries=3, delay=5):
    """
    Generate embeddings for text, handling chunking for large inputs.
    Returns the average embedding of all chunks.
    """
    embeddings = []
    chunks = list(split_text_into_chunks(text, max_tokens=8192))
    logger.info(f"Generating embeddings for {len(chunks)} chunks.")
    
    for idx, chunk in enumerate(chunks, start=1):
        logger.debug(f"Processing chunk {idx}/{len(chunks)}")
        for attempt in range(1, retries + 1):
            try:
                response = client.embeddings.create(
                    model=EMBEDDING_MODEL, 
                    input=chunk
                )
                embedding = response.data[0].embedding
                embeddings.append(embedding)
                logger.debug(f"Successfully generated embedding for chunk {idx}")
                break  # If successful, exit retry loop
            except Exception as e:
                logger.warning(f"Attempt {attempt} - Error generating embedding for chunk {idx}: {e}")
                if attempt < retries:
                    logger.info(f"Retrying in {delay} seconds...")
                    time.sleep(delay)
                else:
                    logger.error(f"Max retries reached for chunk {idx}. Skipping chunk.")
        else:
            logger.error(f"Failed to generate embedding for chunk {idx} after {retries} attempts.")
    
    if not embeddings:
        logger.error("No embeddings were generated.")
        return None
    
    # Average embeddings across chunks
    averaged_embedding = [sum(col) / len(col) for col in zip(*embeddings)]
    logger.info("Averaged embeddings across all chunks.")
    return averaged_embedding

def recreate_collection(collection_name, vector_size):
    """Delete if exists and recreate the collection."""
    try:
        # Check if collection exists
        if qdrant.collection_exists(collection_name):
            logger.info(f"Collection '{collection_name}' exists, deleting...")
            qdrant.delete_collection(collection_name)
            time.sleep(2)  # Wait for deletion to complete
            logger.info(f"Collection '{collection_name}' deleted.")
        
        # Create new collection
        logger.info(f"Creating collection '{collection_name}' with vector size {vector_size}")
        qdrant.create_collection(
            collection_name=collection_name,
            vectors_config=rest.VectorParams(
                distance=rest.Distance.COSINE,
                size=vector_size,
            ),
            timeout=60  # Increase timeout to allow for directory cleanup
        )
        logger.info(f"Collection '{collection_name}' created successfully.")
        
    except Exception as e:
        logger.error(f"Failed to recreate collection '{collection_name}': {e}")
        raise

def load_sf_public_data():
    """Load SF Public Data into Qdrant."""
    collection_name = 'SFPublicData'
    
    # Get sample embedding to determine vector size
    sample_embedding = get_embedding("Sample text to determine vector size.")
    if not sample_embedding:
        logger.error("Failed to get a sample embedding for vector size determination.")
        return False
    vector_size = len(sample_embedding)
    
    # Create or recreate collection
    recreate_collection(collection_name, vector_size)

    # Get paths for datasets
    script_dir = os.path.dirname(os.path.abspath(__file__))
    datasets_path = os.path.join(script_dir, 'data', 'datasets')
    fixed_folder = os.path.join(datasets_path, 'fixed')

    # Get list of fixed files (without path)
    fixed_files = set()
    if os.path.exists(fixed_folder):
        fixed_files = {f for f in os.listdir(fixed_folder) if f.endswith('.json')}

    # Get list of all original files that don't have a fixed version
    original_files = []
    if os.path.exists(datasets_path):
        original_files = [f for f in os.listdir(datasets_path) 
                         if f.endswith('.json') and f not in fixed_files]

    # Process fixed files first
    all_files_to_process = [(os.path.join(fixed_folder, f), True) for f in fixed_files]
    # Then add original files that don't have fixed versions
    all_files_to_process.extend([(os.path.join(datasets_path, f), False) for f in original_files])

    if not all_files_to_process:
        logger.warning(f"No JSON files found to process")
        return False

    logger.info(f"Found {len(all_files_to_process)} JSON files to process for SFPublicData")
    points_to_upsert = []

    for idx, (article_path, is_fixed) in enumerate(all_files_to_process, start=1):
        filename = os.path.basename(article_path)
        source_type = "fixed" if is_fixed else "original"
        logger.info(f"Processing {source_type} file {idx}/{len(all_files_to_process)}: {filename}")

        try:
            with open(article_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Error reading {filename}: {e}")
            continue

        # Extract data with safe defaults
        title = data.get('title', '')
        description = data.get('description', '')
        page_text = data.get('page_text', '')
        columns = data.get('columns', [])
        url = data.get('url', '')
        endpoint = data.get('endpoint', '')
        queries = data.get('queries', [])
        report_category = data.get('report_category', '')
        publishing_department = data.get('publishing_department', '')
        last_updated_date = data.get('rows_updated_at', '')
        periodic = data.get('periodic', '')
        district_level = data.get('district_level', '')
        category = data.get('category', '')

        # Format text for embedding
        combined_text_parts = []
        if title:
            combined_text_parts.append(f"Title: {title}")
        if url:
            combined_text_parts.append(f"URL: {url}")
        if endpoint:
            combined_text_parts.append(f"Endpoint: {endpoint}")
        if description:
            combined_text_parts.append(f"Description: {description}")
        if page_text:
            combined_text_parts.append(f"Content: {page_text}")
        if columns:
            columns_formatted = format_columns(columns)
            combined_text_parts.append(f"Columns: {columns_formatted}")
        if report_category:
            combined_text_parts.append(f"Report Category: {report_category}")
        if publishing_department:
            combined_text_parts.append(f"Publishing Department: {publishing_department}")
        if last_updated_date:
            combined_text_parts.append(f"Last Updated: {last_updated_date}")
        if periodic:
            combined_text_parts.append(f"Periodic: {periodic}")
        if district_level:
            combined_text_parts.append(f"District Level: {district_level}")
        if queries:
            combined_text_parts.append(f"Queries: {queries}")

        combined_text = "\n".join(combined_text_parts)

        # Generate embedding
        embedding = get_embedding(combined_text)
        if not embedding:
            logger.error(f"Failed to generate embedding for article '{title}'. Skipping.")
            continue

        # Prepare payload
        payload = {
            'title': title,
            'description': description,
            'url': url,
            'endpoint': endpoint,
            'columns': serialize_columns(columns),
            'column_names': [col.get('name', '').lower() for col in columns],
            'category': category.lower() if category else '',
            'publishing_department': str(publishing_department).lower() if publishing_department else '',
            'last_updated_date': convert_to_timestamp(last_updated_date) if last_updated_date else None,
            'queries': queries
        }

        # Remove None values from payload
        payload = {k: v for k, v in payload.items() if v is not None}

        point = rest.PointStruct(
            id=str(uuid.uuid4()),
            vector=embedding,
            payload=payload
        )
        points_to_upsert.append(point)

        # Batch upload every 100 points
        if len(points_to_upsert) >= 100:
            try:
                qdrant.upsert(collection_name=collection_name, points=points_to_upsert)
                logger.info(f"Successfully upserted batch of {len(points_to_upsert)} documents")
                points_to_upsert = []
            except Exception as e:
                logger.error(f"Error upserting batch to Qdrant: {e}")

    # Upload any remaining points
    if points_to_upsert:
        try:
            qdrant.upsert(collection_name=collection_name, points=points_to_upsert)
            logger.info(f"Successfully upserted final batch of {len(points_to_upsert)} documents")
        except Exception as e:
            logger.error(f"Error upserting final batch to Qdrant: {e}")

    return True

if __name__ == '__main__':
    load_sf_public_data() 