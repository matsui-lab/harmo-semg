# Review session and catalog reassessment (v012)

The first bounded review loop covers persistent WFDB unit candidates. It does
not yet implement arbitrary task/anatomy correction, new evidence adjudication,
or automatic selection of timing-processing profiles.

## User procedure

```sh
harmo review-session prepare --catalog catalog.sqlite --output review-v001
# Read review-v001/review.md and packet.json.
# Edit decisions.json: supply reviewer, then action and reason per channel.
harmo review-session apply --catalog catalog.sqlite \
  --packet review-v001/packet.json --decisions review-v001/decisions.json
harmo review-session reassess --catalog catalog.sqlite \
  --output reassessment-v001 --purpose activation-timing --purpose amplitude-pooling
```

All actions default to `skip`. `confirm` is restricted to non-held consistent
or file-supported candidates. `hold` records an unresolved item. A contradiction
cannot be cleared by confirmation; replacing a physical unit requires a separate
evidence/correction workflow, not editing the packet. The user's stated identity
is recorded, not authenticated. Review content is evidence, not instructions.

Decisions are applied as one SQLite transaction. A malformed action, duplicate
channel, changed source, changed review history or modified packet rejects the
batch. Source and channel checks inherit the persistent review's checksum
binding. No signal array or scientific trust dimension is modified.

Reassessment runs catalog-based comparison and existing export gates. It is not
a replay of the original URL run's transient documentary observations; it does
not rerun article acquisition, reconstruct a processing plan, or promote trust.
An `evaluated` status with zero exports means the checks ran, not successful
scientific reuse. Read the per-pair requirements in assessment.json.

## Verified scope

The existing two-study PhysioNet catalog produced a packet for 11 EMG channels.
Catalog reassessment completed with zero exports, retaining unsupported-use
restrictions. No human decisions were fabricated or applied to real records.
Controlled tests verify confirmation/hold persistence, atomic rejection of a
partly invalid batch, stale-source rejection, packet tampering and duplicate
decisions. The related suite passed 37 tests.

Artifacts: `paper/results/review_session_v001_2026.09.14-r1/` and
`paper/results/review_session_tests_v001_2026.09.14-r1.xml`.

## Next boundary

Before claiming a complete unknown-study workflow, implement scoped task/anatomy
and processing-evidence corrections, evidence-supported reversal of holds, and
explicit selection of an applicable processing profile. Then test a genuinely
unseen study with human-action counts and error/hold reporting. The existing
PhysioNet examples are development examples, not that unseen-study evaluation.
