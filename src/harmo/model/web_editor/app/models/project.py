from __future__ import annotations

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Literal, Tuple
from uuid import uuid4

Vec3 = Tuple[float, float, float]

MarkerStatus = Literal["existing", "new", "modified", "delete-pending"]


class MarkerRecord(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    parentFramePath: str
    local: Vec3
    world: Vec3
    originalName: Optional[str] = None
    originalParentFramePath: Optional[str] = None
    originalLocal: Optional[Vec3] = None
    status: MarkerStatus = "existing"
    visible: bool = True
    selected: bool = False


class FrameRecord(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    path: str
    name: str
    parentPath: Optional[str] = None
    translation: Optional[Vec3] = None


class MeshRecord(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    framePath: str
    meshFile: str = ""
    url: Optional[str] = None
    scale: Optional[Vec3] = None


class ProjectState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    projectId: str
    modelName: str
    workDir: str
    originalFilePath: Optional[str] = None
    markers: List[MarkerRecord] = []
    frames: List[FrameRecord] = []
    meshes: List[MeshRecord] = []
