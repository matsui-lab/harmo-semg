# Explicit metadata scope checkpoint v004

Date: 2026-09-13. Extends v003; no manuscript claim is changed.

## Added rules

For supported JSON metadata, an exact `record` key may contain a finite array of recording names. Each observation retains the JSON pointer of its individual target. Wildcards, empty lists, duplicate targets and numeric identifiers do not acquire inferred scope. String ranges and filename similarity are never expanded.

A top-level `records` array may explicitly reference a top-level `protocols` array through `protocol_id`. Each protocol ID must be unique. The resolver inherits only a literal record-level `task` value; it retains pointers to the record reference, protocol identity and definition. It does not inherit channel attributes such as muscle, side or unit from a recording-level protocol.

Example of the supported structural pattern:

```json
{
  "records": [{"record": ["r1", "r2"], "protocol_id": "P1"}],
  "protocols": [{"protocol_id": "P1", "task": "walking"}]
}
```

This is an explicitly supported metadata pattern, not an assertion that public repositories universally use this schema. A missing reference remains unresolved. An inline task does not override the referenced task: disagreements are preserved and enter the existing channel/record-scoped export hold logic. No semantic equivalence between task labels is inferred.

Input record and protocol lists are bounded to 1,000 rows; expanded rows are bounded to 2,000, including failed-reference attempts. The existing document byte limit also applies.

## Verification boundary

Synthetic tests verify individual scope pointers, the full protocol-reference chain, conflicting inline and inherited definitions, missing and duplicate references, preservation of channel boundaries, resource limits, and propagation of joined conflicts into actual export decisions.

General prose and PDF interpretation, construction of scientifically validated task/anatomy contracts, automatic processing selection and unseen-study evaluation remain unfinished. This checkpoint must not be described as reaching the theoretical or empirical limit of rule-based automation.
