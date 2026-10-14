"""One bounded entry point for public PhysioNet, Figshare and Zenodo records.

Repository bytes and conversion success do not establish scientific permission.
Unknown sources and unsupported archives remain explicit held work items.
"""
import json
import re
import sqlite3
import uuid
import unicodedata
import shutil
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from harmo.config.automap import require, save, discover, run as automap
from harmo.public_batch import normalize_url, run_batch, catalog, sha
from harmo.proposal_catalog import ingest
from harmo.repository_bridge import download_asset, collection_articles
from harmo.archive_intake import extract_zip
from harmo import payload_cache


def route(url):
    require(isinstance(url, str), "URL_string_required")
    p = urlsplit(url)
    require(p.scheme == "https" and not p.username and not p.password and p.port in (None,443)
            and not p.query and not p.fragment and "%" not in p.path, "plain_public_HTTPS_URL_required")
    if p.hostname in ("physionet.org", "www.physionet.org"):
        slug, version, canonical = normalize_url(url)
        return dict(repository="physionet", id=slug, version=version, canonical=canonical, dataset=slug+"/"+version)
    mend = (re.fullmatch(r'/datasets/([a-z0-9]{10})/([1-9][0-9]*)/?', p.path) if p.hostname == 'data.mendeley.com' else
            re.fullmatch(r'/10\.17632/([a-z0-9]{10})\.([1-9][0-9]*)', p.path) if p.hostname == 'doi.org' else None)
    if mend:
        identity, version = mend.groups()
        return dict(repository='mendeley', id=identity, version=int(version),
                    dataset='doi:10.17632/'+identity+'.'+version,
                    canonical='https://data.mendeley.com/datasets/'+identity+'/'+version,
                    api='https://data.mendeley.com/public-api/datasets/'+identity+'/snapshot/'+version)
    collection = re.fullmatch(r"/10\.6084/m9\.figshare\.c\.([1-9][0-9]*)\.v([1-9][0-9]*)",p.path) if p.hostname == "doi.org" else (
        re.fullmatch(r"/collections/[^/]+/([1-9][0-9]*)/([1-9][0-9]*)/?",p.path) if
        p.hostname == "figshare.com" or (p.hostname or "").endswith(".figshare.com") else None)
    if collection:
        cid, version = collection.groups()
        doi = "10.6084/m9.figshare.c.%s.v%s" % (cid,version)
        return dict(repository="figshare-collection",id=cid,version=int(version),dataset="doi:"+doi,
                    canonical="https://doi.org/"+doi,api="https://api.figshare.com/v2/collections/%s/versions/%s" % (cid,version))
    if p.hostname == "doi.org":
        fig = re.fullmatch(r"/10\.6084/m9\.figshare\.(\d+)\.v(\d+)", p.path)
        zen = re.fullmatch(r"/10\.5281/zenodo\.(\d+)", p.path)
    else:
        fig = re.fullmatch(r"/articles/[^/]+/[^/]+/(\d+)/(\d+)/?", p.path) if (
            p.hostname == "figshare.com" or (p.hostname or "").endswith(".figshare.com")) else None
        zen = re.fullmatch(r"/records?/(\d+)/?", p.path) if p.hostname == "zenodo.org" else None
    if fig:
        record_id, version = fig.groups()
        require(int(record_id) > 0 and int(version) > 0, "positive_record_and_version_required")
        doi = "10.6084/m9.figshare.%s.v%s" % (record_id,version)
        return dict(repository="figshare", id=record_id, version=int(version),
            canonical="https://doi.org/"+doi, dataset="doi:"+doi,
            api="https://api.figshare.com/v2/articles/%s/versions/%s" % (record_id,version))
    if zen:
        record_id = zen[1]
        require(int(record_id) > 0, "positive_record_required")
        doi = "10.5281/zenodo."+record_id
        return dict(repository="zenodo", id=record_id, canonical="https://zenodo.org/records/"+record_id,
                    dataset="doi:"+doi, api="https://zenodo.org/api/records/"+record_id)
    raise ValueError("unsupported_or_unversioned_repository_URL")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("repository metadata redirect requires review")


def metadata(route_spec):
    if route_spec.get('repository') == 'mendeley':
        from harmo.mendeley_repository import metadata as fetch
        return fetch(route_spec)
    # API URLs are constructed above, never taken from document contents.
    opener = build_opener(NoRedirect())
    with opener.open(Request(route_spec["api"], headers={"User-Agent":"harmo-mixed-batch/1.0"}),timeout=60) as response:
        raw = response.read(2*1024**2+1)
    require(len(raw) <= 2*1024**2, "metadata_size_limit")
    return json.loads(raw)


