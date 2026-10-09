---
name: harvest-paper
description: Explicit command only. Synchronize the user's checked def-sheet review items into accepted knowledge metadata through the deterministic harvest script; use Obsidian Markdown checkboxes for selection.
disable-model-invocation: true
---

# Harvest checked knowledge

Run for `$harvest-paper`, `/harvest-paper`, or a direct request to harvest the
checked items. Reading, distillation and checking a box alone never start a
write. Match the user's language; preserve identifiers, commands and raw errors.

## Review in the source's def sheet

Use the caller's existing partial or complete `def-sheet.md`. Each selectable
candidate is an ordinary Markdown task item, usable directly in Obsidian. Link
it to its clearly labeled review draft in `knowledge/build/reviews/`; the draft
holds the complete proposed content, source evidence, target and reviewed
add/update identity decision. Already accepted, reused knowledge remains a direct
canonical link without a new capture payload. For updates, retain enough before/after
content to make the change reviewable. Accepted rows link to real canonical
metadata. A draft link never represents an accepted entry.

When preparing new candidates, read only the requested source scope and compare
plausible identities through the supported bounded API. Preserve native source
markers and source-scoped meaning. Do not infer identity from the row label,
heading, paper title or name match. New-paper reading normally uses
`$capture-kgdistiller`; whole-source distillation is separately requested, usually
for the user's own notes or already familiar material.

The user reviews the linked drafts, checks the desired task boxes,
then explicitly asks to harvest. That request authorizes the checked content
and its stated target. Do not repeat selection in chat or a native question UI,
start a frontend, or ask for the same permission again. If the user only asks
to prepare a sheet, save it for review and stop. Unchecked rows remain pending.
The checkbox means selected for synchronization; it never means `understood`.
If the user edits a draft, perform a targeted re-review and regenerate its
prepared item before applying. The script detects draft/payload differences;
never import the old payload while showing revised text.

## Run the deterministic synchronization

Read [the checkbox contract](references/checkbox-contract.md) for the exact
prepare/apply commands. The agent prepares payloads during candidate capture;
the user only reviews and checks the generated task rows. Let the script parse the checked
items, load their prepared payloads, validate source and target freshness,
perform supported transactional ingest and refresh successful metadata links.
Reuse the prepared source content and identity decisions; do not reread the
whole paper, regenerate definitions or run a new model extraction on the normal
path. The agent handles only concrete invalid input, unresolved identity,
unsupported operations or changed content that requires a new review.

Keep `understanding` and direct `pending_prerequisites` independent of selection.
Preserve existing understanding unless the user explicitly changed it in the
reviewed content. Harvest only the prepared direct dependency layer; unresolved
terms do not trigger recursive search. Apply only the supported reviewed scope.

After a committed receipt, replace each imported candidate's draft link with
its real canonical entry link and record its synchronized state so rerunning
does not duplicate it. Preserve unchecked items, unrelated annotations and
partial source coverage. If a transaction succeeded but sheet refresh failed,
recover its receipt before retrying the write. Report accepted links, the real
receipt and any item still awaiting attention. No Git commit, push or publication
is implied by harvesting knowledge.
