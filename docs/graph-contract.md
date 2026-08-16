# Native Vault graph contract

## Authority layers

A native Vault has three deliberately different layers:

1. ordinary Markdown concept, field, and topic notes are knowledge authority;
2. captured Markdown/Typst/LaTeX versions and derivation rows are immutable
   source evidence and review history;
3. `.kgdistiller/graph` is a deterministic derived `qlkg-v3` projection.

The graph, frontend, recall reports, portable store, static HTML, and Obsidian
views never become a second authority.

## Native notes and identity

Each native note has closed, canonical kgdistiller-owned frontmatter keys and a
stable Vault-local ID. Non-kgdistiller user properties, comments, and line
endings remain user-owned and survive native merges. Concept notes contain
concise reviewed knowledge text. Field and topic notes define the taxonomy DAG.
Note paths are portable provenance; a filename, directory, heading, Wikilink
display, or source order never defines identity.

Concept and taxonomy links may have multiple parents. The canonical graph is a
DAG, not a forced tree. A UI may choose a deterministic primary display parent
without discarding other memberships.

Changing a stable ID, merging concepts, or reassigning qualified identities is
an explicit reviewed decision. Federated identity is always
`vault_id:node_id`; equal local IDs in different Vaults remain different unless
an explicit reviewed alignment says otherwise.

## Source archive and derivations

`source capture` assigns one stable document ID and append-only version
sequence. Raw source blobs are content-addressed. Authority-text digests use
strict UTF-8 with CRLF and lone CR normalized to LF; all other bytes, including
the final newline, remain significant.

A derivation records how one captured version supports zero or more concepts
and typed relations. A fresh current version resolves to `committed`,
`reviewed-empty`, or a valid newline-only `carried-forward` chain. A semantic
source change without a new review makes the document and affected knowledge
stale. Removed text never deletes a concept or relation automatically.

Evidence remains in the source ledger and is joined by recall/API. Do not add
ad hoc source-version fields to `qlkg-v3` nodes or treat an excerpt as identity.

## Native compilation

The native compiler inventories exactly the configured concept, field, and
topic Markdown roots and emits the existing `qlkg-v3` schema. It derives:

- knowledge nodes and entry text from concept-note authority;
- field/topic nodes and `contains` topology from taxonomy/native links;
- supported semantic relations from typed concept properties;
- authority paths and definition hashes from the native note bytes;
- curation status from note/ledger evidence closure;
- canonical source registry, diagnostics, graph artifacts, and entry shards.

The source-hash map binds only normalized native authority notes. Raw evidence,
source-ledger JSONL, blobs, and receipts are not graph source entries. The Vault
store binds those separately.

Compilation is deterministic and byte-exact. `knowledge check` hydrates the
official graph, validates source hashes, verifies evidence closure, recompiles
native notes, and rejects any byte or semantic mismatch.

## Relations

Supported relations remain:

- `contains`: field/topic classification;
- `prerequisite-for`: direct learning dependency;
- `implies`: direct logical entailment;
- `generalizes`: target is a special case;
- `contrasts-with`: explicit symmetric comparison;
- `derived-from`: direct construction or proof dependency.

Store direct, high-confidence relations with source-version evidence. Do not
store transitive closure, document order, co-occurrence, broad association, or
an embedding similarity. `contains` is typed and acyclic;
`prerequisite-for` is acyclic; all semantic endpoints exist in the same native
Vault generation.

## Federated recall

Read-only consumers use `recall`, `/api/v1`, or federated MCP—not raw shards.
Each response binds one federation generation plus the Vault manifest, graph,
source-ledger, authority, and live-source tokens for every included Vault.
Missing/unhealthy Vaults remain explicit.

Identity resolution uses exact canonical names and collision-free reviewed
aliases. Taxonomy, lexical, and graph lanes expose deterministic ranking reasons
but cannot establish identity. Retrieval preserves multi-parent paths,
staleness, evidence, omissions, and bounds. Full evidence is fetched only for a
final bounded handle set.

## Required native invariants

- native note paths are portable, NFC, collision-free, ordinary single-link
  files under one configured authority root;
- node IDs are stable and unique inside one Vault;
- taxonomy is a multi-parent acyclic DAG with valid endpoint types;
- semantic edges have existing endpoints, supported predicates, and current
  reviewed evidence;
- current knowledge nodes and semantic evidence close in both directions;
- source version/predecessor/carry-forward chains are canonical and bounded;
- graph artifacts, manifest, source hashes, and recompiled bytes agree;
- one request exposes no mixed generation;
- stale or missing evidence remains visible rather than silently trusted or
  deleted.

Run:

```sh
kgdistiller knowledge check --vault VAULT_ID
kgdistiller recall status --vault VAULT_ID
```

## Legacy isolation

The marker scanner (`--[[...]]--`, `#kn[...]`, `\kn{...}`), optional identity
registries, `qlkg-agent-delta-v3`, and single-project low-level commands retain
their legacy authority meaning. The native compiler never scans those markers
inside captured source evidence.

Never copy or relabel a legacy graph/delta/store/projection as native. Adopt
legacy knowledge by capturing its source as evidence, producing reviewable
native note candidates, re-resolving identities, applying a native v1
transaction, and recompiling. When metadata is replaceable, re-distill instead
of adding a compatibility layer.
