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
another paper or base is a separate record. Decide whether the item is new or
an existing record. If `lag.changed_files` is above 0, run `kgd index` first so
the comparison sees current files.

## 3. Write

**New item.** Write `<root>/.knowledge/drafts/<id>.md` in the record format,
with `id = slug(label)` (append `-2`, `-3`, … on a collision), then accept it:

```sh
kgd accept <root>/.knowledge/drafts/<id>.md
```

The owner's capture request is the consent; do not ask again. Copy the Evidence
quotes verbatim from the cited lines. Link existing records by id (`"[[id]]"`,
or `"[[base:id]]"` for another base); a link to a draft you also wrote needs
both drafts in the same `accept`. A refusal lists each problem and writes
nothing: fix the draft and accept again.

**Existing item.** Edit `<root>/.knowledge/entries/<id>.md` in place with an
editing tool that refuses when the file changed since you read it (Claude Code
`Edit`, Codex `apply_patch`); never rewrite a whole record from an earlier
read. Then:

```sh
kgd check --base B
```

Fix every `errors` item that names your file. `moved` evidence after a source
edit is repaired with `kgd check --base B --fix-lines`; `stale` evidence needs
the quote re-copied from the source.

## 4. Pending terms, one level

A term the passage uses without explaining becomes a plain value (not a link)
in `requires` or in the role where it occurs. Do not invent its definition or
a target, and do not resolve the next level now. When the owner later studies
it, that capture records its own direct gaps.

## 5. Understanding

Set `understanding` only from the owner's explicit statement in this
conversation (`unknown`, `not-yet-understood`, `understood`). Saving, reading
or finding a definition never establishes it. Preserve an existing value
unless the owner changes it.

## 6. Index

```sh
kgd index
```

Every knowledge write ends with this, so search sees the change.

## 7. Report

Return the uid(s), the accept receipt (`created`, `understanding_set`) or the
`check` result for an in-place edit, the pending terms recorded, and the
`index` result. Name any refusal and what was left unwritten. If the owner
wants a sheet for this source, `kgd sheet SOURCE` regenerates it.
