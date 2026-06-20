"""
api/server.py - HTTP API Server
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json


class APIHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/run":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body)
            
            response = {"status": "ok", "task": data.get("task")}
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass  # Suppress logging


def start_server(port: int = 8000):
    server = HTTPServer(("0.0.0.0", port), APIHandler)
    print(f"API server running on port {port}")
    server.serve_forever()
