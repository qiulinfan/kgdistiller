# Multi-vault knowledge workspace implementation specification

Status: implementation contract for the first usable local release.

Target branch: `codex/multivault-v1`.

This specification replaces the product assumption that one CLI invocation is
manually pointed at one knowledge-project root. It preserves kgdistiller's
provider-neutral deterministic core, explicit identity rules, evidence-backed
semantic relations, stale-safe writes, loopback-only local service, and
Markdown/Typst/LaTeX source support.

## 1. Product outcome

The release is usable when a user can:

1. register several local knowledge vaults once in a machine-local catalog;
2. give an ingestion Skill a source file without also giving it a project path;
3. have kgdistiller resolve exactly one owning vault, capture the source version,
   and create or update reviewed Markdown concept notes in that vault;
4. open the vault in Obsidian and see concept notes and their links in the native
   graph;
5. change a previously captured source, compare it with the captured predecessor,
   and review only the affected concepts;
6. query all available vaults without specifying their paths, descend through a
   bounded taxonomy, rerank a small candidate set, and retrieve source-grounded
   context;
7. inspect the same federated knowledge through a polished local web application;
8. clone or move a vault, register its new machine-local path, verify it, and use
   it without a database, vector index, embedding provider, or materialization
   step.

## 2. Non-goals

- No vector database, embeddings, provider configuration, or model calls in the
  deterministic engine.
- No authenticated multi-user or remotely exposed web service.
- No implicit knowledge identity from headings, document order, keywords,
  similarity, or graph proximity.
- No automatic deletion of a concept because source text disappeared.
- No cross-vault link claim in Obsidian's built-in graph. The native graph is
  per vault; the kgdistiller web application owns the federated graph.
- No mandatory Obsidian plugin in this release.
- No background file watcher. Capture and ingestion are explicit Skill/CLI
  actions; freshness is checked whenever status, ingest, query, or serve runs.

## 3. Authority hierarchy

The system has three distinct layers.

### 3.1 Evidence layer

An immutable source-version blob plus its metadata is evidence. It preserves the
text against which a concept or relation was reviewed. The live source path is a
locator, not identity and not the historical evidence body.

### 3.2 Knowledge layer

One Markdown concept note is the editable authority for one knowledge identity.
Its explicit `kgd_schema` and `kgd_id` frontmatter are a definition marker. Its
body is the curated entry. Its typed `kgd_*` link properties are the canonical
semantic relation declarations.

Typst and LaTeX remain accepted source-evidence formats. The default distillation
workflow produces Markdown concept notes instead of inserting identities into
the raw source.

### 3.3 Derived layer

The deterministic JSON graph, query views, navigation shards, web payloads,
static exports, and any browsing-only Obsidian projection are derived. They can
be rebuilt entirely from concept notes, vault metadata, derivation evidence, and
the immutable source-version ledger.

## 4. Filesystem layout

### 4.1 Machine-local vault registry

The only global state is:

```text
~/.kgdistiller/vaults.json
```

It contains absolute paths for this machine and must never be copied into a
vault snapshot or committed by kgdistiller. The default may be overridden in
tests and advanced automation by `KGDISTILLER_HOME`; this variable designates a
kgdistiller-specific directory and must never change `HOME` or `CODEX_HOME`.

`qlkg-vault-registry-v1` has:

```json
{
  "schema": "qlkg-vault-registry-v1",
  "vaults": [
    {"id": "math", "path": "C:/Users/name/Notes/Math"}
  ]
}
```

Vault IDs are lowercase bounded namespaces. Paths are absolute canonical local
paths. Duplicate IDs, duplicate canonical paths, missing vault manifests, and
overlapping registered roots fail closed. Registry writes use a lock, staging,
canonical JSON, atomic replacement, and a bounded ordinary-file check. The
registry contains at most 256 vaults, is at most 1 MiB, and rejects IDs longer
than 64 UTF-8 bytes, labels longer than 256 UTF-8 bytes, and canonical paths
longer than 4096 UTF-8 bytes. Readers and writers reject filesystem roots,
non-ordinary files, symlinks/reparse-point escapes, and any final resolved path
outside the explicitly selected kgdistiller home or vault.

