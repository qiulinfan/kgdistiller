---
name: compile-knowledge-sheets
description: Extract source-backed knowledge records from a bounded part or the whole of a registered document, guided by its user-registered document type, as drafts for owner review, settle the knowledge already recorded by the conflict rules (ignore, overwrite, relocate or turn pending), then generate the source's def/pending sheet. Use for requested whole-source or partial distillation of mathematical notes, computer-science notes, papers, blogs or project documents, including when the user asks to file, ingest or re-ingest a document (for example a rewritten, renamed, merged or split note) into their kgdistiller (kgd/kgdt) knowledge base; one item while reading belongs to capture-kgdistiller.
---

# Compile knowledge sheets

Turn a bounded scope of one registered source into draft records for its new
knowledge, which the owner reviews and ticks in the source's sheet, and settle
the knowledge that is already recorded by the knowledge model's conflict rules
("Conflicts, updates and relocation"). Re-admitting a rewritten, renamed,
merged or split note is the same request.

Match the owner's language. Keep commands, ids, keys and raw errors unchanged.

Read [references/record-format.md](references/record-format.md) before writing
drafts and [references/sheet-contract.md](references/sheet-contract.md) before
generating a sheet. Paths given to `kgd` may be absolute or relative to the
working directory; the base is the registered root that contains the path.

## 1. Set the scope

Establish the source file(s) and the requested coverage: selected passages,
chapters or the whole file. Full-source distillation needs an explicit request
and is usual for the owner's own notes or familiar material; new reading
normally uses `$capture-kgdistiller`. For a whole-source request, read proofs,
examples and appendices too. Report actual coverage; a partial pass never
claims completeness.

## 2. Read the profile and inventory

```sh
kgd sheet SOURCE --json
```

It returns the base, the document type with its `node_kinds`,
`relation_kinds` (kinds with ordered roles), `epistemic` list and guidance,
`line_count`, and the accepted records, drafts and pending terms that already
cite this source. Follow the type's guidance; do not infer kinds from the file
extension or impose a built-in catalog. Read the source text with your own
tools. An unregistered file is an error that asks for a glob in
`$KGDISTILLER_HOME/config.json`: propose the glob and its type to the owner
instead of registering it.

## 3. Establish identity

Build the candidate batch (labels, aliases, line ranges, kinds, participants)
first, then resolve it:

```sh
kgd resolve "LABEL" "ALIAS" ...
kgd search "LABEL OR DEFINING PHRASE" --base B
kgd get UID
```

Compare definitions and conditions yourself. The same name is not the same
concept: a homonym gets its own record. A concept this source did not define
or originate is not a node here, even when the source explains it: link the
record of its defining source or keep a global pending term. Leave ambiguous
candidates out and list them. If `lag.changed_files` is above 0,
`lag.unembedded` is above 0 or `lag.embedding_changed` is true, run
`kgd index` first. Run `kgd check --base B` too, so you know which recorded
citations no longer verify.

Classify every candidate:

- **new**: no record holds this knowledge point, or only a homonym does; it
  becomes a draft (step 4).
- **unchanged**: a record holds it with the same meaning. Write nothing new;
  if that record's citation no longer verifies and this passage states it, its
  citation moves here (step 5).
- **superseding**: a record holds it with a different meaning, and this
  passage's definition is more general or corrects it. The record is
  overwritten and relocated here (step 5). A definition that the recorded one
  explicitly rules out, such as the error it corrected, is conflicting even
  when it is broader.
- **conflicting**: any other changed meaning, a narrower definition
  included. Write nothing for it and put it to the owner (step 9).

Knowledge that disappeared is gone from its source:

- when the scope is the whole source, records that cite this source (the
  inventory of step 2) whose knowledge the source no longer contains;
- in every case, records of this base whose source file no longer exists and
  that no candidate re-cites.

Step 5 deletes them to pending. Do not look for where that knowledge went.

## 4. Write drafts

Write one `<root>/.knowledge/drafts/<id>.md` per new record in scope:

- nodes only for the noun-like objects the source defines and the theorems
  it states precisely, in the type's node kinds;
- relations for every other statement that can be true or false, including
  measured results, comparisons, findings and hypotheses, with every
  participant bound to a declared role; a finding about one concept binds it
  alone; self-relations and relations about relations are allowed; examples
  and applications are relations of the kind the type registers for them;
- for each relation, an Evidence quote that itself states the relation; a
  connection you infer by combining passages, or with another source this one
  does not state, is not written here (report it, see step 9);
- role values only as links or pending concepts; settings, hyperparameters
  and measured values stay in the prose;
- `requires` for direct understanding prerequisites; unexplained terms as
  plain pending values, one level deep, unless any registered base already
  holds the defining record (link it, across bases if needed); never a
  stand-in definition for a missing source;
- ids from the meaning, without section, figure, table, equation or theorem
  numbers;
- Evidence quotes copied verbatim from the cited lines.

Drafts may link accepted records and each other. Never set `understanding`
without an explicit owner statement.

## 5. Settle recorded knowledge

Edit accepted records in place with an editing tool that refuses when the
file changed since you read it (Claude Code `Edit`, Codex `apply_patch`),
following the knowledge model's "Updates, deletes and identity changes of
accepted records":

- **unchanged** with a citation that no longer verifies: move its `source`,
  `lines` and Evidence to this passage and change nothing else;
- **superseding**: keep the id and `understanding`, and rewrite everything
  else from this passage, `source`, `lines` and Evidence included. When this
  passage lies in another base, the record moves there by the relocation
  procedure;
- **gone**: delete the record and turn every link to it into a pending term
  that keeps its wording, by the delete procedure.

## 6. Check

```sh
kgd check --base B
```

Every draft you wrote and every record you edited must pass: no `errors`
entry may name it, and its evidence must be fresh. Fix and re-check. Check
every other base you edited too. Pre-existing findings on other files are
reported, not repaired.

## 7. Generate the sheet

```sh
kgd sheet SOURCE
```

It writes `<root>/.knowledge/sheets/<source path>.md`, keeping the ticks of
drafts that still exist. Run `kgd sheet` the same way for every other source
that already has a sheet and whose records step 5 edited, deleted or
relocated, so no sheet shows a superseded state. Delete the sheet of a source
file that no longer exists; nothing can regenerate it.

## 8. Organize and index

When step 5 changed or deleted accepted records, invoke
`$organize-kgdistiller` with those UIDs, so navigation routes follow the
changes and routes to deleted records retire. Finish with `kgd index`.

The owner then reviews the drafts in Obsidian, ticks the rows to accept and
runs `$harvest-kgdistiller` (or `kgd accept` on chosen drafts). The harvest
workflow organizes the records it admits; do not route unaccepted drafts or
create a manual topic catalog here.

## 9. Report

Report the following:

- the scope and actual coverage;
- the drafts written and the sheet path;
- the unchanged knowledge points with their records, marking those whose
  citation moved;
- the overwritten records, each with its reason (more general, or a
  correction) and its old and new `source:lines`, and any relocation to
  another base;
- the records deleted to pending, with the links that became pending terms;
- the conflicts put to the owner, each with both definitions and their source
  lines;
- the ambiguous candidates left out.

When the drafts exceed a node threshold that the type's guidance sets, say
which nodes you re-checked. List separately the connections you inferred but
did not write, such as two sources' claims on one question, with the UIDs and
passages that ground them.

Applying the owner's decision on a conflict is an in-place edit of the
recorded item, as in `$capture-kgdistiller`.
