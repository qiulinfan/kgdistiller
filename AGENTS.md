# Agent guidance for kgdistiller

- Work directly on `main` for the owner's ongoing main feature. Keep completed
  work local unless instructed otherwise; push only when the owner explicitly
  asks to push. Do not create new PRs unless requested.
- Keep the deterministic core provider-neutral. Model-specific behavior belongs
  in Agent skills or adapters.
- The knowledge model, record format, drafts, sheets, `check` and the write
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
  records: nodes, and relations whose role keys bind participants), plus
  `.knowledge/drafts/<id>.md` (proposed new records in the same format) and
  `.knowledge/sheets/<source path>.md` (generated def/pending sheets whose only
  read-back state is the draft checkboxes). The product reads and writes
  nothing else under `.knowledge/`. Do not reintroduce edge files, graph
  manifests, identity or alignment registries, receipts, plan files, snapshots
  or stores. The database `$KGDISTILLER_HOME/index.sqlite` is derived from
  `config.json`, the `entries/` files and the embedding model; `kgd index` is
  its only writer and readers open it read-only.
- Writes go through drafts plus `kgd accept`/`kgd harvest` (create-only,
  `os.link` then `os.unlink`, never overwriting), or through in-place edits of
  accepted records with a stale-read-safe editing tool followed by
  `kgd check`. The home lock is held by `accept`, `harvest`,
  `check --fix-lines` and `base add|rm`; no data file is ever locked or
  truncated. The product never re-serializes frontmatter; `--fix-lines`
  rewrites only the `lines:` line.
- Every Skill that writes knowledge finishes with `kgd index`.
- Format agnosticism: a knowledge source is any registered UTF-8 text document.
  Read it as numbered lines; never parse its syntax, scan for markers, pair
  sibling formats, or convert it. `.md`, `.typ`, `.tex`, `.txt` and every other
  extension are treated identically.
- Records come only from reviewed capture or compile through drafts, guided by
  the source's user-registered document type. Never infer identity from
  document order, headings, syntax or keyword co-occurrence; the same name is
  not the same concept.
- Source document types are user-registered extraction profiles, independent
  of file format and knowledge domain: `$KGDISTILLER_HOME/types/<name>.md`, whose
  frontmatter (read with PyYAML `BaseLoader`) has `node_kinds` and optional
  `relation_kinds`/`epistemic`, and whose body is the guidance. A base's sources
  are its `sources` glob→type map; each source has exactly one type. Do not
  hardcode the owner's example types. Agents read a source's profile and
  inventory through `kgd sheet SOURCE --json` and the text with their own tools.
- Tokens (`tokens`) and name keys (`name_key`) live in `index.py`; every
  tokenizer user imports them from there.
- Hash-free consistency: no content-hash mechanism anywhere in the product
  (no hashing module, no stored or compared checksums), and ids are readable
  slugs, never hash-derived. `check` compares each record's Evidence quotes
  with its cited lines by text and reports moved or stale records;
  `check --fix-lines` rewrites only moved line ranges. The index detects
  changes by file stat. Staleness is reported, never used to hide knowledge
  from retrieval, MCP or the Obsidian graph. Vector writes are guarded by the
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
  never read or write the real home. Unit tests use a fake encoder. The
  real-model smoke test is opt-in and never runs in CI:
  `HF_HUB_OFFLINE=1 KGD_TEST_EMBEDDING_MODEL=<model id> <python with the retrieval extra> -m unittest discover -s tests -p test_real_model.py`.
  Its switch is test-only; `KGDISTILLER_HOME` stays the only product
  environment variable.
- PyYAML>=6 is the single runtime dependency. NumPy and sentence-transformers
  come only with the `retrieval` extra (the dense lane); NumPy is also in the
  `dev` group so the fake-encoder tests run in CI. The product ships no default
  embedding model and no revision pin.
- Do not add user knowledge data, credentials, generated graphs, or model keys to
  this repository. Retrieval stays generic: never hardcode papers, benchmark
  questions or answers.
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
- Plugin unload leaves workspace layout restoration to Obsidian; never detach graph
  leaves in `onunload`. Settings headings use `Setting.setHeading` and omit the
  plugin name. The root clean
  builder explicitly installs integration devDependencies, including when the
  caller sets `NODE_ENV=production`; this is required to reproduce release assets.
- Native indexing of the `.knowledge` folder follows
  [docs/obsidian.md](docs/obsidian.md). The plugin setting defaults to off;
  `kgd obsidian install` turns it on in the plugin's `data.json`. The plugin is
  desktop only (`isDesktopOnly`), desktop-capability guarded and scoped to the
  whole `.knowledge` subtree (the folder is the product constant, not a
  setting) with no exclusion list; do not enable competing hidden-folder
  indexers. The plugin's model is built live from the metadata-cache
  frontmatter of `entries/` and `drafts/`; the details pane reads the selected
  record's file through the vault only on demand. Its link grammar and name
  key follow `kgd`: casefolding uses `integrations/obsidian/src/case-folding.json`,
  which `tests/test_records.py` checks against `unicodedata` (regenerate it when
  that test fails after a Python Unicode upgrade), and both test suites assert
  `tests/fixtures/link-grammar.json` and `tests/fixtures/name-key.json`. It
  reads no feed and no database. Preserve the complete upstream MIT notice and pinned source
  revision in the bundle.
- Claude Code has the full product integration: the transactional
  `kgdistiller claude link` installer, driven by
  `workflows/claude-manifest.json`, installs Skills, Claude Code agent presets
  (`.claude/agents/*.md`), and the canonical product root, mirroring
  `kgdistiller codex link`. Keep the `skills` and `workflows` sections of the
  two runtime manifests identical; agent presets stay runtime-specific files.
- `scripts/link-claude-skills.sh` / `.ps1` remain a skills-only development
  shortcut independent of both manifests; they install no agents, workflows,
  or receipts, and `kgdistiller claude link` adopts symlinks they created.
  They delegate to the product-owned `scripts/link-skills.sh RUNTIME` /
  `.ps1 -Runtime RUNTIME`; that Skill-only linker also supports native OpenCode
  and OMP homes. Full agent/workflow integration remains governed by the
  Codex and Claude manifests; shared Skill links do not port agent presets.
