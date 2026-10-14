"""Parse VTK PolyData (.vtp) XML files and extract geometry data.

Supports both ASCII and appended (base64 + zlib compressed) formats.
"""
from __future__ import annotations

import base64
import struct
import zlib
from typing import Dict, List, Optional, Tuple

from lxml import etree


def parse_vtp(file_path: str) -> Optional[Dict]:
    """Parse a VTP file and return geometry data.

    Returns:
        dict with keys:
            points: list of (x, y, z) tuples
            normals: list of (nx, ny, nz) tuples (may be empty)
            polys: list of face index tuples (triangles or quads)
        or None on failure
    """
    try:
        tree = etree.parse(file_path)
        root = tree.getroot()
    except Exception:
        return None

    piece = root.find(".//Piece")
    if piece is None:
        return None

    num_points = int(piece.get("NumberOfPoints", "0"))
    if num_points == 0:
        return None

    # Collect all DataArray offsets for appended mode boundary detection
    appended_offsets: List[int] = []
    appended_data_text: Optional[str] = None
    appended_el = root.find(".//AppendedData")
    if appended_el is not None:
        encoding = appended_el.get("encoding", "raw")
        raw_text = appended_el.text or ""
        # Strip leading underscore marker
        stripped = raw_text.lstrip()
        if stripped.startswith("_"):
            stripped = stripped[1:]
        if encoding == "base64" and stripped:
            appended_data_text = stripped.replace("\n", "").replace("\r", "").replace(" ", "").replace("\t", "")
            # Collect all offsets from DataArrays in the document
            for da in root.iter("DataArray"):
                if da.get("format") == "appended":
                    try:
                        appended_offsets.append(int(da.get("offset", "0")))
                    except ValueError:
                        pass
            appended_offsets = sorted(set(appended_offsets))

    def read_data_array(el: etree._Element) -> Optional[List[float]]:
        if el is None:
            return None
        fmt = el.get("format", "ascii")
        dtype = el.get("type", "Float32")

        if fmt == "ascii":
            return _parse_ascii_array(el.text or "", dtype)
        elif fmt == "appended" and appended_data_text is not None:
            offset = int(el.get("offset", "0"))
            # Find end boundary
            end = len(appended_data_text)
            for o in appended_offsets:
                if o > offset:
                    end = o
                    break
            chunk = appended_data_text[offset:end]
            return _parse_appended_chunk(chunk, dtype)
        return None

    # Parse points
    points_el = piece.find(".//Points/DataArray")
    points_flat = read_data_array(points_el)
    if not points_flat:
        return None
    points = [(points_flat[i], points_flat[i + 1], points_flat[i + 2])
              for i in range(0, len(points_flat), 3)]

    # Parse normals (optional)
    normals: List[Tuple[float, float, float]] = []
    normals_el = piece.find(".//PointData/DataArray[@Name='Normals']")
    if normals_el is None:
        normals_el = piece.find(".//PointData/DataArray")
    if normals_el is not None:
        normals_flat = read_data_array(normals_el)
        if normals_flat:
            normals = [(normals_flat[i], normals_flat[i + 1], normals_flat[i + 2])
                       for i in range(0, len(normals_flat), 3)]

    # Parse polygons
    polys: List[Tuple[int, ...]] = []
    polys_section = piece.find(".//Polys")
    if polys_section is not None:
        conn_el = polys_section.find("DataArray[@Name='connectivity']")
        off_el = polys_section.find("DataArray[@Name='offsets']")
        if conn_el is not None and off_el is not None:
            conn_flat = read_data_array(conn_el)
            off_flat = read_data_array(off_el)
            if conn_flat and off_flat:
                conn = [int(v) for v in conn_flat]
                offsets = [int(v) for v in off_flat]
                prev = 0
                for off in offsets:
                    off_int = int(off)
                    face = tuple(conn[prev:off_int])
                    if len(face) >= 3:
                        polys.append(face)
                    prev = off_int

    return {"points": points, "normals": normals, "polys": polys}


def _parse_ascii_array(text: str, dtype: str) -> Optional[List[float]]:
    """Parse space-separated numeric text into a flat list."""
    try:
        parts = text.split()
        if not parts:
            return None
        if dtype in ("Int32", "Int64", "UInt32", "UInt64"):
            return [float(int(p)) for p in parts]
        else:
            return [float(p) for p in parts]
    except (ValueError, TypeError):
        return None


def _decode_b64_segments(text: str) -> bytes:
    """Decode concatenated base64 segments (each with own padding)."""
    result = bytearray()
    pos = 0
    length = len(text)

    while pos < length:
        end = pos
        while end < length and text[end] != "=":
            end += 1
        while end < length and text[end] == "=":
            end += 1
        if end == pos:
            break
        segment = text[pos:end]
        try:
            result.extend(base64.b64decode(segment))
        except Exception:
            break
        pos = end

    return bytes(result)


def _parse_appended_chunk(chunk: str, dtype: str) -> Optional[List[float]]:
    """Parse an appended data chunk (base64 encoded, possibly zlib compressed)."""
    try:
        raw = _decode_b64_segments(chunk)
        if len(raw) < 12:
            return None

        num_blocks = struct.unpack_from("<I", raw, 0)[0]
        block_size = struct.unpack_from("<I", raw, 4)[0]
        last_block_size = struct.unpack_from("<I", raw, 8)[0]

        header_size = 12 + 4 * num_blocks
        if len(raw) < header_size:
            return None

        compressed_sizes = []
        for i in range(num_blocks):
            cs = struct.unpack_from("<I", raw, 12 + 4 * i)[0]
            compressed_sizes.append(cs)

        pos = header_size
        decompressed = bytearray()
        for cs in compressed_sizes:
            if pos + cs > len(raw):
                return None
            block = raw[pos: pos + cs]
            try:
                decompressed.extend(zlib.decompress(block))
            except zlib.error:
                try:
                    decompressed.extend(zlib.decompress(block, -15))
                except zlib.error:
                    return None
            pos += cs

        if dtype in ("Float32",):
            count = len(decompressed) // 4
            return list(struct.unpack(f"<{count}f", decompressed[:count * 4]))
        elif dtype in ("Float64",):
            count = len(decompressed) // 8
            return list(struct.unpack(f"<{count}d", decompressed[:count * 8]))
        elif dtype in ("Int32",):
            count = len(decompressed) // 4
            return [float(v) for v in struct.unpack(f"<{count}i", decompressed[:count * 4])]
        elif dtype in ("UInt32",):
            count = len(decompressed) // 4
            return [float(v) for v in struct.unpack(f"<{count}I", decompressed[:count * 4])]
        elif dtype in ("Int64",):
            count = len(decompressed) // 8
            return [float(v) for v in struct.unpack(f"<{count}q", decompressed[:count * 8])]
        else:
            count = len(decompressed) // 4
            return list(struct.unpack(f"<{count}f", decompressed[:count * 4]))
    except Exception:
        return None
