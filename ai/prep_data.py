import os
import json
import re
import time
import uuid  # For generating UUIDs
from urllib.parse import urlparse  # For parsing URLs
from openai import OpenAI  # Ensure this import matches your OpenAI library
import qdrant_client
from qdrant_client.http import models as rest
from dotenv import load_dotenv
from datetime import datetime

# Load environment variables
load_dotenv()

# ------------------------------
# Configuration and Setup
# ------------------------------

# Initialize OpenAI client with API key from environment variables
openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    raise ValueError("OpenAI API key not found in environment variables.")

client = OpenAI(api_key=openai_api_key)

GPT_MODEL = 'gpt-4'
EMBEDDING_MODEL = "text-embedding-3-large"  # Keeping your original embedding model

# Initialize Qdrant client
qdrant = qdrant_client.QdrantClient(host='localhost', port=6333)  # Adjust port if necessary

# Collection configuration
collection_name = 'SFPublicData'

# ------------------------------
# Utility Functions
# ------------------------------

def sanitize_filename(filename):
    """Sanitize the filename by removing or replacing invalid characters."""
    sanitized = re.sub(r'[<>:"/\\|?*]', '', filename)
    sanitized = sanitized.strip()
    return sanitized

def get_embedding(text, retries=3, delay=5):
    """Generate embeddings for the given text using OpenAI API with retry mechanism."""
    for attempt in range(1, retries + 1):
        try:
            response = client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=text
            )
            embedding = response.data[0].embedding
            print(f"Embedding generated: type={type(embedding)}, length={len(embedding)}")
            return embedding
        except Exception as e:
            print(f"Attempt {attempt} - Error generating embedding: {e}")
            if attempt < retries:
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                print("Max retries reached. Skipping this text.")
    return None

def format_columns(columns):
    """Format the columns information into a readable string for embedding."""
    if not columns:
        return ""
    formatted = "Columns Information:\n"
    for col in columns:
        formatted += f"- **{col['name']}** ({col['dataTypeName']}): {col['description']}\n"
    return formatted

def serialize_columns(columns):
    """Serialize the columns into a structured dictionary for payload."""
    if not columns:
        return {}
    serialized = {}
    for col in columns:
        serialized[col['name']] = {
            "fieldName": col.get('fieldName', ''),
            "dataTypeName": col.get('dataTypeName', ''),
            "description": col.get('description', ''),
            "position": col.get('position', ''),
            "renderTypeName": col.get('renderTypeName', ''),
            "tableColumnId": col.get('tableColumnId', '')
        }
    return serialized

def extract_endpoint(url):
    """
    Extract the Socrata endpoint from the given URL.

    Args:
        url (str): The URL to process.

    Returns:
        str: The extracted endpoint.
    """
    parsed_url = urlparse(url)
    # Extract the path and, if necessary, query parameters
    endpoint = parsed_url.path
    if parsed_url.query:
        endpoint += f"?{parsed_url.query}"
    return endpoint

# ------------------------------
# Main Processing
# ------------------------------

def main():
    # Define paths
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_folder = os.path.join(script_dir, 'data')
    datasets_folder = os.path.join(data_folder, 'datasets/fixed')
    output_folder = os.path.join(script_dir, 'datasets')

    # Ensure the output directory exists
    os.makedirs(output_folder, exist_ok=True)

    # Verify that the datasets directory exists
    if not os.path.isdir(datasets_folder):
        print(f"Datasets directory not found at {datasets_folder}")
        return

    # List all JSON files in the datasets directory
    article_list = [f for f in os.listdir(datasets_folder) if f.endswith('.json')]

    if not article_list:
        print(f"No JSON files found in {datasets_folder}")
        return

    print(f"Found {len(article_list)} JSON files to process.")

    # Check if the collection exists and recreate it
    try:
        # Determine vector size by generating an embedding for a sample text
        sample_embedding = get_embedding("Sample text for determining vector size.")
        if not sample_embedding:
            print("Failed to generate a sample embedding. Cannot determine vector size.")
            return
        vector_size = len(sample_embedding)

        # Recreate collection with default vector field 'vector'
        qdrant.recreate_collection(
            collection_name=collection_name,
            vectors_config=rest.VectorParams(
                distance=rest.Distance.COSINE,
                size=vector_size,
            )
        )
        print(f"Collection '{collection_name}' created successfully with vector size {vector_size} and default vector field 'vector'.")
    except Exception as e:
        print(f"Error checking or creating collection '{collection_name}': {e}")
        return

    # Process and upsert each file individually
    for idx, filename in enumerate(article_list, start=1):
        article_path = os.path.join(datasets_folder, filename)
        print(f"\nProcessing file {idx}/{len(article_list)}: {filename}")

        try:
            with open(article_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"JSON decode error in file {filename}: {e}")
            continue
        except Exception as e:
            print(f"Error reading file {filename}: {e}")
            continue

        # Extract relevant fields for embedding
        title = data.get('title', 'Untitled')
        description = data.get('description', '')
        page_text = data.get('page_text', '')
        columns = data.get('columns', [])
        url = data.get('url', '')  # Assuming 'url' field exists

        # Format columns for embedding
        columns_formatted = format_columns(columns)

        # Create a combined text for embedding
        combined_text = f"Title: {title}\n\nDescription:\n{description}\n\nContent:\n{page_text}\n\n{columns_formatted}"

        # Generate embedding
        embedding = get_embedding(combined_text)
        if not embedding:
            print(f"Failed to generate embedding for article '{title}'. Skipping this file.")
            continue

        # Serialize columns for payload
        serialized_columns = serialize_columns(columns)
        column_names = [col_name.lower() for col_name in serialized_columns.keys()]  # Standardize to lower case

        # Extract endpoint from URL
        if url:
            endpoint = extract_endpoint(url)
        else:
            endpoint = ""  # Or handle as needed

        # Extract additional fields for payload
        category = data.get('category', '').lower()
        publishing_department = str(data.get('publishing_department', '')).lower()
        last_updated_date_str = data.get('rows_updated_at', '')


        # Convert last_updated_date to Unix timestamp for numerical filtering
        if last_updated_date_str:
            try:
                dt = datetime.fromisoformat(last_updated_date_str.replace('Z', '+00:00'))
                last_updated_timestamp = int(dt.timestamp())
            except ValueError:
                last_updated_timestamp = 0
        else:
            last_updated_timestamp = 0

        # Prepare payload
        payload = {
            'title': title,
            'description': description,
            'url': url,
            'endpoint': endpoint,
            'columns': serialized_columns,  # Keep the serialized columns
            'column_names': column_names,   # Add a list of column names
            'category': category,
            'publishing_department': publishing_department,
            'last_updated_date': last_updated_timestamp
        }

        # Assign a unique UUID as the point ID
        point_id = str(uuid.uuid4())

        point = rest.PointStruct(
            id=point_id,
            vector=embedding,  # This will be stored in the default 'vector' field
            payload=payload,
        )

        # Upsert the single point
        try:
            qdrant.upsert(
                collection_name=collection_name,
                points=[point],
            )
            print(f"Successfully upserted article '{title}' with ID '{point_id}'.")
        except Exception as e:
            print(f"Error upserting article '{title}' to Qdrant: {e}")
            # Optionally, implement retry logic here as well
            continue

        # Optional: Introduce a short delay to avoid hitting API rate limits
        time.sleep(0.1)  # Adjust as necessary

    print("\nData processing and upsertion completed successfully.")

if __name__ == '__main__':
    main()