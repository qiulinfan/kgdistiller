---
name: capture-kgdistiller
description: Save or update one source-backed knowledge record while reading — a node, a relation or an example — citing its source lines verbatim, recording direct pending terms and the owner's stated understanding, then accepting and indexing it. Use for requests to capture this concept, remember this definition or result, or update one record; whole-source extraction belongs to compile-knowledge-sheets.
---

# Capture one knowledge item

Save the item the owner selected while reading as one record in its registered
base. Use the current passage and just enough nearby context to keep its
statement and conditions complete. This is the usual writing path while reading
a new article; do not turn it into a full-source read, a survey, a recursive
prerequisite search or a new base.

Match the owner's language. Keep commands, ids, keys and raw errors unchanged.

Read [references/record-format.md](references/record-format.md) before writing
a record. Paths given to `kgd` may be absolute or relative to the working
directory; the base is the registered root that contains the path.

## 1. Read the profile

```sh
kgd sheet SOURCE --json
```

It returns the source's base, document type, `node_kinds`, `relation_kinds`
(each kind with its roles), `epistemic` list, guidance, `line_count`, and the
records, drafts and pending terms that already cite this source. Follow that
type; do not infer kinds from the file extension. Read the source text with
your own tools and fix the exact 1-based line range that states the item. An
unregistered file is an error that asks for a glob in
`$KGDISTILLER_HOME/config.json`: report it instead of registering one.

## 2. Establish identity

```sh
kgd resolve "LABEL" "ALIAS" ...
kgd search "LABEL OR DEFINING PHRASE"
kgd get UID
```

`resolve` lists, per term, the `senses` (records whose label or alias equals
it), `mentions` and `pending` uses, across every base. Compare definitions and
conditions yourself: the same name is not the same concept, and a homonym in
another paper or base is a separate record. A concept this source did not
define or originate is not a record here, even when the passage explains it:
link the record of its defining source or keep a global pending term. If
`lag.changed_files` is above 0, `lag.unembedded` is above 0 or
`lag.embedding_changed` is true, run `kgd index` first so the comparison sees
current files and vectors.

Then classify the item by the knowledge model's conflict rules ("Conflicts,
updates and relocation"):

- **new**: no record holds this knowledge point, or only a homonym does.
- **unchanged**: a record holds it with the same meaning. Write nothing new.
  If that record's citation no longer verifies (`kgd check --base B` lists it
  as stale or its source as missing) and this passage states the item, move
  its `source`, `lines` and Evidence here.
- **superseding**: a record holds it with a different meaning, and this
  passage's definition is more general or corrects it. Overwrite that record
  and relocate it here (step 3). A definition that the recorded one explicitly
  rules out, such as the error it corrected, is conflicting even when it is
  broader.
- **conflicting**: any other changed meaning, a narrower definition
  included. Show the owner both definitions
  with their source lines, ask which stands, and write nothing for the item
  until they answer.

Capture a node only for a noun-like object the source defines or a theorem it
states precisely. Any other statement that can be true or false is a relation
over the records it concerns, and its Evidence quote must itself state the
relation. A connection you would infer by combining passages, or across
sources, is never written as a relation: report it.

## 3. Write

**New item.** Write `<root>/.knowledge/drafts/<id>.md` in the record format,
with `id = slug(label)` (append `-2`, `-3`, … on a collision); when the label
carries a section, figure, table, equation or theorem number, write an id
from the meaning instead. Role values are links or pending concepts; settings,
hyperparameters and measured values go in the prose. Then accept it:

```sh
kgd accept <root>/.knowledge/drafts/<id>.md
```

The owner's capture request is the consent; do not ask again. Copy the Evidence
quotes verbatim from the cited lines. Link existing records by id (`"[[id]]"`,
or `"[[base:id]]"` for another base); a link to a draft you also wrote needs
both drafts in the same `accept`. A refusal lists each problem and writes
nothing: fix the draft and accept again.

**Recorded item.** Edit `<root>/.knowledge/entries/<id>.md` in place with an
editing tool that refuses when the file changed since you read it (Claude Code
`Edit`, Codex `apply_patch`); never rewrite a whole record from an earlier
read. An overwrite keeps the id and `understanding` and rewrites everything
else from this passage, `source`, `lines` and Evidence included. When this
passage lies in another base, the record moves there by the knowledge model's
relocation procedure. Never rename an accepted id: a better name goes in
`label` or `aliases`. Merging duplicates or splitting a record that conflated
two meanings follows the knowledge model's identity-change procedure. Then:

```sh
kgd check --base B
```

Fix every `errors` item that names your file. `moved` evidence after a source
edit is repaired with `kgd check --base B --fix-lines`; `stale` evidence needs
the quote re-copied from the source.

## 4. Pending terms, one level

A term the passage uses without explaining becomes a plain value (not a link)
in `requires` or in the role where it occurs, unless a registered base already
holds the record that defines it. Look every such term up with `kgd resolve`
before writing it, since it checks every base; a record in another base is
linked as `[[base:id]]`. Do not invent its
definition or a stand-in record, and do not resolve the next level now. When
the owner later studies it, that capture records its own direct gaps.

## 5. Understanding

Set `understanding` only from the owner's explicit statement in this
conversation (`unknown`, `not-yet-understood`, `understood`). Saving, reading
or finding a definition never establishes it. Preserve an existing value
unless the owner changes it.

## 6. Link pending uses

For each created record, run `kgd resolve "LABEL" "ALIAS" ...` with its label
and aliases. The `pending` entries are uses of those names in every base.
Read each using record with `kgd get UID --source-lines 5` and keep only the
uses that mean the created record's definition; a homonym stays pending. Edit
each kept value in place into a link that keeps the source's wording,
`"[[id|wording]]"` within the base or `"[[base:id|wording]]"` across bases,
with an editing tool that refuses stale reads, then run `kgd check --base B`
for every base you edited. Regenerate with `kgd sheet SOURCE` the existing
sheet of every source whose records you edited, relocated or linked.

## 7. Index

```sh
kgd index
```

Every knowledge write ends with this, so search sees the change. The report
includes `embedded`, `unembedded`, `truncated`, `embedding_error` and
`understanding_changed`. If `embedding_error` says
`install kgdistiller[retrieval] or set embedding to null`, the lexical index is
committed and only the vectors are missing: report that message to the owner
and do not edit `config.json`.

## 8. Maintain topic navigation

After successful acceptance or an accepted-record edit, invoke
`$organize-kgdistiller` with the changed accepted UIDs. This authorized ingestion
phase generates/updates useful topic routes and summaries, locally first and
globally when needed. It may split, merge, reparent or retire navigation
records without another selection round; it preserves scientific records,
their understanding and evidence. The organization Skill registers its own
agent-authored guide, verifies coverage and ends with `kgd index`. A refusal
or navigation failure does not undo the captured scientific record; report
both states and finish with `kgd index`.

## 9. Report

Return the item's classification (new, unchanged, superseding with the old
and new source lines, or the conflict put to the owner), the uid(s), the
accept receipt (`created`, `understanding_set`) or the `check` result for an
in-place edit, the pending terms recorded, and the
`index` result (`embedded`, `unembedded`, `truncated`, `embedding_error`,
`understanding_changed`) and the pending uses you linked. Name any refusal
and what was left unwritten, including connections you reported instead of
writing. If the owner wants a sheet for this source, `kgd sheet SOURCE`
regenerates it.
