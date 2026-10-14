from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

logger = logging.getLogger(__name__)

from harmo.model.web_editor.app.models.project import FrameRecord, MarkerRecord, MeshRecord, ProjectState
from harmo.model.web_editor.app.utils.transform import local_to_world_stub, world_to_local_stub

Vec3 = Tuple[float, float, float]

# Typical frame offsets used in the stub to simulate a skeleton hierarchy.
_STUB_FRAME_OFFSETS: Dict[str, Vec3] = {
    "/bodyset/pelvis": (0.0, 0.95, 0.0),
    "/bodyset/femur_r": (0.09, 0.55, 0.0),
    "/bodyset/femur_l": (-0.09, 0.55, 0.0),
    "/bodyset/tibia_r": (0.09, 0.25, 0.0),
    "/bodyset/tibia_l": (-0.09, 0.25, 0.0),
}

_STUB_MARKERS = [
    ("R.ASIS", "/bodyset/pelvis", (0.12, 0.02, 0.13)),
    ("L.ASIS", "/bodyset/pelvis", (-0.12, 0.02, 0.13)),
    ("R.PSIS", "/bodyset/pelvis", (0.07, 0.02, -0.12)),
    ("L.PSIS", "/bodyset/pelvis", (-0.07, 0.02, -0.12)),
    ("R.Knee.Lat", "/bodyset/femur_r", (0.04, -0.38, 0.06)),
    ("R.Knee.Med", "/bodyset/femur_r", (-0.01, -0.38, 0.04)),
    ("L.Knee.Lat", "/bodyset/femur_l", (-0.04, -0.38, 0.06)),
    ("L.Knee.Med", "/bodyset/femur_l", (0.01, -0.38, 0.04)),
    ("R.Ankle.Lat", "/bodyset/tibia_r", (0.03, -0.38, 0.05)),
    ("L.Ankle.Lat", "/bodyset/tibia_l", (-0.03, -0.38, 0.05)),
]

_STUB_FRAMES = [
    ("/bodyset/pelvis", "pelvis", None),
    ("/bodyset/femur_r", "femur_r", "/bodyset/pelvis"),
    ("/bodyset/femur_l", "femur_l", "/bodyset/pelvis"),
    ("/bodyset/tibia_r", "tibia_r", "/bodyset/femur_r"),
    ("/bodyset/tibia_l", "tibia_l", "/bodyset/femur_l"),
]


def _parse_vec3(text: str) -> Optional[Vec3]:
    """Parse a space-separated string of three floats into a Vec3 tuple."""
    try:
        parts = text.strip().split()
        if len(parts) == 3:
            return (float(parts[0]), float(parts[1]), float(parts[2]))
    except (ValueError, AttributeError):
        pass
    return None


