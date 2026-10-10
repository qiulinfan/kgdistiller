---
name: compile-knowledge-sheets
description: Extract source-backed knowledge records from a bounded part or the whole of a registered document, guided by its user-registered document type, as drafts for owner review, then generate the source's def/pending sheet. Use for requested whole-source or partial distillation of mathematical notes, computer-science notes, papers, blogs or project documents, including when the user asks to file or ingest a document into their kgdistiller (kgd/kgdt) knowledge base; one item while reading belongs to capture-kgdistiller.
---

# Compile knowledge sheets

Turn a bounded scope of one registered source into draft records the owner
reviews, and generate the source's sheet so the owner can tick drafts for
acceptance. Accepted records are never written by this Skill; changes to them
are proposed in the report.

Match the owner's language. Keep commands, ids, keys and raw errors unchanged.

Read [references/record-format.md](references/record-format.md) before writing
drafts and [references/sheet-contract.md](references/sheet-contract.md) before
generating a sheet. Paths given to `kgd` may be absolute or relative to the
working directory; the base is the registered root that contains the path.

## 1. Set the scope

Establish the source file(s) and the requested coverage: selected passages,
chapters or the whole file. Full-source distillation needs an explicit request
and is usual for the owner's own notes or familiar material; new reading
normally uses `$capture-kgdistiller`. For a whole-source request, read proofs,
examples and appendices too. Report actual coverage; a partial pass never
claims completeness.

## 2. Read the profile and inventory

```sh
kgd sheet SOURCE --json
```

It returns the base, the document type with its `node_kinds`,
`relation_kinds` (kinds with ordered roles), `epistemic` list and guidance,
`line_count`, and the accepted records, drafts and pending terms that already
cite this source. Follow the type's guidance; do not infer kinds from the file
extension or impose a built-in catalog. Read the source text with your own
tools. An unregistered file is an error that asks for a glob in
`$KGDISTILLER_HOME/config.json`: propose the glob and its type to the owner
instead of registering it.

## 3. Establish identity

Build the candidate batch (labels, aliases, line ranges, kinds, participants)
first, then resolve it:

```sh
kgd resolve "LABEL" "ALIAS" ...
kgd search "LABEL OR DEFINING PHRASE" --base B
kgd get UID
```

Compare definitions and conditions yourself. The same name is not the same
concept: a homonym gets its own record, and an existing record with the same
meaning is reused by linking it, not duplicated. Leave ambiguous candidates out
and list them. If `lag.changed_files` is above 0, run `kgd index` first.

## 4. Write drafts

Write one `<root>/.knowledge/drafts/<id>.md` per new record in scope:

- nodes for the kinds the type lists as node kinds (typically definitions,
  axioms and named, precisely stated results);
- relations for statements connecting records, with every participant bound
  to a declared role; self-relations and relations about relations are
  allowed; examples and applications are relations of the kind the type
  registers for them;
- `requires` for direct understanding prerequisites; unexplained terms as
  plain pending values, one level deep;
- Evidence quotes copied verbatim from the cited lines.

Drafts may link accepted records and each other. Never set `understanding`
without an explicit owner statement.

## 5. Check

```sh
kgd check --base B
```

Every draft you wrote must pass: no `errors` entry may name it, and its
evidence must be fresh. Fix and re-check. Pre-existing findings on other files
are reported, not repaired.

## 6. Generate the sheet

```sh
kgd sheet SOURCE
```

It writes `<root>/.knowledge/sheets/<source path>.md`, keeping the ticks of
drafts that still exist.

## 7. Stop and report

Stop here. The owner reviews the drafts in Obsidian, ticks the rows to accept
and runs `$harvest-kgdistiller` (or `kgd accept` on chosen drafts).

Report the scope and actual coverage, the drafts written, reused records,
ambiguous candidates left out, and the sheet path. List separately every change
you would make to accepted records — edits, deletions, node-to-relation
conversions — with the reason and source lines. Apply such a change only when
the owner asks: edit the record in place with a stale-read-safe tool, run
`kgd check --base B`, then `kgd index`.
