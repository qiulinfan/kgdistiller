# Paper Markdown package contract

This contract defines the handoff from paper acquisition to downstream semantic
analysis. The package preserves source locations and meaning without reproducing
the paper's visual layout.

## Package layout

```text
<paper-package>/
├── paper.md
├── source.json
├── source.pdf                 # when a canonical PDF exists
├── attachments/              # optional semantic supplement Markdown
└── evidence/
    ├── text/page-0001.txt
    └── visual/page-0007.png   # targeted inspection only; never embedded
```

`source.pdf`, extracted page text, and targeted renders are evidence. `paper.md`
is the semantic handoff. An ignored build directory or reported temporary
directory is appropriate; do not add generated packages to Git unless requested.

`source.json` uses `qlpaper-markdown-source-v1` and records:

- selected source identity, URLs, access date, identifier, and version;
- source PDF hash, byte size, metadata, and page count when applicable;
- one extracted-text record per page;
- preliminary detected visual-object candidates, the reviewed final object
  inventory, and their caption locations;
- targeted visual pages and reasons;
- the relative Markdown and attachment paths.

Do not put credentials, cookies, expiring download tokens, or unrelated local
paths in the manifest.

## Markdown source markers

Start a PDF-backed document with:

```markdown
<!-- qlpaper-markdown-v1 -->
<!-- qlpaper-source-sha256: <64 lowercase hex characters> -->
```

Retain one page boundary marker for every source PDF page, in order:

```markdown
<!-- qlpaper-source: page=7 -->
```

Place it immediately before semantic content beginning on that page. A page with
only layout or decorative matter still gets a marker and a short omission note.
Do not force headings or paragraphs to break merely to imitate pagination.

For an authoritative HTML-only paper, use `qlpaper-markdown-v1` plus stable
source URL and section anchors in the manifest. Do not invent PDF hashes, page
numbers, or visual validation claims. The bundled deterministic validator is
PDF-backed; report a manual source/anchor audit instead of claiming it passed.

## Visual object records

Use one record per meaningful figure or table detected in the paper:

```markdown
<!-- qlpaper-object: kind=figure; label=Figure 3; page=7 -->
> **Figure 3 — Caption or concise title**
>
> - `summary`: axes/components, comparison, direction, trend, or mechanism
> - `paper-use`: the claim or argument step supported by this object
> - `uncertainty`: none, or the exact unreadable/ambiguous detail
```

For tables, use `kind=table` and the paper's exact label. Labels and pages must
match `source.json`. Put multi-panel details in `summary`; do not create one
record per panel unless the paper numbers them independently.

A useful figure summary normally names:

- what is compared or connected;
- axes, components, groups, or stages that determine interpretation;
- the important trend, ordering, qualitative mechanism, or quantity;
- what conclusion the authors use it to support;
- any unreadable legend, scale, error bar, or panel.

A useful table summary normally names its row/column dimensions, metric and
units, strongest comparisons, claim-relevant values, and caveats. Preserve a
small table as native Markdown only when the exact cells are themselves evidence;
the object record is still required.

Never use Markdown image syntax, HTML image/media tags, base64 media, SVG, a
source PDF embed, or a link whose target is required to understand the record.
The render may remain under `evidence/visual/` solely for audit.

Do not leave Pandoc or source-converter syntax in the semantic handoff. Normalize
raw HTML tags, HTML tables, layout wrappers, fenced TeX `math` blocks, and
Pandoc's `$`-plus-backtick math form into native Markdown and `$...$` or
`$$...$$` mathematics before validation.

## Text fidelity

Preserve semantic content required to recover the paper's argument:

- title, authors, abstract, headings, lists, definitions, algorithms, theorem
  statements, proof steps, results, limitations, appendices, and supplements;
- math using Markdown-compatible LaTeX delimiters, including referenced labels;
- citation keys or unambiguous inline citations and bibliography entries;
- author-stated uncertainty, negative results, exceptions, and scope boundaries.

Use an official HTML or source archive to reduce extraction noise when it matches
the selected version. Never import statements, appendix material, or corrected
equations absent from the selected paper without labeling them as separate
supplementary evidence.

Normalize columns, line wrapping, ligatures, and hyphenation. Do not silently
repair a suspected paper error. Record ambiguous symbols or broken text with a
bounded warning and source location.

## Targeted visual policy

`prepare_paper.py` renders pages with caption candidates, substantial raster
objects, very little extracted text, or extraction corruption. Inspect every
listed targeted render. Inspect an adjacent page only when the caption and object
appear to be split across a boundary.

The generated `detected_object_candidates` list is immutable audit evidence, not
the final truth. Reconcile false positives and missed objects into
`object_candidates` after reading captions, numbering sequences, cross-
references, and targeted renders. The validator requires a Markdown record for
every item in that reviewed list.

Do not conduct an all-page visual sweep merely to prove layout fidelity. Open a
previously unrendered page only when extracted text, a cross-reference, or an
object candidate shows a concrete semantic ambiguity. OCR only that bounded
region or page when necessary.

The semantic gate passes when every core claim and detected figure/table has a
traceable representation. Pixel fidelity, page-break fidelity, font matching,
LaTeX compilation, and image reconstruction are explicitly outside scope.

## Attachments

Put a complete official supplement or appendix in `attachments/<name>.md` when
it is semantically independent of the main file. Apply the same source and
object marker rules and list the path in `source.json`. Retain raw supplementary
PDFs only as evidence. Do not copy an entire source repository or build tree into
the package.

## Validation gate

The deterministic validator checks source hash and page count, ordered page
markers, manifest coverage, object-candidate coverage, required object record
fields, unresolved placeholders, forbidden media embeds, raw HTML/layout
wrappers, Pandoc math residue, and NUL-corrupted extracted text. It cannot decide
whether prose or an object summary is accurate. The acting Agent must separately
check:

- all main argument steps and central results are present;
- equations and labels used by later claims remain interpretable;
- object summaries match the targeted render and surrounding text;
- limitations and negative evidence are not dropped;
- every uncertainty is specific enough for downstream graph extraction.
