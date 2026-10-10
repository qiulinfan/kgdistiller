# Changelog

All notable changes are documented here.

## 0.4.0 — unreleased

### Knowledge model and storage

- Store knowledge as records. Each accepted record is
  `<base>/.knowledge/entries/<id>.md`: YAML frontmatter with the fixed keys
  `label`, `kind`, `source`, `lines`, `aliases`, `understanding`, `epistemic`
  and `requires` (Obsidian's `tags` and `cssclasses` accepted and ignored),
  every other key a role list declared for the kind, and a Markdown body of
  prose, an optional `## Search terms` section and a final `## Evidence`
  section of verbatim source quotes. The id is the file stem; there is no `id`
  or `schema` field. A record with a non-empty role list is a relation, any
  other record a node; relations have unbounded arity, may repeat a participant
  and may have relations as participants. Values are quoted links (`[[id]]`,
  `[[base:id]]`, `[[.knowledge/entries/id]]`) or plain pending terms. The
  frontmatter is read with PyYAML `BaseLoader` and never re-serialized. The
  format is specified in `docs/model.md`.
- Add drafts and sheets. `.knowledge/drafts/<id>.md` holds proposed new records
  in the same format; `.knowledge/sheets/<source path>.md` is the generated
  def/pending view of one source, whose only read-back state is the draft
  checkboxes.
- Add the write path: `kgd accept DRAFT... [--dry-run]` validates the selected
  drafts together under the home lock and moves them into `entries/` with
  `os.link` then `os.unlink`, never overwriting, refusing with
  `select [[x]] too` when a draft links an unselected draft, and reporting
  `understanding_set`; `kgd harvest SHEET [--dry-run]` accepts the ticked
  drafts of a sheet and regenerates it; `kgd sheet SOURCE [--json]` writes a
  sheet, keeping ticks, or prints the source's extraction profile and
  inventory. Accepted records are edited in place with stale-read-safe tools.
- Replace `check` with the JSON report `{errors: [{path, rule, message}],
  stale, moved}` over the home, records and drafts: config and type shape,
  glob conflicts, ids, frontmatter, registered sources and line ranges, kinds
  and roles from the document type, link grammar and resolution (including
  draft rules and foreign bases), body grammar, and evidence freshness by
  whitespace-normalized quote search. `--fix-lines` rewrites only the `lines:`
  line of moved records under the home lock and reports `fixed` and `skipped`.
- Add the derived database `$KGDISTILLER_HOME/index.sqlite` (SQLite with FTS5,
  tables `meta`, `record`, `link`, `name`, `fts`, and little-endian float32
  L2-normalized vectors as BLOBs) and its only writer
  `kgd index [--rebuild] [--no-embed]`. The lexical phase is one transaction
  with stat-based change detection, upserts that keep rowids, unparseable
  files keeping no row and recomputed unified text so label changes cascade. A
  changed `embedding` model id resets every vector; vectors of deleted and
  changed rows go into a pool keyed by text, so a row whose new text is in the
  pool (an id rename) keeps its vector. The embedding phase then loads the
  model only when some vector is NULL, encodes those rows in batches of 64 and
  writes each vector with an UPDATE guarded by the row's text and the model, so
  a concurrent change makes the write a no-op; texts over the model's input
  limit are reported under `truncated`. `--rebuild` re-derives every row in
  place, re-using vectors by text, and never swaps the database file;
  `--no-embed` skips the embedding phase. An incremental run equals a rebuild
  and a build from a deleted database, vectors included. A missing, damaged or
  other-version database is recreated, so restore is one command. Every read
  reports `lag`, including unembedded rows and a changed model.
- Add `kgd search` (a lexical FTS5 lane, a dense lane and a name lane over
  labels and aliases, fused by reciprocal rank fusion, with `--base`, `--kind`,
  `--class`, `--source` and `--understanding` filters), `kgd resolve` (senses,
  mentions and pending uses of terms) and `kgd get [--source-lines N]`
  (complete records with out- and in-links and live cited source text). All
  are read-only and global across bases. The dense lane encodes the query with
  `meta.embedding`, the model that built the stored vectors, and scans the
  filtered vectors with an exact NumPy dot product; it runs when
  `meta.embedding` is set (an embedding model built the index), `--no-dense`
  skips it, and when stored vectors match the filters but the `retrieval`
  extra is missing or the model cannot be loaded, search fails with a
  `--no-dense` hint.
- Add `kgd neighbors` (closures over links with one recursive CTE: the role
  restriction applies at every hop, `--dir out|in|both`, cycles end at the
  depth limit and records keep their minimum depth; dependency and claim
  closure, applications by kind), `kgd browse` (bases with counts, source
  directories, a source's records by kind with its pending terms, and kind
  listings with all participants at any scope) and `kgd pack` (whole records
  within a UTF-8 byte budget, a breadth-first `requires` closure, shared
  relations and typed gaps). The filters apply to all of them.
- Rewrite `kgd mcp` as a read-only server over the whole home with exactly
  `kg_search`, `kg_resolve`, `kg_get`, `kg_neighbors`, `kg_browse` and
  `kg_pack`, inline input schemas and a fresh read-only connection per call.
  It takes no arguments. The embedding model loads lazily on the first dense
  search and stays resident until `meta.embedding` changes; `kg_search` takes
  `no_dense`, and the other tools load no model.
- Move the tokenizer into `kgdistiller.index` (`tokens`, with CJK unigrams and
  bigrams, and `name_key`).
- Extend `kgd base list` with record and draft counts, the indexed row count
  and per-base lag, and `kgd base rm` with the `[[name:…]]` links left
  dangling in other bases.
- Remove the previous storage and its tooling: the `kgdistiller-entry-v1`
  entry format (`schema`, `id`, `line_start`, `line_end`, fixed human
  sections), `.knowledge/edges.jsonl` and its five fixed relations,
  transactional ingest (`ingest plan|apply`, requests, plans, receipts,
  journals), `capture prepare`, `harvest prepare|apply` and its review
  manifests, the in-memory `GraphView` with BM25, embedding, reranker and graph
  retrieval lanes, retrieval plans, search executions and context bundles, the
  per-model vector cache, `export obsidian` and its graph feed, the `agent`,
  `scan`, `capture`, `ingest` and `export` command groups, `kg_compiled_knowledge`
  and every other previous MCP tool, `mcp --base` and the model flags, and
  every `kgdistiller-*` JSON Schema with its validator. Output shapes are
  documented in `docs/model.md` and `docs/retrieval.md` and asserted by tests.

### Home and sources

- Add a cross-platform installed `kgdistiller`/`kgd` command and one global
  home, `$KGDISTILLER_HOME` (default `~/.knowledge`; the only environment
  variable for kgdistiller's own home and data, absolute after `~` expansion;
  the runtime linkers still honor `CODEX_HOME` and `CLAUDE_CONFIG_DIR`). Its
  `config.json` (`{"bases": {name: {"path", "sources"}}, "embedding"}`) is
  written atomically; `types/<name>.md` are user document types whose
  frontmatter (`node_kinds`, optional `relation_kinds` with ordered roles and
  `epistemic`) is read with PyYAML `BaseLoader` and whose body is the guidance.
  A base's `sources` map globs relative to its root to types with Python glob
  semantics: `*` stays in one segment, `**` spans directories, hidden files and
  directories never match, and a file matched by globs of two types is an
  error. `kgd base add PATH [--name N]`, `base rm NAME` and `base list` manage
  bases; `base add` creates the home with its `.gitignore` on first use and
  creates `<root>/.knowledge/entries/`, and stores paths under the user's home
  as `~/…`. A path argument belongs to the registered root containing its real
  path, with no upward walk and no default. No base root may contain another,
  and the home may not lie inside a base root. A dedicated
  `$KGDISTILLER_HOME/lock`, held by `accept`, `harvest`, `check --fix-lines`
  and `base add|rm`, serializes writers. The `~/.kgdistiller/vaults.json`
  registry, `.knowledge/vault.json` and the per-base `sources.json` are gone.
- Add `pyyaml>=6` as the single runtime dependency.
- Make the hidden `.knowledge/` tree the only knowledge root.
- Make sources format-agnostic: a knowledge source is any registered UTF-8
  text document, read as numbered lines and never parsed. Remove the Markdown,
  Typst and LaTeX marker scanners (`--[[X]]--`, `[[X]]`, `#kn`, `\kn{}` and
  their reference forms), TeX masking, reference scanning, Typst/TeX sibling
  pairing and the rule that source markers define nodes. Markers survive only
  as a frontend convention of the repositories that publish notes.
- Remove every SHA-256, digest and fingerprint mechanism from records,
  storage, outputs, the plugin, the installer link state and the release
  workflow. Ids are readable slugs, never hash-derived; evidence freshness
  compares text, and the index compares file stat and stored text. The
  sentence-transformers adapter is reduced to an encoder that takes a model id
  only; its default model constants, revision pins and the reranker are
  removed.
- Remove every publishing surface: `serve` and its static app, `publish`,
  `export site`, `export latex` and `export latex-registry`, the Typst
  knowledge registry and label rendering, the concept-note projection copies,
  and the course, field and topic taxonomy.
- Remove the rest of the 0.3 surface: `sync`, `build`, `apply`, `show`,
  `stats`, `audit`, `snapshot`, `curate-check`, `reconcile`,
  `derive locate|install` with derived Markdown, `store snapshot|verify`,
  `candidate build|validate`, `agent align|compare|propose`, namespaces such
  as `paper:<digest>`, identity and alignment registries, the raw
  source-evidence lane, support selection, the global `--repo-root`, `--vault`,
  `--kgdistiller-home`, `--registry`, `--graph`, `--identities` and
  `--alignments` options, the `vault` group, `init`, and the
  `KGDISTILLER_VAULT` and `KGDISTILLER_INGEST_*` environment variables.

### Skills and agent integration

- Rewrite the Skills for the record model: `capture-kgdistiller` (one item as a
  draft plus `kgd accept`, or an in-place edit plus `kgd check`),
  `compile-knowledge-sheets` (bounded or whole-source drafts and the sheet;
  it absorbs note curation), `query-kgdistiller` (search, resolve, get,
  neighbors, browse and pack, or the MCP tools) and `deploy-kgdistiller`
  (registration, check, index and restore, plugin, runtime links). Every Skill
  that writes knowledge finishes with `kgd index`. Capture and compile ship identical copies of
  `references/record-format.md`.
- Rename `harvest-paper` to `harvest-kgdistiller`, a generic Skill with model
  invocation enabled in both runtimes that runs `kgd harvest` then
  `kgd index`.
- Remove the `ingest-kgdistiller`, `curate-kgdistiller-notes`,
  `paper-related-work` and `distill-paper` Skills and the `note-curator`,
  `transaction-reviewer` and `related-work-scout` presets in both runtimes;
  `query-reviewer` is the one shipped preset. Workflow step modes are
  `read-only`, `author` and `write`. The manifests' `workflow_resources` are
  `docs/model.md`, `docs/retrieval.md`, `docs/obsidian.md` and
  `docs/deployment.md`.
- Remove the compiled-library retrieval path, its OMP bridge and the manually
  loaded OMP extension, with their tests and documents; OMP reads knowledge
  through the `kgd mcp` server instead.
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

### Obsidian plugin 0.1.5 (unreleased)

- Version 0.1.5 of the Obsidian plugin, versioned independently of the Python
  core (release notes in `.github/obsidian-release-notes.md`), is desktop only
  (`isDesktopOnly`).
- Build the typed graph live from Obsidian's metadata cache of
  `.knowledge/entries/` and `.knowledge/drafts/`, with link resolution and
  name keys matching `kgd` (casefolding through a table that the Python suite
  checks against `unicodedata`) and no feed, polling or database. Node records
  are nodes filled by kind from a fixed palette over the model's sorted kinds;
  borders show understanding (dashed grey unknown, amber not yet understood,
  green understood); `requires` links are dashed arrows; relations with two
  link values are typed edges or loops; every other relation is a diamond with
  role-labelled edges; drafts have a dashed outline, a translucent fill and a
  `draft` badge; foreign links are grey stubs and missing targets red.
  Pending terms are optional ghost nodes, off by default, one per name key.
- Show the neighbourhood of the active record, source or sheet by default, at
  depth 1 or 2, with a full-graph toggle; filter by kind, class,
  understanding, source prefix and drafts. Lay the graph out with
  cytoscape-fcose.
- Add a details pane with the record's fields, its body and Evidence rendered
  through `MarkdownRenderer`, and buttons that open the record, open the source
  at its first line and open the source's sheet when it exists.
- Remove the feed contract, the graph path, the source and definition layers,
  the focus and leaf re-checks, the mobile code paths, the reference-edge
  layer and the configurable hidden folder path (the whole `.knowledge` folder
  is indexed).
- Package the plugin in the Python distribution with cross-platform
  `kgd obsidian install [--base B]`. It installs into a registered base's vault
  only, replaces an older bundle atomically, enables the plugin and its hidden
  indexing (`hiddenKnowledgeEnabled` in `data.json`) while keeping every other
  setting, and warns when the vault's `newLinkFormat` is `relative`. The
  `--replace` and `--no-enable` flags are removed.

### Project

- Require Python 3.11 or newer.
- Pin ruff 0.16.10 in the `dev` dependency group and run
  `ruff check src tests scripts` in CI.
- Add `numpy>=2` to the `retrieval` extra and to the `dev` dependency group.
- Add cytoscape-fcose 2.2.0 (with cose-base and layout-base) as a runtime
  dependency of the Obsidian plugin bundle, with their full MIT notices in
  `THIRD_PARTY_NOTICES.md` and the bundle.
- Run the plugin's notice-asserting build and its vitest suite in CI on Linux,
  macOS and Windows.
- Rewrite the documentation around `docs/model.md`, `docs/retrieval.md`,
  `docs/obsidian.md`, `docs/deployment.md`, `docs/product-workflows.md` and
  `docs/release.md` and remove the superseded design documents; Git history is
  their archive.

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
