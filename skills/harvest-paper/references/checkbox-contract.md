# Obsidian checkbox harvest

The user reviews the source def sheet, checks prepared items in Obsidian, and
asks to harvest. The agent supplies payloads during initial capture or requested
candidate preparation. Harvesting already prepared items requires no new model
extraction or selection conversation.

## Prepare the review once

Use the current registered project and the existing source def sheet:

```sh
kgdistiller --repo-root PROJECT harvest prepare CAPTURES.json \
  --sheet DEF_SHEET.md --output knowledge/build/reviews/HARVEST
```

`CAPTURES.json` contains a nonempty `captures` array of ordinary reviewed
[capture payloads](../../capture-kgdistiller/references/capture-contract.md).
Supported actions are `add` and `update`. An already accepted entry that needs
no change remains a direct canonical sheet link; no `reuse` action is required.

```json
{
  "captures": [
    {
      "name": "Selected concept",
      "source": "notes/chapter.md",
      "text": "A source-backed explanation of the selected definition.",
      "entry": {
        "understanding": "not-yet-understood",
        "pending_prerequisites": ["A direct prerequisite still to learn."]
      },
      "review": {
        "action": "add",
        "reviewer": "reading-agent",
        "evidence": "The explicit definition was checked against existing identities."
      }
    }
  ]
}
```

`name` selects a real native definition marker in the registered source. If the
marker needs to be added, include `source_content` or `source_content_file` as
specified by the capture contract. Each candidate's proposed source starts from
the same current original source and changes only its selected definition and
necessary marker. The batch combines nonoverlapping edits; conflicting or other
unsupported edits require correction. Markdown, Typst and LaTeX keep their
native identity and evidence requirements.

Preparation writes readable drafts with proposed text, before/after structured
entry content, knowledge type, identity review and native source diff. The type
comparison states when an omitted `kind` preserves the existing type; a type
change alone does not re-review the scientific text or refresh stale evidence.
It binds generated task
rows to these frozen proposals and returns `status: prepared`. It does not ingest
the candidates. The review directory must be inside the project and outside
registered sources and accepted data. To append candidates to the same sheet,
use that sheet's existing review directory.

The helper creates and maintains these bindings; do not ask the user to type
internal markers or invent them from a heading, row order or name:

```markdown
<!-- kgdistiller-projection: definition-sheet -->
# Definition sheet

Coverage: partial

<!-- kgdistiller-harvest-review: RELATIVE_PATH_TO_REVIEW_JSON -->

- [ ] [Selected concept (draft)](RELATIVE_PATH_TO_REVIEW_DRAFT) <!-- kgdistiller-harvest: GENERATED_TOKEN -->
```

The actual relative paths are URL-encoded and the token is generated. The
projection marker is the first nonblank content line after optional YAML frontmatter;
it prevents the sheet from being scanned as a native authority. Keep it there.
Only task rows with valid bindings to this review participate in harvest.
Ordinary todos, copied example rows and fenced code examples are ignored.
Preserve the generated name/link and binding; the user changes the checkbox.
Annotations outside the bound name/link can remain in the sheet.

If a user edits the draft itself, the next apply reports the difference. Review
and prepare the affected item again before writing. Do not silently synchronize
a stale prepared payload or turn that correction into full-source distillation.

## Apply checked items

After the user's explicit harvest request, run:

```sh
kgdistiller --repo-root PROJECT harvest apply DEF_SHEET.md \
  --output knowledge/build/reviews/HARVEST_RUN
```

The request plus the checked reviewed items authorizes that scope and target.
Do not ask for a second selection or permission. Preparing a sheet or checking
a box without a harvest request does not initiate a write.

The script checks bound labels/links, frozen drafts and source/entry freshness,
then performs one comparison and one ingest transaction for the selected batch.
It preserves unchecked candidates, unrelated annotations, coverage and learning
state. A checkbox is an import selection; it does not set `understood` or resolve
prerequisites. Only the reviewed payload can explicitly change those fields.
Pending dependencies stay at the directly encountered layer.

After commit, selected rows drop the `(draft)` label and point to the real
canonical entry authorities.
Previously synchronized items are not written again. Later checked candidates
from the same source may incorporate disjoint changes previously committed by
this sheet; arbitrary external source or entry changes require a targeted review.
Do not bypass a stale or ambiguous identity check.

| Result | Meaning and next action |
| --- | --- |
| `nothing-selected` | No new checked candidates remain; no write occurred. |
| `committed` | Metadata committed and accepted entry links refreshed; report the returned receipt. |
| `committed-sheet-pending` | Metadata committed but sheet refresh failed; retain the receipt and correct the reported refresh issue. Run apply on the same sheet to recover the stored request, even if using a different output directory. Do not prepare a duplicate write. |

Unsupported scientific relations/application records remain review proposals;
this adapter does not expand the underlying ingest model. Report concrete errors
or remaining draft scope. Git backup, frontend export and publication are
separate operations.


## Correct a pending draft

When a draft was edited or its evidence changed, review only the affected item
and prepare its corrected capture payload with the same sheet and review
output directory. The preparation command replaces that pending row's draft
link and clears its checkbox; other rows and annotations remain unchanged. The
old draft artifact remains available. The user can review and select the revised
row in Obsidian. This correction path does not add another approval step to the
normal unchanged checked-item harvest.