def _extract_body_transforms(root) -> Dict[str, Vec3]:
    """Parse joint hierarchy from .osim XML and compute default-pose world
    translations for each body.

    Returns a dict mapping body path (e.g. '/bodyset/pelvis') to world translation.
    """

    body_world: Dict[str, Vec3] = {"/ground": (0.0, 0.0, 0.0)}

    # Find all joint types
    joint_types = ["CustomJoint", "PinJoint", "WeldJoint", "FreeJoint",
                   "BallJoint", "EllipsoidJoint", "GimbalJoint",
                   "UniversalJoint", "PointConstraint", "SliderJoint",
                   "PlanarJoint", "ScapulothoracicJoint"]

    joint_els = []
    for jtype in joint_types:
        joint_els.extend(root.findall(f".//JointSet//{jtype}"))

    joints = []
    for joint_el in joint_els:
        parent_frame_name = (joint_el.findtext("socket_parent_frame") or "").strip()
        child_frame_name = (joint_el.findtext("socket_child_frame") or "").strip()

        if not parent_frame_name or not child_frame_name:
            continue

        frames_el = joint_el.findall(".//PhysicalOffsetFrame")

        parent_body_path = None
        parent_translation = (0.0, 0.0, 0.0)
        child_body_path = None
        child_translation = (0.0, 0.0, 0.0)

        for frame_el in frames_el:
            frame_name = frame_el.get("name", "")
            socket_parent = (frame_el.findtext("socket_parent") or "").strip()
            trans_text = (frame_el.findtext("translation") or "").strip()
            translation = _parse_vec3(trans_text) if trans_text else (0.0, 0.0, 0.0)
            if translation is None:
                translation = (0.0, 0.0, 0.0)

            if frame_name == parent_frame_name:
                parent_body_path = socket_parent
                parent_translation = translation
            elif frame_name == child_frame_name:
                child_body_path = socket_parent
                child_translation = translation

        # Read default coordinate values for translation coordinates
        coord_defaults: Dict[str, float] = {}
        for coord_el in joint_el.findall(".//Coordinate"):
            coord_name = coord_el.get("name", "")
            default_val = coord_el.findtext("default_value")
            if default_val is not None:
                try:
                    coord_defaults[coord_name] = float(default_val)
                except ValueError:
                    pass

        # For CustomJoint, read SpatialTransform translation axes
        joint_default_translation = [0.0, 0.0, 0.0]
        spatial_transform = joint_el.find("SpatialTransform")
        if spatial_transform is not None:
            for axis_el in spatial_transform.findall("TransformAxis"):
                axis_name = axis_el.get("name", "")
                if not axis_name.startswith("translation"):
                    continue
                coord_name = (axis_el.findtext("coordinates") or "").strip()
                axis_vec = _parse_vec3(axis_el.findtext("axis") or "")
                if coord_name and coord_name in coord_defaults and axis_vec:
                    val = coord_defaults[coord_name]
                    for i in range(3):
                        joint_default_translation[i] += axis_vec[i] * val

        if parent_body_path and child_body_path:
            joints.append({
                "parent_body": parent_body_path,
                "child_body": child_body_path,
                "parent_translation": parent_translation,
                "child_translation": child_translation,
                "joint_translation": tuple(joint_default_translation),
            })

    # Iteratively resolve body positions
    resolved = True
    max_iterations = 50
    iteration = 0
    while resolved and iteration < max_iterations:
        resolved = False
        iteration += 1
        for j in joints:
            child = j["child_body"]
            parent = j["parent_body"]
            if child in body_world:
                continue
            if parent not in body_world:
                continue
            pw = body_world[parent]
            pt = j["parent_translation"]
            jt = j["joint_translation"]
            body_world[child] = (
                pw[0] + pt[0] + jt[0],
                pw[1] + pt[1] + jt[1],
                pw[2] + pt[2] + jt[2],
            )
            resolved = True

    return body_world


def _extract_osim_data(file_path: str) -> Optional[dict]:
    """Attempt to extract marker/frame/mesh data directly from an .osim XML file."""
    try:
        from lxml import etree

        tree = etree.parse(file_path)
        root = tree.getroot()

        model_el = root.find(".//Model")
        model_name = model_el.get("name", "unknown_model") if model_el is not None else "unknown_model"

        # Parse joint hierarchy for body world positions
        body_world = _extract_body_transforms(root)

        frames: List[FrameRecord] = []
        meshes: List[MeshRecord] = []
        bodies = root.findall(".//BodySet//Body")
        for body in bodies:
            body_name = body.get("name", "body")
            body_path = f"/bodyset/{body_name}"
            world_trans = body_world.get(body_path)
            frames.append(FrameRecord(
                path=body_path,
                name=body_name,
                parentPath=None,
                translation=world_trans,
            ))

            # Extract mesh references from attached_geometry
            for mesh_el in body.findall(".//attached_geometry/Mesh"):
                mesh_file_el = mesh_el.find("mesh_file")
                if mesh_file_el is None or not mesh_file_el.text:
                    continue
                mesh_file = mesh_file_el.text.strip()
                scale_el = mesh_el.find("scale_factors")
                scale = _parse_vec3(scale_el.text) if scale_el is not None and scale_el.text else None
                meshes.append(MeshRecord(
                    id=str(uuid4()),
                    framePath=body_path,
                    meshFile=mesh_file,
                    scale=scale,
                ))

        frame_paths = {f.path for f in frames}
        # Build offset map from body translations
        frame_offsets: Dict[str, Vec3] = {}
        for f in frames:
            if f.translation:
                frame_offsets[f.path] = f.translation
            else:
                frame_offsets[f.path] = (0.0, 0.0, 0.0)

        markers: List[MarkerRecord] = []
        for marker_el in root.findall(".//MarkerSet//Marker"):
            m_name = marker_el.get("name", f"marker_{uuid4().hex[:6]}")
            loc_el = marker_el.find("location")
            socket_el = marker_el.find("socket_parent_frame")
            if loc_el is None or socket_el is None:
                continue
            local = _parse_vec3(loc_el.text or "")
            if local is None:
                continue
            parent_path = (socket_el.text or "").strip()
            # Normalise path prefix
            if not parent_path.startswith("/"):
                parent_path = "/" + parent_path
            if parent_path not in frame_paths:
                frames.append(FrameRecord(path=parent_path, name=parent_path.split("/")[-1]))
                frame_paths.add(parent_path)
                frame_offsets[parent_path] = body_world.get(parent_path, (0.0, 0.0, 0.0))
            offset = frame_offsets.get(parent_path, (0.0, 0.0, 0.0))
            world = local_to_world_stub(local, offset)
            markers.append(
                MarkerRecord(
                    name=m_name,
                    parentFramePath=parent_path,
                    local=local,
                    world=world,
                    originalName=m_name,
                    originalParentFramePath=parent_path,
                    originalLocal=local,
                    status="existing",
                )
            )

        return {"modelName": model_name, "markers": markers, "frames": frames, "meshes": meshes}
    except Exception as e:
        logger.warning("Failed to extract .osim data from %s: %s", file_path, e)
        return None


