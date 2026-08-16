# Native paper import contract

## Selection

Require an explicit selected subset from one validated isolated paper candidate
graph. Each candidate carries exact paper-package evidence and one reviewed
disposition. The user selects one registered native target Vault and authorizes
the source-evidence file that will be captured there.

Do not treat paper distillation or federated alignment as import authority.

## Evidence and identity

Capture the target evidence file and bind every committed concept/relation span
to that exact immutable source version. Re-run federated recall immediately
before preparing the request. Only exact or reviewed-alias results may reuse a
qualified target; ambiguity blocks its write path.

Candidate IDs remain paper-local. Native concept IDs remain Vault-local. Store
qualified handles only in review/query artifacts; native note relations use
the selected Vault's stable node IDs under its closed note contract.

## Native proposal

The import proposal contains bounded exact native note writes/deletes and
source derivation updates. Preserve ordinary Markdown authority, stable IDs,
typed direct relations, multi-parent taxonomy, and concise source-grounded note
bodies. Do not copy a whole paper, reference list, or unbounded excerpt into a
concept note.

Build a canonical `qlkg-vault-ingest-request-v1` with fresh registry/Vault/base
generations, a canonical recall report, selected dispositions, evidence spans,
and reviewed provenance. `alignment_mutations` remains empty in this release.

## Commit and aftermath

`$ingest-kgdistiller` plans and applies the exact reviewed request. Accept only
its canonical committed/already-committed report and durable content-addressed
receipt. Query imported qualified handles after commit. Snapshot, Git commit,
remote push, and publication require separate explicit authority.

Stop when the paper package/candidate graph is invalid, source ownership does
not match the target Vault, capture/diff is incoherent, a selected identity is
ambiguous, evidence cannot be mapped exactly, the request is stale, or review
does not cover every selected candidate.
