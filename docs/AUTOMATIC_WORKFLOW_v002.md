# Rule-based automation checkpoint v002

Date: 2026-09-13. Extends `AUTOMATIC_WORKFLOW_v001.md`; the `harmonize` invocation is unchanged. This checkpoint is not a claim that the limit of rule-based automation has been reached.

## New shared rules

- HTML/XML table rows with explicit vertical cell spans are reconstructed. The inherited record identifier retains the original cell location. Blank cells are never implicitly forward-filled.
- Nested tables, ambiguous columns, horizontal spans and invalid spans remain unresolved rather than being interpreted as a flat table.
- JSON arrays of metadata objects, or a top-level `records` array, and CSV/TSV tables use the same explicit record/channel requirements. Duplicate JSON keys, malformed row widths, non-string identifiers and implicit nested schemas are rejected. JSON paths and logical CSV/TSV row/column locations are distinguished.
- Every accepted observation retains the original source digest, value quotation and record/channel scope locations.
- Unambiguous lexical equivalents for voltage units, laterality and sensor type are normalized. Unit scales are not equated; muscle abbreviations are not expanded automatically.
- Conflicting source definitions and disagreement with an existing unit, laterality, sensor or anatomical label now hold that record out of automatic pair export. Currently this is a conservative whole-record hold, not a purpose- or channel-specific exclusion.
- A no-match is recorded as not extracted by supported rules, never as evidence that the source contains no relevant information.

## Tests and interpretation

The focused suite covers source extraction, scope, conflicts, contracts, export and existing promotion. A positive synthetic export becomes held when contradictory source units are supplied. A declaration about a different record does not block that export. These establish software behavior, not accuracy on unseen scientific studies.

## Still unresolved

The new rules do not construct general task/anatomy contracts from prose, process PDF documents or documents inside archives, infer calibration, choose scientifically justified processing, or automatically upgrade scientific trust. Generic extraction is still observational. The complete URL-to-usable-scientific-output target remains unfinished.

Remaining work must expand positive coverage as well as prevent erroneous approval. In particular, conservative record-level conflict holds should eventually be narrowed to the affected channel and purpose, with tests proving unaffected uses remain available. The source acquisition byte/document budgets may also leave large metadata assets unexamined; these are reported as partial processing rather than scientific ineligibility.

Before claiming generality, freeze the implementation and evaluate independent human reference judgments on studies excluded from development. Keep prior registered studies and the current synthetic fixtures in the development/regression partition.
