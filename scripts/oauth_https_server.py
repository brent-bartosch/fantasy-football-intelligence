#!/usr/bin/env python3
"""
Simple HTTPS server for Yahoo OAuth callback
Creates a self-signed certificate for localhost
"""

import ssl
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import threading
import subprocess

class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Handle OAuth callback"""
        query_components = parse_qs(urlparse(self.path).query)
        
        if 'code' in query_components:
            auth_code = query_components['code'][0]
            
            # Store the code for retrieval
            with open('config/auth_code.txt', 'w') as f:
                f.write(auth_code)
            
            # Send success response
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            
            success_html = """
            <html>
            <head>
                <title>Yahoo Auth Success</title>
                <style>
                    body { font-family: Arial; text-align: center; padding: 50px; }
                    .success { color: green; font-size: 24px; }
                    .code { background: #f0f0f0; padding: 10px; margin: 20px; font-family: monospace; }
                </style>
            </head>
            <body>
                <h1 class="success">✅ Authorization Successful!</h1>
                <p>Authorization code received. You can close this window.</p>
                <p>Return to your terminal to continue.</p>
                <div class="code">Code: {}</div>
            </body>
            </html>
            """.format(auth_code[:10] + '...')
            
            self.wfile.write(success_html.encode())
            print(f"\n✅ Received authorization code: {auth_code[:10]}...")
            
        else:
            self.send_response(400)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            error_html = """
            <html>
            <body>
                <h1>❌ Authorization Failed</h1>
                <p>No authorization code received.</p>
            </body>
            </html>
            """
            self.wfile.write(error_html.encode())
    
    def log_message(self, format, *args):
        """Suppress default logging"""
        pass

def create_self_signed_cert():
    """Create a self-signed certificate for localhost"""
    cert_dir = 'config'
    os.makedirs(cert_dir, exist_ok=True)
    
    cert_file = os.path.join(cert_dir, 'localhost.crt')
    key_file = os.path.join(cert_dir, 'localhost.key')
    
    if not os.path.exists(cert_file) or not os.path.exists(key_file):
        print("Creating self-signed certificate for localhost...")
        
        # Create self-signed certificate using openssl
        subprocess.run([
            'openssl', 'req', '-x509', '-newkey', 'rsa:4096',
            '-keyout', key_file, '-out', cert_file,
            '-days', '365', '-nodes',
            '-subj', '/CN=localhost'
        ], check=True, capture_output=True)
        
        print(f"✅ Certificate created: {cert_file}")
    
    return cert_file, key_file

def start_https_server(port=8000):
    """Start HTTPS server for OAuth callback"""
    
    # Create self-signed certificate
    cert_file, key_file = create_self_signed_cert()
    
    # Create HTTPS server
    httpd = HTTPServer(('localhost', port), OAuthCallbackHandler)
    
    # Set up SSL
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    
    print(f"\n🔒 HTTPS Server running on https://localhost:{port}")
    print("Waiting for OAuth callback...")
    print("\nNote: Your browser may show a security warning about the self-signed certificate.")
    print("This is normal - just click 'Advanced' and 'Proceed to localhost' to continue.\n")
    
    # Handle one request then stop
    httpd.handle_request()
    
    return True

if __name__ == "__main__":
    start_https_server()