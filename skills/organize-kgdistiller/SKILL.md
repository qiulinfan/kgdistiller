---
name: organize-kgdistiller
description: Automatically compile and maintain a semantic topic hierarchy over accepted kgdistiller knowledge, with readable routing summaries and multiple paths to the same UID. Use after capture or harvest, accepted-record edits, corpus growth, or a request to reorganize navigation; preserve scientific records and owner corrections. Topic names, depth and branching are generated here, never supplied as a manual catalog.
---

# Organize accepted knowledge

Build useful entrances into the accepted knowledge graph. Choose topics,
summaries and organization links from meaning; the deterministic helper checks
and saves those decisions. Match the owner's language, keeping commands, UIDs,
structured keys and raw errors unchanged.

This phase is part of authorized ingestion. The owner maintains a finite set
of extraction profiles, not topic names or a growing directory. Do not ask for
approval of routine topic creation, split, merge, reparent or retirement. The
scientific capture/compile/harvest admission boundary remains in force.

Read [references/proposal.md](references/proposal.md) for the executable
proposal and stale-context contract. The helper is
[scripts/organize.py](scripts/organize.py); use the Python environment in which
`kgd` is installed. The agent calls CLI commands; no MCP server or model is
needed by the helper or by `kgd index`.

## Read the affected knowledge

After successful scientific admission or an authorized accepted-record edit,
identify the changed accepted UIDs and their bases. Run `kgd index` so identity
lookups are current. Discover the existing global entrances first. Select the
guide that owns the relevant topic branch as the write scope, even when the
new scientific UIDs live in another base; do not mechanically create a
separate directory in each scientific base. Initialize a registered ordinary
UTF-8 navigation source when needed, then read context:

```sh
kgd navigate --limit 40
python ORGANIZE_SCRIPT init --base B
python ORGANIZE_SCRIPT context --base B --changed B:UID ... > CONTEXT_FILE
kgd get UID ...
kgd neighbors UID ... --dir both --depth 2
```

`ORGANIZE_SCRIPT` is this Skill's `scripts/organize.py`. Keep context and
proposal files in caller-owned temporary storage outside `.knowledge/` and
the home; do not persist a catalog, plan or identity store. On first adoption
or explicit global reorganization omit `--changed`, requiring routes to all
accepted scientific records of the selected base. Context includes a compact
global inventory, current topics/organization links, required coverage and
protected owner-edited records. Read full records only where semantics or
conditions are unclear. Large inventories can be read in bounded portions of
the temporary file; knowledge boundaries are not those portions.
For `scope: global`, cover all accepted scientific records of all available
bases. One proposal may author the shared topic DAG in the selected base while
linking scientific leaves in every vault; never use storage bases as mandatory
semantic partitions.
`--changed` can contain accepted scientific UIDs from any base, for example
`context --base math --changed ml:new-method`, so ML admission can immediately
join an existing shared Math entrance in the same automatic organization pass.

For a large initial corpus, stage meaningful groups in multiple local
proposals, each with only that batch's changed UIDs. Preserve stable topics
and summaries, and omit unchanged records. Several organization records can
share a parent, letting a new route add children without reprinting every old
member. Reread context between applies. Finish with a fresh context using
`--changed` with no UIDs and an empty-upsert `scope: global` proposal: the
helper audits cumulative coverage without requiring a giant model output.
When navigation edits span several owner guides, apply each guide's patch
sequentially and reread before linking foreign newly accepted topics.

`init` copies the optional finite navigation profile into `types/` only when
this Skill is invoked, and registers its generated guide in the selected
base's existing `sources` map. It leaves other source profiles and `embedding`
alone. A colliding source or broad glob is reported; choose another visible
`--source` path/extension within the same base and use it consistently. Never
overwrite an unrelated document or silently change extraction policy.

The guide explicitly attributes classifications to the navigation compiler.
Its topic and organization records cite and quote that guide. Research papers
provide evidence for scientific records, not for freely generated directory
membership. A guide is ordinary registered owner data outside `.knowledge/`;
the helper appends explanatory decisions without parsing its syntax or
rewriting existing source text. It stores no separate tree.

## Decide the semantic changes

Prefer the affected branches and their neighboring topics. Expand to global
reorganization when new knowledge or citation links show that the existing
organization fails across branches. Inspect cross-base topics too; a route
can link a topic or the same scientific UID in another registered base.

Keep stable topic UIDs where their meaning remains useful. Same names alone
do not establish identity. Give each topic a short summary of the questions,
concepts and distinctions it covers so a query agent can choose its next step.
Do not imply scientific entailment through an organization relation.

Use routing value and reading cost together:

- Split an overloaded branch when meaningful subtopics help distinguish the
  next step. Choose as many branches as useful, rather than mechanically
  bisecting or enforcing fixed depth.
- Merge redundant topics or compress a single-child chain whose labels and
  summaries add no new routing information. Preserve useful distinctions,
  aliases and leaf coverage; retire obsolete navigation UIDs only.
- Reparent misleading routes, or add a second parent when a concept has two
  natural uses. Point both routes to the same leaf UID.
- Let new scientific links reveal shared entrances. Preserve those original
  links, including independent scientific relation records as leaves.

Neither an empty outline nor an ever-growing chain is a successful directory.
Avoid groups named only by position or artificial balance. A small corpus
may need only a short path; growth can justify extra depth and branching.
Do not use benchmark queries, gold UIDs or evaluation outcomes to compile a
held-out hierarchy.

`protected` navigation records contain owner changes beyond the helper's
authored form. Preserve them exactly and organize around them. The helper
refuses to edit or retire them. Also preserve every scientific UID, body,
Evidence quote, line range, understanding value and scientific relation.
Pending terms stay pending; drafts never become navigation leaves.

## Save and verify

Write an upsert/retirement proposal using the profile's roles and existing
base-qualified UIDs, then run:

```sh
python ORGANIZE_SCRIPT apply --base B --context CONTEXT_FILE --proposal PROPOSAL_FILE --dry-run
python ORGANIZE_SCRIPT apply --base B --context CONTEXT_FILE --proposal PROPOSAL_FILE
kgd navigate --base B --limit 40
```

The helper checks the projected DAG, linked participants, changed-leaf
coverage, preservation of previously reachable scientific leaves, and exact
previous text/file stats. New objects go through drafts plus `kgd accept`;
existing compiler-authored navigation records use exact-text guarded edits.
Only owned navigation records can be retired. Incoming scientific links to a
retired record cause refusal. An unchanged upsert keeps its original bytes
and source lines. It runs `kgd check --base B` and ends with `kgd index`.

A refusal before writes leaves the corpus intact. A stale context requires a
fresh read and revised proposal. If CLI admission or a later check fails,
report the completed phase and leave prepared drafts/source text recoverable;
do not undo a concurrent owner edit. Fix only the navigation files in scope,
check again and finish with `kgd index`. Missing embedding dependencies leave
the lexical index available; report the actual CLI error without changing
the configured model.

Report changed navigation UIDs, local/global scope and its reason, protected
records, coverage/cycle diagnostics, and the check/index results. Record model
and tool costs in the consuming experiment or caller's transient output,
never in the product or `.knowledge/`. Do not claim a recall improvement
without frozen independent evaluation.
