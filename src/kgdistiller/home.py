"""The kgdistiller home: the base registry, source globs, document types and the write lock.

``$KGDISTILLER_HOME`` (default ``~/.knowledge``) is the only environment variable
for kgdistiller's own home and data. Its ``config.json`` registers every base (a directory whose
knowledge lives under ``<root>/.knowledge/``) with the globs that map its source
files to document types, and ``types/<name>.md`` holds one document type each.
Reads never create anything; ``base add`` creates the home on first use.
"""

from __future__ import annotations

import glob
import json
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePath, PureWindowsPath
from typing import Any

import yaml

HOME_ENVIRONMENT = "KGDISTILLER_HOME"
KNOWLEDGE_DIRECTORY = ".knowledge"
HOME_GITIGNORE = "index.sqlite*\nlock\n"
CONFIG_FILENAME = "config.json"
TYPES_DIRECTORY = "types"
LOCK_FILENAME = "lock"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
ROLE_RE = re.compile(r"^[a-z][a-z0-9-]*$")
FIXED_KEYS = frozenset({
    "label", "kind", "source", "lines", "aliases", "understanding",
    "epistemic", "requires", "tags", "cssclasses",
})
CONFIG_KEYS = {"bases", "embedding"}
BASE_KEYS = {"path", "sources"}
TYPE_KEYS = {"node_kinds", "relation_kinds", "epistemic"}


class KnowledgeError(RuntimeError):
    """Raised when the knowledge contract cannot be satisfied."""


class LockConflict(KnowledgeError):
    """Raised when another kgdistiller writer holds the home lock."""


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def home_directory() -> Path:
    configured = os.environ.get(HOME_ENVIRONMENT)
    if configured is None:
        return Path.home() / KNOWLEDGE_DIRECTORY
    path = Path(configured).expanduser()
    if not path.is_absolute():
        raise KnowledgeError(f"{HOME_ENVIRONMENT} must be an absolute path: {configured!r}")
    return path


def _realpath(path: Path | str) -> Path:
    return Path(os.path.realpath(Path(path).expanduser()))


def _contains(root: Path, path: Path) -> bool:
    return path == root or root in path.parents


@dataclass(frozen=True)
class DocumentType:
    name: str
    node_kinds: tuple[str, ...]
    relation_kinds: dict[str, tuple[str, ...]]
    epistemic: tuple[str, ...]
    guidance: str


@dataclass(frozen=True)
class Base:
    name: str
    path: str
    root: Path
    sources: dict[str, str]
    types: dict[str, DocumentType]

    def source_types(self) -> tuple[dict[str, str], dict[str, list[str]]]:
        """Map every registered file (POSIX path relative to the root) to its type."""
        found: dict[str, set[str]] = {}
        for pattern, type_name in self.sources.items():
            for match in glob.glob(pattern, root_dir=str(self.root), recursive=True):
                if (self.root / match).is_file():
                    found.setdefault(match.replace(os.sep, "/"), set()).add(type_name)
        mapping = {path: next(iter(names)) for path, names in found.items() if len(names) == 1}
        conflicts = {path: sorted(names) for path, names in found.items() if len(names) > 1}
        return mapping, conflicts

    def holds(self, relative: str) -> bool:
        """Whether the base-relative path resolves, through any symlink, to a regular file inside the root."""
        real = _realpath(self.root / relative)
        return _contains(self.root, real) and real.is_file()

    def type_of(self, path: str | PurePath) -> DocumentType:
        relative = PurePath(path).as_posix()
        mapping, conflicts = self.source_types()
        if relative in conflicts:
            raise KnowledgeError(
                f"source {relative} of base {self.name} matches globs of several types: "
                + ", ".join(conflicts[relative])
            )
        if relative not in mapping:
            raise KnowledgeError(
                f"{relative} is not a registered source of base {self.name}; add a glob for it "
                f"under bases.{self.name}.sources in ${HOME_ENVIRONMENT}/{CONFIG_FILENAME}"
            )
        return self.types[mapping[relative]]


@dataclass(frozen=True)
class Home:
    directory: Path
    bases: dict[str, Base]
    embedding: str | None
    types: dict[str, DocumentType]


