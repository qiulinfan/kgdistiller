# Capture one knowledge entry

`capture prepare` handles a reviewed selection while reading a source. It prepares
one candidate, performs one comparison against the current knowledge graph, and
produces the existing transaction requests. It does not distill the whole source,
look up prerequisites, or infer that the reader understands a concept.

```sh
kgdistiller --repo-root PROJECT capture prepare CAPTURE.json \
  --output knowledge/build/captures
```

The agent supplies the content and an explicit source-backed identity review:

```json
{
  "name": "Selected concept",
  "source": "notes/chapter.md",
  "text": "A concise source-backed explanation.",
  "entry": {
    "understanding": "not-yet-understood",
    "pending_prerequisites": ["A prerequisite used here but not yet understood."]
  },
  "review": {
    "action": "add",
    "reviewer": "reading-agent",
    "evidence": "The selected passage explains this concept; its distinct identity was reviewed."
  }
}
```

`name` must select exactly one explicit definition marker in the registered
source. If adding that marker is needed, supply `source_content` with the complete
proposed native source, or `source_content_file` pointing to a UTF-8 file containing
it. Preserve unrelated prose, definitions and references. The helper rejects
changes to other definitions. A new source may contain only the selected
definition. This is a bounded operation; the full ingestion workflow remains
available for larger changes.

Use `review.action: "update"` for an existing entry. The selected native marker
must already own that identity in this exact source. No extra identifier lookup
is needed; an optional `review.target_id` must agree with the selected identity.
The helper also verifies the comparison. The agent must review meaning; an
identical name in a different source does not authorize an update. An unresolved identity, duplicate addition, missing
marker or conflicting target fails preparation. Internal candidate identifiers
and transaction preconditions are generated, not requested from the reader.
Updates preserve omitted structured entry fields; supplied fields replace their
previous values. An explicit empty list clears a list field.

An optional top-level `kind` records the reviewed semantic node kind. Read the
source's profile with `scan --file` and choose one of its registered `node_kinds`
when `document_type` is assigned. A supplied kind must be nonempty text and
match that profile; omission preserves an existing reviewed kind. A native
statement wrapper or file extension does not override the reviewed value. The
helper carries `kind` into the candidate and the delta's `properties.kind`.
Changing only a knowledge type does not re-review its scientific text or refresh
stale source evidence.

`entry` uses the normal structured entry fields. `understanding` may be `unknown`,
`not-yet-understood`, or `understood`; omission makes no mastery claim.
`pending_prerequisites` records only directly encountered gaps as text. It creates
neither placeholder graph nodes nor prerequisite edges. Reading a prerequisite
later can reveal its own immediate gaps in a separate capture.

Markdown, Typst and LaTeX use their existing native scanners. New entries link
to their original `.md`, `.typ` or `.tex` evidence directly; no prepared Markdown
copy of the source is required. Atomic knowledge entries themselves remain
Markdown. Existing explicit derived-evidence bindings remain valid and are not
rewritten as a side effect of capture. This does not add direct PDF capture or
change the separate raw-evidence import workflow.

The output directory must be inside the project and outside registered sources,
committed graph/entry data and derived evidence. The result has this shape, with
actual artifact paths supplied by the command:

```json
{
  "status": "prepared",
  "mode": "plan",
  "name": "Selected concept",
  "source": "notes/chapter.md",
  "action": "add",
  "artifacts": {
    "candidate": "...candidate.json",
    "comparison": "...comparison.json",
    "plan": "...plan.json",
    "apply": "...apply.json"
  },
  "counts": {"candidates": 1, "comparisons": 1, "entries": 1}
}
```

The plan/apply requests already contain normalized authority preconditions and
the complete post-patch marker state. Use the ordinary ingestion sequence:

```sh
kgdistiller --repo-root PROJECT ingest plan PLAN_REQUEST.json --output PLAN.json
# Review the plan, then apply the prepared request in the authorized scope.
kgdistiller --repo-root PROJECT ingest apply APPLY_REQUEST.json --receipt RECEIPT.json
```

Preparation and planning preserve accepted source and knowledge bytes. Only the
transactional ingest apply step installs the entry. Both prepared requests bind
to the same graph generation; prepare again if the graph or source changes.
Existing pending definitions elsewhere in the source remain pending.
