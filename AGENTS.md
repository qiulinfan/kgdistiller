# kgdistiller agent guidance

- Keep the deterministic core provider-neutral. Model-specific behavior belongs
  in Agent skills or adapters.
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
  MIT-0; upstream licenses remain unchanged.
- Claude Code has the full product integration: the transactional
  `kgdistiller claude link` installer, driven by
  `workflows/claude-manifest.json`, installs Skills, Claude Code agent presets
  (`.claude/agents/*.md`), and the canonical product root, mirroring
  `kgdistiller codex link`. Keep the `skills` and `workflows` sections of the
  two runtime manifests identical; agent presets stay runtime-specific files.
- `scripts/link-claude-skills.sh` / `.ps1` remain a skills-only development
  shortcut independent of both manifests; they install no agents, workflows,
  or receipts, and `kgdistiller claude link` adopts symlinks they created.
