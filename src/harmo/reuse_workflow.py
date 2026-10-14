"""Resumable entry point for the paper's registered two-study timing profile."""
import json
from pathlib import Path

from harmo.config.automap import require, save
from harmo.evidence.io import sha256_file
from harmo.timing_workflow import checked_profile
from harmo.schema_resources import schema_directory

URLS = {
    "https://doi.org/10.6084/m9.figshare.c.4494755.v1",
    "https://doi.org/10.6084/m9.figshare.27677016.v1",
}


def implementation_identity():
    package = Path(__file__).resolve().parent
    code = {str(p.relative_to(package)): sha256_file(str(p)) for p in sorted(package.rglob("*.py"))}
    schemas = schema_directory()
    code.update({"schemas/" + p.name: sha256_file(str(p)) for p in sorted(schemas.glob("*.json"))})
    return code


class Stages:
    """Reuse only completed stages with unchanged inputs and output bytes.

    Incomplete attempts are retained. Retrying creates another attempt directory.
    Callers must include all external dependencies in the stage signature.
    """
    def __init__(self, output, resume=False):
        self.output = Path(output).resolve()
        if self.output.exists():
            require(resume and (self.output / "workflow.json").is_file(), "use_new_output_or_explicit_resume")
            require(json.loads((self.output / "workflow.json").read_text()) == {"version": 1}, "unknown_workflow")
        else:
            self.output.mkdir(parents=True)
            save(self.output / "workflow.json", {"version": 1})

    def run(self, name, signature, action):
        receipt = self.output / (name + ".receipt.json")
        if receipt.exists():
            saved = json.loads(receipt.read_text())
            require(saved["signature"] == signature, "stage_inputs_changed:" + name)
            for relative, digest in saved["artifacts"].items():
                path = (self.output / relative).resolve()
                require(self.output in path.parents and path.is_file() and sha256_file(str(path)) == digest,
                        "stage_output_changed:" + name)
            print("reuse: verified completed stage " + name, flush=True)
            return saved["result"]
        index = 1
        while (self.output / (name + "-%03d" % index)).exists():
            index += 1
        attempt = self.output / (name + "-%03d" % index)
        print("reuse: running " + name, flush=True)
        try:
            result = action(attempt)
            # Actions own their output directory; failures never acquire a receipt.
            require(attempt.is_dir(), "stage_did_not_create_output")
            artifacts = {str(p.relative_to(self.output)): sha256_file(str(p))
                         for p in sorted(attempt.rglob("*")) if p.is_file()}
            require(bool(artifacts), "stage_has_no_artifacts")
            save(receipt, dict(signature=signature, artifacts=artifacts, result=result))
            return result
        except Exception as exc:
            save(self.output / (name + "-%03d.failure.json" % index),
                 dict(error=str(exc), type=type(exc).__name__))
            raise


def acquire_sources(source_root, protocol, max_bytes, download):
    """Reuse verified cache or fetch exactly the registered file IDs.

    Uses the existing checksum-bound downloader, not a new signal reader.
    No collection-wide discovery or unregistered file acquisition is implied.
    """
    from harmo.validation_corpus import verify_corpus_asset, _download_asset
    source_root = Path(source_root).resolve()
    specs = [(name, meta) for group in protocol["source_assets"].values() for name, meta in group.items()]
    missing = sum(meta["bytes"] for name, meta in specs if not (source_root / name).is_file())
    require(max_bytes >= 0 and missing <= max_bytes, "download_budget_exceeded")
    require(download or missing == 0, "missing_cache_requires_download_option")
    source_root.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, meta in specs:
        path = source_root / name
        asset = dict(size_bytes=meta["bytes"], checksum_algorithm="md5", checksum=meta["md5"],
                     container="zip" if name.endswith(".zip") else "file")
        cached = path.exists()
        url = "https://ndownloader.figshare.com/files/%s" % meta["file_id"]
        checked = verify_corpus_asset(path, asset) if cached else _download_asset(url, path, asset)
        require(checked["pass"], "source_integrity_failure:" + name)
        rows.append(dict(name=name, path=str(path), url=url, sha256=checked["sha256"], cached=cached))
    return rows


