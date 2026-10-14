import hashlib
import sqlite3

from harmo.source_evidence import extract_description, assess_record, screen_saved_stage
from harmo.public_batch import catalog
from harmo.source_evidence import map_channels


def test_channel_mapping_requires_label_and_source(tmp_path):
    e = source(tmp_path, '<p>Signals from tibialis anterior (TA).</p>')
    m = map_channels({"signals":[{"label":"semg LT TIB.A", "unit":"uV"}],
                      "source_sha256":{"x.hea":"a"*64}}, e)[0]
    assert m["anatomy_candidates"][0]["name"] == "tibialis anterior"
    assert m["anatomy_candidates"][0]["source_support"] == "mentioned"
    assert m["side_candidates"] == ["left"]
    assert m["status"] == "candidate"
    assert m["header_sources"]["x.hea"] == "a"*64


def test_group_not_promoted_to_muscle(tmp_path):
    e = source(tmp_path, '<p>Hamstrings (Ham).</p>')
    m = map_channels({"signals":[{"label":"semg RT HAM"}]}, e)[0]
    assert m["anatomy_candidates"][0]["entity_type"] == "muscle-group"
    assert "group_not_single_muscle" in m["reasons"]


def test_ambiguity_and_no_substring_alias(tmp_path):
    e = source(tmp_path, '<p>tibialis anterior and rectus femoris.</p>')
    mappings = map_channels({"signals":[{"label":"EMG LT RT TA RF"},
                                        {"label":"EMG LEFT DATA"}]}, e)
    assert "anatomy_ambiguous" in mappings[0]["reasons"]
    assert "laterality_ambiguous" in mappings[0]["reasons"]
    assert not mappings[1]["anatomy_candidates"]


def test_no_assignment_by_order_or_dataset_only(tmp_path):
    e = source(tmp_path, '<p>tibialis anterior.</p>')
    m = map_channels({"signals":[{"label":"EMG"}]}, e)[0]
    assert not m["anatomy_candidates"]
    assert not m["side_candidates"]


def test_unsupported_source_kept_unverified(tmp_path):
    e = source(tmp_path, '<p>Other muscle.</p>')
    m = map_channels({"signals":[{"label":"semg LT TA"}]}, e)[0]
    assert m["anatomy_candidates"][0]["source_support"] == "not_found_by_rules"
    assert "anatomy_source_support_not_found_by_rules" in m["reasons"]


def source(tmp_path, text):
    path = tmp_path / "description.html"
    path.write_text(text)
    return extract_description(path, "https://physionet.org/content/example/1.0.0/")


def test_exact_provenance_and_inline_text(tmp_path):
    e = source(tmp_path, '<p>sEMG signals are expressed in <span>&mu;V</span>.</p>')
    c = e["claims"][0]
    assert c["value"] == "uV"
    assert c["quote"][c["start"]:c["end"]] == "sEMG signals are expressed in μV"
    assert e["source_sha256"] == hashlib.sha256((tmp_path/"description.html").read_bytes()).hexdigest()
    assert c["status"] == "candidate"


def test_ignore_scripts_and_titles(tmp_path):
    e = source(tmp_path, '<title>needle electrode</title><script><p>needle electrode</p></script>')
    assert not e["claims"]


def test_negative_statement_never_approved(tmp_path):
    e = source(tmp_path, '<p>No needle electrode was used.</p>')
    assert all(c["status"] == "candidate" for c in e["claims"])
    r = assess_record({"signals": []}, e)
    assert all(p["status"] == "unassessed" for p in r["purposes"].values())


def test_unit_conflict_and_non_emg_exclusion(tmp_path):
    e = source(tmp_path, '<p>sEMG signals are expressed in μV.</p>')
    r = assess_record({"signals": [{"label":"EMG1", "unit":"mV"},
                                    {"label":"angle", "unit":"deg"}]}, e)
    assert len(r["issues"]) == 1
    assert r["issues"][0]["channel"] == "EMG1"
    assert r["automatic_signal_changes"] == []
    assert "possible_source_unit_conflict" in r["purposes"]["amplitude-pooling"]["reasons"]


def test_alias_not_conflict(tmp_path):
    e = source(tmp_path, '<p>sEMG signals are expressed in mV.</p>')
    assert not assess_record({"signals":[{"label":"EMG", "unit":"mv"}]}, e)["issues"]


def test_missing_is_not_absence(tmp_path):
    e = source(tmp_path, '<p>Other text.</p>')
    assert e["coverage"]["related_papers"] == "not_examined"
    assert not e["claims"]
    assert "not evidence of absence" in e["limitation"]


def test_conflicting_source_units(tmp_path):
    e = source(tmp_path, '<p>EMG signals are expressed in mV.</p><p>EMG signals are expressed in uV.</p>')
    assert assess_record({"signals":[{"label":"EMG", "unit":"mV"}]}, e)["issues"]


def test_schema_migration():
    db = sqlite3.connect(":memory:")
    catalog(db)
    catalog(db)
    assert db.execute("SELECT count(*) FROM source_screens").fetchone()[0] == 0


def test_offline_preserves_sources(tmp_path):
    import json
    import pytest
    stage = tmp_path / "example" / "1.0.0"
    record = stage / "converted" / "record"
    record.mkdir(parents=True)
    (stage/"description.html").write_text('<p>needle electrode</p>')
    (record/"conversion.json").write_text(json.dumps({"signals":[]}))
    before = (record/"conversion.json").read_bytes()
    results = screen_saved_stage(stage, tmp_path/"new")
    assert len(results) == 1
    assert (record/"conversion.json").read_bytes() == before
    with pytest.raises(FileExistsError):
        screen_saved_stage(stage, tmp_path/"new")
