"""PhysioNet URL batch acquisition, verified PhysioIO conversion and local catalog.

No uploaded code is executed. No cross-study pooling is implied by cataloging.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import ssl
import subprocess
import sys
import time
import urllib.parse as up
import urllib.request as ur
import uuid
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

VERSION = "public-batch/0.3.0"


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, obj):
    with Path(path).open("x", encoding="utf8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, allow_nan=False)


def safe_url(url):
    p = up.urlsplit(url)
    if p.scheme != "https" or p.hostname not in ("physionet.org", "www.physionet.org") or p.username or p.password or p.port not in (None, 443):
        raise ValueError("Only public HTTPS PhysioNet URLs are supported by this connector")
    return url


def safe_relative(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or ".." in p.parts or "\\" in name or "\x00" in name:
        raise ValueError("unsafe relative path")
    return name


class Redirect(ur.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Transport:
    def __init__(self, max_bytes):
        self.remaining = max_bytes
        self.downloaded = 0
        context = ssl.create_default_context()
        try:
            import certifi
            context.load_verify_locations(certifi.where())
        except ImportError:
            pass
        self.opener = ur.build_opener(Redirect(), ur.HTTPSHandler(context=context))

    def fetch(self, url, dest, limit=256*1024*1024):
        safe_url(url)
        request = ur.Request(url, headers={"User-Agent": "harmo-public-batch/0.1", "Accept-Encoding": "identity"})
        start = time.monotonic()
        partial = dest.with_name(dest.name + ".partial")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self.opener.open(request, timeout=30) as response, partial.open("xb") as f:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > min(limit, self.remaining):
                raise ValueError("download byte budget exceeded")
            n = 0
            while True:
                block = response.read(min(1024*1024, limit-n+1, self.remaining+1))
                if not block:
                    break
                n += len(block)
                self.remaining -= len(block)
                self.downloaded += len(block)
                if n > limit or self.remaining < 0 or time.monotonic()-start > 300:
                    raise ValueError("download resource limit exceeded")
                f.write(block)
            if declared and n != int(declared):
                raise ValueError("truncated download")
        partial.rename(dest)
        return {"url": url, "path": str(dest), "bytes": n, "sha256": sha(dest)}


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)


def normalize_url(url):
    safe_url(url)
    p = up.urlsplit(url)
    match = re.fullmatch(r"/(?:content|files)/([A-Za-z0-9_-]+)/([0-9]+(?:\.[0-9]+)+)/?", p.path)
    if not match or p.query or p.fragment:
        raise ValueError("Specify a versioned PhysioNet dataset root URL")
    slug, version = match.groups()
    return slug, version, "https://physionet.org/content/%s/%s/" % (slug, version)


def inventory(root_url, destination, transport, max_files=10000):
    pending, seen, files = [""], set(), []
    while pending:
        relative = pending.pop(0)
        if relative in seen:
            continue
        seen.add(relative)
        if len(seen) > 1000:
            raise ValueError("directory limit exceeded")
        listing = destination / ("listing-%04d.html" % len(seen))
        transport.fetch(root_url + up.quote(relative, safe="/"), listing, 8*1024*1024)
        parser = Links()
        parser.feed(listing.read_text())
        for href in parser.links:
            # Directory listings contain direct child names only; never follow parents.
            if href == "../" or href.startswith(("/", "?", "#")):
                continue
            if up.urlsplit(href).scheme or up.urlsplit(href).netloc:
                continue
            child = up.unquote(href)
            safe_relative(child)
            if "/" in child.rstrip("/") or "?" in child or "#" in child:
                raise ValueError("unexpected directory entry")
            name = relative + child
            if child.endswith("/"):
                pending.append(name)
            else:
                files.append(name)
            if len(files) > max_files:
                raise ValueError("file count limit exceeded")
    return sorted(set(files))


def inspect_header(path, root):
    """Fail before R can apply missing-field defaults or follow external files."""
    lines = [x.strip() for x in path.read_text().splitlines() if x.strip() and not x.lstrip().startswith("#")]
    fields = lines[0].split()
    if len(fields) < 4 or "/" in fields[0]:
        raise ValueError("explicit single-segment header required")
    count, fs, n = int(fields[1]), float(fields[2].split("/")[0]), int(fields[3])
    if count < 1 or n < 2 or not math.isfinite(fs) or fs <= 0 or n*count > 20_000_000:
        raise ValueError("invalid sampling dimensions or cell limit")
    signals = []
    for line in lines[1:1+count]:
        f = line.split()
        if len(f) < 9 or f[1] not in ("16", "61", "80", "212", "24", "32"):
            raise ValueError("explicit supported signal specification required")
        safe_relative(f[0])
        target = (path.parent / f[0]).resolve()
        if root.resolve() not in target.parents:
            raise ValueError("header references outside dataset")
        gain = re.fullmatch(r"([0-9.eE+-]+)(?:\((-?\d+)\))?/([^\s]+)", f[2])
        if not gain or not math.isfinite(float(gain[1])) or float(gain[1]) <= 0:
            raise ValueError("explicit positive gain and unit required")
        signals.append({"file": f[0], "format": int(f[1]), "gain": float(gain[1]),
                        "baseline": int(gain[2]) if gain[2] else int(f[4]),
                        "unit": gain[3], "label": " ".join(f[8:])})
    if len(signals) != count or len({s["file"] for s in signals}) != 1 or len({s["format"] for s in signals}) != 1:
        raise ValueError("PhysioIO requires single-file single-format records")
    if len({s["label"] for s in signals}) != count:
        raise ValueError("duplicate labels")
    return {"fs": fs, "rows": n, "signals": signals}


def verify_calibration(values, digital, baseline, gain):
    """Allow only bounded IEEE-754 roundoff, not even one ADC-code error.

    Reciprocal multiplication and direct division need not be bit-identical.
    Preserve the reader's values, and independently require reconstruction of
    every original integer code. No empirical percentage tolerance is fitted.
    """
    import numpy as np
    expected = (digital.astype(float)-baseline)/gain
    tolerance = 8*np.finfo(np.float64).eps*(np.abs(digital.astype(float))+abs(baseline))/gain
    if not np.isfinite(values).all() or not np.isfinite(expected).all():
        raise ValueError('nonfinite_calibration')
    difference = np.abs(values-expected)
    if np.any(difference > tolerance) or np.any(difference*gain >= 0.25):
        raise ValueError('reader_calibration_exceeds_roundoff_bound')
    if not np.array_equal(np.rint(values*gain+baseline), digital):
        raise ValueError('reader_does_not_reconstruct_original_ADC_codes')
    return dict(exact=bool(np.array_equal(values,expected)),
                max_absolute_error=float(difference.max(initial=0)),
                max_error_ADC_steps=float((difference*gain).max(initial=0)),
                original_integer_codes_reconstructed=True)


def convert(header, raw_root, output, r_library):
    import numpy as np
    import wfdb
    h = inspect_header(header, raw_root)
    output.mkdir()
    script = Path(__file__).parent / "r_bridge/scripts/read_public_wfdb.R"
    proc = subprocess.run(["Rscript", "--vanilla", str(script), str(header.with_suffix("")), str(output), r_library or ""],
                          capture_output=True, text=True, timeout=180)
    (output / "reader.log").write_text(proc.stdout + proc.stderr)
    if proc.returncode:
        raise ValueError("PhysioIO reader failed; see reader.log")
    meta = json.loads((output / "reader.json").read_text())
    values = np.fromfile(output / "samples.f64", dtype="<f8").reshape((h["rows"],len(h["signals"])), order="F")
    labels = meta["labels"] if isinstance(meta["labels"], list) else [meta["labels"]]
    units = meta["units"] if isinstance(meta["units"], list) else [meta["units"]]
    if labels != [s["label"] for s in h["signals"]] or units != [s["unit"] for s in h["signals"]] or meta["sampling_rate_hz"] != h["fs"]:
        raise ValueError("reader metadata changed")
    # PhysioIO does not map WFDB missing-data sentinel codes to NA. Make that
    # representation conversion explicit, retaining original byte files.
    reference = wfdb.rdrecord(str(header.with_suffix("")), physical=False)
    digital = reference.d_signal
    sentinel = {16:-32768, 61:-32768, 80:-128, 212:-2048, 24:-8388608, 32:-2147483648}
    missing = np.zeros(values.shape, dtype=bool)
    calibration_checks = []
    for i, s in enumerate(h["signals"]):
        calibration_checks.append(verify_calibration(values[:,i],digital[:,i],s['baseline'],s['gain']))
        missing[:,i] = digital[:,i] == sentinel[s["format"]]
    values[missing] = np.nan
    if np.isinf(values).any():
        raise ValueError("infinite signal")
    timestamps = np.arange(h["rows"],dtype=float)/h["fs"]
    with (output / "normalized.npz").open("xb") as f:
        np.savez(f, time=timestamps, values=values)
    with np.load(output / "normalized.npz") as saved:
        np.testing.assert_array_equal(saved["values"], values)
    meta.update(h, wfdb_version=wfdb.__version__, missing_samples=int(missing.sum()),
                validation="Independent WFDB digital calibration within bounded float64 roundoff and exact reconstruction of every ADC code",
                calibration_checks=calibration_checks,
                calibration_rule='8*float64_epsilon*(abs(digital)+abs(baseline))/gain; error<0.25 ADC step; exact integer reconstruction',
                time_unit="s", missing_conversion="WFDB sentinel to NaN", sha256=sha(output/"normalized.npz"))
    write_json(output / "conversion.json", meta)
    return meta


def catalog(connection):
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, manifest TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS assets(dataset TEXT, path TEXT, sha256 TEXT, location TEXT,
      PRIMARY KEY(dataset,path,sha256));
    CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, dataset TEXT, name TEXT,
      status TEXT, location TEXT, metadata TEXT);
    CREATE TABLE IF NOT EXISTS assessments(record_id TEXT, purpose TEXT, status TEXT, reasons TEXT,
      PRIMARY KEY(record_id,purpose));
    CREATE TABLE IF NOT EXISTS comparisons(left_id TEXT, right_id TEXT, channel TEXT, purpose TEXT,
      status TEXT, details TEXT, PRIMARY KEY(left_id,right_id,channel,purpose));
    CREATE TABLE IF NOT EXISTS source_screens(record_id TEXT, run_id TEXT, details TEXT,
      PRIMARY KEY(record_id,run_id));
    """)


