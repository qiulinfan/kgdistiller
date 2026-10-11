---
name: harvest-kgdistiller
description: Accept the draft records the owner ticked in a kgdistiller source sheet, after looking them up again under the conflict rules, with one kgd harvest call, link the pending uses of the new concepts across bases, then re-index. Use when the owner asks to harvest a sheet, accept the checked drafts, or import the items selected in Obsidian into their kgdistiller (kgd/kgdt) knowledge base.
---

# Harvest ticked drafts

A sheet `<root>/.knowledge/sheets/<source path>.md` lists the drafts that cite
its source under `## Drafts`, each with a checkbox. The owner reviews or edits
the drafts in Obsidian and ticks the ones to accept. The harvest request is the
consent for exactly those rows; do not repeat the selection in chat.

Match the owner's language. Keep commands, ids and raw errors unchanged.

Work in batches: one lookup turn for every ticked draft, one command that
harvests and indexes, and a pending-use pass only when the lookup found uses
to link.

## Look up before admitting, in one turn

Knowledge admitted after the compile run may already record a ticked draft or
define one of its pending terms. Look up every ticked draft whose file exists
together: one `kgd resolve "LABEL" "ALIAS" "PENDING TERM" ...` with the labels,
aliases and pending terms of all of them, plus `kgd search "DEFINING PHRASE"
--limit 5` (several in the same turn) for the drafts `resolve` found no sense
for. Compare meanings and apply the knowledge model's conflict rules
("Conflicts, updates and relocation"):

- **unchanged**: a record now holds this knowledge point with the same
  meaning. Delete the draft; harvest skips its row.
- **superseding**: the draft's definition is more general than the recorded
  one or corrects it, and the recorded one does not explicitly rule it out.
  Overwrite the record from the draft by the update or relocation procedure,
  keeping its id and `understanding`, then delete the draft.
- **conflicting**: any other changed meaning, a narrower definition
  included. Stop before harvesting, show the owner both definitions with their
  source lines and ask which stands.

Edit each of the ticked drafts' pending terms that a record now defines into
a link that keeps its wording, with an editing tool that refuses stale reads.

## Harvest and index, in one command

Paths may be absolute or relative to the working directory.

```sh
kgd harvest SHEET && kgd index
```

`kgd harvest SHEET --dry-run` prints `{"would_create": [uid]}` and writes
nothing; use it only when the owner wants to see the selection first.
`kgd harvest` collects the ticked
`## Drafts` rows, skips rows whose draft no longer exists, and runs one
`kgd accept` on the rest under the home lock. On success it moves each draft
into `entries/` without overwriting anything, regenerates the sheet (accepted
rows move into their kind sections, unticked drafts stay unticked) and prints
the receipt `{"created": [uid], "understanding_set": [{"uid", "value"}]}`. Then
`kgd index` makes the new records searchable; every knowledge write ends with
it. Its report includes `embedded`, `unembedded`, `truncated` and
`embedding_error`. If `embedding_error` says `install kgdistiller[retrieval] or
set embedding to null`, the harvest and
the lexical index are committed and only the vectors are missing: report that
message to the owner and do not edit `config.json`.

## Link pending uses, only when the lookup found them

The lookup's `resolve` also listed the `pending` uses of the created records'
labels and aliases in every base. When some of them mean a created record's
definition (a homonym stays pending; `kgd get UID --source-lines 5` only when
the gloss leaves it open), edit each kept value in place into a link that
keeps the source's wording, `"[[id|wording]]"` within the base or
`"[[base:id|wording]]"` across bases, with an editing tool that refuses stale
reads, then run in one command `kgd check --base B && kgd index` for every
base you edited, with `kgd sheet SOURCE` for every other source whose records
you edited and that has a sheet. Otherwise skip this step.

## On refusal

A refusal prints `{"refused": [{"path", "message"}]}`, exits 1 and changes
nothing. Report each listed row and message to the owner. The usual message is
`select [[x]] too`: a ticked draft links the unticked draft `x`, so the owner
ticks it as well or edits the link. Other messages name a check rule (a broken
link, evidence that is no longer fresh, an id that already exists). Leave the
fix to the owner or apply only the edit the owner asks for, then harvest again.

## Boundaries

- Never re-extract the scientific source, regenerate scientific draft content
  or add scientific drafts; the reviewed drafts on disk are what gets accepted.
  The lookup before admitting only deletes drafts whose knowledge is already
  recorded, overwrites a record from a superseding draft and links pending
  terms. Linking pending uses edits only the values it links.
- A checkbox means "selected for acceptance", never "understood". Do not touch
  `understanding`; report `understanding_set` as returned.
- A receipt with `aborted` names a target that appeared meanwhile; rerunning
  the same harvest finishes an interrupted run.
- Harvesting implies no Git commit, push or publication.

## Report

Return the drafts settled before admission (deleted as unchanged, or
overwriting a record, with its uid) and any conflict put to the owner, the
created uids, `understanding_set`, the regenerated sheet path, the pending
uses you linked and the `index` result (`embedded`, `unembedded`,
`truncated`, `embedding_error`), or the refused rows with their messages.
