# Transaction contract

## Authority and identity

An identity is created only by a reviewed native definition marker in a
registered Markdown, Typst, or LaTeX authority. Headings, ordering, syntax
wrappers, abbreviations, lexical similarity, translation, and graph proximity
are not identity evidence. Reviewed authored-name changes use the identity
registry; reviewed cross-namespace mappings use the alignment registry and
remain fingerprint-bound.

A registered source's `document_type` selects user-authored `node_kinds` and
`extraction_guidance`; it is independent of format and knowledge domain. These
rules guide the upstream review and do not create identities. Atomic entries
remain Markdown while evidence links directly to the original `.md`, `.typ` or
`.tex`; source conversion is not an ingest prerequisite. Preserve supported
legacy derived-evidence bindings when maintaining an existing record.

Every semantic edge is direct, typed, and supported by concrete source
evidence. Candidate and personal namespaces remain separate. Conflicting or
uncertain identities block their own operation.

## One stale-safe transaction

A `kgdistiller-ingest-request-v1` binds candidate/query inputs, target graph/snapshot/
alignment digests, source digests, complete native patches, marker/ref
expectations, reviewed mappings, and one bounded `kgdistiller-agent-delta-v1`.
Run `kgdistiller ingest plan` first and inspect the staged result. Apply the
content-addressed request only after review.

Authority SHA-256 values use UTF-8 text with CRLF/CR normalized to LF. The
writer holds one bounded lock, revalidates all preconditions, atomically
installs identity authorities, `.knowledge/entries/` Markdown, registries and
the compact `kgdistiller-graph-v2` graph, then returns a canonical
`kgdistiller-ingest-receipt-v1`.

Reject unknown request, delta, registry, and graph discriminators. The writer
requires `kgdistiller-graph-v2`; if the project reports any other graph schema,
stop and report it instead of preparing a transaction. A transaction preserves
IDs, aliases and accepted relationships. Entry bodies persist once in Markdown
and are hydrated by readers.

Accept success only when `status` is `committed` and after-digests match a fresh
generation-checked `agent status`. Reusing the exact request is idempotent;
changing it requires a new canonical digest and review.

Do not compose `apply`, `sync`, or `reconcile` as a substitute, and do not edit
raw graph files, identities, or alignments directly.
Atomic entry Markdown is changed only through the reviewed transaction. There is no
secondary database, embedding, provider, or materialization boundary. Extraction
profiles are source-registry data within the existing knowledge project.
If rollback is degraded or fails, stop writers, preserve the journal/backups,
and recover from them or a known-good Git revision.

## Downstream state and handoff

A portable snapshot is optional. When explicitly maintaining one for this
update, refresh it with `store snapshot` and confirm with `store verify`.
Otherwise report an existing outdated snapshot as stale and verify it before
using it as backup; do not create one during ordinary capture. A snapshot
contains identity authorities, entry Markdown and evidence, registries,
graph artifacts and a document inventory. None of its inventory files is
required for live graph queries or ordinary Git backup.

Refreshing the Obsidian graph feed is a separate action. The project root may
be an editor vault whose registered Markdown files remain authority; the feed
under `.knowledge/build/obsidian/` is derived and must not be scanned or
ingested back.

Return request/plan paths and digests, precondition digests, reviewed findings,
canonical receipt, post-commit checks, store state, and blocked operations. A
committed ingest receipt does not authorize Git actions or remote pushes.
