# kgdistiller guidance for Claude Code

Read and follow `AGENTS.md` for repository conventions.

Claude Code integration is currently skills-only: `scripts/link-claude-skills.sh`
on POSIX/WSL or `scripts/link-claude-skills.ps1` on native Windows links each
`skills/<name>` into the Claude Code user Skill directory (`~/.claude/skills`,
honoring `CLAUDE_CONFIG_DIR`). These scripts are deliberately independent of the
Codex product manifest (`workflows/manifest.json`) and of the transactional
`kgdistiller codex link` installer; they install no agents, workflows, or
receipts. Porting the full product integration (agents, workflow manifest,
transactional install) to Claude Code is a separate project that needs an
explicit request.
