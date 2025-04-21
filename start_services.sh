#!/bin/bash

export LD_LIBRARY_PATH="/nix/store/qxfi4d8dfc8rpdk3y0dlmdc28nad02pd-zlib-1.2.13/lib:/nix/store/22nxhmsfcv2q2rpkmfvzwg2w5z1l231z-gcc-13.3.0-lib/lib:$LD_LIBRARY_PATH"


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

# Function to check if we're running on Replit
is_replit() {
    [ -n "$REPL_ID" ]
}
start_postgres() {
    if is_replit; then
        echo "Setting up PostgreSQL on Replit..."

        export PGDATA=$HOME/postgres_data

        # Create data directory if needed
        if [ ! -d "$PGDATA/base" ]; then
            echo "Initializing PostgreSQL data directory..."
            initdb -D "$PGDATA"
        fi

        # Modify postgresql.conf to avoid /run/postgresql
        echo "Configuring PostgreSQL socket and PID locations..."
        echo "unix_socket_directories = '$HOME'" >> "$PGDATA/postgresql.conf"
        echo "external_pid_file = '$HOME/postgres.pid'" >> "$PGDATA/postgresql.conf"

        echo "Starting PostgreSQL..."
        pg_ctl -D "$PGDATA" -l "$HOME/postgres_log" start

        echo "Waiting for PostgreSQL to be ready..."
        max_attempts=30
        attempt=1
        while ! pg_isready -h "$HOME" -p 5432 > /dev/null 2>&1; do
            if [ $attempt -ge $max_attempts ]; then
                echo "PostgreSQL failed to start after $max_attempts attempts"
                exit 1
            fi
            echo "Attempt $attempt: PostgreSQL not ready yet..."
            sleep 2
            ((attempt++))
        done
        echo "PostgreSQL is ready!"

        echo "Ensuring database exists..."
        createdb -h "$HOME" -U postgres transparentsf 2>/dev/null || true

    else
        # (Mac path unchanged)
        ...
    fi
}


# Function to initialize the database
init_database() {
    echo "Initializing database..."
    if [ -d "ai" ]; then
        cd ai
        python tools/init_postgres_db.py
        cd ..
    else
        echo "Error: 'ai' directory not found"
        exit 1
    fi
}

# Kill any lingering processes
pkill -f qdrant
pkill -f main.py
pkill -f ghostBridge.js

# Create logs directory
mkdir -p ai/logs

# Start PostgreSQL and initialize DB
start_postgres
init_database

# Start backend
echo "Starting Main..."
cd ai
python main.py &
cd ..

wait_for_service "http://0.0.0.0:8000/backend" "Backend"

# Confirm frontend
echo "Checking frontend..."
wait_for_service "http://0.0.0.0:8000" "Frontend"

# Start Qdrant
echo "Starting Qdrant..."
qdrant &
wait_for_service "http://0.0.0.0:6333/healthz" "Qdrant"

# Start Ghost Bridge
echo "Starting Ghost Bridge..."
cd ai
node tools/ghost_bridge/ghostBridge.js &
cd ..

# Hold the script open
wait

echo "Services started. Check logs/ directory for output."
