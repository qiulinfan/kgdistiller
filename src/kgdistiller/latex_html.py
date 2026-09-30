"""Use an explicit local LaTeX HTML converter without owning a TeX renderer."""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


REQUEST_SCHEMA = "latex-live-html-request-v1"
RESULT_SCHEMA = "latex-live-html-result-v1"
COMMAND_ENV = "KGDISTILLER_LATEX_HTML_COMMAND"


class LatexHtmlError(ValueError):
    """The selected converter failed or returned an invalid result."""


def converter_command() -> list[str]:
    configured = os.environ.get(COMMAND_ENV)
    if configured is None:
        return ["latex-live-export"]
    try:
        command = json.loads(configured)
    except json.JSONDecodeError as error:
        raise LatexHtmlError(f"{COMMAND_ENV} must be a JSON array of command arguments") from error
    if not isinstance(command, list) or not command or any(
        not isinstance(argument, str) or not argument or "\0" in argument
        for argument in command
    ):
        raise LatexHtmlError(f"{COMMAND_ENV} must be a nonempty JSON array of command arguments")
    return command


def run_converter(request: dict[str, Any]) -> dict[str, Any]:
    """One bounded JSON request; commands are never evaluated by a shell."""
    request = {"schema": REQUEST_SCHEMA, **request}
    operation = request.get("operation")
    try:
        process = subprocess.Popen(
            converter_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
        )
        try:
            stdout, stderr = process.communicate(
                json.dumps(request, ensure_ascii=False),
                timeout=120 if operation == "labels" else 600,
            )
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            # The provider's SIGTERM handler aborts and disposes its TeX process
            # groups. Killing only Node immediately would bypass that cleanup.
            process.terminate()
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            raise
    except FileNotFoundError as error:
        raise LatexHtmlError(
            f"LaTeX HTML converter is unavailable; install the obsidian-latex-live "
            f"latex-live-export command or set {COMMAND_ENV} to its JSON argv"
        ) from error
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as error:
        raise LatexHtmlError(f"LaTeX HTML converter failed: {error}") from error
    if process.returncode:
        detail = stderr.strip() or stdout.strip() or f"exit {process.returncode}"
        raise LatexHtmlError(f"LaTeX HTML converter failed: {detail}")
    if len(stdout) > 64 * 1024 * 1024:
        raise LatexHtmlError("LaTeX HTML converter result exceeds 64 MiB")
    try:
        response = json.loads(stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise LatexHtmlError("LaTeX HTML converter returned invalid JSON") from error
    if not isinstance(response, dict) or response.get("schema") != RESULT_SCHEMA:
        raise LatexHtmlError(f"LaTeX HTML converter must return {RESULT_SCHEMA}")
    if response.get("operation") != operation:
        raise LatexHtmlError("LaTeX HTML converter returned another operation")
    return response


_LABEL_TAGS = frozenset(
    {
        "span", "strong", "em", "b", "i", "sub", "sup", "br", "math", "mrow",
        "mi", "mn", "mo", "mtext", "mspace", "ms", "mfrac", "msqrt", "mroot",
        "mstyle", "merror", "mpadded", "mphantom", "mfenced", "menclose", "msub",
        "msup", "msubsup", "munder", "mover", "munderover", "mmultiscripts",
        "mprescripts", "none", "mtable", "mtr", "mlabeledtr", "mtd", "maligngroup",
        "malignmark", "semantics",
        "annotation",
    }
)


class _LabelValidator(HTMLParser):
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _LABEL_TAGS:
            raise LatexHtmlError(f"unsafe element in LaTeX knowledge label: {tag}")
        for key, value in attrs:
            if key.startswith("on") or key in {"style", "href", "src", "xlink:href"}:
                raise LatexHtmlError(f"unsafe attribute in LaTeX knowledge label: {key}")
            if value and ("javascript:" in value.casefold() or "url(" in value.casefold()):
                raise LatexHtmlError("unsafe value in LaTeX knowledge label")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag not in _LABEL_TAGS:
            raise LatexHtmlError(f"unsafe element in LaTeX knowledge label: {tag}")

    def handle_decl(self, decl: str) -> None:
        raise LatexHtmlError("declarations are not allowed in LaTeX knowledge labels")

    def handle_pi(self, data: str) -> None:
        raise LatexHtmlError("processing instructions are not allowed in LaTeX knowledge labels")


def validate_label_html(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 1024 * 1024:
        raise LatexHtmlError("LaTeX knowledge label must contain bounded HTML")
    validator = _LabelValidator(convert_charrefs=True)
    validator.feed(value)
    validator.close()
    return value


def render_latex_labels(state: Any, repo_root: Path | None = None) -> None:
    """Render rich names in batches, leaving plain-source scanning dependency free."""
    groups: dict[str | None, list[dict[str, str]]] = {}
    for node in sorted(state.nodes.values(), key=lambda item: item["id"]):
        properties = node.get("properties") or {}
        if node.get("type") != "knowledge" or properties.get("source_format") != "latex":
            continue
        if properties.get("source_status") == "orphaned" or (node.get("provenance") or {}).get("active") is False:
            # A removed authority has no project macros to load. Its historical
            # label remains a derived view of retained metadata, not a new render.
            cached = properties.get("label_html")
            properties["label_html"] = validate_label_html(cached) if cached else html.escape(str(node["label"]))
            node["properties"] = properties
            continue
        name = str(properties.get("latex_name") or properties.get("source_name") or node["label"])
        if not re.search(r"[\\$]", name):
            properties["label_html"] = html.escape(str(node["label"]))
            node["properties"] = properties
            continue
        authority = str((node.get("provenance") or {}).get("authority", ""))
        source = (repo_root / authority).resolve() if repo_root and authority else None
        if source and repo_root and not source.is_relative_to(repo_root.resolve()):
            raise LatexHtmlError("LaTeX label authority escapes the project")
        source_name = str(source) if source and source.is_file() else None
        groups.setdefault(source_name, []).append({"id": node["id"], "latex": name})
    rendered: dict[str, str] = {}
    for source, labels in groups.items():
        request: dict[str, Any] = {"operation": "labels", "labels": labels}
        if source:
            request["source"] = source
        response = run_converter(request)
        records = response.get("labels")
        if not isinstance(records, list):
            raise LatexHtmlError("LaTeX HTML converter omitted labels")
        expected = {label["id"] for label in labels}
        found: dict[str, str] = {}
        for record in records:
            if not isinstance(record, dict) or record.get("id") not in expected:
                raise LatexHtmlError("LaTeX HTML converter returned an unexpected label")
            node_id = record["id"]
            if node_id in found:
                raise LatexHtmlError("LaTeX HTML converter returned a duplicate label")
            found[node_id] = validate_label_html(record.get("html"))
        if found.keys() != expected:
            raise LatexHtmlError("LaTeX HTML converter omitted knowledge labels")
        rendered.update(found)
    for node_id, value in rendered.items():
        state.nodes[node_id]["properties"]["label_html"] = value


def export_latex_document(
    repo_root: Path,
    source: Path,
    output: Path,
    *,
    state: Any,
    engine: str = "auto",
    replace: bool = False,
) -> dict[str, Any]:
    """Export a native source using explicit graph identities and the local converter."""
    from .cli import atomic_write, sha256_authority_file
    from .latex_registry import marker_registry

    root = repo_root.resolve()
    source = source.resolve()
    try:
        source.relative_to(root)
    except ValueError as error:
        raise LatexHtmlError("LaTeX export source must remain inside the project") from error
    if not source.is_file() or source.suffix.lower() != ".tex":
        raise LatexHtmlError("LaTeX export needs an existing .tex source")
    if output.is_symlink() or (output.exists() and not replace):
        raise LatexHtmlError("LaTeX HTML output already exists; pass --replace")
    if output.resolve() == source or output.suffix.lower() not in {".html", ".htm"}:
        raise LatexHtmlError("LaTeX export output must be a separate HTML file")
    source_hash = sha256_authority_file(source)
    request: dict[str, Any] = {
        "operation": "document",
        "source": str(source),
        "project_root": str(root),
        "markers": marker_registry(state),
    }
    if engine != "auto":
        request["engine"] = engine
    response = run_converter(request)
    document = response.get("html")
    if not isinstance(document, str) or not re.search(r"<html(?:\s|>)", document, re.I):
        raise LatexHtmlError("LaTeX HTML converter omitted the complete document")
    if sha256_authority_file(source) != source_hash:
        raise LatexHtmlError("LaTeX source changed during export; retry")
    for authority, expected in state.manifest.get("source_hashes", {}).items():
        path = (root / authority).resolve()
        if not path.is_relative_to(root) or not path.is_file() or sha256_authority_file(path) != expected:
            raise LatexHtmlError("a knowledge authority changed during export; synchronize and retry")
    if output.is_symlink() or (output.exists() and not replace):
        raise LatexHtmlError("LaTeX HTML output appeared during export; pass --replace")
    atomic_write(output, document)
    return {
        "schema": "kgdistiller-latex-html-export-v1",
        "status": "exported",
        "source": source.relative_to(root).as_posix(),
        "source_sha256": source_hash,
        "output": str(output),
        "report": response.get("report", {}),
    }
