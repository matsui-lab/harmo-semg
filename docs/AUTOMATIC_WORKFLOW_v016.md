# Catalog-bound registered walking replay (v016)

## Result

The new catalog-to-plan-to-execution route processed 50 registered Study 38
walking records (10 subjects, five cycles each) and two muscles per record:
100 six-operation plans. The resulting 20 pointwise-median subject/muscle curves
were exactly equal to the existing registered processing implementation at all
101 points per curve (2,020 values; maximum absolute difference zero).

Results: `paper/results/catalog_timing_replay_v001_2026.09.14-r1/summary.json`.
Related tests: 35 passed in
`paper/results/catalog_timing_plan_tests_v002_2026.09.14-r2.xml`.

## Evidence and implementation

- Original MAT sizes and MD5s must match the frozen protocol. The existing
  binding checks source hashes, channel order, task, reference foot, side,
  sampling rate, and exact catalog-array equality with the selected source.
- Article XML must match the already registered article SHA-256. The evidence
  receipt retains its paragraph defining stride boundaries from force-platform
  foot strikes, plus the exact record and channel binding.
- The internal heel-strike boundary token retains the existing registered
  walking-profile interpretation. The article describes foot strikes, not a
  newly detected or independently validated heel-specific event. This mapping
  must not be generalized to other tasks.
- `raw` means released EMG before the common envelope; the acquisition's
  reported band-pass filtering is not claimed to be absent.
- The protocol explicitly maps Gastrocnemius Medialis to medial gastrocnemius.
  The bridge accepts that bounded alias without changing the catalog or
  accepting arbitrary anatomical substitutions.
- Each assertion's dependencies bind the original MAT, article, and frozen
  protocol. Their bytes are rechecked during planning and execution; changes
  prevent execution. These checks do not authenticate human approval.

## Reproduce

```sh
PYTHONPATH=src python scripts/paper/verify_catalog_timing_planner_v001.py \
  --root . \
  --catalog paper/results/harmonize_timing_clean_v001_2026.09.14-r1/catalog/catalog.sqlite \
  --sources downloads/additional_framework_v001_2026.09.12-r1 \
  --article paper/results/document_resolution_real_v002_2026.09.13-r2/article-0.xml \
  --output NEW_OUTPUT_DIRECTORY
```

Outputs are diagnostic and created only in a new directory. Original catalog
contracts, trust, data, frozen protocol, and manuscript are unchanged.

## Claim boundary and next step

This verifies numerical regression parity on the already used Study 38 cohort.
It is not independent validation or automatic interpretation of unseen studies.
No new Study 120 processing, cross-study comparisons, or scientific releases
were performed in this replay. Next, connect these derived curves to the
existing comparison and release gates while retaining this new execution
provenance, then check cross-study decisions without bypassing those gates.
