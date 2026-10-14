"""Bounded ZIP intake; preserve bytes and explicit parent/member provenance."""
import hashlib
import os
import shutil
import stat
import tempfile
import unicodedata
import zipfile
from pathlib import Path

from harmo.config.automap import require
from harmo.public_batch import sha


def extract_zip(source, destination, max_bytes=1024**3, max_members=1000,
                max_member_bytes=32*1024**2):
    source, destination = Path(source), Path(destination)
    require(not source.is_symlink() and source.is_file(), "regular_archive_required")
    require(not destination.exists() and not destination.is_symlink(), "new_extraction_destination_required")
    require(min(max_bytes, max_members, max_member_bytes) > 0, "positive_extraction_limits_required")
    parent_hash = sha(source)
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        require(0 < len(infos) <= max_members, "archive_member_count_limit")
        names, file_names, total = set(), set(), 0
        for info in infos:
            name = info.orig_filename
            parts = name.rstrip("/").split("/")
            require(name and len(name) <= 512 and len(parts) <= 16 and
                    not any(c in name for c in ("\\", "\x00", ":")) and
                    not any(ord(c) < 32 for c in name) and
                    all(p not in ("", ".", "..") and not p.endswith((" ", ".")) for p in parts),
                    "unsafe_archive_member_path")
            key = unicodedata.normalize("NFC", "/".join(parts)).casefold()
            require(key not in names, "duplicate_archive_member")
            names.add(key)
            kind = stat.S_IFMT(info.external_attr >> 16)
            require(kind in (0, stat.S_IFDIR if info.is_dir() else stat.S_IFREG), "archive_special_file_rejected")
            require(not info.flag_bits & 1, "encrypted_archive_rejected")
            require(info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED), "unsupported_archive_compression")
            require(info.file_size <= max_member_bytes, "archive_member_size_limit")
            require(info.file_size <= max(1, info.compress_size)*1000, "archive_expansion_ratio_limit")
            if not info.is_dir():
                file_names.add(key)
            total += info.file_size
        require(total <= max_bytes, "archive_expanded_size_limit")
        for name in names:
            parts = name.split("/")
            require(not any("/".join(parts[:i]) in file_names for i in range(1,len(parts))),
                    "archive_file_directory_collision")
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".zip-intake-", dir=destination.parent))
        members = []
        try:
            for info in infos:
                if info.is_dir():
                    continue
                target = stage / info.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                count, digest = 0, hashlib.sha256()
                with archive.open(info) as src, target.open("xb") as dst:
                    while True:
                        chunk = src.read(min(1024**2, info.file_size-count+1))
                        if not chunk:
                            break
                        count += len(chunk)
                        require(count <= info.file_size, "archive_actual_size_mismatch")
                        dst.write(chunk); digest.update(chunk)
                    dst.flush(); os.fsync(dst.fileno())
                require(count == info.file_size, "archive_actual_size_mismatch")
                members.append(dict(member=info.filename, bytes=count, sha256=digest.hexdigest(), crc32="%08x" % info.CRC))
            require(sha(source) == parent_hash, "archive_changed_during_extraction")
            stage.rename(destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    return dict(version="zip-intake/1.0", source=str(source.resolve()), source_sha256=parent_hash,
                destination=str(destination.resolve()), expanded_bytes=total, members=members,
                scope="Single archive layer; nested archives remain held; no scientific use approval.")
