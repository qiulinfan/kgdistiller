# Agent guidance for kgdistiller

- Work directly on `main` for the owner's ongoing main feature. Keep completed
  work local unless instructed otherwise; push only when the owner explicitly
  asks to push. Do not create new PRs unless requested.
- Keep the deterministic core provider-neutral. Model-specific behavior belongs
  in Agent skills or adapters.
- The knowledge model, record format, drafts, `check` and the write
  path follow [docs/model.md](docs/model.md); the derived database, `kgd index`,
  lag, `search`/`resolve`/`get`/`neighbors`/`browse`/`pack` and the MCP tools
  follow [docs/retrieval.md](docs/retrieval.md); the Obsidian plugin and
  `kgd obsidian install` follow [docs/obsidian.md](docs/obsidian.md);
  installation, the home, base registration and index restore follow
  [docs/deployment.md](docs/deployment.md), which the `deploy-kgdistiller`
  Skill follows; the Skills, presets and workflow step modes follow
  [docs/product-workflows.md](docs/product-workflows.md); release gates follow
  [docs/release.md](docs/release.md). Change the code and these documents
  together.
- `.knowledge/` is a base's only knowledge root; never create a second tree
  during reads or writes. A base is found only through the roots registered in
  `$KGDISTILLER_HOME/config.json`: a path belongs to the registered root that
  contains its realpath, and `--base NAME` selects a base by name. There is no
  upward walk and no default base.
- The home: `KGDISTILLER_HOME` (default `~/.knowledge`, absolute after `~`
  expansion) is the only environment variable for kgdistiller's own home and
  data; only `codex|claude link|doctor` also honor their runtime's `CODEX_HOME`
  or `CLAUDE_CONFIG_DIR`. It holds `config.json`
  (`{"bases": {name: {"path", "sources"}}, "embedding"}`, written atomically by
  `base add|rm`), `types/<name>.md`, the `.gitignore` written when the home is
  created, the derived database `index.sqlite` (with `-wal`/`-shm`) and the
  dedicated `lock` file. No base root may be an ancestor or descendant of
  another, and the home never lies inside a base root. The home is owner data
  and never enters this repository.
- Storage: a base's knowledge is `.knowledge/entries/<id>.md` (accepted
  records: nodes, and relations whose role keys bind participants) and
  `.knowledge/drafts/<id>.md` (proposed records in the same format: new, or
  under an accepted record's id its replacement). The product reads and writes
  nothing else under `.knowledge/`. Do not reintroduce sheets, pending terms or
  lists, edge files, graph manifests, identity or alignment registries,
  receipts, plan files, snapshots or stores. The database `$KGDISTILLER_HOME/index.sqlite` is derived from
  `config.json`, the `entries/` files and the embedding model; `kgd index` is
  its only writer and readers open it read-only.
- Every admission is a draft the owner accepts (owner, 2026-10-15; docs/model.md
  "Ingestion"). Agents, Skills and the plugin's agent write drafts only, new
  records and replacements alike, and never run `kgd accept` or edit an
  accepted record, except `kgd relocate`, which moves where a record cites
  without changing what it says (owner, 2026-10-15: sources change and
  nothing follows them; the relocate Skill moves a record to a passage the
  owner gives), and the link rewrites a relocation across bases, a merge or a
  split the owner accepted requires. `kgd find` (MCP `kg_find`) finds a text
  in the registered sources, read-only and model-free. `kgd accept` creates a new record with `os.link`
  then `os.unlink`, never overwriting, and moves a replacement over its record
  with `os.replace`, refusing it when the record's file is newer than the
  draft. The home lock is held by `accept`, `relocate`, `check --fix-lines`, `base add|rm` and `embedding set|off`; no data file is ever locked or
  truncated. The product never re-serializes frontmatter; `--fix-lines`
  rewrites only the `lines:` line, and `relocate` only `source:`, `lines:`
  and the Evidence section.
- Embedding follows admission, not edits (owner, 2026-10-15). `kgd accept`
  ends with an index run that embeds only the records it created or replaced.
  After a hand edit of an accepted record the plugin runs
  `kgd index --no-embed`; re-embedding an edited
  record is the owner's manual action (`kgd index`, the plugin's Re-embed),
  and whether it has work is `unembedded`, derived from the stored unified
  text, never from a snapshot or hash.
