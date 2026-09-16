---
name: harvest-paper
description: Explicit command only. Review paper knowledge in a static note, use the current conversation or native question UI to select and edit it, and import only the user's confirmed content.
disable-model-invocation: true
---

# Harvest a paper

Run only for `$harvest-paper` or `/harvest-paper`, after the user chooses to
harvest their reading. Distillation never starts harvesting. Match the user's
language; preserve identifiers, commands and raw errors. Work in the current agent.

## Prepare a static review

Read the paper notes and later corrections. Resolve the target vault and native
authority from context. Query plausible existing entries through bounded read-only
APIs; inspect definitions and conditions, not raw graph/entry shards. Do not infer
identity from a name match or a shared paper alone.

Save `harvest-review.md` outside the live authority. Give candidates stable short
labels such as C1/C2 while retaining their original paper/version-qualified IDs.
For each, show its name, concise mechanism/definition, conditions, source location,
and proposed add/reuse/update action. Link applicable existing knowledge. For an
update, show the existing content and the proposed resulting content so the user
can review what changes and what remains. Include the target at the top.

Show a compact candidate table in the conversation and open the note in the
runtime's file panel/editor when available. Markdown is the review surface; do
not start an HTTP server, open a custom web form, or publish an HTML artifact.
No special JSON review schema, listener or running process is needed.

## Select and edit within the current conversation

Use the harness's available native question/choice UI for concise decisions, or
ordinary conversation when that UI is unavailable. Never assume a particular
widget, multi-select feature or editable table exists. Long edits belong in the
note or normal chat, not a series of forced per-node questions. For example:
"Keep C1 and C3; add this condition to C3; skip C2."

Apply the user's requested edits to the review note and show material changes.
A user may edit the note directly and then ask to import those selections.
Only an explicit user response confirming specific content and its target
permits import. A displayed default, file opening/edit timestamp, timeout or
silence is not confirmation. Do not ask twice when that exact content is already
confirmed. If confirmation is pending, end the turn and continue on their reply;
keep the static note, with no background polling.

## Import the confirmed content

Freeze the confirmed selection and exact edited text in local working files,
including the relevant user decision, review revision and target digests. This is
a record of actual confirmation, never a replacement for it. Recheck source
support and target freshness. If a selected edit is unsupported, the target has
changed materially or identity is unresolved, explain that item and revise its
review; do not silently replace the user's wording or create a duplicate.

Hand only confirmed native updates to `$ingest-kgdistiller` for plan, inspection
and apply. Keep node text and structured entry content consistent. Preserve
paper/version meaning, source provenance and unrelated existing content.
Unselected candidates remain outside the graph. Report entry links and the real
committed receipt after fresh status checks; distinguish drafts and plans from
completed imports. No Git commit, push or publication is implied.
