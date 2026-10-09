# Deployment contract

## Knowledge and optional snapshot boundary

The knowledge project owns native Markdown, Typst and LaTeX sources,
`.knowledge/entries/` Markdown atomic entries linked to evidence, source
registration and durable graph records. Reviewed identities and alignments are
optional; preserve nonempty registries and create them only when used.
`kgdistiller-graph-v2` retains identity/alias/orphan state, accepted edges and
reference occurrences. Entry content is read from the bound Markdown, not a
second persisted body store. `.knowledge/` is the project's only knowledge
root, and graph v2 is the only accepted graph schema.

`documents.jsonl` and `store.json` package an explicitly requested portable
snapshot. They are not live canonical knowledge or prerequisites for capture,
query, the Obsidian graph feed or ordinary Git cloning. Never generate a snapshot merely because
a checker expects one. Existing derived evidence is retained only where used.
Opening the knowledge project as an Obsidian vault changes none of these roles.
The product checkout and the Obsidian graph feed are not authority or backup
roots.

Keep `.knowledge/build/`, journals, plans, receipts, credentials and query logs
local and ignored. The Obsidian graph feed under `.knowledge/build/obsidian/` is
rebuilt on demand. No database materialization is required.

Any other graph schema, including pre-0.4 graphs and SQLite artifacts, fails
closed. Stop and report it to the user; do not relabel or migrate it.

## Source extraction profiles

The source registry may contain a `document_types` mapping. Names and node kinds
are supplied by the user; the product does not pre-register research, mathematics
or computing classes. For example, using placeholder values:

```json
{
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

This is a fragment of `.knowledge/sources.json`, whose top level holds only
`schema`, `sources` and the optional `document_types`. Each source holds only
`id`, `root`, `files` and an optional `document_type`; any other key is
rejected. Omit unused document types. `node_kinds` is a nonempty list of unique
user-defined names, and `extraction_guidance` contains the user's extraction
rules. A source
selects a registered profile by exact name; a submitted semantic `kind` must
belong to that profile. Registration never reclassifies existing identities.
Its file extension selects the reader. If different files need different profiles, register separate bounded file sets.
An omitted `document_type` leaves a source unclassified; do not silently assign
one. Read the original `.md`, `.typ` or `.tex` source instead of converting it.
Knowledge entries and def/pending link sheets remain Markdown.

`kgdistiller --repo-root PROJECT scan --file RELATIVE_AUTHORITY` exposes the
selected source's `document_type` in `sources`, plus the corresponding
`document_types` profile. It also works before definition markers are added.
This read-only route lets extraction workflows read the user's policy without
assuming graph nodes already exist. Registration alone creates no nodes and
makes no RAG or vector-index choice.

## Required checks

Before a snapshot or a graph feed refresh, run `check` and `agent status`. For a requested
snapshot, run `store snapshot` then `store verify`; for a separate snapshot,
verify its output root. Before restoring an actual existing snapshot, verify
it. An ordinary clone without a snapshot needs source/graph checks, not a newly
generated store. A valid graph is directly queryable through generation-checked
`GraphView`. Report an existing unrefreshed snapshot as stale until verified.

Never run `sync` to mask a verification mismatch and never hand-edit manifests,
invent digests, or delete an interrupted ingest journal. Restore a known-good
generation or repair the native authority on its owning machine.

## Product provenance and authorities

Record installed kgdistiller version and full product commit when discoverable.

The knowledge-project root may be the Obsidian editor vault; registered
Markdown files and `.knowledge/entries/*.md` remain authority. The Obsidian
graph feed `.knowledge/build/obsidian/semantic-graph.json` is derived and never
a source. Do not add it to the source registry or feed it to scan, sync,
candidate, or ingest. kgdistiller has no publishing surface; websites, course
registries and HTML conversion belong to the repositories that own the notes.

Installing, linking, snapshotting, committing and pushing are separate
authorities. Never place private sources or secrets in a product repository,
receipt, command output or Codex configuration.
