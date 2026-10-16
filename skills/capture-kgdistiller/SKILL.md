---
name: capture-kgdistiller
description: Draft one knowledge record the owner selected while reading — a concept, a result the source states, or a relation between records — in a short fixed pass, one batched lookup then one command that writes and checks the draft, citing its source lines verbatim, or a web page's text for a concept the passage borrows, and linking only recorded or drafted concepts. The owner accepts it. Use for requests to capture this concept, remember this definition or result, or update one record; extraction from a whole source belongs to compile-kgdistiller.
---

# Capture one knowledge item as a draft

Draft the item the owner selected while reading as one record in its registered
base, in a short fixed pass: one lookup turn, then one command that writes and
checks the draft, then one line to the owner. Every capture ends in a draft:
the owner reads, revises and accepts it (`kgd accept`, or the Obsidian Capture
view); never run `kgd accept` yourself. Batch every lookup into that one turn
and decide from what it returns; look again only when a result is genuinely
ambiguous. Do not read the whole source, survey related work or resolve
prerequisites recursively.

Match the owner's language. Keep commands, ids, keys and raw errors unchanged.
Paths given to `kgd` may be absolute or relative to the working directory; the
base is the registered root that contains the path.

## 1. Look up, in one turn

Run these together, and read the cited lines of the source with your own tool:

```sh
kgd source SOURCE
kgd resolve "NAME" "OTHER NAME" "CONCEPT" ...
kgd search "DEFINING PHRASE" --limit 5 --no-dense
kgd drafts --name "NAME" --name "CONCEPT" ...
```

- `source` gives the source's base, its `source` path relative to the base
  root (`REL` below: `--source` takes this value, never an absolute path) and
  its document type: `node_kinds`, `relation_kinds` with their roles,
  `epistemic` and the guidance, plus the records and drafts already citing
  the source. An unregistered file is an
  error: report the glob it asks for; do not register it.
- `resolve` takes the item's names, in the source's language and in English,
  and every concept the record will link as a participant or prerequisite.
  Per name it lists the `senses` (records with that name) and `mentions` in
  every base.
- `search` finds the same knowledge point under another name. `--no-dense`
  keeps the lookup free of the embedding model; give it the item's names and
  defining words in both languages, so the name and lexical lanes match
  translations.
- `drafts --name` lists the drafts awaiting the owner under those names, so
  you neither duplicate a draft nor miss one you can link.

If a result reports `lag.changed_files` above 0, run `kgd index --no-embed` and
repeat the lookup once. `lag.unembedded` and `lag.embedding_changed` need
nothing: re-embedding is the owner's call.

## 2. Decide

Compare definitions and conditions, not names. A label names one concept
across every base: when a record or draft has the item's name but means
something else, the item is new and its label takes its source's name,
`Label (source)` (the file's name without its extension, or a web page's last
path segment).

- **new**: no record or draft holds this knowledge point, or only one of
  another meaning with the same name does.
- **unchanged**: a record or draft holds it with the same meaning. Write
  nothing, unless that record's citation no longer verifies and this passage
  states the item: then move the citation here with
  `kgd relocate UID - <<'PASSAGE'` and the selected text (add
  `--source REL` when it occurs elsewhere too), which rewrites only its
  `source`, `lines` and, when needed, Evidence, and moves a record of another
  base into this source's base with every link to it.
- **changed**: a record holds it with a different meaning. Draft its
  replacement from this passage, keeping its id and `understanding`, and say
  whether this definition is more general, corrects the recorded one, or
  conflicts with it (a narrower one included). A definition the recorded one
  explicitly rules out is a conflict even when broader. The owner decides by
  accepting or discarding the draft. A replacement lives in its record's
  base: when the record lies in another base than this source, draft nothing
  and report the proposed replacement instead.
- **borrowed**: the passage only uses the concept, or credits it to other work
  (a citation, "proposed by", "popularized by", "as in"). It is that work's:
  when no record or draft holds it, draft it as **foreign** knowledge from the
  page that originates or explains it (step 3), never as a node of this
  passage.

Before writing, check what the passage owns:

- A node for an object this passage defines or originates, or for a result
  it states: a theorem (any precisely stated result, whatever the source
  calls it), a finding or an example, in the type's node kinds, its roles
  linking every record it is about. "The sum of two subspaces is a subspace"
  is a theorem whose `about` links subspace and sum of subspaces, not an
  implication.
- A relation only for a connection the passage states between records, of a
  kind the type declares, such as an implication between two results or a
  comparison, whose Evidence quote itself states it. A connection you would
  infer across passages or sources is reported, never written.

## 3. Write and check the draft, in one command

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
---
A measure space is a triple $(X,\mathcal M,\mu)$ with a σ-algebra $\mathcal M$ and a measure $\mu$.

## Evidence

> 我们称 \((X,\mathcal{M},\mu)\) 为一个 measure space.
RECORD
kgd drafts --source REL
```

- `ID` is `slug(label)` (lowercase, non-alphanumerics to `-`; append `-2` on a
  collision), or an id from the meaning when the label carries a section,
  figure, table, equation or theorem number. A replacement uses the recorded
  record's id, so its draft file has the accepted record's name; write it
  from a fresh read of that record.
- `source` is relative to the base root, and `lines` lists the cited ranges,
  such as `46-48` or `72, 84`. A **foreign** record's `source` is the web
  address of the page that originates or explains the concept (prefer the
  originating paper, such as its arXiv page, then an encyclopedia article),
  it has no `lines`, and each Evidence quote is copied verbatim from that
  page, which you fetch with your web tools. It lives in the base of the
  source being read and uses a kind of that source's type.
- `kind` is one of the type's kinds. A record adds one key per role its kind
  declares, each a list of values (a relation binds at least two values, and
  a node kind that declares roles links at least one); `requires` lists
  direct prerequisites.
- Every value is a link, `"[[id]]"` or `"[[base:id]]"` across bases, to a
  record or a draft, of any base, that the lookup found. A concept nothing
  records is left out of the values and named in the report, to be captured
  on its own first; a statement node with no value left cannot be drafted
  yet, so report it. Settings and measured values stay in the prose.
- The prose opens with the statement. Each `>` quote under `## Evidence` is
  copied verbatim from the cited lines or page.
- Set `epistemic` only from the type's list, and `understanding: no` only when
  the owner said they do not understand it; it is `yes` otherwise.
- The complete format is
  [references/record-format.md](references/record-format.md).

`kgd drafts --source REL` (for a foreign draft, `--name LABEL`) lists the
draft with its `problems`, what `kgd accept` would refuse. Fix the draft until
they are empty. The owner's capture request is the consent; do not ask again.

## 4. Report

One short line: the classification, the draft's label and uid (and the record
it replaces), the concepts left unlinked because nothing records them, and
anything left to the owner, including connections reported instead of
written. The owner accepts the draft.
