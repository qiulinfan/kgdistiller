# Deployment contract

## Knowledge layout

```text
PROJECT/
├── notes/                    # registered source documents, any text format
└── .knowledge/
    ├── vault.json            # stable vault identity
    ├── sources.json          # source registration; optional document types
    ├── entries/<id>.md       # one reviewed entry per knowledge node
    ├── edges.jsonl           # accepted semantic edges
    ├── .gitignore            # ignores build/
    └── build/                # rebuildable local work (ignored)
```

The knowledge project owns its source documents and the `.knowledge/` tree.
Each entry is Obsidian-compatible Markdown: frontmatter properties (`schema`,
`id`, `label`, `kind`, `aliases`, `source`, `line_start`, `line_end`,
`understanding`), the human sections, and an Evidence section quoting the cited
source lines verbatim. `edges.jsonl` holds one accepted edge per line with
exactly `source`, `relation`, `target`, `origin`, `confidence` and `evidence`.
Nothing else is knowledge: `.knowledge/build/` (ingest journals, plans,
receipts, review drafts, retrieval caches, the Obsidian graph feed) is local and
rebuildable. `.knowledge/` is the project's only knowledge root.

Opening the knowledge project as an Obsidian vault changes none of these roles.
The product checkout and the Obsidian graph feed are not knowledge or backup
roots.

## Source extraction profiles

The source registry may contain a `document_types` mapping. Names and node kinds
are supplied by the user; the product does not pre-register research, mathematics
or computing classes. For example, using placeholder values:

```json
{
  "schema": "kgdistiller-sources-v1",
  "document_types": {
    "USER_DOCUMENT_TYPE": {
      "node_kinds": ["USER_NODE_KIND"],
      "extraction_guidance": "The user's rules for nodes, relations, applications and pending gaps."
    }
  },
  "sources": [
    {
      "id": "SOURCE_REGISTRATION",
      "root": "SOURCE_DIRECTORY",
      "files": ["*.tex"],
      "document_type": "USER_DOCUMENT_TYPE"
    }
  ]
}
```

The top level holds only `schema`, `sources` and the optional `document_types`.
Each source holds only `id`, `root`, `files` and an optional `document_type`;
any other key is rejected. A source root must be inside the project and outside
`.knowledge/`. Every file an entry cites must be admitted by exactly one
registered source. Omit unused document types. `node_kinds` is a nonempty list
of unique user-defined names, and `extraction_guidance` contains the user's
extraction rules. A source selects a registered profile by exact name; every
entry citing it must use one of its kinds. Registration never reclassifies
existing entries. If different files need different profiles, register
separate bounded file sets. An omitted `document_type` leaves a source
unclassified; do not silently assign one.

Sources are format-agnostic: any UTF-8 text document is read as lines, and its
syntax is never parsed or converted. `kgdistiller --repo-root PROJECT scan --file
RELATIVE_SOURCE` shows the admitting source, its `document_type` and `profile`,
and the numbered lines, so extraction workflows can read the user's policy
before any entry exists. Registration alone creates no entries and makes no
retrieval-index choice.

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
derived and never a source. Do not add it to the source registry or feed it to
scan, capture or ingest. kgdistiller has no publishing surface; websites,
course registries and HTML rendering belong to the repositories that own the
notes.

`kgdistiller codex link` and `kgdistiller claude link` treat installed copies
as product-owned: `doctor` reports a copy that differs from the product source,
and relinking replaces it or removes a retired one, discarding local edits to
installed files. Report a differing copy before relinking.

Installing, linking, committing and pushing are separate authorities. Never
place private sources or secrets in a product repository, receipt, command
output or agent configuration.
