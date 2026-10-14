"""Narrow, source-bound promotion of the published study_38 EMG unit."""
import json
import re
import sqlite3
from pathlib import Path
import numpy as np
from scipy.io import loadmat
from lxml import etree
from harmo.config.automap import digest, require, save
from harmo.evidence.models import HarmoRecordingBundle, SourceAsset

ARTICLE_HASH = "d1e83284ddefe66ee1960c797977f2f16b7b78df8435578aa8613e150de70738"
DATASET = "doi:10.6084/m9.figshare.c.4494755.v1"


def add_record_semantics(bundle, source, trial, source_hash):
    from harmo.evidence.models import AnatomicalEntity, OntologyTerm, SemanticMapping, TaskDescriptor
    muscles = {"tibialis anterior", "soleus", "gastrocnemius medialis", "peroneus longus",
               "rectus femoris", "vastus medialis", "biceps femoris", "gluteus maximus"}
    def scalar(value):
        require(value.size == 1 and value.dtype.kind in "US", "explicit_scalar_text_required")
        return str(value.item()).strip()
    side_code = scalar(source["EMGSide"])
    require(side_code in ("RX", "LX"), "unknown_emg_side")
    side = {"RX": "right", "LX": "left"}[side_code]
    task = scalar(trial["Task"])
    require(task in ("Walking", "HeelWalking", "ToeWalking", "StepDown", "StepUp"), "unrecognized_task")
    foot = scalar(trial["Foot"])
    require(foot in ("RX", "LX"), "unknown_reference_foot")
    refs = ["doi:10.1038/s41597-019-0323-z#registered-study38-semantics:sha256:"+ARTICLE_HASH,
            "mat:source-fields:sha256:"+source_hash]
    mapping = SemanticMapping("registered-source-labels", "study38/1.0", "mapped", refs)
    for channel in bundle.channels:
        muscle = channel.name.lower()
        require(muscle in muscles, "unrecognized_muscle_label")
        channel.side = side
        channel.muscle = muscle
        channel.sensor_type = "surface-emg"
        channel.anatomy = AnatomicalEntity("muscle",muscle,
            OntologyTerm("HarmoSourceTerms","study38-1.0",muscle.replace(" ","-"),muscle),side,channel.name,mapping)
    bundle.task = TaskDescriptor(task,task,[OntologyTerm("HarmoSourceTerms","study38-1.0",task,task)],
        {"reference_foot": foot, "emg_measurement_side": side_code, "scope": "record-protocol-not-event-times"},mapping,None)
    bundle.schema_version = "1.2.0"
    from harmo.semantic_promotion import checked
    checked(bundle.task, "task")
    for channel in bundle.channels:
        checked(channel.anatomy, "anatomicalEntity")
    return dict(task=task, emg_side=side_code, reference_foot=foot,
        anatomy_channels=len(bundle.channels), sensor_type="surface-emg", source_refs=refs,
        acquisition_bandpass_hz=[10,400], processing_state="not_fully_qualified",
        note="Acquisition filter is reported in the article; no complete processing contract or comparability group inferred")


def unit_claim(raw):
    require(digest(raw) == ARTICLE_HASH, "unreviewed_article_version")
    tree = etree.fromstring(raw, etree.XMLParser(resolve_entities=False, no_network=True))
    matches = []
    for table in tree.findall(".//table-wrap"):
        scope = ""
        for index, row in enumerate(table.findall(".//tr")):
            cells = [re.sub(r"\s+", " ", "".join(c.itertext())).strip() for c in row if c.tag in ("td", "th")]
            if "Field of s.Data structure" in " ".join(cells):
                scope = "s.Data"
            if "Field of s.StandingData structure" in " ".join(cells):
                scope = "s.StandingData"
            if scope == "s.Data" and len(cells) == 2 and cells[0] == ".EMG":
                require("Matrix (8 × N)" in cells[1] and "unit: mV" in cells[1], "unexpected_emg_declaration")
                matches.append(dict(unit="mV", quote=cells[1], locator=f"table/{table.get('id')}/row/{index}"))
    require(len(matches) == 1, "missing_or_competing_unit_claim")
    return dict(matches[0], article_sha256=ARTICLE_HASH, doi="10.1038/s41597-019-0323-z")


