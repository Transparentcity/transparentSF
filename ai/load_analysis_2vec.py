import os
import sys
import re
import time
import uuid
import glob
import logging
from openai import OpenAI
import qdrant_client
from qdrant_client.http import models as rest
from dotenv import load_dotenv
from datetime import datetime
import tiktoken  # For counting tokens

# ------------------------------
# Configure Logging
# ------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("ingestion.log")
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
    qdrant = qdrant_client.QdrantClient(host='localhost', port=6333)  # Adjust if needed
    logger.info("Connected to Qdrant at localhost:6333")
except Exception as e:
    logger.error(f"Failed to connect to Qdrant: {e}")
    raise

# ------------------------------
# Script Paths
# ------------------------------
script_dir = os.path.dirname(os.path.abspath(__file__))
output_folder = os.path.join(script_dir, 'output/annual')

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

def sanitize_filename(filename):
    """Sanitize filename by removing invalid chars."""
    sanitized = re.sub(r'[<>:"/\\|?*]', '', filename).strip()
    logger.debug(f"Sanitized filename: {sanitized}")
    return sanitized

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

# ------------------------------
# Main Processing
# ------------------------------

def main():
    # Parse month argument
    if len(sys.argv) > 1:
        month_str = sys.argv[1]
        # Validate format YYYY-MM
        try:
            datetime.strptime(month_str, "%Y-%m")
            logger.info(f"Using provided month: {month_str}")
        except ValueError:
            logger.error("Invalid month format. Use YYYY-MM.")
            sys.exit(1)
    else:
        logger.info(f"No month provided. Using last completed month: {month_str}")

    # Ensure output folder exists
    month_dir = output_folder
    if not os.path.isdir(month_dir):
        logger.error(f"Output folder not found: {month_dir}")
        sys.exit(1)
    logger.info(f"Processing files in directory: {month_dir}")

    # Gather all .md files in the entire output folder (including root)
    all_md_files = glob.glob(os.path.join(month_dir, '**', '*.md'), recursive=True)
    if not all_md_files:
        logger.warning(f"No .md files found under {month_dir}. Nothing to process.")
        sys.exit(0)
    logger.info(f"Found {len(all_md_files)} .md files to process.")

    # Determine vector size using a sample embedding
    logger.info("Determining vector size using a sample embedding.")
    sample_embedding = get_embedding("Sample text to determine vector size.")
    if not sample_embedding:
        logger.error("Failed to get a sample embedding for vector size determination.")
        sys.exit(1)
    vector_size = len(sample_embedding)
    logger.info(f"Vector size determined: {vector_size}")

    # Create or recreate a collection named for the latest analysis
    collection_name = "LATEST"
    recreate_collection(collection_name, vector_size)

    # Build a list of Qdrant points
    points_to_upsert = []
    for idx, md_file in enumerate(all_md_files, start=1):
        logger.info(f"Processing file {idx}/{len(all_md_files)}: {md_file}")
        try:
            with open(md_file, 'r', encoding='utf-8') as f:
                content = f.read()
            logger.debug(f"Read content from {md_file}")
        except Exception as e:
            logger.error(f"Error reading {md_file}: {e}")
            continue

        # Create embedding
        embedding = get_embedding(content)
        if not embedding:
            logger.error(f"Failed to generate embedding for file {md_file}. Skipping.")
            continue

        # Build payload
        payload = {
            "filename": os.path.basename(md_file),
            "filepath": md_file,
            "month": month_str,
            "content": content
        }

        point_id = str(uuid.uuid4())
        point = rest.PointStruct(id=point_id, vector=embedding, payload=payload)
        points_to_upsert.append(point)
        logger.debug(f"Prepared point {point_id} for upsert.")

    if points_to_upsert:
        logger.info(f"Upserting {len(points_to_upsert)} points into '{collection_name}'.")
        try:
            qdrant.upsert(collection_name=collection_name, points=points_to_upsert)
            logger.info(f"Successfully upserted {len(points_to_upsert)} documents into '{collection_name}'.")
        except Exception as e:
            logger.error(f"Error upserting points into '{collection_name}': {e}")
    else:
        logger.warning("No points to upsert.")

    logger.info("Ingestion completed.")

if __name__ == '__main__':
    main()
