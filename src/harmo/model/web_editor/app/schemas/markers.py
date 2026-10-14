from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Literal, Tuple

Vec3 = Tuple[float, float, float]


class CreateMarkerRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    parentFramePath: str
    locationLocal: Vec3


class UpdateMarkerRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: Optional[str] = None
    parentFramePath: Optional[str] = None
    locationLocal: Optional[Vec3] = None


class UpdateMarkerWorldRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    locationWorld: Vec3


class MarkerResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    name: str
    parentFramePath: str
    locationLocal: Vec3
    locationWorld: Vec3
    originalName: Optional[str] = None
    originalParentFramePath: Optional[str] = None
    originalLocal: Optional[Vec3] = None
    status: str
    visible: bool
    selected: bool


class ProjectUploadResponse(BaseModel):
    projectId: str
    modelName: str
    status: str


class FrameInfo(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    path: str
    name: str
    parentPath: Optional[str] = None
    translation: Optional[Vec3] = None


class MeshInfo(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    framePath: str
    meshFile: str = ""
    url: Optional[str] = None
    scale: Optional[Vec3] = None


class ProjectSceneResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    projectId: str
    modelName: str
    units: Literal["m"] = "m"
    frames: List[FrameInfo]
    markers: List[MarkerResponse]
    meshes: List[MeshInfo]


class DiffSummaryResponse(BaseModel):
    added: List[str]
    modified: List[str]
    renamed: List[str]
    deletePending: List[str]


class ValidationError(BaseModel):
    markerId: str
    markerName: str
    message: str


class ValidationResponse(BaseModel):
    valid: bool
    errors: List[ValidationError]


class SaveResponse(BaseModel):
    projectId: str
    downloadUrl: str
    savedAt: str
