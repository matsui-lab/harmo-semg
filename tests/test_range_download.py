import hashlib
import io
import json
import pytest
from harmo.range_download import download_ranges


def spec():
    return dict(download_url="https://example.org/file", size_bytes=8,
                checksum_algorithm="md5", checksum=hashlib.md5(b"abcdefgh").hexdigest(), container="file")


def response(data, content_range, status=206):
    stream = io.BytesIO(data)
    stream.status = status
    stream.headers = {"Content-Range": content_range}
    return stream


def test_ignore_name_only_prefix_and_verify(tmp_path):
    directory = tmp_path / ".physioio-test"; directory.mkdir()
    request = dict(name="original.zip", id="1", version=1)
    (directory / "request.json").write_text(json.dumps(request))
    (directory / "original.zip").write_bytes(b"xxx")
    ranges = []
    def opener(req, **kwargs):
        ranges.append(req.get_header("Range"))
        if len(ranges) == 1:
            return response(b"abc", "bytes 0-2/8")
        if len(ranges) == 2:
            return response(b"def", "bytes 3-5/8")
        return response(b"gh", "bytes 6-7/8")
    target = tmp_path / "target.zip"
    result = download_ranges(spec(), target, request, opener=opener, chunk_bytes=3)
    assert result["pass"] and result["resumed_bytes"] == 0
    assert target.read_bytes() == b"abcdefgh"
    assert (directory / "original.zip").read_bytes() == b"xxx"
    assert ranges == ["bytes=0-2", "bytes=3-5", "bytes=6-7"]


def test_reject_legacy_recovered_prefix(tmp_path):
    target = tmp_path / "target"
    partial = tmp_path / "target.part"
    partial.write_bytes(b"wrong")
    identity = {key: spec()[key] for key in ("download_url", "size_bytes", "checksum_algorithm", "checksum")}
    (tmp_path / "target.resume.json").write_text(json.dumps(dict(identity=identity, recovered_prefix="old/Ref_1.zip")))
    with pytest.raises(ValueError, match="legacy name-only"):
        download_ranges(spec(), target)
    assert partial.read_bytes() == b"wrong"


@pytest.mark.parametrize("status,header,data", [(200,"bytes 0-2/8",b"abc"),(206,"bytes 1-3/8",b"abc"),(206,"bytes 0-2/8",b"ab")])
def test_invalid_range_never_appended(tmp_path, status, header, data):
    target = tmp_path / "target"
    with pytest.raises(RuntimeError):
        download_ranges(spec(), target, opener=lambda *a, **kw: response(data,header,status),chunk_bytes=3,retries=1)
    assert not target.exists()
    assert target.with_name("target.part").read_bytes() == b""


def test_final_checksum_and_changed_identity_rejected(tmp_path):
    target=tmp_path / "target"
    with pytest.raises(ValueError,match="checksum"):
        download_ranges(spec(),target,opener=lambda *a,**kw:response(b"xxxxxxxx","bytes 0-7/8"))
    assert not target.exists()
    altered=dict(spec(),checksum="0"*32)
    with pytest.raises(ValueError,match="identity"):
        download_ranges(altered,target)


def test_resume_completed_chunk_after_interruption(tmp_path):
    target=tmp_path / "target"
    def first(req, **kwargs):
        if req.get_header("Range") == "bytes=0-3":
            return response(b"abcd", "bytes 0-3/8")
        raise OSError("connection interrupted")
    with pytest.raises(RuntimeError):
        download_ranges(spec(), target, opener=first, chunk_bytes=4, retries=1)
    assert target.with_name("target.part").read_bytes() == b"abcd"
    def second(req, **kwargs):
        assert req.get_header("Range") == "bytes=4-7"
        return response(b"efgh", "bytes 4-7/8")
    assert download_ranges(spec(),target,opener=second,chunk_bytes=4)["resumed_bytes"] == 4
    assert target.read_bytes() == b"abcdefgh"
