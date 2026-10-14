# Planned curves through comparison and release gates (v017)

## Verified outcome

Registered Study 38 catalog-planner outputs now feed the existing Study 38 /
Gait120 comparison pipeline via the optional `planner_replay` argument of
`harmo.registered_timing.run`. All 100 saved cycle executions are recomputed
and checked before their 20 median curves replace the reference input values.
The registered reference is independently recomputed and exact equality is
required. This deliberately restricts this connection to regression replay.

The fixed-code v002 run produced:

- 20/20 comparison statuses and conditions equal to the previous baseline;
- 20/20 pairs with both roles allowed by the unchanged release gates;
- 40/40 released curves (4,040 values), plus phase grids, exactly equal;
- repeat-1 and repeat-2 recording identities and comparison decisions equal;
- amplitude comparability still not established.

No external repository publication occurred: “release” denotes the local
scientific export gate and its selected signal files.

## Traceability and rejection behavior

`planned_timing_rows` checks receipt hashes, the current catalog and evidence,
output hashes, recomputed processing traces, and complete output arrays. It
requires 100 distinct record/channel executions, five cycles for every expected
subject/muscle, and equality with the registered subject curves.

Each derived source contract includes a SHA-256-bound subject execution-lineage
file. The resolver preserves that source, and `timing_export.export_pair`
revalidates its referenced executions before any selected signal is written.
Missing/changed lineage or processing history is rejected. Removing a source
from a released contract also violates existing release-bound contract hashes.

Related tests: **42 passed**. Tests include missing/changed lineage, altered
processing traces, source changes, stale contracts, and existing release holds.

## Retained artifacts and limits

- Successful result: `paper/results/planned_timing_release_v002_2026.09.14-r2/summary.json`.
- Tests: `paper/results/planned_timing_release_tests_v002_2026.09.14-r2.xml`.
- Fixed implementation: `implementation.bundle` in the successful result folder;
  commit `a3ef68af771a951621ab553d404640ad5b832986`.
- v001 was held because runtime bytecode had inadvertently been included in the
  temporary Git snapshot. Runtime caches were excluded in the fixed snapshot;
  no gate was weakened. The failed run remains available separately.
- Existing physiology endpoint reused with SHA-256
  `9e8a72ce2b3060c6f5d734604192639cc3438fb45784e63f7e5ecb09aee7e316`.
  This run does not claim a newly calculated physiology endpoint. Both source
  studies' registered curves were freshly extracted for input verification.
- Original repository changes were not committed or pushed; only the isolated
  validation snapshot was committed. Manuscript and frozen protocol unchanged.

This closes the registered diagnostic-to-comparison-to-export connection. It
does not establish independent validation, unseen-study automation, amplitude
pooling, or automatic discovery of new scientific processing protocols.
The next product task is to expose this explicit registered-profile connection
through the user-facing workflow with durable lineage packaging, rather than
requiring a Python-level call or verification script.
