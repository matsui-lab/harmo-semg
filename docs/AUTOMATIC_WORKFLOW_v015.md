# Catalog-bound timing plans (v015)

The diagnostic planner can now read the current catalog contract, including
scoped contract revisions, and extract one explicitly selected channel. It does
not change stored samples, trust, or scientific export permissions.

```sh
harmo timing-plan --catalog catalog/catalog.sqlite --record RECORD_ID \
  --channel CHANNEL_ID --protocol paper/physiology_positive_control_protocol.yaml \
  --output timing-plan
```

Without confirmed assertions this writes `state.json`, `channel.npz`, and a held
`plan.json`. Task, muscle, side, and sampling rate are observed candidates, not
automatically confirmed scientific facts. Nothing infers a gait cycle from a
walking label, array length, filename, or documentary processing note.

Add `--assertions facts.json` to supply scoped confirmed facts, and optionally
`--execute-output timing-output` to execute a supported plan. The assertion file
maps task, muscle, side, sampling_rate_hz, purpose, processing_protocol,
representation, and cycle_boundary to `{value,status,evidence}` objects.
Evidence requires a local UTF-8 source path, SHA-256, exact quote, source_uri,
pointer, record_id, channel_id, and scope_reason. All assertions in this bridge
are bound to the selected record AND channel. Task/anatomy/rate assertions must
agree with the current contract; conflicting contracts must first be revised.

The caller is responsible for whether quoted text actually supports the
assertion and its scope. Byte/quote checks are not semantic entailment checks,
authenticated human approval, or automated literature interpretation.

Every planning and execution check reloads the current contract, verifies the
original samples, and compares the extracted channel with the catalog values.
A subsequent task/anatomy revision or source change invalidates the plan.

## Scope and verification

- Only the existing registered right-sided TA/MG walking-cycle timing profile
  is executable. Raw and envelope inputs remain distinct processing paths.
- This bridge does not find events, segment continuous recordings, choose new
  scientific protocols, or confer cross-study comparability.
- 33 related tests passed, including synthetic executable input, missing facts,
  wrong scope/quote, conflicting anatomy, and stale contract rejection.
- The existing real PhysioNet EMGDB catalog was read without invented evidence;
  its diagnostic plan was held. No real user approvals were supplied or created.
- Result: `paper/results/catalog_timing_plan_v001_2026.09.14-r1/plan.json`.
- Next: bind validated cycle boundaries and processing-state evidence from the
  registered workflow to these assertions, then verify real positive outputs.
  General automatic extraction/confirmation of those facts remains separate.
