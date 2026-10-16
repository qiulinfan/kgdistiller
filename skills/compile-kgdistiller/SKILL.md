---
name: compile-kgdistiller
description: Extract source-backed knowledge records from a bounded part or the whole of a registered document, guided by its user-registered document type, as drafts for the owner to review and accept, and draft replacements of the knowledge already recorded by the conflict rules. Use for requested whole-source or partial distillation of mathematical notes, computer-science notes, papers, blogs or project documents, including when the user asks to file, ingest or re-ingest a document (for example a rewritten, renamed, merged or split note) into their kgdistiller (kgd/kgdt) knowledge base; one item while reading belongs to capture-kgdistiller.
---

# Compile knowledge into drafts

Turn a bounded scope of one registered source into drafts: new records for its
new knowledge, and replacements for recorded knowledge it changes, by the
knowledge model's conflict rules ("Conflicts, updates and relocation").
Re-admitting a rewritten, renamed, merged or split note is the same request.
Every result is a draft that the owner reviews, revises and accepts
(`kgd accept`, or the Obsidian Capture view); never run `kgd accept` and never
edit an accepted record, except to move its citation with `kgd relocate`.

Work in batches, not item by item: one lookup turn for the whole candidate
batch, one turn that writes every draft, and one command that relocates the
moved citations and lists the drafts with their problems. Look again only for
a candidate whose lookup is genuinely ambiguous.

Match the owner's language. Keep commands, ids, keys and raw errors unchanged.

Read [references/record-format.md](references/record-format.md) before writing
drafts. Paths given to `kgd` may be absolute or relative to the working
directory; the base is the registered root that contains the path.

## 1. Set the scope

Establish the source file(s) and the requested coverage: selected passages,
chapters or the whole file. Full-source distillation needs an explicit request
and is usual for the owner's own notes or familiar material; new reading
normally uses `$capture-kgdistiller`. For a whole-source request, read proofs,
examples and appendices too. Report actual coverage; a partial pass never
claims completeness.

## 2. Read the profile and inventory

```sh
kgd source SOURCE
```

It returns the base, the document type with its `node_kinds`,
`relation_kinds` (kinds with ordered roles), `epistemic` list and guidance,
`line_count`, and the accepted records and drafts that already cite this
source. Follow the type's guidance; do not infer kinds from the file extension
or impose a built-in catalog. Read the source text with your own tools. An
unregistered file is an error that asks for a glob in
`$KGDISTILLER_HOME/config.json`: propose the glob and its type to the owner
instead of registering it.

## 3. Establish identity

Build the candidate batch (labels, aliases, line ranges, kinds, participants
and every concept the drafts will link) first, then look it all up in one
turn:

```sh
kgd resolve "LABEL" "ALIAS" "CONCEPT" ...
kgd drafts --name "LABEL" --name "CONCEPT" ...
kgd search "DEFINING PHRASE" --limit 5
kgd check --base B
```

One `resolve` takes every name of the batch, and one `drafts --name` the same
names among the drafts awaiting the owner. Run a `search` (several in the same
turn) only for the candidates `resolve` found no sense for, to catch the same
knowledge point under another name or language; `kgd get UID` only when a
returned gloss leaves the comparison open. Compare definitions and conditions
yourself. A label names one concept across every base: a candidate of
another meaning whose name a record or draft already has is new, and its
label takes this source's name, `Label (FILE NAME)`, the file's name without
its extension. A concept this source did not define or originate is not a node here,
even when the source explains it: link its record or draft, or leave it out of
the values and list it. Leave ambiguous candidates out and list them. If
`lag.changed_files` is above 0, run `kgd index --no-embed` first and repeat the
lookup once; `lag.unembedded` and `lag.embedding_changed` only weaken the
dense lane, and re-embedding is the owner's call. `kgd check --base B` tells
you which recorded citations no longer verify.

Classify every candidate:

- **new**: no record or draft holds this knowledge point, or only one of
  another meaning with the same name does; it becomes a new draft (step 4).
- **unchanged**: a record or draft holds it with the same meaning. Draft
  nothing, unless that record's citation no longer verifies and this passage
  states it: then move the citation here with
  `kgd relocate UID - <<'PASSAGE'` and the passage's text (step 5).
- **changed**: a record holds it with a different meaning. Draft its
  replacement from this passage, keeping the id and `understanding`, and
  report whether the new definition is more general, corrects the recorded
  one, or conflicts with it in another way, a narrower one included. A
  definition that the recorded one explicitly rules out, such as the error it
  corrected, is a conflict even when it is broader. When this passage lies in
  another base, draft the record in that base with the same id (a
  relocation).

Knowledge that disappeared is gone from its source:

- when the scope is the whole source, records that cite this source (the
  inventory of step 2) whose knowledge the source no longer contains;
- in every case, records of this base whose source file no longer exists and
  that no candidate re-cites.

Report them; their fate is the owner's. Do not look for where that knowledge
went and do not delete anything.

## 4. Write drafts

Write one `<root>/.knowledge/drafts/<id>.md` per new record or replacement in
scope:

- nodes only for the noun-like objects the source defines and the theorems
  it states precisely, in the type's node kinds;
- relations for every other statement that can be true or false, including
  measured results, comparisons, findings and hypotheses, with every
  participant bound to a declared role; a finding about one concept binds it
  alone; self-relations and relations about relations are allowed; examples
  and applications are relations of the kind the type registers for them;
- for each relation, an Evidence quote that itself states the relation; a
  connection you infer by combining passages, or with another source this one
  does not state, is not written here (report it, see step 6);
- every value a link to a record or to a draft of this base; a concept nothing
  records stays out of the values; settings, hyperparameters and measured
  values stay in the prose;
- `requires` for direct understanding prerequisites, one level deep;
- ids from the meaning, without section, figure, table, equation or theorem
  numbers; a replacement keeps the recorded id and is written from a fresh
  read of the record;
- Evidence quotes copied verbatim from the cited lines.

Drafts may link accepted records and each other. Leave `understanding` out
(it is `yes`) unless the owner said they do not understand an item.

## 5. Relocate moved citations and list the drafts, in one command

```sh
kgd relocate UID - <<'PASSAGE'
THE PASSAGE THAT STATES IT
PASSAGE
kgd drafts --source SOURCE
```

Add one `kgd relocate` for each unchanged knowledge point whose citation no
longer verifies, with the passage of this source that states it; it rewrites
only the record's `source`, `lines` and, when needed, Evidence. Every draft
you wrote must have empty `problems`: they are what `kgd accept` would refuse.
Fix the drafts and run the command again. Pre-existing problems of other
drafts are reported, not repaired.

## 6. Report

Report the following:

- the scope and actual coverage;
- the new drafts written;
- the unchanged knowledge points with their records, marking those whose
  citation `kgd relocate` moved;
- the replacement drafts, each with its reason (more general, a correction,
  or a conflict) and the old and new `source:lines`, and any relocation to
  another base;
- the records whose knowledge disappeared from their source;
- the concepts left unlinked because nothing records them;
- the ambiguous candidates left out.

When the drafts exceed a node threshold that the type's guidance sets, say
which nodes you re-checked. List separately the connections you inferred but
did not write, such as two sources' claims on one question, with the UIDs and
passages that ground them. The owner reviews and accepts the drafts in the
Obsidian Capture view or with `kgd accept`.