### 4.2 Portable vault layout

```text
VAULT/
├── .kgdistiller/
│   ├── vault.json
│   ├── sources/
│   │   ├── manifest.json
│   │   ├── generations/GENERATION_SHA256/
│   │   │   ├── documents.jsonl
│   │   │   ├── versions.jsonl
│   │   │   └── derivations.jsonl
│   │   └── blobs/sha256/aa/FULL_RAW_SHA256
│   ├── receipts/sha256/aa/FULL_SHA256.json
│   ├── graph/
│   │   └── sources.json
│   └── build/
├── Knowledge/
│   ├── Concepts/
│   ├── Fields/
│   └── Topics/
└── ... user-authored source files ...
```

`.kgdistiller/vault.json` is committed and portable:

```json
{
  "schema": "qlkg-vault-v1",
  "id": "math",
  "label": "Mathematics",
  "description": "Personal mathematics notes",
  "concept_root": "Knowledge/Concepts",
  "field_root": "Knowledge/Fields",
  "topic_root": "Knowledge/Topics",
  "source_include": ["**/*.md", "**/*.typ", "**/*.tex"],
  "source_exclude": ["Knowledge/**", ".kgdistiller/**"]
}
```

All paths in a portable vault contract are normalized relative paths contained
by the vault. Symlinks/reparse points may not redirect managed or source paths
outside the vault.

Receipts are portable clone/store assets and must be retained. The
`.kgdistiller/build` directory remains disposable transaction workspace.

## 5. Concept note contract

A concept note is UTF-8 Markdown with YAML frontmatter using only simple scalar
or list values supported by Obsidian properties:

```markdown
---
kgd_schema: qlkg-concept-v1
kgd_id: measure-space
aliases:
  - 测度空间
tags:
  - kgdistiller/concept
kgd_fields:
  - "[[Knowledge/Fields/Measure Theory]]"
kgd_topics:
  - "[[Knowledge/Topics/Measure]]"
kgd_prerequisites:
  - "[[Knowledge/Concepts/Sigma Algebra]]"
kgd_implies: []
kgd_generalizes: []
kgd_contrasts_with: []
kgd_derived_from: []
---

# Measure space

A measure space is ...
```

Rules:

- `kgd_id`, not the filename or heading, is identity.
- The first H1 is the display label. Changing it is an explicit rename reviewed
  against existing IDs and aliases.
- Filenames are readable slugs. Every generated internal link uses the vault-
  relative path, not a potentially ambiguous basename.
- Obsidian may rename a file and update links; kgdistiller retains the ID and
  records the path move on the next sync.
- `kgd_prerequisites` is written on the dependent concept and compiles to
  `prerequisite prerequisite-for concept`.
- `kgd_implies`, `kgd_generalizes`, and `kgd_derived_from` are outgoing from the
  current concept. `kgd_contrasts_with` is symmetric and canonicalized once.
- Relation links must resolve to a concept/field/topic note in the same vault.
- Concept notes do not duplicate source-version mappings. The derivation ledger
  is the sole authority for concept and relation evidence; source history shown
  in the UI is joined from that ledger.
- A relation declaration without current evidence in a committed derivation
  remains visible but receives `needs-review`; it is excluded from trusted
  retrieval by default.
- Unknown `kgd_*` properties fail validation. Non-kgdistiller user properties
  are preserved byte-for-byte by transactional edits.

The implementation may use a small established YAML parser already present in
the environment only after checking current project dependencies and types. It
must not implement a general YAML parser with regular expressions.

Field and topic notes use the same explicit-identity principle:

```markdown
---
kgd_schema: qlkg-taxonomy-v1
kgd_id: measure-theory
kgd_kind: field
aliases:
  - Measure Theory
kgd_parents: []
---

# Measure theory
```

