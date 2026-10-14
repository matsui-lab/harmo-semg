import json
import sqlite3
import numpy as np
import pytest
from scipy.io import savemat
from harmo.catalog_evidence import unit_claim, promote_units, DATASET
from harmo.config.automap import run
from harmo.proposal_catalog import ingest


def test_unreviewed_article_rejected():
    with pytest.raises(ValueError, match="unreviewed_article"):
        unit_claim(b"unit: mV")


@pytest.mark.parametrize('change,expected',[
    ({},None),({'version':2},'identity_mismatch'),
    ({'related_materials':[]},'article_link_required'),
    ({'doi':'10.6084/m9.figshare.9784964'},'versioned_figshare')])
def test_linked_release_requires_version_and_article(tmp_path,change,expected):
    from harmo.catalog_evidence import linked_release
    metadata=dict(id=9784964,version=1,doi='10.6084/m9.figshare.9784964.v1',
        related_materials=[dict(identifier='10.1038/s41597-019-0323-z',identifier_type='DOI',relation='IsSupplementTo')])
    metadata.update(change)
    path=tmp_path/'metadata.json'; path.write_text(json.dumps(metadata))
    if expected:
        with pytest.raises(ValueError,match=expected): linked_release(path)
    else:
        dataset,proof=linked_release(path)
        assert dataset=='doi:'+metadata['doi'] and proof['links']


def test_measurement_side_is_not_reference_foot():
    from types import SimpleNamespace
    from harmo.catalog_evidence import add_record_semantics
    from harmo.evidence.models import ChannelDescriptor
    bundle = SimpleNamespace(channels=[ChannelDescriptor("ch0","Soleus","mV","unknown")])
    result = add_record_semantics(bundle, {"EMGSide":np.array(["RX "])},
        {"Task":np.array(["Walking"]),"Foot":np.array(["LX"])}, "a"*64)
    assert bundle.channels[0].side == "right"
    assert bundle.channels[0].anatomy.laterality == "right"
    assert bundle.task.facets["reference_foot"] == "LX"
    assert bundle.task.compatibility_group is None
    assert result["processing_state"] == "not_fully_qualified"


def test_unknown_side_not_inferred_from_foot():
    from types import SimpleNamespace
    from harmo.catalog_evidence import add_record_semantics
    with pytest.raises(ValueError, match="unknown_emg_side"):
        add_record_semantics(SimpleNamespace(channels=[]), {"EMGSide":np.array(["unknown"])},
            {"Task":np.array(["Walking"]),"Foot":np.array(["RX"])}, "a"*64)


def test_promotes_units_only_and_reuses(tmp_path, monkeypatch):
    root = tmp_path / "sources"; root.mkdir()
    trials = np.empty((1,2), dtype=[("EMG",object)])
    for item in trials.flat:
        item["EMG"] = np.arange(24).reshape(8,3)
    savemat(root / "a.mat", {"s":{"Data":trials,"EMGFreq":1000,"EmgVarName":np.array(["c%d"%i for i in range(8)])}})
    proposals = tmp_path / "proposals"
    run(root,proposals,["a.mat"])
    store = tmp_path / "store"
    ingest(proposals,root,store,DATASET)
    article = tmp_path / "article.xml"; article.write_bytes(b"fixture")
    monkeypatch.setattr("harmo.catalog_evidence.unit_claim", lambda raw: {"unit":"mV"})
    result = promote_units(store,article,tmp_path / "result.json")
    assert result["source_verified"] == 2
    db = sqlite3.connect(store / "catalog.sqlite")
    from pathlib import Path
    for row in db.execute("SELECT location FROM records"):
        dest = Path(row[0])
        old = json.loads((dest / "recording.bundle.json").read_text())
        new = json.loads((dest / "recording.semantic.bundle.json").read_text())
        assert old["channels"][0]["unit"] == "unknown"
        assert new["channels"][0]["unit"] == "mV"
        assert old["samples"] == new["samples"]
    db.close()
    second = promote_units(store,article,tmp_path / "again.json")
    assert all(r["reused"] for r in second["records"])
