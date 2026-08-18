from __future__ import annotations

from http.server import ThreadingHTTPServer
import socket
import ssl


class ThreadedTLSHTTPServer(ThreadingHTTPServer):
    """Keep slow TLS handshakes away from the accept loop."""

    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 64

    def __init__(
        self,
        *args,
        tls_handshake_timeout_seconds: float = 10.0,
        **kwargs,
    ) -> None:
        self._tls_context: ssl.SSLContext | None = None
        self._tls_handshake_timeout_seconds = tls_handshake_timeout_seconds
        super().__init__(*args, **kwargs)

    def enable_tls(self, context: ssl.SSLContext) -> None:
        self._tls_context = context

    def process_request_thread(
        self,
        request: socket.socket,
        client_address,
    ) -> None:
        wrapped_request = request
        if self._tls_context is not None:
            try:
                request.settimeout(self._tls_handshake_timeout_seconds)
                wrapped_request = self._tls_context.wrap_socket(
                    request,
                    server_side=True,
                )
                wrapped_request.settimeout(None)
            except (OSError, ssl.SSLError):
                request.close()
                return
        super().process_request_thread(wrapped_request, client_address)
