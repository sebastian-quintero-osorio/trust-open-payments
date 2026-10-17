"""Exercise the actual local proxy, including the contract's wallet redirect."""
import http.client
import importlib.util
import threading
import unittest
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

spec = importlib.util.spec_from_file_location('trust_server', Path(__file__).resolve().parents[1] / 'scripts/serve.py')
server_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server_module)


class ProxyTests(unittest.TestCase):
    def test_static_assets_reload_instead_of_using_cached_versions(self):
        root = Path(__file__).resolve().parents[1] / 'src'
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(server_module.Handler, directory=str(root)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
        try:
            for path in ['/portal-prestamos/prestamista.html', '/lib/app.css', '/app.js']:
                with self.subTest(path=path):
                    connection.request('GET', path, headers={'If-Modified-Since': 'Wed, 01 Jan 2031 00:00:00 GMT'})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.getheader('Cache-Control'), 'no-store')
                    self.assertEqual(response.read(), (root / path.lstrip('/')).read_bytes())
        finally:
            connection.close()
            server.shutdown()
            server.server_close()

    def test_wallet_authorization_keeps_return_location(self):
        target = 'https://app.example.test/op/return?loan_id=00000000-0000-4000-8000-000000000030&op_status=completed'

        class WalletHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header('Location', target)
                self.end_headers()

            def log_message(self, *args): pass

        upstream = ThreadingHTTPServer(('127.0.0.1', 0), WalletHandler)
        proxy = ThreadingHTTPServer(('127.0.0.1', 0), server_module.Handler)
        proxy.api_origin = f'http://127.0.0.1:{upstream.server_port}'
        for server in [upstream, proxy]:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        connection = http.client.HTTPConnection('127.0.0.1', proxy.server_port)
        try:
            connection.request('GET', '/api/v1/dev/fake-wallet/authorize/payment-1?decision=approve')
            response = connection.getresponse()
            self.assertEqual(response.status, 302)
            self.assertEqual(response.getheader('Location'), target)
            self.assertEqual(response.read(), b'')
        finally:
            connection.close()
            for server in [proxy, upstream]:
                server.shutdown()
                server.server_close()


if __name__ == '__main__': unittest.main()
