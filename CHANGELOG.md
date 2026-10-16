# Changelog

All notable changes are documented here.

## Unreleased

Upgrading from 2.0.0: a record's unified text no longer ends with its
`source`, so the next index run of any kind drops every vector, and a plain
`kgd index` (Re-embed in the sidebar) embeds every record again. A statement
node whose kind declares roles but links no record no longer passes
`kgd check`; link what it is about.
Run `kgd claude link` or `kgd codex link` again for the updated Skills.

- `kgd relocate` finds the passage in the registered sources of every base,
  narrowed by `--base` and `--source` (both repeatable). When the passage lies
  in another base it moves the record, accepted or a draft, there with its
  id, rewrites the record's own links for that base and every role and
  `requires` link to it in every base's records and drafts, and reports
  `moved` and `relinked`; `relocated` is the new uid. It refuses when that
  base already has the id or a replacement draft of the record waits. The
  old path of drafting the record in the other base is gone.
- Drafts link drafts of every base: `kgd check` and `kgd accept` resolve a
  draft's links to drafts of any base, `kgd accept` takes drafts of several
  bases together, and `kgd drafts` reports `with`, `linked_by` and a value's
  `draft` state across bases.
- A result a source states, such as "the sum of two subspaces is a
  subspace", is a theorem node whose roles link every record it concerns,
  not an implication; a relation connects records, such as one result and
  another. A node kind that declares roles links at least one record in
  them (`kgd check` rule `kind`), and a relation binds at least two values.
- `degree` counts only the other records that exist; a link to a missing
  record counts for nothing.
- `pack` adds every record with role links to at least two packed records,
  statement nodes as well as relations.
- A document type that a base's sources name and that does not load makes
  the reads (`search`, `resolve`, `get`, `neighbors`, `browse`, `pack`,
  `drafts`, `source`) fail with an error naming it; `kgd index` still runs.
- `kgd index` carries no vectors over from a database of another schema
  version, and its report no longer has `understanding_changed`.
- The unified text no longer ends with the record's `source`, so relocating
  a record keeps its vector unless its Evidence is replaced.
- Obsidian plugin: **Localize here** and **Localize to selection** work
  across bases, moving the record into the local source's base; the Capture
  card's + finds records and drafts of every base, and capture may link a
  draft of any base. The plugin passes `--` before the values of
  `kgd edit` and `kgd relocate`, so a value starting with `-` stays a value.

## 2.0.0 — 2026-10-16

