---
name: capture-kgdistiller
description: Save or update one source-backed knowledge record while reading — a node, a relation or an example — in a short fixed pass, one batched lookup then one command that writes, accepts and indexes, citing its source lines verbatim, recording direct pending terms and the owner's stated understanding. Use for requests to capture this concept, remember this definition or result, or update one record; whole-source extraction belongs to compile-knowledge-sheets.
---

# Capture one knowledge item

Save the item the owner selected while reading as one record in its registered
base, in a short fixed pass: one lookup turn, then one command that writes,
accepts and indexes. Batch every lookup into that one turn, decide from what
it returns, and look again only when a result is genuinely ambiguous. Do not
read the whole source, survey related work or resolve prerequisites
recursively.

Match the owner's language. Keep commands, ids, keys and raw errors unchanged.
Paths given to `kgd` may be absolute or relative to the working directory; the
base is the registered root that contains the path.

## 1. Look up, in one turn

Run these together:

```sh
kgd sheet SOURCE --json
kgd resolve "LABEL" "ALIAS" "TERM" ...
kgd search "DEFINING PHRASE" --limit 5
```

and read [references/record-format.md](references/record-format.md) and the
cited passage of the source with your own tools.

- `sheet --json` gives the source's base, document type (`node_kinds`,
  `relation_kinds` with their roles, `epistemic`, guidance) and the records
  already citing it. An unregistered file is an error: report the glob it asks
  for; do not register it.
- `resolve` takes the item's label and aliases and every term the record will
  name as a participant or in `requires`. Per term it lists the `senses`
  (records with that name), `mentions` and `pending` uses across every base.
- `search` finds the same knowledge point under another name or language.

If a result reports `lag.changed_files` above 0 or `lag.embedding_changed`, run
`kgd index` and repeat the lookup once. `lag.unembedded` alone needs nothing:
records captured since the last full index lack only their vector, and
`resolve` and the name and lexical lanes still find them.

## 2. Decide

Compare definitions and conditions; the same name is not the same concept.
Classify the item by the knowledge model's conflict rules:

- **new**: no record holds this knowledge point, or only a homonym does.
  Its draft is `<root>/.knowledge/drafts/<id>.md` in the record format, with
  `id = slug(label)` (append `-2`, `-3`, … on a collision), or an id from the
  meaning when the label carries a section, figure, table, equation or theorem
  number.
- **unchanged**: a record holds it with the same meaning. Write nothing,
  unless that record's citation no longer verifies and this passage states
  the item: then move its `source`, `lines` and Evidence here.
- **superseding**: a record holds it with a different meaning, and this
  passage's definition is more general or corrects it. Overwrite that record
  in place, keeping its id and `understanding`. A definition the recorded one
  explicitly rules out is conflicting even when broader.
- **conflicting**: any other changed meaning, a narrower one included. Show
  the owner both definitions with their source lines and write nothing until
  they answer.

Writing rules:

- A node only for a noun-like object the source defines or a theorem it states
  precisely; any other statement that can be true or false is a relation whose
  Evidence quote itself states it. A connection you would infer across
  passages or sources is reported, never written.
- A concept this source only uses, even one it explains, is not a record here:
  link the defining record that `resolve` found (`"[[id]]"`, or
  `"[[base:id]]"` across bases), otherwise keep it as a plain pending value.
  One level only; never invent a stand-in definition.
- Role values are links or pending concepts; settings and measured values go
  in the prose. Copy Evidence quotes verbatim from the cited lines.
- Set `understanding` only from the owner's explicit statement; keep an
  existing value unless the owner changes it.
- Edit an accepted record only with an editing tool that refuses when the file
  changed since you read it (Claude Code `Edit`, Codex `apply_patch`). Never
  rename an accepted id; a relocation to another base follows the knowledge
  model's relocation procedure.

## 3. Write, accept and index, in one command

For a new item, write the draft and admit it in the same shell command:

```sh
mkdir -p <root>/.knowledge/drafts && cat > <root>/.knowledge/drafts/<id>.md <<'RECORD'
...the record...
RECORD
kgd accept <root>/.knowledge/drafts/<id>.md && kgd index --no-embed
```

For an in-place edit, edit with the editing tool, then run
`kgd check --base B && kgd index --no-embed`. `--no-embed` indexes the record
for `resolve`, `get` and the name and lexical lanes at once, without loading
the embedding model; its vector is built by the next full `kgd index`, which a
reader runs when it sees `lag.unembedded` above 0. When the source already has a sheet, add
`kgd sheet SOURCE` to the same command so the sheet shows the change. The
owner's capture request is the consent; do not ask again. A refusal lists each
problem and writes nothing: fix the draft and run the command again.

## 4. Link pending uses, only when the lookup found them

When step 1's `resolve` listed `pending` uses of the created record's own label
or aliases that mean its definition (a homonym stays pending), edit each into
a link that keeps the source's wording, `"[[id|wording]]"` or
`"[[base:id|wording]]"`, then run `kgd check --base B && kgd index --no-embed`
for every base you edited, with `kgd sheet SOURCE` for each edited record's
source that has a sheet. Otherwise skip this step.

## 5. Report

One short answer: the classification, the uid, the pending terms recorded,
the pending uses linked, and anything refused or left to the owner, including
connections reported instead of written.
