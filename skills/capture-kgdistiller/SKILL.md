---
name: capture-kgdistiller
description: Save or update one source-backed knowledge entry while reading, citing its source lines verbatim and preserving personal understanding and direct pending prerequisites. Use for requests to capture this concept, remember this definition, or update one entry; full-source distillation belongs to compile-knowledge-sheets.
---

# Capture one knowledge item

Save the selected knowledge to the caller's registered base through one bounded
transaction. Use the current passage and just enough nearby context to
preserve its definition and conditions. Do not turn this into a full-source read,
a survey, a recursive prerequisite search or a new base.
This is the usual writing path while reading a new article. Full-source
distillation is separately requested, typically for the user's own notes or
already familiar material.

Match the user's language. Retain technical names, formulas, type/status labels,
structured keys and raw errors.

## Select and understand the item

Establish the target base, registered source, selected concept and the exact
line range that defines or states it from the current conversation. Ask only for
an essential missing target or a genuinely ambiguous meaning. Read additional
local context only when a defining condition or source statement is incomplete.

Read the source's type profile and numbered text before preparing content:

```sh
kgd scan --file SOURCE --base B
```

Pass `--base B` after every command, or run inside the registered base root;
relative paths are resolved against the working directory. The result names the
source's base and `type`, its `profile` (`node_kinds`, `relation_kinds`,
`epistemic`, `guidance`), and lists every line with its 1-based number. A source
is a UTF-8 text document matched by the base's globs in
`$KGDISTILLER_HOME/config.json`, and every source has exactly one type;
kgdistiller never parses its syntax, so `.md`, `.typ`, `.tex` and `.txt` are
read the same way. Follow the user-registered type; do not infer a document
type from the extension or substitute a built-in type list. An unregistered
file cannot be captured; report it instead of registering a glob silently.

Keep the full definition and essential assumptions in the entry. Definitions,
axioms, precise theorems, algorithms and architectures may be nodes. A claim or
example belongs to a relation/application; do not manufacture a concept merely
to fit the entry API. Preserve unsupported complete assertions as review
proposals and identify the remaining adapter gap.

If a selected term is only mentioned and not explained, record it as a direct
pending prerequisite of the current owning knowledge entry. Do not invent its
definition or a canonical target. If no owner is established, keep the gap as a
source-scoped review proposal until that ownership is resolved.

## Preserve learning state and stop at one layer

Read any existing entry's `understanding`. The values are `unknown`,
`not-yet-understood` and `understood`. Record the user's explicit statement and
preserve existing state otherwise. Saving a definition, reading its source or
matching an entry never establishes understanding.

`pending_prerequisites` holds concise direct gaps: term, required meaning and
source/use context, including whether the gap is a missing definition or a
known definition the user still needs to learn. A verified existing entry may be
linked while remaining a learning gap. Add no target node for an unresolved term.
When the user later chooses to study that prerequisite, its own entry can record
its next layer. Stop here during this capture.

Read existing learning fields before changing their lists. Omitted fields keep
their current values on an update; an explicitly supplied pending list replaces
that list, and `[]` clears it. Resolve only the item the user has actually
understood or asked to update. Finding an external source alone does not clear a
learning gap.

## Prepare and apply one bounded update

Use the product's [capture preparation contract](references/capture-contract.md)
for the payload and CLI. The payload cites the source path and line range; the
helper copies those lines verbatim into the entry's Evidence section, checks the
kind against the source's document type, checks the label and aliases against
every existing entry, and writes the plan and apply requests. Do not hand-write
the Evidence quote or the request files. Keep prepared artifacts in the base's
`.knowledge/build/reviews/` or another directory under its `.knowledge/` and
outside `.knowledge/entries/`. `build/` is excluded from Obsidian hidden-folder
indexing by default; remove `build` from the kgdistiller plugin's exclusion list
to open drafts there.

Supply a source-grounded summary and an explicit reviewed add/update intent. A
new entry gets a readable id derived from its label; a label without an ASCII
slug, such as a Chinese term, needs an explicit `id`. An update names its target
entry id. Source documents are never edited. Before adding, resolve the name
with `$query-kgdistiller` (`agent resolve`, then `agent search` when needed);
name similarity alone cannot establish semantic identity, and the helper refuses
a label or alias that already identifies another entry.

Hand the generated requests to `$ingest-kgdistiller`: plan, inspect the listed
changes, and apply the authorized scope. A user's concrete request to save this
item is authorization for that item; do not ask for the same permission again.
Preparation does not itself commit knowledge. A refused apply (for example
`stale-evidence` after the source changed) needs a fresh preparation, not an
overridden gate.

The transaction leaves other knowledge in the source untouched. Verify the
committed receipt, run `kgd check --base B`, and return a link
to the accepted entry plus its understanding and direct remaining gaps. If a
source def/pending sheet exists or was requested, refresh only its affected rows
and retain `partial` coverage. Preserve unrelated rows and annotations. A partial
sheet need not be filled before capture ends.
If the user wants to review several items before saving, prepare them with
`kgd harvest prepare … --base B` so the sheet gets clearly labeled draft links as
Markdown tasks. The user selects those items in Obsidian and explicitly requests
`$harvest-paper` to ingest them. A checkbox selects an import; it does not change
understanding.