- Ingestion stays lightweight; the owner's budget for waiting on one capture
  is about 20 seconds. The core stays model-free; model work stays in the
  Skills, the presets and the plugin's agent. The writing Skills work in
  batches: one lookup turn (one `kgd resolve` and one `kgd drafts --name` of
  every name, `kgd search --limit 5` only where needed), one write turn, then
  one `kgd drafts` that lists the drafts with what `kgd accept` would refuse.
  A capture's lookup loads no embedding model (`search --no-dense`); the
  owner's `kgd accept` embeds the admitted records through the shared
  embedding process, 0.15 s when it is warm and about 2 s more when it must
  load. The
  `capture-writer` presets run it in the background on a small model
  at low reasoning effort, since model reasoning, not `kgd`, dominates capture
  time. `kgd index` never invokes a generative model; its only model is the
  embedding encoder.
- A record asserts only what its cited text states: a relation citing a paper
  or note quotes text stating that relation, and a connection no source states
  is reported, never written. Each concept has one record, owned by the source
  that defines or originates it; a source that only uses a concept, even one
  it explains, links its record. Every value is a link (no pending terms); a
  concept nothing records is captured on its own first, from a local source
  or as foreign knowledge.
- A label names one concept across every base (owner, 2026-10-15): no two
  records, and no two drafts, share a label's name key, and a draft shares
  none with an accepted record but the one it replaces (`check` rule
  `label`, also refused by `accept`). A new concept of another meaning whose
  name is taken adds its source's name, `Label (source)` (`label_suffix`: the
  file's stem, or a web page's last path segment); the plugin's capture adds
  it itself. Aliases are not unique.
- Foreign knowledge (owner, 2026-10-15; docs/model.md "Foreign knowledge and
  localization"): a record whose `source` is a web address (the outside work
  that originates the concept, or a page explaining it) is foreign, has no
  `lines`, and quotes that page verbatim. Foreign is derived from `source`,
  never stored. `check` never fetches it and computes no freshness for it. Its
  base is the base of the source being read, and its kind is one a type
  registered for that base declares. Localization moves its evidence to a
  local passage through a draft with the same id.
- `understanding` is `yes` or `no`, absent meaning `yes`; only the owner sets
  `no`.
- Conflicts follow the owner's verbatim statements in docs/model.md
  "Conflicts, updates and relocation". Every draft is preceded by a lookup of
  its knowledge point and of every concept it links, in every base and in the
  drafts. When the knowledge point is already recorded:
  - an unchanged meaning is ignored, and a broken citation moves to the
    admitted passage with `kgd relocate`;
  - a changed meaning becomes a replacement draft keeping the id, reported as
    more general, a correction or a conflict; accepting it is the owner's
    decision.
  Relocation is global, across bases. Knowledge that disappears from its
  source is reported; the agent deletes nothing and its fate is the owner's.
- 如无必要，勿增实体: delete whatever a decision makes unnecessary, outright and
  in the same change, with no fallback or compatibility path (docs/model.md
  principle 11).
- Nodes are concepts and the statements about one record (theorems, findings,
  examples; their roles link what they are about); a relation is a connection
  the source states between two or more records, itself a record. A record's
  class follows its kind's section in the type, never stored; importance is
  connectivity (`degree`), never kind. Role values are links to
  knowledge records, never literals; kgd stores no
  structured measurements. `lines` lists one or more ranges; the Evidence
  quotes are the anchor.
- Rejected drafts leave no trace; do not add a rejection list or any other
  memory of rejections.
- Accepted ids and base names are permanent citation keys: never rename them
  or reuse them for another concept. Ids come from meaning, never from section,
  figure, table, equation or theorem numbers; merges and splits rewrite the
  inbound links once.
- Format agnosticism: a local knowledge source is any registered UTF-8 text
  document.
  Read it as numbered lines; never parse its syntax, scan for markers, pair
  sibling formats, or convert it. `.md`, `.typ`, `.tex`, `.txt` and every other
  extension are treated identically.
- Records come only from reviewed capture or compile through drafts, guided by
  the source's user-registered document type. Never infer identity from
  document order, headings, syntax or keyword co-occurrence; compare meanings
  before treating a record of the same name as the knowledge point.
