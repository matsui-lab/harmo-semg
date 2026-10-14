"""Use PhysioIO repository clients for frozen, checksum-bound source assets."""
import json
import re
import subprocess
import tempfile
from pathlib import Path


def collection_articles(collection_id, output, r_library=""):
    """Retain PhysioIO's bounded collection listing; no independent API client."""
    if not re.fullmatch(r"[1-9][0-9]*",str(collection_id)):
        raise ValueError("positive_collection_id_required")
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    script = Path(__file__).parent/"r_bridge/scripts/list_figshare_collection.R"
    proc = subprocess.run(["Rscript","--vanilla",str(script),str(collection_id),str(output),r_library],
                          capture_output=True,text=True,timeout=180)
    output.with_suffix(".log").write_text(proc.stdout+proc.stderr)
    if proc.returncode:
        raise ValueError("PhysioIO collection listing failed; see " + str(output.with_suffix(".log")))
    return json.loads(output.read_text())


def repository_request(asset):
    doi = asset["source_record_doi"]
    fig = re.search(r"(?:figshare\.|/)(\d+)\.v(\d+)$", doi)
    if fig and "figshare.com/" in asset["download_url"]:
        return dict(repository="figshare", id=fig[1], version=int(fig[2]), name=asset["name"])
    zen = re.fullmatch(r"10\.5281/zenodo\.(\d+)",doi)
    if zen:
        return dict(repository="zenodo", id=zen[1], name=asset["name"])
    if "figshare.com/" in asset["download_url"]:
        raise ValueError("Figshare corpus asset requires an explicitly pinned article/version")
    return None


def download_asset(asset, destination, r_library="", resume_downloads=False):
    from harmo.validation_corpus import _download_asset, verify_corpus_asset
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    request = repository_request(asset)
    if resume_downloads:
        from harmo.range_download import download_ranges
        return download_ranges(asset, destination, request)
    if request is None:
        if str(asset.get('source_record_doi','')).startswith('10.17632/'):
            from harmo.range_download import download_ranges
            return download_ranges(asset, destination)
        result = _download_asset(asset["download_url"], destination, asset)
        return dict(result, acquisition_backend="existing-checksum-bound-http")
    if Path(request["name"]).name != request["name"] or request["name"] in (".", ".."):
        raise ValueError("repository asset name must be a single filename")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".physioio-", dir=destination.parent))
    if request["repository"] == "figshare":
        # Reuse the bounded metadata client; never select a payload by name alone.
        from harmo.mixed_batch import metadata
        document = metadata(dict(api="https://api.figshare.com/v2/articles/%s/versions/%s" %
                                 (request["id"], request["version"])))
        (stage/"repository.metadata.json").write_text(json.dumps(document))
        duplicate = validate_figshare_selection(asset, request, document)
        if duplicate:
            # PhysioIO's current name selector cannot disambiguate this case.
            # Use the existing pinned-ID transport in a fresh, isolated stage.
            from harmo.range_download import download_ranges
            downloaded = stage/request["name"]
            result = download_ranges(asset, downloaded)
            if not verify_corpus_asset(downloaded, asset)["pass"]:
                raise ValueError("fixed-ID output differs from frozen checksum")
            downloaded.rename(destination)
            record = dict(request=request, file_id=asset.get("file_id"),
                          reason="duplicate_filename_fixed_id_selection", verification=result)
            (stage/"acquisition.json").write_text(json.dumps(record, indent=2))
            return dict(verify_corpus_asset(destination, asset),
                        acquisition_backend="checksum-bound-http-ranges", acquisition=record)
    request_file = stage/"request.json"
    request_file.write_text(json.dumps(request))
    script = Path(__file__).parent/"r_bridge/scripts/download_repository.R"
    proc = subprocess.run(["Rscript","--vanilla",str(script),str(request_file),str(stage),r_library],
                          capture_output=True,text=True,timeout=3700)
    (stage/"reader.log").write_text(proc.stdout+proc.stderr)
    if proc.returncode:
        raise ValueError("PhysioIO acquisition failed; see " + str(stage/"reader.log"))
    downloaded = stage/request["name"]
    result = verify_corpus_asset(downloaded,asset)
    if not result["pass"]:
        raise ValueError("PhysioIO output differs from frozen size/checksum/archive: "+str(downloaded))
    downloaded.rename(destination)
    metadata = json.loads((stage/"acquisition.json").read_text())
    return dict(verify_corpus_asset(destination,asset), acquisition_backend="PhysioIO", acquisition=metadata)


def validate_figshare_selection(asset, request, document):
    """Bind the fixed payload to the versioned inventory before any download."""
    if str(document.get("id")) != request["id"] or document.get("version") != request["version"]:
        raise ValueError("repository_record_or_version_mismatch")
    match = re.fullmatch(r"https://ndownloader.figshare.com/files/([1-9][0-9]*)", asset["download_url"])
    if not match or str(asset.get("file_id", match[1])) != match[1]:
        raise ValueError("Figshare_file_ID_URL_mismatch")
    files = document.get("files")
    if not isinstance(files, list) or not 0 < len(files) <= 1000:
        raise ValueError("bounded_file_inventory_required")
    selected = [row for row in files if str(row.get("id")) == match[1]]
    if len(selected) != 1:
        raise ValueError("unique_fixed_file_ID_required")
    row = selected[0]
    if (row.get("name") != asset["name"] or row.get("size") != asset["size_bytes"]
            or row.get("download_url") != asset["download_url"]
            or asset["checksum_algorithm"] != "md5"
            or row.get("computed_md5", "").lower() != asset["checksum"].lower()):
        raise ValueError("fixed_file_metadata_mismatch")
    return sum(row.get("name") == asset["name"] for row in files) > 1
