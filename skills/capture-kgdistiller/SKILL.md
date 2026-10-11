---
name: capture-kgdistiller
description: Save or update one source-backed knowledge record while reading — a node, a relation or an example — in a short fixed pass, one batched lookup then one command that writes, accepts and indexes, citing its source lines verbatim, recording direct pending terms and the owner's stated understanding. Use for requests to capture this concept, remember this definition or result, or update one record; whole-source extraction belongs to compile-knowledge-sheets.
---

# Capture one knowledge item

Save the item the owner selected while reading as one record in its registered
base, in a short fixed pass: one lookup turn, then one command that writes,
accepts and indexes, then one line to the owner. Batch every lookup into that
one turn and decide from what it returns; look again only when a result is
genuinely ambiguous. Do not read the whole source, survey related work or
resolve prerequisites recursively.

Match the owner's language. Keep commands, ids, keys and raw errors unchanged.
Paths given to `kgd` may be absolute or relative to the working directory; the
base is the registered root that contains the path.

## 1. Look up, in one turn

Run these together, and read the cited lines of the source with your own tool:

```sh
kgd sheet SOURCE --json
kgd resolve "NAME" "OTHER NAME" "TERM" ...
kgd search "DEFINING PHRASE" --limit 5 --no-dense
```

- `sheet --json` gives the source's base and document type: `node_kinds`,
  `relation_kinds` with their roles, `epistemic` and the guidance, plus the
  records already citing the source. An unregistered file is an error: report
  the glob it asks for; do not register it.
- `resolve` takes the item's names, in the source's language and in English,
  and every term the record will name as a participant or prerequisite. Per
  term it lists the `senses` (records with that name), `mentions` and
  `pending` uses in every base.
- `search` finds the same knowledge point under another name. `--no-dense`
  keeps the lookup free of the embedding model; give it the item's names and
  defining words in both languages, so the name and lexical lanes match
  translations.

If a result reports `lag.changed_files` above 0 or `lag.embedding_changed`, run
`kgd index` and repeat the lookup once. `lag.unembedded` alone needs nothing.

## 2. Decide

The same name is not the same concept; compare definitions and conditions.

- **new**: no record holds this knowledge point, or only a homonym does.
- **unchanged**: a record holds it with the same meaning. Write nothing, unless
  that record's citation no longer verifies and this passage states the item:
  then move its `source`, `lines` and Evidence here.
- **superseding**: a record holds it with a different meaning, and this
  passage's definition is more general or corrects it. Overwrite that record
  in place, keeping its id and `understanding`. A definition the recorded one
  explicitly rules out is conflicting even when broader.
- **conflicting**: any other changed meaning, a narrower one included. Show
  the owner both definitions with their source lines and write nothing.

Before writing, check what the passage owns:

- A node only for a noun-like object this passage defines or originates, or a
  theorem it states precisely. A concept it credits to other work (a citation,
  "proposed by", "popularized by", "as in") is that work's: link the record
  `resolve` found, otherwise keep it as a plain pending value.
- Any other statement that can be true or false, such as a result, comparison
  or finding, is a relation whose Evidence quote itself states it. A
  connection you would infer across passages or sources is reported, never
  written.

## 3. Write, accept and index, in one command

For a new item, write the draft and admit it in one shell command:

```sh
mkdir -p ROOT/.knowledge/drafts && cat > ROOT/.knowledge/drafts/ID.md <<'RECORD'
---
label: Measure space
kind: definition
aliases:
  - 测度空间
source: notes/math/measure-theory/chapters/01-sigma-algebra.tex
lines: 206-224
requires:
  - "[[sigma-algebra]]"
  - measurable space
---
A measure space is a triple $(X,\mathcal M,\mu)$ with a σ-algebra $\mathcal M$ and a measure $\mu$.

## Evidence

> 我们称 \((X,\mathcal{M},\mu)\) 为一个 measure space.
RECORD
kgd accept ROOT/.knowledge/drafts/ID.md && kgd index --no-embed
```

- `ID` is `slug(label)` (lowercase, non-alphanumerics to `-`; append `-2` on a
  collision), or an id from the meaning when the label carries a section,
  figure, table, equation or theorem number. `source` is relative to the base
  root, and `lines` lists the cited ranges, such as `46-48` or `72, 84`.
- `kind` is one of the type's kinds. A relation adds one key per role of its
  kind, each a list of values; `requires` lists direct prerequisites.
- Every value is a link, `"[[id]]"` or `"[[base:id]]"` across bases, or a plain
  pending term; settings and measured values stay in the prose.
- The prose opens with the statement. Each `>` quote under `## Evidence` is
  copied verbatim from the cited lines.
- Set `epistemic` only from the type's list and `understanding` only from the
  owner's explicit statement.
- The complete format is
  [references/record-format.md](references/record-format.md); read it when
  `kgd accept` refuses, then fix the draft and run the command again.

For an in-place edit of an accepted record, use an editing tool that refuses
when the file changed since you read it (Claude Code `Edit`, Codex
`apply_patch`), then run `kgd check --base B && kgd index --no-embed`. Never
rename an accepted id; a relocation to another base follows the knowledge
model's relocation procedure. When the source already has a sheet, add
`kgd sheet SOURCE` to the command. The owner's capture request is the
consent; do not ask again.

`--no-embed` indexes the record at once for `resolve`, `get` and the name and
lexical lanes without loading the embedding model; the next full `kgd index`,
which a reader runs when it sees `lag.unembedded`, builds its vector.

## 4. Link pending uses, only when the lookup found them

When step 1's `resolve` listed `pending` uses of the created record's own names
that mean its definition (a homonym stays pending), edit each into a link that
keeps the source's wording, `"[[id|wording]]"` or `"[[base:id|wording]]"`, then
run `kgd check --base B && kgd index --no-embed` for every base you edited.
Otherwise skip this step.

## 5. Report

One short line: the classification and uid, the pending terms recorded and the
pending uses linked, and anything left to the owner, including connections
reported instead of written.
