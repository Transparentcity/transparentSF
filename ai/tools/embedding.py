import time
from openai import OpenAI
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def get_embedding(text, retries=3, delay=5):
    # Initialize client inside the function to ensure environment variables are loaded
    client = OpenAI()
    
    for attempt in range(1, retries + 3):
        try:
            response = client.embeddings.create(
                model="text-embedding-3-large",
                input=text
            )
            embedding = response.data[0].embedding
            print(f"Embedding generated for query: type={type(embedding)}, length={len(embedding)}")
            return embedding
        except Exception as e:
            print(f"Attempt {attempt} - Error generating embedding: {e}")
            if attempt < retries:
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                print("Max retries reached. Skipping this query.")
    return None
