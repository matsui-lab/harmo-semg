"""Bounded HTTP ranges for resuming checksum-pinned public corpus assets."""
import json
import os
import time
from pathlib import Path
from urllib.request import Request, urlopen

from harmo.validation_corpus import verify_corpus_asset


def download_ranges(asset, destination, repository_request=None, opener=urlopen,
                    chunk_bytes=16*1024**2, retries=3):
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    if chunk_bytes <= 0 or retries <= 0:
        raise ValueError("positive chunk size and retries required")
    if repository_request is not None and (Path(repository_request["name"]).name != repository_request["name"]
                                           or repository_request["name"] in (".", "..")):
        raise ValueError("repository filename must be a single safe name")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    receipt = destination.with_name(destination.name + ".resume.json")
    identity = {key: asset[key] for key in ("download_url", "size_bytes", "checksum_algorithm", "checksum")}
    expected = int(asset["size_bytes"])
    if partial.exists():
        if not receipt.exists() or json.loads(receipt.read_text())["identity"] != identity:
            raise ValueError("partial download identity is absent or differs; retained unchanged")
        if json.loads(receipt.read_text()).get("recovered_prefix"):
            raise ValueError("legacy name-only prefix is not identity-bound; retained unchanged")
    else:
        if receipt.exists():
            raise ValueError("resume receipt exists without partial bytes")
        prefix = None
        # PhysioIO's legacy request binds only article/version/name, not file ID.
        # Its partial files may belong to another same-named payload. Preserve
        # them, but resume only our own URL/size/checksum-bound range receipts.
        receipt.write_text(json.dumps(dict(identity=identity, recovered_prefix=str(prefix) if prefix else None)))
        partial.touch(exist_ok=False)
    start_offset = partial.stat().st_size
    if start_offset > expected:
        raise ValueError("oversize partial download; retained unchanged")
    print("range download: %s resumes at %d / %d bytes" % (destination.name, start_offset, expected), flush=True)
    while partial.stat().st_size < expected:
        start = partial.stat().st_size
        end = min(expected - 1, start + chunk_bytes - 1)
        length = end - start + 1
        # Range-specific cache keys prevent a cached response for a different range.
        url = asset["download_url"] + ("&" if "?" in asset["download_url"] else "?") + "harmo_range=%d-%d" % (start, end)
        error = None
        for attempt in range(retries):
            try:
                request = Request(url, headers={"Range": "bytes=%d-%d" % (start, end),
                                               "Accept-Encoding": "identity", "User-Agent": "harmo-corpus-resume/1.0"})
                with opener(request, timeout=120) as response:
                    if response.status != 206 or response.headers.get("Content-Range") != "bytes %d-%d/%d" % (start, end, expected):
                        raise ValueError("server did not confirm the exact requested range")
                    data = response.read(length + 1)
                if len(data) != length:
                    raise ValueError("incomplete range response")
                with partial.open("ab") as target:
                    target.write(data)
                    target.flush()
                    os.fsync(target.fileno())
                print("range download: %s %d / %d bytes" % (destination.name, end + 1, expected), flush=True)
                error = None
                break
            except (OSError, ValueError) as exc:
                error = exc
                if attempt + 1 < retries:
                    time.sleep(min(2**attempt, 4))
        if error is not None:
            raise RuntimeError("range download stopped; completed bytes retained: %s" % error) from error
    checked = verify_corpus_asset(partial, asset)
    if not checked["pass"]:
        raise ValueError("completed download failed checksum/archive verification; bytes retained for inspection")
    partial.rename(destination)
    result = dict(verify_corpus_asset(destination, asset), acquisition_backend="checksum-bound-http-ranges",
                  resumed_bytes=start_offset, prefix_unverified_until_final_checksum=True)
    receipt.write_text(json.dumps(dict(identity=identity, completed=True, result=result), indent=2))
    return result
