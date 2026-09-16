---
name: distill-paper
description: Explicit command only. Read the full paper, trace architecture, mathematical derivations and citation uses, and batch-link concrete prerequisites to existing knowledge; never imports or starts harvesting.
disable-model-invocation: true
---

# Distill a paper

Run only for `$distill-paper` or `/distill-paper`. Ordinary paper explanation
needs no Skill. Match the user's language; preserve identifiers and raw errors.
Work in the current agent without subagents. Aim to finish within two minutes
by avoiding duplicate source processing, batching lookups, and writing one concise
note. Full-paper coverage and correct prerequisite tracing take priority over time.

## Read the full paper

Prefer **HTML, then LaTeX, then PDF**. Reuse verified source already in context.
For a readable paper HTML URL, run the bundled helper directly, then read its
`source.txt` and metadata in full (or complete section ranges if necessary).
Never use column clipping such as `cut -c` to read formulas or assumptions.
The extractor retains headings, TeX alternatives, citations and
original anchors without asking a model to summarize the page first:

```sh
python3 <skill-directory>/scripts/read_html.py PAPER_HTML_URL --output-dir PACKAGE/html
```

Do not ask WebFetch to write a section guide or paper interpretation before you
write another one. The helper fetches once; if it fails, use the next source format
without retry loops or toolchain installation. Read the whole body, definitions,
derivations/proofs, experiments, discussion, limitations, substantive appendices
and bibliography; follow all active source includes. Do not replace this with an
abstract, selected method sections or a model summary. Inspect architecture figures
when connections or operations are only shown visually; captions alone may not
specify the structure. If any needed source is inaccessible or truncated, identify
the missing part and affected dependencies instead of claiming complete coverage.
Verify the title/version and final URL.

For the LaTeX fallback only, use `prepare_paper.py ARCHIVE --output-dir PACKAGE
--arxiv-url VERSIONED_URL`, then `validate_paper_markdown.py --manifest
PACKAGE/source.json`. HTML/PDF need no TeX manifest. Local lawful downloading and
knowledge extraction are not redistribution; keep source copies local and write
original notes with provenance, not a transcript to publish.

## Trace three source-backed dependency paths

While reading, capture what the paper actually relies on:

| Path | What to trace |
|---|---|
| Architecture | Inputs/representations → connected modules/operators → outputs and objectives. Identify inherited components, their position and interfaces, and what the paper modifies or introduces. A familiar model name does not establish every component as known. |
| Mathematical derivation | For each substantive step, name the definition, identity, theorem, inequality or assumption used, its required conditions and exact use site. Include implicit but identifiable dependencies, marking your reconstruction separately from the author's claim. Do not replace them with broad subjects. |
| Citations | Resolve cited works through the bibliography (title, authors/year, arXiv/DOI when available). Record what is actually borrowed: architecture, operation, mathematical support, inspiration, background or comparison. Match known paper records and the particular reused knowledge; citation alone is not method inheritance. |

For a citation that actually supplies a reused method, architecture component or
mathematical result, search the knowledge base by paper title or
arXiv/DOI, in addition to checking the reused concept. Link any verified paper
record and explain exactly what this paper borrows from it. A missing concept
entry does not establish that the cited paper is unread or unrecorded.
Do not search every reference: background, comparison-only citations and mere
experiment-tool mentions need no paper-record lookup. A known paper does not mean
every method inside it is mastered. Link known parts and explain the changes.
Follow a cited source only when the current paper leaves a needed definition,
condition or borrowed operation unclear; do not recursively read its bibliography
or launch related-work research. Citations used only for comparison remain context,
not prerequisite knowledge or new method nodes.

## Batch the existing-knowledge check

Derive the lookup inventory from these paths after the full reading, retaining
all useful, actually used dependencies and their locations. Elementary steps may
be stated without a separate lookup when appropriate to the user's stated
background (for example, a familiar triangle inequality). This is not a verified
KB match or a claim of whole-subject mastery. Do not start from a title-based
keyword list or drop a meaningful dependency just to meet a query/count budget.
Use source-language terms and useful aliases; for cited papers include their
identity, not just generic method names. Query established vaults directly, or
use `kgdistiller vault list` to locate the registered default when none is known.
Plan for up to 30 concrete lookup terms per paper, including useful rephrasings
and borrowed-paper identities. This is a working budget, not a completeness cap:
technical papers may require more. The helper accepts 30 terms per call; use
smaller batches when useful and further calls for unresolved dependencies.

First search for candidate **titles and short summaries**, without returning every
candidate's full content to the agent:

```sh
python3 <skill-directory>/scripts/lookup.py --vault NAME --vault OTHER \
  --output PACKAGE/knowledge-search-1.json \
  "Concrete source term" "Another prerequisite"
```

Read the saved JSON without `head`/`cut` truncation. Judge each candidate against
the particular paper step. Reject mere word overlap; retain plausible special
cases even when their titles differ. Short summaries are screening evidence,
not verified definitions. If results are noisy or incomplete, search again using
more discriminating terms, synonyms, a mathematical equivalent or paper ID.
Multiple searches are expected when useful; do not stop after one weak result
or repeatedly issue the same uninformative query. A missing/truncated preview
is not evidence of irrelevance; read that candidate if it remains plausible.

Then read only the IDs you selected, separately for each vault:

```sh
python3 <skill-directory>/scripts/lookup.py --vault NAME --read \
  --output PACKAGE/knowledge-read-1.json SELECTED_ID ANOTHER_SELECTED_ID
```

The helper uses public read-only resolve/search APIs for previews and `get` for
selected content. It never automatically reads the first N ranked candidates.
Check the returned definitions, conditions and provenance before accepting a link;
exact names and search scores alone do not establish applicability. Deduplicate
selected IDs within each vault and reuse verified content across follow-up searches.
A matching applicable entry is mastered: link it and its paper use without
reteaching it. Missing is not proof the user does not know it. Never accept an
error, unread candidate, truncated definition or changed generation as a verified
negative/match; only retrieve the specific missing evidence when needed. Describe absence as
not found in this bounded lookup, not absence from an entire subject in the vault.
Do not read raw graph or entry shards or expand into whole-subject inventories.

## Write once, then finish

Save `paper-notes.md` with:

- One short source line: title, paper/version, original URL and any coverage gap.
- One sentence per main section; add a method-subsection locator only if useful.
- Compact architecture, mathematical-dependency and citation-use maps: paper
  component/step → specific prerequisite or cited work → use site → existing link
  or unresolved/new status. If a path is absent, say so rather than inventing it.
  Reuse rows across paths where appropriate; do not restate mastered definitions.
- Paper/version-qualified candidate IDs with the mechanism/definition, essential
  conditions and source locations. Keep derivation/implementation details with
  their owning method rather than duplicating near-identical candidates.

Keep the candidate content precise. Experimental scores, settings and review
judgments are paper data, not knowledge candidates. Distinguish assumptions,
construction choices and proved statements; a related theorem is usable only
under its actual conditions. Source identity and correctness take priority over
hitting the time target. State any real gap briefly instead of hiding it.

In conversation return only the note link, counts and any blocking gap (one or
two lines); do not repeat the note or list every long candidate ID again.
Candidates remain outside the personal graph and are not yet mastered knowledge.
Finish here; only a later explicit `harvest-paper` handles selection and import.