def _slug_list(value: Any, label: str, pattern: re.Pattern[str], *, required: bool) -> tuple[str, ...]:
    if not isinstance(value, list) or (required and not value):
        raise KnowledgeError(f"{label} must be a {'non-empty ' if required else ''}list")
    for item in value:
        if not isinstance(item, str) or not pattern.fullmatch(item):
            raise KnowledgeError(f"{label} has an invalid slug: {item!r}")
    if len(set(value)) != len(value):
        raise KnowledgeError(f"{label} must not repeat a value")
    return tuple(value)


def load_type(path: Path) -> DocumentType:
    name = path.stem
    where = f"document type {path}"
    if not SLUG_RE.fullmatch(name):
        raise KnowledgeError(f"{where}: the file name must be a slug matching {SLUG_RE.pattern}")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise KnowledgeError(f"{where}: expected YAML frontmatter between two '---' lines")
    end = lines.index("---", 1)
    try:
        data = yaml.load("\n".join(lines[1:end]), Loader=yaml.BaseLoader)
    except yaml.YAMLError as error:
        raise KnowledgeError(f"{where}: invalid frontmatter: {error}") from error
    if not isinstance(data, dict):
        raise KnowledgeError(f"{where}: the frontmatter must be a mapping")
    unknown = sorted(set(data) - TYPE_KEYS)
    if unknown:
        raise KnowledgeError(f"{where}: unknown frontmatter key {unknown[0]!r}")
    node_kinds = _slug_list(data.get("node_kinds"), f"{where}: node_kinds", SLUG_RE, required=True)
    raw_relations = data.get("relation_kinds", {})
    if not isinstance(raw_relations, dict):
        raise KnowledgeError(f"{where}: relation_kinds must map each kind to its roles")
    relation_kinds: dict[str, tuple[str, ...]] = {}
    for kind, roles in raw_relations.items():
        if not SLUG_RE.fullmatch(kind):
            raise KnowledgeError(f"{where}: relation_kinds has an invalid slug: {kind!r}")
        if kind in node_kinds:
            raise KnowledgeError(f"{where}: {kind!r} is both a node kind and a relation kind")
        relation_kinds[kind] = _slug_list(roles, f"{where}: roles of {kind}", ROLE_RE, required=True)
        fixed = sorted(FIXED_KEYS.intersection(relation_kinds[kind]))
        if fixed:
            raise KnowledgeError(f"{where}: role {fixed[0]!r} of {kind} is a fixed record key")
    epistemic = _slug_list(data.get("epistemic", []), f"{where}: epistemic", SLUG_RE, required=False)
    guidance = "\n".join(lines[end + 1 :]).strip()
    if not guidance:
        raise KnowledgeError(f"{where}: the body (the extraction guidance) must not be empty")
    return DocumentType(name, node_kinds, relation_kinds, epistemic, guidance)


def _missing_home(home: Path) -> KnowledgeError:
    return KnowledgeError(f"no kgdistiller home at {home}; run `kgd base add PATH` to create it")


def _check_glob(pattern: Any, where: str) -> None:
    if not isinstance(pattern, str) or not pattern:
        raise KnowledgeError(f"{where}: a source glob must be a non-empty string")
    if "\\" in pattern:
        raise KnowledgeError(f"{where}: source glob {pattern!r} must use '/' and no backslash")
    if pattern.startswith("/") or PureWindowsPath(pattern).drive:
        raise KnowledgeError(f"{where}: source glob {pattern!r} must be relative to the base root")
    for segment in pattern.split("/"):
        if segment == "..":
            raise KnowledgeError(f"{where}: source glob {pattern!r} must not contain '..'")
        if not segment or segment.startswith("."):
            raise KnowledgeError(f"{where}: source glob {pattern!r} has an empty or hidden segment")


