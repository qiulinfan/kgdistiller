#!/usr/bin/env python3
"""Prepare a PDF for semantic, image-free Markdown transcription."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any


SCHEMA = "qlpaper-markdown-source-v1"
CAPTION_RE = re.compile(
    r"(?im)^\s*(Figure|Fig\.?|Table)\s*"
    r"([A-Z]?\d+(?:[.\-]\d+)?|[IVXLCDM]+)\s*[:.\-]?\s*(.{0,240})$"
)


class PrepareError(RuntimeError):
    """Raised when a PDF cannot be prepared safely."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_empty_output(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise PrepareError(f"output directory is not empty: {path}")
    path.mkdir(parents=True, exist_ok=True)


def find_captions(text: str, page: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for match in CAPTION_RE.finditer(text):
        raw_kind, number, caption = match.groups()
        kind = "table" if raw_kind.lower().startswith("table") else "figure"
        label = f"{'Table' if kind == 'table' else 'Figure'} {number}"
        key = (kind, number.lower())
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "kind": kind,
                "label": label,
                "page": page,
                "caption_excerpt": " ".join(caption.split())[:240],
                "caption_near_page_start": match.start() < max(300, len(text) // 5),
            }
        )
    return candidates


def substantial_image_count(page: Any) -> int:
    page_area = float(page.width) * float(page.height)
    if page_area <= 0:
        return 0
    count = 0
    for image in page.images:
        width = abs(float(image.get("x1", 0)) - float(image.get("x0", 0)))
        height = abs(float(image.get("bottom", 0)) - float(image.get("top", 0)))
        if width * height / page_area >= 0.04:
            count += 1
    return count


