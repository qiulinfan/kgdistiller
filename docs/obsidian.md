# Obsidian with native kgdistiller Vaults

A native kgdistiller Vault is already an Obsidian-compatible directory. Open
the Vault root directly. Concept, field, and topic notes are ordinary Markdown
authority; no projection or plugin is required for basic navigation.

## Authority and derived views

Native Markdown under the configured concept/field/topic roots is authoritative.
Its kgdistiller-owned frontmatter keys form a closed canonical contract for
stable IDs, taxonomy links, and typed relation endpoints. Non-kgdistiller user
properties, comments, and line endings remain user-owned and are preserved by
native merges. The note body stores the concise reviewed knowledge entry. Source
versions, evidence spans, derivation history, and curation/evidence freshness
remain in the source ledger and are projected through the graph and
kgdistiller's API/workspace.

Obsidian's built-in graph follows Markdown links and therefore sees concept and
taxonomy endpoints, but it does not preserve relation types. Use
`kgdistiller serve` for typed incoming/outgoing relations, provenance, source
history/diff, stale review, and federated search across Vaults.

`.kgdistiller/graph` is derived JSON and must not be edited or interpreted by an
Obsidian plugin as authority.

## Recommended local settings

kgdistiller never creates or modifies `.obsidian`. Configure settings manually
and keep them machine-local unless the user deliberately versions them.

For the default authority layout, a useful graph filter is:

```text
path:"Knowledge"
```

Optional groups may distinguish native kgdistiller concept, field, and topic
tags. Treat tags as display/filter metadata, not identity. Vault ID plus the
stable node ID establishes identity; a filename, heading, basename, or Wikilink
display text does not.

Use light/dark themes and plugins according to personal preference, but do not
make a plugin-generated cache or index part of the portable store.

## Editing notes

Edit ordinary Markdown content normally. Preserve the closed kgdistiller-owned
frontmatter keys and stable ID; unrelated user properties/comments remain
allowed. A node may have multiple field/topic parents; do not force the DAG into
one folder tree. Semantic relations require direct reviewed source evidence and
one supported relation type.

For a human-authored note-only change that does not add or change source
derivations, evidence bindings, curation state, or typed semantic relations,
rebuild the derived native graph and then verify it:

```sh
kgdistiller knowledge sync --vault VAULT_ID
kgdistiller knowledge check --vault VAULT_ID
```

`knowledge check` only verifies; it does not rebuild stale graph output. If the
edit changes a source-grounded definition, evidence, derivation status, or typed
relation, capture the live source and create a reviewed native ingest request so
the ledger, notes, curation state, and graph update together. Do not hand-edit
graph or ledger JSON, or use `knowledge sync` to bypass that review boundary.

The packaged workspace returns only safe Vault-relative open actions. It never
exposes an absolute path through `/api/v1`. Opening a local file remains a
user-controlled desktop action.

## Source evidence

Markdown, Typst, and LaTeX evidence files can coexist in the Vault outside the
native knowledge roots. Resolve and capture them before curation:

```sh
kgdistiller vault locate SOURCE
kgdistiller source capture SOURCE
kgdistiller source diff SOURCE
```

The source archive is immutable evidence. Do not edit blobs or generation
JSONL. Edit the live source, capture a new version, review its predecessor diff,
and apply one native transaction.

## Legacy projection isolation

`kgdistiller export obsidian` produces a lossy
`qlkg-obsidian-projection-v1` only for explicitly selected legacy marker
projects or external browsing copies. It is not used by a native Vault.

Never:

- register the projection as a source or native authority root;
- rescan or ingest projected notes;
- merge projection frontmatter into native notes;
- treat projection edits as round-trip authority;
- copy projection files into a `qlkg-vault-store-v3` inventory.

Regenerate a legacy projection from its legacy authority graph. Migrate legacy
knowledge through captured evidence, reviewable native note candidates, fresh
qualified recall, and `qlkg-vault-ingest-request-v1`—not by adopting projection
bytes.