`field` and `topic` are the only `kgd_kind` values. Fields are roots and have no
parents. Topic parents are one or more vault-relative links to fields;
topic-to-topic nesting is outside this release because the retained `qlkg-v3`
graph contract has a two-level taxonomy. A topic may belong to several fields,
and a concept may belong to several fields and topics, so the resulting
navigation graph is still a DAG. `kgd_fields` links only to field notes and
`kgd_topics` only to topic notes. Semantic relation properties connect concepts
to concepts; taxonomy membership is compiled separately as `field -> topic`,
`field -> concept`, and `topic -> concept` `contains` edges.

## 6. Source archive and incremental derivation

### 6.1 Stable document identity

`qlkg-source-document-v1` records:

- stable `document_id`;
- vault-relative live `path`;
- format;
- current normalized text digest;
- current version ID;
- lifecycle status: `captured`, `reviewed-empty`, `distilled`, `stale`, or
  `failed`.

Moving a file within one vault updates the locator without changing its document
identity only after an exact-content match or explicit review. Path alone never
establishes identity.

### 6.2 Immutable versions

`qlkg-source-version-v1` records one captured generation:

- `version_id`, `document_id`, and monotonic per-document sequence;
- raw blob SHA-256 and normalized UTF-8 text SHA-256;
- blob relative path, original vault-relative locator, format, byte count, and
  capture timestamp;
- predecessor version ID when one exists.

`version_id` is a capture-event identity of the form
`doc:<document_id>:v<zero-padded-sequence>`, not a content identity. Blob
filenames contain only the full raw digest; format and the captured locator live
in metadata so identical bytes with different source suffixes are truly
deduplicated. A sequence `A → B → A` therefore produces three version
events while the third event reuses the first event's blob.

Blobs are content-addressed and immutable. Equal raw content is deduplicated.
The normalized digest prevents newline-only checkout changes from triggering
semantic re-distillation; exact raw bytes remain available as the backup.

### 6.3 Derivation ledger

`qlkg-derivation-v1` binds one source version to:

- graph generation reviewed;
- candidate IDs and their dispositions;
- resulting concept IDs;
- bounded evidence spans for each concept and semantic relation. Every span has
  `version_id`, inclusive 1-based `start_line` and `end_line`, and
  `excerpt_sha256`; optional `start_column` and `end_column` must occur together
  and are 0-based Unicode-scalar offsets with a half-open end;
- status `planned`, `committed`, `reviewed-empty`, `carried-forward`,
  `superseded`, or `failed`;
- `inherited_from_version_id` when the status is `carried-forward`;
- canonical ingest receipt digest for a committed update.

Several concepts may cite the same source version. A concept may cite several
source versions. The ledger is the sole authoritative mapping from source
versions to concepts, relations, and evidence spans, and is the authoritative
answer to whether a source version has been reviewed and distilled.

Evidence coordinates address the version's normalized text: decode strict
UTF-8, replace CRLF and bare CR with LF, and perform no other Unicode or
whitespace normalization. Splitting that text on LF defines 1-based logical
lines; line terminators are excluded, and a final LF creates a final empty
logical line. Without columns, the excerpt is the selected full logical lines
joined by one LF with no extra trailing LF. With columns, it is the exact
substring from `(start_line, start_column)` inclusive to
`(end_line, end_column)` exclusive, retaining intervening LF characters.
`excerpt_sha256` is the lowercase SHA-256 of that excerpt's UTF-8 bytes. Empty,
out-of-range, reversed, or partially specified spans fail validation.

### 6.4 Atomic source-ledger generations

`.kgdistiller/sources/manifest.json` is a bounded
`qlkg-source-ledger-v1` atomic pointer. It contains the current generation
digest plus the relative path, byte count, row count, and SHA-256 of each
canonical JSONL artifact. The generation directory name is the SHA-256 of this
canonical artifact inventory. Generation directories and raw blobs are
immutable.

