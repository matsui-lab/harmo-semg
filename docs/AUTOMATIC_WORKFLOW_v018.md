# One-command registered timing workflow (v018)

`harmo timing-run` connects an existing registered catalog to cycle planning,
execution, fresh physiology validation, repeat comparisons, and gated local
signal export. It no longer needs a paper verification script or a previously
saved physiology result to orchestrate those steps.

```sh
harmo timing-run \
  --catalog /absolute/path/to/catalog/catalog.sqlite \
  --source-cache /absolute/path/to/registered-source-cache \
  --protocol /absolute/path/to/physiology_positive_control_protocol.yaml \
  --article /absolute/path/to/registered-study38-article.xml \
  --output /absolute/path/to/NEW_RUN_DIRECTORY
```

The source cache must contain the ten registered `SubjectN.mat` files and
`Gait120_011_to_020.zip`. The catalog must already contain the source-bound
Study 38 records. The protocol and article must match their registered hashes.
There is no arbitrary-study or amplitude-pooling mode in this command.
Existing URL acquisition/catalog construction remains a separate preceding
workflow; this command does not download new data.

## Outputs and exit status

- `inputs.json`: selected inputs, hashes, and implementation identity.
- `status.json`: current/final stage and failure details, atomically replaced.
- `planner/`: 100 channel-cycle plans, assertions, and execution receipts.
- `physiology.result.json`: newly calculated physiological endpoint.
- `resolution/`: subject lineage, two comparison repetitions, contracts,
  release decisions, and permitted `selected-signals/timing.npz` files.
- `selection.json` and `summary.json`: released versus held comparisons.
- `inventory.json`: hashes and sizes of output files as generated (excluding
  the inventory itself). This is an integrity inventory, not a signed manifest.

Exit code 0 means the registered workflow completed with all 20 local exports;
2 means comparisons completed but one or more releases remain held; 1 means
an operational/validation failure caught by the CLI. Held releases are not
silently exported. No data are published to an external service.

The output directory must be new. Failed runs retain intermediate files and
their failure stage. This command does not yet resume an interrupted run.
Successful exports already written before a later failure may remain in that
failed run; always consult the final status, not directory presence alone.

## Scope and preservation

The package modules own the executable path; the historical verification
scripts remain unchanged as earlier regression records. No original catalog,
source, manuscript, or frozen protocol is modified. This remains a registered
Study 38/Gait120 workflow, not an unseen-study validation.

The output refers to the original catalog/source paths. It is not yet a
self-contained relocatable package. Preserve those inputs and the validated
implementation alongside the output. Portable packaging, checked resume, and
connection to a single URL-intake command remain subsequent tasks.

Related tests: 45 passed in
`paper/results/timing_command_tests_v002_2026.09.14-r2.xml`.
The initial v001 attempt exposed a status-file overwrite conflict before
planning; the corrected implementation preserves atomic stage updates. That
attempt remains separate from the subsequent v002 run.

## Completed command-level validation

The real v002 CLI run exited 0: 50 cycles, 100 plans, 20 subject/muscle curves,
fresh physiology pass, 20 comparisons, 20 released and 0 held. The two comparison
repetitions were reproducible; amplitude comparability remained not established.
All 40 exported curves (4,040 values) and phase grids exactly matched the prior
baseline. All 2,138 output files listed in the generated inventory passed hash
and size verification.

- Run: `paper/results/timing_command_v002_2026.09.14-r2/summary.json`.
- Numerical parity: `paper/results/timing_command_parity_v001_2026.09.14-r1.json`.
- Fixed implementation commit: `3ee38e086eb109940aac2966452dd66220164e58`.
- Saved snapshot: `paper/results/timing_command_implementation_v001_2026.09.14-r1.bundle`.

The clean implementation snapshot was committed only in a temporary validation
repository; the original worktree was neither committed nor pushed.
