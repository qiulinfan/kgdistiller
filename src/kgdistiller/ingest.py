"""Transactional, review-gated ingestion into the entry store.

A request carries one reviewed delta. Planning applies it in memory and reports
the changes. Applying takes the home lock, re-validates the same delta against
the current store, installs the changed entry files and ``edges.jsonl`` through
a recoverable journal, and writes a readable receipt keyed by ``request_id``.
Source documents are only read, never edited.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import stat
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any

from . import home
from .contracts import canonical_json
from .entries import ID_RE, WINDOWS_RESERVED, render_entry
from .home import Base, KnowledgeError, LockConflict, atomic_write_text, knowledge_root
from .json_schema import validate_json_schema
from .knowledge_store import (
    EDGES_PATH,
    ENTRIES_DIR,
    KnowledgeState,
    StoreError,
    apply_delta,
    load_state,
    render_edges,
)

REQUEST_SCHEMA = "kgdistiller-ingest-request-v1"
PLAN_SCHEMA = "kgdistiller-ingest-plan-v1"
RECEIPT_SCHEMA = "kgdistiller-ingest-receipt-v1"
ERROR_SCHEMA = "kgdistiller-ingest-error-v1"
JOURNAL_SCHEMA = "kgdistiller-ingest-journal-v1"
CAPABILITY = "transactional-ingest-v1"
REQUEST_ID_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?")
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_TEXT_LENGTH = 16 * 1024
FailureInjector = Callable[[str], None]


class IngestError(KnowledgeError):
    """A stable, machine-readable transactional ingestion failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str = "validation",
        diagnostics: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.diagnostics = diagnostics or []

    def payload(self) -> dict[str, Any]:
        return {
            "schema": ERROR_SCHEMA,
            "error": {
                "code": self.code,
                "message": str(self),
                "stage": self.stage,
                "diagnostics": self.diagnostics,
            },
        }


@dataclass(frozen=True)
class IngestPaths:
    base: Base


def _validate_json_schema(payload: Any, filename: str, code: str) -> None:
    schema = json.loads(
        resources.files("kgdistiller").joinpath("schemas", filename).read_text(encoding="utf-8")
    )
    errors = validate_json_schema(payload, schema)
    if errors:
        raise IngestError(
            code,
            f"JSON Schema validation failed with {len(errors)} error(s)",
            diagnostics=[
                {"path": ".".join(str(item) for item in error.path), "message": error.message}
                for error in errors[:32]
            ],
        )


def _review_items(values: list[Any], field: str) -> None:
    for item in values:
        size = len((item if isinstance(item, str) else canonical_json(item)).encode("utf-8"))
        if size > MAX_TEXT_LENGTH:
            raise IngestError("invalid-request", f"review.{field} item exceeds {MAX_TEXT_LENGTH} bytes")


def validate_request(payload: Any, *, mode: str | None = None) -> dict[str, Any]:
    _validate_json_schema(payload, f"{REQUEST_SCHEMA}.schema.json", "invalid-request")
    request_id = payload["request_id"]
    if not REQUEST_ID_RE.fullmatch(request_id) or request_id.casefold() in WINDOWS_RESERVED:
        raise IngestError("invalid-request", f"invalid request_id: {request_id!r}")
    if mode is not None and payload["mode"] != mode:
        raise IngestError("invalid-request", f"request mode {payload['mode']!r} does not match {mode!r}")
    review = payload["review"]
    if not review["reviewer"].strip():
        raise IngestError("invalid-request", "review.reviewer must not be empty")
    _review_items(review["evidence"], "evidence")
    _review_items(review["provenance"], "provenance")
    return copy.deepcopy(payload)


def load_request(path: Path, *, mode: str | None = None) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_REQUEST_BYTES:
            raise IngestError("invalid-request", "ingest request exceeds 8 MiB")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise IngestError("invalid-request", f"cannot read JSON request: {path}") from error
    return validate_request(payload, mode=mode)


def _invoke(injector: FailureInjector | None, stage: str) -> None:
    if injector is not None:
        injector(stage)


def _state_dir(paths: IngestPaths) -> Path:
    return knowledge_root(paths.base.root) / "build/kgdistiller-ingest"


def receipt_path(paths: IngestPaths, request_id: str) -> Path:
    return _state_dir(paths) / "receipts" / f"{request_id}.json"


def journal_path(paths: IngestPaths) -> Path:
    return _state_dir(paths) / "journal.json"


def _counts(state: KnowledgeState) -> dict[str, int]:
    return {"entries": len(state.entries), "edges": len(state.edges)}


def _load_current(paths: IngestPaths) -> KnowledgeState:
    try:
        return load_state(paths.base.root)
    except StoreError as error:
        raise IngestError(error.code, str(error), diagnostics=error.diagnostics) from error
    except (KnowledgeError, OSError, ValueError) as error:
        raise IngestError("invalid-store", str(error)) from error


def _apply(paths: IngestPaths, state: KnowledgeState, request: dict[str, Any]):
    try:
        return apply_delta(state, request["delta"], paths.base)
    except StoreError as error:
        raise IngestError(error.code, str(error), diagnostics=error.diagnostics) from error
    except (KnowledgeError, OSError, ValueError) as error:
        raise IngestError("invalid-store", str(error)) from error


