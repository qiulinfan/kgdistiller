# Deployment contract

## Home and base layout

```text
$KGDISTILLER_HOME/            # default ~/.knowledge; owner data
├── config.json               # bases, their source globs, embedding
├── types/<USER_TYPE>.md      # one user-defined document type per file
├── .gitignore                # "index.sqlite*" and "lock"
└── lock                      # writer lock; its contents are meaningless

BASE_ROOT/
├── notes/                    # registered source documents, any text format
└── .knowledge/
    ├── entries/<id>.md       # one reviewed entry per knowledge node
    ├── edges.jsonl           # accepted semantic edges
    └── build/                # rebuildable local work (ignored by the base)
```

`KGDISTILLER_HOME` is the only environment variable for kgdistiller's own home
and data (the runtime linkers also honor `CODEX_HOME` and `CLAUDE_CONFIG_DIR`);
it must be absolute after `~` expansion. A base owns its source documents and its
`.knowledge/` tree, the base's only knowledge root. Each entry is
Obsidian-compatible Markdown: frontmatter properties (`schema`, `id`, `label`,
`kind`, `aliases`, `source`, `line_start`, `line_end`, `understanding`), the
human sections, and an Evidence section quoting the cited source lines
verbatim. `edges.jsonl` holds one accepted edge per line with exactly `source`,
`relation`, `target`, `origin`, `confidence` and `evidence`. Nothing else in a
base is knowledge: `.knowledge/build/` (ingest journals, plans, receipts,
review drafts, retrieval caches, the Obsidian graph feed) is local and
rebuildable.

Opening a base root as an Obsidian vault changes none of these roles. The
product checkout and the Obsidian graph feed are not knowledge or backup roots.

## Base registration

`kgd base add BASE_ROOT [--name NAME]` creates the home on first use, records
the base in `config.json` and creates `BASE_ROOT/.knowledge/entries/`.
`kgd base list` shows every base; `kgd base rm NAME` removes only the
registration. Both writers hold the home lock and write `config.json`
atomically. Base names match `^[a-z0-9][a-z0-9-]*$`.

A command finds its base through `--base NAME` after the command, or through
the registered root that contains the working directory's real path. There is
no upward search, no default base and no base identity file. Outside every
registered root without `--base`, a command refuses and lists the registered
bases.

- No base root may equal, contain or lie inside another base root.
- The home may not equal or lie inside a base root.
- `path` is stored as `~/…` when the root lies under the user's home, otherwise
  as an absolute path. A moved base needs its `path` edited by hand.

## Source globs and document types

Names, kinds and guidance are supplied by the user; the product does not
pre-register research, mathematics or computing classes. For example, using
placeholder values, `config.json`:

```json
{
  "bases": {
    "NAME": {
      "path": "~/BASE_ROOT",
      "sources": {
        "notes/**/*.md": "USER_TYPE",
        "notes/*/main.tex": "USER_TYPE"
      }
    }
  },
  "embedding": null
}
```

and `types/USER_TYPE.md`:

```markdown
---
node_kinds: [USER_NODE_KIND]
relation_kinds:
  USER_RELATION_KIND: [USER_ROLE, USER_OTHER_ROLE]
epistemic: [USER_STATUS]
---
The user's rules for nodes, relations, applications and pending gaps.
```

- The top level holds exactly `bases` and `embedding`; each base holds exactly
  `path` and `sources`. `embedding` is `null` or a model id.
- Each `sources` key is a glob relative to the base root with Python glob
  semantics: `*` stays within one path segment, `**` spans directories, and
  hidden files and directories (`.knowledge/`, `.obsidian/`, `.git/`) never
  match. A glob is non-empty and relative, uses `/`, and contains no `..` or
  hidden segment.
- Each value names an existing type file. Several globs may match one file
  when they name the same type; globs naming two different types for one file
  are an error. Every registered source therefore has exactly one type, and
  there is no unclassified source.
- A type file has a slug stem and frontmatter read with PyYAML's `BaseLoader`
  (every scalar stays a string). `node_kinds` is a required nonempty list of
  unique slugs; `relation_kinds` maps a kind to a nonempty list of unique role
  slugs; `epistemic` is a list of slugs. No kind is both a node kind and a
  relation kind. The non-empty body is the extraction guidance.
- Every entry citing a source uses one of its type's `node_kinds`, and every
  file an entry cites must be a registered source. Registration never
  reclassifies existing entries.

Sources are format-agnostic: any UTF-8 text document is read as lines, and its
syntax is never parsed or converted. `kgd scan --file SOURCE --base NAME`
shows the source's base, its `type` and `profile` (`node_kinds`,
`relation_kinds`, `epistemic`, `guidance`) and the numbered lines, so
extraction workflows can read the user's policy before any entry exists.
Registration alone creates no entries and makes no retrieval-index choice.

## Required checks

Before a feed refresh, a commit or a restore, run `check` and `agent status`.
`check` must print `OK`. Entries reported `moved` are fixed with
`check --fix-lines` after the source edit is confirmed; `stale` and `ambiguous`
entries need a reviewed re-capture. Staleness never filters retrieval or the
feed.

Never hand-edit `edges.jsonl`, invent line ranges, or delete an interrupted
ingest journal. Restore a known-good revision or repair the source on its owning
machine.

## Product provenance and boundaries

Record installed kgdistiller version and full product commit when discoverable.

The Obsidian graph feed `.knowledge/build/obsidian/semantic-graph.json` is
derived and never a source. It lies under the hidden `.knowledge/build/`, which
no glob matches; never feed it to scan, capture or ingest. kgdistiller has no
publishing surface; websites, course registries and HTML rendering belong to
the repositories that own the notes.

`kgdistiller codex link` and `kgdistiller claude link` treat installed copies
as product-owned: `doctor` reports a copy that differs from the product source,
and relinking replaces it or removes a retired one, discarding local edits to
installed files. Report a differing copy before relinking.

Installing, linking, committing and pushing are separate authorities. Never
place private sources, the home's `config.json` or types, or secrets in a
product repository, receipt, command output or agent configuration.
