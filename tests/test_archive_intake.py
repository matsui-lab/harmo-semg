import hashlib
import json
import sqlite3
import stat
import zipfile
import pytest
from harmo.archive_intake import extract_zip
from harmo.mixed_batch import run


def make_zip(path, members):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members:
            z.writestr(name, data)


def test_valid_bytes_and_provenance(tmp_path):
    src = tmp_path/"data.zip"
    make_zip(src, [("folder/data.csv", b"exact bytes"), ("readme.md", b"documentation")])
    original = src.read_bytes()
    result = extract_zip(src, tmp_path/"out")
    assert src.read_bytes() == original
    assert result["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert result["members"][0]["sha256"] == hashlib.sha256(b"exact bytes").hexdigest()
    assert (tmp_path/"out/folder/data.csv").read_bytes() == b"exact bytes"


@pytest.mark.parametrize("name", ["../bad", "/absolute", "C:/bad", "a\\bad", "a/../b", "a//b"])
def test_unsafe_paths(tmp_path, name):
    make_zip(tmp_path/"x.zip", [(name,b"data")])
    with pytest.raises(ValueError, match="unsafe"):
        extract_zip(tmp_path/"x.zip", tmp_path/"out")
    assert not (tmp_path/"out").exists()


@pytest.mark.parametrize("members,limits,reason", [
    ([("A",b"a"),("a",b"b")], {}, "duplicate"),
    ([("a",b"a"),("a/b",b"b")], {}, "collision"),
    ([("a",b"abcd")], {"max_member_bytes":3}, "member_size"),
    ([("a",b"ab"),("b",b"cd")], {"max_bytes":3}, "expanded_size"),
    ([("a",b"a"),("b",b"b")], {"max_members":1}, "member_count")])
def test_limits_and_collisions(tmp_path, members, limits, reason):
    make_zip(tmp_path/"x.zip", members)
    with pytest.raises(ValueError,match=reason):
        extract_zip(tmp_path/"x.zip",tmp_path/"out",**limits)
    assert not (tmp_path/"out").exists()


def test_symlink_rejected(tmp_path):
    info=zipfile.ZipInfo("link"); info.create_system=3
    info.external_attr=(stat.S_IFLNK | 0o777) << 16
    make_zip(tmp_path/"x.zip", [(info,b"target")])
    with pytest.raises(ValueError,match="special"):
        extract_zip(tmp_path/"x.zip",tmp_path/"out")


def test_corrupt_crc_leaves_no_adopted_output(tmp_path):
    src=tmp_path/"x.zip"
    make_zip(src, [("a",b"unique-content")])
    src.write_bytes(src.read_bytes().replace(b"unique-content",b"broken-content"))
    with pytest.raises(zipfile.BadZipFile):
        extract_zip(src,tmp_path/"out")
    assert not (tmp_path/"out").exists()
    assert not list(tmp_path.glob(".zip-intake-*"))


def test_mixed_zip_to_catalog_and_nested_archive_hold(tmp_path):
    src=tmp_path/"x.zip"
    make_zip(src, [("folder/emg.csv",b"time_s,EMG\n0,1\n0.001,2\n0.002,3\n"),
                   ("nested.zip",b"not recursively opened")])
    content=src.read_bytes()
    def metadata(spec):
        return dict(id=123,version=2,files=[dict(id=1,name="x.zip",size=len(content),
                    computed_md5=hashlib.md5(content).hexdigest())])
    def acquire(asset,dest,library):
        dest.write_bytes(content); return {"pass":True}
    result=run(["https://doi.org/10.6084/m9.figshare.123.v2"],tmp_path/"store",
               fetch_metadata=metadata,acquire=acquire)
    entry=result["entries"][0]
    assert entry["converted"] == 1
    assert entry["status"] == "partial"
    manifest=json.loads(__import__('pathlib').Path(entry["archives"][0]["provenance"]).read_text())
    assert manifest["source_sha256"] == hashlib.sha256(content).hexdigest()
    assert any(r["source"].endswith("nested.zip") for r in entry["not_converted"])
    with sqlite3.connect(result["catalog"]) as db:
        assert db.execute("SELECT count(*) FROM assessments WHERE status='unassessed'").fetchone()[0] == 2
