import os
import sys
import json
import re
import time
import uuid
import glob
import logging
import argparse
from openai import OpenAI
import qdrant_client
from qdrant_client.http import models as rest
from dotenv import load_dotenv
import tiktoken  # For counting tokens

# ------------------------------
# Configure Logging
# ------------------------------
script_dir = os.path.dirname(os.path.abspath(__file__))
log_dir = os.path.join(script_dir, 'logs')
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, 'vector_loader.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_file),  # Log to file
        logging.StreamHandler(sys.stdout)  # Also log to console
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
GPT_MODEL = 'gpt-4'
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

# ------------------------------
# Script Paths
# ------------------------------
script_dir = os.path.dirname(os.path.abspath(__file__))
output_folder = os.path.join(script_dir, 'output/')

# ------------------------------
# Collection Names Configuration
# ------------------------------
TIMEFRAMES = ['annual', 'monthly', 'daily']
LOCATIONS = ['citywide'] + [f'district_{i}' for i in range(1, 12)]  # districts 1-11

# ------------------------------
# Utility Functions
# ------------------------------

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
        existing = qdrant.get_collection(collection_name)
        if existing:
            qdrant.delete_collection(collection_name)
            logger.info(f"Deleted existing collection '{collection_name}'.")
    except Exception as e:
        logger.warning(f"Collection '{collection_name}' does not exist or could not be deleted: {e}")
    
    try:
        qdrant.recreate_collection(
            collection_name=collection_name,
            vectors_config=rest.VectorParams(
                distance=rest.Distance.COSINE,
                size=vector_size,
            )
        )
        logger.info(f"Collection '{collection_name}' recreated with vector size {vector_size}.")
    except Exception as e:
        logger.error(f"Failed to recreate collection '{collection_name}': {e}")
        raise

def process_directory(directory_path, collection_name, qdrant_client, vector_size):
    """
    Process all markdown files in a directory and load them into a Qdrant collection.
    """
    logger.info(f"Processing directory {directory_path} for collection {collection_name}")
    
    # Gather all .md files in the directory (not including subdirectories)
    all_md_files = glob.glob(os.path.join(directory_path, '*.md'))
    if not all_md_files:
        logger.warning(f"No .md files found under {directory_path}. Skipping collection.")
        return False

    logger.info(f"Found {len(all_md_files)} .md files to process for {collection_name}")

    # Create or recreate the collection
    recreate_collection(collection_name, vector_size)

    # Build and upsert points
    points_to_upsert = []
    for idx, md_file in enumerate(all_md_files, start=1):
        logger.info(f"Processing file {idx}/{len(all_md_files)}: {md_file}")
        try:
            with open(md_file, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            logger.error(f"Error reading {md_file}: {e}")
            continue

        embedding = get_embedding(content)
        if not embedding:
            logger.error(f"Failed to generate embedding for file {md_file}. Skipping.")
            continue

        payload = {
            "filename": os.path.basename(md_file),
            "filepath": md_file,
            "content": content
        }

        point_id = str(uuid.uuid4())
        point = rest.PointStruct(id=point_id, vector=embedding, payload=payload)
        points_to_upsert.append(point)

    if points_to_upsert:
        try:
            qdrant_client.upsert(collection_name=collection_name, points=points_to_upsert)
            logger.info(f"Successfully upserted {len(points_to_upsert)} documents into '{collection_name}'")
            return True
        except Exception as e:
            logger.error(f"Error upserting points into '{collection_name}': {e}")
            return False
    
    return False

def clear_existing_collections(qdrant_client):
    """
    Clear all existing collections except SFPublicData.
    """
    try:
        collections = qdrant_client.get_collections().collections
        for collection in collections:
            collection_name = collection.name
            if collection_name != "SFPublicData":
                logger.info(f"Deleting collection: {collection_name}")
                qdrant_client.delete_collection(collection_name)
        logger.info("Finished clearing existing collections")
    except Exception as e:
        logger.error(f"Error clearing collections: {e}")
        raise

def get_directory_path(base_path, timeframe, location):
    """
    Get the correct directory path based on timeframe and location.
    """
    if location == 'citywide':
        if timeframe == 'annual':
            return os.path.join(base_path, 'ai/output/analysis')
        else:
            return os.path.join(base_path, 'ai/output', timeframe)
    else:
        # For district locations
        if timeframe == 'annual':
            return os.path.join(base_path, 'ai/output/districts', location)
        else:
            return os.path.join(base_path, 'ai/output', timeframe, 'districts', location)

def clear_collections_for_timeframe(qdrant_client, timeframe):
    """
    Clear collections for a specific timeframe.
    """
    try:
        collections = qdrant_client.get_collections().collections
        for collection in collections:
            collection_name = collection.name
            if collection_name.startswith(f"{timeframe}_"):
                logger.info(f"Deleting collection: {collection_name}")
                qdrant_client.delete_collection(collection_name)
        logger.info(f"Finished clearing collections for timeframe: {timeframe}")
    except Exception as e:
        logger.error(f"Error clearing collections for timeframe {timeframe}: {e}")
        raise

def load_vectors(base_path, timeframe=None):
    """
    Main function to load vectors into different collections based on timeframe and location.
    If timeframe is specified, only process collections for that timeframe.
    """
    try:
        # Get vector size using sample embedding
        sample_embedding = get_embedding("Sample text to determine vector size.")
        if not sample_embedding:
            logger.error("Failed to get a sample embedding for vector size determination.")
            return False
        vector_size = len(sample_embedding)

        # If timeframe is specified, only clear and process that timeframe
        timeframes_to_process = [timeframe] if timeframe else TIMEFRAMES
        
        if timeframe:
            clear_collections_for_timeframe(qdrant, timeframe)
        else:
            clear_existing_collections(qdrant)

        # Process collections for specified timeframe(s) and locations
        for tf in timeframes_to_process:
            for location in LOCATIONS:
                collection_name = f"{tf}_{location}"
                directory_path = get_directory_path(base_path, tf, location)
                
                if os.path.exists(directory_path):
                    logger.info(f"Processing collection: {collection_name} from directory: {directory_path}")
                    process_directory(directory_path, collection_name, qdrant, vector_size)
                else:
                    logger.info(f"Directory does not exist: {directory_path}")

        logger.info(f"Vector loading completed for timeframe(s): {timeframes_to_process}")
        return True

    except Exception as e:
        logger.error(f"Error in load_vectors: {e}")
        return False

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Load vectors into Qdrant collections')
    parser.add_argument('--timeframe', choices=TIMEFRAMES, help='Specify timeframe to process (annual, monthly, or daily)')
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_vectors(script_dir, args.timeframe) 