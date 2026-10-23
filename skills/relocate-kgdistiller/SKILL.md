---
name: relocate-kgdistiller
description: Find where kgdistiller records went after their sources were edited, renamed, moved, merged or split, and move their citations there — following each record's knowledge markers through its own file, then its base, then every other base, judging a reworded passage by its meaning, and making knowledge that is unmarked and nowhere to be found foreign, citing the outside page that states it. Use when kgd check reports stale, moved or followed records, when the owner names knowledge (exactly or roughly) that is no longer where it was, or pastes the passage that now states it.
---

# Relocate records

Sources change: lines shift, files are renamed and moved, passages are
reworded. A record finds its way back through its markers, the places its
source names it (`--[[NAME]]--` in md and txt, `\kn{NAME}` or `\kn[NAME]{TEXT}`
in tex). This Skill moves each record's citation to where its knowledge now
is. Its meaning never changes here: only `source`, `lines` and, when the old
quotes are gone, the Evidence.

Match the owner's language. Keep commands, uids, keys and raw errors
unchanged.

## 1. Collect the records

- When the owner names knowledge, find it in one turn:

  ```sh
  kgd resolve "NAME" "OTHER NAME"
  kgd search "NAME" --limit 5 --no-dense
  kgd drafts --name "NAME"
  ```

  The name may be a translation, an alias or a paraphrase. When several
  records remain plausible, ask the owner which, listing their labels and
  current `source:lines`.
- Otherwise start from `kgd check`: its `moved` records (quotes shifted in the
  same file), `follow` records (markers found them elsewhere) and `stale`
  records (quotes gone), plus `source ... does not exist` errors.

## 2. Let kgd follow what only moved, in one command

```sh
kgd check --fix-lines
kgd relocate UID
```

`--fix-lines` rewrites every record whose quotes only shifted lines, and
every record whose markers lead to another file of its base where its quotes
still hold. For each record it leaves, run `kgd relocate UID` (a draft by its
file path, `ROOT/.knowledge/drafts/ID.md`): without a passage it searches the
record's markers in its own file, then in every source of its base, then in
every other base, and moves the record there when its quotes still hold,
into the other base with every link to it when it lies there. Run the
records of one batch together.

## 3. Judge what was reworded

`kgd relocate UID` refuses with the record's `markers` when they name it but
its quotes no longer hold: the passage was rewritten. Read the lines around
each marker with your own tools and compare them with the record:

- **the same knowledge, reworded** (wording, notation, examples or context
  changed, the meaning did not): relocate to the new passage, which replaces
  the Evidence with its text:

  ```sh
  kgd relocate UID - <<'PASSAGE'
  THE PASSAGE AS THE SOURCE NOW WRITES IT
  PASSAGE
  ```

  Pass `--source REL` (the base-relative path) when the passage occurs more
  than once.
- **a changed meaning**: draft a replacement under the record's id from the
  new passage, per the compile Skill's conflict rules, and report it; the
  owner accepts it.

When the owner pasted the passage themselves, relocate to it the same way.

## 4. Make unmarked, lost knowledge foreign

`kgd relocate UID` refuses with `no marker names the record` when the record
has no marker. Look for its passage once with its most distinctive quote:

```sh
kgd find "A DISTINCTIVE PHRASE OF ITS EVIDENCE" --limit 5
```

When it is found, relocate to it as in step 3. When it is not, the knowledge
has left the owner's files: find the page that states it, preferring the
originating work (its arXiv or publisher page), then an encyclopedia article,
read it with your web tools, and make the record foreign with that page's own
text as its Evidence:

```sh
kgd relocate UID --url https://THE-PAGE - <<'PASSAGE'
THE PAGE'S OWN SENTENCES THAT STATE IT, VERBATIM
PASSAGE
```

A foreign record takes no marker. If no page states the knowledge, report the
record instead.

## 5. Report

One line per record: its label, old and new `source:lines` (or URL), whether
the Evidence was kept or replaced, and for a move to another base its new uid
and how many files had links rewritten (`relinked`). List the replacement
drafts written and the records left unresolved. `kgd relocate` refreshes the
keyword index when it writes an accepted record; a replaced Evidence waits for
the owner's re-embed like any edit. The owner's request is the consent; never
edit a record by hand.
