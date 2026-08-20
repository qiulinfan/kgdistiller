# kgdistiller guidance for Claude Code

Read and follow `AGENTS.md` for repository conventions.

Claude Code has the full product integration. `kgdistiller claude link`
transactionally installs the Skills, the Claude Code agent presets from
`.claude/agents/*.md`, and the canonical product root declared by
`workflows/claude-manifest.json` into the Claude Code home (`~/.claude`,
honoring `CLAUDE_CONFIG_DIR`); `kgdistiller claude doctor` verifies the
installation. The `skills` and `workflows` sections of
`workflows/claude-manifest.json` must stay identical to
`workflows/manifest.json`. `scripts/link-claude-skills.sh` /
`scripts/link-claude-skills.ps1` remain a skills-only development shortcut;
the full installer adopts symlinks they created. The agent-facing install
recipe lives in the README.