A writer stages all three JSONL artifacts, flushes them and any new blobs,
validates hashes and referential integrity, installs the immutable generation,
then atomically replaces `manifest.json` last. A reader loads and validates the
manifest before and after loading all artifacts; if the manifest token changes,
it retries a bounded number of times and otherwise fails with a stable stale-
generation error. An incomplete unreferenced generation is never visible and
may be cleaned only by an explicit doctor/repair action.

### 6.5 Incremental workflow

`source capture FILE` resolves the vault, reads one consistent source
generation, writes/reuses the blob, and appends canonical metadata atomically.

For an unchanged raw digest it returns a verified no-op. If raw bytes changed
but normalized UTF-8 text did not, it captures the exact new raw version. It
atomically records a `carried-forward` derivation referencing the predecessor
only when that predecessor resolves to reviewed data; otherwise it archives the
event without a derivation and keeps it unreviewed. Because normalized
line/column coordinates and excerpt hashes are identical, a carry row inherits
the predecessor's reviewed concept, relation, and evidence mapping without
asking for semantic re-distillation. For a changed normalized
digest it returns a bounded line diff against the predecessor and the concept
IDs resolved through the predecessor's effective derivation. Resolution follows
`inherited_from_version_id` through zero or more `carried-forward` rows until a
`committed` or `reviewed-empty` row is reached. Every hop must stay within one
document, point strictly to a lower sequence, preserve the same normalized
digest, and form an acyclic bounded chain; otherwise the ledger is invalid and
capture fails closed. Those resolved concepts become the initial review set;
the Agent may add candidates discovered in changed text.

A document is fresh only when its current version has a `committed`,
`reviewed-empty`, or valid `carried-forward` derivation whose normalized digest
matches that version. Capture alone never marks semantically changed text as
reviewed.

Removed source text never deletes a concept or edge automatically. Affected
knowledge is retained as `needs-review` until a reviewed transaction updates,
supersedes, or explicitly removes it.

## 7. Vault resolution and command surface

New commands:

```text
kgdistiller vault init PATH --id ID --label LABEL
kgdistiller vault add PATH
kgdistiller vault remove ID
kgdistiller vault list
kgdistiller vault locate FILE
kgdistiller vault doctor [ID]

kgdistiller source capture FILE
kgdistiller source status FILE
kgdistiller source diff FILE [--from VERSION] [--to VERSION]

kgdistiller knowledge sync [--vault ID]
kgdistiller knowledge check [--vault ID]
kgdistiller knowledge ingest plan REQUEST --output PLAN
kgdistiller knowledge ingest apply REQUEST --receipt RECEIPT

kgdistiller recall status [--vault ID]
kgdistiller recall resolve NAME...
kgdistiller recall search QUESTION [--vault ID]
kgdistiller recall get VAULT_ID:CONCEPT_ID
kgdistiller recall expand VAULT_ID:CONCEPT_ID
kgdistiller recall context QUESTION
```

Legacy single-project commands remain available during this release, but new
Skills use only the vault/source/knowledge/recall surface.

The native ingest request carries a vault ID and expected registry generation;
the command resolves the current vault through the machine-local registry and
does not accept or require a repository-root argument. Plan and apply both fail
closed if the registry generation, registered path, vault manifest, source
ledger, live source, concept notes, or graph generation changed.

`vault locate` resolves a canonical absolute file against registered roots and
requires exactly one owner. Unregistered, missing, overlapping, symlink-escaped,
or excluded files return stable structured errors. There is no silent current-
directory fallback for the new surface.

## 8. Federated retrieval

Each result uses the stable handle `vault_id:node_id`; node IDs are unique only
inside a vault. Cross-vault exact mappings remain explicit alignments, never
implicit merges.

The read-only retrieval sequence is:

1. enumerate healthy registered vaults and their bounded root cards;
2. resolve exact names and reviewed aliases in every selected vault;
3. select zero or more fields/topics, then descend the `contains` navigation DAG;
4. execute scoped lexical retrieval inside the selected frontier;
5. expand trusted semantic edges from established seeds;
6. fuse deterministic lane evidence;
7. let the query Skill rerank only the bounded candidate cards;
8. fetch and pack full evidence only for the final selected handles.