def plan_ingest(
    paths: IngestPaths,
    request: dict[str, Any],
    *,
    failure_injector: FailureInjector | None = None,
) -> dict[str, Any]:
    """Validate a reviewed delta against the current store without writing."""
    validated = validate_request(request, mode="plan")
    if journal_path(paths).exists():
        raise IngestError("lock-conflict", "an interrupted ingest is pending; rerun ingest apply",
                          stage="lock")
    before = _load_current(paths)
    after, changes = _apply(paths, before, validated)
    _invoke(failure_injector, "validated")
    return {
        "schema": PLAN_SCHEMA,
        "request_id": validated["request_id"],
        "status": "planned",
        "changes": changes,
        "counts": {"before": _counts(before), "after": _counts(after)},
    }


@contextmanager
def writer_lock(paths: IngestPaths) -> Iterator[None]:
    """Hold the home's single knowledge-writer lock."""
    with ExitStack() as stack:
        try:
            stack.enter_context(home.lock())
        except LockConflict as error:
            raise IngestError(
                "lock-conflict", "another kgdistiller writer holds the home lock", stage="lock"
            ) from error
        yield


def _journal_failure(message: str) -> IngestError:
    return IngestError("install-failed", message, stage="recovery")


def _lstat(path: Path) -> int | None:
    try:
        return path.lstat().st_mode
    except FileNotFoundError:
        return None


def _no_symlinks(base: Path, path: Path) -> None:
    cursor = base
    for part in path.relative_to(base).parts:
        cursor = cursor / part
        mode = _lstat(cursor)
        if mode is not None and stat.S_ISLNK(mode):
            raise _journal_failure(f"ingest journal path traverses a symlink: {cursor}")


def _managed_target(value: Any) -> str:
    """Accept only the entry files and the edge file as journal targets."""
    if not isinstance(value, str):
        raise _journal_failure("ingest journal target must be a path")
    if value == EDGES_PATH.as_posix():
        return value
    path = PurePosixPath(value)
    if (
        path.parent.as_posix() == ENTRIES_DIR.as_posix()
        and path.suffix == ".md"
        and ID_RE.fullmatch(path.stem)
        and path.stem not in WINDOWS_RESERVED
    ):
        return value
    raise _journal_failure(f"ingest journal target is not managed: {value!r}")


def _read_journal(paths: IngestPaths) -> dict[str, Any] | None:
    path = journal_path(paths)
    _no_symlinks(paths.base.root, path)
    mode = _lstat(path)
    if mode is None:
        return None
    if not stat.S_ISREG(mode):
        raise _journal_failure("ingest journal is not an ordinary file")
    try:
        journal = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _journal_failure("invalid ingest journal") from error
    if (
        not isinstance(journal, dict)
        or set(journal) != {"schema", "request_id", "status", "targets"}
        or journal["schema"] != JOURNAL_SCHEMA
        or not isinstance(journal["request_id"], str)
        or not REQUEST_ID_RE.fullmatch(journal["request_id"])
        or journal["status"] not in {"installing", "committed", "rolled-back"}
        or not isinstance(journal["targets"], list)
    ):
        raise _journal_failure("invalid ingest journal")
    seen = set()
    backups = _backup_root(paths, journal["request_id"])
    _no_symlinks(paths.base.root, backups)
    for record in journal["targets"]:
        if not isinstance(record, dict) or set(record) != {"path", "existed"} or type(record["existed"]) is not bool:
            raise _journal_failure("invalid ingest journal target")
        target = _managed_target(record["path"])
        if target in seen:
            raise _journal_failure(f"duplicate ingest journal target: {target}")
        seen.add(target)
        backup = backups / target
        _no_symlinks(paths.base.root, backup)
        mode = _lstat(backup)
        if record["existed"] and (mode is None or not stat.S_ISREG(mode)):
            raise _journal_failure(f"missing ingest journal backup: {target}")
        if not record["existed"] and mode is not None:
            raise _journal_failure(f"unexpected ingest journal backup: {target}")
    return journal


def _backup_root(paths: IngestPaths, request_id: str) -> Path:
    return _state_dir(paths) / "backups" / request_id


def _staging_root(paths: IngestPaths, request_id: str) -> Path:
    return _state_dir(paths) / "staging" / request_id


def _restore(paths: IngestPaths, journal: dict[str, Any]) -> None:
    backups = _backup_root(paths, journal["request_id"])
    errors = []
    for record in reversed(journal["targets"]):
        target = paths.base.root / record["path"]
        try:
            _no_symlinks(paths.base.root, target)
            if record["existed"]:
                _atomic_copy(backups / record["path"], target)
            else:
                target.unlink(missing_ok=True)
        except (OSError, IngestError) as error:
            errors.append({"path": record["path"], "message": str(error)})
    try:
        receipt_path(paths, journal["request_id"]).unlink(missing_ok=True)
    except OSError as error:
        errors.append({"path": "receipt", "message": str(error)})
    if errors:
        raise IngestError("install-failed", "rollback could not restore every target",
                          stage="rollback", diagnostics=errors)


