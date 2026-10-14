# Real-input development replay v006

Date: 2026-09-13. This replay uses previously acquired development inputs, not unseen studies or independent reference labels.

## Observed result

The original catalogue contains 127 converted records: 126 from Figshare article 9784964 v1 (one subject, multiple modalities/trials) and one PhysioNet emgdb 1.0.0 recording. The generic documentary-resolution path matched zero attributes and filled zero records. This denominator is not 127 independent EMG studies, and the result is not the output of the separate, previously registered study-specific reuse path.

All original recording contracts and trust files remained byte-identical. This run evaluated attribute resolution only, not a new full acquisition, pair export or scientific analysis.

## Concrete defect corrected

The Figshare metadata describes the payload only as Subject 1 data. It links the associated article using `IsSupplementTo`, which the earlier related-document discovery ignored. That typed relationship is now admitted as an article candidate; `Cites` remains excluded. Candidate discovery does not establish recording scope.

The first retrieval attempt failed because network name resolution was restricted; its evidence is retained in `document_resolution_real_v001_2026.09.13-r1`. With network access, the second run acquired DOI 10.1038/s41597-019-0323-z as PMC6897988 and verified the DOI in the XML itself. The XML SHA-256 is `d1e83284ddefe66ee1960c797977f2f16b7b78df8435578aa8613e150de70738`.

## Why attribute completion remained zero

The article provides a data dictionary and acquisition prose, rather than rows explicitly keyed by each converted recording. Its table explains the EMG matrix, channel-name variable and voltage unit. Its prose explains how participant-level side information and trial-level tasks are stored. The current generic table extractor requires an explicit recording column, so it does not bind these definitions to MAT fields or trial arrays. The PhysioNet description likewise does not meet this generic table pattern; the older bounded protocol adapter is a separate path.

This is evidence of an unimplemented structural join, not proof of missing scientific information and not proof that rule-based automation is impossible.

## Next implementation boundary

Parse source data-dictionary declarations into field-level definitions. Match the declared paths against actual MAT structure, confirm array orientation/channel-name counts and source identity, and then map subject-level and trial-level metadata to each derived recording. Keep conflicting declarations and unit reinterpretation separate from scientific approval. The already observed article is now a development case; subsequent success on it must not be called unseen validation.

Machine-readable result: `paper/results/document_resolution_real_v002_2026.09.13-r2/report.json`. Reproduction entry point: `scripts/paper/evaluate_document_resolution.py`.