- Source document types are user-registered extraction profiles, independent
  of file format and knowledge domain: `$KGDISTILLER_HOME/types/<name>.md`, whose
  frontmatter (read with PyYAML `BaseLoader`) has `node_kinds` and optional
  `relation_kinds` (each a mapping of kinds to ordered roles) and `epistemic`, and whose body is the guidance. A base's sources
  are its `sources` glob→type map; each source has exactly one type. Do not
  hardcode the owner's example types. `templates/document-types/` holds
  optional editable starting profiles; they are bundled as data, never
  activated by installation or `base add`. Agents read a source's profile and
  inventory through `kgd source SOURCE` and the text with their own tools.
- Tokens (`tokens`) and name keys (`name_key`) live in `index.py`; every
  tokenizer user imports them from there.
- Hash-free consistency: no content-hash mechanism anywhere in the product
  (no hashing module, no stored or compared checksums), and ids are readable
  slugs, never hash-derived. `check` compares each record's Evidence quotes
  with its cited lines by text and reports moved or stale records;
  `check --fix-lines` rewrites only moved line ranges. The index detects
  changes by file stat. Staleness is reported, never used to hide knowledge
  from retrieval, MCP or the Obsidian sidebar. Vector writes are guarded by the
  row's text and `meta.embedding`, never by hashing; queries are encoded with
  `meta.embedding`; `--rebuild` re-derives in place and never swaps the
  database file.
- Publishing (websites, course registries, marker registries, HTML conversion)
  belongs to consuming repositories; kgdistiller has no publishing surface.
- In a fresh checkout run root `npm run build` before any `uv` command: the
  package and its editable build include the gitignored
  `integrations/obsidian/main.js`, so a plain `git+` install cannot work.
- Implementation changes pass these gates: the complete unit test suite
  (`uv run --locked python -m unittest discover -s tests`),
  `uv run --locked ruff check src tests scripts`, `uv build` with
  `scripts/check_distribution.py`, the installed-wheel
  `scripts/smoke_installed_runtime.py`, and, when the plugin changes,
  `npm run check` in `integrations/obsidian` plus root `npm run build` (which
  asserts the bundled license notices) and root `npm test`. Every test and
  `scripts/smoke_installed_runtime.py` use a temporary `KGDISTILLER_HOME` and
  never read or write the real home. Unit tests use a fake encoder. The int8
  encoder tests (`tests/test_embedding_adapter.py`) need the `retrieval` extra,
  so they run locally and are skipped in CI. The
  real-model smoke test is opt-in and never runs in CI:
  `HF_HUB_OFFLINE=1 KGD_TEST_EMBEDDING_MODEL=<model id> <python with the retrieval extra> -m unittest discover -s tests -p test_real_model.py`.
  Its switch is test-only; `KGDISTILLER_HOME` stays the only product
  environment variable.
- PyYAML>=6 is the single runtime dependency. NumPy, PyTorch, transformers
  and sentencepiece come only with the `retrieval` extra (the dense lane);
  NumPy is also in the `dev` group so the fake-encoder tests run in CI. The
  product ships no default embedding model and no revision pin.
- Embedding (owner, 2026-10-15): a model is an XLM-RoBERTa sentence encoder
  with its sentencepiece tokenizer, loaded with int8 weights (the vocabulary
  table included) and run one text at a time, about 1 GB for `BAAI/bge-m3`;
  `meta.embedding` records the model with its precision (`BAAI/bge-m3 int8`).
  At most one model is in memory per user: every process asks the one
  embedding process (`adapters/embed_service.py`), which holds a lifetime lock,
  only encodes, never opens the database, and exits after 15 idle minutes.
  The model is the home's (`config.json`), never a vault or plugin setting:
  `kgd embedding set|off` checks and writes it, and the plugin's Embedding
  settings call that command, the one kgd run the plugin lets go online.
  Embedding never leaves the machine; no hosted embedding API is used.
- Do not add user knowledge data, credentials, derived databases
  (`index.sqlite`), or model keys to this repository. Retrieval stays generic:
  never hardcode papers, benchmark questions or answers.
