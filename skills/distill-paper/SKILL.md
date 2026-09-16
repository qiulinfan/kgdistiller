---
name: distill-paper
description: Explicit command only. Read paper HTML directly, batch-link existing knowledge, and save concise section guidance and source-backed knowledge candidates; never imports or starts harvesting.
disable-model-invocation: true
---

# Distill a paper

Run only for `$distill-paper` or `/distill-paper`. Ordinary paper explanation
needs no Skill. Match the user's language; preserve identifiers and raw errors.
Work in the current agent without subagents. Aim to finish within two minutes
with one source read, one batched lookup, and one concise note.

## Read the source once

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
without retry loops or toolchain installation. Read enough original content to
verify mechanisms and conditions, and respect reported formula/visual gaps.
Verify the title/version and final URL; an abstract alone is not the paper.

For the LaTeX fallback only, use `prepare_paper.py ARCHIVE --output-dir PACKAGE
--arxiv-url VERSIONED_URL`, then `validate_paper_markdown.py --manifest
PACKAGE/source.json`. HTML/PDF need no TeX manifest. Local lawful downloading and
knowledge extraction are not redistribution; keep source copies local and write
original notes with provenance, not a transcript to publish.

## Batch the existing-knowledge check

Choose the lookup batch **after reading the source operations and derivations**,
not in parallel with source acquisition based on the title. Include the concrete
operators, definitions or theorems those steps use, not just architecture names.
Pick source-language terms and a useful alias in the same batch rather than
doing Chinese and then English passes. Use established vaults without rediscovering
them. If none is established, `kgdistiller vault list` gives the registered default.

```sh
python3 <skill-directory>/scripts/lookup.py --vault NAME --vault OTHER \
  --output PACKAGE/knowledge-lookup.json \
  "Concrete source term" "Another prerequisite"
```

Read the saved lookup JSON without `head`/`cut` truncation. The helper uses only
public read-only queries: batch resolve, one bounded search
for unresolved terms, and deduplicated content retrieval. It returns identities,
plausible entries, provenance and real gaps together. Read definitions and
conditions to decide applicability; scores or names alone do not establish it.
A matching applicable entry is mastered: link it and its paper use without
reteaching it. Missing is not proof the user does not know it. Never accept an
error, unread candidate, truncated definition or changed generation as a verified
negative/match;
only retrieve the specific missing evidence when needed. Describe absence as
not found in this bounded lookup, not absence from an entire subject in the vault. Do not read raw graph
or entry shards or expand into whole-subject inventories.

## Write once, then finish

Save `paper-notes.md` with:

- One short source line: title, paper/version and original URL.
- One sentence per main section; add a method-subsection locator only if useful.
- Existing-entry links and their concrete uses; no repeated definitions, internal
  paths, namespace explanations or per-term missing-status narrative.
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
