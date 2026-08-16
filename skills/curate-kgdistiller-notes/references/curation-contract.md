# Native Vault curation contract

## Inputs

Require one or more user-selected `.md`, `.typ`, or `.tex` source paths. Resolve
each path through `kgdistiller vault locate`; do not accept a guessed Vault or
repository root. Authority and patch paths in one ingest request must belong to
its selected Vault. The query-report artifact path is instead portable and
relative to the request artifact root; its source/evidence ownership must still
resolve to the selected Vault.

For every source, retain the canonical `document_id`, captured `version_id`,
normalized content digest, predecessor version when present, and bounded diff.
A captured version is immutable evidence. A capture alone is never a reviewed
derivation.

## Identity decisions

Resolve a complete bounded candidate batch through federated `recall`. Use
Vault-qualified handles in all internal and user-facing decisions. Record one
of these dispositions for every candidate:

- `reuse`: exact or reviewed-alias identity was established;
- `add`: a reviewed new identity will be authored;
- `update`: reviewed evidence changes an existing identity;
- `reject`: the candidate is not knowledge to retain;
- `defer`: identity or evidence remains unresolved.

Ambiguous recall results may only be rejected or deferred. Lexical, taxonomy,
graph, translation, acronym, or layout similarity never upgrades ambiguity to
identity.

## Native note proposal

Propose canonical Markdown only under the Vault manifest's concept, field, and
topic roots. Preserve the existing stable concept ID when updating. Express
taxonomy and semantic relations with the native closed frontmatter contract;
do not create implicit identities from headings or prose. Keep relation
evidence direct and source-grounded.

Every committed concept or relation in a derivation must cite at least one
span from the same captured source version. Bind line and optional column
coordinates to the exact excerpt digest. A `reviewed-empty` derivation contains
no candidates, concepts, or relation evidence.

## Request handoff

The handoff targets `qlkg-vault-ingest-request-v1` and includes:

- `vault_id`, registry generation, and Vault manifest digest;
- source-ledger generation, graph generation, and note inventory bases;
- one canonical federated recall report artifact and digest;
- exact write/delete note patches with before and after byte digests;
- source-version derivation updates and canonical evidence spans;
- empty `alignment_mutations` for this release;
- explicit reviewed status, reviewer, evidence, and provenance.

The request is bounded and self-digested. Planning and applying the request are
separate responsibilities of `$ingest-kgdistiller`.

## Stable stop conditions

Stop without a handoff when Vault ownership is missing or ambiguous, capture or
diff is invalid, generations cannot be kept coherent, an authority path is
unsafe, evidence does not support a proposed note/relation, recall ambiguity
would affect a write, or user review is incomplete.
