# Performance baseline

This page records a reproducible local baseline, not a portable service-level
objective. Results depend on storage, available memory, Python, graph shape,
source size, and query mix. The fixture is disposable and contains no personal
knowledge.

## Historical reference environment

This measurement predates the Windows-native and WSL support decision. It is
retained for provenance and regression context, but it is not a release or
support gate.

- Apple A18 Pro (`arm64`)
- 8 GiB unified memory
- macOS 26.5.1 (build 25F80)
- Python 3.9.6
- 100,000 knowledge nodes, 100,001 total nodes, and 100,000 edges
- equally divided generated Markdown and Typst authorities

## Query and synchronization profile

Command:

```sh
PYTHONPATH=src python3 scripts/stress_workflow.py --nodes 100000 \
  --skip-transaction --query-samples 20 \
  --report /tmp/kgdistiller-stress-100k-query.json
```

The run completed successfully. Read-only queries left the SQLite index
byte-for-byte unchanged, and a no-op incremental sync left the graph digest
unchanged.

| Measurement | Result |
| --- | ---: |
| Initial full sync | 68.515 s |
| No-op incremental sync | 59.987 s |
| Resolve batch p50 / p95 / max | 0.000323 / 0.000348 / 0.000368 s |
| FTS search p50 / p95 / max | 0.213244 / 0.225039 / 0.228828 s |
| Hybrid context p50 / p95 / max | 0.328977 / 0.346568 / 0.347011 s |
| Peak resident memory | 2,546,581,504 bytes (2.37 GiB) |

Hybrid retrieval uses exact and FTS seeds, a bounded graph neighborhood, and
personalized PageRank over at most 400 candidate nodes. The standalone PPR
command continues to support the full selected namespace. This keeps normal
context assembly bounded without changing the graph or identity contracts.

## Transaction and fault profile

A separate full workflow run used the same graph size with transactional plan,
fault injection, apply, and a concurrent reader:

```sh
PYTHONPATH=src python3 scripts/stress_workflow.py --nodes 100000 \
  --report /tmp/kgdistiller-stress-100k-report.json
```

It completed with a planned request and committed receipt. The injected
pre-install failure left material graph state unchanged. During apply, the
reader made 5,410 observations, saw only the old (`missing`) or new (`exact`)
generation, and reported no errors.

| Measurement | Result |
| --- | ---: |
| Transaction plan | 232.085 s |
| Transaction apply | 271.132 s |
| Fault-injection rollback check | 19.350 s |
| Peak resident memory | 2,818,736,128 bytes (2.63 GiB) |

That transaction run preceded the bounded-PPR optimization, so its 9.108 s
single context timing is retained as historical evidence and must not be used
as the current query baseline.

## Release interpretation

The original exploratory targets of a full sync below 60 seconds and peak
memory below 1 GiB were not met on this 8 GiB host. They are findings, not
silently relaxed pass results. The historical measurement envelope was:

- initial sync at or below 90 seconds;
- no-op incremental sync at or below 90 seconds;
- resolve-batch p95 below 0.05 seconds;
- FTS search p95 below 0.5 seconds;
- hybrid-context p95 below 1 second;
- peak resident memory below 3 GiB;
- transaction plan below 300 seconds and apply below 360 seconds.

These bounds include modest variance around the recorded run. They do not block
release because the source environment is outside the supported Windows-host
matrix. A Windows-native, WSL, or production workload must establish its own
baseline instead of treating these numbers as universal guarantees.
