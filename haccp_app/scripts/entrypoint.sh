#!/bin/bash
set -e

echo "=== HACCP App Startup ==="

# Run database initialization (non-destructive)
echo "Checking database initialization..."
python /app/scripts/init_db.py

# Start Streamlit
echo "Starting Streamlit application..."
exec streamlit run /app/main.py --server.address=0.0.0.0 --server.port=8501
