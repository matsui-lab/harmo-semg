"""Convert VTP (VTK PolyData) files to Wavefront OBJ format."""
from __future__ import annotations

import logging

from harmo.model.web_editor.app.utils.vtp_parser import parse_vtp

logger = logging.getLogger(__name__)


def convert_vtp_to_obj(vtp_path: str, obj_path: str) -> bool:
    """Convert a VTP file to OBJ format.

    Returns True on success, False on failure.
    """
    data = parse_vtp(vtp_path)
    if data is None:
        logger.warning("Failed to parse VTP file: %s", vtp_path)
        return False

    points = data["points"]
    normals = data["normals"]
    polys = data["polys"]

    if not points:
        logger.warning("No points in VTP file: %s", vtp_path)
        return False

    has_normals = len(normals) == len(points)

    try:
        with open(obj_path, "w") as f:
            f.write(f"# Converted from {vtp_path}\n")
            f.write(f"# Vertices: {len(points)}, Faces: {len(polys)}\n\n")

            for x, y, z in points:
                f.write(f"v {x:.8g} {y:.8g} {z:.8g}\n")

            if has_normals:
                f.write("\n")
                for nx, ny, nz in normals:
                    f.write(f"vn {nx:.8g} {ny:.8g} {nz:.8g}\n")

            f.write("\n")
            for face in polys:
                if has_normals:
                    parts = " ".join(f"{idx + 1}//{idx + 1}" for idx in face)
                else:
                    parts = " ".join(str(idx + 1) for idx in face)
                f.write(f"f {parts}\n")

        return True
    except Exception as e:
        logger.error("Failed to write OBJ file %s: %s", obj_path, e)
        return False
