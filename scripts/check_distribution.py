#!/usr/bin/env python3
"""Verify that built distributions contain every shipped runtime resource."""

from __future__ import annotations

import argparse
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "src" / "kgdistiller"
DEFAULT_DIST_ROOT = REPO_ROOT / "dist"


def _is_python_cache(path: Path) -> bool:
    """Return whether a product path is generated Python bytecode state."""

    return any(part.casefold() == "__pycache__" for part in path.parts) or (
        path.suffix.casefold() in {".pyc", ".pyo"}
    )


def _single(dist_root: Path, pattern: str) -> Path:
    matches = sorted(dist_root.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one {pattern!r} artifact in {dist_root}, "
            f"found {[path.name for path in matches]}"
        )
    return matches[0]


def _expected_package_files() -> set[str]:
    expected: set[str] = set()
    for path in PACKAGE_ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(PACKAGE_ROOT)
        if _is_python_cache(relative):
            continue
        if path.suffix == ".py" or relative.parts[0] in {"schemas", "static"}:
            expected.add(PurePosixPath("kgdistiller", *relative.parts).as_posix())
    if not any(name.startswith("kgdistiller/schemas/") for name in expected):
        raise RuntimeError("source schema inventory is empty")
    if not any(name.startswith("kgdistiller/static/") for name in expected):
        raise RuntimeError("source static-asset inventory is empty")
    return expected


def _expected_product_files() -> tuple[set[str], set[str]]:
    wheel: set[str] = set()
    sdist: set[str] = set()
    roots = (
        REPO_ROOT / "skills",
        REPO_ROOT / "workflows",
        REPO_ROOT / ".codex" / "agents",
    )
    files = [
        path
        for root in roots
        for path in root.rglob("*")
        if path.is_file() and not _is_python_cache(path.relative_to(root))
    ]
    files.append(REPO_ROOT / "docs" / "product-workflows.md")
    files.extend(
        (
            REPO_ROOT / "scripts" / "link-codex-product.sh",
            REPO_ROOT / "scripts" / "link-codex-product.ps1",
        )
    )
    for path in files:
        relative = path.relative_to(REPO_ROOT)
        relative_posix = PurePosixPath(*relative.parts).as_posix()
        wheel.add(PurePosixPath("kgdistiller", "product", *relative.parts).as_posix())
        sdist.add(relative_posix)
    if not any(name.startswith("kgdistiller/product/skills/") for name in wheel):
        raise RuntimeError("source product Skill inventory is empty")
    return wheel, sdist


def _expected_obsidian_plugin_files() -> tuple[set[str], set[str]]:
    wheel: set[str] = set()
    sdist: set[str] = set()
    for name in ("main.js", "manifest.json", "styles.css"):
        source = REPO_ROOT / "integrations" / "obsidian" / name
        if not source.is_file() or source.stat().st_size == 0:
            raise RuntimeError(f"Obsidian plugin bundle file is missing or empty: {source}")
        wheel.add(PurePosixPath("kgdistiller", "obsidian_plugin", name).as_posix())
        sdist.add(PurePosixPath("integrations", "obsidian", name).as_posix())
    return wheel, sdist


def _missing(expected: set[str], observed: set[str]) -> list[str]:
    return sorted(expected - observed)


def check_wheel(path: Path, expected: set[str]) -> None:
    with zipfile.ZipFile(path) as archive:
        names = {PurePosixPath(name).as_posix() for name in archive.namelist()}
        missing = _missing(expected, names)
        if missing:
            raise RuntimeError(f"wheel is missing runtime files: {missing}")
        entry_points = [
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        ]
        if len(entry_points) != 1:
            raise RuntimeError("wheel has no unique entry_points.txt")
        entry_text = archive.read(entry_points[0]).decode("utf-8")
        expected_entries = {
            "kgdistiller = kgdistiller.cli:main",
            "kgd = kgdistiller.cli:main",
        }
        missing_entries = sorted(
            entry for entry in expected_entries if entry not in entry_text
        )
        if missing_entries:
            raise RuntimeError(
                f"wheel is missing console entry points: {missing_entries}"
            )


def check_sdist(path: Path, expected: set[str], product_sources: set[str]) -> None:
    with tarfile.open(path, "r:gz") as archive:
        names = {PurePosixPath(name).as_posix() for name in archive.getnames()}
    suffix = "/src/kgdistiller/__init__.py"
    roots = {name[: -len(suffix)] for name in names if name.endswith(suffix)}
    if len(roots) != 1:
        raise RuntimeError("sdist has no unique package root")
    root = next(iter(roots))
    expected_sdist = {f"{root}/src/{name}" for name in expected}
    expected_sdist.update(f"{root}/{name}" for name in product_sources)
    missing = _missing(expected_sdist, names)
    if missing:
        raise RuntimeError(f"sdist is missing runtime files: {missing}")
    for required in ("LICENSE", "README.md", "pyproject.toml"):
        if f"{root}/{required}" not in names:
            raise RuntimeError(f"sdist is missing {required}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dist-root",
        type=Path,
        default=DEFAULT_DIST_ROOT,
        help="directory containing exactly one current wheel and source archive",
    )
    args = parser.parse_args(argv)
    dist_root = args.dist_root.resolve()
    try:
        package_files = _expected_package_files()
        product_files, product_sources = _expected_product_files()
        obsidian_files, obsidian_sources = _expected_obsidian_plugin_files()
        expected = package_files | product_files | obsidian_files
        wheel = _single(dist_root, "kgdistiller-*.whl")
        sdist = _single(dist_root, "kgdistiller-*.tar.gz")
        check_wheel(wheel, expected)
        check_sdist(
            sdist,
            package_files,
            product_sources | obsidian_sources,
        )
    except (OSError, RuntimeError, tarfile.TarError, zipfile.BadZipFile) as error:
        print(f"distribution check failed: {error}", file=sys.stderr)
        return 1
    schemas = sum(name.startswith("kgdistiller/schemas/") for name in expected)
    static = sum(name.startswith("kgdistiller/static/") for name in expected)
    modules = sum(name.endswith(".py") for name in expected)
    product = sum(name.startswith("kgdistiller/product/") for name in expected)
    obsidian = sum(
        name.startswith("kgdistiller/obsidian_plugin/") for name in expected
    )
    print(
        f"distribution check passed: modules={modules} schemas={schemas} "
        f"static={static} product={product} obsidian={obsidian} "
        f"wheel={wheel.name} sdist={sdist.name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
