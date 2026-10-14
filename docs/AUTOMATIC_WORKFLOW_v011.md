# Fixed-implementation timing verification (v011)

Date: 2026-09-14.

The current implementation was copied into an isolated local Git repository
at `/private/tmp/harmo-timing-release.kSdu2l` and frozen as
`f6fde8bccd7454aa1aa1b0e04343c5540f329d01`. Source, schemas, package metadata
and tests were included. Only bytecode and pytest caches were ignored.
The original working tree was not committed, reset or pushed. The independent
snapshot is retained as `implementation.bundle` in the result directory.
This is a local execution snapshot, not an upstream release or independent
analyst replication.

## Results

Output: `paper/results/harmonize_timing_clean_v001_2026.09.14-r1`.

- `harmonize --timing-profile study38-gait120` completed with exit code 0.
- 1,211 series cataloged; 404 EMG records received registered source-grounded
  attributes; 50 Study 38 gait cycles were selected.
- Fresh physiological control passed; 20 comparisons were timing-comparable
  and reproducible across two runs.
- All 20 comparisons were released, with zero holds. The policy was unchanged.
- Twenty NPZ files contain 40 curves and 4,040 signal values. Hashes, finite
  values, 101-point 0–100% phase axes and release permission were checked.
- Curves, physiological endpoints and exported arrays exactly match the frozen
  `registered_reuse_clean_v001_2026.09.13-r1` baseline.
- Resume inserted zero records, reused all 1,211 records and verified completed
  stages against their hashes; 20 released comparisons were retained, exit 0.
- Related tests: 67 passed, with the existing pytest configuration warning.
- Snapshot Git status remained clean after execution and resume.

Evidence: `baseline_comparison_v001.json`, `release_arrays_v001.json`,
`run-001.json`, `run-002.json`, and stage receipts in the result directory;
`paper/results/harmonize_timing_clean_tests_v001_2026.09.14-r1.xml`.

The top-level legacy label `completed-with-holds` is used by harmonize even
when this registered route has no holds; the timing summary explicitly records
`completed`, `released=20`, `held=0`. Interpret actual counts, not this label.

## Scope

The run used previously downloaded, checksum-verified files and a reviewed
article, on the same Mac/Python environment. It did not revalidate network
acquisition, an independent environment, arbitrary URLs or unknown studies.
This establishes end-to-end execution for the registered two-study timing
profile. Amplitude comparability remains not established. The former export
hold was reproduced in the modified working tree and absent with fixed clean
implementation provenance; no scientific criterion was weakened.