- The Community directory entry stays in this monorepo: root manifest/versions
  mirror the canonical `integrations/obsidian` metadata, guarded by
  `scripts/build-obsidian-plugin.mjs`. Root `npm run build` installs/builds that
  integration and copies only ignored root main.js/styles.css; the Python
  bundled installer still consumes the original integration paths.
  Plugin tags have no `v` prefix and match manifest.version exactly; the Python
  core's version is independent and its tags are `core-x.y.z`, so they never
  start the plugin release workflow. Keep the release workflow's tag guard and
  the full Cytoscape.js, cytoscape-fcose, cose-base and layout-base MIT
  notices in the actual bundle. Original project code is MIT; upstream
  licenses remain unchanged.
- Plugin unload leaves workspace layout restoration to Obsidian; never detach the
  sidebar's leaves in `onunload`. Settings headings use `Setting.setHeading` and omit the
  plugin name. The root clean
  builder explicitly installs integration devDependencies, including when the
  caller sets `NODE_ENV=production`; this is required to reproduce release assets.
- Native indexing of the `.knowledge` folder follows
  [docs/obsidian.md](docs/obsidian.md). The plugin setting defaults to off;
  `kgd obsidian install` turns it on in the plugin's `data.json`. The plugin is
  desktop only (`isDesktopOnly`), desktop-capability guarded and scoped to the
  whole `.knowledge` subtree (the folder is the product constant, not a
  setting) with no exclusion list; do not enable competing hidden-folder
  indexers. The plugin has a lookup sidebar and a Capture view, and draws no
  graph of the whole base: the lookup sidebar shows the neighbourhood of the
  record in focus, for a selection (in any view, the plugin's own included), a
  typed query or the file open in the editor, and the Capture view lists the
  drafts waiting for the owner by source (file name, parent folders added only
  when names clash, foreign drafts under "foreign"). It reads
  knowledge and drafts only through a resident `kgd mcp` (`kg_drafts` for
  drafts), off until the vault's settings name a kgd executable,
  covering every base. With the configured executable and
  `KGDISTILLER_HOME` it also runs `kgd base list`, `kgd source SOURCE`,
  `kgd edit`, `kgd accept`, `kgd relocate`, `kgd index`, `kgd index --no-embed`
  and `kgd embedding`
  (`kgd embedding set` fills the Hugging Face model cache), and it writes and
  deletes draft files; the plugin never parses records, and never opens the
  home or the database itself. Capture's ids
  casefold with `integrations/obsidian/src/case-folding.json`, which
  `tests/test_records.py` checks against `unicodedata` (regenerate it when
  that test fails after a Python Unicode upgrade). The plugin's agent (judgment, Capture, Find sources and
  search; see docs/obsidian.md "Agent") is the model-side adapter that keeps
  the core model-free: its harness runs inside the plugin, calls the Chat
  Completions API of the model chosen among the configured providers, at the
  reasoning levels each service kind declares from its vendor's documentation
  (never found by sending the model requests), and Exa's hosted MCP search
  directly (keyless
  unless the owner adds an Exa key), and stores API keys only through
  Obsidian SecretStorage. It depends on no external
  agent runtime, owns its prompts and
  checks every model answer and citation, and writes knowledge only as a
  draft (new, foreign, a replacement, or a localization), under the same
  rules as the capture Skill; the agent never accepts,
  and a draft becomes knowledge when the owner accepts it in the Capture view
  (`kgd accept`, which indexes and embeds it). The plugin refreshes the
  keyword index (`kgd index --no-embed`) whenever a record file is saved. Preserve the complete upstream MIT notice and pinned source
  revision in the bundle.
- Claude Code has the full product integration: the transactional
  `kgdistiller claude link` installer, driven by
  `workflows/claude-manifest.json`, installs Skills, Claude Code agent presets
  (`.claude/agents/*.md`), and the canonical product root, mirroring
  `kgdistiller codex link`. Keep the `skills` and `workflows` sections of the
  two runtime manifests identical; agent presets stay runtime-specific files.
- `scripts/link-skills.sh RUNTIME` / `scripts/link-skills.ps1 -Runtime RUNTIME`
  (`claude`, `codex`, `opencode` or `omp`) are a skills-only development
  shortcut independent of both manifests; they install no agents, workflows,
  or link state, and `kgdistiller claude link` adopts the symlinks they
  create. Full agent/workflow integration is governed
  by the Codex and Claude manifests; shared Skill links do not port agent
  presets.