def file_assets(spec, document):
    if spec['repository'] == 'mendeley':
        from harmo.mendeley_repository import file_assets as build
        return build(spec, document)
    require(str(document.get("id")) == spec["id"], "repository_record_identity_mismatch")
    if spec["repository"] == "figshare":
        require(document.get("version") == spec["version"], "repository_version_mismatch")
    files = document.get("files")
    if spec["repository"] == "zenodo" and isinstance(files,dict):
        # PhysioIO already supports both Zenodo representations. Normalize only
        # the planning manifest here; acquisition still belongs to PhysioIO.
        entries = files.get("entries")
        require(isinstance(entries,dict) and 0 < len(entries) <= 1000,"public_file_inventory_required")
        normalized = []
        for name, row in entries.items():
            require(isinstance(row,dict) and row.get("key",name) == name,"Zenodo_file_key_mismatch")
            normalized.append(dict(row,key=name,links=dict(self=row.get("links",{}).get("content",""))))
        files = normalized
    require(isinstance(files, list) and 0 < len(files) <= 1000, "public_file_inventory_required")
    assets, names, ids = [], set(), set()
    duplicate_names = False
    for f in files:
        name = f.get("name") if spec["repository"] == "figshare" else f.get("key")
        require(isinstance(name,str) and name not in ("", ".", "..") and Path(name).name == name
                and not any(c in name for c in ("\\", "\x00", "/", ":"))
                and not any(ord(c) < 32 for c in name) and name == name.rstrip(". "),
                "unsafe_or_duplicate_repository_filename")
        key = unicodedata.normalize("NFC", name).casefold()
        if key in names:
            require(spec["repository"] == "figshare", "unsafe_or_duplicate_repository_filename")
            duplicate_names = True
        names.add(key)
        size = f.get("size")
        require(isinstance(size,int) and not isinstance(size,bool) and size > 0, "positive_declared_file_size_required")
        if spec["repository"] == "figshare":
            checksum = f.get("computed_md5")
            require(type(f.get("id")) is int and f["id"] > 0 and f["id"] not in ids, "unique_file_id_required")
            ids.add(f["id"])
            url = "https://ndownloader.figshare.com/files/%s" % f["id"]
        else:
            require(isinstance(f.get("checksum"),str) and f["checksum"].startswith("md5:"), "MD5_file_checksum_required")
            checksum = f["checksum"][4:]
            url = f.get("links",{}).get("self", "")
            parsed = urlsplit(url)
            require(parsed.scheme == "https" and parsed.hostname == "zenodo.org" and not parsed.username
                    and not parsed.password and parsed.port in (None,443), "unexpected_Zenodo_file_URL")
        require(isinstance(checksum,str) and re.fullmatch(r"[a-fA-F0-9]{32}",checksum), "repository_checksum_required")
        assets.append(dict(name=name, local_name=name, size_bytes=size, checksum_algorithm="md5", checksum=checksum.lower(),
            container="zip" if name.lower().endswith(".zip") else "file", download_url=url,
            source_record_doi=spec["dataset"][4:]))
        if spec["repository"] == "figshare":
            assets[-1]["file_id"] = f["id"]
    if duplicate_names:
        # Isolate every file in this article, including names differing only
        # by case/Unicode normalization. No generated filename can overwrite
        # an original filename, and the original extension stays intact.
        for a in assets:
            a["local_name"] = "file-%s/%s" % (a["file_id"], a["name"])
    return assets