def acquire_article(source_root, article=None, download=False):
    from harmo.catalog_evidence import unit_claim
    from urllib.request import urlopen, Request
    path = Path(article).resolve() if article else Path(source_root).resolve() / "study38.article.xml"
    if not path.exists():
        require(article is None and download, "missing_article_requires_download_option")
        request = Request("https://www.ebi.ac.uk/europepmc/webservices/rest/PMC6897988/fullTextXML",
                          headers={"User-Agent": "harmo-registered-reuse/1.0"})
        with urlopen(request, timeout=60) as response:
            raw = response.read(2 * 1024**2 + 1)
        require(len(raw) <= 2 * 1024**2, "article_size_limit")
        unit_claim(raw)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as target:
            target.write(raw)
    unit_claim(path.read_bytes())
    return path


def run(urls, source_root, protocol_path, article, output, resume=False, download=False, max_bytes=2*1024**3, planned=False):
    """Run registered acquisition→mapping→catalog→evidence→timing→selection."""
    from harmo.config.automap import run as automap
    from harmo.proposal_catalog import ingest
    from harmo.catalog_evidence import DATASET, promote_units, unit_claim
    from harmo.timing_workflow import execute
    require(isinstance(urls, list) and len(urls) == 2 and all(isinstance(u, str) for u in urls)
            and set(urls) == URLS, "profile_requires_exact_two_registered_versioned_URLs")
    protocol = checked_profile(protocol_path)
    # Reviewed XML only; obtaining arbitrary related publications is out of scope.
    article = acquire_article(source_root, article, download)
    state = Stages(output, resume)
    sources = acquire_sources(source_root, protocol, max_bytes, download)
    source_root = Path(source_root).resolve()
    # Bind stages to the complete implementation and schema contents, not just a version label.
    code = implementation_identity()
    signature = dict(protocol=protocol["protocol_sha256"], article=sha256_file(str(article)), code=code,
                     sources={r["name"]: r["sha256"] for r in sources})
    def mapping(attempt):
        automap(source_root, attempt, list(protocol["source_assets"]["study_38"]))
        return dict(proposals=str(attempt))
    mapped = state.run("mapping", signature, mapping)
    store = state.output / "catalog"
    # The catalog ingest is independently idempotent and checks stored sample identities.
    accumulated = ingest(mapped["proposals"], source_root, store, DATASET)
    print("reuse: catalog inserted=%d reused=%d" % (accumulated["inserted"], accumulated["reused"]), flush=True)
    # Preserve evidence version URLs/locations across resume; promotion checks exact reuse.
    evidence_index = 1
    while (state.output / ("semantics-%03d.json" % evidence_index)).exists():
        evidence_index += 1
    semantics = promote_units(store, article, state.output / ("semantics-%03d.json" % evidence_index), True)
    require(semantics["source_verified"] == 404 and semantics["held"] == 0, "registered_semantics_incomplete")
    # Revalidate selected samples on every resume, even when timing outputs can be reused.
    from harmo.timing_workflow import bind_catalog
    binding = bind_catalog(store, source_root, protocol)
    timing_signature = dict(signature, catalog_bindings=binding["bindings"])
    if planned:
        import numpy, scipy
        from harmo.contract_revision import _row, digest
        from harmo.public_export import load_record
        timing_signature.update(execution='catalog-planner/1',
            input_paths=dict(catalog=str(store.resolve()),sources=str(source_root),
                             protocol=str(Path(protocol_path).resolve()),article=str(article)),
            runtime=dict(numpy=numpy.__version__,scipy=scipy.__version__),
            catalog_contracts={b['record_id']:digest(load_record(_row(store/'catalog.sqlite',b['record_id']))[0].to_dict())
                               for b in binding['bindings']})
    def timing_action(attempt):
        require(implementation_identity() == code, "implementation_changed_during_workflow")
        if planned:
            from harmo.planned_timing_workflow import run as execute_plans
            result=dict(execute_plans(store/'catalog.sqlite',source_root,protocol_path,article,attempt),output=str(attempt))
        else:
            result = dict(execute(store, source_root, protocol_path, attempt), output=str(attempt))
        require(implementation_identity() == code, "implementation_changed_during_workflow")
        return result
    timing = state.run("timing-planned" if planned else "timing", timing_signature, timing_action)
    require(implementation_identity() == code, "implementation_changed_during_workflow")
    summary = dict(version="registered-reuse/1.0", urls=urls, acquisition=sources,
                   catalog=accumulated, semantics_verified=semantics["source_verified"], timing=timing,
                   status=timing["status"],
                   scope="Registered two-study timing profile; not the full 8-study conversion corpus or arbitrary-URL reuse.")
    report_index = 1
    while (state.output / ("run-%03d.json" % report_index)).exists():
        report_index += 1
    save(state.output / ("run-%03d.json" % report_index), summary)
    return summary
