# Lightweight LaTeX paper package

```text
<paper-package>/
├── link.txt             # one versionless https://arxiv.org/abs/IDENTIFIER URL
├── source.json          # small provenance and source-text inventory
├── source/              # original LaTeX and supporting text, once only
│   ├── main.tex
│   └── references.bib
├── reading.md           # independent explanation in the read-paper workflow
├── paper.md             # complete LaTeX -> Markdown transcription
├── paper_ch.md          # aligned Chinese narrative; English academic terms
├── knowledge/           # isolated graph when requested
└── learning/            # concept dossiers and reading route when requested
```

No `evidence/`, PDFs (including figure PDFs), rendered pages, OCR output, duplicate
HTML or retained compressed archive. Fetch the archive temporarily, keep its hash,
and retain only its text source. The prepared subset is for semantic reading,
not a promise that the original paper can be compiled from this subset.

In `$read-paper`, the parent prepares only source/, source.json and link.txt.
After source-only validation, three workers can run concurrently: a translator
owns both Markdown full texts, an independent reader owns reading.md, and a graph
extractor owns knowledge/. Reader and graph extraction consume original TeX and
do not depend on the translation. Full bilingual validation remains a final gate
for the translator's branch, not a gate for beginning ordinary interpretation.

## Manifest

`source.json` uses `qlpaper-latex-source-v1`:

- `identifier`, `version`: exact arXiv paper/version;
- `arxiv_url`: canonical versionless abs URL, identical to `link.txt`;
- `archive_url`: versioned arXiv src URL;
- `archive_sha256`: digest of downloaded archive before filtering;
- `files`: sorted records of `path`, `sha256`, `bytes` for all retained source text;
- `source_sha256`: SHA256 of the UTF-8, sorted-key, compact JSON encoding of `files`;
- `entrypoints`: files containing a LaTeX documentclass/documentstyle declaration;
- `omitted_assets`: paths excluded from the downloaded archive, not their contents.

The source tree's bytes remain unchanged. A changed source requires a rebuilt
manifest, candidate snapshot and fresh alignment; never hand-edit a digest to
make stale graph artifacts pass. Do not change registered personal graphs during
paper package maintenance. Old PDF packages are not accepted by this validator;
convert them only in a user-authorized scope.

## Location evidence

Prefer `source/main.tex`, a bounded line range and `\label{...}` / section name.
Read `\input`, `\include`, bibliography and supplement dependencies rather than
assuming one file is complete. The script discovers document entrypoints, not
all semantic dependencies; the acting Agent reviews that coverage.

Both complete Markdown readings use source markers and identical block IDs:

```markdown
<!-- qlpaper-source: file=source/main.tex; lines=20-35 -->
<!-- qlpaper-block: b001 -->
```

No page-count, page-marker or visual-inspection requirement. Equations, small
important table cells, claims, assumptions and exceptions must remain traceable.
Describe visual content only to the extent supported by captions, surrounding
text or original textual figure source. Record exact missing visual information
when needed. Never treat an omitted asset as evidence that was inspected.

Validation output goes to stdout. If a log must be retained, use an explicitly
assigned temporary location outside the paper package; a parallel translator
must not write into the graph worker's `knowledge/` directory. Do not create a
parallel evidence tree. Explain the result without dumping hashes or internal details.

## Bilingual full-text contract

paper.md is a complete source-order transcription, including all active body text,
footnotes, appendices, bibliography, equations and table cells. paper_ch.md follows
the same blocks and order, translating narrative into Chinese while retaining
English proper names and academic terms, mathematical notation, data and reference
identities. An index or summary does not meet either deliverable.

Each figure/table keeps its original numbered title/caption as a Markdown heading
and its LaTeX label. Figures retain caption text without images or invented visual
summaries. Tables become native Markdown tables with all values; clarify merged
header relationships through repeated/combined labels. Do not silently omit a row,
column, footnote or uncertainty. paper_ch.md retains the original table/figure
titles and identifiers; caption narrative is translated with technical terms kept.

Every corresponding heading/paragraph/formula/table block has the same unique
`qlpaper-block` ID in each version. Block-ID agreement alone is not a completeness
proof: review both against the active LaTeX, and verify mathematical/data fidelity
and that Chinese prose retains all qualifications. A translation must not add a
teaching explanation absent from the original. Bibliography remains unchanged.

If a converter is used, inspect its warnings, unresolved macros, raw wrappers,
figure/title loss and complex table conversion. TeX is parsed, never executed;
local include paths must stay within the source inventory. No PDF fallback.

Citation and cross-reference commands must be rendered as readable Markdown
references/links, retaining their original identifiers as anchors or comments.
Do not leave visible \cite, \ref or \label commands in narrative text. Inspect
converter macro loss in bibliography titles, table headers and highlighted cells.
