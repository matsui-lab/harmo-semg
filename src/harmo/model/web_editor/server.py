"""Embedded web server for the marker editor.

Launches a FastAPI backend that serves both the REST API and the
pre-built React frontend as static files.  When invoked from the
``harmo edit-markers`` CLI, the server automatically creates a project
from the specified .osim file and opens the browser.
"""
from __future__ import annotations

import logging
import os
import shutil
import signal
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_STATIC_DIR = str(Path(__file__).parent / "static")
_PROJECTS_BASE = "/tmp/opensim_projects"


def _find_free_port(start: int = 8090, end: int = 8190) -> int:
    """Find an available TCP port in the given range."""
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port found in range {start}-{end}")


def _create_app(
    model_path: Optional[str] = None,
    output_path: Optional[str] = None,
    geometry_dir: Optional[str] = None,
) -> "FastAPI":
    """Build and return the FastAPI application instance.

    Parameters
    ----------
    model_path : str, optional
        Path to the .osim file to auto-load on startup.
    output_path : str, optional
        If set, the save endpoint will also copy the result here.
    geometry_dir : str, optional
        Additional Geometry directory to search.
    """
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles

    from harmo.model.web_editor.app.api.projects import router as projects_router
    from harmo.model.web_editor.app.services.project_store import store

    # Stash config for the auto-load startup hook
    _startup_cfg = {
        "model_path": model_path,
        "output_path": output_path,
        "geometry_dir": geometry_dir,
        "auto_project_id": None,
    }

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # --- Startup: auto-load a model if provided via CLI ---
        if _startup_cfg["model_path"]:
            _auto_load_model(store, _startup_cfg)
        yield
        # --- Shutdown ---
        store.cleanup()

    app = FastAPI(title="harmo Marker Editor", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API routes
    app.include_router(projects_router, prefix="/api")

    # ---------- convenience endpoint: /api/auto-project ----------
    @app.get("/api/auto-project")
    async def auto_project():
        """Return the auto-loaded project id (if any)."""
        pid = _startup_cfg.get("auto_project_id")
        if pid:
            return {"projectId": pid}
        return {"projectId": None}

    # ---------- Save hook: copy result to CLI --output path ----------
    if output_path:
        from fastapi import Request, Response
        from starlette.middleware.base import BaseHTTPMiddleware

        class _CopyOnSaveMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                response = await call_next(request)
                if (
                    request.method == "POST"
                    and "/save" in str(request.url)
                    and response.status_code == 200
                ):
                    # After a successful save, copy the edited file to the CLI output
                    _copy_saved_model(store, _startup_cfg)
                return response

        app.add_middleware(_CopyOnSaveMiddleware)

    # Serve static frontend (must be last so /api routes take priority)
    if os.path.isdir(_STATIC_DIR) and os.listdir(_STATIC_DIR):
        app.mount("/assets", StaticFiles(directory=os.path.join(_STATIC_DIR, "assets")), name="assets")

        # Serve index.html for all non-API, non-asset routes (SPA fallback)
        from fastapi.responses import FileResponse as _FR

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str):
            index = os.path.join(_STATIC_DIR, "index.html")
            if os.path.isfile(index):
                return _FR(index)
            return {"detail": "Frontend not found. Build the frontend first."}
    else:
        @app.get("/")
        async def no_frontend():
            return {
                "status": "ok",
                "detail": "API is running. Frontend static files not found.",
            }

    return app


