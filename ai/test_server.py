#!/usr/bin/env python3
"""
Minimal test server for TransparentSF to verify basic functionality.
"""

from fastapi import FastAPI
import uvicorn

app = FastAPI(title="TransparentSF Test Server")

@app.get("/")
async def root():
    """Root endpoint for testing."""
    return {"message": "TransparentSF Test Server is running!", "status": "success"}

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "message": "Server is running"}

if __name__ == "__main__":
    print("Starting TransparentSF Test Server...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
