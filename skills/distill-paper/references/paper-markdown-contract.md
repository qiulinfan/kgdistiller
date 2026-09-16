# LaTeX fallback package

```text
<paper-package>/
├── link.txt             # one versionless https://arxiv.org/abs/IDENTIFIER URL
├── source.json          # versioned provenance and source-text inventory
├── source/              # original LaTeX and supporting text, once only
│   ├── main.tex
│   └── references.bib
├── reading.md           # optional user-requested explanation
├── section-guide.md     # optional short navigation with original HTML links
├── knowledge/           # optional isolated graph and concrete prerequisites
├── context/             # optional user-requested external research
└── learning/            # optional concept dossiers and reading route
```

This contract applies only to the LaTeX fallback. HTML is preferred and PDF is
the last resort; neither needs a TeX manifest. Within a LaTeX package, only
`link.txt`, `source.json` and the inventoried `source/` are required.
Existing `paper.md` or `paper_ch.md` may remain; do not delete, regenerate or
compare them as part of source preparation. Full-text Markdown transcription
and translation are not required outputs.

No `evidence/`, PDFs (including figure PDFs), rendered pages, OCR output, duplicate
HTML or retained compressed archive. Fetch the archive temporarily, keep its hash,
and retain only its text source. The prepared subset is for semantic reading,
not a promise that the original paper can be compiled from this subset.

`$distill-paper` writes a short `paper-notes.md` with section navigation, existing
knowledge links and paper-qualified candidates. Candidates await user selection
before transactional import; this does not launch a reading pipeline.

## Manifest

`source.json` uses `qlpaper-latex-source-v1`:

- `identifier`, `version`: exact arXiv paper/version;
- `arxiv_url`: canonical versionless abs URL, identical to `link.txt`;
- `archive_url`: versioned arXiv src URL;
- `archive_sha256`: digest of downloaded archive before filtering;
- `files`: sorted records of `path`, `sha256`, `bytes` for retained source text;
- `source_sha256`: SHA256 of the UTF-8, sorted-key, compact JSON encoding of `files`;
- `entrypoints`: files containing a LaTeX documentclass/documentstyle declaration;
- `omitted_assets`: paths excluded from the downloaded archive, not their contents.

The source tree's bytes remain unchanged. A changed source requires a rebuilt
manifest and revalidation of dependent graph artifacts; never hand-edit a digest
to make stale artifacts pass. Do not change registered personal graphs during
package maintenance. Old PDF packages are not accepted by this validator;
convert them only in a user-authorized scope.

## Online reading entry

Open the official versioned paper page, follow an available HTML reading link,
and verify the destination's identity, version and content. Prefer HTML for
browser reading and built-in translation. Do not claim an unvisited or
constructed HTML URL works. If no usable HTML is available, return the verified
original abstract/publisher entry with its access limitation. Report any version
difference from the local source; do not use a later rendering as evidence of
an earlier version's exact text without checking it.

Return this URL in the handoff; keep `link.txt` in its established one-URL format
and do not add invented manifest fields. Do not save full HTML or a translated
copy. Web-link verification is an agent action separate from source validation;
report inaccessible online material without rejecting intact local TeX.

## Location evidence and validation

Prefer `source/main.tex`, a bounded line range and a LaTeX label or section name.
Read local input/include, bibliography and supplement dependencies rather than
assuming one file is complete. The script discovers document entrypoints, not
all semantic dependencies; the acting agent reviews that coverage.

Source-package validation is the default. The established `--source-only` flag
remains a synonym for callers that already use it. No full-text file, aligned
block IDs, translation language or cross-file mathematical comparison is required.
Existing auxiliary files are left unchanged.

An optional package-local Markdown explanation may use source markers:

```markdown
<!-- qlpaper-source: file=source/main.tex; lines=20-35 -->
```

Pass `--markdown FILE` to explicitly validate that file's source ranges and
content constraints. This is not a completeness or translation check.

No page-count, page-marker or mandatory visual-inspection requirement. Equations,
important table cells, claims, assumptions and exceptions must remain traceable.
Describe visual content only as supported by inspected source or a verified
online rendering. Never treat an omitted asset as evidence that was inspected.

Validation output goes to stdout. If a log must be retained, use an explicitly
assigned temporary location outside the package. Do not write into another
worker's outputs or create a parallel evidence tree. Explain the result without
dumping hashes or internal details.
