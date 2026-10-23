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

It returns the base, the `source` path relative to the base root (`REL`
below: `--source` takes this value, never an absolute path), the profile of
every document type the base allows (`types`, each with its `node_kinds`,
`relation_kinds` (kinds with ordered roles), `epistemic` list and guidance;
with one type also at the top level), `line_count`, the accepted records and
drafts that already cite this source, and its `markers`. Choose the type that
fits the document from what it is, and follow that type's guidance; in a base
of several types every draft declares it as `type:`. Do not impose a built-in
catalog. Read the source text with your own tools. Every md, tex and txt file
of a base outside hidden directories is a source; report any other file
instead of working around it.

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
  corrected, is a conflict even when it is broader. A replacement lives in
  its record's base: when the record lies in another base than this source,
  draft nothing and report the proposed replacement with the record.

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

- nodes for the objects the source defines and the results it states, in
  the type's node kinds: theorems (every precisely stated result, whatever
  the source calls it), findings and examples, their roles linking every
  record they are about ("the sum of two subspaces is a subspace" is a
  theorem about subspace and sum of subspaces, not an implication); a node
  kind that declares roles links at least one record;
- relations for the connections the source states between records, of the
  kinds the type declares, such as implications between results and
  comparisons, with every participant bound to a declared role and at
  least two values; relations about relations are allowed;
- for each relation, an Evidence quote that itself states the relation; a
  connection you infer by combining passages, or with another source this one
  does not state, is not written here (report it, see step 6);
- every value a link to a record or to a draft, of any base; a concept
  nothing records stays out of the values and is reported, and a statement
  node left with no value is reported instead of drafted; settings,
  hyperparameters and measured values stay in the prose;
- `requires` for direct understanding prerequisites, one level deep;
- ids from the meaning, without section, figure, table, equation or theorem
  numbers; a replacement keeps the recorded id and is written from a fresh
  read of the record;
- Evidence quotes copied verbatim from the cited lines;
- `type:` the chosen document type, unless the base has only one.

Drafts may link accepted records and each other. Leave `understanding` out
(it is `yes`) unless the owner said they do not understand an item.

## 5. Mark, relocate moved citations and list the drafts, in one command

```sh
kgd mark ROOT/.knowledge/drafts/ID.md
kgd relocate UID
kgd relocate UID - <<'PASSAGE'
THE PASSAGE THAT STATES IT
PASSAGE
kgd drafts --source REL
```

Add one `kgd mark` per new local draft: it writes a marker naming the draft
into this source on the line of its first quote, without changing the line
count (`--[[LABEL]]--:` in md and txt, `\kn[LABEL]{}` in tex), so the record is
found again after the file is edited, renamed or moved. Where the source
already writes an item's name at its definition, wrap that name in the
source's own syntax instead (`--[[NAME]]--`, `\kn{NAME}`; NAME is the label,
its part after `·`, an alias or the id) and skip its `kgd mark`. A replacement
keeps the marker its record already has; a foreign draft takes none.

For each unchanged knowledge point whose citation no longer verifies, run
`kgd relocate UID` first: it follows the record's markers (its own file, its
base, then the other bases) and needs no passage when the quotes still hold
there. When it is refused, add one `kgd relocate` with the passage of this
source that states it; it rewrites
only the record's `source`, `lines` and, when needed, Evidence, and when the
record lies in another base it moves the record into this source's base and
rewrites every link to it. Pass `--base` or `--source REL` when the passage
occurs more than once. Every draft you wrote must have empty `problems`: they
are what `kgd accept` would refuse. Fix the drafts and run the command again. Pre-existing problems of other
drafts are reported, not repaired.

## 6. Report

Report the following:

- the scope and actual coverage;
- the new drafts written;
- the unchanged knowledge points with their records, marking those whose
  citation `kgd relocate` moved;
- the replacement drafts, each with its reason (more general, a correction,
  or a conflict) and the old and new `source:lines`, and the proposed
  replacements of records of another base, which were not drafted;
- the records `kgd relocate` moved into this source's base;
- the records whose knowledge disappeared from their source;
- the concepts left unlinked because nothing records them;
- the ambiguous candidates left out.

When the drafts exceed a node threshold that the type's guidance sets, say
which nodes you re-checked. List separately the connections you inferred but
did not write, such as two sources' claims on one question, with the UIDs and
passages that ground them. The owner reviews and accepts the drafts in the
Obsidian Capture view or with `kgd accept`.
