import os
import sys
import http.server
import socketserver
import threading
import time
from pathlib import Path

try:
    import ngrok
    NGROK_AVAILABLE = True
except ImportError:
    NGROK_AVAILABLE = False
    print("Ngrok package not available. Please install it with: pip install ngrok")
    sys.exit(1)

# Configuration
PORT = 8765
REPORTS_DIR = Path(__file__).parent / "ai" / "output" / "reports"

class ReportsHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(REPORTS_DIR), **kwargs)
    
    def log_message(self, format, *args):
        # Override to provide cleaner console output
        print(f"[Server] {args[0]} {args[1]} {args[2]}")

def start_http_server():
    handler = ReportsHTTPRequestHandler
    try:
        with socketserver.TCPServer(("", PORT), handler) as httpd:
            print(f"Serving monthly reports from {REPORTS_DIR}")
            print(f"Local URL: http://localhost:{PORT}")
            print("\nAvailable monthly reports:")
            for i, file in enumerate(sorted(os.listdir(REPORTS_DIR)), 1):
                if file.startswith("monthly_report") and file.endswith(".html"):
                    print(f"{i}. {file}")
            httpd.serve_forever()
    except OSError as e:
        if e.errno == 48:  # Address already in use
            print(f"Port {PORT} is already in use. The server might already be running.")
            print(f"You can still access your reports at http://localhost:{PORT}")
        else:
            raise

def set_ngrok_authtoken():
    print("\n" + "="*60)
    print("Ngrok Authentication Setup")
    print("="*60)
    print("Ngrok requires an authtoken to create public URLs.")
    print("If you don't have one, sign up at https://dashboard.ngrok.com/signup")
    print("Then get your token at https://dashboard.ngrok.com/get-started/your-authtoken")
    
    token = input("\nEnter your ngrok authtoken: ")
    if not token:
        print("No token provided. Exiting.")
        sys.exit(1)
    
    # Set the token as an environment variable
    os.environ["NGROK_AUTHTOKEN"] = token
    print("\nAuthtoken set for this session.")
    return token

if __name__ == "__main__":
    # Check if reports directory exists
    if not REPORTS_DIR.exists():
        print(f"Error: Reports directory not found at {REPORTS_DIR}")
        print("Please check the path and try again.")
        sys.exit(1)
    
    # Check if there are any report files
    report_files = [f for f in os.listdir(REPORTS_DIR) if f.startswith("monthly_report") and f.endswith(".html")]
    if not report_files:
        print(f"Error: No monthly report files found in {REPORTS_DIR}")
        print("Please generate reports first.")
        sys.exit(1)
    
    # Check for ngrok authtoken in environment variables
    if not os.environ.get("NGROK_AUTHTOKEN"):
        token = set_ngrok_authtoken()
    
    # Start HTTP server in a separate thread
    print("\nStarting HTTP server...")
    server_thread = threading.Thread(target=start_http_server, daemon=True)
    server_thread.start()
    time.sleep(1)  # Give the server a moment to start
    
    # Start ngrok in the main thread
    print("\nStarting ngrok tunnel...")
    try:
        listener = ngrok.forward(PORT, authtoken_from_env=True)
        public_url = listener.url()
        
        print("\n" + "="*60)
        print(f"🌐 Public URL (accessible from any device): {public_url}")
        print("="*60 + "\n")
        
        report_files = sorted([f for f in os.listdir(REPORTS_DIR) if f.startswith("monthly_report") and f.endswith(".html")])
        if report_files:
            print(f"To view a report on your phone, open this URL:")
            print(f"{public_url}/{report_files[0]}")
        
        print("\nPress Ctrl+C to stop the server")
        
        # Keep the script running until interrupted
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down server...")
            listener.close()
            print("Server stopped.")
    except Exception as e:
        print(f"Error starting ngrok: {e}")
        print("Please check your authtoken and internet connection.")
        sys.exit(1) 