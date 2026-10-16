---
name: relocate-kgdistiller
description: Move one kgdistiller record's citation to a passage the owner gives — after a source was edited, renamed, merged or split, or to localize foreign knowledge — by finding the record from the knowledge's name and running kgd relocate with the passage's text, which finds the passage in the registered sources and rewrites only the record's source, lines and, when needed, Evidence. Use when the owner names a piece of knowledge (exactly or roughly) and pastes the new passage that states it.
---

# Relocate one record

The owner gives two things: the knowledge's name, exact or approximate, and
the text of the passage that now states it. Sources change, and nothing
follows them automatically; this Skill moves one record's citation to the
passage the owner points at. Its meaning stays: only `source`, `lines` and,
when the old quotes are not in the passage, the Evidence change. It works the
same for a foreign record, which becomes local.

Match the owner's language. Keep commands, uids, keys and raw errors
unchanged.

## 1. Find the record, in one turn

```sh
kgd resolve "NAME" "OTHER NAME"
kgd search "NAME" --limit 5 --no-dense
kgd drafts --name "NAME"
```

Pick the record or draft the owner means: the name may be a translation, an
alias or a paraphrase. When several remain plausible, ask the owner which,
listing their labels and current `source:lines`; do not guess.

## 2. Relocate, in one command

Pass the passage on standard input, exactly as the owner gave it:

```sh
kgd relocate UID - <<'PASSAGE'
THE PASSAGE'S TEXT
PASSAGE
```

`UID` is `base:id` of an accepted record; a draft is given by its file path,
`ROOT/.knowledge/drafts/ID.md`. `kgd relocate` finds the passage in the
record's base the way `kgd find` does: by its words in order, ignoring line
breaks and the markup a reading view hides. It needs exactly one match there:

- `refused` with several matches: run it again with `--source PREFIX` naming
  the right file, from the matches it lists;
- `refused` with no match: say so; when it names a match in another base,
  moving a record across bases is the knowledge model's relocation, so report
  it to the owner instead.

On success it rewrites the record under the home lock and refreshes the
keyword index. The owner's request is the consent; do not ask again, and never
edit the record yourself.

## 3. Report

One short line: the record's label, its old and new `source:lines`, and
whether the evidence was kept or replaced by the passage's text. A replaced
evidence waits for the owner's re-embed like any edit.
