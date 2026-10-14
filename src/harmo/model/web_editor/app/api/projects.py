from __future__ import annotations

import asyncio
import os
import shutil
import zipfile
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from harmo.model.web_editor.app.models.project import MarkerRecord, ProjectState
from harmo.model.web_editor.app.schemas.markers import (
    CreateMarkerRequest,
    DiffSummaryResponse,
    FrameInfo,
    MarkerResponse,
    MeshInfo,
    ProjectSceneResponse,
    ProjectUploadResponse,
    SaveResponse,
    UpdateMarkerRequest,
    UpdateMarkerWorldRequest,
    ValidationError,
    ValidationResponse,
)
from harmo.model.web_editor.app.services.opensim_service import opensim_service
from harmo.model.web_editor.app.services.project_store import store

router = APIRouter()

_PROJECTS_BASE = "/tmp/opensim_projects"
_SAMPLES_BASE = os.environ.get("OPENSIM_SAMPLES_DIR", "")


def _new_project_id() -> str:
    import secrets

    return "proj_" + secrets.token_hex(4)


def _marker_to_response(m: MarkerRecord) -> MarkerResponse:
    return MarkerResponse(
        id=m.id,
        name=m.name,
        parentFramePath=m.parentFramePath,
        locationLocal=m.local,
        locationWorld=m.world,
        originalName=m.originalName,
        originalParentFramePath=m.originalParentFramePath,
        originalLocal=m.originalLocal,
        status=m.status,
        visible=m.visible,
        selected=m.selected,
    )


def _get_project_or_404(project_id: str) -> ProjectState:
    state = store.get_project(project_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    return state


def _frames_offset_map(state: ProjectState) -> Dict[str, tuple]:
    """Build a path -> offset map using frame translations."""
    offset_map: Dict[str, tuple] = {}
    for frame in state.frames:
        if frame.translation:
            offset_map[frame.path] = frame.translation
        else:
            offset_map[frame.path] = (0.0, 0.0, 0.0)
    return offset_map


def _build_scene_response(project_id: str, state: ProjectState) -> ProjectSceneResponse:
    """Build a ProjectSceneResponse, populating mesh URLs."""
    for m in state.meshes:
        serve_name = os.path.basename(m.meshFile)
        if serve_name.lower().endswith(".vtp"):
            serve_name = serve_name.rsplit(".", 1)[0] + ".obj"
        m.url = f"/api/projects/{project_id}/geometry/{serve_name}"
    store.save_project(state)

    return ProjectSceneResponse(
        projectId=project_id,
        modelName=state.modelName,
        units="m",
        frames=[
            FrameInfo(path=f.path, name=f.name, parentPath=f.parentPath, translation=f.translation)
            for f in state.frames
        ],
        markers=[_marker_to_response(m) for m in state.markers],
        meshes=[
            MeshInfo(id=m.id, framePath=m.framePath, meshFile=m.meshFile, url=m.url, scale=m.scale)
            for m in state.meshes
        ],
    )


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


@router.post("/projects", response_model=ProjectUploadResponse, status_code=201)
async def upload_project(file: UploadFile = File(...)):
    """Accept an .osim or .zip file and create a new project."""
    filename = file.filename or "upload"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".osim", ".zip"):
        raise HTTPException(status_code=400, detail="Only .osim or .zip files are accepted")

    project_id = _new_project_id()
    work_dir = os.path.join(_PROJECTS_BASE, project_id)
    os.makedirs(work_dir, exist_ok=True)

    dest_path = os.path.join(work_dir, filename)
    content = await file.read()
    with open(dest_path, "wb") as fh:
        fh.write(content)

    osim_path: Optional[str] = None

    if ext == ".zip":
        with zipfile.ZipFile(dest_path, "r") as zf:
            # Validate paths to prevent zip slip
            real_work_dir = os.path.realpath(work_dir)
            for member in zf.infolist():
                target = os.path.realpath(os.path.join(work_dir, member.filename))
                if not target.startswith(real_work_dir + os.sep) and target != real_work_dir:
                    raise HTTPException(status_code=400, detail="Zip contains unsafe path entries")
            zf.extractall(work_dir)
        for root_dir, _, files in os.walk(work_dir):
            for f in files:
                if f.endswith(".osim"):
                    osim_path = os.path.join(root_dir, f)
                    break
            if osim_path:
                break
        if osim_path is None:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail="No .osim file found inside the zip archive")
    else:
        osim_path = dest_path

    model_name = os.path.splitext(os.path.basename(osim_path))[0]
    state = store.create_project(project_id, model_name, work_dir)
    state.originalFilePath = osim_path
    store.save_project(state)

    return ProjectUploadResponse(projectId=project_id, modelName=model_name, status="uploaded")


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/load", response_model=ProjectSceneResponse)
async def load_project(project_id: str):
    """Load the model and populate the project scene."""
    state = _get_project_or_404(project_id)

    if not state.originalFilePath or not os.path.isfile(state.originalFilePath):
        raise HTTPException(status_code=422, detail="Original model file not found on disk")

    data = opensim_service.load_model(state.originalFilePath, project_id)

    state.modelName = data["modelName"]
    state.markers = data["markers"]
    state.frames = data["frames"]
    state.meshes = data.get("meshes", [])

    return _build_scene_response(project_id, state)


