# Transaction contract

## Authority and identity

An identity is created only by a reviewed native definition marker in a
registered Markdown, Typst, or LaTeX authority. Headings, ordering, syntax
wrappers, abbreviations, lexical similarity, translation, and graph proximity
are not identity evidence. Reviewed authored-name changes use the identity
registry; reviewed cross-namespace mappings use the alignment registry and
remain fingerprint-bound.

Every semantic edge is direct, typed, and supported by concrete source
evidence. Candidate and personal namespaces remain separate. Conflicting or
uncertain identities block their own operation.

## One stale-safe transaction

A `qlkg-ingest-request-v2` binds candidate/query inputs, target graph/snapshot/
alignment digests, source digests, complete native patches, marker/ref
expectations, reviewed mappings, and one bounded `qlkg-agent-delta-v3`.
Run `kgdistiller ingest plan` first and inspect the staged result. Apply the
content-addressed request only after review.

Authority SHA-256 values use UTF-8 text with CRLF/CR normalized to LF. The
writer holds one bounded lock, revalidates all preconditions, atomically
installs native authorities, registries, generated Typst registry, and the
deterministic `qlkg-v3` graph, then returns a canonical
`qlkg-ingest-receipt-v2`.

Reject superseded request, delta, registry, and graph discriminators. Ingest is
not a migration boundary: use the deployment workflow to establish a Git
rollback point, explicitly update reviewed registry discriminators, and rebuild
the v3 graph from native authorities before preparing a transaction.

Accept success only when `status` is `committed` and after-digests match a fresh
generation-checked `agent status`. Reusing the exact request is idempotent;
changing it requires a new canonical digest and review.

Do not compose `apply`, `sync`, or `reconcile` as a substitute, and do not edit
graph JSON/JSONL, entry shards, identities, or alignments directly. There is no
secondary database, embedding, provider, profile, or materialization boundary.
If rollback is degraded or fails, stop writers, preserve the journal/backups,
and recover from them or a known-good Git revision.

## Downstream state and handoff

If a `qlkg-store-v2` manifest already exists, refresh with `store snapshot` and
confirm with `store verify`. A store snapshot contains only authorities,
registries, deterministic graph artifacts, and canonical document inventory.

Static-site and Obsidian exports are separate downstream actions. The project
root may be an editor vault whose registered Markdown files remain authority;
only the managed Obsidian subtree or external browsing-only vault/projection is
lossy and must not be scanned or ingested back.

Return request/plan paths and digests, precondition digests, reviewed findings,
canonical receipt, post-commit checks, store state, and blocked operations. A
committed ingest receipt does not authorize Git actions, remote publication,
network exposure, or export adoption.