def contracts(record_id, dataset, meta, dest, base):
    from harmo.evidence.models import (ArrayReference, ChannelDescriptor, HarmoRecordingBundle,
        SourceAsset, Timebase, TrustReport, TrustEvidence, TrustStatus)
    bundle = HarmoRecordingBundle(record_id,dataset,"unknown:"+record_id,"unknown:"+record_id,
        ArrayReference(str(dest/"normalized.npz"),"npz","float64",[meta["rows"],len(meta["signals"])],meta["sha256"]),
        Timebase(meta["fs"],meta["rows"]),
        [ChannelDescriptor("ch-%d"%i,s["label"],s["unit"],"unknown",source_name=s["label"]) for i,s in enumerate(meta["signals"])],
        [SourceAsset(base+up.quote(n,safe="/"),h,dataset_release=dataset) for n,h in meta["source_sha256"].items()])
    trust = TrustReport(record_id,[
        TrustEvidence("semantic_fidelity",TrustStatus.UNASSESSED,"public-source-semantics","Header labels retained; anatomy, sensor, task and processing evidence not independently qualified."),
        TrustEvidence("scientific_validity",TrustStatus.UNASSESSED,"public-source-use","Scientific purpose eligibility not established by conversion."),
        TrustEvidence("computational_reproducibility",TrustStatus.PASS,"dual-reader-values",meta["validation"]),
        TrustEvidence("traceability",TrustStatus.PASS,"source-hashes","Local header and signal hashes retained.")])
    write_json(dest/"recording.bundle.json",bundle.to_dict())
    write_json(dest/"trust.report.json",trust.to_dict())


