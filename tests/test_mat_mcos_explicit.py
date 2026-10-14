from __future__ import annotations

import struct

from harmo.ingest.plugins.mat_mcos_explicit import _mcos_property_cell_ids


def _property_block(fields):
    payload = struct.pack("<I", len(fields))
    payload += b"".join(struct.pack("<3I", *field) for field in fields)
    return payload + b"\0" * ((-len(payload)) % 8)


def test_mcos_property_resolution_uses_normal_object_not_object_id():
    names = b"data\0varnames\0table\0any\0string\0"
    class_start = 40 + len(names)
    class_start += (-class_start) % 8
    class_region = struct.pack("<12I", *([0] * 12))
    object_start = class_start + len(class_region)
    object_records = [
        (0, 0, 0, 0, 0, 0),
        (1, 0, 0, 0, 1, 1),
        (2, 0, 0, 1, 0, 2),
        (1, 0, 0, 0, 2, 3),
    ]
    object_region = b"".join(struct.pack("<6I", *record) for record in object_records)
    property_start = object_start + len(object_region)
    property_region = b"".join(
        [
            _property_block([]),
            _property_block([(1, 1, 0), (2, 1, 1)]),
            _property_block([(1, 1, 7), (2, 1, 8)]),
        ]
    )
    property_end = property_start + len(property_region)
    offsets = (
        class_start,
        object_start,
        object_start,
        property_start,
        property_end,
        property_end,
        0,
        0,
    )
    header = struct.pack("<II8I", 3, 5, *offsets)
    metadata = (
        header
        + names
        + b"\0" * (class_start - len(header) - len(names))
        + class_region
        + object_region
        + property_region
    )

    assert _mcos_property_cell_ids(metadata, 1) == {"data": 0, "varnames": 1}
    assert _mcos_property_cell_ids(metadata, 3) == {"data": 7, "varnames": 8}
