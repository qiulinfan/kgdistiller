# Capture one knowledge entry

`capture prepare` turns one reviewed selection into ingest requests. It reads the
cited source lines, builds the entry record, checks it against the current entry
store, and writes a plan request and an apply request. It does not distill the
whole source, look up prerequisites, edit the source, or infer that the reader
understands a concept.

```sh
kgdistiller --repo-root PROJECT capture prepare CAPTURE.json \
  --output .knowledge/build/reviews/captures
```

The agent supplies the content, the cited lines and an explicit identity review:

```json
{
  "label": "Measure space",
  "source": "notes/measure.tex",
  "line_start": 42,
  "line_end": 47,
  "kind": "definition",
  "aliases": ["测度空间"],
  "text": "A measurable space (X, F) equipped with a measure mu on F.",
  "entry": {
    "context": "Introduced before the construction of the Lebesgue integral.",
    "understanding": "not-yet-understood",
    "pending_prerequisites": ["sigma-algebra: the domain of a measure; not yet studied."]
  },
  "review": {
    "action": "add",
    "reviewer": "reading-agent",
    "evidence": "Lines 42-47 state the definition; agent resolve found no entry with this name."
  }
}
```

| Field | Meaning |
|---|---|
| `label` | Single-line display name; becomes the entry's `label` and H1. |
| `id` | Optional for `add`: readable slug `[a-z0-9]+(-[a-z0-9]+)*`, at most 200 characters. Defaults to the slug of the label; required when the label has no ASCII slug (for example a pure-CJK label). Never a hash. |
| `source` | Project-relative path of a file admitted by exactly one registered source. |
| `line_start`, `line_end` | 1-based inclusive line range that states the knowledge. These lines are copied verbatim into the entry's Evidence section. |
| `kind` | Required for `add`. Must be one of the source's document-type `node_kinds` when the source declares a `document_type`; any nonempty single-line kind otherwise. |
| `aliases` | Optional list of other names. Unique across the whole store; an alias equal to the label is dropped. |
| `text` | The entry's Summary. |
| `entry` | Optional `context`, `role`, `understanding`, `prerequisites`, `pending_prerequisites`, `common_confusions`, `open_questions`. |
| `review` | `action` (`add` or `update`), `reviewer`, `evidence` (why this identity decision is right), and `target_id` (required for `update`, rejected for `add`). |

Read the source's profile and numbered lines with
`kgdistiller --repo-root PROJECT scan --file SOURCE` before choosing the kind and
the line range. Any registered text format works the same way; the helper never
parses source syntax.

An `update` keeps its target's id. Omitted fields keep their current values;
supplied fields replace them, and an explicit empty list clears a list field.
Changing the label keeps the old label as an alias automatically. A label or
alias that already identifies another entry fails preparation; resolve the
identity with `$query-kgdistiller` and review an update instead.

`understanding` may be `unknown`, `not-yet-understood` or `understood`; a new
entry starts as `unknown`. `pending_prerequisites` records only directly
encountered gaps as text. It creates neither placeholder entries nor
prerequisite edges.

The output directory must be inside the project and outside registered source
roots and `.knowledge/entries/`. The result names the generated requests:

```json
{
  "status": "prepared",
  "request_id": "capture-measure-space-1",
  "artifacts": {
    "plan": ".../capture-measure-space-1.plan.json",
    "apply": ".../capture-measure-space-1.apply.json"
  },
  "entries": [
    {"id": "measure-space", "label": "Measure space", "action": "add",
     "entry": ".knowledge/entries/measure-space.md"}
  ],
  "counts": {"entries": 1}
}
```

`request_id` is `capture-<id>-<n>` with the next `n` not used by an existing
receipt or request file. Apply the requests through the ordinary ingest
sequence:

```sh
kgdistiller --repo-root PROJECT ingest plan PLAN_REQUEST.json --output PLAN.json
# Review the plan, then apply the prepared request in the authorized scope.
kgdistiller --repo-root PROJECT ingest apply APPLY_REQUEST.json --receipt RECEIPT.json
kgdistiller --repo-root PROJECT check
```

Preparation and planning write nothing to the entry store. Apply re-validates
the request against the current store and the current source text: if the cited
lines no longer equal the Evidence quote, it fails with `stale-evidence`;
prepare again from the current source.
