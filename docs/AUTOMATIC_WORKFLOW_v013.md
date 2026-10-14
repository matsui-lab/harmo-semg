# Source-bound task/anatomy revision (v013)

This increment lets a reviewer correct task and anatomical descriptors and
record documentary processing notes. It does not yet resolve scientific-use
holds or execute an inferred processing protocol.

```sh
harmo contract-revision prepare --catalog catalog.sqlite --record RECORD_ID --output revision-v001
# Edit proposal.json, using current-contract.json as the descriptor template.
harmo contract-revision apply --catalog catalog.sqlite --proposal revision-v001/proposal.json
harmo review-session reassess --catalog catalog.sqlite --output assessment-v002 \
  --purpose activation-timing --purpose amplitude-pooling
```

Supply reviewer and reason and one or more edits. The actions below are the
only supported fields:

- `task`: `value` is a full TaskDescriptor, copied and corrected from the
  current contract; channel_id is null/omitted. compatibility_group must be null.
- `anatomy`: specify channel_id and a full AnatomicalEntity in value. The legacy
  muscle and side attributes are updated consistently. Muscle groups are not
  converted into individual muscles.
- `processing_note`: value is a documentary description string, with no channel
  ID. It is retained as evidence, not installed as a validated processing state.

Every edit requires an `evidence` object:

```json
{
  "path": "/absolute/path/to/source.txt",
  "sha256": "SHA256_OF_THAT_FILE",
  "source_uri": "SOURCE_URL_OR_IDENTIFIER",
  "pointer": "section or line locator",
  "quote": "Verbatim text present in the file",
  "record_id": "RECORD_ID",
  "channel_id": null,
  "scope_reason": "Why this statement applies to exactly this record/channel"
}
```

This version supports UTF-8 evidence up to 2 MiB and exact text matching. It does
not parse PDF or normalize XML markup for quote matching. The software checks
integrity, quote presence, declared scope and descriptor schemas; the reviewer
is responsible for whether the source actually supports the interpretation.
An exact quote is not an independent semantic validation. Reviewer identity is
recorded but not authenticated.

## Persistence and safety

Revisions are append-only events in `contract-revisions.sqlite` beside the
record. Normal catalog reads apply the latest source-bound derived contract.
Original bundle, trust report and samples remain unchanged. All ancestral
evidence remains required, even if a later edit supersedes an earlier field.
Changed/missing evidence or base/sample data fails closed; it is not silently
ignored. Preserve this sidecar when moving records. It is not a tamper-proof
authorization store and deleting it removes this extra layer.

Applying a revision clears prior processing compatibility and adds unassessed
semantic/scientific trust findings to the loaded view. It never grants task
equivalence, normalization validity or export permission. Units, waveform data,
event boundaries, trust flags and processing_state cannot be edited through
this interface. Existing unit reviews become stale when channel bindings change.

## Verification and remaining work

Controlled tests cover task and channel-specific anatomy revisions, processing
notes, unmodified original samples/contracts, no inherited export approval,
stale proposals, quote/scope failures, forbidden fields and ancestral evidence
invalidation. A real existing EMGDB record was used only to prepare the review
template; no human review was fabricated and no real metadata was corrected.

Still needed: evidence-supported reversal of holds, validated processing-plan
execution and trust reassessment, ergonomic editing templates, and evaluation
on an unseen study. This is a review/persistence increment, not a completed
unknown-study harmonization workflow.
