import copy
from harmo.semantic_promotion import promote, FORMAT
from test_evidence_contracts import bundle

def fixture():
    b = bundle()
    for c in b.channels:
        c.muscle = None
        c.side = None
        c.sensor_type = "unknown"
    anatomy = {"entity_type":"muscle", "canonical_label":"tibialis anterior", "ontology_term":{
        "ontology":"source-vocabulary", "ontology_version":"1", "term_id":"TA", "label":"tibialis anterior"}}
    rows = []
    for channel, field, value in [("TA","anatomy",anatomy),("TA","side","left"),
                                   ("TA","sensor_type","surface-emg"),("TA","unit","V"),
                                   ("","task",{"activity":"walking", "ontology_terms":[{"ontology":"source-vocabulary", "ontology_version":"1", "term_id":"walk", "label":"walking"}], "facets":{"terrain":"level"}, "compatibility_group":None})]:
        rows.append(dict(record="x.hea",header_sha256="a"*64,channel=channel,field=field,value=value))
    doc = dict(uri="https://example.test/definitions",sha256="b"*64,data=dict(format=FORMAT,dataset=b.study_id,definitions=rows))
    return b, doc


def test_promotes_explicit_attributes_and_preserves_original():
    b,d = fixture()
    before = b.to_dict()
    result, report = promote(b,"x.hea","a"*64,[d])
    assert result.channels[0].anatomy.canonical_label == "tibialis anterior"
    assert result.channels[0].side == "left"
    assert result.task.activity == "walking"
    assert sum(r["status"] == "source_verified" for r in report["decisions"]) == 5
    assert b.to_dict() == before
    assert result.samples.to_dict() == b.samples.to_dict()
    assert report["scientific_use"] == "unassessed"


def test_wrong_version_or_hash_never_promotes():
    for mode in ("dataset", "hash"):
        b,d = fixture()
        if mode == "dataset": d["data"]["dataset"] += "/different"
        result,r = promote(b,"x.hea","c"*64 if mode == "hash" else "a"*64,[d])
        assert result.task is None
        assert result.channels[0].anatomy is None
        assert r["ignored"]


def test_conflict_is_attribute_local():
    b,d = fixture()
    other = copy.deepcopy(d)
    other["data"]["definitions"] = [dict(d["data"]["definitions"][1],value="right")]
    result,r = promote(b,"x.hea","a"*64,[d,other])
    assert result.channels[0].side is None
    assert result.channels[0].anatomy.laterality == "unknown"
    assert result.task.activity == "walking"
    assert any(x["field"]=="side" and x["status"]=="conflicting" for x in r["decisions"])


def test_unit_conflict_does_not_erase_anatomy():
    b,d = fixture()
    result,r = promote(b,"x.hea","a"*64,[d],{"issues":[{"channel":"TA"}]})
    assert result.channels[0].anatomy is not None
    assert any(x["field"]=="unit" and x["status"]=="conflicting" for x in r["decisions"])


def test_muscle_group_preserved():
    b,d = fixture()
    d["data"]["definitions"][0]["value"]["entity_type"] = "muscle-group"
    d["data"]["definitions"][0]["value"]["canonical_label"] = "hamstrings"
    result,_ = promote(b,"x.hea","a"*64,[d])
    assert result.channels[0].anatomy.entity_type == "muscle-group"
    assert result.channels[0].muscle is None


def test_no_definition_and_other_record_do_not_promote():
    b,d = fixture()
    for docs in ([],[d]):
        result,r = promote(b,"other.hea","a"*64,docs)
        assert result.channels[0].anatomy is None
        assert all(x["status"]=="unresolved" for x in r["decisions"])


def test_invalid_task_does_not_block_anatomy():
    b,d = fixture()
    d["data"]["definitions"][-1]["value"]["activity"] = 123
    result,r = promote(b,"x.hea","a"*64,[d])
    assert result.task is None
    assert result.channels[0].anatomy is not None
    assert r["decisions"][0]["status"] == "invalid"


def test_discovery_writes_new_view_and_invalid_source_blocks(tmp_path):
    import json
    import pytest
    from harmo.semantic_promotion import promote_directory, digest
    b,d = fixture()
    raw, directory = tmp_path/"raw", tmp_path/"converted"
    raw.mkdir(); directory.mkdir()
    (raw/"x.hea").write_text("test header")
    for row in d["data"]["definitions"]:
        row["header_sha256"] = digest(raw/"x.hea")
    (raw/"channels.record-definitions.json").write_text(json.dumps(d["data"]))
    original = b.to_json()
    (directory/"recording.bundle.json").write_text(original)
    report = promote_directory(directory,"x.hea",raw,"https://example.test/")
    assert not report["invalid_documents"]
    assert (directory/"recording.bundle.json").read_text() == original
    with pytest.raises(FileExistsError):
        promote_directory(directory,"x.hea",raw,"https://example.test/")
    second = tmp_path/"second"
    second.mkdir()
    (second/"recording.bundle.json").write_text(original)
    (raw/"invalid.record-definitions.json").write_text("not JSON")
    report = promote_directory(second,"x.hea",raw,"https://example.test/")
    assert report["invalid_documents"]
    assert not any(x["status"]=="source_verified" for x in report["decisions"])


def test_existing_mappings_not_overwritten():
    import pytest
    b,d = fixture()
    b.channels[0].side = "right"
    with pytest.raises(ValueError):
        promote(b,"x.hea","a"*64,[d])
