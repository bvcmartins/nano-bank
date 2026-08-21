import json
from http.server import HTTPServer, BaseHTTPRequestHandler

class MockLedgerHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        elif self.path == "/balances":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"[]")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/entries":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"id": "123456789"}')
        else:
            self.send_response(404)
            self.end_headers()

def run():
    server_address = ('127.0.0.1', 8091)
    httpd = HTTPServer(server_address, MockLedgerHandler)
    print("🚀 Mock Ledger Server running on port 8091...")
    httpd.serve_forever()

if __name__ == '__main__':
    run()
