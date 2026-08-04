"""Dependency-free local browser for a kgdistiller graph."""

from __future__ import annotations

import json
import mimetypes
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def load_graph_payload(graph_dir: Path) -> dict[str, Any]:
    """Hydrate the committed graph and its entry shards for the browser."""
    manifest = _read_json(graph_dir / "manifest.json")
    nodes = _read_jsonl(graph_dir / "nodes.jsonl")
    node_index = {str(node["id"]): node for node in nodes}
    for node in nodes:
        node["text"] = ""
    for shard in (manifest.get("entry_store") or {}).get("shards", []):
        relative = Path(str(shard["path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe entry shard path: {relative}")
        for record in _read_jsonl(graph_dir / relative):
            node = node_index.get(str(record["id"]))
            if node is None:
                raise ValueError(f"entry shard references unknown node: {record['id']}")
            node["text"] = str(record.get("text", ""))
            if record.get("entry"):
                node["entry"] = record["entry"]
    return {
        "manifest": manifest,
        "diagnostics": _read_json(graph_dir / "diagnostics.json"),
        "nodes": nodes,
        "edges": _read_jsonl(graph_dir / "edges.jsonl"),
        "references": _read_jsonl(graph_dir / "references.jsonl"),
    }


def source_excerpt(project_root: Path, raw_path: str, line: int, radius: int = 8) -> dict[str, Any]:
    """Read a bounded source excerpt while preventing traversal outside the project."""
    target = (project_root / unquote(raw_path)).resolve()
    try:
        relative = target.relative_to(project_root.resolve())
    except ValueError as error:
        raise ValueError("source path lies outside the project") from error
    if not target.is_file():
        raise FileNotFoundError(relative.as_posix())
    lines = target.read_text(encoding="utf-8").splitlines()
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


def serve_graph(
    project_root: Path,
    graph_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """Serve the graph and bundled viewer over a loopback HTTP server."""
    static_root = Path(__file__).with_name("static")
    payload = load_graph_payload(graph_dir)
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def send_bytes(self, content: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)

        def send_json(self, value: Any, status: int = 200) -> None:
            self.send_bytes(
                json.dumps(value, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
                status,
            )

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urlparse(self.path)
            if parsed.path == "/api/graph.json":
                self.send_bytes(payload_bytes, "application/json; charset=utf-8")
                return
            if parsed.path == "/api/source":
                query = parse_qs(parsed.query)
                authority = query.get("path", [""])[0]
                try:
                    line = int(query.get("line", ["1"])[0])
                    self.send_json(source_excerpt(project_root, authority, line))
                except (ValueError, OSError, UnicodeError) as error:
                    self.send_json({"error": str(error)}, 400)
                return
            relative = "index.html" if parsed.path in {"", "/"} else parsed.path.lstrip("/")
            if "/" in relative or relative.startswith("."):
                self.send_json({"error": "not found"}, 404)
                return
            target = static_root / relative
            if not target.is_file():
                self.send_json({"error": "not found"}, 404)
                return
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
                content_type += "; charset=utf-8"
            self.send_bytes(target.read_bytes(), content_type)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
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
