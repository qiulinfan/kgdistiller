"""Closed importlib-resource provider for the packaged F8 frontend."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from importlib import resources
from typing import BinaryIO, Iterable, Protocol

from .api import StaticAsset
from .contracts import ContractError, canonical_json, parse_contract_json, validate_contract


MAX_BUNDLE_MANIFEST_BYTES = 512 * 1024
MAX_BUNDLE_FILE_BYTES = 4 * 1024 * 1024
MAX_BUNDLE_BYTES = 16 * 1024 * 1024
MAX_BUNDLE_FILES = 128
MAX_BUNDLE_ENTRIES = MAX_BUNDLE_FILES + 4
_REMOTE_SCHEME_RE = re.compile(r"(?:https?|wss?|ftp):", re.IGNORECASE)
_PROTOCOL_RELATIVE_RE = re.compile(r'''["'`(=]\s*//[^\s"'`<>{}]''')
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


class FrontendAssetError(RuntimeError):
    """Stable path-free packaged-frontend startup failure."""

    code = "frontend-bundle-invalid"

    def __init__(self) -> None:
        super().__init__("packaged frontend bundle is unavailable or invalid")


class _Traversable(Protocol):
    name: str

    def iterdir(self) -> Iterable["_Traversable"]:
        ...

    def is_file(self) -> bool:
        ...

    def is_dir(self) -> bool:
        ...

    def open(self, mode: str = "r", *args: object, **kwargs: object) -> BinaryIO:
        ...

    def joinpath(self, child: str) -> "_Traversable":
        ...


def _read_bounded(resource: _Traversable, maximum: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    with resource.open("rb") as handle:
        reader = handle  # type: BinaryIO
        while True:
            chunk = reader.read(min(64 * 1024, maximum + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise FrontendAssetError()
            chunks.append(chunk)
    return b"".join(chunks)


def _portable_key(path: str) -> tuple[str, ...]:
    if (
        not path
        or len(path.encode("utf-8")) > 4096
        or unicodedata.normalize("NFC", path) != path
        or "\\" in path
        or path.startswith("/")
        or path.endswith("/")
    ):
        raise FrontendAssetError()
    parts = path.split("/")
    if any(
        not part
        or part in {".", ".."}
        or part.endswith((" ", "."))
        or any(ord(character) < 32 or ord(character) == 127 for character in part)
        or any(character in '<>:"|?*' for character in part)
        or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED
        for part in parts
    ):
        raise FrontendAssetError()
    return tuple(part.casefold() for part in parts)


def _inventory(root: _Traversable) -> tuple[dict[str, _Traversable], set[str]]:
    files: dict[str, _Traversable] = {}
    directories: set[str] = set()
    pending: list[tuple[_Traversable, str, int]] = [(root, "", 0)]
    entries = 0
    while pending:
        directory, prefix, depth = pending.pop()
        if depth > 3:
            raise FrontendAssetError()
        children: list[_Traversable] = []
        for child in directory.iterdir():
            entries += 1
            if entries > MAX_BUNDLE_ENTRIES:
                raise FrontendAssetError()
            children.append(child)
        for child in sorted(children, key=lambda item: item.name, reverse=True):
            name = child.name
            relative = f"{prefix}/{name}" if prefix else name
            _portable_key(relative)
            is_file = child.is_file()
            is_dir = child.is_dir()
            if is_file == is_dir:
                raise FrontendAssetError()
            if is_file:
                if relative in files or len(files) >= MAX_BUNDLE_FILES + 1:
                    raise FrontendAssetError()
                files[relative] = child
            else:
                directories.add(relative)
                pending.append((child, relative, depth + 1))
    folded = [_portable_key(path) for path in [*files, *directories]]
    if len(folded) != len(set(folded)):
        raise FrontendAssetError()
    return files, directories


def _assert_offline(path: str, data: bytes) -> str:
    text = data.decode("utf-8", errors="strict")
    scanned = re.sub(
        r'''(["'`])http://www\.w3\.org/2000/svg\1''',
        r"\1__SVG_NAMESPACE__\1",
        text,
    )
    scanned = re.sub(r"\\(?:u002f|x2f|/)", "/", scanned, flags=re.IGNORECASE)
    backslash_pattern = (
        r'''["'`(=]\s*\\{4,}[^\s"'`<>{}]'''
        if path.endswith(".js")
        else r'''["'`(=]\s*\\{2,}[^\s"'`<>{}]'''
    )
    if (
        _REMOTE_SCHEME_RE.search(scanned)
        or _PROTOCOL_RELATIVE_RE.search(scanned)
        or re.search(backslash_pattern, scanned)
        or "sourceMappingURL" in text
    ):
        raise FrontendAssetError()
    return text


class PackagedStaticAssetProvider:
    """Hydrate and validate the complete packaged frontend before serving bytes."""

    def __init__(self, *, package: str = "kgdistiller") -> None:
        try:
            root = resources.files(package).joinpath("static").joinpath("v1")
            self._assets, self.bundle_sha256 = self._load(root)
        except FrontendAssetError:
            raise
        except (ContractError, OSError, UnicodeError, ValueError, TypeError, RecursionError):
            raise FrontendAssetError() from None

    @staticmethod
    def _load(root: _Traversable) -> tuple[dict[str, StaticAsset], str]:
        first_files, first_directories = _inventory(root)
        manifest_resource = first_files.get("bundle.json")
        if manifest_resource is None:
            raise FrontendAssetError()
        manifest_bytes = _read_bounded(manifest_resource, MAX_BUNDLE_MANIFEST_BYTES)
        manifest_text = manifest_bytes.decode("utf-8", errors="strict")
        manifest = validate_contract(parse_contract_json(manifest_text))
        if manifest.get("schema") != "qlkg-frontend-bundle-v1":
            raise FrontendAssetError()
        if manifest_bytes != (canonical_json(manifest) + "\n").encode("utf-8"):
            raise FrontendAssetError()
        records = list(manifest["files"])
        expected_paths = [str(record["path"]) for record in records]
        expected_inventory = {"bundle.json", *expected_paths}
        if set(first_files) != expected_inventory:
            raise FrontendAssetError()
        expected_directories = {
            "/".join(path.split("/")[:index])
            for path in expected_inventory
            for index in range(1, len(path.split("/")))
        }
        if first_directories != expected_directories:
            raise FrontendAssetError()

        assets: dict[str, StaticAsset] = {}
        actual_total = len(manifest_bytes)
        index_text: str | None = None
        for record in records:
            path = str(record["path"])
            declared = int(record["bytes"])
            if declared < 1 or declared > MAX_BUNDLE_FILE_BYTES:
                raise FrontendAssetError()
            resource = first_files[path]
            first = _read_bounded(resource, declared)
            second = _read_bounded(resource, declared)
            if first != second or len(second) != declared:
                raise FrontendAssetError()
            if hashlib.sha256(second).hexdigest() != record["sha256"]:
                raise FrontendAssetError()
            text = _assert_offline(path, second)
            if path == "index.html":
                index_text = text
            actual_total += len(second)
            if actual_total > MAX_BUNDLE_BYTES:
                raise FrontendAssetError()
            cache_control = (
                "no-store"
                if record["cache_policy"] == "no-store"
                else "public, max-age=31536000, immutable"
            )
            assets[f"/{path}"] = StaticAsset(
                content=second,
                media_type=str(record["media_type"]),
                etag=f'"{record["sha256"]}"',
                cache_control=cache_control,
            )
        if index_text is None:
            raise FrontendAssetError()
        entry_reference = f'src="/{manifest["entry"]}"'
        if index_text.count(entry_reference) != 1:
            raise FrontendAssetError()
        references = re.findall(r"(?:src|href)=\"([^\"]+)\"", index_text)
        for reference in references:
            if reference.startswith("#"):
                continue
            if not reference.startswith("/") or reference not in assets:
                raise FrontendAssetError()

        second_files, second_directories = _inventory(root)
        if set(second_files) != expected_inventory or second_directories != expected_directories:
            raise FrontendAssetError()
        second_manifest = _read_bounded(second_files["bundle.json"], MAX_BUNDLE_MANIFEST_BYTES)
        if second_manifest != manifest_bytes:
            raise FrontendAssetError()
        for record in records:
            path = str(record["path"])
            current = _read_bounded(second_files[path], int(record["bytes"]))
            if current != assets[f"/{path}"].content:
                raise FrontendAssetError()
        assets["/"] = assets.pop("/index.html")
        return assets, str(manifest["bundle_sha256"])

    def resolve(self, request_path: str) -> StaticAsset | None:
        if (
            not isinstance(request_path, str)
            or not request_path.startswith("/")
            or "?" in request_path
            or "#" in request_path
            or "%" in request_path
            or "\\" in request_path
            or "\0" in request_path
        ):
            return None
        return self._assets.get(request_path)


__all__ = ["FrontendAssetError", "PackagedStaticAssetProvider"]
