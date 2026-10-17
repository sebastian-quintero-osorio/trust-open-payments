"""The demo must fail explicitly when no usable callback URL is available."""
import importlib.util
from pathlib import Path
import queue
import unittest

spec = importlib.util.spec_from_file_location("demo_launcher", Path(__file__).resolve().parents[1] / "scripts" / "demo.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class DemoTunnelTest(unittest.TestCase):
    def test_reads_https_url_after_startup_messages(self):
        lines = queue.Queue()
        lines.put("Starting tunnel")
        lines.put("| https://demo-example.trycloudflare.com |")
        self.assertEqual(demo.tunnel_url(lines), "https://demo-example.trycloudflare.com")

    def test_rejects_process_exit_without_a_url(self):
        lines = queue.Queue()
        lines.put("Tunnel failed")
        lines.put(None)
        with self.assertRaises(RuntimeError):
            demo.tunnel_url(lines)

    def test_rejects_http_and_other_domains(self):
        lines = queue.Queue()
        lines.put("http://demo-example.trycloudflare.com")
        lines.put("https://other.example")
        lines.put(None)
        with self.assertRaises(RuntimeError):
            demo.tunnel_url(lines)

    def test_timeout_is_explicit(self):
        with self.assertRaises(RuntimeError):
            demo.tunnel_url(queue.Queue(), timeout=0.01)


if __name__ == "__main__":
    unittest.main()
