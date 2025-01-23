#!/bin/bash

# Function to wait for a service to be ready
wait_for_service() {
    local url=$1
    local name=$2
    local max_attempts=60
    local attempt=1
    
    echo "Waiting for $name to be ready..."
    while ! curl -s "$url" > /dev/null 2>&1; do
        if [ $attempt -ge $max_attempts ]; then
            echo "$name failed to start after $max_attempts attempts"
            exit 1
        fi
        echo "Attempt $attempt: $name not ready yet..."
        sleep 3
        ((attempt++))
    done
    echo "$name is ready!"
}

# Kill existing processes
pkill -f qdrant
pkill -f main.py


cd ai

# Create logs directory if it doesn't exist
mkdir -p logs

# Start Qdrant and wait for it
echo "Starting Qdrant..."
qdrant > logs/qdrant.log 2>&1 &
wait_for_service "http://0.0.0.0:6333/healthz" "Qdrant"

# # Initialize vector database if needed
# if [ ! -f .qdrant-initialized ]; then
#     echo "Initializing vector database..."
#     python vector_loader.py
#     touch .qdrant-initialized
# fi

# Start backend and wait for it
echo "Starting Main..."
python main.py > logs/main.log 2>&1 &
wait_for_service "http://0.0.0.0:8000/backend" "Backend"

# Start frontend and wait for it
echo "Checking frontend..."
wait_for_service "http://0.0.0.0:80010" "Frontend"

# Keep script running to maintain processes
wait
