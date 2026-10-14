from __future__ import annotations

import threading
from typing import Dict, List, Optional

from harmo.model.web_editor.app.models.project import ProjectState


class ProjectStore:
    """Thread-safe in-memory store mapping projectId -> ProjectState."""

    def __init__(self) -> None:
        self._store: Dict[str, ProjectState] = {}
        self._lock = threading.Lock()

    def create_project(self, project_id: str, model_name: str, work_dir: str) -> ProjectState:
        state = ProjectState(
            projectId=project_id,
            modelName=model_name,
            workDir=work_dir,
        )
        with self._lock:
            self._store[project_id] = state
        return state

    def get_project(self, project_id: str) -> Optional[ProjectState]:
        with self._lock:
            return self._store.get(project_id)

    def save_project(self, state: ProjectState) -> None:
        with self._lock:
            self._store[state.projectId] = state

    def list_projects(self) -> List[str]:
        with self._lock:
            return list(self._store.keys())

    def delete_project(self, project_id: str) -> None:
        with self._lock:
            self._store.pop(project_id, None)

    def cleanup(self) -> None:
        """Remove all projects from the store."""
        with self._lock:
            self._store.clear()


# Module-level singleton
store = ProjectStore()