# ---------------------------------------------------------------------------
# Geometry serving
# ---------------------------------------------------------------------------


def _find_geometry_file(work_dir: str, filename: str) -> Optional[str]:
    """Search for a geometry file anywhere under the work directory."""
    for root_dir, _, files in os.walk(work_dir):
        if filename in files:
            return os.path.join(root_dir, filename)
    return None


@router.get("/projects/{project_id}/geometry/{filename}")
async def serve_geometry(project_id: str, filename: str):
    """Serve a geometry file, converting VTP to OBJ on the fly."""
    state = _get_project_or_404(project_id)
    safe_filename = os.path.basename(filename)

    if safe_filename.lower().endswith(".obj"):
        obj_path = _find_geometry_file(state.workDir, safe_filename)
        if obj_path:
            return FileResponse(obj_path, media_type="text/plain", filename=safe_filename)

        vtp_filename = safe_filename.rsplit(".", 1)[0] + ".vtp"
        vtp_path = _find_geometry_file(state.workDir, vtp_filename)
        if vtp_path:
            converted_dir = os.path.join(state.workDir, "_converted")
            os.makedirs(converted_dir, exist_ok=True)
            obj_output = os.path.join(converted_dir, safe_filename)
            if not os.path.exists(obj_output):
                from harmo.model.web_editor.app.utils.vtp_to_obj import convert_vtp_to_obj
                if not convert_vtp_to_obj(vtp_path, obj_output):
                    raise HTTPException(status_code=500, detail=f"Failed to convert {vtp_filename} to OBJ")
            return FileResponse(obj_output, media_type="text/plain", filename=safe_filename)

        raise HTTPException(status_code=404, detail=f"Geometry file '{safe_filename}' not found")

    file_path = _find_geometry_file(state.workDir, safe_filename)
    if not file_path:
        raise HTTPException(status_code=404, detail=f"Geometry file '{safe_filename}' not found")
    return FileResponse(file_path)


# ---------------------------------------------------------------------------
# Samples
# ---------------------------------------------------------------------------


@router.get("/samples")
async def list_samples():
    """List available sample models."""
    samples = []
    if not _SAMPLES_BASE or not os.path.isdir(_SAMPLES_BASE):
        return {"samples": samples}
    for entry in sorted(os.listdir(_SAMPLES_BASE)):
        model_dir = os.path.join(_SAMPLES_BASE, entry)
        if not os.path.isdir(model_dir):
            continue
        osim_files = []
        for root_dir, _, files in os.walk(model_dir):
            for f in sorted(files):
                if f.endswith(".osim"):
                    osim_files.append(f)
        if osim_files:
            samples.append({"name": entry, "files": osim_files})
    return {"samples": samples}


