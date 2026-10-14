# Bounded timing planner (v014)

The planner searches registered operation transitions rather than generating
Python/R code. Six primitives are currently available: mean centering,
band-pass filtering, rectification, Hampel filtering, RMS envelope generation,
and within-cycle scaling/101-point phase normalization. Breadth-first search
has an explicit depth limit and excludes revisiting a state.

For raw input it selects six steps. For a released envelope it selects only
phase normalization. Only the frozen Study38–Gait120 protocol is supported;
parameters are not optimized or inferred. The first version accepts one
presegmented right-side cycle for either registered muscle. It does not segment
continuous signals, aggregate subjects, infer event meaning, or explore multiple
scientifically distinct parameter sets. This is not yet a general planner.

```sh
harmo timing-plan --input state.json \
  --protocol paper/physiology_positive_control_protocol.yaml \
  --output plan-v001.json --execute-output diagnostic-v001
```

The input names a one-dimensional `values` array in a local NPZ and its SHA-256,
sampling frequency for raw EMG, and facts with value/status/evidence fields.
Required facts include task, purpose, processing_protocol, cycle_boundary,
muscle, side, representation and (for raw signals) sampling_rate_hz. Evidence
contains path, SHA-256 and scope_reason. Only confirmed facts can satisfy
preconditions. These are caller-supplied, source-bound assertions: hash checking
does not prove that a document supports the asserted meaning. Automatic
construction from corrected catalog contracts remains to be implemented.

Plans retain settings, primitive transitions, input/evidence identity and code
and numerical-library versions. Execution rebuilds the plan and validates
inputs before writing diagnostic arrays. A short cycle, missing/changed input,
nonfinite values, unsupported representation, insufficient sampling frequency,
or missing confirmed facts prevents execution. Documentary correction does not
automatically assert protocol compatibility. No trust or release state is changed.

## Verification

Synthetic raw and envelope paths exactly match the existing numerical
functions. Missing confirmations, changed evidence, altered step lists,
insufficient search depth and incompatible sampling frequency are held.
The real-data replay uses registered Subject1, five selected cycles and two
muscles, comparing ten generated raw-path outputs with the existing function.
It is a previously used development example, not unseen-study validation.

Next: bind corrected catalog contracts to state assertions with explicit
processing/event evidence, add subject-level aggregation, then integrate the
existing physiological and release checks. Diagnostic output here must not be
described as authorized scientific export.
