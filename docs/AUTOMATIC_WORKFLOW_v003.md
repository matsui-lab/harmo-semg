# Rule-based automation checkpoint v003

Date: 2026-09-13. Extends v002 without replacing the earlier checkpoint.

## Implemented

Documentary conflicts now enter each pair's export gate. A conflict explicitly naming a channel blocks comparisons using that channel, whether it is the reference or the target. Other channels still undergo all existing scientific, trust and numerical checks. A record-wide conflict, such as inconsistent task declarations, blocks all its channels. No field is assumed irrelevant to a purpose without an established rule; purpose-specific relaxation remains unimplemented.

Verified downloaded ZIP assets can supply HTML, XML, JSON, CSV and TSV observations directly. The reader verifies the acquisition SHA-256, keeps the archive and member hashes, and reads members without extracting files onto disk. Duplicate member names, unsafe paths, symbolic links, encryption and exhausted budgets are rejected or reported. One malformed document does not discard other readable documents.

Default limits per ZIP: 10,000 directory entries, 100 candidate documents, 2 MiB per document, and 16 MiB total reserved uncompressed document bytes. Nested archives and PDFs are not interpreted. Unsupported metadata structure and exhausted budgets do not establish that scientific metadata is absent.

## Verification

Regression cases include both reference and target channel conflicts, preservation of unaffected eligible channel output, record-wide task conflict, ZIP acquisition mismatch, unsafe paths, duplicate member identity, byte/count budgets, malformed XML isolation and connection to automatic document collection.

These are synthetic software regression tests, not an unseen-study scientific evaluation. No claim of general prose interpretation, automatic scientific contract construction or purpose-specific signal processing is added. The full automatic harmonization goal remains incomplete.