The two-level navigation structure is a DAG, not a forced tree. A deterministic
tree view may be derived for display, while a topic may retain multiple field
parents and a knowledge node may retain multiple field or topic parents.

The engine exposes `roots`, `children`, and scope-aware `search` operations so
an Agent can inspect one frontier at a time. Lexical lookup must not rescan every
entry body for every federated request. Build deterministic per-generation
lexicon/navigation shards or equivalent in-memory indexes. A long-lived local
server may cache an immutable `GraphView` per vault generation and invalidate it
when the manifest token changes; independent CLI requests remain generation-
checked.

A missing or invalid registered vault is reported in `incomplete_vaults`; it is
never silently omitted from a federated answer.

## 9. Backend API

The backend remains loopback-only by default and exposes versioned read APIs:

```text
GET  /api/v1/status
GET  /api/v1/vaults
GET  /api/v1/vaults/{vault}/roots
GET  /api/v1/vaults/{vault}/nodes/{node}
GET  /api/v1/vaults/{vault}/nodes/{node}/neighbors
GET  /api/v1/vaults/{vault}/stale
GET  /api/v1/vaults/{vault}/sources/{document}
GET  /api/v1/vaults/{vault}/sources/{document}/versions
GET  /api/v1/vaults/{vault}/sources/{document}/diff
GET  /api/v1/vaults/{vault}/sources/{document}/excerpt
POST /api/v1/search
POST /api/v1/context
```

Every response is bounded and carries vault/graph/source generation digests.
Responses use explicit closed web DTO schemas rather than exposing arbitrary
internal graph `properties`. Node summaries, node details, and search results
are different shapes. The server never returns machine-local absolute vault
paths.

Requests that depend on a loaded graph carry an opaque generation token. A
generation mismatch returns a versioned `409` error with a stable code and the
current token so the client can reload rather than mix generations.

The server rejects traversal, forged Host/Origin, unregistered vaults, stale
source generations, oversized request bodies, and unknown fields. The first
usable release is read-only over HTTP; writes continue through transactional
CLI/Skills.

## 10. Frontend

Frontend source lives in a separate top-level `frontend/` Vite + TypeScript
project with its own tests and build. The first release uses native TypeScript,
CSS tokens, and bounded SVG graph rendering; it does not add React, a UI kit, a
graph-layout dependency, or a client-side diff engine without a demonstrated
need. The Python package ships only compiled static output plus its asset
manifest and the versioned API server. Development may use a separate frontend
dev server; the installed product remains one local `kgdistiller serve`
command with no CDN or network dependency.

Required views:

- vault selector plus missing/stale health status;
- federated search with visible identity/taxonomy/lexical/graph lane reasons;
- typed graph with vault, field, topic, relation, and curation filters;
- concept detail with aliases, typed incoming/outgoing relations, provenance,
  and an Obsidian/open-file action;
- source history and predecessor diff;
- `stale`/`needs-review` queue;
- responsive light/dark layout and keyboard-accessible navigation.

The frontend never interprets raw graph shards and never invents identity. It
consumes only `/api/v1` contracts.

## 11. Obsidian compatibility

Concept, field, and topic notes are ordinary visible Markdown inside the vault.
Relations are stored as internal links in simple frontmatter properties, so the
native Obsidian graph sees the same endpoints. The built-in graph does not
preserve relation types; kgdistiller's frontend does.

The product does not modify `.obsidian` settings automatically. Documentation
provides a recommended graph filter for `path:"Knowledge"` and optional groups
for `tag:#kgdistiller/concept`, fields, and topics.

The existing projection command remains a downstream browsing export for legacy
authority projects and external vaults. It is not used by the new native-vault
workflow and is never ingested back.

## 12. Skill workflow

The maintained product Skills change as follows:

- curation receives one or more source paths, calls `vault locate`, captures the
  version, reads the predecessor diff and derivation ledger, then prepares
  concept-note changes;
