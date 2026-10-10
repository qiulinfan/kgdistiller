# kgdistiller agent guidance

- Work directly on `main` for the owner's ongoing main feature. Keep completed
  work local unless instructed otherwise; push only when the owner explicitly
  asks to push. Do not create new PRs unless requested.
- Keep the deterministic core provider-neutral. Model-specific behavior belongs
  in Agent skills or adapters.
- Text BM25, optional local embedding/reranker adapters, versioned search results
  and derived vector-cache boundaries follow [docs/retrieval.md](docs/retrieval.md).
- Read-only compiled definitions, sense navigation and complete-entry packing
  follow [docs/compiled-retrieval.md](docs/compiled-retrieval.md). Keep all source
  and retrieval content caller-supplied; do not hardcode papers or benchmark answers.
- The shared personal-research model follows
  [docs/concepts-and-relations.md](docs/concepts-and-relations.md): knowledge
  nodes and typed relations, including applications, live in the hidden
  `.knowledge/` tree.
  Source-scoped def/pending sheets are lightweight metadata link projections for
  papers, notes, blogs and other knowledge files. Preserve complete scientific
  content and existing identities; report unsupported
  adapters instead of claiming partial synchronization is complete. Source sheets
  may be partial; source coverage, definition availability and user understanding
  are independent. Capture only direct dependency gaps and expand another level
  only when the user chooses to study it. New reading normally uses agile
  capture; explicit whole-source distillation is typically for authored notes
  or familiar material. Harvest uses Obsidian def-sheet task selections and
  deterministic ingest; checking a task does not imply understanding.
- `.knowledge/` is a base's only knowledge root; never create a second tree
  during reads or writes. A base is found only through the roots registered in
  `$KGDISTILLER_HOME/config.json`: the registered root containing the cwd, or
  the per-command `--base NAME`. There is no upward walk and no default base.
  Hidden Obsidian indexing still requires its explicit plugin setting.
- The home: `KGDISTILLER_HOME` (default `~/.knowledge`, absolute after `~`
  expansion) is the only environment variable for kgdistiller's own home and
  data; only `codex|claude link|doctor` also honor their runtime's `CODEX_HOME`
  or `CLAUDE_CONFIG_DIR`. It holds
  `config.json` (`{"bases": {name: {"path", "sources"}}, "embedding"}`, written
  atomically by `base add|rm`), `types/<name>.md`, the `.gitignore` written when
  the home is created, and the dedicated `lock` file held by `base add|rm`,
  `check --fix-lines` and ingest/harvest apply. No base root may be an ancestor
  or descendant of another, and the home never lies inside a base root.
  Base-bound commands take a per-command `--base NAME`; there are no global
  path flags. The home is owner data and never enters this repository.
- Storage: a base's knowledge is exactly `.knowledge/entries/<id>.md` (one
  reviewed entry per node, carrying id, label, kind, aliases, source path, line
  range and understanding in frontmatter, the human sections and a verbatim
  Evidence quote) plus `.knowledge/edges.jsonl` (accepted semantic edges with
  exactly `source`, `relation`, `target`, `origin`, `confidence`, `evidence`).
  The rules live in [docs/graph-contract.md](docs/graph-contract.md). Persist
  nothing else as knowledge; `.knowledge/build/` is rebuildable local work.
  Do not reintroduce graph manifests, node or reference files, identity or
  alignment registries, snapshots or stores.
- Format agnosticism: a knowledge source is any registered UTF-8 text document.
  Read it as numbered lines; never parse its syntax, scan for markers, pair
  sibling formats, or convert it. `.md`, `.typ`, `.tex`, `.txt` and every other
  extension are treated identically.
- Nodes come only from reviewed capture or curation through transactional
  ingest, guided by the source's user-registered document type. Never infer
  identity from document order, headings, syntax or keyword co-occurrence, and
  require evidence for semantic relations.
- Source document types are user-registered extraction profiles, independent
  of file format and knowledge domain: `$KGDISTILLER_HOME/types/<name>.md`, whose
  frontmatter (read with PyYAML `BaseLoader`) has `node_kinds` and optional
  `relation_kinds`/`epistemic`, and whose body is the guidance. A base's sources
  are its `sources` glob→type map; each source has exactly one type. Follow
  `docs/concepts-and-relations.md`; do not hardcode the owner's example types.
  Agents read the type profile and numbered lines through
  `scan --file SOURCE --base B`.
- Hash-free consistency: no content-hash mechanism anywhere in the product
  (no hashing module, no stored or compared checksums), and ids are readable
  slugs, never hash-derived. `check` compares each entry's Evidence with its cited lines by
  text and reports moved, stale or ambiguous entries; `check --fix-lines`
  rewrites only moved line ranges. Ingest uses the home lock plus semantic
  re-validation at apply time. Staleness is reported, never used to hide
  knowledge from retrieval, graph traversal, MCP or the Obsidian feed.
- Publishing (websites, course registries, marker registries, HTML conversion)
  belongs to consuming repositories; kgdistiller has no publishing surface.
- Implementation changes pass these gates: the complete unit test suite
  (`uv run --locked python -m unittest discover -s tests`),
  `uv run --locked ruff check src tests scripts`, `uv build` with
  `scripts/check_distribution.py`, and `npm run check` in
  `integrations/obsidian` when the plugin changes. Every test and
  `scripts/smoke_installed_runtime.py` use a temporary `KGDISTILLER_HOME` and
  never read or write the real home.
- PyYAML>=6 is the single runtime dependency; anything else stays optional.
- Do not add user knowledge data, credentials, generated graphs, or model keys to
  this repository.
- The Community directory entry stays in this monorepo: root manifest/versions
  mirror the canonical `integrations/obsidian` metadata, guarded by
  `scripts/build-obsidian-plugin.mjs`. Root `npm run build` installs/builds that
  integration and copies only ignored root main.js/styles.css; the Python
  bundled installer still consumes the original integration paths.
  Plugin tags have no `v` prefix and match manifest.version exactly; the Python
  core's version is independent. Keep the release workflow's tag guard and
  the full Cytoscape MIT notice in the actual bundle. Original project code is
  MIT; upstream licenses remain unchanged.
- Plugin unload leaves workspace layout restoration to Obsidian; never detach graph
  leaves in `onunload`. Settings headings use `Setting.setHeading` and omit the
  plugin name. The root clean
  builder explicitly installs integration devDependencies, including when the
  caller sets `NODE_ENV=production`; this is required to reproduce release assets.
- Optional native indexing of the `.knowledge` folder follows
  [docs/obsidian-hidden-knowledge.md](docs/obsidian-hidden-knowledge.md). Keep it
  disabled by default, desktop-capability guarded and scoped to that subtree
  (the folder is the product constant, not a setting),
  minus the user-editable exclusion list (default `build`); do not enable
  competing hidden-folder indexers.
  Preserve the complete upstream MIT notice and pinned source revision in the bundle.
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