@router.post("/samples/{sample_name}/load", response_model=ProjectSceneResponse)
async def load_sample(sample_name: str, file: str = Query(...)):
    """Load a sample model into a new project."""
    safe_name = os.path.basename(sample_name)
    sample_dir = os.path.join(_SAMPLES_BASE, safe_name)
    if not os.path.isdir(sample_dir):
        raise HTTPException(status_code=404, detail=f"Sample '{safe_name}' not found")

    safe_file = os.path.basename(file)
    osim_path = None
    for root_dir, _, files in os.walk(sample_dir):
        if safe_file in files:
            osim_path = os.path.join(root_dir, safe_file)
            break
    if not osim_path:
        raise HTTPException(status_code=404, detail=f"File '{safe_file}' not found in sample '{safe_name}'")

    project_id = _new_project_id()
    work_dir = os.path.join(_PROJECTS_BASE, project_id)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, shutil.copytree, sample_dir, work_dir)

    new_osim_path = None
    for root_dir, _, files in os.walk(work_dir):
        if safe_file in files:
            new_osim_path = os.path.join(root_dir, safe_file)
            break

    model_name = os.path.splitext(safe_file)[0]
    state = store.create_project(project_id, model_name, work_dir)
    state.originalFilePath = new_osim_path
    store.save_project(state)

    data = opensim_service.load_model(new_osim_path, project_id)
    state.modelName = data["modelName"]
    state.markers = data["markers"]
    state.frames = data["frames"]
    state.meshes = data.get("meshes", [])

    return _build_scene_response(project_id, state)


# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/markers", response_model=List[MarkerResponse])
async def list_markers(project_id: str):
    state = _get_project_or_404(project_id)
    return [_marker_to_response(m) for m in state.markers]


@router.post("/projects/{project_id}/markers", response_model=MarkerResponse, status_code=201)
async def add_marker(project_id: str, body: CreateMarkerRequest):
    state = _get_project_or_404(project_id)

    valid_paths = {f.path for f in state.frames}
    if valid_paths and body.parentFramePath not in valid_paths:
        raise HTTPException(
            status_code=422,
            detail=f"parentFramePath '{body.parentFramePath}' is not a known frame in this project",
        )

    offset_map = _frames_offset_map(state)
    world = opensim_service.compute_world_coords(body.locationLocal, body.parentFramePath, offset_map)

    marker = MarkerRecord(
        name=body.name,
        parentFramePath=body.parentFramePath,
        local=body.locationLocal,
        world=world,
        originalName=None,
        originalParentFramePath=None,
        originalLocal=None,
        status="new",
    )
    state.markers.append(marker)
    store.save_project(state)
    return _marker_to_response(marker)


@router.patch("/projects/{project_id}/markers/{marker_id}", response_model=MarkerResponse)
async def update_marker_local(project_id: str, marker_id: str, body: UpdateMarkerRequest):
    state = _get_project_or_404(project_id)
    marker = next((m for m in state.markers if m.id == marker_id), None)
    if marker is None:
        raise HTTPException(status_code=404, detail=f"Marker '{marker_id}' not found")

    offset_map = _frames_offset_map(state)

    if body.name is not None:
        marker.name = body.name
    if body.parentFramePath is not None:
        valid_paths = {f.path for f in state.frames}
        if valid_paths and body.parentFramePath not in valid_paths:
            raise HTTPException(
                status_code=422,
                detail=f"parentFramePath '{body.parentFramePath}' is not a known frame",
            )
        marker.parentFramePath = body.parentFramePath
    if body.locationLocal is not None:
        marker.local = body.locationLocal

    marker.world = opensim_service.compute_world_coords(marker.local, marker.parentFramePath, offset_map)

    if marker.status == "existing":
        marker.status = "modified"

    store.save_project(state)
    return _marker_to_response(marker)


@router.patch("/projects/{project_id}/markers/{marker_id}/world", response_model=MarkerResponse)
async def update_marker_world(project_id: str, marker_id: str, body: UpdateMarkerWorldRequest):
    state = _get_project_or_404(project_id)
    marker = next((m for m in state.markers if m.id == marker_id), None)
    if marker is None:
        raise HTTPException(status_code=404, detail=f"Marker '{marker_id}' not found")

    offset_map = _frames_offset_map(state)
    marker.world = body.locationWorld
    marker.local = opensim_service.compute_local_from_world(body.locationWorld, marker.parentFramePath, offset_map)

    if marker.status == "existing":
        marker.status = "modified"

    store.save_project(state)
    return _marker_to_response(marker)