def _parse_config(home: Path, payload: Any, types: dict[str, DocumentType] | None) -> Home:
    """Validate config.json; with ``types`` None the glob type names are not checked against types/."""
    where = str(home / CONFIG_FILENAME)
    if not isinstance(payload, dict) or set(payload) != CONFIG_KEYS:
        raise KnowledgeError(f"{where}: the top level must have exactly the keys bases and embedding")
    embedding = payload["embedding"]
    if embedding is not None and (not isinstance(embedding, str) or not embedding.strip()):
        raise KnowledgeError(f"{where}: embedding must be null or a model id")
    if not isinstance(payload["bases"], dict):
        raise KnowledgeError(f"{where}: bases must be an object keyed by base name")
    bases: dict[str, Base] = {}
    for name, entry in payload["bases"].items():
        if not SLUG_RE.fullmatch(name):
            raise KnowledgeError(f"{where}: base name {name!r} must match {SLUG_RE.pattern}")
        if not isinstance(entry, dict) or set(entry) != BASE_KEYS:
            raise KnowledgeError(f"{where}: base {name} must have exactly the keys path and sources")
        stored = entry["path"]
        if not isinstance(stored, str) or not (stored.startswith("~/") or Path(stored).is_absolute()):
            raise KnowledgeError(f"{where}: base {name} path must be '~/…' or absolute")
        if not isinstance(entry["sources"], dict):
            raise KnowledgeError(f"{where}: base {name} sources must map globs to type names")
        for pattern, type_name in entry["sources"].items():
            _check_glob(pattern, f"{where}: base {name}")
            if not isinstance(type_name, str) or (types is not None and type_name not in types):
                raise KnowledgeError(f"{where}: base {name} glob {pattern!r} names unknown type {type_name!r}")
        bases[name] = Base(name, stored, _realpath(stored), dict(entry["sources"]), types or {})
    home_real = _realpath(home)
    for base in bases.values():
        for other in bases.values():
            if other is not base and _contains(base.root, other.root):
                raise KnowledgeError(f"{where}: base {other.name} root lies inside base {base.name} root")
        if _contains(base.root, home_real) or _realpath(base.root / KNOWLEDGE_DIRECTORY) == home_real:
            raise KnowledgeError(f"{where}: the home {home} lies inside base {base.name} root {base.root}")
    return Home(home, bases, embedding, types or {})


