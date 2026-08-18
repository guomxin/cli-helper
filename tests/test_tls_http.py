from __future__ import annotations

from http.server import BaseHTTPRequestHandler
import http.client
import socket
import ssl
import threading
import unittest

from bscli.core.tls_http import ThreadedTLSHTTPServer


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, _format: str, *_args) -> None:
        return None


class _BlockingFirstContext:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()
        self._calls = 0
        self.observed_timeouts: list[float | None] = []

    def wrap_socket(self, request: socket.socket, *, server_side: bool):
        self.observed_timeouts.append(request.gettimeout())
        with self._lock:
            self._calls += 1
            call = self._calls
        if call == 1:
            self.entered.set()
            self.release.wait(timeout=3)
            raise ssl.SSLError("stalled handshake")
        return request


class ThreadedTLSHTTPServerTests(unittest.TestCase):
    def test_stalled_tls_handshake_does_not_block_accept_loop(self) -> None:
        context = _BlockingFirstContext()
        server = ThreadedTLSHTTPServer(
            ("127.0.0.1", 0),
            _HealthHandler,
            tls_handshake_timeout_seconds=0.5,
        )
        server.enable_tls(context)  # type: ignore[arg-type]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        stalled = socket.create_connection(server.server_address, timeout=2)
        try:
            self.assertTrue(context.entered.wait(timeout=2))
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                server.server_port,
                timeout=2,
            )
            connection.request("GET", "/healthz")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"ok")
            connection.close()
            self.assertEqual(context.observed_timeouts, [0.5, 0.5])
        finally:
            context.release.set()
            stalled.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
