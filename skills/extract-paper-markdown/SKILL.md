---
name: extract-paper-markdown
description: Acquire the canonical full text for a research paper from a landing page, DOI, title, HTML article, or PDF and turn it into a complete, source-traceable Markdown package. Preserve prose, equations, claims, appendices, and supplements; summarize figures and complex tables as compact semantic object records after targeted multimodal inspection; and never reconstruct, embed, or visually reproduce paper images. Use when a downstream Agent needs paper text in Markdown for summarization, concept extraction, claim analysis, retrieval, or knowledge-graph construction without paying the cost of a LaTeX transcription or visual facsimile.
---

# Extract a paper into Markdown

Produce a semantic paper transcription, not a visual clone. Own acquisition,
text recovery, source mapping, and figure/table summaries. Stop before concept or
knowledge-graph extraction; `$distill-paper-knowledge` owns that next stage.

## Resolve the authoritative paper

For a URL, DOI, title, or viewer, find a lawful complete version. Prefer the
publisher or proceedings page, then arXiv, then an author or institutional
repository. Record the landing URL, final PDF URL, identifier, version/date,
authors, page count, supplements, access date, and source hash.

Never substitute an abstract, search snippet, viewer shell, related paper, or
access-control bypass for the paper. A complete official HTML, Markdown, or
source archive may provide cleaner text, but retain the matching PDF as the
page and visual baseline whenever one exists.

## Prepare the evidence package

Read and follow
[references/paper-markdown-contract.md](references/paper-markdown-contract.md).
For a local PDF, start in an empty ignored or temporary directory:

```sh
python3 <skill-directory>/scripts/prepare_paper.py PAPER.pdf \
  --output-dir PAPER_PACKAGE --landing-url LANDING_URL \
  --pdf-url PDF_URL --identifier DOI_OR_ARXIV
```

The script extracts every page's text, detects likely figure/table captions and
low-text failures, renders only pages needing visual interpretation, and creates
`paper.md`, `source.json`, and immutable source evidence. It does not OCR by
default and does not render ordinary text-only pages.

If the authoritative input is complete HTML without a PDF, follow the same
Markdown and manifest contract, use stable section/object anchors instead of
invented page numbers, and report that page-grounded validation is unavailable.

## Write the semantic Markdown

Reconstruct section order, prose, native Markdown lists/tables, citations,
footnotes, equations as `$...$` or `$$...$$`, theorem/result scope, limitations,
appendices, and any supplement needed by the argument. Preserve referenced
equation, figure, and table labels. Normalize layout-only line breaks and omit
headers, footers, decorative chrome, and publisher styling.

Keep one source marker per PDF page. For each detected figure or table:

1. inspect only its targeted page render and, when necessary, an adjacent page;
2. add one object marker and a compact record containing `summary`, `paper-use`,
   and `uncertainty`;
3. capture what the object communicates, not its pixels or layout;
4. record unreadable details instead of reconstructing or guessing them.

Treat the script's `detected_object_candidates` as a preflight hint. Reconcile
it against caption numbering, targeted renders, and cross-references; put the
reviewed final inventory in `source.json.object_candidates`, adding missed
objects and excluding false positives without deleting the original detection
audit.

Do not embed images, screenshots, PDFs, base64 data, or local render paths in
`paper.md`. Do not redraw plots, recreate TikZ, transcribe every plotted point,
or make a complex table visually identical. Preserve a small table as Markdown
when exact cells matter; summarize a large table's dimensions, key comparisons,
important values, and conclusion in its object record.

Use OCR only for a flagged page whose semantic text is otherwise missing. It is
evidence, not truth; compare it with the targeted render. A figure with an
adequate semantic summary is complete even though no image appears in Markdown.

## Validate and deliver

For a PDF-backed package, run:

```sh
python3 <skill-directory>/scripts/validate_paper_markdown.py \
  --manifest PAPER_PACKAGE/source.json \
  --markdown PAPER_PACKAGE/paper.md
```

Before delivery, confirm the paper's argument, central equations/results,
limitations, appendices, supplements, and all detected visual objects are
represented. Stop only when the authoritative full text is unavailable or an
unreadable region blocks a core claim; otherwise retain a bounded uncertainty.

Return the package path, provenance, source and Markdown hashes, page and object
counts, visually inspected pages, OCR usage, attachments, and unresolved gaps.
Do not start graph extraction or mutate any personal knowledge store.
