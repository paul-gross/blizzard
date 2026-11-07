"""Put a hub pause between the runner's queue read and its next claim."""

from __future__ import annotations

import threading
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx2


@contextmanager
def pause_on_claim(
    hub_url: str, runner_id: str
) -> Generator[tuple[str, threading.Event, list[tuple[int, dict[str, object]]]]]:
    armed = threading.Event()
    observed = threading.Event()
    answers: list[tuple[int, dict[str, object]]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.forward()

        def do_POST(self) -> None:
            self.forward()

        def do_PUT(self) -> None:
            self.forward()

        def forward(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            headers = {
                k: v for k, v in self.headers.items() if k.lower() not in ("host", "connection", "content-length")
            }
            target = self.path.split("?", 1)[0] == "/api/fleet/routes" and self.command == "POST" and armed.is_set()
            if target:
                armed.clear()
                pause = httpx2.post(f"{hub_url}/api/runners/{runner_id}/pause", json={"by": "rehearsal"}, timeout=30)
                assert pause.status_code == 200, pause.text
            response = httpx2.request(self.command, hub_url + self.path, headers=headers, content=body, timeout=30)
            if target:
                answers.append((response.status_code, response.json()))
                observed.set()
            self.send_response(response.status_code)
            for name, value in response.headers.items():
                if name.lower() not in ("transfer-encoding", "connection", "content-encoding", "content-length"):
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        armed.set()
        yield f"http://127.0.0.1:{server.server_port}", observed, answers
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
