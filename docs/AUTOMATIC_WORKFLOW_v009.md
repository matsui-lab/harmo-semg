# Persistent unit review (v009)

Unit candidates and human confirmation/hold actions now persist in a separate
`catalog.sqlite.unit-review.sqlite` append-only event store. The source catalog,
sample arrays and scientific trust reports are not modified. Keep this sidecar
with its catalog; deleting it removes this additional enforcement layer.

`harmo harmonize --local-llm-model ...` automatically saves its review queue.
Later harmonize assessments and public-export calls consult stored decisions,
including when no model is requested. Explicit run-local review cannot override
a persistent hold. Catalogs with no sidecar retain their earlier behavior.

## Commands

```sh
harmo unit-review import --catalog catalog.sqlite --queue review.queue.json
harmo unit-review show --catalog catalog.sqlite
harmo unit-review confirm --catalog catalog.sqlite --record RECORD_ID \
  --channel CHANNEL_ID --reviewer 'Reviewer name' --reason 'Reviewed source evidence'
harmo unit-review hold --catalog catalog.sqlite --record RECORD_ID \
  --channel CHANNEL_ID --reviewer 'Reviewer name' --reason 'Unresolved definition'
```

Confirmation is limited to existing consistent/file-supported candidates. It is
not scientific approval. Conflicts cannot be cleared by confirmation, and no
unit rescaling is performed. Human holds survive fresh candidate imports with
the same source binding. Resolving a conflict or reversing a human hold needs a
further explicit evidence-revision workflow; this version does not silently
discard such decisions. Reviewer names are recorded assertions, not authenticated
identities or digital signatures.

Every reuse verifies actual normalized samples, sample checksum, channel
definitions, original header checksum and repository-description checksum.
Changes or missing files invalidate reuse and hold amplitude export; timing still
uses all existing scientific gates. Stored decisions are scoped to one catalog,
not synchronized across independent catalogs. Unsupported formats fail closed.

## Verification

The frozen two-study live queue from v008 was imported and reread through the
CLI: two bound records, one file-supported EMGDB channel and ten conflicting SEMG
channels were preserved. No human confirmation was fabricated. Automated tests
cover conflict rejection, append-only human history, retained human holds,
changed-source invalidation and default export enforcement. This is persistence
verification, not a new model-accuracy or full-study benchmark.