def expand_collection(spec, stage, r_library, fetch_metadata, list_collection):
    """Only snapshot current membership; never pretend to reconstruct old membership."""
    pinned = fetch_metadata(spec)
    save(stage/"requested.metadata.json",pinned)
    require(str(pinned.get("id")) == spec["id"] and pinned.get("version") == spec["version"], "collection_identity_mismatch")
    latest_spec = dict(spec,api="https://api.figshare.com/v2/collections/"+spec["id"])
    before = fetch_metadata(latest_spec)
    save(stage/"current.before.json",before)
    require(str(before.get("id")) == spec["id"] and before.get("version") == spec["version"],
            "historical_collection_membership_not_supported")
    count = before.get("articles_count")
    require(type(count) is int and 0 < count < 1000 and pinned.get("articles_count") == count,
            "collection_count_or_single_page_limit")
    def checked_listing(name):
        listing = list_collection(spec["id"],stage/name,r_library)
        # Also save injected test/provider results if the bridge did not write them.
        if not (stage/name).exists(): save(stage/name,listing)
        rows = listing.get("articles")
        require(isinstance(rows,list) and len(rows) == count,"incomplete_collection_listing")
        urls, ids = [], set()
        for row in rows:
            child = route("https://doi.org/"+str(row.get("doi","")))
            require(child["repository"] == "figshare" and child["id"] == str(row.get("article_id")),
                    "collection_article_identity_mismatch")
            require(child["id"] not in ids,"duplicate_collection_article")
            ids.add(child["id"]); urls.append(child["canonical"])
        return sorted(urls)
    urls = checked_listing("members.before.json")
    require(checked_listing("members.after.json") == urls,"collection_membership_changed")
    after = fetch_metadata(latest_spec)
    save(stage/"current.after.json",after)
    require(before == after,"collection_metadata_changed")
    save(stage/"snapshot.json",dict(collection=spec["canonical"],articles=urls,
         scope="Current membership observed twice and article versions pinned; not a historical or atomic repository snapshot."))
    return urls