- query starts with federated `recall status`, chooses vault/taxonomy frontiers,
  and returns stable vault-qualified handles;
- ingest is still the only mutating Skill and applies concept notes, derivation
  evidence rows, and the deterministic graph generation in one
  stale-safe transaction;
- deploy initializes/registers/moves/verifies vaults and serves the multi-vault
  frontend.

Skills never ask for a folder that can be resolved from the input path or the
vault registry. They ask only when a source is unregistered, multiple ownership
would be ambiguous, or a semantic decision requires human review.

All updated Skills must keep the language-alignment rule and pass the active
Skill validator plus `kgdistiller codex doctor`.

## 13. Transactions and concurrency

The existing plan/apply/idempotency/stale-precondition model is retained, but
the native-vault path uses a new `qlkg-vault-ingest-request-v1` and matching
receipt. It does not change the meaning of `qlkg-ingest-request-v2`, whose
marker-patch contract remains legacy-only.
One vault has one writer lock. A transaction that changes knowledge installs:

- concept note files;
- document/version/derivation ledger rows;
- identity/alignment registries when reviewed;
- one deterministic graph generation;
- a canonical receipt.

Planning changes no live bytes. Apply rechecks the vault registration, graph
generation, source version, live source hash, concept-note hashes, and query
report. Any pre-install failure writes nothing. Any install failure restores all
targets. Queries see either the complete old generation or complete new
generation.

Managed parent directories are byte-free scaffolding rather than transaction
content targets. Apply creates each missing parent with anchored no-clobber
semantics and durably records actual ownership before publishing a file. Normal
rollback removes only recorded directories whose stable filesystem identity is
unchanged and that are still empty. A hard crash
between a successful directory creation and that ownership record may leave
only empty scaffolding; recovery conservatively retains it and removes every
file temporary, journal, receipt, source pointer, graph artifact, and authority
file from the failed transaction. Later apply may safely reuse the empty
scaffolding. Recovery never deletes a non-empty, replaced, or third-party-won
directory.

The minimal F4 contract rejects every non-empty alignment mutation. Its
canonical portable receipts live at
`.kgdistiller/receipts/sha256/aa/FULL_SHA256.json`. A receipt is finalized before
installation so derivation rows can bind its digest; it therefore does not
contain the post-commit source-ledger generation. The closed success report,
which is produced after commit, returns that final ledger generation token.

Registry mutation uses its own machine-local lock and never takes a vault writer
lock while held, preventing cross-scope lock inversion.

## 14. Compatibility and migration

`qlkg-v3` remains the exact derived graph schema for this release; this decision
is not deferred to implementation. The native compiler generates
`.kgdistiller/graph/sources.json` as a canonical `qlkg-sources-v3` registry.
That registry inventories only `Knowledge/Concepts`, `Knowledge/Fields`, and
`Knowledge/Topics`; its field/topic definitions are compiled from taxonomy
notes, while concept membership is read from typed concept-note links rather
than inferred from globs.

The `qlkg-v3` manifest's `source_hashes` contains only normalized hashes of the
authoritative concept and taxonomy Markdown files. Captured raw evidence and
ledger JSONL are excluded from that map. Knowledge-node text and entry come from
the concept-note body, taxonomy nodes come from taxonomy notes, semantic edges
come from typed concept properties, and `provenance.authority` remains the
vault-relative authoritative Markdown path. Structured source versions and
evidence spans remain solely in the source ledger and are joined by the
retrieval/API layers; they are not added as ad hoc graph fields.

`registry_sha256`, optional identity-registry behavior, graph artifact layout,
graph digest calculation, and graph-generation loading semantics keep their
current `qlkg-v3` meanings. New surrounding contracts receive new immutable
discriminators without relabeling an old structure. Existing source-marker
projects remain readable through legacy commands and may be adopted explicitly:

1. register the project as a vault;
2. capture its registered authorities as evidence;
3. generate reviewable concept-note candidates from current active nodes;
4. require user review before concept notes become authority;
5. rebuild and verify the derived graph.

