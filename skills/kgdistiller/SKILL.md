---
name: kgdistiller-distill
description: Distill one Markdown, Typst, or LaTeX authority into a source-backed kgdistiller graph by proposing explicit knowledge markers, cross-file references, grounded entries, and direct semantic edges, then validating the scoped result.
---

# Distill a knowledge source

Use this skill when the user asks an Agent to extract, curate, update, or publish
knowledge from a configured kgdistiller authority.

## Safety and authority

- The selected source file remains authoritative.
- Preserve all user-authored `#kn`, `--[[...]]--`, `\kn{}`, and reference
  markers unless the user explicitly asks to remove or rename them.
- Work on one changed file at a time unless the user requests a broader scope.
- Default to a reviewable proposal. Apply source edits and graph deltas without a
  pause only when the user has explicitly authorized automatic application.
- Never place credentials, prompts containing secrets, or private source text in
  model-provider logs or committed delta metadata.

Read `docs/graph-contract.md` completely before curating a project for the first
time in a task.

## Workflow

1. Locate the project registry and confirm that the selected path is configured.
2. Run a scoped scan:

   ```sh
   kgdistiller --repo-root PROJECT scan --file SOURCE
   ```

3. Read the complete authority and search existing graph names and aliases with
   `kgdistiller search` and `kgdistiller show`.
4. Decide which concepts are independently teachable, reusable, and stable.
   Do not promote every heading, equation, example, proof, or keyword.
5. Propose any missing native definition markers:
   - Typst: `#kn[Name]`
   - Markdown: `--[[Name]]--`
   - LaTeX: `\kn{Name}`
6. For a direct immediate dependency whose authority is another file, add one
   meaningful native reference marker. Do not add references for same-file or
   merely transitive prerequisites.
7. Create a `qlkg-agent-delta-v2` containing a concise source-grounded entry for
   every node defined in the file and only direct, evidenced semantic edges.
8. Present the source diff and graph delta together in review mode.
9. After approval, edit the source, then run:

   ```sh
   kgdistiller --repo-root PROJECT scan --file SOURCE
   kgdistiller --repo-root PROJECT apply DELTA
   kgdistiller --repo-root PROJECT sync --file SOURCE
   kgdistiller --repo-root PROJECT curate-check --file SOURCE
   kgdistiller --repo-root PROJECT check
   ```

10. Report added and reused nodes, references, edge changes, warnings, and the
    exact authority processed.

If a duplicate name, dangling reference, semantic cycle, missing field, or
missing cross-file reference appears, resolve it from the authority and existing
graph rather than weakening validation.