def _atomic_copy(source: Path, target: Path) -> None:
    temporary = target.with_name(f".{target.name}.restore")
    shutil.copyfile(source, temporary)
    os.replace(temporary, target)


def _cleanup(paths: IngestPaths, request_id: str) -> None:
    shutil.rmtree(_backup_root(paths, request_id), ignore_errors=True)
    shutil.rmtree(_staging_root(paths, request_id), ignore_errors=True)
    journal_path(paths).unlink(missing_ok=True)


def recover_ingest(paths: IngestPaths) -> dict[str, Any] | None:
    """Finish or roll back an interrupted install; the caller holds the lock."""
    journal = _read_journal(paths)
    if journal is None:
        return None
    if journal["status"] != "committed":
        _restore(paths, journal)
    _cleanup(paths, journal["request_id"])
    return {"request_id": journal["request_id"],
            "status": "committed" if journal["status"] == "committed" else "rolled-back"}


def _write_journal(paths: IngestPaths, journal: dict[str, Any]) -> None:
    atomic_write_text(journal_path(paths), json.dumps(journal, ensure_ascii=False, indent=2) + "\n")


def _install(
    paths: IngestPaths,
    request_id: str,
    after: KnowledgeState,
    changes: dict[str, list[Any]],
    failure_injector: FailureInjector | None,
) -> dict[str, Any]:
    staged: dict[str, str | None] = {}
    for entry_id in (*changes["entries_created"], *changes["entries_updated"]):
        staged[(ENTRIES_DIR / f"{entry_id}.md").as_posix()] = render_entry(after.entries[entry_id])
    for entry_id in changes["entries_removed"]:
        staged[(ENTRIES_DIR / f"{entry_id}.md").as_posix()] = None
    if changes["edges_added"] or changes["edges_removed"]:
        staged[EDGES_PATH.as_posix()] = render_edges(after.edges.values())
    staging = _staging_root(paths, request_id)
    backups = _backup_root(paths, request_id)
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(backups, ignore_errors=True)
    for relative, content in staged.items():
        if content is not None:
            atomic_write_text(staging / relative, content)
    _invoke(failure_injector, "staged")
    targets = []
    for relative in staged:
        target = paths.base.root / relative
        _no_symlinks(paths.base.root, target)
        existed = target.is_file()
        if existed:
            (backups / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target, backups / relative)
        targets.append({"path": relative, "existed": existed})
    journal = {"schema": JOURNAL_SCHEMA, "request_id": request_id, "status": "installing",
               "targets": targets}
    _write_journal(paths, journal)
    _invoke(failure_injector, "prepared-install")
    try:
        for relative, content in staged.items():
            target = paths.base.root / relative
            if content is None:
                target.unlink(missing_ok=True)
            else:
                atomic_write_text(target, content)
            if relative != EDGES_PATH.as_posix():
                _invoke(failure_injector, "installed-entry")
        _invoke(failure_injector, "installed")
    except BaseException as error:
        _restore(paths, journal)
        _cleanup(paths, request_id)
        if isinstance(error, IngestError):
            raise
        raise IngestError("install-failed", str(error), stage="install") from error
    return journal


def load_receipt(paths: IngestPaths, request_id: str) -> dict[str, Any] | None:
    path = receipt_path(paths, request_id)
    if not path.is_file():
        return None
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise IngestError("invalid-store", f"unreadable ingest receipt: {path}") from error
    _validate_json_schema(receipt, f"{RECEIPT_SCHEMA}.schema.json", "invalid-store")
    return receipt


def apply_ingest(
    paths: IngestPaths,
    request: dict[str, Any],
    *,
    failure_injector: FailureInjector | None = None,
) -> dict[str, Any]:
    """Re-validate and install a reviewed delta; replay an identical request."""
    validated = validate_request(request, mode="apply")
    request_id = validated["request_id"]
    with writer_lock(paths):
        recover_ingest(paths)
        existing = load_receipt(paths, request_id)
        if existing is not None:
            if canonical_json(existing["request"]) == canonical_json(validated):
                return existing
            raise IngestError("request-conflict",
                              f"request_id {request_id!r} was already committed with a different request",
                              stage="idempotency")
        before = _load_current(paths)
        after, changes = _apply(paths, before, validated)
        _invoke(failure_injector, "validated")
        journal = _install(paths, request_id, after, changes, failure_injector)
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "request_id": request_id,
            "status": "committed",
            "request": validated,
            "changes": changes,
            "counts": _counts(after),
        }
        _validate_json_schema(receipt, f"{RECEIPT_SCHEMA}.schema.json", "install-failed")
        try:
            atomic_write_text(receipt_path(paths, request_id),
                              json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            _invoke(failure_injector, "receipt-written")
            journal["status"] = "committed"
            _write_journal(paths, journal)
        except BaseException as error:
            _restore(paths, journal)
            _cleanup(paths, request_id)
            if isinstance(error, IngestError):
                raise
            raise IngestError("install-failed", str(error), stage="receipt") from error
        _cleanup(paths, request_id)
        return receipt