kgdistiller 2.0.0 gives the knowledge base its settled form. Every way
knowledge enters the base ends in a draft that the owner reviews, edits and
accepts; knowledge from outside the owner's files is recorded as foreign
knowledge instead of pending terms (docs/model.md, "Ingestion" and "Foreign
knowledge and localization"); and the graph has one rule: concepts and the
statements about one of them are nodes, and a relation connects two or more
records. Records and document types of 1.x need the one-time edits below.

Upgrading from 1.1.0: the index schema changed, so the next index run
rebuilds the database and keeps the vector of every record whose text is
unchanged; a plain `kgd index` (Re-embed in the sidebar) embeds the rest.
Rewrite each type's `node_kinds` as a mapping of kinds to roles
(`definition: []`), and move kinds that state something about one record,
such as a theorem or a finding, from `relation_kinds` to `node_kinds` with
their role, as the templates do; a relation with fewer than two values no
longer passes `kgd check`. Records with a plain-text value, or an
`understanding` of `unknown`, `not-yet-understood` or `understood`, no longer
pass `kgd check`; edit them. An absent `understanding`, which meant unknown
in 1.x, now means `yes`. Labels are now unique across every base: records or
drafts that repeat a label fail `kgd check` and `kgd accept`; rename one, or
add its source's name. Run `kgd claude link` or
`kgd codex link` again: the compile Skill is renamed and the harvest Skill is
gone. Delete any `.knowledge/sheets/` folder.

- Every value of a record is a link. Pending terms are gone: a concept no
  record holds is captured on its own first. `kgd resolve` lists senses and
  mentions only, and `neighbors`, `browse` and `pack` report no terms.
- A record whose `source` is a web address is foreign: it quotes the page of
  the outside work that originates or explains a concept, has no `lines`, and
  is accepted, linked and retrieved like any record. Retrieval results carry
  `foreign`, and `browse` lists a base's foreign addresses at its root.
- `understanding` is `yes` or `no`, absent meaning `yes`.
- A draft with an accepted record's id replaces that record when accepted;
  `kgd accept` reports it under `updated` and refuses it when the record
  changed after the draft. `kgd drafts` reports `replaces` and `foreign`.
- Sheets and `kgd harvest` are removed; `kgd source SOURCE` replaces
  `kgd sheet SOURCE --json` and prints the same profile without pending terms.
- The Skills only draft: `capture-kgdistiller` drafts a borrowed concept as
  foreign knowledge and a changed meaning as a replacement, and
  `compile-knowledge-sheets` became `compile-kgdistiller`, which drafts
  replacements instead of editing records. `harvest-kgdistiller` is removed.
- The embedding model is seen and switched in the lookup sidebar, beside
  Re-embed, as the agent's model is: `kgd embedding` reports the runnable
  models in the local Hugging Face cache as `cached`, and a switch is
  confirmed first.
- Drafts are edited on their Capture view card: label, kind, other names,
  epistemic label, understanding and statement in place, and each role's and
  `requires`' links with × to remove and + to link a record or draft found by
  name. Each change runs the new `kgd edit DRAFT FIELD [VALUE...]`, which
  rewrites only that field (and renames a new draft whose label changed);
  `kgd drafts` reports each draft's `choices` of kinds, roles and epistemic
  labels.
- Updating a record is drawing it into a draft: **Revise as draft** in the
  lookup sidebar, or dragging the record onto the Capture view, copies it to
  a draft with its id, which replaces the record when accepted. A draft still
  equal to its record is valid; accepting it only removes it and reports it
  under `updated`.
- A label names one concept across every base: `kgd check` (rule `label`)
  and `kgd accept` refuse a label another record, or for a draft another
  draft, already has, and suggest `Label (source)`, the source's file name or
  web page added. Capture in the plugin adds it to a new concept of another
  meaning by itself.
- Sources change and nothing follows them: `kgd find` (MCP `kg_find`) finds
  a passage or a name in the registered sources, tolerant of line breaks and
  of the markup a reading view hides, and `kgd relocate` moves one record's
  source, lines and, when needed, evidence to the only passage of its base
  that holds a text. The new `relocate-kgdistiller` Skill finds the record
  from the knowledge's name and runs it; the capture and compile Skills
  relocate moved citations the same way.
- Nodes are concepts and statements about one record; a relation is a
  connection between two or more records (docs/model.md, "Research knowledge
  selection"). A type's `node_kinds` maps each kind to its roles, so a
  `theorem: [about]` node links what it is about, and a relation binds at
  least two values. A record's class follows its kind's section in the types
  of its base and is no longer stored: editing a type reclassifies its records
  without re-indexing, and `kgd check` reports a kind two types of one base
  declare differently. The templates merge theorem, proposition, lemma and
  corollary into `theorem` and make `finding` and `example` nodes.
- Importance is connectivity, not kind: search, `get` and `neighbors` report
  each record's `degree`, the number of distinct records it links or is
  linked by, and the lookup sidebar shows it as Connections. `search`
  reports a node's role values as `participants` too.
- A schema change no longer drops the vectors: a rebuilt index takes them
  over by text when the embedding model is unchanged.
- Obsidian plugin: the Capture view lists the drafts by source, each by file
  name (with parent folders when names clash) and every foreign draft under
  "foreign". Capture drafts a borrowed concept as foreign knowledge from the
  pages Find sources finds, and a changed meaning as a replacement. Text
  selected in the lookup sidebar or the Capture view is looked up with the
  hold or command like any text, and can be captured as foreign knowledge.
  **Localize here** on a foreign record, and **Localize to selection** on a
  foreign draft, relocate it with `kgd relocate` to the passage the agent
  confirms in the selected local source.

## 1.1.0 — 2026-10-15

kgdistiller 1.1.0 turns the knowledge base into a usable Obsidian plugin:
look knowledge up while reading, let an agent draft what is new, accept it
in one click, and keep one small local embedding model behind it.

Upgrading from 1.0.1: reinstall with the `retrieval` extra, whose packages
changed, and run `kgd index` once, which re-embeds every record because
vectors now record their precision. Copy-mode agent installations need
`kgd claude link` or `kgd codex link` again. In Obsidian, the lookup sidebar
and the Capture view work once **kgd executable** names the `kgd` command, and
the agent once a model provider is configured.

- Look knowledge up in Obsidian. Select text and hold ⌘ (Ctrl elsewhere) for
  0.3 s, run **Look up selection**, or type into the search box of the
  **kgdistiller Lookup** sidebar (ribbon **Open kgdistiller**, command
  **Search knowledge**). It searches every registered base and shows the
  records of the same name, the records that use it as a pending term, the
  record in focus with a typed graph of its neighbours within three steps and
  a collapsible legend, its prose, Evidence and links, the relations about
  it, the records built on it, its `requires` chain, and the related records.
  It follows the file open in the editor: a record comes into focus, and a
  source or its sheet lists the records the source defines. The plugin reads
  knowledge only through one resident `kgd mcp`, stopped after 15 idle
  minutes, and never parses records; **kgd executable** and **Knowledge
  home** configure it.
- Add an agent to the lookup sidebar that runs inside the plugin and calls
  the owner's model providers directly. It judges whether a selection is
  **Recorded**, **Possibly** or **Not recorded**; answers a typed question
  from the knowledge base first and from arXiv papers and the web for the
  rest, citing records and pages it was given; **Find sources** finds the
  work that introduced a term (from the references the selected lines cite,
  an Exa web search and arXiv) and the Wikipedia article that explains it;
  and **Capture** drafts the selection. **Model providers** settings hold any
  number of providers: fifteen hosted services (DeepSeek, OpenAI, OpenRouter,
  SiliconFlow, Moonshot, Zhipu, Zhipu GLM Coding Plan, Qwen, Doubao, Groq,
  Mistral, xAI, MiniMax, StepFun, Together AI), Ollama, LM Studio and any
  other OpenAI-compatible service, with model detection and a connection
  **Test**. The sidebar chooses the model and a reasoning level among those
  the service documents, or **auto**. DeepSeek and both Zhipu services use the
  thinking switch (reasoning `none` sends `thinking: disabled`). API keys live
  in Obsidian SecretStorage; web search uses Exa's hosted MCP server, keyless
  unless an Exa key is set.
- A model request answered with HTTP 503 or 429 is sent again after 1 s and
  2 s. Failed model and search requests show the service's own explanation,
  such as Zhipu's code 1113 for an exhausted balance, on one line with the
  key masked.
- Capture in Obsidian only drafts; the owner accepts. The **kgdistiller
  Capture** view, below the lookup sidebar, shows captures while they draft,
  several at a time, and every draft in full with whether `kgd accept` would
  take it. **Accept** admits a draft with the drafts it links, **Open draft**
  opens the file beside the source, and **Discard** deletes it after asking;
  **Show drafts** opens the view.
- `kgd drafts` and the MCP tool `kg_drafts` list the drafts from their files,
  with each value's target and exactly what `kgd accept` would refuse, writing
  nothing and taking no lock. `kgd mcp` has seven tools.
- A record is embedded when it is admitted, and an edited one only when the
  owner asks. `kgd accept` and `kgd harvest` end with an index run that
  embeds the records they created and report it under `index`. After an edit,
  Skills run `kgd index --no-embed`, and the Obsidian plugin refreshes the
  keyword index whenever a record file of its vault is saved, renamed or
  deleted. The sidebar's **Re-embed** runs `kgd index`; it is solid with the
  number of records whose text changed since their vector was computed, or
  after a model change.
- At most one embedding model is in memory per user: every `kgd` process and
  every `kgd mcp` asks one shared embedding process, which exits after 15
  idle minutes. The model is an XLM-RoBERTa sentence encoder that ships
  `sentencepiece.bpe.model`, such as `BAAI/bge-m3`, run with int8 weights and
  the sentencepiece tokenizer in about 1 GB, at about 14 records a second on
  Apple silicon; `meta.embedding` records the precision
  (`BAAI/bge-m3 int8`). The `retrieval` extra is PyTorch, transformers,
  sentencepiece and NumPy instead of sentence-transformers.
- `kgd embedding` shows the home's embedding model and its vectors;
  `kgd embedding set MODEL` downloads and checks a model (about 2.3 GB for
  `BAAI/bge-m3`) and writes it under the home lock, and `kgd embedding off`
  turns the dense lane off. Neither re-embeds: the next index run drops the
  old vectors, and a plain `kgd index` embeds every record. The plugin's
  **Embedding** settings run these commands.
- Make the writing Skills lightweight: one lookup turn, one write turn and
  one command. The capture lookup loads no embedding model. The new
  `kgdistiller-capture-writer` agent preset runs captures in the background
  on a small model at low reasoning effort.
- Remove topic navigation: the `organize-kgdistiller` Skill, the
  `navigation` document-type template and type key, `kgd navigate` and its
  `check` rule. Remove the plugin's graph view with its filters and details
  pane, the **Open typed graph** and **Reload typed graph** commands and the
  **Show drafts** setting. Remove `scripts/link-claude-skills.{sh,ps1}` (use
  `scripts/link-skills.sh claude` or `scripts/link-skills.ps1 -Runtime
  claude`), the `scripts/link-{codex,claude}-product.{sh,ps1}` wrappers and the
  manifests' `linkers` key. The installers' error class is `ProductError`; the
  `ClaudeProductError` alias is gone.

## 1.0.1 — 2026-10-11

The Python core and the Obsidian plugin both move to 1.0.1. This release
settles the knowledge model before more knowledge accumulates. Run
`kgd index` once after upgrading: the index schema moves to version 2, so it
recreates the database and re-embeds every record. Copy-mode agent
installations need `kgd claude link` or `kgd codex link` again for the
updated Skills and workflow manifests.

- Bundle editable `math-notes`, `ai-research` and `cs-notes` document-type
  starter templates. The owner selects, edits and registers them; installation
  and base registration activate no default types. Document the research
  knowledge selection policy and source-context reading in both READMEs.
- Add agent-authored topic navigation. The `organize-kgdistiller` Skill
  compiles a multi-parent topic DAG with routing summaries over accepted
  knowledge, as ordinary topic and organization records citing a registered
  agent-authored guide, and splits, merges, reparents or retires topics as the
  corpus grows while preserving scientific records and owner corrections. A
  document type may declare finite `navigation` role conventions; `kgd navigate`
  discovers root topics and projects direct children read-only. Retrieval
  evaluation is specified as deterministic recall of frozen gold record uids.
- Settle the knowledge model's node, assertion and identity rules. Nodes are
  noun-like objects and precisely stated theorems; every other statement that
  can be true or false is a relation. A relation quotes source text that
  states it; a connection no source states is reported, never written. Each
  concept has one record, owned by the source that defines or originates it;
  another source that uses it, even explaining it at length, records a pending
  term, and capture and harvest backfill those pending uses into links once
  the defining record is accepted. Same-named concepts with different
  definitions are different records. Role values are knowledge references
  only, a symmetric kind declares a single role, pending gaps are global across
  bases, rejected drafts leave no trace, and accepted ids and base names are
  permanent. `epistemic` states how the cited passage itself supports the
  record. `kgd check` reports a scientific record that links a navigation
  record. The `ai-research` starter replaces `result` with `theorem` and adds
  `property`.
- Let `lines` list several ascending, disjoint ranges (`lines: 12-18, 40-44`),
  so one record can quote separate passages of its source; each quote lies
  inside one range. `kgd check --fix-lines` writes the merged ranges where the
  quotes now stand, `kgd get --source-lines` widens each cited range, and the
  Obsidian plugin opens the first line of the first range. The index schema
  moves to `user_version = 2` with a `lines` column; the next `kgd index`
  recreates the database and re-embeds every record.
- Handle knowledge conflicts at admission, following the owner's statements
  recorded verbatim in `docs/model.md`. Every admission first looks the
  knowledge point up in every base, pending uses included, and every term is
  looked up before it is written as pending. A knowledge point already
  recorded with the same meaning is ignored, and a broken citation moves to
  the admitted passage. A changed meaning overwrites the record in place,
  keeping its id, only when the new definition is more general or corrects
  the old one; otherwise the admitting agent asks the owner. Relocation moves
  a record to its new source, across bases when needed. Knowledge that
  disappears from its source becomes pending: the record is deleted and links
  to it become pending terms. `compile-knowledge-sheets` therefore edits
  accepted records and runs as a `write` step, the `author` step mode is
  removed, and `harvest-kgdistiller` looks the ticked drafts up again before
  accepting them.

## 1.0.0 — 2026-10-10

The first stable release. The Python core and the Obsidian plugin both move to
1.0.0; the plugin keeps its own release tags (`1.0.0`), the core is tagged
`core-1.0.0`.

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
  limit are reported under `truncated`, and an embedding failure under
  `embedding_error` with exit 1 after the lexical commit. `--rebuild`
  re-derives every row in place, re-using vectors by text, keeps the rows of
  bases whose root is unavailable, and never swaps the database file;
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

### Obsidian plugin 1.0.0

- Version 1.0.0 of the Obsidian plugin, versioned independently of the Python
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
- Read and write UTF-8 on stdin, stdout and stderr whatever the locale or
  Windows code page, so CLI JSON and `kgd mcp` stdio stay UTF-8 on Windows
  pipes.
- Build the bundled Obsidian plugin with root `npm run build` before any `uv`
  command in a checkout; install from a built checkout rather than `git+`.
- Tag Python core releases `core-x.y.z`; bare `x.y.z` tags are plugin
  releases.
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
  execution contracts. These derived runtime paths are removed in 1.0.0.

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
