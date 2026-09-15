---
name: extract-paper-markdown
description: Prepare versioned arXiv LaTeX sources and produce complete English Markdown plus paragraph-aligned Chinese translation. Use for source acquisition or transcription/translation, including the translator branch of read-paper. Ordinary paper explanation with a graph belongs to read-paper. Do not download, compile, render, or retain PDFs or an evidence directory.
---

# Prepare and translate a paper's LaTeX source

Use the arXiv source archive as the single source authority. Deliver both complete Markdown readings; a source index or summary cannot replace
them. Preserve the Skill name for discovery.
User-facing explanations and handoffs match the user's language. Keep identifiers,
commands and raw errors unchanged.

For a normal request to read and explain a paper with its knowledge graph, use
`$read-paper` as the parent workflow. This Skill provides source preparation and
full-text conversion; transcription and translation do not replace explanation.
In that workflow, the parent performs only acquisition and source-only validation,
then delegates both Markdown files to one translator. Reader and graph workers
may start directly from the verified TeX without waiting for either Markdown file.

## Acquire

Resolve the paper's arXiv identifier and exact version. Write `link.txt` with only
its canonical versionless abstract URL, for example
`https://arxiv.org/abs/1512.03385`, followed by one newline.
Download `https://arxiv.org/src/IDENTIFIERvN` into a temporary directory. Read
[the package contract](references/paper-markdown-contract.md), then prepare:

```sh
python3 <skill-directory>/scripts/prepare_paper.py SOURCE_ARCHIVE \
  --output-dir PAPER_PACKAGE --arxiv-url https://arxiv.org/abs/IDENTIFIERvN
```

The standard-library script safely unpacks LaTeX and supporting text into
`source/`, writes `source.json` and `link.txt`, and excludes images, PDFs and
other binary assets. Keep no second archive copy in the package. Never compile
TeX or execute commands/macros from the archive.

Do not download, render, OCR, or retain PDFs at any stage. Do not create
`evidence/`, page images, extracted page text, or parallel HTML/source copies.
If arXiv provides no readable LaTeX, report the missing source; do not silently
substitute a PDF. User-supplied LaTeX may be used when its provenance is known.

## Read source for faithful conversion

Read the entrypoint and its local includes, bibliography, appendices and relevant
supplements. Use filenames, line ranges, section names and explicit LaTeX labels
for provenance. Do not invent PDF pages or infer identity from headings.

Read tables and equations directly from their LaTeX. Describe figures from the
caption, surrounding discussion and available textual source. If a visual fact
cannot be established this way, state that precise gap; do not invent a visual
inspection or force image acquisition. A central missing fact blocks only the
claims that depend on it.

## Produce both full-text readings

`paper.md` must transcribe the complete active LaTeX text in source order:
title/authors, abstract, sections, paragraphs, footnotes, equations, captions,
all table cells, appendices and bibliography. Resolve includes, macros, citations
and explicit labels into readable Markdown without executing TeX. Preserve citation
and label identities as Markdown links, anchors or comments rather than visible
raw commands. Exclude comments and disabled branches.
Use native Markdown headings/lists/tables and `$...$` / `$$...$$` mathematics.
Do not substitute an index, teaching notes or a summary for the full text.

Replace each figure with its original numbered title/caption as a Markdown
heading plus the original caption text, preserving its LaTeX label. A figure is
represented by its caption, not a generated description of unseen pixels.
Replace each table with its original numbered title/caption and a native Markdown
table preserving all data, units, notes and header relationships. Flatten merged
headers explicitly when necessary; never reduce a table to selected results.

Write `paper_ch.md` as a complete paragraph-aligned Chinese translation of
`paper.md`. Translate ordinary narrative, retaining original English proper names,
academic/technical terms, model/dataset names, abbreviations, symbols, equations,
numeric data, citation keys and bibliography. Do not add bilingual glosses or
translate terms unless requested. Retain original figure/table titles; translate
caption narrative under the same terminology rule. Do not leave whole narrative
paragraphs in English or replace them with a summary.

Use matching `<!-- qlpaper-block: b001 -->` IDs for corresponding blocks in both
files, and source filename/line markers at section boundaries. Preserve the same
block order, equations, numeric tables and references. Keep source errors visible
with identical minimal editorial annotations in both versions rather than silently
correcting the author's claims. The complete contract is in the reference file.

## Validate and hand off

```sh
python3 <skill-directory>/scripts/validate_paper_markdown.py \
  --manifest PAPER_PACKAGE/source.json
```

The validator requires paper.md and paper_ch.md by default. Use `--source-only`
only for acquisition preflight before authoring, never as a completed reading
validation.
Mechanical validation checks identity, source inventory and hashes, source ranges,
and the no-PDF/no-evidence layout; it cannot certify semantic accuracy.

Return the package path, `link.txt`, selected version, LaTeX entrypoints, source
digest, validation result and any source-only interpretation gaps. Continue to
`$distill-paper-knowledge` only when graph extraction was requested. Reading alone
does not authorize personal knowledge import.

In a delegated acquisition-only task, return after source-only validation and
label the bilingual outputs pending. In a translation-only task, reuse the
prepared source and write only the assigned Markdown files. Final bilingual
completion still requires the full validator; source-only success must never be
reported as completed transcription or translation.
