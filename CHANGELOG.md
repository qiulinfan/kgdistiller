# Changelog

All notable changes are documented here. Beginning with 0.4, published
`kgdistiller-*` schema names are immutable; incompatible data-contract changes
require incrementing the affected contract version.

## 0.4.0 — unreleased

- Make sources format-agnostic: a knowledge source is any registered UTF-8 text
  document, read as numbered lines and never parsed. Remove the Markdown, Typst
  and LaTeX marker scanners (`--[[X]]--`, `[[X]]`, `#kn`, `\kn{}` and their
  reference forms), TeX masking, reference scanning (`references.jsonl`),
  Typst/TeX sibling pairing and the rule that source markers define nodes.
  Markers survive only as a frontend convention of the repositories that
  publish notes.
- Make reviewed entries the node store. Each `.knowledge/entries/<id>.md`
  (`kgdistiller-entry-v1`) carries `schema`, `id`, `label`, `kind`, `aliases`,
  `source`, `line_start`, `line_end` and `understanding` as plain Obsidian
  properties, the human sections, and an Evidence section quoting the cited
  source lines verbatim. Unknown keys and sections are rejected. Ids are
  readable slugs of the label (an explicit id when the label has none), never
  hash-derived; the `_kgd-` fallback filename is gone.
- Keep accepted semantic edges in one file, `.knowledge/edges.jsonl`, with
  exactly `source`, `relation`, `target`, `origin`, `confidence` and `evidence`
  and the five relations `prerequisite-for`, `implies`, `generalizes`,
  `contrasts-with` and `derived-from`. Remove `kgdistiller-graph-v2`,
  `graph/nodes.jsonl`, `graph/references.jsonl`, `graph/manifest.json`,
  `identities.json` (aliases now live in entries), `alignments.json`, edge
  `evidence_fingerprints`/`stale_endpoints` and every curation status.
- Add `kgdistiller check [--fix-lines]`: it validates entries and edges
  (schema, unique ids, labels and aliases, registered and readable sources,
  line ranges, document-type kinds, edge endpoints, acyclic
  `prerequisite-for`) and reports an entry as moved, stale or ambiguous by
  searching its whitespace-normalized Evidence quote in the source.
  `--fix-lines` rewrites only moved line ranges, under the writer lock.
- Report staleness without gating it: remove every needs-review, orphan and
  curation gate from query, retrieval, graph traversal, MCP and the Obsidian
  feed, together with `--include-stale`/`--include-orphaned`. The graph edge
  policy is `high-confidence|all`.
- `scan --file` returns the file's registered source, document type, profile
  (`node_kinds`, `extraction_guidance`) and numbered lines; `init` writes
  `sources.json`, `vault.json`, an empty `entries/` and `edges.jsonl` and
  scans nothing.
- Rewrite transactional ingest on the entry store. A
  `kgdistiller-ingest-request-v1` carries one `kgdistiller-agent-delta-v1`
  (`create_entries`, `update_entries` with `expected_label`, `remove_entries`,
  `add_edges`, `remove_edges`). Apply holds the writer lock, re-validates the
  delta semantically against the current store and source text
  (`stale-evidence`, `label-mismatch`, `identity-collision`, `dangling-edge`
  and other stable codes), installs entries and edges atomically through a
  journal and writes a readable `kgdistiller-ingest-receipt-v1` keyed by
  `request_id`. Replays are detected by comparing the stored request text.
  Source documents are never edited: authority patches and marker
  expectations are gone.
- Capture cites `source`, `line_start` and `line_end`, copies the cited lines
  verbatim as Evidence, checks the kind against the source's document type and
  the names against every entry, keeps an old label as an alias on rename, and
  names requests `capture-<id>-<n>`. Harvest detects changes by comparing the
  draft, entry and cited source text, ingests each run as
  `harvest-<review>-<run>` and drops the definition-sheet projection marker.
- `GraphView`, CLI and MCP read the entries and `edges.jsonl` directly; node
  records are entry records plus their `entry` path, and loading refuses a
  store while an ingest journal exists. `kgdistiller-query-status-v1` reports
  entry, edge and relation counts.
- Match CJK text lexically: the shared tokenizer (`kgdistiller.tokens`) emits
  CJK unigrams and bigrams, so `测度` finds `测度论`. BM25 and the embedding
  projection index aliases, kind, the human sections and the Evidence quote.
- Keep the optional local embedding/reranker lane (`kgdistiller[retrieval]`,
  pinned BGE-M3 models) with one rebuildable vector cache file per model, keyed
  by entry id and storing the embedded text; an entry is re-embedded when its
  text differs.
- Remove every SHA-256, digest and fingerprint mechanism: entry
  `kgd_source_sha256`/`kgd_definition_sha256`, node, edge and manifest hashes,
  `graph_sha256`, `snapshot_sha256`, registry and identity digests,
  `request_sha256`, `base_graph_sha256`, plan, receipt, retrieval and context
  digests, `bundle_sha256` and the plugin's digest check, compiled-library and
  OMP tool hashes, hash-keyed vector caches, installer link-state digests and
  the release attestation step.
