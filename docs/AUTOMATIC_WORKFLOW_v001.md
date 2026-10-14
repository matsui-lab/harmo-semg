# URL-driven harmonization: development checkpoint v001

This is a bounded development workflow, not a validated general-purpose automatic harmonizer.

## Invocation

Supply the same versioned URL JSON input accepted by `mixed-batch`:

```sh
harmo harmonize --urls urls.json --output results \
  --purpose activation-timing --purpose amplitude-pooling \
  --max-bytes 1073741824 --max-comparisons 1000
```

The existing intake dependencies, repository support and access restrictions still apply. `--r-library` selects the installed reader library when needed. This command does not bypass authentication, download limits or unsupported formats.

## Implemented

1. Run existing mixed repository intake and catalogue registration.
2. Read downloaded HTML/XML and repository descriptions; resolve explicitly documented-by article DOIs through Europe PMC, with identity checks and bounded requests.
3. Extract literal table cells with source digest, quotation and location. Match explicit record and channel identifiers exactly; retain conflicts and unmatched observations.
4. Enumerate recording/channel candidates within the requested datasets and comparison budget; evaluate timing and amplitude separately using existing export gates.
5. Export eligible pair outputs and preserve held reasons, errors and unprocessed comparisons in `automatic.summary.json`.

Document observations are NOT automatically promoted into scientifically approved contracts. Pair outputs are not pooled populations. A completed execution with holds is not evidence of general automation or scientific validity. Acquisition/document failures and exhausted comparison budgets produce partial execution.

## Remaining implementation, in dependency order

1. **Scoped evidence resolution:** resolve definitions, table/prose cross-references, exceptions and recording applicability. Distinguish no extraction from absence in the source. Handle PDF and archive-contained documents. Preserve competing interpretations.
2. **Contract construction:** convert supported terms into versioned task/anatomy/unit contracts, bind them to exact input hashes and recordings, and check contradictions. Source matching alone must not confer scientific approval.
3. **Purpose-specific processing:** select only justified normalization, timing alignment and signal transformations; execute them with reproducible parameters and verify their numerical output. Never manufacture calibration or synchronization evidence.
4. **Unseen-study validation:** freeze code and rules before evaluating studies excluded from development. Separate software correctness, factual extraction, applicability and scientific-use decisions.

## Completion criteria to freeze before unseen evaluation

- URL and purpose are the only study-specific user inputs on supported public sources, apart from access credentials and resource budgets.
- Independently adjudicated eligible records reach a usable output without per-study handwritten mapping or contracts; report their proportion, not only counts of successful downloads.
- Report erroneous approvals alongside coverage, separately for timing and amplitude, with denominators and uncertainty. All-held output cannot satisfy completion.
- Include missing evidence, conflicting scope, unknown units, incompatible tasks and unsupported inputs as negative controls.
- Compare against format-only conversion on the same inputs and show which incorrect reuse decisions are prevented and which valid reuse tasks become executable.
- Re-running fixed inputs preserves contracts, numerical outputs and decisions. Interrupted or failed work is distinguished from a scientific hold.
- Human reference judgments must be obtained before examining automated answers; one reviewer plus adjudication or two reviewers is preferable. Developer-created labels are not independent verification.

The numerical acceptance thresholds and held-out study set have not yet been fixed. Existing registered studies and synthetic fixtures are development/regression evidence, not an unseen benchmark.