def compare_catalog(db, record_id, dest):
    from harmo.evidence.models import HarmoRecordingBundle, TrustReport
    from harmo.evidence.policy import ComparabilityPolicyEngine, ComparabilityRequest
    semantic = dest/"recording.semantic.bundle.json"
    bundle = HarmoRecordingBundle.from_dict(json.loads((semantic if semantic.exists() else dest/"recording.bundle.json").read_text()))
    trust = TrustReport.from_dict(json.loads((dest/"trust.report.json").read_text()))
    details = []
    # Bounded reference selection: one prior record per exact source label, not
    # a claim that those labels identify equivalent muscles across studies.
    for channel in bundle.channels:
        if "emg" not in channel.name.lower():
            continue
        for prior_id, location in db.execute("SELECT id,location FROM records WHERE id<>? ORDER BY id",(record_id,)):
            prior = Path(location)
            if not (prior/"recording.bundle.json").exists():
                continue
            semantic_prior = prior/"recording.semantic.bundle.json"
            left = HarmoRecordingBundle.from_dict(json.loads((semantic_prior if semantic_prior.exists() else prior/"recording.bundle.json").read_text()))
            match = next((c for c in left.channels if c.name==channel.name),None)
            if match is None:
                continue
            lt = TrustReport.from_dict(json.loads((prior/"trust.report.json").read_text()))
            for purpose in ("activation-timing","amplitude-pooling"):
                request = ComparabilityRequest(channel.name+"."+purpose,purpose,match.channel_id,channel.channel_id)
                result = ComparabilityPolicyEngine().evaluate_with_details(left,bundle,lt,trust,request)
                status = result["assertion"]["status"]
                db.execute("INSERT OR IGNORE INTO comparisons VALUES(?,?,?,?,?,?)",(prior_id,record_id,channel.name,purpose,status,json.dumps(result)))
                details.append(result)
            break
    write_json(dest/"comparisons.json",details)
    return len(details)