def extract_pdf(source: Path, text_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        import pdfplumber  # type: ignore[import-not-found]
    except ImportError as error:
        raise PrepareError(
            "pdfplumber is required; install the kgdistiller paper extra"
        ) from error
    text_dir.mkdir(parents=True, exist_ok=True)
    pages: list[dict[str, Any]] = []
    with pdfplumber.open(source) as pdf:
        metadata = {str(key): str(value) for key, value in (pdf.metadata or {}).items()}
        for page_number, page in enumerate(pdf.pages, start=1):
            extracted = page.extract_text(layout=True) or ""
            nul_characters = extracted.count("\x00")
            if nul_characters:
                extracted = extracted.replace("\x00", "")
            text_path = text_dir / f"page-{page_number:04d}.txt"
            text_path.write_text(extracted.rstrip() + "\n", encoding="utf-8")
            compact = "".join(extracted.split())
            replacements = extracted.count("\ufffd")
            captions = find_captions(extracted, page_number)
            reasons: list[str] = []
            if captions:
                reasons.append("caption-candidate")
            if len(compact) < 120:
                reasons.append("low-text")
            if replacements > max(2, len(compact) // 100):
                reasons.append("text-corruption")
            significant_images = substantial_image_count(page)
            if significant_images:
                reasons.append("substantial-raster-object")
            pages.append(
                {
                    "page": page_number,
                    "text_path": text_path.relative_to(text_dir.parent.parent).as_posix(),
                    "extracted_characters": len(compact),
                    "replacement_characters": replacements,
                    "nul_characters_removed": nul_characters,
                    "embedded_images": len(page.images),
                    "substantial_images": significant_images,
                    "caption_candidates": captions,
                    "visual_reasons": reasons,
                }
            )
    return metadata, pages


def render_page(source: Path, target: Path, page: int, dpi: int) -> None:
    renderer = shutil.which("pdftoppm")
    if renderer is None:
        raise PrepareError("pdftoppm is required for targeted visual pages")
    target.parent.mkdir(parents=True, exist_ok=True)
    prefix = target.with_suffix("")
    result = subprocess.run(
        [
            renderer,
            "-f",
            str(page),
            "-l",
            str(page),
            "-singlefile",
            "-png",
            "-r",
            str(dpi),
            str(source),
            str(prefix),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise PrepareError(result.stderr.strip() or f"pdftoppm failed on page {page}")
    produced = prefix.with_suffix(".png")
    if produced != target:
        produced.rename(target)


def visual_page_numbers(pages: list[dict[str, Any]]) -> dict[int, set[str]]:
    selected: dict[int, set[str]] = {}
    for record in pages:
        page = int(record["page"])
        reasons = set(record["visual_reasons"])
        if reasons:
            selected.setdefault(page, set()).update(reasons)
        if page > 1 and any(
            bool(item["caption_near_page_start"])
            for item in record["caption_candidates"]
        ):
            selected.setdefault(page - 1, set()).add("adjacent-to-top-caption")
    return selected


def write_markdown_skeleton(path: Path, digest: str, pages: list[dict[str, Any]]) -> None:
    lines = [
        "<!-- qlpaper-markdown-v1 -->",
        f"<!-- qlpaper-source-sha256: {digest} -->",
        "",
        "# QLPAPER_UNRESOLVED: paper title",
        "",
        "> QLPAPER_UNRESOLVED: authors, version, identifier, and source provenance",
    ]
    for record in pages:
        page = int(record["page"])
        lines.extend(
            [
                "",
                f"<!-- qlpaper-source: page={page} -->",
                f"QLPAPER_UNRESOLVED: transcribe semantic content from PDF page {page}.",
            ]
        )
        for candidate in record["caption_candidates"]:
            lines.extend(
                [
                    "",
                    "<!-- qlpaper-object: "
                    f"kind={candidate['kind']}; label={candidate['label']}; page={page} -->",
                    f"> **{candidate['label']} — QLPAPER_UNRESOLVED: title**",
                    ">",
                    "> - `summary`: QLPAPER_UNRESOLVED",
                    "> - `paper-use`: QLPAPER_UNRESOLVED",
                    "> - `uncertainty`: QLPAPER_UNRESOLVED",
                ]
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="local canonical PDF")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--landing-url")
    parser.add_argument("--pdf-url")
    parser.add_argument("--identifier")
    parser.add_argument("--version")
    parser.add_argument("--dpi", type=int, default=144)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_input = args.pdf.expanduser().resolve()
    output = args.output_dir.expanduser().resolve()
    try:
        if not source_input.is_file():
            raise PrepareError(f"PDF does not exist: {source_input}")
        with source_input.open("rb") as stream:
            signature = stream.read(5)
        if signature != b"%PDF-":
            raise PrepareError(f"input is not a PDF file: {source_input}")
        if not 72 <= args.dpi <= 300:
            raise PrepareError("--dpi must be between 72 and 300")
        require_empty_output(output)
        source = output / "source.pdf"
        shutil.copyfile(source_input, source)
        digest = sha256(source)
        metadata, pages = extract_pdf(source, output / "evidence" / "text")
        if not pages:
            raise PrepareError("PDF contains no pages")
        selected = visual_page_numbers(pages)
        visual_records: list[dict[str, Any]] = []
        for page, reasons in sorted(selected.items()):
            target = output / "evidence" / "visual" / f"page-{page:04d}.png"
            render_page(source, target, page, args.dpi)
            visual_records.append(
                {
                    "page": page,
                    "path": target.relative_to(output).as_posix(),
                    "reasons": sorted(reasons),
                }
            )
        markdown = output / "paper.md"
        write_markdown_skeleton(markdown, digest, pages)
        object_candidates = [
            candidate
            for record in pages
            for candidate in record["caption_candidates"]
        ]
        manifest = {
            "schema": SCHEMA,
            "source_pdf": "source.pdf",
            "source_sha256": digest,
            "source_bytes": source.stat().st_size,
            "page_count": len(pages),
            "metadata": metadata,
            "acquisition": {
                "landing_url": args.landing_url,
                "pdf_url": args.pdf_url,
                "identifier": args.identifier,
                "version": args.version,
                "access_date": date.today().isoformat(),
            },
            "tools": {"text": "pdfplumber", "render": "pdftoppm", "dpi": args.dpi},
            "pages": pages,
            "detected_object_candidates": object_candidates,
            "object_candidates": object_candidates,
            "visual_pages": visual_records,
            "markdown": "paper.md",
            "attachments": [],
        }
        (output / "source.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, PrepareError, ValueError) as error:
        print(f"prepare_paper: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "schema": SCHEMA,
                "output": str(output),
                "pages": len(pages),
                "objects": len(object_candidates),
                "visual_pages": len(visual_records),
                "source_sha256": digest,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