def _read_config(home: Path) -> Any:
    try:
        return json.loads((home / CONFIG_FILENAME).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise KnowledgeError(f"{home / CONFIG_FILENAME}: invalid JSON: {error}") from error


def _load_types(home: Path) -> dict[str, DocumentType]:
    directory = home / TYPES_DIRECTORY
    if not directory.is_dir():
        raise KnowledgeError(f"missing document type directory: {directory}")
    return {path.stem: load_type(path) for path in sorted(directory.glob("*.md"))}


def load_home(home: Path, *, types: bool = True) -> Home:
    """The validated home; ``types=False`` reads config.json only, for the derived index and its reads."""
    if not (home / CONFIG_FILENAME).is_file():
        raise _missing_home(home)
    return _parse_config(home, _read_config(home), _load_types(home) if types else None)


def registered_bases() -> dict[str, Base]:
    return load_home(home_directory()).bases


def base_for_path(bases: dict[str, Base], path: Path | str) -> Base | None:
    """The registered base whose root contains the path's realpath; no upward walk."""
    real = _realpath(path)
    return next((base for base in bases.values() if _contains(base.root, real)), None)


def resolve_base(name: str | None, cwd: Path) -> Base:
    bases = registered_bases()
    registered = ", ".join(sorted(bases)) or "(none)"
    if name is not None:
        base = bases.get(name)
        if base is None:
            raise KnowledgeError(f"unknown base {name!r}; registered bases: {registered}")
    else:
        base = base_for_path(bases, cwd)
        if base is None:
            raise KnowledgeError(
                f"{cwd} is not inside any registered base root; registered bases: {registered}; "
                "pass --base NAME"
            )
    if not base.root.is_dir():
        raise KnowledgeError(f"base {base.name} is unavailable: {base.path} is not a directory")
    return base


def _render_config(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _create_missing_home_files(home: Path) -> None:
    """Write whichever of config.json, types/ and .gitignore is missing; the caller holds the lock."""
    if not (home / CONFIG_FILENAME).exists():
        atomic_write_text(home / CONFIG_FILENAME, _render_config({"bases": {}, "embedding": None}))
    (home / TYPES_DIRECTORY).mkdir(exist_ok=True)
    if not (home / ".gitignore").exists():
        atomic_write_text(home / ".gitignore", HOME_GITIGNORE)


@contextmanager
def lock() -> Iterator[Path]:
    """Hold ``$KGDISTILLER_HOME/lock`` exclusively; never wait and never truncate it."""
    home = home_directory()
    if not home.is_dir():
        raise _missing_home(home)
    path = home / LOCK_FILENAME
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise KnowledgeError(f"the home lock is not an ordinary file: {path}")
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")  # msvcrt locks bytes, so the file needs one
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise LockConflict(f"another kgdistiller writer holds the home lock: {path}") from error
        try:
            yield path
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _stored_path(root: Path) -> str:
    user_home = _realpath(Path.home())
    if root != user_home and user_home in root.parents:
        return "~/" + root.relative_to(user_home).as_posix()
    return root.as_posix()


def _count(directory: Path) -> int:
    return sum(1 for path in directory.glob("*.md") if path.is_file())


def _describe(base: Base) -> dict[str, Any]:
    knowledge = base.root / KNOWLEDGE_DIRECTORY
    available = base.root.is_dir()
    return {
        "name": base.name,
        "path": base.path,
        "root": str(base.root),
        "available": available,
        "records": _count(knowledge / "entries") if available else None,
        "drafts": _count(knowledge / "drafts") if available else None,
    }


def add_base(path: Path | str, name: str | None = None) -> dict[str, Any]:
    root = _realpath(path)
    if not root.is_dir():
        raise KnowledgeError(f"base root is not an existing directory: {path}")
    base_name = Path(os.path.abspath(os.path.expanduser(os.fspath(path)))).name if name is None else name
    if not SLUG_RE.fullmatch(base_name):
        hint = "; pass --name with a slug" if name is None else ""
        raise KnowledgeError(f"base name {base_name!r} must match {SLUG_RE.pattern}{hint}")
    knowledge = root / KNOWLEDGE_DIRECTORY
    if knowledge.is_symlink():
        raise KnowledgeError(f"{knowledge} must not be a symlink")
    home = home_directory()
    if _contains(root, _realpath(home)):
        raise KnowledgeError(f"the kgdistiller home {home} must not lie inside the base root {root}")
    home.mkdir(parents=True, exist_ok=True)
    with lock():
        _create_missing_home_files(home)
        payload = _read_config(home)
        current = _parse_config(home, payload, _load_types(home))
        if base_name in current.bases:
            raise KnowledgeError(f"base name {base_name!r} is already registered")
        for other in current.bases.values():
            if other.root == root:
                raise KnowledgeError(f"{root} is already registered as base {other.name}")
            if _contains(other.root, root):
                raise KnowledgeError(f"{root} lies inside the root of base {other.name}")
            if _contains(root, other.root):
                raise KnowledgeError(f"{root} contains the root of base {other.name}")
        payload["bases"][base_name] = {"path": _stored_path(root), "sources": {}}
        added = _parse_config(home, payload, current.types).bases[base_name]
        (knowledge / "entries").mkdir(parents=True, exist_ok=True)
        atomic_write_text(home / CONFIG_FILENAME, _render_config(payload))
    return {"home": str(home), "added": _describe(added)}


def remove_base(name: str) -> dict[str, Any]:
    home = home_directory()
    load_home(home)
    with lock():
        payload = _read_config(home)
        current = _parse_config(home, payload, _load_types(home))
        if name not in current.bases:
            registered = ", ".join(sorted(current.bases)) or "(none)"
            raise KnowledgeError(f"unknown base {name!r}; registered bases: {registered}")
        del payload["bases"][name]
        atomic_write_text(home / CONFIG_FILENAME, _render_config(payload))
    removed = current.bases[name]
    return {"home": str(home), "removed": {"name": name, "path": removed.path, "root": str(removed.root)}}


def list_bases() -> dict[str, Any]:
    home = load_home(home_directory(), types=False)
    return {
        "home": str(home.directory),
        "bases": [_describe(home.bases[name]) for name in sorted(home.bases)],
    }