- Remove commands and features without replacement: `sync`, `build`, `apply`,
  `search`, `show`, `stats`, `audit`, `snapshot`, `curate-check`, `reconcile`,
  `derive locate|install` with derived Markdown and the derived cache,
  `store snapshot|verify` with `kgdistiller-store-v1`, `documents.jsonl` and
  `store.json`, `candidate build|validate`, `agent align|compare|propose`,
  namespaces such as `paper:<digest>`, the raw source-evidence lane (`agent
  evidence`, `agent evidence-resolve`), support selection, the compact context
  projection (`kgdistiller-context-bundle-v3`), the `distill-paper` Skill, the
  stress harness and the global `--graph`, `--identities` and `--alignments`
  options.
- Port the full product integration to Claude Code: transactional
  `kgdistiller claude link` / `kgdistiller claude doctor` driven by
  `workflows/claude-manifest.json` install the Skills, the Claude Code agent
  presets from `.claude/agents/*.md`, and the canonical product root below the
  Claude Code home, with `kgdistiller-claude-links-v1` state and adoption of
  symlinks previously created by the skills-only shortcut scripts. Installed
  copies are compared byte for byte with the product; link-state records are
  `{kind, mode, name, source, target}`. Installed copies are product-owned:
  `doctor` reports a differing copy, and relinking replaces it or removes a
  retired one, discarding local edits to installed files.
- Add a cross-platform installed `kgdistiller`/`kgd` command with a strict
  machine-local vault registry at `~/.kgdistiller/vaults.json`. Commands can
  select a registered vault by name or stable UUID from any working directory,
  while portable identity lives in tracked `.knowledge/vault.json`.
- Make the hidden `.knowledge/` tree the only knowledge root; `kgdistiller init`
  creates it and every CLI default resolves below it.
- Establish the `kgdistiller-*` schema namespace with independent v1 contracts.
  Pre-0.4 schema aliases and readers are not retained.
- Replace the disposable SQLite Agent index with an in-memory `GraphView` used
  by CLI and MCP.
- Add deterministic `kgdistiller-retrieval-plan-v1`,
  `kgdistiller-search-result-v1..v3`, `kgdistiller-search-execution-v1..v3`
  and `kgdistiller-context-bundle-v1..v2` contracts for identity, lexical,
  optional embedding and bounded graph retrieval.
- Remove every publishing surface: `serve` and its static app, `publish`,
  `export site`, `export latex` and `export latex-registry`, the Typst
  knowledge registry and label rendering, the concept-note projection copies of
  `export obsidian`, and the course, field and topic taxonomy with its
  `contains` relation. Each `sources.json` source holds only `id`, `root`,
  `files` and an optional `document_type`; other keys are rejected.
- Add a read-only Obsidian plugin and its `kgdistiller-obsidian-graph-v1` feed,
  which `kgdistiller export obsidian` writes atomically to
  `.knowledge/build/obsidian/semantic-graph.json` from every entry and accepted
  edge: concepts with kind, aliases, entry path and understanding, the cited
  source documents, semantic edges and definition line ranges. The feed is never
  registered or rescanned.
- Package that plugin in the Python distribution and add cross-platform
  `kgdistiller obsidian install`, with atomic replacement, settings
  preservation, and registered-vault selection from any working directory.
- Remove superseded 0.3 database/vector design specifications; Git history is
  their archive.
- Require Python 3.11 or newer.
- Pin ruff 0.16.10 in the `dev` dependency group and run
  `ruff check src tests scripts` in CI.
- Release Obsidian plugin 0.1.5, versioned independently of the Python core
  (release notes in `.github/obsidian-release-notes.md`): hidden-folder
  indexing fixed to `.knowledge` with an exclusion list (default `build`); the
  default graph path `.knowledge/build/obsidian/semantic-graph.json`, loaded
  through the vault adapter and re-checked on focus or leaf change; a contract
  for the entries-and-edges feed with any safe relative source path and no
  digest; no reference layer; nodes styled by understanding with Kind and
  Understanding details; Open buttons for an entry's own file and a source at
  its cited lines; and type-checked stored settings.

## 0.3.0

- Added the first portable store, transactional ingest, machine-local query
  index, bounded hybrid retrieval, and multi-platform release coverage.
- Added explicit embedding policy/provider experiments and versioned retrieval
  execution contracts. These derived runtime paths are removed in 0.4.0.

## 0.2.1

- Separated provider-neutral query and ingestion Skills.
- Added conservative identity resolution, graph comparison, and reviewed
  alignment handling.

## 0.2.0

- Added the self-contained Agent snapshot, bounded graph retrieval, context
  bundles, and read-only MCP tools.

## 0.1.0

- Initial standalone Markdown, Typst, and LaTeX graph compiler and local
  browser.
