#!/usr/bin/env python3
"""Validate mechanical invariants of a qlpaper Markdown package."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


SCHEMA = "qlpaper-markdown-source-v1"
OBJECT_RE = re.compile(
    r"^<!-- qlpaper-object: kind=(figure|table); label=([^;]+); page=(\d+) -->$",
    re.M,
)
FORBIDDEN_MEDIA = (
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), "Markdown image embed"),
    (re.compile(r"<\s*(?:img|picture|video|object|embed)\b", re.I), "HTML media embed"),
    (re.compile(r"data:image/", re.I), "base64 image data"),
)
FORBIDDEN_CONVERSION_DEBRIS = (
    (
        re.compile(
            r"<\s*/?\s*[A-Za-z][A-Za-z0-9-]*(?:\s+[^<>]*?)?/?>",
            re.I,
        ),
        "raw HTML tag",
    ),
    (
        re.compile(r"^```\s*(?:math|latex|tex)\s*$", re.I | re.M),
        "fenced TeX math block",
    ),
    (re.compile(r"\$`|`\$"), "Pandoc backtick math delimiter"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    return parser.parse_args()


def candidate_key(item: dict[str, Any]) -> tuple[str, str, int]:
    return (str(item.get("kind")), str(item.get("label")), int(item.get("page", 0)))


def package_path(root: Path, raw: object) -> Path:
    path = (root / str(raw)).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"package path escapes its root: {raw}") from error
    return path


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest.expanduser().resolve()
    markdown_path = args.markdown.expanduser().resolve()
    errors: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        content = markdown_path.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as error:
        print(f"validate_paper_markdown: {error}", file=sys.stderr)
        return 1

    if manifest.get("schema") != SCHEMA:
        errors.append(f"manifest schema must be {SCHEMA}")
    if not content.startswith("<!-- qlpaper-markdown-v1 -->\n"):
        errors.append("missing qlpaper-markdown-v1 header")
    if "QLPAPER_UNRESOLVED" in content:
        errors.append("unresolved transcription or object markers remain")
    if "\ufffd" in content:
        errors.append("Markdown contains Unicode replacement characters")
    if "\x00" in content:
        errors.append("Markdown contains NUL characters")

    page_count = manifest.get("page_count")
    if not isinstance(page_count, int) or page_count < 1:
        errors.append("manifest page_count must be a positive integer")
        page_count = 0
    expected_pages = list(range(1, page_count + 1))
    found_pages = [
        int(value)
        for value in re.findall(r"^<!-- qlpaper-source: page=(\d+) -->$", content, re.M)
    ]
    if found_pages != expected_pages:
        errors.append(
            "page markers must appear exactly once in source order: "
            f"expected {expected_pages}, found {found_pages}"
        )

    expected_digest = str(manifest.get("source_sha256", ""))
    digest_match = re.search(
        r"^<!-- qlpaper-source-sha256: ([0-9a-f]{64}) -->$", content, re.M
    )
    if digest_match is None or digest_match.group(1) != expected_digest:
        errors.append("Markdown source digest does not match manifest")
    try:
        source_pdf = package_path(
            manifest_path.parent, manifest.get("source_pdf", "source.pdf")
        )
    except ValueError as error:
        errors.append(str(error))
        source_pdf = manifest_path.parent / "__invalid_source.pdf"
    if not source_pdf.is_file():
        errors.append(f"source PDF is missing: {source_pdf}")
    elif expected_digest and sha256(source_pdf) != expected_digest:
        errors.append("source PDF digest no longer matches manifest")

    try:
        declared_markdown = package_path(
            manifest_path.parent, manifest.get("markdown", "paper.md")
        )
    except ValueError as error:
        errors.append(str(error))
    else:
        if declared_markdown != markdown_path:
            errors.append("validated Markdown path does not match the manifest")

    for pattern, label in FORBIDDEN_MEDIA:
        if pattern.search(content):
            errors.append(f"forbidden {label} remains")
    for pattern, label in FORBIDDEN_CONVERSION_DEBRIS:
        if pattern.search(content):
            errors.append(f"forbidden conversion debris remains: {label}")

    found_objects: list[tuple[str, str, int]] = []
    matches = list(OBJECT_RE.finditer(content))
    for index, match in enumerate(matches):
        key = (match.group(1), match.group(2).strip(), int(match.group(3)))
        found_objects.append(key)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        block = content[match.end() : end]
        for field in ("`summary`:", "`paper-use`:", "`uncertainty`:"):
            if field not in block:
                errors.append(f"object {key} is missing {field}")
    if len(found_objects) != len(set(found_objects)):
        errors.append("duplicate qlpaper object markers found")

    raw_candidates = manifest.get("object_candidates", [])
    if not isinstance(raw_candidates, list):
        errors.append("manifest object_candidates must be a list")
        raw_candidates = []
    try:
        expected_objects = [
            candidate_key(item) for item in raw_candidates if isinstance(item, dict)
        ]
    except (TypeError, ValueError):
        errors.append("manifest contains an invalid object candidate")
        expected_objects = []
    missing_objects = sorted(set(expected_objects) - set(found_objects))
    if missing_objects:
        errors.append(f"Markdown is missing manifest object candidates: {missing_objects}")

    raw_pages = manifest.get("pages", [])
    if not isinstance(raw_pages, list):
        errors.append("manifest pages must be a list")
        raw_pages = []
    for page in expected_pages:
        record = next(
            (item for item in raw_pages if isinstance(item, dict) and item.get("page") == page),
            None,
        )
        if record is None:
            errors.append(f"manifest has no record for source page {page}")
            continue
        try:
            text_path = package_path(manifest_path.parent, record.get("text_path", ""))
        except ValueError as error:
            errors.append(str(error))
            continue
        if not text_path.is_file():
            errors.append(f"page {page} extracted text is missing: {text_path}")
        elif b"\x00" in text_path.read_bytes():
            errors.append(f"page {page} extracted text contains NUL characters")

    raw_visual = manifest.get("visual_pages", [])
    if not isinstance(raw_visual, list):
        errors.append("manifest visual_pages must be a list")
        raw_visual = []
    for item in raw_visual:
        if not isinstance(item, dict):
            errors.append("manifest contains an invalid visual-page record")
            continue
        try:
            render = package_path(manifest_path.parent, item.get("path", ""))
        except ValueError as error:
            errors.append(str(error))
            continue
        if not render.is_file():
            errors.append(f"targeted visual render is missing: {render}")

    raw_attachments = manifest.get("attachments", [])
    if not isinstance(raw_attachments, list):
        errors.append("manifest attachments must be a list")
        raw_attachments = []
    for item in raw_attachments:
        raw_path = item.get("path") if isinstance(item, dict) else item
        try:
            attachment = package_path(manifest_path.parent, raw_path)
        except ValueError as error:
            errors.append(str(error))
            continue
        if not attachment.is_file():
            errors.append(f"semantic attachment is missing: {attachment}")
            continue
        if attachment.suffix.lower() != ".md":
            errors.append(f"semantic attachment must be Markdown: {attachment}")
            continue
        attachment_text = attachment.read_text(encoding="utf-8")
        if "QLPAPER_UNRESOLVED" in attachment_text or "\ufffd" in attachment_text:
            errors.append(f"semantic attachment is unresolved or corrupted: {attachment}")
        for pattern, label in FORBIDDEN_MEDIA:
            if pattern.search(attachment_text):
                errors.append(f"attachment contains forbidden {label}: {attachment}")
        for pattern, label in FORBIDDEN_CONVERSION_DEBRIS:
            if pattern.search(attachment_text):
                errors.append(
                    f"attachment contains forbidden conversion debris ({label}): "
                    f"{attachment}"
                )

    if errors:
        for error in errors:
            print(f"validate_paper_markdown: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "schema": "qlpaper-markdown-validation-v1",
                "status": "ok",
                "pages": page_count,
                "objects": len(found_objects),
                "markdown_sha256": sha256(markdown_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
