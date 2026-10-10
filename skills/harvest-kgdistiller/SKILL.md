---
name: harvest-kgdistiller
description: Accept the draft records the owner ticked in a kgdistiller source sheet, with one kgd harvest call, then re-index. Use when the owner asks to harvest a sheet, accept the checked drafts, or import the items selected in Obsidian into their kgdistiller (kgd/kgdt) knowledge base.
---

# Harvest ticked drafts

A sheet `<root>/.knowledge/sheets/<source path>.md` lists the drafts that cite
its source under `## Drafts`, each with a checkbox. The owner reviews or edits
the drafts in Obsidian and ticks the ones to accept. The harvest request is the
consent for exactly those rows; do not repeat the selection in chat.

Match the owner's language. Keep commands, ids and raw errors unchanged.

## Run

Paths may be absolute or relative to the working directory.

```sh
kgd harvest SHEET --dry-run
kgd harvest SHEET
kgd index
```

`--dry-run` prints `{"would_create": [uid]}` and writes nothing; use it when
the owner wants to see the selection first. `kgd harvest` collects the ticked
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

## On refusal

A refusal prints `{"refused": [{"path", "message"}]}`, exits 1 and changes
nothing. Report each listed row and message to the owner. The usual message is
`select [[x]] too`: a ticked draft links the unticked draft `x`, so the owner
ticks it as well or edits the link. Other messages name a check rule (a broken
link, evidence that is no longer fresh, an id that already exists). Leave the
fix to the owner or apply only the edit the owner asks for, then harvest again.

## Boundaries

- Never re-extract the source, regenerate draft content or write new drafts;
  the drafts as they are on disk are what gets accepted.
- A checkbox means "selected for acceptance", never "understood". Do not touch
  `understanding`; report `understanding_set` as returned.
- A receipt with `aborted` names a target that appeared meanwhile; rerunning
  the same harvest finishes an interrupted run.
- Harvesting implies no Git commit, push or publication.

## Report

Return the created uids, `understanding_set`, the regenerated sheet path and
the `index` result (`embedded`, `unembedded`, `truncated`,
`embedding_error`), or the refused rows with their messages.
