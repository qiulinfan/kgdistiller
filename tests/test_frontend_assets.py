from __future__ import annotations

import contextlib
import http.client
import hashlib
import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from importlib import resources
from pathlib import Path
from unittest import mock

from kgdistiller import api as api_module
from kgdistiller.api import ApiService, FederationSnapshotCache, StaticAsset
from kgdistiller.contracts import (
    ContractError,
    canonical_json,
    parse_contract_json,
    self_digest,
    validate_contract,
)
from kgdistiller.federation import FederationSnapshot
from kgdistiller.frontend_assets import FrontendAssetError, PackagedStaticAssetProvider


def empty_snapshot() -> FederationSnapshot:
    registry = "1" * 64
    generation = api_module.sha256_json(
        {
            "registry_generation": registry,
            "vaults": [],
            "incomplete_vaults": [],
        }
    )
    return FederationSnapshot(
        registry_generation=registry,
        generation=generation,
        vaults=(),
        incomplete_vaults=(),
    )


def packaged_root():
    return resources.files("kgdistiller").joinpath("static").joinpath("v1")


class PackagedProviderTests(unittest.TestCase):
    def test_packaged_inventory_is_git_binary_and_provider_valid(self) -> None:
        repository = Path(__file__).parents[1]
        manifest = validate_contract(
            parse_contract_json(packaged_root().joinpath("bundle.json").read_text("utf-8"))
        )
        inventory = ["bundle.json", *(record["path"] for record in manifest["files"])]
        for relative in inventory:
            with self.subTest(path=relative):
                repository_relative = f"src/kgdistiller/static/v1/{relative}"
                checked = subprocess.run(
                    ["git", "check-attr", "text", "--", repository_relative],
                    cwd=repository,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, checked.returncode, checked.stderr)
                self.assertEqual(
                    f"{repository_relative}: text: unset\n",
                    checked.stdout.replace("\r\n", "\n"),
                )

        provider = PackagedStaticAssetProvider()
        self.assertIsNotNone(provider.resolve("/"))
        for record in manifest["files"]:
            if record["path"] == "index.html":
                continue
            self.assertIsNotNone(provider.resolve(f"/{record['path']}"))

    def test_importlib_provider_is_closed_and_byte_only(self) -> None:
        provider = PackagedStaticAssetProvider()
        index = provider.resolve("/")
        self.assertIsNotNone(index)
        assert index is not None
        self.assertIsInstance(index.content, bytes)
        self.assertEqual("no-store", index.cache_control)
        self.assertIsNone(provider.resolve("/index.html"))
        self.assertIsNone(provider.resolve("/missing"))
        self.assertIsNone(provider.resolve("/assets/%2e%2e/index.html"))
        manifest = validate_contract(
            parse_contract_json(packaged_root().joinpath("bundle.json").read_text("utf-8"))
        )
        for record in manifest["files"]:
            if record["path"] == "index.html":
                continue
            asset = provider.resolve(f"/{record['path']}")
            self.assertIsNotNone(asset)
            assert asset is not None
            self.assertEqual(record["bytes"], len(asset.content))
            self.assertEqual(f'"{record["sha256"]}"', asset.etag)

    def test_corrupt_or_extra_bundle_files_fail_at_startup(self) -> None:
        source = Path(__file__).parents[1] / "src" / "kgdistiller" / "static" / "v1"
        for mutation in ("corrupt", "extra"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                package_root = Path(directory) / "static" / "v1"
                shutil.copytree(source, package_root)
                if mutation == "extra":
                    (package_root / "escape.js").write_bytes(b"third")
                else:
                    manifest = json.loads((package_root / "bundle.json").read_text("utf-8"))
                    asset = next(item["path"] for item in manifest["files"] if item["path"].endswith(".js"))
                    (package_root / asset).write_bytes(b"corrupt")
                with mock.patch("kgdistiller.frontend_assets.resources.files", return_value=Path(directory)):
                    with self.assertRaises(FrontendAssetError):
                        PackagedStaticAssetProvider()

    def test_zip_import_resources_use_the_same_closed_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "frontend.zip"
            source = Path(__file__).parents[1] / "src" / "kgdistiller" / "static" / "v1"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("zipfrontend/__init__.py", "")
                for path in sorted(source.rglob("*")):
                    if path.is_file():
                        package.write(path, f"zipfrontend/static/v1/{path.relative_to(source).as_posix()}")
            sys.path.insert(0, str(archive))
            importlib.invalidate_caches()
            try:
                provider = PackagedStaticAssetProvider(package="zipfrontend")
                self.assertIsNotNone(provider.resolve("/"))
                self.assertIsNone(provider.resolve("/index.html"))
            finally:
                sys.path.remove(str(archive))
                sys.modules.pop("zipfrontend", None)
                importlib.invalidate_caches()

    def test_manifest_portable_collision_is_invalid_even_when_resigned(self) -> None:
        manifest = json.loads(packaged_root().joinpath("bundle.json").read_text("utf-8"))
        asset = dict(next(item for item in manifest["files"] if item["path"].endswith(".js")))
        asset["path"] = asset["path"].swapcase()
        manifest["files"].append(asset)
        manifest["files"].sort(key=lambda item: item["path"])
        manifest["bundle_sha256"] = self_digest(manifest, "bundle_sha256")
        with self.assertRaises(ContractError):
            validate_contract(manifest)

    def test_remote_special_urls_are_rejected_even_when_bundle_is_resigned(self) -> None:
        source = Path(__file__).parents[1] / "src" / "kgdistiller" / "static" / "v1"
        for remote in (
            "https:evil.example/x",
            "http://www.w3.org/2000/svg.evil.example/x",
            "//例子.测试/x",
            r"\\\\evil.example\x",
        ):
            with self.subTest(remote=remote), tempfile.TemporaryDirectory() as directory:
                package_root = Path(directory) / "static" / "v1"
                shutil.copytree(source, package_root)
                manifest_path = package_root / "bundle.json"
                manifest = json.loads(manifest_path.read_text("utf-8"))
                record = next(item for item in manifest["files"] if item["path"].endswith(".js"))
                asset_path = package_root.joinpath(*record["path"].split("/"))
                hostile = asset_path.read_bytes() + f"\nconst remote = '{remote}';\n".encode("utf-8")
                asset_path.write_bytes(hostile)
                record["bytes"] = len(hostile)
                record["sha256"] = hashlib.sha256(hostile).hexdigest()
                manifest["bundle_sha256"] = self_digest(manifest, "bundle_sha256")
                manifest_path.write_bytes((canonical_json(manifest) + "\n").encode("utf-8"))
                with mock.patch("kgdistiller.frontend_assets.resources.files", return_value=Path(directory)):
                    with self.assertRaises(FrontendAssetError):
                        PackagedStaticAssetProvider()

    def test_packaged_styles_bind_accessibility_media_preferences(self) -> None:
        provider = PackagedStaticAssetProvider()
        manifest = json.loads(packaged_root().joinpath("bundle.json").read_text("utf-8"))
        styles = b"\n".join(
            provider.resolve(f"/{record['path']}").content
            for record in manifest["files"]
            if record["path"].endswith(".css")
            and provider.resolve(f"/{record['path']}") is not None
        )
        self.assertIn(b"prefers-reduced-motion", styles)
        self.assertIn(b"forced-colors", styles)


class FrontendCliTests(unittest.TestCase):
    def test_bare_serve_loads_packaged_workspace_from_arbitrary_cwd(self) -> None:
        from kgdistiller import cli

        provider = mock.sentinel.provider
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            sys, "argv", ["kgdistiller", "serve", "--no-open", "--port", "0"]
        ), mock.patch(
            "kgdistiller.frontend_assets.PackagedStaticAssetProvider",
            return_value=provider,
        ) as constructor, mock.patch("kgdistiller.api.serve_api") as serve:
            previous = Path.cwd()
            os.chdir(directory)
            try:
                self.assertEqual(0, cli.main())
            finally:
                os.chdir(previous)
        constructor.assert_called_once_with()
        serve.assert_called_once_with(
            host="127.0.0.1",
            port=0,
            static_assets=provider,
            open_browser=False,
        )

    def test_explicit_legacy_serve_stays_on_the_unversioned_server(self) -> None:
        from kgdistiller import cli

        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            sys,
            "argv",
            ["kgdistiller", "--repo-root", directory, "serve", "--legacy", "--no-open"],
        ), mock.patch("kgdistiller.web.serve_graph") as serve, mock.patch(
            "kgdistiller.frontend_assets.PackagedStaticAssetProvider"
        ) as constructor:
            self.assertEqual(0, cli.main())
        constructor.assert_not_called()
        serve.assert_called_once_with(
            Path(directory).resolve(),
            (Path(directory) / "knowledge" / "graph").resolve(),
            host="127.0.0.1",
            port=8765,
            open_browser=False,
        )

    def test_serve_rejects_an_out_of_range_port_without_leaking_cwd(self) -> None:
        from kgdistiller import cli

        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            sys, "argv", ["kgdistiller", "serve", "--port", "999999"]
        ), contextlib.redirect_stderr(stderr):
            previous = Path.cwd()
            os.chdir(directory)
            try:
                with self.assertRaises(SystemExit) as failure:
                    cli.main()
            finally:
                os.chdir(previous)
        self.assertEqual(2, failure.exception.code)
        self.assertNotIn(directory, stderr.getvalue())
        self.assertIn("0 to 65535", stderr.getvalue())


class StaticHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        packaged = PackagedStaticAssetProvider()

        class ShadowProvider:
            def resolve(self, request_path: str):
                if request_path == "/api/v1/status":
                    return StaticAsset(b"shadow", "text/html; charset=utf-8", '"' + "0" * 64 + '"', "no-store")
                return packaged.resolve(request_path)

        snapshot = empty_snapshot()
        service = ApiService(
            cache=FederationSnapshotCache(capture=lambda **_kwargs: snapshot),
            static_assets=ShadowProvider(),
        )
        self.server = api_module.create_api_server(host="127.0.0.1", port=0, service=service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, method: str, target: str, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        try:
            connection.request(method, target, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.getheaders(), response.read()
        finally:
            connection.close()

    def test_api_precedes_static_and_unknown_paths_never_fallback(self) -> None:
        status, _, body = self.request("GET", "/api/v1/status")
        self.assertEqual(200, status)
        self.assertEqual("qlkg-api-response-v1", json.loads(body)["schema"])
        status, _, body = self.request("GET", "/not-a-route")
        self.assertEqual(404, status)
        self.assertEqual("static-asset-not-found", json.loads(body)["error"]["code"])
        status, _, _ = self.request("GET", "/index.html")
        self.assertEqual(404, status)

    def test_index_and_fingerprinted_assets_have_one_exact_cache_policy(self) -> None:
        status, headers, body = self.request("GET", "/")
        self.assertEqual(200, status)
        self.assertIn(b"kgdistiller", body)
        values = [value for key, value in headers if key.casefold() == "cache-control"]
        self.assertEqual(["no-store"], values)
        csp = dict(headers)["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        manifest = json.loads(packaged_root().joinpath("bundle.json").read_text("utf-8"))
        asset_path = next(item["path"] for item in manifest["files"] if item["path"].endswith(".js"))
        status, headers, _ = self.request("GET", f"/{asset_path}")
        self.assertEqual(200, status)
        header_map = dict(headers)
        self.assertEqual("public, max-age=31536000, immutable", header_map["Cache-Control"])
        status, _, body = self.request("GET", f"/{asset_path}", {"If-None-Match": header_map["ETag"]})
        self.assertEqual(304, status)
        self.assertEqual(b"", body)


if __name__ == "__main__":
    unittest.main()