def linked_release(path):
    """Resolve an explicit article-level Figshare release link, not a citation."""
    from harmo.related_documents import related_dois
    raw=Path(path).read_bytes()
    metadata=json.loads(raw)
    doi=metadata.get('doi','')
    match=re.fullmatch(r'10\.6084/m9\.figshare\.(\d+)\.v(\d+)',doi)
    require(match is not None,'versioned_figshare_release_required')
    require(str(metadata.get('id'))==match[1] and str(metadata.get('version'))==match[2],'repository_release_identity_mismatch')
    links=[r for r in related_dois(metadata) if r['doi'].lower()=='10.1038/s41597-019-0323-z']
    require(bool(links),'explicit_reviewed_article_link_required')
    return 'doi:'+doi,dict(source_uri='https://doi.org/'+doi,sha256=digest(raw),links=links)


def promote_units(store, article, output, record_semantics=False, linked_metadata=None, view_root=None):
    store, article, output = map(lambda p: Path(p).resolve(), (store,article,output))
    claim = unit_claim(article.read_bytes())
    expected_dataset,link = linked_release(linked_metadata) if linked_metadata else (DATASET,None)
    require(not output.exists(), "output_exists")
    db = sqlite3.connect("file:"+str(store / "catalog.sqlite")+"?mode=ro", uri=True)
    rows = db.execute("SELECT id,dataset,name,location,metadata FROM records ORDER BY id").fetchall()
    db.close()
    originals, results = {}, []
    for rid, dataset, name, location, metadata in rows:
        meta = json.loads(metadata)
        proposal = meta.get("proposal", {})
        if proposal.get("signal_type") != "EMG":
            continue
        if linked_metadata and dataset != expected_dataset:
            continue
        result = dict(record_id=rid, status="held")
        try:
            require(dataset == expected_dataset, "dataset_release_mismatch")
            dest = Path(location).resolve()
            require(store in dest.parents, "record_outside_store")
            require(len(meta["source_sha256"]) == 1, "ambiguous_source")
            source_hash = next(iter(meta["source_sha256"].values()))
            if source_hash not in originals:
                source = store / "sources" / source_hash
                require(digest(source.read_bytes()) == source_hash, "source_changed")
                originals[source_hash] = loadmat(source)["s"][0,0]
            source = originals[source_hash]
            match = re.fullmatch(r"s\.Data\.(\d+)\.EMG", proposal["data_key"])
            require(match is not None, "record_scope_mismatch")
            values = source["Data"].flat[int(match[1])]["EMG"].T
            labels = [str(v).strip() for v in source["EmgVarName"].flat]
            bundle = HarmoRecordingBundle.from_dict(json.loads((dest / "recording.bundle.json").read_text()))
            require(bundle.recording_id == rid and bundle.study_id == dataset, "identity_mismatch")
            require([c.name for c in bundle.channels] == labels and len(labels) == 8, "channel_binding_mismatch")
            require(digest((dest / "normalized.npz").read_bytes()) == bundle.samples.checksum, "waveform_changed")
            with np.load(dest / "normalized.npz", allow_pickle=False) as saved:
                np.testing.assert_array_equal(saved["values"], values)
            for channel in bundle.channels:
                require(channel.unit in ("unknown", "mV"), "declared_unit_conflict")
                channel.unit = "mV"
            bundle.sources.append(SourceAsset(article.as_uri(), ARTICLE_HASH))
            result.update(status="source_verified", attribute="unit", value="mV", channels=8,
                          source_sha256=source_hash, data_key=proposal["data_key"], evidence=claim,
                          scientific_use="unassessed", value_transform="none")
            if link:
                result['repository_article_link']=link
            if record_semantics:
                result["record_semantics"] = add_record_semantics(bundle,source,source["Data"].flat[int(match[1])],source_hash)
            candidate = HarmoRecordingBundle.from_dict(bundle.to_dict()).to_dict()
            view_dir=Path(view_root).resolve()/rid if view_root else dest
            view_dir.mkdir(parents=True,exist_ok=True)
            target = view_dir / ("recording.semantic.v002.bundle.json" if record_semantics else "recording.semantic.bundle.json")
            evidence_path = view_dir / ("record_semantics.v002.json" if record_semantics else "unit.evidence.json")
            if target.exists():
                require(json.loads(target.read_text()) == candidate, "existing_semantic_view_differs")
                require(json.loads(evidence_path.read_text()) == result, "existing_evidence_differs")
                result["reused"] = True
            else:
                save(evidence_path, result)
                save(target, candidate)
        except (ValueError, OSError, KeyError, IndexError, AssertionError) as exc:
            result.update(status="held", reason=str(exc))
        results.append(result)
    summary = dict(records=results, source_verified=sum(r["status"] == "source_verified" for r in results),
                   held=sum(r["status"] == "held" for r in results), scientific_use="unassessed")
    save(output, summary)
    return summary
