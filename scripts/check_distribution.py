#!/usr/bin/env python3
"""Verify that built distributions contain every shipped runtime resource."""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "src" / "kgdistiller"
DIST_ROOT = REPO_ROOT / "dist"


def _single(pattern: str) -> Path:
    matches = sorted(DIST_ROOT.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one {pattern!r} artifact in {DIST_ROOT}, "
            f"found {[path.name for path in matches]}"
        )
    return matches[0]


def _expected_package_files() -> set[str]:
    expected: set[str] = set()
    for path in PACKAGE_ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(PACKAGE_ROOT)
        if path.suffix == ".py" or relative.parts[0] in {"schemas", "static"}:
            expected.add(PurePosixPath("kgdistiller", *relative.parts).as_posix())
    if not any(name.startswith("kgdistiller/schemas/") for name in expected):
        raise RuntimeError("source schema inventory is empty")
    if not any(name.startswith("kgdistiller/static/") for name in expected):
        raise RuntimeError("source static-asset inventory is empty")
    return expected


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
        if "kgdistiller = kgdistiller.cli:main" not in entry_text:
            raise RuntimeError("wheel is missing the kgdistiller console entry point")


def check_sdist(path: Path, expected: set[str]) -> None:
    with tarfile.open(path, "r:gz") as archive:
        names = {PurePosixPath(name).as_posix() for name in archive.getnames()}
    suffix = "/src/kgdistiller/__init__.py"
    roots = {name[: -len(suffix)] for name in names if name.endswith(suffix)}
    if len(roots) != 1:
        raise RuntimeError("sdist has no unique package root")
    root = next(iter(roots))
    expected_sdist = {f"{root}/src/{name}" for name in expected}
    missing = _missing(expected_sdist, names)
    if missing:
        raise RuntimeError(f"sdist is missing runtime files: {missing}")
    for required in ("LICENSE", "README.md", "pyproject.toml"):
        if f"{root}/{required}" not in names:
            raise RuntimeError(f"sdist is missing {required}")


def main() -> int:
    try:
        expected = _expected_package_files()
        wheel = _single("kgdistiller-*.whl")
        sdist = _single("kgdistiller-*.tar.gz")
        check_wheel(wheel, expected)
        check_sdist(sdist, expected)
    except (OSError, RuntimeError, tarfile.TarError, zipfile.BadZipFile) as error:
        print(f"distribution check failed: {error}", file=sys.stderr)
        return 1
    schemas = sum(name.startswith("kgdistiller/schemas/") for name in expected)
    static = sum(name.startswith("kgdistiller/static/") for name in expected)
    modules = sum(name.endswith(".py") for name in expected)
    print(
        f"distribution check passed: modules={modules} schemas={schemas} "
        f"static={static} wheel={wheel.name} sdist={sdist.name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
