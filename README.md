# kgdistiller

`kgdistiller` turns Markdown, Typst, and LaTeX authority sources into a
deterministic, source-backed knowledge graph. Authors may mark concepts
themselves, or let a coding agent propose knowledge nodes, references, entries,
and semantic relations under the same validation contract.

The project is local-first. Source files and graph data stay in your repository,
and the bundled browser listens on loopback by default. Publishing a website is
optional.

## Source markers

Each global knowledge name has at most one authoritative definition. References
can occur anywhere.

| Format | Definition | Reference |
| --- | --- | --- |
| Typst | `#kn[Measure space]` | `#ref[Measure space]` |
| Markdown | `--[[Measure space]]--` | `[[Measure space]]` |
| LaTeX | `\kn{Measure space}` | `\knref{Measure space}` |

Aliases can be used for Markdown display text: `[[Measure space|spaces]]`.
Ordinary headings, examples, equations, and unmarked statements do not become
nodes automatically.

## What it produces

- stable global node identities;
- field and topic classification;
- source provenance and backlinks;
- direct semantic relations such as `prerequisite-for`, `implies`,
  `generalizes`, `contrasts-with`, and `derived-from`;
- deterministic JSONL graph artifacts;
- a local SQLite full-text index;
- a generated Typst reference registry;
- a dependency-free local graph browser.

The committed graph is an index of the authority sources, not a replacement for
them. Agent-authored entries add searchable context while provenance continues
to point to the source.

## Install

For development:

```sh
git clone https://github.com/qiulinfan/kgdistiller.git
cd kgdistiller
uv sync
uv run kgdistiller --help
```

As a tool:

```sh
uv tool install git+https://github.com/qiulinfan/kgdistiller.git
```

Typst is required when a graph contains Typst-authored node labels. Markdown and
LaTeX scanning use the Python standard library.

## Start a local project

```sh
cd your-notes-repository
kgdistiller init --source-root notes
```

This creates `knowledge/sources.json`, a bounded source registry, and the first
graph snapshot. Adjust fields, sources, file globs, topics, and canonical web
locations in that registry.

Then run:

```sh
kgdistiller sync
kgdistiller check
kgdistiller search "measure space"
kgdistiller serve
```

`serve` opens <http://127.0.0.1:8765/>. It exposes the graph and bounded source
excerpts only to the local machine unless another host is explicitly selected.

## Agentic distillation

The deterministic engine does not silently ask a hosted model to rewrite your
notes. Agent integrations use a reviewable workflow:

1. scan one changed authority and inspect its existing graph neighborhood;
2. preserve author-written markers;
3. propose any missing `kn` and meaningful cross-file `ref` markers;
4. distill source-grounded entries and direct semantic edges;
5. validate the source diff and graph delta;
6. apply, synchronize, and run file-level curation checks.

The repository includes a reusable Agent Skill at
[`skills/kgdistiller/SKILL.md`](skills/kgdistiller/SKILL.md). It works with
repository-aware coding agents instead of coupling the graph engine to one model
provider. Review-first operation is the default; fully automatic application is
an explicit user choice.

## Use as a Git submodule

Keeping the engine separate from personal data is a first-class workflow:

```sh
git submodule add https://github.com/qiulinfan/kgdistiller.git vendor/kgdistiller
PYTHONPATH=vendor/kgdistiller/src python3 -m kgdistiller --repo-root . sync
```

The host repository owns `knowledge/sources.json`, `knowledge/graph/`, the source
documents, and any site integration. The submodule owns scanners, schemas,
validation, Agent workflow instructions, and the local browser.

## Compatibility

The initial public release reads and writes the existing `qlkg-v2`,
`qlkg-sources-v2`, and `qlkg-agent-delta-v2` schemas. Preserving that stable data
contract allows existing graphs to move to the standalone engine without a
destructive migration. Future schema changes will require explicit migration.

See [the graph contract](docs/graph-contract.md) for identity, curation, and
validation rules.

## Development

```sh
uv run python -m unittest discover -s tests -v
uv build
```

Licensed under Apache-2.0.
