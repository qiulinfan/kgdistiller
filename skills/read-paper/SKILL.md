---
name: read-paper
description: Read and explain a research paper through parallel translation, independent interpretation, and paper-scoped graph extraction with concrete prerequisite links to the personal knowledge base. The graph worker defaults to lookup, treats accurately matched entries as mastered, and explains only specific gaps; the normal paper explanation remains primary.
---

# Read the paper first

The main deliverable is a clear explanation of the author's ideas, terminology,
methods and argument, at least as useful as an ordinary reading without this
workflow. The graph and translation supplement that explanation. Do not lead
with workflow receipts, node counts, or what the learner supposedly knows.

Match the user's language and requested depth. Explain the author's unfamiliar
terms in context. In the graph branch, follow the user's convention: an accurately
matched, applicable knowledge entry is already mastered and needs only a link
and its role in this paper. Explain unmatched specific dependencies as needed.
Never infer mastery of an entire subject from individual matches or demand a
knowledge inventory before reading.
An explicit request for direct reading without Skills takes precedence: answer
normally rather than starting this workflow.

## 1. Prepare one immutable source package

Use the acquisition stage of `$extract-paper-markdown` to obtain the exact arXiv
LaTeX archive and prepare `source/`, `source.json`, and `link.txt`. Reuse a valid
source package when one exists. Check its source inventory with:

```sh
python3 <extract-paper-markdown-dir>/scripts/validate_paper_markdown.py \
  --manifest PAPER_PACKAGE/source.json --source-only
```

This is acquisition preflight, not completed bilingual-reading validation.
Do not transcribe or translate the whole paper in the parent before delegation.
Take entrypoint paths from `source.json`, not the uploaded archive filename.
Read enough source to identify includes and give workers the
complete package, not a parent-written summary. Preserve the original source;
do not execute TeX, acquire PDFs, or create duplicate evidence directories.

## 2. Launch three independent workers

Read [the worker handoffs](references/worker-handoffs.md). Launch all three as
soon as source preflight passes, before awaiting any one result. Use fresh
contexts and disjoint write ownership. Do not fork the parent's conversation
into the independent reader. Each worker reads the same original TeX directly;
neither reader nor graph worker waits for the translation.

| Worker | Task | Sole output ownership |
|---|---|---|
| Translator | Transcribe and translate the complete source using `$extract-paper-markdown` | `paper.md`, `paper_ch.md` |
| Independent reader | Read normally without paper/graph Skills or personal-graph context | `reading.md` |
| Graph extractor | Use `$distill-paper-knowledge` to extract and link methods and exact prerequisites | `knowledge/`, including graph, entries and source-backed links |

Use the runtime's isolated subagent mechanism. The independent reader uses the
`paper-reader` preset when available, otherwise a fresh general-purpose worker
with the minimal prompt in the handoff reference. Never use `paper-distiller`
for this role: its graph instructions would contaminate ordinary interpretation.
Do not give the reader graph schemas, node-admission rules, translation contracts,
learner-match reports, or instructions to execute this Skill. The translator and
graph extractor receive only their relevant Skill and source/output paths.

The graph extractor writes paper-scoped methods and mechanisms plus readable
entries and queries the personal knowledge base by default. Pass the established
knowledge-project target or registered default to this worker only. Trace specific
operations and derivation steps to exact prerequisites, verify their applicable
formulations in the knowledge base, and record source-backed links. Never replace
this work with broad subject prerequisites or claims that the user knows them.
Honor an explicit request to omit personal lookup. Lookup failure stays local to
the graph branch and does not delay the reader. Do not automatically run
`$trace-concept-lineage` or spawn a
second layer of dossier workers for this default reading workflow.

If concurrency slots are limited, start the reader first and use available slots
for the other branches. If isolated subagents are unavailable, explain that
limitation and give a normal source-led reading first, then complete translation
and graph work as possible. Do not claim independent parallel execution occurred.

## 3. Integrate without weakening the reading

Read the independent reader's full answer. Check material claims against the
source and correct actual mistakes, preserving the explanation's substance,
examples and reasoning. Do not replace it with a graph summary, a list of unknown
terms, or an abbreviated abstract. The reader's ordinary explanation may discuss
experiments and limitations even though those do not become knowledge nodes.

As soon as the explanation is ready, present it while the other branches finish
if useful. Continue the authorized work; do not let translation or graph tooling
delay a valid explanation. Keep branch failures local: report missing translation
or graph artifacts precisely and retry within that branch without discarding the
reading or silently claiming all deliverables are complete.

Before final delivery, check:

- the reading explains the paper on its own, without opening the graph;
- no substantive part of the reader's explanation was lost during integration;
- full bilingual validation passes after the translator finishes;
- the graph snapshot validates and each method/prerequisite has either a verified
  existing-entry link with its paper use or a readable explanation of the specific gap;
- specific dependency uses have source locations, applicable conditions and
  verified knowledge-base handles, or an explicit unresolved/lookup-blocked state;
- all workers used the same source revision and respected file ownership.

The parent owns any integration corrections after the affected worker finishes.
Return the substantive explanation in the conversation first, followed by the
linked method graph and entries, then the bilingual source links. A file link
alone is not delivery of the explanation unless the user asked for file-only
output. Keep validation details short and disclose any incomplete branch.
Follow-up questions continue ordinary source-led discussion; do not relaunch the
whole pipeline or conduct a knowledge audit for every question.

Reading never authorizes importing into a personal graph or submitting a review.
