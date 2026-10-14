"""Machine-verifiable public-source release and rights metadata.

The source registry distinguishes repository-level rights evidence from
content-level checksum verification.  A CC licence can therefore be verified
without pretending that multi-gigabyte source archives have already been
downloaded and hashed.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence


ALLOWED_REDISTRIBUTION = {"permitted", "permitted-with-attribution"}
FIGSHARE_RECORD_TYPES = {"article", "collection"}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_sha256(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonical_license_name(name: str, url: str) -> str:
    """Normalize repository display labels without discarding licence version."""
    normalized_url = url.rstrip("/").lower()
    if normalized_url.endswith("/licenses/by/4.0"):
        return "CC BY 4.0"
    if normalized_url.endswith("/publicdomain/zero/1.0"):
        return "CC0 1.0"
    return name


def normalize_figshare_articles(
    articles: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Return the stable release fields used for the frozen manifest digest."""
    normalized = []
    for article in articles:
        license_value = article.get("license") or {}
        files = []
        for file_value in article.get("files") or []:
            files.append({
                "file_id": int(file_value["id"]),
                "md5": str(
                    file_value.get("computed_md5")
                    or file_value.get("supplied_md5")
                    or ""
                ),
                "name": str(file_value["name"]),
                "size_bytes": int(file_value["size"]),
            })
        license_url = str(license_value.get("url", ""))
        normalized.append({
            "article_id": int(article["id"]),
            "doi": str(article["doi"]),
            "files": sorted(files, key=lambda item: item["file_id"]),
            "license": {
                "name": canonical_license_name(
                    str(license_value.get("name", "")), license_url
                ),
                "url": license_url,
            },
            "version": int(article["version"]),
        })
    return sorted(normalized, key=lambda item: item["article_id"])


def summarize_figshare_articles(
    articles: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    manifest = normalize_figshare_articles(articles)
    files = [file_value for article in manifest for file_value in article["files"]]
    return {
        "article_count": len(manifest),
        "article_ids": [article["article_id"] for article in manifest],
        "file_count": len(files),
        "licenses": sorted({article["license"]["name"] for article in manifest}),
        "manifest_sha256": canonical_sha256(manifest),
        "total_size_bytes": sum(file_value["size_bytes"] for file_value in files),
        "versions": sorted({article["version"] for article in manifest}),
    }


def _default_fetch_json(url: str) -> Any:
    for attempt in range(5):
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "harmo-semg-source-release-verifier/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # nosec B310
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            retryable = exc.code in {403, 429} or 500 <= exc.code < 600
            if not retryable or attempt == 4:
                raise
            retry_after = exc.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else float(2 ** attempt)
            time.sleep(min(delay, 16.0))
    raise AssertionError("unreachable")


def _default_fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "harmo-semg-source-release-verifier/1.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # nosec B310
        return response.read().decode("utf-8")


def fetch_figshare_articles(
    release: Mapping[str, Any],
    fetch_json: Optional[Callable[[str], Any]] = None,
) -> List[Dict[str, Any]]:
    """Fetch all article records underlying one frozen Figshare release."""
    fetcher = fetch_json or _default_fetch_json
    record_type = release.get("record_type")
    api_record = str(release.get("api_record", ""))
    if record_type == "article":
        value = fetcher(api_record)
        if not isinstance(value, Mapping):
            raise ValueError("Figshare article API did not return an object")
        return [dict(value)]
    if record_type != "collection":
        raise ValueError("unsupported Figshare record_type: {}".format(record_type))
    members = fetcher(api_record)
    if not isinstance(members, list):
        raise ValueError("Figshare collection API did not return a list")
    urls = []
    for member in members:
        if not isinstance(member, Mapping) or not member.get("url"):
            raise ValueError("Figshare collection member lacks an API URL")
        urls.append(str(member["url"]))

    def fetch_member(url: str) -> Dict[str, Any]:
        value = fetcher(url)
        if not isinstance(value, Mapping):
            raise ValueError("Figshare member API did not return an object")
        return dict(value)

    with ThreadPoolExecutor(max_workers=min(8, len(urls) or 1)) as executor:
        return list(executor.map(fetch_member, urls))


def verify_figshare_source(
    study_id: str,
    source: Mapping[str, Any],
    fetch_json: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """Compare a live Figshare release with the checksum-bound registry summary."""
    release = source.get("release") or {}
    if release.get("repository") != "figshare":
        raise ValueError("source {} is not a Figshare release".format(study_id))
    actual = summarize_figshare_articles(fetch_figshare_articles(release, fetch_json))
    expected = release.get("manifest") or {}
    checks = {
        key: actual.get(key) == expected.get(key)
        for key in (
            "article_count",
            "article_ids",
            "file_count",
            "licenses",
            "manifest_sha256",
            "total_size_bytes",
            "versions",
        )
    }
    checks["declared_license_matches_manifest"] = actual["licenses"] == [
        source.get("license")
    ]
    return {
        "study_id": study_id,
        "repository": "figshare",
        "expected": dict(expected),
        "actual": actual,
        "checks": checks,
        "pass": all(checks.values()),
    }


def verify_mendeley_source(
    study_id: str,
    source: Mapping[str, Any],
    fetch_text: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    """Verify DOI, version, and licence on every official Mendeley dataset page."""
    release = source.get("release") or {}
    if release.get("repository") != "mendeley-data":
        raise ValueError("source {} is not a Mendeley Data release".format(study_id))
    fetcher = fetch_text or _default_fetch_text
    rows = []
    for record in release.get("records") or []:
        page = fetcher(str(record["landing_page"]))
        checks = {
            "doi": str(record["doi"]) in page,
            "version": "Version {}".format(record["version"]) in page,
            "license": str(source["license"]) in page,
        }
        rows.append({
            "doi": record["doi"],
            "landing_page": record["landing_page"],
            "version": record["version"],
            "checks": checks,
            "pass": all(checks.values()),
        })
    return {
        "study_id": study_id,
        "repository": "mendeley-data",
        "records": rows,
        "pass": bool(rows) and all(row["pass"] for row in rows),
    }


def source_rights_report(
    source_registry: Mapping[str, Any],
    expected_study_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Assess release identity and redistribution evidence without network access."""
    sources = source_registry.get("sources") or {}
    expected = sorted(str(value) for value in (expected_study_ids or sources.keys()))
    observed = sorted(str(value) for value in sources)
    rows = []
    for study_id in expected:
        source = sources.get(study_id) or {}
        evidence = source.get("license_evidence") or {}
        release = source.get("release") or {}
        evidence_urls = evidence.get("urls") or []
        if isinstance(evidence_urls, str):
            evidence_urls = [evidence_urls]
        rights_ready = bool(
            source.get("license")
            and source.get("license_url")
            and source.get("redistribution") in ALLOWED_REDISTRIBUTION
            and evidence.get("evidence_type") == "official-repository-metadata"
            and evidence.get("checked_at")
            and evidence_urls
        )
        release_ready = bool(
            release.get("repository")
            and release.get("record_type")
            and release.get("version_status") == "frozen"
        )
        rows.append({
            "study_id": study_id,
            "license": source.get("license"),
            "redistribution": source.get("redistribution"),
            "repository": release.get("repository"),
            "rights_ready": rights_ready,
            "release_identity_ready": release_ready,
            "pass": rights_ready and release_ready,
        })
    return {
        "expected_study_ids": expected,
        "observed_study_ids": observed,
        "study_id_set_matches": observed == expected,
        "studies": rows,
        "pass": observed == expected and bool(rows) and all(row["pass"] for row in rows),
    }
