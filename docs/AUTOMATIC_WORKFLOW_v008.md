# Opt-in local unit review in harmonize

Date: 2026-09-14. Adds the v011 unit-candidate policy to the URL workflow.

## Entry point

With an installed local Ollama model and running loopback server:

```sh
harmo harmonize --urls urls.json --output output \
  --purpose activation-timing --purpose amplitude-pooling \
  --local-llm-model gemma4:12b --local-llm-port 11439 \
  --max-unit-channels 32
```

The option does not install/start a model server, call a cloud model, or automatically approve metadata. Without the option, the existing workflow remains unchanged. A compatible PhysioIO/PhysioCore installation with exported readWFDB is required for the existing acquisition/conversion stage; select it through the normal R library settings.

## Data flow and gates

1. Existing URL intake acquires and catalogs source-preserving converted records.
2. For cataloged WFDB EMG channels, bind the downloaded header to its recorded SHA-256 and the exact channel index/name. Use explicit units parsed by WFDB, not an LLM interpretation of the header.
3. Query the local model using only the downloaded repository-description paragraphs for that channel. Preserve request, response, document hash and source locations.
4. Reconcile the description candidate against verified header evidence. Save a versioned run-local `unit-review/review.queue.json` containing all candidates, conflicts, exceptions and limits. No sample, bundle or trust-report modification occurs.
5. Pass those decisions into the same run's purpose assessment/export gate. Unresolved/missing/stale unit review blocks amplitude-pooling export. Timing follows all existing policy, sample and trust gates; this feature grants no timing permission.

Record/channel IDs and sample checksums bind review decisions to the current assessment. File-supported or consistent unit candidates do not grant any new scientific permission: previous comparability/trust gates still must pass. Other record acquisition continues after individual review errors, missing model service, unsupported record format or review-budget exhaustion.

## Limits

This integration covers cataloged WFDB headers and repository HTML descriptions. It does not yet connect general PDF/article retrieval, other file formats, arbitrary record-scope inference, or physiological normalization extraction to production approval. Headerless and unsupported records are explicitly held for amplitude when review is enabled. The separate calibration/normalization experiment remains separate from this unit-only gate.

The review is run-local, not a global persistent approval lock: standalone public-export calls without supplied unit review retain their earlier behavior. Review-queue persistence and reuse across independent commands require a further provenance-checked integration; do not describe this as universal catalog enforcement.

## Verification

Unit and integration tests cover source-wise contradiction, verified header binding, unchanged catalog bytes, missing/stale review, amplitude export rejection and unchanged timing-policy decisions. URL CLI wiring is tested with controlled acquisition; a live bounded URL run is recorded separately under `paper/results/unit_workflow_live_v002_2026.09.14-r2`.

The first live attempt (v001) acquired source data but conversion failed because the default PhysioIO lacked readWFDB. The retry explicitly uses the preexisting updated PhysioIO and PhysioCore libraries, without changing the system installation. Each study is limited to one record and the batch to 50 MB; partial dataset-selection status is expected and must not be reported as full-study completion. Inspect the retry's automatic.summary.json for actual reviewed records, holds and completion.

### Completed bounded live run

Run `ed50becd11fc46f0ba0835ef116b41bb` in the v002 directory completed acquisition and conversion of one EMGDB record and one SEMG record, with no intake errors. Gemma 4 12B reviewed all 11 EMG channels. The EMGDB channel retained a file-supported mV candidate; the description supplied no competing candidate. All ten SEMG channels had conflicting declarations (description: microvolts; verified header: millivolts) and were held. No units or samples were rewritten, and no automatic approvals were issued.

Purpose assessment exported zero comparisons. Existing scientific/trust requirements also prevent reuse in this example, so this is not evidence that the new gate alone changed a previously allowed real-data export. Controlled tests separately establish that unresolved unit review blocks otherwise eligible amplitude export while preserving timing-policy decisions. Overall CLI status is `partial` (exit 2), reflecting deliberately incomplete study selection, not full-study completion.

The related test suite passed 80 tests (`paper/results/unit_workflow_tests_v003_2026.09.14-r3.xml`). Checkpoint persistence, prompt source-first ordering, and additional runtime/input guards were added while the live process was already running; those hardening changes were tested separately and are not claimed to have executed in that frozen live run. This bounded example is not an independent extraction-accuracy benchmark.