def run(urls, output, r_library="", max_bytes=1024**3, max_records=None, selection_request=None,
        fetch_metadata=metadata, acquire=download_asset, physionet=run_batch, list_collection=collection_articles):
    require(isinstance(urls,list) and 0 < len(urls) <= 100 and all(isinstance(u,str) for u in urls), "1_to_100_URLs_required")
    require(isinstance(max_bytes,int) and max_bytes > 0 and (max_records is None or max_records > 0), "positive_limits_required")
    output = Path(output).resolve()
    marker = output / "mixed-store.json"
    if output.exists():
        require(marker.is_file() and json.loads(marker.read_text()) == {"version":1}, "not_a_managed_mixed_store")
    else:
        output.mkdir(parents=True)
        save(marker,{"version":1})
    store = output / "catalog"
    if not store.exists():
        store.mkdir()
        save(store / "proposal-store.json", {"version":1})
    db = sqlite3.connect(store / "catalog.sqlite")
    catalog(db); db.close()
    run_dir = output / "runs" / uuid.uuid4().hex
    run_dir.mkdir(parents=True)
    report = dict(version="mixed-batch/1.0", run_dir=str(run_dir), catalog=str(store / "catalog.sqlite"), entries=[],
        limits=dict(max_payload_bytes=max_bytes,max_records_per_physionet_dataset=max_records,
                    budget_scope="Declared uncached file payload reservations; API metadata and retries excluded, not a hard network cap"),
        scope="Common storage and purpose-specific holds; no automatic approval from parser success.")
    remaining, seen = max_bytes, {}
    expansion_remaining = min(max_bytes, 1024**3)
    report["limits"]["max_batch_expanded_bytes"] = expansion_remaining
    queue = [(url,None) for url in urls]
    for index, (url,parent) in enumerate(queue):
        entry = dict(url=url,status="held",errors=[],purpose_assessments={p:"unassessed" for p in ("activation-timing","amplitude-pooling")})
        if parent is not None: entry["parent_collection_index"] = parent
        report["entries"].append(entry)
        try:
            spec = route(url)
            entry.update(repository=spec["repository"],dataset=spec["dataset"])
            if spec["canonical"] in seen:
                entry.update(status="duplicate",duplicate_of=seen[spec["canonical"]],reason="canonical_source_already_requested")
                save(run_dir / ("source-%03d.status.json" % index),entry)
                continue
            seen[spec["canonical"]] = index
            if spec["repository"] == "figshare-collection":
                stage = run_dir / ("collection-%03d" % index)
                stage.mkdir()
                entry["source_report"] = str(stage)
                children = expand_collection(spec,stage,r_library,fetch_metadata,list_collection)
                require(len(queue)+len(children) <= 1000,"expanded_batch_entry_limit")
                queue.extend((child,index) for child in children)
                entry.update(status="expanded",articles=children)
                save(run_dir / ("source-%03d.status.json" % index),entry)
                continue
            if remaining <= 0:
                raise ValueError("batch_payload_budget_exhausted")
            if spec["repository"] == "physionet":
                result = physionet([spec["canonical"]],store,r_library,remaining,max_records)
                remaining = max(0,remaining-result["network_bytes"])
                dataset = result["datasets"][0]
                entry.update(status=dataset["status"],details=dataset,run_id=result["run_id"],
                             converted=sum(r["status"]=="converted" for r in dataset["records"]))
                if dataset.get('source_report'):
                    entry['source_report'] = dataset['source_report']
            else:
                stage = run_dir / ("source-%03d" % index)
                stage.mkdir()
                doc = fetch_metadata(spec)
                save(stage / "repository.metadata.json",doc)
                save(stage/'repository.evidence.json',dict(source_uri=spec['api'],source_sha256=sha(stage/'repository.metadata.json'),repository=spec['repository']))
                assets = file_assets(spec,doc)
                if max_records is not None:
                    # Select payloads before downloading, not after downloading
                    # the whole repository. ZIP selection is at archive level.
                    docs = [a for a in assets if Path(a['name']).suffix.lower() in ('.md','.pdf','.html','.xml','.json')
                            or a['name'].lower().startswith(('readme','license'))]
                    signals = [a for a in assets if a not in docs and Path(a['name']).suffix.lower() in
                               ('.csv','.tsv','.txt','.mat','.c3d','.trc','.mot','.sto','.h5','.hdf5','.edf','.hea','.zip','.rar','.7z','.gz','.tar')]
                    selected = signals[:max_records] + docs[:20]
                    entry['file_selection'] = dict(available=len(assets),selected=len(selected),
                        omitted=len(assets)-len(selected),scope='repository-file-or-archive, not individual records inside archives')
                    assets = selected
                entry['source_report'] = str(stage)
                unsupported = [a for a in assets if Path(a['name']).suffix.lower() in ('.rar','.7z','.gz','.tar')]
                entry['unsupported_before_download'] = [dict(name=a['name'],reason='unsupported_archive_container',size_bytes=a['size_bytes']) for a in unsupported]
                assets = [a for a in assets if a not in unsupported]
                cache_root = output/'payload-cache'
                cached, cache_errors = {}, {}
                for a in assets:
                    try:
                        cached[a['local_name']] = payload_cache.lookup(cache_root, a)
                    except Exception as exc:
                        cache_errors[a['local_name']] = str(exc)
                declared = sum(a["size_bytes"] for a in assets if not cached.get(a['local_name'])
                               and a['local_name'] not in cache_errors)
                require(declared <= remaining,"declared_files_exceed_remaining_batch_budget")
                remaining -= declared
                raw = stage / "raw"; raw.mkdir()
                acquired = []
                for a in assets:
                    try:
                        require(a['local_name'] not in cache_errors, cache_errors.get(a['local_name'], 'invalid_cache'))
                        target = raw/a["local_name"]
                        target.parent.mkdir(parents=True, exist_ok=True)
                        previous = cached.get(a['local_name'])
                        if previous is not None:
                            shutil.copyfile(previous, target)
                            payload_cache.verify(target, a)
                            checked = {'pass': True, 'acquisition_backend': 'verified-payload-cache', 'source': str(previous)}
                        else:
                            checked = acquire(a,target,r_library)
                            require(checked["pass"], "download_verification_failed")
                            payload_cache.verify(target, a)
                            payload_cache.store(cache_root, a, target, checked)
                        acquired.append(dict(name=a["name"],local_name=a["local_name"],file_id=a.get("file_id"),
                            sha256=sha(target),verification=checked))
                    except Exception as exc:
                        entry["errors"].append(dict(file=a["name"],local_name=a["local_name"],file_id=a.get("file_id"),error=str(exc)))
                save(stage / "acquisition.json",acquired)
                # Failed/unverified downloads are not supplied to readers.
                successful = {a["local_name"] for a in acquired}
                extracted_files, expanded_archives = set(), set()
                entry["archives"] = []
                for a in acquired:
                    if not a["name"].lower().endswith(".zip"):
                        continue
                    folder = "archive-" + a["sha256"]
                    if a["local_name"] != a["name"]:
                        folder += "-file-%s" % a["file_id"]
                    try:
                        manifest = extract_zip(raw/a["local_name"], raw/folder, max_bytes=expansion_remaining)
                        expansion_remaining -= manifest["expanded_bytes"]
                        manifest["dataset"] = spec["dataset"]
                        manifest["repository_metadata"] = str(stage / "repository.metadata.json")
                        manifest["repository_file"] = dict(name=a["name"], local_name=a["local_name"], file_id=a["file_id"])
                        manifest_path = stage / (folder + ".provenance.json")
                        save(manifest_path,manifest)
                        entry["archives"].append(dict(file=a["name"],status="extracted",provenance=str(manifest_path),
                            members=len(manifest["members"])))
                        extracted_files.update(folder + "/" + m["member"] for m in manifest["members"])
                        expanded_archives.add(a["local_name"])
                    except Exception as exc:
                        entry["errors"].append(dict(file=a["name"],error=str(exc)))
                        entry["archives"].append(dict(file=a["name"],status="held",reason=str(exc)))
                inventory = discover(raw)
                inventory["inventory"] = [r for r in inventory["inventory"] if r["source"] in
                    ({a["local_name"] for a in assets} | extracted_files) - expanded_archives]
                inventory["files"] = [f for f in inventory["files"] if f in successful | extracted_files]
                save(stage / "inventory.json",inventory)
                entry["not_converted"] = [r for r in inventory["inventory"] if r["role"] != "signal_candidate"]
                entry["acquired_files"] = len(acquired)
                entry["reused_payloads"] = sum(a['verification'].get('acquisition_backend') == 'verified-payload-cache' for a in acquired)
                entry["downloaded_payloads"] = len(acquired) - entry['reused_payloads']
                if inventory["files"]:
                    from harmo.document_clock import propose as clock_proposal
                    clock_metadata, clock_holds = {}, []
                    for relative in inventory['files']:
                        try:
                            proposed=clock_proposal(raw/relative,stage/'repository.metadata.json')
                            if proposed: clock_metadata[relative]=proposed
                        except (ValueError, OSError, IndexError, TypeError) as exc:
                            clock_holds.append(dict(source=relative,reason=str(exc)))
                    save(stage/'clock-proposals.json',dict(metadata=clock_metadata,held=clock_holds))
                    automap(raw,stage / "proposals",inventory["files"],metadata=clock_metadata)
                    stored = ingest(stage / "proposals",raw,store,spec["dataset"])
                    entry.update(converted=stored["inserted"]+stored["reused"],ingestion=stored)
                else:
                    entry.update(converted=0,reason="no_supported_signal_files; see inventory and archive holds")
                unresolved_files = [r for r in entry["not_converted"] if r["role"] != "documentation"]
                entry["status"] = "converted-with-use-holds" if entry["converted"] and not entry["errors"] and not unresolved_files and not unsupported and not entry.get("ingestion",{}).get("held") else "partial"
            entry["purpose_reason"] = "Source-specific semantic and processing evidence must satisfy the existing use policy; downloading/converting alone does not approve reuse."
        except Exception as exc:
            entry["errors"].append(dict(error=str(exc)))
        print(json.dumps({k:entry[k] for k in ("url","status")}),flush=True)
        save(run_dir / ("source-%03d.status.json" % index),entry)
    def effective_status(index):
        e = report["entries"][index]
        return effective_status(e["duplicate_of"]) if e["status"] == "duplicate" else e["status"]
    for index in reversed(range(len(report["entries"]))):
        e = report["entries"][index]
        if e["status"] == "expanded":
            children = [j for j,c in enumerate(report["entries"]) if c.get("parent_collection_index") == index]
            e["child_indices"] = children
            e["status"] = "completed-with-use-holds" if children and all(effective_status(j) in
                ("completed","converted-with-use-holds") for j in children) else "partial"
            save(run_dir / ("collection-%03d.final.json" % index),e)
    if selection_request is not None:
        from harmo.public_export import export_catalog
        try:
            selected = export_catalog(store / "catalog.sqlite",selection_request,run_dir / "selection")
            report["selection"] = dict(status=selected["status"],exported=selected["exported"],output=str(run_dir / "selection"))
        except Exception as exc:
            report["selection"] = dict(status="failed",error=str(exc))
    report["remaining_payload_budget"] = remaining
    report["remaining_expansion_budget"] = expansion_remaining
    report["status"] = "completed-with-use-holds" if all(effective_status(i) in
        ("completed","converted-with-use-holds","completed-with-use-holds") for i in range(len(report["entries"]))) else "partial"
    if selection_request is not None and not report.get("selection",{}).get("exported",0):
        report["status"] = "partial"
    save(run_dir / "summary.json",report)
    return report
