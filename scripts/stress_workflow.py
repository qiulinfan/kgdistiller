#!/usr/bin/env python3
"""Run a disposable large-graph, query, and transactional-ingest stress test."""

from __future__ import annotations

import argparse
import json
import platform
import resource
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from kgdistiller.agent import (
    build_context_bundle,
    compare_graph,
    index_status,
    resolve_concepts,
    search_index,
    sha256_json,
)
from kgdistiller.alignment import empty_alignment_set
from kgdistiller.candidate import build_candidate_snapshot
from kgdistiller.cli import (
    apply_delta,
    ensure_database,
    load_state,
    make_agent_snapshot,
    sha256_file,
    sha256_text,
    synchronize,
)
from kgdistiller.ingest import (
    CAPABILITY,
    IngestPaths,
    apply_ingest,
    finalize_request,
    plan_ingest,
)


def _timed(callable_: Any, *args: Any, **kwargs: Any) -> tuple[Any, float]:
    started = time.perf_counter()
    value = callable_(*args, **kwargs)
    return value, round(time.perf_counter() - started, 6)


def _latency_summary(samples: list[float]) -> dict[str, float | int]:
    ordered = sorted(samples)
    if not ordered:
        return {"samples": 0, "p50": 0.0, "p95": 0.0, "max": 0.0}

    def percentile(fraction: float) -> float:
        index = max(0, min(len(ordered) - 1, int(len(ordered) * fraction + 0.999999) - 1))
        return round(ordered[index], 6)

    return {
        "samples": len(ordered),
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "max": round(ordered[-1], 6),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _material_hashes(paths: IngestPaths) -> dict[str, str]:
    selected = [paths.alignments, paths.identities, paths.registry]
    selected.extend(path for path in sorted(paths.graph_dir.rglob("*")) if path.is_file())
    return {
        path.relative_to(paths.repo_root).as_posix(): sha256_file(path)
        for path in selected
        if path.is_file()
    }


def _registry() -> dict[str, Any]:
    return {
        "schema": "qlkg-sources-v2",
        "fields": [
            {
                "id": "stress-testing",
                "label": "Stress Testing",
                "text": "Synthetic concepts used only by a disposable stress fixture.",
            }
        ],
        "sources": [
            {
                "id": "stress:markdown",
                "subject": "stress",
                "course": "markdown",
                "knowledge_origin": "personal-note",
                "fields": ["stress-testing"],
                "root": "notes/markdown",
                "files": ["*.md"],
                "web": "https://example.invalid/stress/markdown",
                "topics": [],
            },
            {
                "id": "stress:typst",
                "subject": "stress",
                "course": "typst",
                "knowledge_origin": "personal-note",
                "fields": ["stress-testing"],
                "root": "notes/typst",
                "files": ["*.typ"],
                "web": "https://example.invalid/stress/typst",
                "topics": [],
            },
            {
                "id": "stress:transaction",
                "subject": "stress",
                "course": "transaction",
                "knowledge_origin": "personal-note",
                "fields": ["stress-testing"],
                "root": "notes/transaction",
                "files": ["*.md"],
                "web": "https://example.invalid/stress/transaction",
                "topics": [],
            },
        ],
    }


def _prepare_fixture(
    root: Path,
    knowledge_nodes: int,
    *,
    reserve_transaction_node: bool,
) -> tuple[IngestPaths, Path]:
    if knowledge_nodes < 20:
        raise ValueError("knowledge_nodes must be at least 20")
    bulk = knowledge_nodes - (2 if reserve_transaction_node else 1)
    markdown_count = bulk // 2
    typst_count = bulk - markdown_count

    markdown = root / "notes/markdown/bulk.md"
    markdown.parent.mkdir(parents=True, exist_ok=True)
    markdown.write_text(
        "# Synthetic Markdown authorities\n\n"
        + "".join(
            f"--[[Stress MD {index:06d}]]--\n\n"
            for index in range(markdown_count)
        ),
        encoding="utf-8",
    )

    typst = root / "notes/typst/bulk.typ"
    typst.parent.mkdir(parents=True, exist_ok=True)
    typst.write_text(
        "= Synthetic Typst authorities\n\n"
        + "".join(
            f"#kn[Stress Typst {index:06d}]\n\n"
            for index in range(typst_count)
        ),
        encoding="utf-8",
    )

    transaction = root / "notes/transaction/chapter.md"
    transaction.parent.mkdir(parents=True, exist_ok=True)
    transaction.write_text(
        "# Transaction authority\n\n"
        "--[[Alpha]]--\n\n"
        "Alpha is the baseline transaction concept.\n",
        encoding="utf-8",
    )

    knowledge = root / "knowledge"
    knowledge.mkdir(parents=True, exist_ok=True)
    registry = knowledge / "sources.json"
    _write_json(registry, _registry())
    alignments = knowledge / "alignments.json"
    _write_json(alignments, empty_alignment_set())

    paths = IngestPaths(
        repo_root=root,
        registry=registry,
        graph_dir=knowledge / "graph",
        identities=knowledge / "identities.json",
        alignments=alignments,
        database=knowledge / "build/knowledge.sqlite",
        typst_registry=knowledge / "build/knowledge-registry.typ",
    )
    return paths, transaction


def _sync(paths: IngestPaths, files: list[Path] | None = None) -> tuple[Any, Any, Any]:
    return synchronize(
        paths.repo_root,
        paths.registry,
        paths.graph_dir,
        paths.database,
        paths.typst_registry,
        identities=paths.identities,
        alignments=paths.alignments,
        files=files or [],
        course=None,
        subject=None,
        write=True,
    )


def _install_alpha_entry(paths: IngestPaths, transaction: Path) -> None:
    delta = paths.repo_root / "knowledge/build/alpha.delta.json"
    _write_json(
        delta,
        {
            "schema": "qlkg-agent-delta-v2",
            "remove_nodes": [],
            "nodes": [
                {
                    "id": "alpha",
                    "text": "Alpha is the baseline transaction concept.",
                    "properties": {"entry_origin": "stress-fixture"},
                }
            ],
            "edges": [],
            "remove_edges": [],
        },
    )
    apply_delta(
        paths.graph_dir,
        paths.database,
        paths.typst_registry,
        delta,
        paths.alignments,
    )
    _sync(paths, [transaction.relative_to(paths.repo_root)])


def _transaction_request(
    paths: IngestPaths,
    transaction: Path,
    *,
    mode: str,
) -> dict[str, Any]:
    ensure_database(paths.database, load_state(paths.graph_dir), paths.alignments)
    content = transaction.read_text(encoding="utf-8") + (
        "\n--[[Beta]]--\n\nBeta is the transactional stress concept.\n"
    )
    candidate = build_candidate_snapshot(
        {
            "schema": "qlkg-candidate-graph-v1",
            "namespace": "stress:transaction",
            "nodes": [
                {
                    "id": "beta",
                    "type": "knowledge",
                    "label": "Beta",
                    "text": "Beta is the transactional stress concept.",
                    "properties": {"aliases": []},
                    "provenance": {
                        "authority": "notes/transaction/chapter.md",
                        "line": 7,
                        "source_format": "markdown",
                    },
                }
            ],
            "edges": [],
            "references": [],
            "diagnostics": {"errors": [], "warnings": []},
        }
    )
    candidate_path = paths.repo_root / "knowledge/build/beta.snapshot.json"
    _write_json(candidate_path, candidate)
    comparison = compare_graph(paths.database, candidate)
    if comparison["summary"] != {
        "known": 0,
        "partial": 0,
        "new": 1,
        "conflict": 0,
        "uncertain": 0,
        "total": 1,
    }:
        raise AssertionError(f"unexpected transaction comparison: {comparison['summary']}")
    comparison_path = paths.repo_root / "knowledge/build/beta.comparison.json"
    _write_json(comparison_path, comparison)
    state = load_state(paths.graph_dir)
    return finalize_request(
        {
            "schema": "qlkg-ingest-request-v1",
            "request_id": "stress-add-beta",
            "mode": mode,
            "capabilities": [CAPABILITY],
            "base_graph_sha256": state.manifest["graph_sha256"],
            "base_alignment_sha256": sha256_json(empty_alignment_set()),
            "candidate_snapshot": {
                "path": candidate_path.relative_to(paths.repo_root).as_posix(),
                "sha256": candidate["snapshot_sha256"],
            },
            "query_report": {
                "path": comparison_path.relative_to(paths.repo_root).as_posix(),
                "sha256": sha256_json(comparison),
            },
            "authority_patches": [
                {
                    "path": transaction.relative_to(paths.repo_root).as_posix(),
                    "operation": "write",
                    "expected_sha256": sha256_file(transaction),
                    "content": content,
                    "content_sha256": sha256_text(content),
                    "expected_markers": {
                        "definitions": ["alpha", "beta"],
                        "references": [],
                    },
                }
            ],
            "decisions": [
                {
                    "candidate_id": "beta",
                    "action": "add",
                    "target_id": "beta",
                    "evidence": "The disposable authority explicitly defines Beta.",
                }
            ],
            "delta": {
                "schema": "qlkg-agent-delta-v2",
                "remove_nodes": [],
                "nodes": [
                    {
                        "id": "beta",
                        "text": "Beta is the transactional stress concept.",
                        "properties": {"entry_origin": "stress-fixture"},
                    }
                ],
                "edges": [],
                "remove_edges": [],
            },
            "alignment_decisions": [],
            "review": {
                "status": "reviewed",
                "reviewer": "stress-harness",
                "evidence": ["Beta has an explicit disposable authority marker."],
                "provenance": [
                    {
                        "path": transaction.relative_to(paths.repo_root).as_posix(),
                        "line": 7,
                        "kind": "authority",
                    }
                ],
            },
        }
    )


def _reader_loop(
    database: Path,
    stop: threading.Event,
    observations: list[str],
    errors: list[str],
) -> None:
    while not stop.is_set():
        try:
            result = resolve_concepts(database, ["Beta"])[0]
            observations.append(str(result["status"]))
        except Exception as error:  # pragma: no cover - asserted by caller
            errors.append(f"{type(error).__name__}: {error}")
        stop.wait(0.005)


def run_stress(
    root: Path,
    *,
    knowledge_nodes: int,
    fault_injection: bool,
    transactions: bool = True,
    query_samples: int = 20,
) -> dict[str, Any]:
    if query_samples < 1 or query_samples > 1000:
        raise ValueError("query_samples must be between 1 and 1000")
    paths, transaction = _prepare_fixture(
        root,
        knowledge_nodes,
        reserve_transaction_node=transactions,
    )
    (_, _, sync_report), sync_seconds = _timed(_sync, paths)
    _install_alpha_entry(paths, transaction)
    baseline_state = load_state(paths.graph_dir)
    baseline_knowledge = sum(
        1 for node in baseline_state.nodes.values() if node["type"] == "knowledge"
    )
    expected_baseline = knowledge_nodes - 1 if transactions else knowledge_nodes
    if baseline_knowledge != expected_baseline:
        raise AssertionError(
            f"expected {expected_baseline} baseline knowledge nodes, got {baseline_knowledge}"
        )

    database_before_queries = sha256_file(paths.database)
    status, status_seconds = _timed(index_status, paths.database)
    resolved, resolve_seconds = _timed(
        resolve_concepts,
        paths.database,
        ["Stress MD 000001", "Stress Typst 000001", "Alpha"],
    )
    searched, search_seconds = _timed(
        search_index,
        paths.database,
        "Stress MD 000001",
        limit=5,
    )
    context, context_seconds = _timed(
        build_context_bundle,
        paths.database,
        "Stress Typst 000001",
        token_budget=6000,
        result_limit=8,
        max_depth=1,
        include_taxonomy=True,
    )
    resolve_latencies: list[float] = []
    search_latencies: list[float] = []
    context_latencies: list[float] = []
    for _ in range(query_samples):
        _, duration = _timed(
            resolve_concepts,
            paths.database,
            ["Stress MD 000001", "Stress Typst 000001", "Alpha"],
        )
        resolve_latencies.append(duration)
        _, duration = _timed(
            search_index,
            paths.database,
            "Stress MD 000001",
            limit=5,
        )
        search_latencies.append(duration)
        _, duration = _timed(
            build_context_bundle,
            paths.database,
            "Stress Typst 000001",
            token_budget=6000,
            result_limit=8,
            max_depth=1,
            include_taxonomy=True,
        )
        context_latencies.append(duration)
    database_after_queries = sha256_file(paths.database)
    if database_before_queries != database_after_queries:
        raise AssertionError("read-only query operations changed the disposable index bytes")
    if any(item["status"] != "exact" for item in resolved):
        raise AssertionError(f"exact batch resolution failed: {resolved}")
    if not searched or not context["nodes"]:
        raise AssertionError("search or GraphRAG context returned no nodes")

    graph_before_incremental = load_state(paths.graph_dir).manifest["graph_sha256"]
    (_, _, incremental_report), incremental_seconds = _timed(
        _sync,
        paths,
        [Path("notes/markdown/bulk.md")],
    )
    graph_after_incremental = load_state(paths.graph_dir).manifest["graph_sha256"]
    if graph_before_incremental != graph_after_incremental:
        raise AssertionError("unchanged file-scoped sync changed the graph digest")
    indexed_after_incremental = index_status(paths.database)["snapshot_sha256"]
    persisted_after_incremental = make_agent_snapshot(
        load_state(paths.graph_dir)
    )["snapshot_sha256"]
    if indexed_after_incremental != persisted_after_incremental:
        raise AssertionError(
            "file-scoped sync left the disposable index on a different snapshot: "
            f"index={indexed_after_incremental} graph={persisted_after_incremental}"
        )

    plan: dict[str, Any] = {"status": "skipped"}
    receipt: dict[str, Any] = {"status": "skipped", "request_sha256": None}
    plan_seconds = 0.0
    failure_seconds = 0.0
    apply_seconds = 0.0
    observations: list[str] = []
    reader_errors: list[str] = []
    if transactions:
        plan_request = _transaction_request(paths, transaction, mode="plan")
        plan, plan_seconds = _timed(plan_ingest, paths, plan_request)
        if plan["status"] != "planned":
            raise AssertionError(f"transaction plan did not complete: {plan['status']}")

        if fault_injection:
            before_failure = _material_hashes(paths)

            def inject(stage: str) -> None:
                if stage == "staged-scan":
                    raise RuntimeError("stress fault injection at staged-scan")

            apply_request = _transaction_request(paths, transaction, mode="apply")
            started = time.perf_counter()
            try:
                apply_ingest(paths, apply_request, failure_injector=inject)
            except RuntimeError as error:
                if "stress fault injection" not in str(error):
                    raise
            else:  # pragma: no cover - a regression must fail loudly
                raise AssertionError("fault injection did not interrupt the transaction")
            failure_seconds = round(time.perf_counter() - started, 6)
            if before_failure != _material_hashes(paths):
                raise AssertionError("pre-install fault injection changed material graph state")

        apply_request = _transaction_request(paths, transaction, mode="apply")
        stop = threading.Event()
        reader = threading.Thread(
            target=_reader_loop,
            args=(paths.database, stop, observations, reader_errors),
            daemon=True,
        )
        reader.start()
        try:
            receipt, apply_seconds = _timed(apply_ingest, paths, apply_request)
        finally:
            stop.set()
            reader.join(timeout=5)
        observations.append(resolve_concepts(paths.database, ["Beta"])[0]["status"])
        if reader_errors:
            raise AssertionError(f"concurrent readers failed: {reader_errors[:3]}")
        if not observations or set(observations) - {"missing", "exact"}:
            raise AssertionError(f"reader observed a mixed generation: {set(observations)}")
        if observations[-1] != "exact":
            raise AssertionError("reader did not observe the committed generation")
        if receipt["status"] != "committed":
            raise AssertionError(f"transaction did not commit: {receipt['status']}")

    final_state = load_state(paths.graph_dir)
    final_knowledge = sum(
        1 for node in final_state.nodes.values() if node["type"] == "knowledge"
    )
    if final_knowledge != knowledge_nodes:
        raise AssertionError(
            f"expected {knowledge_nodes} final knowledge nodes, got {final_knowledge}"
        )

    max_rss_raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    max_rss_bytes = max_rss_raw if sys.platform == "darwin" else max_rss_raw * 1024
    return {
        "schema": "kgdistiller-stress-report-v1",
        "status": "passed",
        "fixture": str(root),
        "knowledge_nodes": final_knowledge,
        "formats": ["markdown", "typst"],
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "graph": {
            "nodes": len(final_state.nodes),
            "edges": len(final_state.edges),
            "references": len(final_state.references),
            "graph_sha256": final_state.manifest["graph_sha256"],
        },
        "query": {
            "index_schema": status["schema"],
            "snapshot_sha256": status["snapshot_sha256"],
            "database_byte_stable": True,
            "resolved": len(resolved),
            "search_results": len(searched),
            "context_nodes": len(context["nodes"]),
            "latency_seconds": {
                "resolve_batch": _latency_summary(resolve_latencies),
                "search": _latency_summary(search_latencies),
                "hybrid_context": _latency_summary(context_latencies),
            },
        },
        "incremental": {
            "scope": incremental_report["scope"],
            "files": incremental_report["files"],
            "graph_digest_unchanged": True,
        },
        "transaction": {
            "enabled": transactions,
            "plan_status": plan["status"],
            "receipt_status": receipt["status"],
            "request_sha256": receipt["request_sha256"],
            "fault_injection": fault_injection and transactions,
            "reader_observations": len(observations),
            "reader_statuses": sorted(set(observations)),
            "reader_errors": 0,
        },
        "timings_seconds": {
            "initial_sync": sync_seconds,
            "agent_status": status_seconds,
            "resolve_batch": resolve_seconds,
            "search": search_seconds,
            "context": context_seconds,
            "incremental_sync": incremental_seconds,
            "ingest_plan": plan_seconds,
            "fault_injection": failure_seconds,
            "ingest_apply": apply_seconds,
        },
        "max_rss_raw": max_rss_raw,
        "max_rss_bytes": max_rss_bytes,
        "initial_sync_report": {
            "files": sync_report["files"],
            "definitions": sync_report["definitions"],
            "warnings": sync_report["warnings"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nodes", type=int, default=100_000)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--skip-fault-injection", action="store_true")
    parser.add_argument("--skip-transaction", action="store_true")
    parser.add_argument("--query-samples", type=int, default=20)
    args = parser.parse_args()

    if args.keep:
        root = Path(tempfile.mkdtemp(prefix="kgdistiller-stress-"))
        report = run_stress(
            root,
            knowledge_nodes=args.nodes,
            fault_injection=not args.skip_fault_injection,
            transactions=not args.skip_transaction,
            query_samples=args.query_samples,
        )
    else:
        with tempfile.TemporaryDirectory(prefix="kgdistiller-stress-") as temporary:
            report = run_stress(
                Path(temporary),
                knowledge_nodes=args.nodes,
                fault_injection=not args.skip_fault_injection,
                transactions=not args.skip_transaction,
                query_samples=args.query_samples,
            )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.report is not None:
        args.report.expanduser().resolve().write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