The native-vault compiler never falls back to the legacy
`#kn`/`--[[...]]--`/`\kn` scanner inside source-evidence files. Legacy commands
remain explicitly isolated so two authority rules are never mixed in one vault
generation.

There is no automatic relabeling of an old graph, projection, or Agent delta.
If no irreplaceable legacy metadata exists, re-distillation is preferred over a
compatibility layer.

## 15. Feature slices and atomic gates

Each slice is implemented, atomically tested, committed, and pushed before the
next dependent slice begins.

### F0 — specification and branch

- This document, branch creation, and clean-diff validation.
- Gate: `git diff --check` and documentation review.

### F1 — vault registry and routing

- Schemas, safe machine-local registry, vault manifest, CLI commands, ownership
  resolution, doctor.
- Atomic gate: new registry/vault test module plus CLI parser tests only.

### F2 — immutable source archive

- Atomic source-ledger manifest/generations, document/version/derivation
  schemas, capture/status/diff, event versions, blob deduplication,
  newline-only carry-forward, and portable verification.
- Atomic gate: new source archive test module only.

### F3 — native concept notes and graph compilation

- Frontmatter parser/renderer, concept and taxonomy note contracts, stable IDs,
  typed relation compilation, ledger evidence validation, deterministic
  `qlkg-v3` rebuild, and native Obsidian links.
- Atomic gate: concept-note/compiler tests plus the exact existing core tests
  touched by scanner behavior.

### F4 — vault transaction integration

- Plan/apply support for concept notes, ledgers, evidence, graph, rollback and
  idempotency.
- Atomic gate: new vault-ingest tests plus exact affected ingest tests.

### F5 — federated recall and read-only MCP

- Vault-qualified handles, health reporting, roots/children/scoped search,
  federated fusion/context, incomplete-vault evidence, MCP tools.
- Atomic gate: new federation tests plus exact affected query/retrieval/MCP
  tests.

### F6 — portable vault store v3

- Snapshot and verification include the portable vault manifest, authoritative
  concept notes, document/version/derivation ledgers, every referenced raw blob,
  and the derived graph. Existing `qlkg-store-v2` meaning is unchanged.
- Atomic gate: new portable-vault-store tests only.

### F7 — versioned backend API

- `/api/v1` read routes, generation cache, source history/diff, security and
  bounds.
- Atomic gate: new API test module plus exact affected web security tests.

### F8 — separate frontend

- Frontend project, tests, production build, packaging, multi-vault UX.
- Atomic gate: frontend unit/component tests and frontend production build only.

### F9 — Skills, docs, distribution, migration path

- Product Skill rewrites, workflow manifest, README/deployment/Obsidian guide,
  packaging checks and one disposable end-to-end vault smoke script.
- Atomic gate: Skill validator, product doctor tests, distribution-content test,
  and the new smoke test only.

## 16. Independent final regression

After all workers finish and every slice is pushed, the coordinator starts from
the integrated branch and independently runs:

```sh
uv run python -m unittest discover -s tests -v
uv build --out-dir build/release/multivault-v1
uv run python scripts/check_distribution.py \
  --dist-root build/release/multivault-v1
```

It also installs the wheel in an isolated environment and exercises this real
user path on disposable vaults:

1. initialize and register two vaults;
2. locate sources without passing repository roots;
3. capture a source and ingest several concept notes from it;
4. open/read the generated Obsidian-native notes;
5. modify the source and verify predecessor diff plus affected concepts;
6. run federated recall and bounded context across both vaults;
7. start the loopback server, exercise every `/api/v1` route, and load the built
   frontend;
8. move one vault, repair its registry path, verify, and query again;
9. confirm a missing vault is reported rather than silently omitted;
10. confirm the product worktree contains no personal knowledge, credentials,
    generated vault, or untracked release data.

Regression evidence must include exact commands, counts, skipped tests, platform
limitations, commit SHA, remote branch, and a clean final `git status`.
