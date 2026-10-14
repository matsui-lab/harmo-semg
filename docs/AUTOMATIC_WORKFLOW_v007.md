# Verified article-to-MAT binding v007

Date: 2026-09-13. Development case, not unseen-study validation.

## Resolved failure

The generic document extractor cannot yet infer arbitrary MAT schemas. However, the repository already contained a reviewed article-to-MAT binding for this source. Its collection-only identifier gate prevented using it with the individually linked Figshare article release. The binding now also accepts an explicit, version-consistent Figshare release whose metadata directly links the reviewed article. The exact reviewed article hash remains mandatory. This extends applicability of a registered profile; it does not claim a general language or data-dictionary interpreter.

The automatic workflow now invokes that profile after successful related-article acquisition and before pair assessment. Unsupported article hashes do not select it. Binding errors or held records make the workflow partial. Existing scientific export gates remain unchanged.

## Checks before completing attributes

- Figshare DOI, article ID and release version agree, and the metadata declares the paper relationship.
- Article bytes match the reviewed data-dictionary version.
- Cached MAT bytes match their acquisition hash.
- The proposed recording points to the expected trial EMG matrix.
- Array orientation and all eight channel labels match the contract.
- Saved sample checksum and numerical values match the corresponding MAT matrix.
- Measurement side comes from subject-level `EMGSide`; trial `Foot` remains a distinct reference-side facet.
- Task and muscle labels belong to the reviewed profile. Unknown side or unsupported labels remain held.

The unit can be completed from unknown to mV only after the raw-to-saved numerical equality check; no multiplication or sample reinterpretation is performed. Conflicting existing units are rejected.

## Real development result

For the acquired Subject 1 file, 42 EMG trial records, comprising 336 channel instances, received source-verified unit, anatomy, laterality, sensor and task information. No selected EMG record was held. Tasks were 25 Walking, 10 ToeWalking, 4 HeelWalking and 3 StepUp. These are not 42 independent studies.

Derived views are saved separately under `paper/results/document_dictionary_real_v002_2026.09.13-r2/views`. Original catalogue contracts and trust files were not changed in the replay. The normal automatic path saves derived semantic views beside original contracts, using the pre-existing immutable/reuse checks.

The 127-record earlier generic-resolution replay had zero completions; this profile-specific replay completes its 42 EMG records. The other modalities and the PhysioNet recording are outside this profile, not newly adjudicated failures.

Scientific use remains unassessed. No timing alignment, normalized EMG envelope, common processing group or amplitude normalization is inferred. Full URL-to-analysis export and generalization to unseen articles remain unproven.
