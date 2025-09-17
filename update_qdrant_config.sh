#!/bin/bash

# Script to update Qdrant configuration in production
# Run this on the production server

echo "Updating Qdrant configuration in production..."

# Update vector_loader_sfpublic.py
echo "Updating vector_loader_sfpublic.py..."
sed -i 's/qdrant = qdrant_client.QdrantClient(host='\''localhost'\'', port=6333)/qdrant_host = os.getenv("QDRANT_URL", "localhost")\n    qdrant_port = int(os.getenv("QDRANT_PORT", "6333"))\n    qdrant = qdrant_client.QdrantClient(host=qdrant_host, port=qdrant_port)/' /opt/transparentsf/ai/vector_loader_sfpublic.py

# Update vector_loader_periodic.py
echo "Updating vector_loader_periodic.py..."
sed -i 's/qdrant = qdrant_client.QdrantClient(host='\''localhost'\'', port=6333)/qdrant_host = os.getenv("QDRANT_URL", "localhost")\n    qdrant_port = int(os.getenv("QDRANT_PORT", "6333"))\n    qdrant = qdrant_client.QdrantClient(host=qdrant_host, port=qdrant_port)/' /opt/transparentsf/ai/vector_loader_periodic.py

# Update prep_data.py
echo "Updating prep_data.py..."
sed -i 's/qdrant = qdrant_client.QdrantClient(host='\''localhost'\'', port=6333)/qdrant_host = os.getenv("QDRANT_URL", "localhost")\nqdrant_port = int(os.getenv("QDRANT_PORT", "6333"))\nqdrant = qdrant_client.QdrantClient(host=qdrant_host, port=qdrant_port)/' /opt/transparentsf/ai/prep_data.py

# Update tools/vector_query.py
echo "Updating tools/vector_query.py..."
sed -i 's/qdrant = qdrant_client.QdrantClient(host="localhost", port=6333)/qdrant_host = os.getenv("QDRANT_URL", "localhost")\nqdrant_port = int(os.getenv("QDRANT_PORT", "6333"))\nqdrant = qdrant_client.QdrantClient(host=qdrant_host, port=qdrant_port)/' /opt/transparentsf/ai/tools/vector_query.py

# Add os import to vector_query.py if not present
if ! grep -q "import os" /opt/transparentsf/ai/tools/vector_query.py; then
    sed -i '1i import os' /opt/transparentsf/ai/tools/vector_query.py
fi

echo "Qdrant configuration updated successfully!"


