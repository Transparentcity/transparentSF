import os
from qdrant_client import QdrantClient
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def check_collections():
    """Check the contents of all Qdrant collections."""
    try:
        # Connect to Qdrant
        client = QdrantClient(host='localhost', port=6333)
        
        # Get all collections
        collections = client.get_collections().collections
        
        print("\nQdrant Collection Statistics:")
        print("-" * 50)
        
        for collection in collections:
            collection_name = collection.name
            try:
                # Get collection info including vector count
                collection_info = client.get_collection(collection_name)
                points_count = collection_info.points_count
                
                # Get a sample point to verify content
                sample = client.scroll(
                    collection_name=collection_name,
                    limit=1
                )[0]  # [0] because scroll returns a tuple (points, next_page_offset)
                
                has_points = len(sample) > 0
                
                print(f"\nCollection: {collection_name}")
                print(f"Points count: {points_count}")
                print(f"Has points: {has_points}")
                if has_points:
                    # Print filename from the first point's payload if it exists
                    point = sample[0]
                    if point.payload and 'filename' in point.payload:
                        print(f"Sample file: {point.payload['filename']}")
                
            except Exception as e:
                print(f"\nCollection: {collection_name}")
                print(f"Error getting info: {str(e)}")
                
        print("\n" + "-" * 50)
        
    except Exception as e:
        logger.error(f"Error connecting to Qdrant: {e}")
        raise

if __name__ == "__main__":
    check_collections() 