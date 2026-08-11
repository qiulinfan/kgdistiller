#!/usr/bin/env python3
"""Benchmark bounded query-only semantic scans over disposable Agent indexes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from kgdistiller.agent import (
    embedding_inventory,
    index_generation_token,
    install_embedding_records,
    resolve_agent_index_path,
    semantic_search_batch,
    write_agent_index,
)
from kgdistiller.contracts import sha256_json
from kgdistiller.providers import provider_config_sha256


REPORT_SCHEMA = "qlkg-semantic-benchmark-v1"
DEFAULT_SIZES = [1_000, 10_000, 100_000]
DEFAULT_DIMENSIONS = 128
DEFAULT_SAMPLES = 5
MAX_READY_RECORDS = 100_000
MAX_VECTOR_BYTES = 128 * 1024 * 1024
MAX_SCALAR_OPERATIONS = 64_000_000
RESULT_LIMIT = 10


class SyntheticQueryProvider:
    """One deterministic query-only provider used by the disposable benchmark."""

    name = "deterministic-fixture"
    model = "semantic-benchmark-v1"

    def __init__(self, config: dict[str, Any], vector: list[float]) -> None:
        self.dimensions = int(config["dimensions"])
        self.provider_config_sha256 = provider_config_sha256(config)
        self._vector = list(vector)
        self.query_batches = 0
        self.document_embedding_calls = 0

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        self.query_batches += 1
        return [list(self._vector) for _ in texts]


def _provider_config(dimensions: int) -> dict[str, Any]:
    return {
        "adapter": "deterministic-fixture",
        "model": SyntheticQueryProvider.model,
        "dimensions": dimensions,
        "base_url": "http://127.0.0.1",
        "credential_env": "KGD_SEMANTIC_BENCHMARK_UNUSED",
    }


def _snapshot(size: int) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "qlkg-agent-snapshot-v1",
        "namespace": "personal",
        "graph": {
            "schema": "qlkg-v2",
            "sha256": hashlib.sha256(
                f"kgdistiller-semantic-benchmark:{size}".encode("ascii")
            ).hexdigest(),
            "counts": {"nodes": size, "edges": 0, "references": 0},
        },
        "nodes": [
            {
                "id": f"synthetic-{index:06d}",
                "type": "knowledge",
                "label": f"Synthetic semantic node {index:06d}",
                "text": f"Deterministic semantic benchmark record {index:06d}.",
                "properties": {
                    "aliases": [],
                    "curation_status": "current",
                    "source_status": "active",
                },
                "provenance": {
                    "authority": "synthetic/semantic-benchmark.md",
                    "line": index + 1,
                },
            }
            for index in range(size)
        ],
        "edges": [],
        "references": [],
        "diagnostics": {"errors": [], "warnings": []},
    }
    payload["snapshot_sha256"] = sha256_json(payload)
    return payload


def _install_vectors(
    database: Path, *, size: int, dimensions: int
) -> tuple[dict[str, Any], list[float]]:
    config = _provider_config(dimensions)
    config_digest = provider_config_sha256(config)
    vector = [1.0] * dimensions
    inventory = embedding_inventory(database)
    records = [
        {
            "namespace": "personal",
            "node_id": node["node_id"],
            "provider": config["adapter"],
            "model": config["model"],
            "dimensions": dimensions,
            "embedding_input_schema": "qlkg-node-embedding-text-v1",
            "provider_config_sha256": config_digest,
            "content_sha256": node["content_sha256"],
            "vector": vector,
        }
        for node in inventory["nodes"]
    ]
    outcome = install_embedding_records(
        database,
        records,
        expected_snapshot_sha256=str(inventory["snapshot_sha256"]),
        expected_graph_sha256=str(inventory["graph_sha256"]),
    )
    if outcome.get("status") != "installed" or int(outcome.get("installed", -1)) != size:
        raise RuntimeError("synthetic vector installation did not complete")
    return config, vector


def _ready_space(
    database: Path, *, config: dict[str, Any], dimensions: int
) -> tuple[int, int]:
    inventory = embedding_inventory(database)
    nodes = {
        str(node["node_id"]): node
        for node in inventory["nodes"]
        if node.get("active") is True
    }
    digest = provider_config_sha256(config)
    ready = [
        record
        for record in inventory["records"]
        if record.get("node_id") in nodes
        and record.get("provider") == config["adapter"]
        and record.get("model") == config["model"]
        and record.get("dimensions") == dimensions
        and record.get("provider_config_sha256") == digest
        and record.get("content_sha256")
        == nodes[str(record["node_id"])].get("content_sha256")
        and record.get("vector_valid") is True
    ]
    return len(ready), sum(int(record["dimensions"]) * 4 for record in ready)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _file_manifest(root: Path) -> dict[str, dict[str, Any]]:
    return {
        path.relative_to(root).as_posix(): {
            "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _latency_summary(samples: list[float]) -> dict[str, float | int]:
    ordered = sorted(samples)

    def percentile(fraction: float) -> float:
        index = max(
            0,
            min(len(ordered) - 1, int(len(ordered) * fraction + 0.999999) - 1),
        )
        return round(ordered[index], 6)

    return {
        "samples": len(ordered),
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "max": round(ordered[-1], 6),
    }


def _validate_configuration(sizes: list[int], dimensions: int, samples: int) -> None:
    if (
        not sizes
        or len(sizes) != len(set(sizes))
        or any(size < 1 or size > MAX_READY_RECORDS for size in sizes)
    ):
        raise ValueError(f"sizes must be unique integers between 1 and {MAX_READY_RECORDS}")
    if dimensions < 1:
        raise ValueError("dimensions must be positive")
    if samples < 1 or samples > 100:
        raise ValueError("samples must be between 1 and 100")
    for size in sizes:
        if size * dimensions * 4 > MAX_VECTOR_BYTES:
            raise ValueError("requested vector space exceeds the 128 MiB byte budget")
        if size * dimensions > MAX_SCALAR_OPERATIONS:
            raise ValueError("one benchmark query exceeds the 64M scalar-operation budget")


def _benchmark_case(
    root: Path, *, size: int, dimensions: int, samples: int
) -> dict[str, Any]:
    case_root = root / f"n{size}-d{dimensions}"
    case_root.mkdir(parents=True, exist_ok=False)
    database = case_root / "knowledge.sqlite"

    # Setup is deliberately outside the measured region.
    write_agent_index(database, _snapshot(size))
    config, vector = _install_vectors(database, size=size, dimensions=dimensions)
    ready_records, ready_vector_bytes = _ready_space(
        database, config=config, dimensions=dimensions
    )
    if ready_records != size or ready_vector_bytes != size * dimensions * 4:
        raise RuntimeError("synthetic ready-vector inventory is incomplete")

    provider = SyntheticQueryProvider(config, vector)
    physical = resolve_agent_index_path(database)
    database_bytes = physical.stat().st_size
    before_token = index_generation_token(database)
    before_manifest = _file_manifest(case_root)
    latencies: list[float] = []
    expected_ids: list[str] | None = None
    for _ in range(samples):
        started = time.perf_counter()
        batches = semantic_search_batch(
            database,
            ["deterministic semantic benchmark query"],
            provider,
            limit=RESULT_LIMIT,
        )
        latencies.append(time.perf_counter() - started)
        result_ids = [str(item["node"]["id"]) for item in batches[0]]
        if expected_ids is None:
            expected_ids = result_ids
        elif result_ids != expected_ids:
            raise RuntimeError("semantic benchmark results are not deterministic")

    after_token = index_generation_token(database)
    after_manifest = _file_manifest(case_root)
    generation_unchanged = after_token == before_token
    bytes_unchanged = after_manifest == before_manifest
    if not generation_unchanged or not bytes_unchanged:
        raise RuntimeError("semantic search mutated the disposable Agent index")
    if provider.document_embedding_calls != 0:
        raise RuntimeError("semantic search called a document embedding method")

    return {
        "size": size,
        "dimensions": dimensions,
        "ready_records": ready_records,
        "ready_vector_bytes": ready_vector_bytes,
        "database_bytes": database_bytes,
        "scalar_operations_per_query": size * dimensions,
        "latency_seconds": _latency_summary(latencies),
        "query_batches": provider.query_batches,
        "document_embedding_calls": provider.document_embedding_calls,
        "result_limit": RESULT_LIMIT,
        "result_count": len(expected_ids or []),
        "result_ids_sha256": sha256_json(expected_ids or []),
        "generation_unchanged": generation_unchanged,
        "database_bytes_unchanged": bytes_unchanged,
    }


def run_benchmark(
    *, sizes: list[int], dimensions: int, samples: int
) -> dict[str, Any]:
    _validate_configuration(sizes, dimensions, samples)
    with tempfile.TemporaryDirectory(prefix="kgdistiller-semantic-benchmark-") as temporary:
        root = Path(temporary)
        cases = [
            _benchmark_case(
                root,
                size=size,
                dimensions=dimensions,
                samples=samples,
            )
            for size in sizes
        ]
    return {
        "schema": REPORT_SCHEMA,
        "status": "passed",
        "configuration": {
            "sizes": sizes,
            "dimensions": dimensions,
            "samples": samples,
            "query_batch_size": 1,
            "result_limit": RESULT_LIMIT,
        },
        "environment": {
            "python": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "os_name": os.name,
            "sqlite": sqlite3.sqlite_version,
        },
        "limits": {
            "ready_records": MAX_READY_RECORDS,
            "vector_bytes": MAX_VECTOR_BYTES,
            "scalar_operations_per_call": MAX_SCALAR_OPERATIONS,
        },
        "cases": cases,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes",
        nargs="+",
        type=int,
        default=list(DEFAULT_SIZES),
        help="ready-vector counts to benchmark (default: 1000 10000 100000)",
    )
    parser.add_argument("--dimensions", type=int, default=DEFAULT_DIMENSIONS)
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = run_benchmark(
            sizes=list(args.sizes),
            dimensions=int(args.dimensions),
            samples=int(args.samples),
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"semantic benchmark failed: {error}", file=sys.stderr)
        return 1
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n"
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