@router.delete("/projects/{project_id}/markers/{marker_id}", response_model=MarkerResponse)
async def delete_marker(project_id: str, marker_id: str):
    state = _get_project_or_404(project_id)
    marker = next((m for m in state.markers if m.id == marker_id), None)
    if marker is None:
        raise HTTPException(status_code=404, detail=f"Marker '{marker_id}' not found")

    marker.status = "delete-pending"
    store.save_project(state)
    return _marker_to_response(marker)


@router.post("/projects/{project_id}/markers/{marker_id}/restore", response_model=MarkerResponse)
async def restore_marker(project_id: str, marker_id: str):
    state = _get_project_or_404(project_id)
    marker = next((m for m in state.markers if m.id == marker_id), None)
    if marker is None:
        raise HTTPException(status_code=404, detail=f"Marker '{marker_id}' not found")

    if marker.status != "delete-pending":
        raise HTTPException(status_code=422, detail="Marker is not in delete-pending state")

    marker.status = "new" if marker.originalName is None else "existing"
    store.save_project(state)
    return _marker_to_response(marker)


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/diff", response_model=DiffSummaryResponse)
async def get_diff(project_id: str):
    state = _get_project_or_404(project_id)

    added: List[str] = []
    modified: List[str] = []
    renamed: List[str] = []
    delete_pending: List[str] = []

    for m in state.markers:
        if m.status == "new":
            added.append(m.id)
        elif m.status == "modified":
            if m.originalName and m.originalName != m.name:
                renamed.append(m.id)
            else:
                modified.append(m.id)
        elif m.status == "delete-pending":
            delete_pending.append(m.id)

    return DiffSummaryResponse(
        added=added,
        modified=modified,
        renamed=renamed,
        deletePending=delete_pending,
    )


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/validate", response_model=ValidationResponse)
async def validate_project(project_id: str):
    state = _get_project_or_404(project_id)

    errors: List[ValidationError] = []
    valid_frame_paths = {f.path for f in state.frames}
    seen_names: Dict[str, str] = {}

    for m in state.markers:
        if m.status == "delete-pending":
            continue

        if m.name in seen_names:
            errors.append(
                ValidationError(
                    markerId=m.id,
                    markerName=m.name,
                    message=f"Duplicate marker name '{m.name}' (conflicts with marker {seen_names[m.name]})",
                )
            )
        else:
            seen_names[m.name] = m.id

        if valid_frame_paths and m.parentFramePath not in valid_frame_paths:
            errors.append(
                ValidationError(
                    markerId=m.id,
                    markerName=m.name,
                    message=f"Invalid parentFramePath '{m.parentFramePath}'",
                )
            )

        for label, val in [("local x", m.local[0]), ("local y", m.local[1]), ("local z", m.local[2])]:
            if not isinstance(val, (int, float)):
                errors.append(
                    ValidationError(
                        markerId=m.id,
                        markerName=m.name,
                        message=f"Non-numeric value for {label}: {val}",
                    )
                )

    return ValidationResponse(valid=len(errors) == 0, errors=errors)


# ---------------------------------------------------------------------------
# Save / Download
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/save", response_model=SaveResponse)
async def save_project(project_id: str):
    state = _get_project_or_404(project_id)

    output_filename = f"{state.modelName}_edited.osim"
    output_path = os.path.join(state.workDir, output_filename)

    ok = opensim_service.save_model(state, output_path)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to save the model")

    state.markers = [m for m in state.markers if m.status != "delete-pending"]
    for m in state.markers:
        if m.status in ("modified", "new"):
            m.originalName = m.name
            m.originalParentFramePath = m.parentFramePath
            m.originalLocal = m.local
            m.status = "existing"
    store.save_project(state)

    saved_at = datetime.now(timezone.utc).isoformat()
    download_url = f"/api/projects/{project_id}/download"
    return SaveResponse(projectId=project_id, downloadUrl=download_url, savedAt=saved_at)


@router.get("/projects/{project_id}/download")
async def download_project(project_id: str):
    state = _get_project_or_404(project_id)
    output_filename = f"{state.modelName}_edited.osim"
    output_path = os.path.join(state.workDir, output_filename)

    if not os.path.isfile(output_path):
        raise HTTPException(
            status_code=404,
            detail="Saved file not found. Call POST /save first.",
        )

    return FileResponse(
        path=output_path,
        media_type="application/octet-stream",
        filename=output_filename,
    )
