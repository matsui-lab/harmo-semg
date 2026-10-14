# Registered timing execution from harmonize (v010)

The ordinary `harmonize` entry point can explicitly select the paper's existing
Study 38–Gait120 processing profile. This routes to the established acquisition,
automatic mapping, catalog, verified article binding, cycle selection, timing
processing, physiological control and release pipeline. It does not infer a
protocol from arbitrary URLs, reuse an unrelated generic catalog, or convert LLM
unit candidates into scientific permission.

```sh
harmo harmonize \
  --urls paper/revision_plans/registered_reuse_urls_v001.json \
  --output output/registered-timing \
  --purpose activation-timing \
  --timing-profile study38-gait120 \
  --source-cache downloads/additional_framework_v001_2026.09.12-r1 \
  --timing-protocol paper/physiology_positive_control_protocol.yaml \
  --article paper/results/document_resolution_real_v002_2026.09.13-r2/article-0.xml
```

Add `--download` to authorize acquisition of missing registered assets/article;
without it this command requires existing verified inputs. `--resume` verifies
completed stages against source, protocol, implementation and output hashes.
The exact two versioned URLs and frozen protocol are mandatory. Amplitude
pooling, a record limit, fewer than 20 comparisons, or an LLM override are
rejected for this route rather than silently ignored.

The profile uses pre-segmented gait cycles, checks source-to-catalog numerical
identity, and compares two muscles in ten participants from each study. It does
not implement continuous-signal gait-event detection. Successful physiological
checks and comparable timing do not bypass release gates, including source-code
reproducibility checks. A modified working tree can therefore yield correct
curves and twenty comparable pairs while all formal exports remain held.

General URL intake and its persistent unit-review sidecar remain a separate
route; this registered route is timing-only, with no new amplitude permission.
The remaining generalization task is evidence-checked selection/adaptation of
processing profiles for other studies, not another unit-label extractor.

## Real execution, 2026-09-14

`paper/results/harmonize_timing_v001_2026.09.14-r1` was run through the new
harmonize entry point using the verified source cache, not a fresh network
download. It inserted 1,211 series, selected 50 Study 38 cycles, and verified
20 participant/muscle curves against the source-based processing. The fresh
physiological control passed; all 20 timing comparisons were comparable and
their identities/decisions reproduced across two runs.

`baseline_comparison_v001.json` establishes exact equality of the 20 catalog
curves, selected-cycle count, input-row hash and physiological endpoints against
`registered_reuse_clean_v001_2026.09.13-r1`. Formal release differs: this modified
working-tree execution retains the computational-reproducibility warning,
therefore all 20 exports remain quarantined (CLI exit 2). No release policy was
relaxed. This validates numerical reconnection, not submission-ready release
of this revised implementation or independent replication. The related test
suite passed 67 tests (`harmonize_timing_tests_v001_2026.09.14-r1.xml`).
