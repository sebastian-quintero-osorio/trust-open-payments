#!/usr/bin/env python3
"""Local static server and same-origin API proxy; never logs tokens or bodies."""
import argparse
import http.client
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]

class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        if not self.path.startswith('/api/'):
            self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def do_GET(self):
        if self.path.startswith('/api/'):
            return self.proxy()
        # Development files must be reloaded, even if a browser cached an older
        # response before this server started sending Cache-Control: no-store.
        if 'If-Modified-Since' in self.headers:
            del self.headers['If-Modified-Since']
        if self.path.startswith('/op/return?') or self.path == '/op/return':
            self.path = '/op/return.html'
        return super().do_GET()

    def do_POST(self): self.proxy()
    def do_PUT(self): self.proxy()
    def do_PATCH(self): self.proxy()

    def proxy(self):
        if not self.path.startswith('/api/v1/'):
            self.send_error(404)
            return
        target = urlsplit(self.server.api_origin)
        connection = (http.client.HTTPSConnection if target.scheme == 'https' else http.client.HTTPConnection)(target.netloc, timeout=20)
        try:
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            headers = {k: v for k, v in self.headers.items() if k.lower() in ['authorization', 'content-type', 'accept']}
            connection.request(self.command, target.path.rstrip('/') + self.path, body, headers)
            response = connection.getresponse()
            data = response.read()
            self.send_response(response.status)
            self.send_header('Content-Type', response.getheader('Content-Type', 'application/json'))
            if response.getheader('Location'):
                self.send_header('Location', response.getheader('Location'))
        except (OSError, http.client.HTTPException):
            data = json.dumps({'error': {'code': 'backend_unavailable', 'message': 'No se pudo conectar con el servidor de Trust. Inténtalo de nuevo.', 'details': {}}}).encode()
            self.send_response(502)
            self.send_header('Content-Type', 'application/json')
        finally:
            connection.close()
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        # Request URLs may contain identifiers. Keep local logs minimal.
        pass

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=5173)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--api', default='http://localhost:8000')
    parser.add_argument('--directory', default=str(ROOT / 'src'))
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), partial(Handler, directory=args.directory))
    server.api_origin = args.api
    print(f'Trust: http://{args.host}:{args.port}', flush=True)
    server.serve_forever()
