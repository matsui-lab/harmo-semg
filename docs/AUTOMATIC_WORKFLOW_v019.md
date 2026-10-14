# Registered URLs through resumable catalog planning (v019)

The explicit `study38-gait120-planned` profile connects the existing URL/cache
acquisition and catalog workflow to the new cycle planner, freshly calculated
physiology endpoint, repeated comparisons, and unchanged local release gates.
The previous `study38-gait120` profile retains its earlier execution path.

```sh
harmo harmonize \
  --urls paper/revision_plans/registered_reuse_urls_v001.json \
  --purpose activation-timing \
  --timing-profile study38-gait120-planned \
  --source-cache /absolute/path/to/registered-sources \
  --timing-protocol /absolute/path/to/physiology_positive_control_protocol.yaml \
  --article /absolute/path/to/registered-study38-article.xml \
  --output /absolute/path/to/NEW_WORKFLOW_DIRECTORY
```

The URL JSON contains exactly the two registered versioned DOI URLs:

```json
[
  "https://doi.org/10.6084/m9.figshare.c.4494755.v1",
  "https://doi.org/10.6084/m9.figshare.27677016.v1"
]
```

If registered files are missing, explicitly add `--download` and an adequate
`--max-bytes` download budget (for example 2147483648). Cached files are checked
against registered identities, not silently trusted. Arbitrary URLs, other
participants, arbitrary article versions, and amplitude pooling are not enabled
by this profile. Acquisition continues using the existing downloader/readers.

## Resume behavior

Repeat the same command with `--resume`. Completed mapping and `timing-planned`
stages are reused only if the stage signature and recorded output hashes agree.
Catalog ingest is idempotent and selected samples are rechecked each time.

The planned-stage signature additionally binds selected current recording
contracts, original input paths, and NumPy/SciPy versions. Implementation/schema
and source/article/protocol hashes are already part of the shared signature.
Changed dependencies or output bytes stop reuse rather than silently recompute
under an old success record. Use a new output directory for changed inputs.

If a stage failed before acquiring a success receipt, resume retains its failed
attempt and runs that stage in a new attempt directory. This is stage-level
resume: it does not yet resume at an individual cycle inside `timing-planned`.

For this new profile, exit 0 means all 20 local releases completed; exit 2 means
release holds remain; caught operational failures return 1. Read
`automatic.summary.json`, numbered `run-*.json`, and the selected
`timing-planned-*/status.json` for details. Nothing is published externally.

## Portability boundary

The workflow still references absolute catalog, article, source, and execution
paths. Moving only the output directory is not supported: checks may fail, or
references may still point to the original location. A portable package must
collect those dependencies and explicitly rebuild path-bound contracts and
release decisions at the destination; blindly replacing strings would invalidate
their hashes. That packaging/restore path is not implemented in this revision.

Tests: `paper/results/planned_url_tests_v001_2026.09.14-r1.xml` (42 passed).

## Real initial run and resume

The registered URL pair was run against the already verified local cache (no
new network acquisition was exercised). Initial run: 1,211 inserted records,
404 source-verified EMG semantics, 100 cycle plans, fresh physiology pass,
20 comparisons and 20 permitted local exports. All 40 timing curves / 4,040
values and phase grids exactly matched the earlier baseline.

Repeating the same CLI command with `--resume` exited 0, inserted zero records,
reused all 1,211 records, and verified/reused both mapping and timing-planned
stage receipts. No second timing attempt was needed.

- Run records: `paper/results/planned_url_v001_2026.09.14-r1/run-001.json`
  and `run-002.json` in the same directory.
- Parity: `paper/results/planned_url_parity_v001_2026.09.14-r1.json`.
- Clean implementation commit: `380373cde07e23e38defcb09fb933530568cd634`.
- Snapshot: `paper/results/planned_url_implementation_v001_2026.09.14-r1.bundle`.

Only the isolated validation snapshot was committed; the original worktree was
not committed or pushed. Existing readers, frozen protocols, and manuscript
were not modified by this revision.