def run_batch(urls, output, r_library="", max_bytes=1024**3, max_records=None):
    """Append one run to a local, source-versioned catalog. No remote writes."""
    if not isinstance(urls, list) or not urls or len(urls)>100 or any(not isinstance(u,str) for u in urls):
        raise ValueError("1..100 URL strings required")
    if max_bytes <= 0 or (max_records is not None and max_records < 1):
        raise ValueError("positive resource limits required")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    run_dir = output / "runs" / run_id
    run_dir.mkdir(parents=True)
    transport = Transport(max_bytes)
    summary = {"version":VERSION,"run_id":run_id,"datasets":[],"network_bytes":0,
               "limits":{"max_bytes":max_bytes,"max_records_per_dataset":max_records},
               "scope":"Source-preserving catalog integration, not cross-study averaging or permission to pool."}
    db = sqlite3.connect(output / "catalog.sqlite")
    catalog(db)
    for url in dict.fromkeys(urls):
        entry = {"url":url,"status":"failed","records":[],"errors":[]}
        summary["datasets"].append(entry)
        try:
            slug, version, canonical = normalize_url(url)
            dataset = slug+"/"+version
            stage = run_dir / slug / version
            stage.mkdir(parents=True)
            entry['source_report'] = str(stage)
            raw = stage / "raw"
            raw.mkdir()
            transport.fetch(canonical, stage / "description.html", 8*1024*1024)
            from harmo.source_evidence import extract_description, assess_record
            source_evidence = extract_description(stage / "description.html", canonical)
            write_json(stage / "source.evidence.json", source_evidence)
            base = "https://physionet.org/files/"+dataset+"/"
            if max_records is not None:
                from harmo.physionet_selection import select_records, acquire_selected
                selection = select_records(base, stage, transport, max_records)
                names = selection['records']
                entry['selection'] = selection
            else:
                names = inventory(base, stage, transport)
                write_json(stage / "inventory.json",names)
            headers = [n for n in names if n.endswith(".hea")]
            other_signals = [n for n in names if PurePosixPath(n).suffix.lower() in
                (".edf",".bdf",".mat",".h5",".hdf5",".c3d",".trc",".mot",".sto",".zip",".gz",".tar")]
            entry["formats_not_yet_connected"] = other_signals
            selected = headers if max_records is None else headers[:max_records]
            # A capped validation run must never masquerade as a full dataset run.
            entry.update(dataset=dataset, available_records=len(headers), selected_records=len(selected),
                         selection_complete=len(headers)==len(selected))
            if max_records is not None:
                entry.update(available_records=None, selection_complete=False)
            selected_stems = {str(PurePosixPath(h).with_suffix("")) for h in selected}
            files = names if max_records is None else [n for n in names if n in selected or
                    str(PurePosixPath(n).with_suffix("")) in selected_stems or n.endswith((".csv",".json",".md",".pdf")) or
                    PurePosixPath(n).name in ("RECORDS","ANNOTATORS","LICENSE.txt","SHA256SUMS.txt")]
            assets = []
            if max_records is not None:
                assets, acquisition_errors = acquire_selected(selection, base, stage, transport)
                entry['errors'].extend(acquisition_errors)
                files = []
                for asset in assets:
                    db.execute("INSERT OR IGNORE INTO assets VALUES(?,?,?,?)",(dataset,asset['relative_path'],asset['sha256'],asset['path']))
                db.commit()
            for name in files:
                try:
                    asset = transport.fetch(base+up.quote(name,safe="/"),raw/name)
                    asset["relative_path"] = name
                    assets.append(asset)
                    db.execute("INSERT OR IGNORE INTO assets VALUES(?,?,?,?)",(dataset,name,asset["sha256"],asset["path"]))
                    db.commit()
                except Exception as exc:
                    entry["errors"].append({"file":name,"error":str(exc)})
            write_json(stage / "assets.json",assets)
            expected = {}
            for checksum in raw.rglob("SHA256SUMS.txt"):
                for line in checksum.read_text().splitlines():
                    match = re.fullmatch(r"([a-fA-F0-9]{64})\s+\*?(.+)",line)
                    if match:
                        expected[str((checksum.parent/match[2]).relative_to(raw))] = match[1].lower()
            bad = {a["relative_path"] for a in assets if a["relative_path"] in expected and a["sha256"] != expected[a["relative_path"]]}
            for name in selected:
                record = {"name":name,"status":"needs_review"}
                entry["records"].append(record)
                rid = hashlib.sha256((dataset+"/"+name).encode()).hexdigest()
                dest = stage / "converted" / rid
                dest.parent.mkdir(exist_ok=True)
                try:
                    h = inspect_header(raw/name,raw)
                    dependencies = [name,str(PurePosixPath(name).parent/h["signals"][0]["file"])]
                    if any(n in bad for n in dependencies):
                        raise ValueError("repository checksum mismatch")
                    meta = convert(raw/name,raw,dest,r_library)
                    meta["source_sha256"] = {n:sha(raw/n) for n in dependencies}
                    meta["repository_checksums"] = {n:("verified" if n in expected else "not_provided") for n in dependencies}
                    # Content-addressed record version; source changes cannot overwrite an old record.
                    record_id = hashlib.sha256((rid+json.dumps(meta["source_sha256"],sort_keys=True)+VERSION).encode()).hexdigest()
                    record.update(status="converted",id=record_id,rows=meta["rows"],channels=len(meta["signals"]),location=str(dest))
                    db.execute("INSERT OR IGNORE INTO records VALUES(?,?,?,?,?,?)",(record_id,dataset,name,"converted",str(dest),json.dumps(meta)))
                    contracts(record_id,dataset,meta,dest,base)
                    screen = assess_record(meta, source_evidence)
                    write_json(dest/"source.screen.json",screen)
                    from harmo.semantic_promotion import promote_directory
                    promote_directory(dest, name, raw, base)
                    db.execute("INSERT INTO source_screens VALUES(?,?,?)",(record_id,run_id,json.dumps(screen)))
                    record["pairwise_assessments"] = compare_catalog(db,record_id,dest)
                    for purpose in ("activation-timing","amplitude-pooling"):
                        reasons = screen["purposes"][purpose]["reasons"]
                        db.execute("INSERT OR IGNORE INTO assessments VALUES(?,?,?,?)",(record_id,purpose,"unassessed",json.dumps(reasons)))
                    db.commit()
                    write_json(dest/"record.json",dict(record,metadata=meta,scientific_use="unassessed"))
                except Exception as exc:
                    db.rollback()
                    record["status"] = "needs_review"
                    record["reason"] = str(exc)
            entry["status"] = "completed" if selected and not entry["errors"] and not other_signals and all(r["status"]=="converted" for r in entry["records"]) and entry["selection_complete"] else "partial"
            entry["checksum_mismatches"] = sorted(bad)
        except Exception as exc:
            entry["errors"].append({"error":str(exc)})
        print(json.dumps({"url":url,"status":entry["status"],"converted":sum(r["status"]=="converted" for r in entry["records"])}),flush=True)
    summary["network_bytes"] = transport.downloaded
    write_json(run_dir / "summary.json", summary)
    db.execute("INSERT INTO runs VALUES(?,?)",(run_id,json.dumps(summary)))
    db.commit()
    db.close()
    return summary
