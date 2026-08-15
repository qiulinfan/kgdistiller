"""Dependency-free local browser for a kgdistiller graph."""

from __future__ import annotations

import ipaddress
import json
import mimetypes
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse, urlsplit

from .cli import sha256_text
from .query import GraphView, load_graph_view


_STATIC_ASSETS = {"index.html", "app.js", "style.css"}


class StaleSourceError(ValueError):
    """Raised when live authority bytes no longer match the loaded graph view."""


class StaleGraphGenerationError(ValueError):
    """Raised when a source request belongs to an older browser snapshot."""


def _allowed_hostnames(bind_host: str) -> set[str]:
    configured = bind_host.strip().strip("[]").casefold()
    try:
        loopback = ipaddress.ip_address(configured).is_loopback
    except ValueError:
        loopback = configured == "localhost"
    if loopback:
        return {"127.0.0.1", "localhost", "::1", configured}
    return {configured} if configured else set()


def _request_authority(value: str, port: int) -> tuple[str, int] | None:
    if not value or any(character.isspace() for character in value) or "," in value:
        return None
    try:
        parsed = urlsplit(f"//{value}")
        parsed_port = parsed.port
    except ValueError:
        return None
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed_port != port
    ):
        return None
    return parsed.hostname.casefold(), parsed_port


def _origin_authority(value: str, port: int) -> tuple[str, int] | None:
    if not value or any(character.isspace() for character in value) or "," in value:
        return None
    try:
        parsed = urlsplit(value)
        parsed_port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() != "http"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed_port != port
    ):
        return None
    return parsed.hostname.casefold(), parsed_port


def _graph_payload(view: GraphView) -> dict[str, Any]:
    snapshot = view.snapshot
    return {
        "manifest": {
            "schema": snapshot["graph"]["schema"],
            "graph_sha256": snapshot["graph"]["sha256"],
            "counts": snapshot["graph"]["counts"],
            "snapshot_sha256": snapshot["snapshot_sha256"],
        },
        "diagnostics": snapshot["diagnostics"],
        "nodes": snapshot["nodes"],
        "edges": snapshot["edges"],
        "references": snapshot["references"],
    }


def load_graph_payload(graph_dir: Path) -> dict[str, Any]:
    """Load one complete, digest-checked graph generation for the browser."""
    return _graph_payload(load_graph_view(graph_dir))


def source_excerpt(
    project_root: Path,
    authority_path: str,
    line: int,
    radius: int = 8,
    *,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Read and optionally authenticate one bounded source excerpt exactly once."""
    # HTTP callers pass the value already decoded exactly once by ``parse_qs``.
    # Treat percent characters in an authority filename literally here; a
    # second URL decode could turn a valid ``%2e%2e`` directory into traversal.
    target = (project_root / authority_path).resolve()
    try:
        relative = target.relative_to(project_root.resolve())
    except ValueError as error:
        raise ValueError("source path lies outside the project") from error
    if not target.is_file():
        if expected_sha256 is not None:
            raise StaleSourceError(
                f"stale source authority: {relative.as_posix()}; run kgdistiller sync"
            )
        raise FileNotFoundError(relative.as_posix())
    try:
        with target.open("r", encoding="utf-8", newline=None) as handle:
            text = handle.read()
    except (OSError, UnicodeError) as error:
        if expected_sha256 is not None:
            raise StaleSourceError(
                f"stale source authority: {relative.as_posix()}; run kgdistiller sync"
            ) from error
        raise
    if expected_sha256 is not None and sha256_text(text) != expected_sha256:
        raise StaleSourceError(
            f"stale source authority: {relative.as_posix()}; run kgdistiller sync"
        )
    lines = text.splitlines()
    center = max(1, line)
    start = max(1, center - radius)
    end = min(len(lines), center + radius)
    return {
        "authority": relative.as_posix(),
        "start": start,
        "end": end,
        "line": center,
        "lines": [
            {"number": number, "text": lines[number - 1]}
            for number in range(start, end + 1)
        ],
    }


def create_graph_server(
    project_root: Path,
    graph_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    """Create the local graph HTTP server without starting its event loop."""
    static_root = Path(__file__).with_name("static")
    # Fail before binding a port if the initial generation is incomplete.
    load_graph_payload(graph_dir)
    resolved_project_root = project_root.resolve()
    allowed_hostnames = _allowed_hostnames(host)

    class Handler(BaseHTTPRequestHandler):
        def send_bytes(self, content: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(content)

        def send_json(self, value: Any, status: int = 200) -> None:
            self.send_bytes(
                json.dumps(value, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
                status,
            )

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            host_headers = self.headers.get_all("Host", failobj=[])
            request_authority = (
                _request_authority(host_headers[0], self.server.server_port)
                if len(host_headers) == 1
                else None
            )
            if (
                request_authority is None
                or request_authority[0] not in allowed_hostnames
            ):
                self.send_json({"error": "misdirected request host"}, 421)
                return
            origin_headers = self.headers.get_all("Origin", failobj=[])
            if len(origin_headers) > 1:
                self.send_json({"error": "forbidden request origin"}, 403)
                return
            if origin_headers:
                origin_authority = _origin_authority(
                    origin_headers[0], self.server.server_port
                )
                if origin_authority != request_authority:
                    self.send_json({"error": "forbidden request origin"}, 403)
                    return
            parsed = urlparse(self.path)
            if parsed.path == "/api/graph.json":
                try:
                    self.send_json(load_graph_payload(graph_dir))
                except (OSError, UnicodeError, ValueError) as error:
                    self.send_json({"error": str(error)}, 409)
                return
            if parsed.path == "/api/source":
                query = parse_qs(parsed.query)
                authority = query.get("path", [""])[0]
                try:
                    view = load_graph_view(graph_dir)
                    requested_snapshot = query.get("snapshot", [""])[0]
                    if not requested_snapshot:
                        raise ValueError("source request has no graph snapshot generation")
                    if requested_snapshot != view.snapshot["snapshot_sha256"]:
                        raise StaleGraphGenerationError(
                            "stale browser graph generation; reload the graph"
                        )
                    expected_hash = view.source_hashes.get(authority)
                    if expected_hash is None:
                        raise ValueError("source path is not an authority in the current graph")
                    line = int(query.get("line", ["1"])[0])
                    self.send_json(
                        source_excerpt(
                            resolved_project_root,
                            authority,
                            line,
                            expected_sha256=expected_hash,
                        )
                    )
                except (StaleGraphGenerationError, StaleSourceError) as error:
                    self.send_json({"error": str(error)}, 409)
                except (ValueError, OSError, UnicodeError) as error:
                    self.send_json({"error": str(error)}, 400)
                return
            relative = "index.html" if parsed.path in {"", "/"} else parsed.path.lstrip("/")
            if relative not in _STATIC_ASSETS:
                self.send_json({"error": "not found"}, 404)
                return
            target = static_root / relative
            if not target.is_file():
                self.send_json({"error": "not found"}, 404)
                return
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in {
                "application/javascript",
                "application/json",
            }:
                content_type += "; charset=utf-8"
            self.send_bytes(target.read_bytes(), content_type)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)


def serve_graph(
    project_root: Path,
    graph_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """Serve the graph and bundled viewer over a loopback HTTP server."""
    server = create_graph_server(project_root, graph_dir, host=host, port=port)
    url = f"http://{host}:{server.server_port}/"
    print(f"kgdistiller browser: {url}")
    print("Press Ctrl-C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nkgdistiller browser stopped.")
    finally:
        server.server_close()
