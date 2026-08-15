#!/usr/bin/env python3
"""Exercise a disposable Markdown/Typst/LaTeX JSON-memory workflow."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from kgdistiller.alignment import empty_alignment_set
from kgdistiller.cli import (
    apply_delta,
    generated_id,
    load_state,
    make_agent_snapshot,
    sha256_authority_file,
    sha256_file,
    sha256_text,
    synchronize,
)
from kgdistiller.contracts import sha256_json
from kgdistiller.ingest import (
    CAPABILITY,
    REQUEST_SCHEMA,
    IngestError,
    IngestPaths,
    apply_ingest,
    finalize_request,
    plan_ingest,
)
from kgdistiller.query import (
    COMPARISON_SCHEMA,
    GraphView,
    expand,
    query_status,
    resolve_concepts,
    search,
)
from kgdistiller.retrieval import (
    build_context_from_execution,
    execute_retrieval_plan,
    legacy_retrieval_plan,
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _timed(callable_: Callable[..., Any], *args: Any, **kwargs: Any) -> tuple[Any, float]:
    started = time.perf_counter()
    result = callable_(*args, **kwargs)
    return result, round(time.perf_counter() - started, 6)


def _latency_summary(samples: list[float]) -> dict[str, float | int]:
    ordered = sorted(samples)
    if not ordered:
        return {"samples": 0, "p50": 0.0, "p95": 0.0, "max": 0.0}

    def percentile(fraction: float) -> float:
        index = min(len(ordered) - 1, max(0, int(len(ordered) * fraction + 0.999) - 1))
        return round(ordered[index], 6)

    return {
        "samples": len(ordered),
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "max": round(ordered[-1], 6),
    }


def _maximum_rss() -> tuple[int | None, int | None, str]:
    try:
        import resource
    except ImportError:
        return None, None, "unavailable"
    raw = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return raw, raw if sys.platform == "darwin" else raw * 1024, "getrusage"


def _graph_bytes(graph_dir: Path) -> dict[str, str]:
    return {
        path.relative_to(graph_dir).as_posix(): sha256_file(path)
        for path in sorted(graph_dir.rglob("*"))
        if path.is_file()
    }


def _registry() -> dict[str, Any]:
    return {
        "schema": "qlkg-sources-v3",
        "fields": [
            {
                "id": "stress-testing",
                "label": "Stress Testing",
                "text": "Synthetic concepts used only by a disposable stress fixture.",
            }
        ],
        "sources": [
            {
                "id": "stress:fixture",
                "subject": "stress",
                "course": "fixture",
                "knowledge_origin": "personal-note",
                "fields": ["stress-testing"],
                "root": "notes/stress",
                "files": ["*.md", "*.typ", "*.tex"],
                "web": "https://example.invalid/stress",
                "topics": [],
            }
        ],
    }


def _build_repository(root: Path, nodes: int) -> tuple[IngestPaths, list[str], dict[str, list[str]]]:
    notes = root / "notes/stress"
    notes.mkdir(parents=True)
    labels = [f"Stress Concept {index:05d}" for index in range(nodes)]
    ids = [generated_id(label) for label in labels]
    by_format: dict[str, list[str]] = {"markdown": [], "typst": [], "latex": []}
    markdown: list[str] = ["# Stress authority", ""]
    typst: list[str] = ["= Stress authority", ""]
    latex: list[str] = [r"\section{Stress authority}", ""]
    for index, label in enumerate(labels):
        if index % 3 == 0:
            by_format["markdown"].append(ids[index])
            markdown.extend([f"> **Definition: --[[{label}]]--**", ">", f"> Fixture {index}.", ""])
        elif index % 3 == 1:
            by_format["typst"].append(ids[index])
            typst.extend([f"#definition(title: [#kn[{label}]])[Fixture {index}.]", ""])
        else:
            by_format["latex"].append(ids[index])
            latex.extend([rf"\begin{{definition}}\kn{{{label}}} Fixture {index}.\end{{definition}}", ""])
    (notes / "concepts.md").write_text("\n".join(markdown), encoding="utf-8")
    (notes / "concepts.typ").write_text("\n".join(typst), encoding="utf-8")
    (notes / "concepts.tex").write_text("\n".join(latex), encoding="utf-8")

    registry = root / "knowledge/sources.json"
    alignments = root / "knowledge/alignments.json"
    _write_json(registry, _registry())
    _write_json(alignments, empty_alignment_set())
    paths = IngestPaths(
        repo_root=root,
        registry=registry,
        graph_dir=root / "knowledge/graph",
        identities=root / "knowledge/identities.json",
        alignments=alignments,
        typst_registry=root / "knowledge/build/knowledge-registry.typ",
    )
    synchronize(
        root,
        registry,
        paths.graph_dir,
        paths.typst_registry,
        identities=paths.identities,
        alignments=paths.alignments,
        files=[],
        course=None,
        subject=None,
        write=True,
    )

    delta = root / "knowledge/build/stress.delta.json"
    _write_json(
        delta,
        {
            "schema": "qlkg-agent-delta-v3",
            "remove_nodes": [],
            "nodes": [
                {
                    "id": node_id,
                    "text": f"Deterministic payload for {label}; token-{index:05d}.",
                }
                for index, (node_id, label) in enumerate(zip(ids, labels))
            ],
            "edges": [
                {
                    "source": ids[index - 3],
                    "relation": "prerequisite-for",
                    "target": ids[index],
                    "confidence": "high",
                    "evidence": f"Same-authority fixture chain {index - 3} -> {index}.",
                }
                for index in range(3, nodes)
            ],
            "remove_edges": [],
        },
    )
    apply_delta(paths.graph_dir, paths.typst_registry, delta)
    return paths, ids, by_format


def _candidate_snapshot() -> dict[str, Any]:
    snapshot = {
        "schema": "qlkg-agent-snapshot-v2",
        "namespace": "paper:stress",
        "graph": {
            "schema": "qlkg-v3",
            "sha256": "c" * 64,
            "counts": {"nodes": 1, "edges": 0, "references": 0},
        },
        "nodes": [
            {
                "id": "stress-ingested",
                "type": "knowledge",
                "label": "Stress Ingested",
                "text": "A transactionally ingested stress concept.",
                "properties": {"aliases": []},
                "provenance": {
                    "authority": "paper.md",
                    "line": 1,
                    "source_format": "markdown",
                },
            }
        ],
        "edges": [],
        "references": [],
        "diagnostics": {"errors": [], "warnings": []},
    }
    snapshot["snapshot_sha256"] = sha256_json(snapshot)
    return snapshot


def _transaction_request(
    paths: IngestPaths,
    markdown_ids: list[str],
    *,
    mode: str,
    request_id: str,
) -> dict[str, Any]:
    candidate = _candidate_snapshot()
    candidate_path = paths.repo_root / "knowledge/build/stress-candidate.json"
    _write_json(candidate_path, candidate)
    target = make_agent_snapshot(load_state(paths.graph_dir))
    comparison = {
        "schema": COMPARISON_SCHEMA,
        "alignment_sha256": sha256_json(empty_alignment_set()),
        "candidate": {
            "namespace": candidate["namespace"],
            "snapshot_sha256": candidate["snapshot_sha256"],
            "graph_sha256": candidate["graph"]["sha256"],
        },
        "target": {
            "namespace": "personal",
            "snapshot_sha256": target["snapshot_sha256"],
            "graph_sha256": target["graph"]["sha256"],
        },
        "results": [
            {
                "candidate": {"namespace": "paper:stress", "id": "stress-ingested"},
                "status": "unmatched",
                "identity_target_id": None,
                "candidates": [],
                "registry_evidence": [],
                "rejected_target_ids": [],
            }
        ],
        "summary": {
            "matched": 0,
            "ambiguous": 0,
            "unmatched": 1,
            "present_edges": 0,
            "missing_edges": 0,
        },
        "alignment_report_sha256": "1" * 64,
    }
    comparison_path = paths.repo_root / "knowledge/build/stress-comparison.json"
    _write_json(comparison_path, comparison)
    authority = paths.repo_root / "notes/stress/concepts.md"
    content = authority.read_text(encoding="utf-8") + (
        "\n> **Definition: --[[Stress Ingested]]--**\n>\n"
        "> A transactionally ingested stress concept.\n"
    )
    state = load_state(paths.graph_dir)
    return finalize_request(
        {
            "schema": REQUEST_SCHEMA,
            "request_id": request_id,
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
                    "path": authority.relative_to(paths.repo_root).as_posix(),
                    "operation": "write",
                    "expected_sha256": sha256_authority_file(authority),
                    "content": content,
                    "content_sha256": sha256_text(content),
                    "expected_markers": {
                        "definitions": [*markdown_ids, "stress-ingested"],
                        "references": [],
                    },
                }
            ],
            "decisions": [
                {
                    "candidate_id": "stress-ingested",
                    "action": "add",
                    "target_id": "stress-ingested",
                    "evidence": "The disposable authority explicitly defines it.",
                }
            ],
            "delta": {
                "schema": "qlkg-agent-delta-v3",
                "remove_nodes": [],
                "nodes": [
                    {
                        "id": "stress-ingested",
                        "text": "A transactionally ingested stress concept.",
                    }
                ],
                "edges": [],
                "remove_edges": [],
            },
            "alignment_decisions": [],
            "review": {
                "status": "reviewed",
                "reviewer": "stress-harness",
                "evidence": ["The authority marker and candidate were reviewed."],
                "provenance": [
                    {
                        "path": authority.relative_to(paths.repo_root).as_posix(),
                        "line": 1,
                        "kind": "authority",
                    }
                ],
            },
        }
    )


def run(nodes: int, query_samples: int, *, transaction: bool, fault_injection: bool) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="kgdistiller-stress-") as raw:
        root = Path(raw)
        paths, ids, by_format = _build_repository(root, nodes)
        view = GraphView.load(paths.graph_dir, paths.alignments)
        status = query_status(view)
        graph_before_queries = _graph_bytes(paths.graph_dir)
        latencies: dict[str, list[float]] = {
            "resolve": [],
            "lexical_search": [],
            "graph_expand": [],
            "hybrid_context": [],
        }
        middle = nodes // 2
        for _ in range(query_samples):
            resolved, elapsed = _timed(
                resolve_concepts, view, [f"Stress Concept {middle:05d}"]
            )
            if resolved[0]["candidate_ids"] != [ids[middle]]:
                raise RuntimeError("identity resolution returned the wrong concept")
            latencies["resolve"].append(elapsed)

            searched, elapsed = _timed(
                search, view, f"token-{middle:05d}", limit=10
            )
            if not searched or searched[0]["node"]["id"] != ids[middle]:
                raise RuntimeError("lexical search returned the wrong concept")
            latencies["lexical_search"].append(elapsed)

            expanded, elapsed = _timed(
                expand,
                view,
                [ids[0]],
                direction="out",
                edge_types=["prerequisite-for"],
                max_depth=min(8, nodes - 1),
                limit=min(50, nodes),
            )
            if not expanded["nodes"]:
                raise RuntimeError("graph expansion returned no nodes")
            latencies["graph_expand"].append(elapsed)

            plan = legacy_retrieval_plan(
                f"token-{middle:05d}", limit=min(20, nodes), max_depth=2
            )
            execution = execute_retrieval_plan(view, plan)
            _, elapsed = _timed(
                build_context_from_execution,
                view,
                execution,
                plan=plan,
                token_budget=3000,
            )
            latencies["hybrid_context"].append(elapsed)
        graph_after_queries = _graph_bytes(paths.graph_dir)
        if graph_before_queries != graph_after_queries:
            raise RuntimeError("read-only JSON queries changed the authority graph")

        digest_before_incremental = load_state(paths.graph_dir).manifest["graph_sha256"]
        synchronize(
            root,
            paths.registry,
            paths.graph_dir,
            paths.typst_registry,
            identities=paths.identities,
            alignments=paths.alignments,
            files=[Path("notes/stress/concepts.md")],
            course=None,
            subject=None,
            write=True,
        )
        digest_after_incremental = load_state(paths.graph_dir).manifest["graph_sha256"]

        transaction_report: dict[str, Any] = {
            "enabled": transaction,
            "plan_status": "skipped",
            "receipt_status": "skipped",
            "fault_rollback": "skipped",
            "reader_errors": 0,
        }
        if transaction:
            if fault_injection:
                failing = _transaction_request(
                    paths,
                    by_format["markdown"],
                    mode="apply",
                    request_id="stress-fault",
                )

                def inject(stage: str) -> None:
                    if stage == "installed-graph":
                        raise IngestError("stress-fault", stage, stage=stage)

                try:
                    apply_ingest(paths, failing, failure_injector=inject)
                except IngestError:
                    transaction_report["fault_rollback"] = "passed"
                else:
                    raise RuntimeError("fault injection unexpectedly committed")
            planned = plan_ingest(
                paths,
                _transaction_request(
                    paths,
                    by_format["markdown"],
                    mode="plan",
                    request_id="stress-plan",
                ),
            )
            receipt = apply_ingest(
                paths,
                _transaction_request(
                    paths,
                    by_format["markdown"],
                    mode="apply",
                    request_id="stress-apply",
                ),
            )
            transaction_report.update(
                {
                    "plan_status": planned["status"],
                    "receipt_status": receipt["status"],
                    "query_backend": receipt["engine"]["query_backend"],
                }
            )
            committed = GraphView.load(paths.graph_dir, paths.alignments)
            if resolve_concepts(committed, ["Stress Ingested"])[0]["status"] != "exact":
                transaction_report["reader_errors"] = 1

        maximum_rss_raw, maximum_rss_bytes, maximum_rss_source = _maximum_rss()
        final_state = load_state(paths.graph_dir)
        final_knowledge = sum(
            node.get("type") == "knowledge" for node in final_state.nodes.values()
        )
        return {
            "schema": "qlkg-stress-report-v2",
            "status": "passed",
            "knowledge_nodes": nodes,
            "final_knowledge_nodes": final_knowledge,
            "formats": sorted(by_format),
            "query": {
                "backend": status["backend"],
                "graph_byte_stable": graph_before_queries == graph_after_queries,
                "latency_seconds": {
                    key: _latency_summary(value) for key, value in latencies.items()
                },
            },
            "incremental": {
                "graph_digest_unchanged": (
                    digest_before_incremental == digest_after_incremental
                )
            },
            "transaction": transaction_report,
            "sqlite_files": len(list(root.rglob("*.sqlite"))),
            "max_rss_raw": maximum_rss_raw,
            "max_rss_bytes": maximum_rss_bytes,
            "max_rss_source": maximum_rss_source,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes", type=int, default=1000)
    parser.add_argument("--query-samples", type=int, default=5)
    parser.add_argument("--skip-transaction", action="store_true")
    parser.add_argument("--skip-fault-injection", action="store_true")
    args = parser.parse_args()
    if args.nodes < 3 or args.nodes > 20_000:
        parser.error("--nodes must be between 3 and 20000")
    if args.query_samples < 1 or args.query_samples > 100:
        parser.error("--query-samples must be between 1 and 100")
    try:
        report = run(
            args.nodes,
            args.query_samples,
            transaction=not args.skip_transaction,
            fault_injection=not args.skip_fault_injection,
        )
    except Exception as error:
        print(f"stress workflow failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
