# Source-bound attribute resolution v005

Date: 2026-09-13. Extends v004. This is a software development checkpoint, not a new empirical manuscript result.

## New behavior

The automatic workflow now produces a derived recording contract when exact record/channel-scoped documentary observations can fill missing attributes. Original catalogue files and trust reports remain unchanged. The derived contract and its before/after canonical hashes are saved in the assessment output. Source quotations, digests and positions remain in the attribute decisions.

The initial rule vocabulary deliberately reuses the existing bounded source vocabulary:

- Laterality: left, right and midline, including the existing unambiguous lexical normalization.
- Sensor: surface EMG, needle EMG, footswitch and electrogoniometer.
- Anatomy: exact full names in `source_evidence.ANATOMY`; abbreviations do not qualify. Muscle groups remain groups. These use the existing local `HarmoSourceTerms` vocabulary, not an externally validated anatomical ontology.
- Task: literal walking, running and rest. No compatibility group, event times, detailed task facets or equivalence between studies is inferred.

Existing values are never overwritten. Existing anatomical mappings that would require a joint laterality reassessment remain unchanged. Unit declarations can confirm existing units or expose conflicts, but cannot reinterpret decoded sample values. Processing and trust are not upgraded.

Before saving a changed view, the workflow checks the actual normalized samples against the original checksum, shape and timebase. The export path recomputes the derived attributes against the currently loaded original/previous semantic contract, and still applies all scientific and signal gates. A saved derived view is not independently trusted as authorization.

## Verification

Synthetic tests cover positive filling, idempotence, wrong record scope, unresolved abbreviations, conflicting sides, unit scale conflict, muscle-group preservation and automatic output saving without changing original files or trust. The untrusted synthetic example remains non-exportable despite successful attribute completion.

## Remaining limitation

This closes part of the observation-to-contract connection only. It does not demonstrate that arbitrary public studies supply these facts in supported structures, nor that a completed contract has sufficient scientific evidence for reuse. Unseen real-study evaluation, broader source interpretation, task-specific evidence and automatic processing remain required. Do not count synthetic attribute completion as successful real-data harmonization.
