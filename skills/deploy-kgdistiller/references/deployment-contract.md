# Deployment contract

## Portable authority boundary

The knowledge project, not the kgdistiller product checkout and not SQLite, is
the backup unit. It owns registered Markdown, Typst, or LaTeX authorities plus
the committed credential-free files under `knowledge/`: source, identity,
alignment, and embedding policy registries; deterministic graph artifacts;
document inventory; exact embedding objects; and `store.json`.

Keep `knowledge/build/`, SQLite/WAL/generation files, local profiles,
credentials, plans, receipts, rendered pages, and provider caches local and
ignored. Exact committed embeddings are derived retrieval artifacts and never
grant identity or relation authority.

## Required checks

Before snapshot or export, run `check`, inspect embedding status separately,
create the portable store snapshot, and run `store verify`. A verified store is
not automatically semantically ready when a required embedding policy is
degraded. Never call a provider during `store snapshot`, `store verify`, or
`store materialize`.

On restore, verify before sync, delete only disposable build state when that is
explicitly intended, materialize the verified generation, check Agent status,
and exercise bounded resolution/context queries. Preserve an interrupted ingest
journal for automatic recovery; do not delete it to hide a mixed generation.

## Product and publication provenance

Record the installed kgdistiller version and exact full product commit. A
static publication must be a `qlkg-static-export-v1` bundle produced by
`kgdistiller export site`. Its receipt binds producer repository/version/commit,
source revision/digests, private and public graph digests, visibility policy,
and artifact bytes. The standalone verifier is the consumer boundary.
Public edges contain only the structural `source`, `relation`, and `target`
triple; source-derived evidence, fingerprints, origin, confidence, and
curation fields stay private even when both endpoints are public.
Export requires a clean instance checkout including untracked files. The
source registry, four core private graph projections, every manifest-declared
entry shard, and every authority named by `source_hashes` must be tracked by
current `HEAD`. Authority hashes use UTF-8 text with CRLF/CR normalized to LF,
matching scan, sync, ingest, store, and check on Windows and POSIX. That proven
`HEAD` is `source.revision`; it has no manual override. Commit instance inputs
first, export and verify second, then commit the exact four-file bundle as a
separate adoption revision. Bundle artifact hashes and byte counts likewise
use canonical LF UTF-8 text so the standalone verifier survives checkout EOL
conversion.
Periodic refresh uses explicit `--replace`: the old exact four-file bundle must
verify, the successor is generated and verified in staging, and installation
uses a rollback-safe directory swap. The successor manifest records the prior
`export_sha256`; never pre-delete the adopted bundle. The successor swap is the
commit point. A post-commit cleanup failure returns the committed export with
`cleanup_status: pending` and an exact managed path under ignored
`knowledge/build/.kgd-export-recovery/`; the next export verifies the successor
receipt before finishing cleanup. V1 contains exactly the three non-manifest
artifacts `graph.json`, `knowledge-registry.typ`, and `verify_export.py`.

Installing, linking, snapshotting, exporting, committing, pushing, and making
data network-public are separate authorities. Never place secrets in a project,
receipt, command output, Codex configuration, or export.
