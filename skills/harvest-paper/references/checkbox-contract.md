# Obsidian checkbox harvest

The user reviews the source def sheet, checks prepared items in Obsidian, and
asks to harvest. The agent supplies payloads during initial capture or requested
candidate preparation. Harvesting already prepared items requires no new model
extraction or selection conversation.

## Prepare the review once

Use the current registered base and the existing source def sheet. Pass
`--base B` after the command, or run inside the registered base root; relative
paths are resolved against the working directory:

```sh
kgd harvest prepare CAPTURES.json \
  --sheet DEF_SHEET.md --output .knowledge/build/reviews/HARVEST --base B
```

`CAPTURES.json` contains a nonempty `captures` array of ordinary reviewed
[capture payloads](../../capture-kgdistiller/references/capture-contract.md).
Supported actions are `add` and `update`. An already accepted entry that needs
no change remains a direct sheet link; no `reuse` action is required.

```json
{
  "captures": [
    {
      "label": "Measure space",
      "source": "notes/measure.tex",
      "line_start": 42,
      "line_end": 47,
      "kind": "definition",
      "text": "A measurable space equipped with a measure.",
      "entry": {
        "understanding": "not-yet-understood",
        "pending_prerequisites": ["sigma-algebra: the domain of a measure; not yet studied."]
      },
      "review": {
        "action": "add",
        "reviewer": "reading-agent",
        "evidence": "Lines 42-47 state the definition; agent resolve found no existing entry."
      }
    }
  ]
}
```

Each capture cites a registered source by path and line range; the cited lines
become the entry's verbatim Evidence quote. Sources are never edited, and any
text format works the same way.

Preparation writes one readable draft per item showing the before → after entry
fields (label, kind, aliases, understanding), the entry sections before and
after, the identity review, the cited source range and the Evidence quote. It
binds generated task rows to these frozen proposals and returns
`status: prepared`. It does not ingest the candidates. The review directory
must be inside the base root. The sheet must be Markdown outside
`.knowledge/entries/` and separate from the cited source. To append candidates
to the same sheet, use that sheet's existing review directory. A sheet that does
not exist yet starts as `# Definition sheet` with `Coverage: partial`.

The helper creates and maintains these bindings; do not ask the user to type
internal markers or invent them from a heading, row order or name:

```markdown
# Definition sheet

Coverage: partial

<!-- kgdistiller-harvest-review: RELATIVE_PATH_TO_REVIEW_JSON -->

- [ ] [Measure space (draft)](RELATIVE_PATH_TO_REVIEW_DRAFT) <!-- kgdistiller-harvest: 1-measure-space -->
```

The relative paths are URL-encoded. The review binding names the
`kgdistiller-checkbox-review-v1` manifest, which stores each item under a
readable token `<n>-<label slug>` together with its frozen payload, the draft
text, the entry file text (or none for a new entry) and the cited source text.
Only task rows with valid bindings to this review participate in harvest.
Ordinary todos, copied example rows and fenced code examples are ignored.
Preserve the generated label/link and binding; the user changes the checkbox.
Annotations outside the bound label/link can remain in the sheet. New rows
follow the sheet's existing line endings.

## Apply checked items

After the user's explicit harvest request, run:

```sh
kgd harvest apply DEF_SHEET.md \
  --output .knowledge/build/reviews/HARVEST_RUN --base B
```

The request plus the checked reviewed items authorizes that scope and target.
Do not ask for a second selection or permission. Preparing a sheet or checking
a box without a harvest request does not initiate a write.

Freshness is checked by comparing text: for every checked item, the draft file,
the target entry file and the cited source lines must equal what was stored at
preparation. Any difference stops the run with a message naming the item; review
it and prepare it again. The checked items are then ingested as one transaction
with the readable request id `harvest-<review directory>-<run>`. The run is
recorded before ingest, so an interrupted run resumes by looking up that
request's receipt instead of writing twice.

Harvest preserves unchecked candidates, unrelated annotations, coverage and
learning state. A checkbox is an import selection; it does not set `understood`
or resolve prerequisites. Only the reviewed payload can explicitly change those
fields. Pending dependencies stay at the directly encountered layer.

After commit, selected rows drop the `(draft)` label and link to the committed
entries. Previously committed items are not written again.

| Result | Meaning and next action |
| --- | --- |
| `nothing-selected` | No new checked candidates remain; no write occurred. |
| `committed` | Entries committed and links refreshed; report the returned receipt. |
| `committed-sheet-pending` | Entries committed but sheet refresh failed; retain the receipt and correct the reported refresh issue. Run apply on the same sheet to finish; it reuses the committed receipt. Do not prepare a duplicate write. |

Unsupported scientific relations/application records remain review proposals;
this adapter does not expand the underlying ingest model. Report concrete errors
or remaining draft scope. Git backup is a separate operation.

## Correct a pending draft

When a draft was edited or its source lines changed, review only the affected
item and prepare its corrected capture payload with the same sheet and review
output directory. The preparation command writes a new draft revision, replaces
that pending row's draft link and clears its checkbox; other rows and
annotations remain unchanged. The old draft file remains available. The user can
review and select the revised row in Obsidian. This correction path does not add
another approval step to the normal unchanged checked-item harvest.