def _auto_load_model(store, cfg: dict) -> None:
    """Create a project and load the model specified via CLI."""
    import secrets

    from harmo.model.web_editor.app.services.opensim_service import opensim_service

    model_path = cfg["model_path"]
    if not model_path or not os.path.isfile(model_path):
        logger.warning("Model file not found: %s", model_path)
        return

    model_path = os.path.abspath(model_path)
    model_name = os.path.splitext(os.path.basename(model_path))[0]

    project_id = "proj_" + secrets.token_hex(4)
    work_dir = os.path.join(_PROJECTS_BASE, project_id)
    os.makedirs(work_dir, exist_ok=True)

    # Copy the .osim file and its sibling Geometry directory
    dest_osim = os.path.join(work_dir, os.path.basename(model_path))
    shutil.copy2(model_path, dest_osim)

    # Also copy Geometry folder if it exists near the model
    geom_candidates = []
    if cfg.get("geometry_dir"):
        geom_candidates.append(cfg["geometry_dir"])
    model_parent = os.path.dirname(model_path)
    geom_candidates += [
        os.path.join(model_parent, "Geometry"),
        os.path.join(model_parent, "geometry"),
        os.path.join(os.path.dirname(model_parent), "Geometry"),
        os.path.join(os.path.dirname(model_parent), "geometry"),
    ]
    for gdir in geom_candidates:
        if os.path.isdir(gdir):
            dest_geom = os.path.join(work_dir, "Geometry")
            if not os.path.exists(dest_geom):
                shutil.copytree(gdir, dest_geom)
            break

    state = store.create_project(project_id, model_name, work_dir)
    state.originalFilePath = dest_osim
    store.save_project(state)

    # Load the model data
    data = opensim_service.load_model(dest_osim, project_id)
    state.modelName = data["modelName"]
    state.markers = data["markers"]
    state.frames = data["frames"]
    state.meshes = data.get("meshes", [])

    # Set mesh URLs
    for m in state.meshes:
        serve_name = os.path.basename(m.meshFile)
        if serve_name.lower().endswith(".vtp"):
            serve_name = serve_name.rsplit(".", 1)[0] + ".obj"
        m.url = f"/api/projects/{project_id}/geometry/{serve_name}"

    store.save_project(state)
    cfg["auto_project_id"] = project_id

    logger.info(
        "Auto-loaded model '%s' as project %s (%d markers, %d meshes)",
        model_name,
        project_id,
        len(state.markers),
        len(state.meshes),
    )


def _copy_saved_model(store, cfg: dict) -> None:
    """After save, copy the edited .osim to the CLI output path."""
    output_path = cfg.get("output_path")
    project_id = cfg.get("auto_project_id")
    if not output_path or not project_id:
        return
    state = store.get_project(project_id)
    if not state:
        return
    edited = os.path.join(state.workDir, f"{state.modelName}_edited.osim")
    if os.path.isfile(edited):
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        shutil.copy2(edited, output_path)
        logger.info("Copied edited model to: %s", output_path)


def run_server(
    model_path: Optional[str] = None,
    output_path: Optional[str] = None,
    geometry_dir: Optional[str] = None,
    port: int = 0,
    open_browser: bool = True,
) -> int:
    """Start the embedded marker editor web server.

    Parameters
    ----------
    model_path : str, optional
        Path to .osim model file to auto-load.
    output_path : str, optional
        Where to copy the edited .osim on save.
    geometry_dir : str, optional
        Additional Geometry directory.
    port : int
        Port to listen on (0 = auto-select).
    open_browser : bool
        Whether to open the browser automatically.

    Returns
    -------
    int
        Exit code (0 = success).
    """
    import uvicorn

    if port == 0:
        port = _find_free_port()

    os.makedirs(_PROJECTS_BASE, exist_ok=True)

    app = _create_app(
        model_path=model_path,
        output_path=output_path,
        geometry_dir=geometry_dir,
    )

    url = f"http://127.0.0.1:{port}"

    if open_browser:
        def _delayed_open():
            time.sleep(1.5)
            webbrowser.open(url)

        threading.Thread(target=_delayed_open, daemon=True).start()

    print(f"\n  harmo marker-editor (web)")
    print(f"  ─────────────────────────")
    if model_path:
        print(f"  Model:   {model_path}")
    if output_path:
        print(f"  Output:  {output_path}")
    print(f"  URL:     {url}")
    print(f"  API:     {url}/api/auto-project")
    print(f"\n  Press Ctrl+C to stop the server.\n")

    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    except KeyboardInterrupt:
        pass

    return 0
