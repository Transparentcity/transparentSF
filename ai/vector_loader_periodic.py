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
from concurrent.futures import ThreadPoolExecutor, as_completed

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
    """Splits text into chunks that fit within the token limit."""
    tokenizer = tiktoken.encoding_for_model(EMBEDDING_MODEL)
    tokens = tokenizer.encode(text)
    total_tokens = len(tokens)
    
    chunks = []
    for i in range(0, total_tokens, max_tokens):
        chunk_tokens = tokens[i:i + max_tokens]
        chunk_text = tokenizer.decode(chunk_tokens)
        chunks.append(chunk_text)
    
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

def parse_markdown_content(content):
    """Parse markdown content to extract structured information."""
    # Extract title (first h1)
    title_match = re.search(r'^# (.+)$', content, re.MULTILINE)
    title = title_match.group(1) if title_match else ""
    
    # Extract query URL and description
    query_url = ""
    description = ""
    query_match = re.search(r'\*\*Query URL:\*\* (.+?)(?=\n\n|\Z)', content)
    desc_match = re.search(r'\*\*Description:\*\* (.+?)(?=\n\n|\Z)', content)
    
    if query_match:
        query_url = query_match.group(1)
    if desc_match:
        description = desc_match.group(1)
    
    # Extract column metadata
    column_metadata = []
    column_section = re.search(r'## Column Metadata\n\n\|.+?\n\|[-\s\|]+\n((?:\|[^\n]+\n)+)', content)
    if column_section:
        table_rows = column_section.group(1).strip().split('\n')
        for row in table_rows:
            cells = [cell.strip() for cell in row.split('|')[1:-1]]
            if len(cells) >= 3:  # Field Name, Description, Data Type
                column_metadata.append({
                    'name': cells[0],
                    'description': cells[1],
                    'type': cells[2] if len(cells) > 2 else ''
                })

    return {
        'title': title,
        'query_url': query_url,
        'description': description,
        'column_metadata': column_metadata
    }

def parse_file(md_file):
    """Parse a single markdown file."""
    try:
        with open(md_file, 'r', encoding='utf-8') as f:
            content = f.read()
        parsed = parse_markdown_content(content)
        # Add the full content to the parsed data
        parsed['full_content'] = content
        return content, parsed
    except Exception as e:
        logger.error(f"Error reading {md_file}: {e}")
        return None, None

def create_embedding_text(parsed):
    """Create the text to be embedded."""
    embedding_parts = []
    
    if parsed['title']:
        embedding_parts.extend([
            f"Title: {parsed['title']}",
            f"This document primarily contains data about: {parsed['title']}"
        ])
    
    if parsed['description']:
        embedding_parts.extend([
            f"Description: {parsed['description']}",
            f"This dataset provides: {parsed['description']}"
        ])
    
    if parsed['column_metadata']:
        embedding_parts.append("The dataset contains the following data fields:")
        for col in parsed['column_metadata']:
            embedding_parts.append(f"- {col['name']}: {col['description']}")
    
    # Add the full content at the end
    if parsed.get('full_content'):
        embedding_parts.append("Full content:")
        embedding_parts.append(parsed['full_content'])
    
    return "\n\n".join(embedding_parts)

def create_payload(md_file, parsed):
    """Create the payload for a point."""
    return {
        "filename": os.path.basename(md_file),
        "filepath": md_file,
        "title": parsed['title'],
        "description": parsed['description'],
        "query_url": parsed['query_url'],
        "column_metadata": parsed['column_metadata'],
        "column_names": [col['name'].lower() for col in parsed['column_metadata']]
    }

def get_embeddings_batch(texts, batch_size=20):
    """Generate embeddings for multiple texts in batches, handling large texts via chunking."""
    all_embeddings = []
    
    for i in range(0, len(texts), batch_size):
        batch_texts = texts[i:i + batch_size]
        batch_embeddings = []
        
        # Process each text in the batch
        for text in batch_texts:
            # Split into chunks if needed
            chunks = split_text_into_chunks(text)
            chunk_embeddings = []
            
            # Get embeddings for chunks
            try:
                response = client.embeddings.create(
                    model=EMBEDDING_MODEL,
                    input=chunks
                )
                chunk_embeddings = [data.embedding for data in response.data]
                
                # Average the chunk embeddings
                if chunk_embeddings:
                    averaged_embedding = [sum(col) / len(col) for col in zip(*chunk_embeddings)]
                    batch_embeddings.append(averaged_embedding)
                else:
                    batch_embeddings.append(None)
                    
            except Exception as e:
                logger.error(f"Error in batch {i//batch_size + 1}: {e}")
                batch_embeddings.append(None)
        
        all_embeddings.extend(batch_embeddings)
        logger.debug(f"Processed batch {i//batch_size + 1}")
    
    return all_embeddings

def process_directory(directory_path, collection_name, qdrant_client, vector_size):
    """Process files in parallel with batched embeddings."""
    all_md_files = glob.glob(os.path.join(directory_path, '*.md'))
    if not all_md_files:
        return False

    recreate_collection(collection_name, vector_size)
    
    # Prepare all files first
    file_contents = []
    parsed_contents = []
    
    # Read and parse all files in parallel
    with ThreadPoolExecutor(max_workers=4) as executor:
        future_to_file = {
            executor.submit(parse_file, md_file): md_file 
            for md_file in all_md_files
        }
        
        for future in as_completed(future_to_file):
            md_file = future_to_file[future]
            try:
                content, parsed = future.result()
                if content and parsed:
                    file_contents.append(content)
                    parsed_contents.append((md_file, parsed))
            except Exception as e:
                logger.error(f"Error processing {md_file}: {e}")

    # Generate embeddings in batches
    embedding_texts = [
        create_embedding_text(parsed[1])  # Create the text to embed
        for parsed in parsed_contents
    ]
    
    embeddings = get_embeddings_batch(embedding_texts)

    # Create points
    points_to_upsert = []
    for (md_file, parsed), embedding in zip(parsed_contents, embeddings):
        if embedding is None:
            continue
            
        payload = create_payload(md_file, parsed)
        payload['content'] = parsed['full_content']  # Add full content to payload
        points_to_upsert.append(
            rest.PointStruct(
                id=str(uuid.uuid4()),
                vector=embedding,
                payload=payload
            )
        )

    # Batch upsert points
    if points_to_upsert:
        try:
            batch_size = 100
            for i in range(0, len(points_to_upsert), batch_size):
                batch = points_to_upsert[i:i + batch_size]
                qdrant_client.upsert(
                    collection_name=collection_name,
                    points=batch
                )
            return True
        except Exception as e:
            logger.error(f"Error upserting points: {e}")
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
        return os.path.join(base_path, 'ai/output', timeframe)
    else:
        # For district locations
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