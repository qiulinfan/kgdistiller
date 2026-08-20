# kgdistiller agent guidance

- Keep the deterministic core provider-neutral. Model-specific behavior belongs
  in Agent skills or adapters.
- Never infer graph identity from document order, headings, or keyword
  co-occurrence. Only explicit source markers define knowledge nodes.
- Preserve user-authored markers and require evidence for semantic relations.
- The local server must bind to `127.0.0.1` by default and prevent path traversal.
- Maintain compatibility with all three authority formats: Markdown, Typst, and
  LaTeX.
- Run the complete unit test suite and package build for implementation changes.
- Do not add user knowledge data, credentials, generated graphs, or model keys to
  this repository.
- Claude Code has the full product integration: the transactional
  `kgdistiller claude link` installer, driven by
  `workflows/claude-manifest.json`, installs Skills, Claude Code agent presets
  (`.claude/agents/*.md`), and the canonical product root, mirroring
  `kgdistiller codex link`. Keep the `skills` and `workflows` sections of the
  two runtime manifests identical; agent presets stay runtime-specific files.
- `scripts/link-claude-skills.sh` / `.ps1` remain a skills-only development
  shortcut independent of both manifests; they install no agents, workflows,
  or receipts, and `kgdistiller claude link` adopts symlinks they created.
