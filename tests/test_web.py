from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.web import (  # noqa: E402
    create_graph_server,
    load_graph_payload,
    source_excerpt,
)
from kgdistiller.cli import (  # noqa: E402
    GraphState,
    load_state,
    make_artifacts,
    sha256_text,
    write_artifacts,
)


class WebPayloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="kgdistiller-web-")
        self.root = Path(self.temporary.name)
        self.graph = self.root / "knowledge/graph"
        self.source_text = "one\ntwo\nthree\n"
        state = GraphState(
            nodes={
                "demo": {
                    "id": "demo",
                    "type": "knowledge",
                    "label": "Demo",
                    "text": "Hydrated entry",
                    "entry": {
                        "context": "A structured context",
                        "common_confusions": ["Demo is not a placeholder."],
                    },
                    "properties": {
                        "aliases": [],
                        "curation_status": "current",
                        "knowledge_origin": "research",
                        "source_status": "active",
                    },
                    "provenance": {
                        "authority": "notes/demo.md",
                        "line": 2,
                        "active": True,
                    },
                },
                "general": {
                    "id": "general",
                    "type": "field",
                    "label": "General",
                    "text": "",
                    "properties": {"aliases": [], "origin": "config"},
                },
            },
            edges={
                ("general", "contains", "demo"): {
                    "source": "general",
                    "relation": "contains",
                    "target": "demo",
                    "origin": "config",
                }
            },
            references=[],
            manifest={},
        )
        write_artifacts(
            self.graph,
            make_artifacts(
                state,
                {"notes/demo.md": sha256_text(self.source_text)},
            ),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_entry_shards_are_hydrated(self) -> None:
        payload = load_graph_payload(self.graph)
        self.assertEqual("Hydrated entry", payload["nodes"][0]["text"])
        self.assertEqual(
            {
                "context": "A structured context",
                "common_confusions": ["Demo is not a placeholder."],
            },
            payload["nodes"][0]["entry"],
        )
        self.assertEqual(
            "research", payload["nodes"][0]["properties"]["knowledge_origin"]
        )

    def test_source_excerpt_is_bounded_to_project(self) -> None:
        source = self.root / "notes/demo.md"
        source.parent.mkdir(parents=True)
        source.write_text(self.source_text, encoding="utf-8")
        excerpt = source_excerpt(self.root, "notes/demo.md", 2, radius=1)
        self.assertEqual([1, 2, 3], [line["number"] for line in excerpt["lines"]])
        with self.assertRaisesRegex(ValueError, "outside"):
            source_excerpt(self.root, "../private.md", 1)
        outside = self.root.parent / f"{self.root.name}-outside.md"
        outside.write_text("secret\n", encoding="utf-8")
        link = self.root / "notes/outside.md"
        link.symlink_to(outside)
        try:
            with self.assertRaisesRegex(ValueError, "outside"):
                source_excerpt(self.root, "notes/outside.md", 1)
        finally:
            outside.unlink(missing_ok=True)

    def test_source_excerpt_hashes_and_splits_one_open_generation(self) -> None:
        source = self.root / "notes/demo.md"
        source.parent.mkdir(parents=True)
        source.write_text(self.source_text, encoding="utf-8")
        replacement = self.root / "notes/replacement.md"
        replacement.write_text("new\ngeneration\n", encoding="utf-8")
        real_open = Path.open

        def open_then_replace(path: Path, *args: object, **kwargs: object):
            handle = real_open(path, *args, **kwargs)
            if path == source.resolve():
                replacement.replace(path)
            return handle

        with patch.object(Path, "open", open_then_replace):
            excerpt = source_excerpt(
                self.root,
                "notes/demo.md",
                2,
                radius=1,
                expected_sha256=sha256_text(self.source_text),
            )
        self.assertEqual("two", excerpt["lines"][1]["text"])
        self.assertEqual("new\ngeneration\n", source.read_text(encoding="utf-8"))

    def test_http_routes_serve_payload_static_assets_and_source(self) -> None:
        source = self.root / "notes/demo.md"
        source.parent.mkdir(parents=True)
        source.write_text(self.source_text, encoding="utf-8")
        server = create_graph_server(self.root, self.graph, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        try:
            connection.request("GET", "/api/graph.json")
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(200, response.status)
            self.assertEqual("Demo", payload["nodes"][0]["label"])
            self.assertEqual("no-store", response.getheader("Cache-Control"))
            self.assertEqual("nosniff", response.getheader("X-Content-Type-Options"))
            self.assertIn("default-src 'self'", response.getheader("Content-Security-Policy"))

            connection.request("GET", "/")
            response = connection.getresponse()
            body = response.read().decode("utf-8")
            self.assertEqual(200, response.status)
            self.assertIn("text/html", response.getheader("Content-Type", ""))
            self.assertIn("kgdistiller", body)

            connection.request("GET", "/app.js")
            response = connection.getresponse()
            app = response.read().decode("utf-8")
            self.assertEqual(200, response.status)
            self.assertIn("javascript", response.getheader("Content-Type", ""))
            self.assertIn('["common_confusions", "Common confusions"]', app)
            self.assertIn("snapshot=${encodeURIComponent(snapshot)}", app)

            snapshot = payload["manifest"]["snapshot_sha256"]
            connection.request(
                "GET",
                "/api/source?path=notes%2Fdemo.md&line=2"
                f"&snapshot={snapshot}",
            )
            response = connection.getresponse()
            excerpt = json.loads(response.read())
            self.assertEqual(200, response.status)
            self.assertEqual("notes/demo.md", excerpt["authority"])
            self.assertEqual(2, excerpt["line"])
            self.assertEqual("two", excerpt["lines"][1]["text"])
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_http_source_rejects_authority_stale_against_loaded_graph_view(self) -> None:
        source = self.root / "notes/demo.md"
        source.parent.mkdir(parents=True)
        source.write_text(self.source_text, encoding="utf-8")
        server = create_graph_server(self.root, self.graph, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        try:
            snapshot = load_graph_payload(self.graph)["manifest"]["snapshot_sha256"]
            source.write_text(self.source_text + "changed\n", encoding="utf-8")
            connection.request(
                "GET",
                "/api/source?path=notes%2Fdemo.md&line=2"
                f"&snapshot={snapshot}",
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(409, response.status)
            self.assertIn("stale source authority", payload["error"])

            source.unlink()
            connection.request(
                "GET",
                "/api/source?path=notes%2Fdemo.md&line=2"
                f"&snapshot={snapshot}",
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(409, response.status)
            self.assertIn("stale source authority", payload["error"])
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_http_source_decodes_authority_path_exactly_once(self) -> None:
        literal_authority = "notes/%2e%2e/demo.md"
        literal_text = "literal percent path\n"
        literal_source = self.root / literal_authority
        literal_source.parent.mkdir(parents=True)
        literal_source.write_text(literal_text, encoding="utf-8")
        # This is the path an erroneous second URL decode would select.
        (self.root / "demo.md").write_text("wrong source\n", encoding="utf-8")
        state = load_state(self.graph)
        write_artifacts(
            self.graph,
            make_artifacts(
                state,
                {
                    "notes/demo.md": sha256_text(self.source_text),
                    literal_authority: sha256_text(literal_text),
                },
            ),
        )
        server = create_graph_server(self.root, self.graph, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        try:
            snapshot = load_graph_payload(self.graph)["manifest"]["snapshot_sha256"]
            connection.request(
                "GET",
                "/api/source?path=notes%2F%252e%252e%2Fdemo.md&line=1"
                f"&snapshot={snapshot}",
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(200, response.status)
            self.assertEqual(literal_authority, payload["authority"])
            self.assertEqual("literal percent path", payload["lines"][0]["text"])
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_http_source_rejects_browser_snapshot_from_previous_generation(self) -> None:
        source = self.root / "notes/demo.md"
        source.parent.mkdir(parents=True)
        source.write_text(self.source_text, encoding="utf-8")
        browser_snapshot = load_graph_payload(self.graph)["manifest"]["snapshot_sha256"]
        server = create_graph_server(self.root, self.graph, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        try:
            state = load_state(self.graph)
            state.nodes["demo"]["label"] = "Demo generation B"
            write_artifacts(
                self.graph,
                make_artifacts(
                    state,
                    {"notes/demo.md": sha256_text(self.source_text)},
                ),
            )
            connection.request(
                "GET",
                "/api/source?path=notes%2Fdemo.md&line=2"
                f"&snapshot={browser_snapshot}",
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(409, response.status)
            self.assertIn("stale browser graph generation", payload["error"])
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_http_routes_reject_traversal_bad_lines_and_nested_static_paths(self) -> None:
        private = self.root.parent / f"{self.root.name}-private.md"
        private.write_text("secret\n", encoding="utf-8")
        server = create_graph_server(self.root, self.graph, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        try:
            snapshot = load_graph_payload(self.graph)["manifest"]["snapshot_sha256"]
            for path in (
                f"/api/source?path=..%2F{private.name}&line=1&snapshot={snapshot}",
                "/api/source?path=notes%2Fdemo.md&line=not-a-number"
                f"&snapshot={snapshot}",
                "/api/source?path=knowledge%2Fsources.json&line=1"
                f"&snapshot={snapshot}",
            ):
                connection.request("GET", path)
                response = connection.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(400, response.status)
                self.assertIn("error", payload)

            connection.request("GET", "/nested/app.js")
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(404, response.status)
            self.assertEqual({"error": "not found"}, payload)

            connection.request("GET", "/x%5C..%5C..%5Cweb.py")
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(404, response.status)
            self.assertEqual({"error": "not found"}, payload)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)
            private.unlink(missing_ok=True)

    def test_http_rejects_forged_host_and_origin(self) -> None:
        server = create_graph_server(self.root, self.graph, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        try:
            connection.request(
                "GET",
                "/",
                headers={"Host": f"attacker.example:{server.server_port}"},
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(421, response.status)
            self.assertEqual({"error": "misdirected request host"}, payload)

            connection.request(
                "GET",
                "/",
                headers={
                    "Host": f"127.0.0.1:{server.server_port}",
                    "Origin": f"http://attacker.example:{server.server_port}",
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(403, response.status)
            self.assertEqual({"error": "forbidden request origin"}, payload)

            connection.request(
                "GET",
                "/",
                headers={
                    "Host": f"localhost:{server.server_port}",
                    "Origin": f"http://localhost:{server.server_port}",
                },
            )
            response = connection.getresponse()
            response.read()
            self.assertEqual(200, response.status)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
