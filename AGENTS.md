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
  nodes and typed relations, including applications, live in visible `knowledge/`.
  Source-scoped def/pending sheets are lightweight metadata link projections for
  papers, notes, blogs and other knowledge files. Preserve native authority,
  complete scientific content and existing identities; report unsupported
  adapters instead of claiming partial synchronization is complete. Source sheets
  may be partial; source coverage, definition availability and user understanding
  are independent. Capture only direct dependency gaps and expand another level
  only when the user chooses to study it. New reading normally uses agile
  capture; explicit whole-source distillation is typically for authored notes
  or familiar material. Harvest uses Obsidian def-sheet task selections and
  deterministic ingest; checking a task does not imply understanding.
- The project's single metadata root can be `knowledge/` or `.knowledge/`;
  resolve it through `knowledge_paths`, never create a second tree during reads
  or writes. New projects default to the visible root. Hidden Obsidian indexing
  still requires its explicit plugin setting and semantic graph path.
- Keep metadata minimal: `knowledge/entries/` is the single persisted entry
  body store. Graph v2 retains stable identities, aliases, orphan state,
  accepted edges and reference occurrences; it is not a disposable cache.
  Read existing public graph v1 without mutation; explicit writes produce v2.
  Identity/alignment registries, portable snapshots and consumer exports are
  optional and must not be created merely to fill a default directory layout.
- Source document types are user-registered extraction profiles, independent
  of file format and knowledge domain. Follow the registration contract in
  `docs/concepts-and-relations.md`; do not hardcode the owner's example types
  or require source-to-source Markdown/Typst/LaTeX conversion. Read selected
  profiles through `scan --file`; preserve reviewed node kinds independently
  of scanner syntax and link entries to native evidence. RAG remains open.
- Never infer graph identity from document order, headings, or keyword
  co-occurrence. Only explicit source markers define knowledge nodes.
- Preserve user-authored markers and require evidence for semantic relations.
- The local server must bind to `127.0.0.1` by default and prevent path traversal.
- Maintain compatibility with all three authority formats: Markdown, Typst, and
  LaTeX.
- Native LaTeX scanning, mathematical labels, TeX registries and direct HTML
  integration follow [docs/latex-sources.md](docs/latex-sources.md). Keep the
  renderer in obsidian-latex-live; the graph core only resolves explicit names
  and validates the local converter protocol.
- Run the complete unit test suite and package build for implementation changes.
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
- Optional native indexing of a configured hidden knowledge folder follows
  [docs/obsidian-hidden-knowledge.md](docs/obsidian-hidden-knowledge.md). Keep it
  disabled by default, desktop-capability guarded and scoped to that subtree;
  do not migrate the core storage root or enable competing hidden-folder indexers.
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
