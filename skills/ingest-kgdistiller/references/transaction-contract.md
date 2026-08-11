# Transaction contract

## Authority and identity

An identity is created only by a reviewed native definition marker in a
registered Markdown, Typst, or LaTeX authority. Headings, ordering, syntax
wrappers, abbreviations, embeddings, and graph proximity are not identity
evidence. Reviewed authored-name changes use the identity registry; reviewed
cross-namespace mappings use the alignment registry and remain fingerprint
bound.

Every semantic edge must be direct, typed, and supported by concrete source
evidence. Candidate and personal namespaces remain separate. Conflicting or
uncertain identities block their own operation instead of being silently
merged.

## One stale-safe transaction

A `qlkg-ingest-request-v1` binds the candidate/query inputs, target graph,
snapshot, alignment and source digests, complete source patch, marker/ref
expectations, reviewed mappings, and bounded `qlkg-agent-delta-v2`. Run
`kgdistiller ingest plan` first in staging and inspect the result. Apply the
same content-addressed request only after review.

An authority source digest is SHA-256 over UTF-8 text read with universal
newline translation: CRLF and lone CR are represented as LF, while every other
character remains exact. Use this boundary for `expected_sha256`; raw checkout
bytes are not the transaction contract.

The writer holds one bounded lock, revalidates all preconditions, installs the
authority and derived generation atomically, and returns a canonical
`qlkg-ingest-receipt-v1`. Accept success only when status is committed and the
after-digests match a fresh Agent status. Reusing the exact request is
idempotent; changing it requires a new digest and review.

Do not compose legacy `apply`, `sync`, or `reconcile` commands as a substitute
for this boundary. Do not edit graph JSONL, entry shards, SQLite, alignment, or
identity files directly. If rollback is degraded or fails, stop writers,
preserve the journal and repository, and recover from its recorded backups or a
known-good Git revision.

## Handoff

Return request and plan paths/digests, precondition digests, plan findings,
canonical receipt, post-commit checks, and any operation left blocked. A
committed ingest receipt does not authorize Git, remote publication, provider
credentials, or static export adoption.