class OpenSimService:
    def load_model(self, file_path: str, project_id: str) -> dict:
        """Load .osim file and extract markers and frames."""
        try:
            import opensim  # type: ignore
            return self._load_with_opensim(file_path, project_id)
        except ImportError:
            pass

        xml_data = _extract_osim_data(file_path)
        if xml_data is not None:
            return xml_data

        return self._load_stub(file_path, project_id)

    def _load_with_opensim(self, file_path: str, project_id: str) -> dict:
        import opensim  # type: ignore

        model = opensim.Model(file_path)
        model.initSystem()

        frames: List[FrameRecord] = []
        markers: List[MarkerRecord] = []
        meshes: List[MeshRecord] = []

        body_set = model.getBodySet()
        for i in range(body_set.getSize()):
            body = body_set.get(i)
            b_name = body.getName()
            path = f"/bodyset/{b_name}"
            frames.append(FrameRecord(path=path, name=b_name, parentPath=None))

        sim_state = model.initSystem()
        marker_set = model.getMarkerSet()
        for i in range(marker_set.getSize()):
            m = marker_set.get(i)
            m_name = m.getName()
            parent_body = m.getParentFrameName()
            loc = m.get_location()
            local: Vec3 = (loc[0], loc[1], loc[2])
            world_loc = m.getLocationInGround(sim_state)
            world: Vec3 = (world_loc[0], world_loc[1], world_loc[2])
            markers.append(
                MarkerRecord(
                    name=m_name,
                    parentFramePath=parent_body,
                    local=local,
                    world=world,
                    originalName=m_name,
                    originalParentFramePath=parent_body,
                    originalLocal=local,
                    status="existing",
                )
            )

        # OpenSim SDK path does not expose mesh references directly in this
        # implementation, so enrich frames/meshes from XML when available.
        xml_data = _extract_osim_data(file_path)
        if xml_data is not None:
            xml_frames: Dict[str, FrameRecord] = {f.path: f for f in xml_data.get("frames", [])}
            for frame in frames:
                xml_frame = xml_frames.get(frame.path)
                if xml_frame and xml_frame.translation is not None:
                    frame.translation = xml_frame.translation

            existing_paths = {f.path for f in frames}
            for xml_frame in xml_data.get("frames", []):
                if xml_frame.path not in existing_paths:
                    frames.append(xml_frame)
                    existing_paths.add(xml_frame.path)

            meshes = xml_data.get("meshes", [])

        return {
            "modelName": model.getName(),
            "markers": markers,
            "frames": frames,
            "meshes": meshes,
        }

    def _load_stub(self, file_path: str, project_id: str) -> dict:
        model_name = os.path.splitext(os.path.basename(file_path))[0]

        frames = [
            FrameRecord(path=path, name=name, parentPath=parent)
            for path, name, parent in _STUB_FRAMES
        ]

        markers = []
        for m_name, parent_path, local in _STUB_MARKERS:
            offset = _STUB_FRAME_OFFSETS.get(parent_path, (0.0, 0.0, 0.0))
            world = local_to_world_stub(local, offset)
            markers.append(
                MarkerRecord(
                    name=m_name,
                    parentFramePath=parent_path,
                    local=local,
                    world=world,
                    originalName=m_name,
                    originalParentFramePath=parent_path,
                    originalLocal=local,
                    status="existing",
                )
            )

        return {"modelName": model_name, "markers": markers, "frames": frames, "meshes": []}

    def compute_world_coords(self, local: Vec3, parent_frame_path: str, frames_map: dict) -> Vec3:
        """Convert local coordinates to world coordinates."""
        offset = frames_map.get(parent_frame_path, (0.0, 0.0, 0.0))
        return local_to_world_stub(local, offset)

    def compute_local_from_world(self, world: Vec3, parent_frame_path: str, frames_map: dict) -> Vec3:
        """Convert world coordinates to local coordinates."""
        offset = frames_map.get(parent_frame_path, (0.0, 0.0, 0.0))
        return world_to_local_stub(world, offset)

    def save_model(self, state: ProjectState, output_path: str) -> bool:
        """Save edited markers back to an .osim file."""
        try:
            import opensim  # type: ignore
            return self._save_with_opensim(state, output_path)
        except ImportError:
            pass

        if state.originalFilePath and os.path.isfile(state.originalFilePath):
            return self._save_with_lxml(state, state.originalFilePath, output_path)

        return self._create_osim_from_scratch(state, output_path)

    def _save_with_opensim(self, state: ProjectState, output_path: str) -> bool:
        import opensim  # type: ignore

        model = opensim.Model(state.originalFilePath)
        marker_set = model.updMarkerSet()

        for marker in state.markers:
            if marker.status == "delete-pending":
                idx = marker_set.getIndex(marker.originalName or marker.name)
                if idx >= 0:
                    marker_set.remove(idx)

        for marker in state.markers:
            if marker.status == "delete-pending":
                continue
            idx = marker_set.getIndex(marker.originalName or marker.name)
            if idx >= 0:
                m = marker_set.get(idx)
                m.setName(marker.name)
                loc = opensim.Vec3(marker.local[0], marker.local[1], marker.local[2])
                m.set_location(loc)
            else:
                m = opensim.Marker()
                m.setName(marker.name)
                loc = opensim.Vec3(marker.local[0], marker.local[1], marker.local[2])
                m.set_location(loc)
                marker_set.adoptAndAppend(m)

        model.printToXML(output_path)
        return True

    def _save_with_lxml(self, state: ProjectState, original_path: str, output_path: str) -> bool:
        from lxml import etree

        parser = etree.XMLParser(remove_blank_text=False)
        tree = etree.parse(original_path, parser)
        root = tree.getroot()

        objects_el = root.find(".//MarkerSet/objects")
        if objects_el is None:
            model_el = root.find(".//Model")
            if model_el is None:
                return False
            marker_set_el = etree.SubElement(model_el, "MarkerSet")
            objects_el = etree.SubElement(marker_set_el, "objects")

        existing_els: Dict[str, "etree._Element"] = {}
        for m_el in objects_el.findall("Marker"):
            name = m_el.get("name", "")
            if name:
                existing_els[name] = m_el

        delete_original_names = {
            (m.originalName or m.name)
            for m in state.markers
            if m.status == "delete-pending"
        }

        for orig_name in delete_original_names:
            el = existing_els.pop(orig_name, None)
            if el is not None:
                objects_el.remove(el)

        for marker in state.markers:
            if marker.status == "delete-pending":
                continue

            orig_name = marker.originalName or marker.name
            m_el = existing_els.get(orig_name)

            if m_el is not None:
                m_el.set("name", marker.name)
                loc_el = m_el.find("location")
                if loc_el is None:
                    loc_el = etree.SubElement(m_el, "location")
                loc_el.text = f"{marker.local[0]} {marker.local[1]} {marker.local[2]}"
                socket_el = m_el.find("socket_parent_frame")
                if socket_el is None:
                    socket_el = etree.SubElement(m_el, "socket_parent_frame")
                socket_el.text = marker.parentFramePath
            else:
                new_el = etree.SubElement(objects_el, "Marker")
                new_el.set("name", marker.name)
                loc_el = etree.SubElement(new_el, "location")
                loc_el.text = f"{marker.local[0]} {marker.local[1]} {marker.local[2]}"
                socket_el = etree.SubElement(new_el, "socket_parent_frame")
                socket_el.text = marker.parentFramePath

        tree.write(output_path, xml_declaration=True, encoding="UTF-8", pretty_print=True)
        return True

    def _create_osim_from_scratch(self, state: ProjectState, output_path: str) -> bool:
        """Create a minimal .osim XML file when no original file is available."""
        from lxml import etree

        root = etree.Element("OpenSimDocument")
        root.set("Version", "40000")
        model_el = etree.SubElement(root, "Model")
        model_el.set("name", state.modelName)
        marker_set_el = etree.SubElement(model_el, "MarkerSet")
        objects_el = etree.SubElement(marker_set_el, "objects")

        for marker in state.markers:
            if marker.status == "delete-pending":
                continue
            m_el = etree.SubElement(objects_el, "Marker")
            m_el.set("name", marker.name)
            loc_el = etree.SubElement(m_el, "location")
            loc_el.text = f"{marker.local[0]} {marker.local[1]} {marker.local[2]}"
            socket_el = etree.SubElement(m_el, "socket_parent_frame")
            socket_el.text = marker.parentFramePath

        tree = etree.ElementTree(root)
        tree.write(output_path, xml_declaration=True, encoding="UTF-8", pretty_print=True)
        return True


# Module-level singleton
opensim_service = OpenSimService()
