#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
marker_editor.py
================
PyQt5 + PyVista 製 OpenSim インタラクティブ・マーカー位置エディタ

【パイプライン統合位置】
    BIDS_LIKE/study_model.py → study{id}_pre_GUI.osim を生成
        ↓
    本ツール (marker_editor) で骨格3D表示・マーカー位置をインタラクティブに編集
        ↓
    study{id}_post_GUI.osim を保存
        ↓
    code/batch_runner.py (スケーリング → IK → ID → ...)

【起動方法】
    # スタンドアロン
    python marker_editor.py
    python marker_editor.py --model path/to/model.osim

    # パイプライン CLI
    pipe-gui --model path/to/model.osim

【依存パッケージ （opensim_scripting 環境）】
    PyQt5>=5.15, opensim, pyvista>=0.38, pyvistaqt>=0.11, numpy
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import tempfile
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

# ---------- PyQt5 ----------
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QColor
from PyQt5.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

# ---------- PyVista ----------
try:
    import pyvista as pv
    from pyvistaqt import QtInteractor
    import vtk
    PYVISTA_AVAILABLE = True
except ImportError:
    PYVISTA_AVAILABLE = False

# ---------- OpenSim ----------
try:
    import opensim as osim
    OPENSIM_AVAILABLE = True
except ImportError:
    OPENSIM_AVAILABLE = False

logger = logging.getLogger(__name__)

# =============================================================================
# 定数  —  opensim-gui (Bundle.properties / OpenSimViewport.js) 準拠
# =============================================================================
# マーカー球のデフォルト半径 (m) — opensim-gui CTL_MarkerRadius=0.01
MARKER_RADIUS = 0.010

# 骨メッシュ — MeshStandardMaterial 相当 (metalness=0, roughness=1)
BONE_COLOR   = "#E8D0A8"
BONE_OPACITY = 0.92

# マーカーの色 — opensim-gui Bundle.properties: CTL_MarkersColorRGB=1.0,0.6,0.8
MARKER_COLOR_NORMAL   = "#FF99CC"   # ピンク (opensim-gui デフォルト)
MARKER_COLOR_SELECTED = "#FF3300"   # 選択時オレンジ赤

# 背景色 — opensim-gui dark theme: 0x333333
BG_COLOR = "#3D3D3D"

# グラウンドグリッド — opensim-gui GridHelper(30,30, 0x444444, 0x888888)
GRID_COLOR  = "#666666"
GRID_HALF   = 2.0          # ±2 m (人体スケール)
GRID_N      = 20            # 分割数 (10cm 刻み)


# =============================================================================
# _GeometryParser: .osim XML から body→VTPファイルリストを抽出する
# =============================================================================
class _GeometryParser:
    """
    .osim XML を解析して Body ごとの ジオメトリファイル名リストを返す。
    OpenSim 3.x (DisplayGeometry) と 4.x (Mesh) の両形式に対応。
    """

    @staticmethod
    def parse(model_path: str) -> dict[str, list[str]]:
        """
        Args:
            model_path (str): .osim ファイルのパス

        Returns:
            dict[str, list[str]]: body_name → [vtp_filename, ...]
        """
        body_geoms: dict[str, list[str]] = {}
        try:
            root = ET.parse(model_path).getroot()

            # --- OpenSim 4.x 形式: Body/attached_geometry/Mesh/mesh_file ---
            for body_el in root.iter("Body"):
                name = body_el.get("name", "")
                files = [
                    mf.text.strip()
                    for mesh_el in body_el.iter("Mesh")
                    if (mf := mesh_el.find("mesh_file")) is not None
                    and mf.text
                ]
                if files:
                    body_geoms.setdefault(name, []).extend(files)

            # --- OpenSim 3.x 形式: Body/VisualObject/GeometrySet/DisplayGeometry/geometry_file ---
            for body_el in root.iter("Body"):
                name = body_el.get("name", "")
                for dg in body_el.iter("DisplayGeometry"):
                    gf = dg.findtext("geometry_file")
                    if gf and gf.strip():
                        body_geoms.setdefault(name, []).append(gf.strip())

        except Exception as exc:
            logger.warning("ジオメトリ解析失敗: %s", exc)

        return body_geoms


# =============================================================================
# _ModelState: OpenSim モデルの状態管理・座標変換計算
# =============================================================================
class _ModelState:
    """
    OpenSim モデルをロードし、初期化姿勢での:
      - 各ボディの Ground 座標系変換行列 (4x4 numpy)
      - 各マーカーの Ground 座標系位置 (numpy[3])
    を保持する。

    マーカー位置を編集しても initSystem() を呼び直す必要はない。
    ボディ変換行列は不変なので、ローカル座標から World 座標を
    T_body @ [lx, ly, lz, 1] で高速に算出できる。
    """

    def __init__(self, model: "osim.Model"):
        self.model = model  # GC 防止のため self に保持

        # initSystem() で初期化状態を計算
        self._state = model.initSystem()

        # body_name → 4x4 numpy 変換行列（Ground フレーム基準）
        self.body_transforms: dict[str, np.ndarray] = {}
        self._compute_body_transforms()

    # ---- 変換行列の計算 -------------------------------------------------------

    def _compute_body_transforms(self):
        """ボディごとに Ground 座標系変換行列を計算して保持する。"""
        body_set = self.model.getBodySet()
        for i in range(body_set.getSize()):
            body = body_set.get(i)
            name = body.getName()
            transform = body.getTransformInGround(self._state)
            R = transform.R()
            p = transform.p()
            mat = np.eye(4, dtype=float)
            for r in range(3):
                for c in range(3):
                    mat[r, c] = R.get(r, c)
                mat[r, 3] = p.get(r)
            self.body_transforms[name] = mat

    # ---- 公開 API ------------------------------------------------------------

    def marker_world_position(self, marker_name: str) -> np.ndarray:
        """
        指定マーカーの Ground 座標系位置を返す。
        set_location() 後も再 initSystem() 不要で正確な値を返す。

        Args:
            marker_name (str): マーカー名

        Returns:
            np.ndarray: shape=(3,)  世界座標 (m)
        """
        mk = self.model.getMarkerSet().get(marker_name)
        body_name = mk.getParentFrameName().split("/")[-1]  # '/bodyset/pelvis' → 'pelvis'
        loc = mk.get_location()
        local = np.array([loc.get(0), loc.get(1), loc.get(2), 1.0])
        T = self.body_transforms.get(body_name, np.eye(4))
        return (T @ local)[:3]


# =============================================================================
# _MarkerDragStyle: カメラ操作 + マーカードラッグ統合 VTK スタイル
# =============================================================================

class _MarkerDragStyle(vtk.vtkInteractorStyleTrackballCamera):  # type: ignore[misc]
    """
    vtkInteractorStyleTrackballCamera を拡張し、マーカー球体のドラッグ移動を追加。

    動作:
      - マーカー上で左ボタン押し → ドラッグでマーカーを「カメラ視線に垂直な平面」内で移動
      - 背景/骨上で左ボタン押し → 通常のカメラ回転

    コールバック (Marker3DViewport が設定する):
      cb_marker_actors  : () → dict[str, vtkActor]  マーカー名→アクタ
      cb_world_pos      : (name: str) → np.ndarray  現在の World 座標
      cb_select         : (name: str | None) → None  選択イベント
      cb_drag_move      : (name: str, pos: ndarray) → None  ドラッグ中
      cb_drag_end       : (name: str, pos: ndarray) → None  ドラッグ終了
    """

    def __init__(self):
        super().__init__()
        self.cb_marker_actors = None
        self.cb_world_pos     = None
        self.cb_select        = None
        self.cb_drag_move     = None
        self.cb_drag_end      = None

        self._drag_active          = False
        self._drag_name: str | None = None
        self._plane_origin: np.ndarray | None = None
        self._plane_normal: np.ndarray | None = None
        self._last_world:  np.ndarray | None = None

    # ---- 内部ユーティリティ -------------------------------------------------

    def _renderer(self):
        return self.GetInteractor().GetRenderWindow().GetRenderers().GetFirstRenderer()

    def _pick_marker_at_cursor(self) -> "str | None":
        """カーソル位置にあるマーカー名を返す。なければ None。"""
        iren = self.GetInteractor()
        x, y = iren.GetEventPosition()
        renderer = self._renderer()
        picker = vtk.vtkPropPicker()
        picker.Pick(x, y, 0, renderer)
        actor = picker.GetActor()
        if actor is None:
            return None
        markers = self.cb_marker_actors() if self.cb_marker_actors else {}
        for name, stored in markers.items():
            if actor is stored:
                return name
        return None

    def _cursor_to_plane(self) -> "np.ndarray | None":
        """現在のカーソル位置 → ドラッグ平面との交点 (World xyz)。"""
        iren = self.GetInteractor()
        x, y = iren.GetEventPosition()
        renderer = self._renderer()

        # Near/Far クリップ面での World 座標を取得
        renderer.SetDisplayPoint(x, y, 0)
        renderer.DisplayToWorld()
        near = np.array(renderer.GetWorldPoint()[:3])
        renderer.SetDisplayPoint(x, y, 1)
        renderer.DisplayToWorld()
        far  = np.array(renderer.GetWorldPoint()[:3])

        ray = far - near
        d = np.linalg.norm(ray)
        if d < 1e-12:
            return None
        ray /= d

        n = self._plane_normal
        denom = np.dot(ray, n)
        if abs(denom) < 1e-8:
            return None
        t = np.dot(self._plane_origin - near, n) / denom
        return near + t * ray

    # ---- VTK 仮想メソッドオーバーライド -------------------------------------

    def OnLeftButtonDown(self):
        hit = self._pick_marker_at_cursor()
        if hit is None:
            # 背景 / 骨クリック → カメラ回転
            if self.cb_select:
                self.cb_select(None)
            super().OnLeftButtonDown()
            return

        # マーカードラッグ開始
        self._drag_active = True
        self._drag_name   = hit

        world = (self.cb_world_pos(hit) if self.cb_world_pos else np.zeros(3))
        self._last_world  = world.copy()

        # ドラッグ平面: マーカー位置を通り、カメラ視線に垂直
        renderer = self._renderer()
        cam_pos  = np.array(renderer.GetActiveCamera().GetPosition())
        n = world - cam_pos
        n_len = np.linalg.norm(n)
        self._plane_normal = n / n_len if n_len > 1e-10 else np.array([0.0, 0.0, 1.0])
        self._plane_origin = world.copy()

        if self.cb_select:
            self.cb_select(hit)

    def OnMouseMove(self):
        if not self._drag_active:
            super().OnMouseMove()
            return

        new_pos = self._cursor_to_plane()
        if new_pos is None or self._drag_name is None:
            return

        self._plane_origin = new_pos.copy()   # 平面を移動させて追従
        self._last_world   = new_pos.copy()
        if self.cb_drag_move:
            self.cb_drag_move(self._drag_name, new_pos)

    def OnLeftButtonUp(self):
        if self._drag_active:
            name  = self._drag_name
            final = self._last_world
            self._drag_active = False
            self._drag_name   = None
            if self.cb_drag_end and name is not None and final is not None:
                self.cb_drag_end(name, final)
        else:
            super().OnLeftButtonUp()


# =============================================================================
# Marker3DViewport: PyVista 埋め込み 3D ビューポート
# =============================================================================
class Marker3DViewport(QtInteractor):
    """
    PyVista (VTK) を使った 3D ビューポートウィジェット。

    機能:
    - 骨格メッシュ (VTP ファイル) を World 座標でレンダリング
    - マーカーを球体 (Sphere) で表示
    - 左クリックでマーカーを選択 → `marker_picked` シグナルを emit
    - `update_marker_position()` でマーカー球体をリアルタイム更新

    シグナル:
      marker_picked(str)                    : クリックでマーカー選択 (空文字=解除)
      marker_dragged(str, float, float, float): ドラッグ中のマーカー世界座標
      marker_drag_ended(str, float, float, float): ドラッグ終了時の最終世界座標
    """

    marker_picked     = pyqtSignal(str)
    marker_dragged    = pyqtSignal(str, float, float, float)   # name, wx, wy, wz
    marker_drag_ended = pyqtSignal(str, float, float, float)   # name, wx, wy, wz

    def __init__(self, parent: QWidget | None = None):
        """pyvistagqt.QtInteractor を PyQt5 レイアウトに埋め込む"""
        super().__init__(parent)

        # マーカーアクタの辞書: name → pyvista.Actor
        self._marker_actors: dict[str, object] = {}
        # マーカー World 座標のキャッシュ: name → np.array([x,y,z])
        self._marker_world: dict[str, np.ndarray] = {}
        # 現在選択されているマーカー名
        self._selected: str | None = None

        # 高品質レンダリング設定
        self._setup_scene()

    # ---- シーン設定 -----------------------------------------------------------

    def _setup_scene(self):
        """opensim-gui 準拠の背景・ライティング・アンチエイリアシングを設定する。"""
        # --- 背景: opensim-gui dark theme (#333333) ---
        self.set_background(BG_COLOR)

        # --- アンチエイリアシング (opensim-gui: antialias=true) ---
        try:
            self.enable_anti_aliasing("ssaa")
        except Exception:
            pass

        # --- ライティング: opensim-gui 3灯式 (Ambient + Sun + Camera) ---
        self.remove_all_lights()

        # 1) Sun ライト (opensim-gui: dirLight, HSL(0.1,1,0.95), pos(1,3,-1))
        #    暖白色の太陽光を右上前方から — シーン固定
        self.add_light(pv.Light(
            position=(1.5, 3.0, 1.0),
            focal_point=(0, 0.9, 0),
            color="#FFF5E0",           # HSL(36, 100%, 95%) ≈ 暖白
            intensity=0.7,
            light_type="scene light",
        ))

        # 2) Camera ライト (opensim-gui: sceneLight, 0xB7B7B7, カメラ追従)
        #    中間グレーのヘッドライト — カメラと一緒に動く
        self.add_light(pv.Light(
            color="#B7B7B7",
            intensity=0.45,
            light_type="headlight",
        ))

        # 3) Ambient 相当 (opensim-gui: AmbientLight, 白, intensity=0.2)
        #    底面からの間接光で影を軽減
        self.add_light(pv.Light(
            position=(0, -2.0, 0),
            focal_point=(0, 1.0, 0),
            color="white",
            intensity=0.20,
            light_type="scene light",
        ))

        # マーカードラッグ + カメラ操作統合スタイルをセット
        self._drag_style = _MarkerDragStyle()
        self._drag_style.cb_marker_actors = lambda: dict(self._marker_actors)
        self._drag_style.cb_world_pos     = self.get_marker_world_pos
        self._drag_style.cb_select        = self._on_select_from_style
        self._drag_style.cb_drag_move     = self._on_drag_move_from_style
        self._drag_style.cb_drag_end      = self._on_drag_end_from_style
        self.iren.interactor.SetInteractorStyle(self._drag_style)

    # ---- グラウンドグリッド -----------------------------------------------------

    @staticmethod
    def _make_ground_grid() -> pv.PolyData:
        """
        opensim-gui の GridHelper(30,30, 0x444444, 0x888888) 相当の
        グラウンドグリッドを PolyData (線分) として生成する。

        Y=0 平面に XZ 方向のグリッドを描画する。
        """
        pts = []
        lines = []
        idx = 0
        step = (GRID_HALF * 2) / GRID_N
        for i in range(GRID_N + 1):
            t = -GRID_HALF + i * step
            # X 方向の線
            pts.append([t, 0.0, -GRID_HALF])
            pts.append([t, 0.0,  GRID_HALF])
            lines.append([2, idx, idx + 1])
            idx += 2
            # Z 方向の線
            pts.append([-GRID_HALF, 0.0, t])
            pts.append([ GRID_HALF, 0.0, t])
            lines.append([2, idx, idx + 1])
            idx += 2

        pts_arr = np.array(pts, dtype=float)
        cells = np.hstack(lines).astype(np.int64)
        grid = pv.PolyData(pts_arr, lines=cells)
        return grid

    # ---- シーン構築 -----------------------------------------------------------

    def load_scene(
        self,
        bone_meshes: list[pv.PolyData],
        marker_positions: dict[str, np.ndarray],
    ):
        """
        骨格メッシュとマーカーをシーンに追加する。

        Args:
            bone_meshes:       World 座標変換済みの骨メッシュリスト
            marker_positions:  {marker_name: np.array([wx, wy, wz])}
        """
        self.clear_actors()
        self._marker_actors.clear()
        self._marker_world.clear()
        self._selected = None

        # --- グラウンドグリッド (opensim-gui GridHelper 相当) ---
        grid = self._make_ground_grid()
        self.add_mesh(
            grid,
            color=GRID_COLOR,
            line_width=1,
            opacity=0.5,
            pickable=False,
            reset_camera=False,
        )

        # --- 骨格メッシュを追加（pickable=False で選択不可）---
        # opensim-gui MeshStandardMaterial: metalness=0, roughness=1
        for mesh in bone_meshes:
            self.add_mesh(
                mesh,
                color=BONE_COLOR,
                opacity=BONE_OPACITY,
                smooth_shading=True,
                pbr=True,
                metallic=0.0,
                roughness=0.75,
                pickable=False,
                reset_camera=False,
            )

        # --- マーカー球体を追加（pickable=True で選択可）---
        self._marker_world = dict(marker_positions)
        for name, pos in marker_positions.items():
            self._add_marker_actor(name, pos, selected=False)

        # カメラを全体が見えるようにリセット
        self.reset_camera()
        self.render()

    def _add_marker_actor(
        self,
        name: str,
        world_pos: np.ndarray,
        selected: bool,
    ):
        """
        マーカー球体アクタを追加または置き換える（内部用）。
        """
        # 既存アクタを削除
        if name in self._marker_actors:
            self.remove_actor(self._marker_actors[name], reset_camera=False)

        color  = MARKER_COLOR_SELECTED if selected else MARKER_COLOR_NORMAL
        radius = MARKER_RADIUS * 1.4 if selected else MARKER_RADIUS

        sphere = pv.Sphere(radius=radius, center=world_pos.tolist(), theta_resolution=20, phi_resolution=20)
        # opensim-gui MeshPhongMaterial: shininess=30, specular=0, emissive=0
        actor = self.add_mesh(
            sphere,
            color=color,
            smooth_shading=True,
            specular=0.15,
            specular_power=30,
            pickable=True,
            reset_camera=False,
            name=f"__marker__{name}",   # PyVista 名前付きアクタ
        )
        self._marker_actors[name] = actor
        self._marker_world[name] = world_pos.copy()

    # ---- 外部から呼ぶ公開 API -------------------------------------------------

    def select_marker(self, name: str | None):
        """
        指定マーカーをハイライト表示する（赤くする）。
        None を渡すと全マーカーをノーマル表示する。
        """
        prev = self._selected
        self._selected = name

        # 前の選択を解除
        if prev and prev in self._marker_world:
            self._add_marker_actor(prev, self._marker_world[prev], selected=False)

        # 新しい選択を強調
        if name and name in self._marker_world:
            self._add_marker_actor(name, self._marker_world[name], selected=True)

        # 選択マーカーのラベルを表示
        self._update_label(name)
        self.render()

    def update_marker_position(self, name: str, world_pos: np.ndarray):
        """
        マーカー球体の位置をリアルタイム更新する（モデルへの反映なし）。

        Args:
            name:      マーカー名
            world_pos: 新しい World 座標 np.array([x, y, z])
        """
        is_selected = (name == self._selected)
        self._add_marker_actor(name, world_pos, selected=is_selected)
        self._update_label(name if is_selected else None)
        self.render()

    def _update_label(self, name: str | None):
        """選択マーカーの名前ラベルを更新する。"""
        # 既存ラベルを削除
        try:
            self.remove_actor("__marker_label__", reset_camera=False)
        except Exception:
            pass

        if name and name in self._marker_world:
            pos = self._marker_world[name]
            label_pos = pos + np.array([0, MARKER_RADIUS * 2.5, 0])
            self.add_point_labels(
                [label_pos.tolist()],
                [name],
                text_color="white",
                font_size=10,
                bold=True,
                shape_opacity=0.45,
                reset_camera=False,
                name="__marker_label__",
            )

    # ---- _MarkerDragStyle コールバック (VTK スレッドから呼ばれる) -----------

    def get_marker_world_pos(self, name: str) -> np.ndarray:
        """キャッシュされたマーカー World 座標を返す。"""
        return self._marker_world.get(name, np.zeros(3)).copy()

    def _on_select_from_style(self, name: "str | None"):
        """クリック選択イベント → marker_picked シグナルを emit。"""
        self.marker_picked.emit(name or "")

    def _on_drag_move_from_style(self, name: str, world_pos: np.ndarray):
        """ドラッグ中: 球体をリアルタイム更新 + marker_dragged シグナルを emit。"""
        self.update_marker_position(name, world_pos)
        self.marker_dragged.emit(
            name,
            float(world_pos[0]),
            float(world_pos[1]),
            float(world_pos[2]),
        )

    def _on_drag_end_from_style(self, name: str, world_pos: np.ndarray):
        """ドラッグ終了: marker_drag_ended シグナルを emit。"""
        self.marker_drag_ended.emit(
            name,
            float(world_pos[0]),
            float(world_pos[1]),
            float(world_pos[2]),
        )


# =============================================================================
# MarkerEditorWindow: メインウィンドウ
# =============================================================================
class MarkerEditorWindow(QMainWindow):
    """
    OpenSim .osim マーカーエディタのメインウィンドウ。

    レイアウト:
      [左] マーカーリスト + 検索
      [中] Marker3DViewport (PyVista 埋め込み 3D ビュー)
      [右] マーカー情報 + X/Y/Z スピンボックス + 適用ボタン
    """

    APP_TITLE = "OpenSim マーカーエディタ"
    # スピンボックスが変わってから 3D 更新を遅らせる時間 (ms)
    _PREVIEW_DELAY_MS = 80

    def __init__(
        self,
        initial_model_path: str | None = None,
        initial_output_path: str | None = None,
        geometry_dir: str | None = None,
    ):
        super().__init__()

        # --- 内部状態 ---
        self._model: "osim.Model | None" = None      # GC 防止
        self._model_state: "_ModelState | None" = None
        self._model_path: str | None = None
        self._output_path: str | None = initial_output_path
        self._geometry_dir: str | None = geometry_dir
        self._dirty: bool = False
        self._block_spinbox: bool = False

        # スピンボックス変更のデバウンス用タイマー
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._on_preview_timeout)

        self._build_ui()
        self._update_title()

        if not OPENSIM_AVAILABLE:
            self._show_warning("OpenSim 未検出", "opensim がインストールされていません。")
        if not PYVISTA_AVAILABLE:
            self._show_warning("PyVista 未検出", "pyvistaqt がインストールされていません。\n"
                               "pip install pyvistaqt を実行してください。")

        if initial_model_path:
            self._load_model(initial_model_path)

    # =========================================================================
    # UI 構築
    # =========================================================================
    def _build_ui(self):
        """全ウィジェットのレイアウトを構築する。"""
        self.setWindowTitle(self.APP_TITLE)
        self.resize(1280, 820)

        # === opensim-gui 風ダークテーマ QSS ===
        self.setStyleSheet(textwrap.dedent("""\
            QMainWindow, QWidget {
                background-color: #2B2B2B;
                color: #CCCCCC;
                font-size: 12px;
            }
            QMenuBar {
                background-color: #333333;
                color: #CCCCCC;
            }
            QMenuBar::item:selected {
                background-color: #4D78BC;
            }
            QMenu {
                background-color: #333333;
                color: #CCCCCC;
                border: 1px solid #555555;
            }
            QMenu::item:selected {
                background-color: #4D78BC;
            }
            QToolBar {
                background-color: #333333;
                border-bottom: 1px solid #444444;
                spacing: 6px;
                padding: 2px;
            }
            QPushButton {
                background-color: #444444;
                color: #DDDDDD;
                border: 1px solid #555555;
                border-radius: 3px;
                padding: 5px 12px;
                min-height: 22px;
            }
            QPushButton:hover {
                background-color: #4D78BC;
                border-color: #5A8AD0;
            }
            QPushButton:pressed {
                background-color: #3A5F9A;
            }
            QPushButton:disabled {
                background-color: #383838;
                color: #666666;
                border-color: #444444;
            }
            QLineEdit {
                background-color: #3A3A3A;
                color: #CCCCCC;
                border: 1px solid #555555;
                border-radius: 3px;
                padding: 4px 6px;
                selection-background-color: #4D78BC;
            }
            QLineEdit:focus {
                border-color: #4D78BC;
            }
            QListWidget {
                background-color: #2F2F2F;
                color: #CCCCCC;
                border: 1px solid #444444;
                outline: none;
            }
            QListWidget::item {
                padding: 3px 4px;
            }
            QListWidget::item:selected {
                background-color: #4D78BC;
                color: #FFFFFF;
            }
            QListWidget::item:hover {
                background-color: #3A3A3A;
            }
            QGroupBox {
                color: #AAAAAA;
                border: 1px solid #444444;
                border-radius: 4px;
                margin-top: 8px;
                padding-top: 14px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QDoubleSpinBox {
                background-color: #3A3A3A;
                color: #CCCCCC;
                border: 1px solid #555555;
                border-radius: 3px;
                padding: 3px 6px;
                selection-background-color: #4D78BC;
            }
            QDoubleSpinBox:focus {
                border-color: #4D78BC;
            }
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                background-color: #444444;
                border: none;
                width: 16px;
            }
            QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
                background-color: #4D78BC;
            }
            QLabel {
                color: #CCCCCC;
                background: transparent;
            }
            QStatusBar {
                background-color: #333333;
                color: #999999;
                border-top: 1px solid #444444;
            }
            QSplitter::handle {
                background-color: #444444;
                width: 2px;
            }
            QScrollBar:vertical {
                background: #2B2B2B;
                width: 10px;
                border: none;
            }
            QScrollBar::handle:vertical {
                background: #555555;
                border-radius: 4px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: #666666;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """))

        # === メニューバー ===
        menu = self.menuBar()
        file_menu = menu.addMenu("ファイル(&F)")

        for label, shortcut, slot in [
            ("モデルを開く(&O)…", "Ctrl+O", self._on_open),
            ("上書き保存(&S)", "Ctrl+S", self._on_save),
            ("名前を付けて保存(&A)…", "Ctrl+Shift+S", self._on_save_as),
        ]:
            act = file_menu.addAction(label)
            act.setShortcut(shortcut)
            act.triggered.connect(slot)
        file_menu.addSeparator()
        q = file_menu.addAction("終了(&Q)")
        q.setShortcut("Ctrl+Q")
        q.triggered.connect(self.close)

        # === ツールバー ===
        tb = QToolBar("メインツールバー")
        tb.setMovable(False)
        self.addToolBar(tb)

        self._btn_open = QPushButton("📂 開く")
        self._btn_open.clicked.connect(self._on_open)
        tb.addWidget(self._btn_open)

        tb.addSeparator()

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)

        self._btn_save_as = QPushButton("💾 名前を付けて保存")
        self._btn_save_as.clicked.connect(self._on_save_as)
        self._btn_save_as.setEnabled(False)
        tb.addWidget(self._btn_save_as)

        # === メイン スプリッター（左 / 中央3D / 右）===
        splitter = QSplitter(Qt.Horizontal)
        self.setCentralWidget(splitter)

        # ---- 左ペイン：マーカーリスト ----
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(6, 6, 3, 6)

        lbl = QLabel("マーカー一覧")
        lbl.setFont(QFont("", 10, QFont.Bold))
        lv.addWidget(lbl)

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("名前で絞り込み…")
        self._search_box.textChanged.connect(self._on_search)
        lv.addWidget(self._search_box)

        self._marker_list = QListWidget()
        self._marker_list.currentItemChanged.connect(self._on_list_selection_changed)
        lv.addWidget(self._marker_list)

        self._count_label = QLabel("マーカー: 0 件")
        lv.addWidget(self._count_label)

        splitter.addWidget(left)

        # ---- 中央ペイン：3D ビューポート ----
        if PYVISTA_AVAILABLE:
            self._viewport = Marker3DViewport(self)
            self._viewport.marker_picked.connect(self._on_3d_marker_picked)
            self._viewport.marker_dragged.connect(self._on_marker_dragged)
            self._viewport.marker_drag_ended.connect(self._on_marker_drag_ended)
            splitter.addWidget(self._viewport)
        else:
            # フォールバック: プレースホルダー
            placeholder = QLabel("PyVista が必要です\n\npip install pyvistaqt")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet("background:#2B2B2B; color:#888888; font-size:14px;")
            splitter.addWidget(placeholder)

        # ---- 右ペイン：編集パネル ----
        right = QWidget()
        right.setMinimumWidth(260)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(3, 6, 6, 6)

        # マーカー情報グループ
        info_grp = QGroupBox("マーカー情報")
        info_form = QFormLayout(info_grp)
        self._lbl_name = QLabel("—")
        self._lbl_body = QLabel("—")
        info_form.addRow("名前:", self._lbl_name)
        info_form.addRow("ボディ:", self._lbl_body)
        rv.addWidget(info_grp)

        # 座標編集グループ
        pos_grp = QGroupBox("位置 (m)  ローカルフレーム基準")
        pos_form = QFormLayout(pos_grp)

        spin_kw = dict(
            decimals=6, singleStep=0.001, minimum=-10.0, maximum=10.0
        )
        self._spin_x = QDoubleSpinBox(**spin_kw)
        self._spin_y = QDoubleSpinBox(**spin_kw)
        self._spin_z = QDoubleSpinBox(**spin_kw)
        self._spin_x.setPrefix("X = ")
        self._spin_y.setPrefix("Y = ")
        self._spin_z.setPrefix("Z = ")

        for spin in (self._spin_x, self._spin_y, self._spin_z):
            spin.valueChanged.connect(self._on_spinbox_changed)

        pos_form.addRow("X 軸 (右):", self._spin_x)
        pos_form.addRow("Y 軸 (上):", self._spin_y)
        pos_form.addRow("Z 軸 (前):", self._spin_z)
        rv.addWidget(pos_grp)

        # 変更適用ボタン
        self._btn_apply = QPushButton("✅  変更をモデルに適用")
        self._btn_apply.setMinimumHeight(36)
        self._btn_apply.setEnabled(False)
        self._btn_apply.clicked.connect(self._on_apply)
        rv.addWidget(self._btn_apply)

        # 変更ヒント
        self._lbl_hint = QLabel("")
        self._lbl_hint.setWordWrap(True)
        self._lbl_hint.setStyleSheet("color:#F0A050; font-size:11px;")
        rv.addWidget(self._lbl_hint)

        rv.addStretch()

        # モデルパス情報
        path_grp = QGroupBox("ファイルパス")
        path_form = QFormLayout(path_grp)
        self._lbl_model_path  = QLabel("（未読み込み）")
        self._lbl_model_path.setWordWrap(True)
        self._lbl_model_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._lbl_output_path = QLabel("（未設定）")
        self._lbl_output_path.setWordWrap(True)
        self._lbl_output_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path_form.addRow("読込:", self._lbl_model_path)
        path_form.addRow("保存先:", self._lbl_output_path)
        rv.addWidget(path_grp)

        splitter.addWidget(right)
        splitter.setSizes([230, 800, 260])

        # === ステータスバー ===
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("モデルを開いてください（Ctrl+O）")

    # =========================================================================
    # モデル操作
    # =========================================================================

    def _load_model(self, path: str):
        """
        .osim ファイルをロードし、3D シーンを構築する。

        Args:
            path (str): .osim ファイルのパス
        """
        if not OPENSIM_AVAILABLE:
            self._show_warning("OpenSim 未検出", "opensim をインストールしてください。")
            return

        self._status_bar.showMessage("モデルを読み込み中…")
        QApplication.processEvents()

        try:
            # --- OpenSim モデルをロード（インスタンスを self に保持して GC を防ぐ）---
            self._model = osim.Model(path)
            self._model_path = path
            self._dirty = False

            # --- 初期化状態と変換行列を計算 ---
            self._status_bar.showMessage("モデルを初期化中 (initSystem)…")
            QApplication.processEvents()
            self._model_state = _ModelState(self._model)

            # --- デフォルト保存先を推定 ---
            if self._output_path is None:
                self._output_path = self._suggest_output_path(path)

            # --- マーカー World 座標を計算 ---
            marker_positions = self._compute_all_marker_world_positions()

            # --- ジオメトリフォルダを検索 ---
            geom_dir = self._resolve_geometry_dir(path)

            # --- 骨格メッシュをロード ---
            bone_meshes = self._load_bone_meshes(path, geom_dir) if geom_dir else []

            # --- UI を更新 ---
            self._refresh_marker_list()

            # --- 3D シーンを構築 ---
            if PYVISTA_AVAILABLE and bone_meshes or marker_positions:
                self._status_bar.showMessage("3D シーンを構築中…")
                QApplication.processEvents()
                self._viewport.load_scene(bone_meshes, marker_positions)

            self._update_ui_state(loaded=True)
            self._update_title()

            n = self._model.getMarkerSet().getSize()
            geom_msg = f"ジオメトリ: {'あり' if bone_meshes else 'なし（骨未表示）'}"
            self._status_bar.showMessage(
                f"ロード完了: {Path(path).name}  マーカー {n} 件  {geom_msg}"
            )

        except Exception as exc:
            self._model = None
            self._model_state = None
            logger.exception("モデルのロードに失敗")
            self._show_error("モデルのロードに失敗しました", str(exc))
            self._status_bar.showMessage("読み込みエラー")

    def _compute_all_marker_world_positions(self) -> dict[str, np.ndarray]:
        """全マーカーの World 座標を辞書で返す。"""
        result = {}
        if self._model_state is None:
            return result
        ms = self._model.getMarkerSet()
        for i in range(ms.getSize()):
            name = ms.get(i).getName()
            try:
                result[name] = self._model_state.marker_world_position(name)
            except Exception as exc:
                logger.warning("マーカー %s の座標計算失敗: %s", name, exc)
        return result

    def _resolve_geometry_dir(self, model_path: str) -> str | None:
        """
        Geometry フォルダを自動検索する。
        モデルファイルの親ディレクトリから 'Geometry' または
        'Geometryのコピー' フォルダを探す。
        """
        # コマンドライン引数で明示指定された場合
        if self._geometry_dir and Path(self._geometry_dir).is_dir():
            return self._geometry_dir

        base = Path(model_path).parent
        candidates = [
            base / "Geometryのコピー",
            base / "Geometry",
            base.parent / "Geometry",
            base.parent / "Geometryのコピー",
        ]
        for c in candidates:
            if c.is_dir():
                logger.info("Geometry dir: %s", c)
                return str(c)

        logger.warning("Geometry フォルダが見つかりませんでした")
        return None

    def _load_bone_meshes(
        self,
        model_path: str,
        geom_dir: str,
    ) -> list["pv.PolyData"]:
        """
        .osim XML からジオメトリファイルを解析し、
        OpenSim 変換行列を適用した骨格メッシュリストを返す。
        """
        body_geoms = _GeometryParser.parse(model_path)
        meshes = []

        for body_name, vtp_files in body_geoms.items():
            T = self._model_state.body_transforms.get(body_name)
            if T is None:
                continue
            for vtp_file in vtp_files:
                vtp_path = Path(geom_dir) / vtp_file
                if not vtp_path.exists():
                    logger.debug("VTP not found: %s", vtp_path)
                    continue
                try:
                    mesh = pv.read(str(vtp_path))
                    mesh.transform(T, inplace=True)
                    meshes.append(mesh)
                except Exception as exc:
                    logger.warning("VTP 読み込み失敗 %s: %s", vtp_file, exc)

        logger.info("骨格メッシュ: %d 個ロード完了", len(meshes))
        return meshes

    def _refresh_marker_list(self):
        """マーカーリストウィジェットを再構築する。"""
        self._marker_list.clear()
        if self._model is None:
            return

        ms = self._model.getMarkerSet()
        for i in range(ms.getSize()):
            mk = ms.get(i)
            name = mk.getName()
            body = mk.getParentFrameName().split("/")[-1]
            loc = mk.get_location()
            text = (
                f"{name}  [{body}]"
                f"  ({loc.get(0):.3f}, {loc.get(1):.3f}, {loc.get(2):.3f})"
            )
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, name)
            self._marker_list.addItem(item)

        count = ms.getSize()
        self._count_label.setText(f"マーカー: {count} 件")
        self._lbl_model_path.setText(str(self._model_path or ""))
        self._lbl_output_path.setText(str(self._output_path or "（未設定）"))

    def _save_model(self, path: str):
        """モデルを .osim ファイルとして保存する。"""
        if self._model is None:
            return
        try:
            self._model.finalizeConnections()
            self._model.printToXML(path)
            self._dirty = False
            self._output_path = path
            self._lbl_output_path.setText(path)
            self._update_title()
            self._status_bar.showMessage(f"保存完了: {path}")
            logger.info("モデルを保存: %s", path)
        except Exception as exc:
            self._show_error("保存エラー", str(exc))

    @staticmethod
    def _suggest_output_path(input_path: str) -> str:
        """入力パスから保存先パスを推定する（pre_GUI→post_GUI）"""
        p = Path(input_path)
        stem = p.stem
        if "_pre_GUI" in stem:
            new_stem = stem.replace("_pre_GUI", "_post_GUI")
        elif stem.endswith("_scaled"):
            new_stem = stem + "_post_GUI"
        else:
            new_stem = stem + "_edited"
        return str(p.parent / (new_stem + p.suffix))

    # =========================================================================
    # スロット
    # =========================================================================

    def _on_open(self):
        """「モデルを開く」ダイアログ"""
        if not self._confirm_discard():
            return
        start_dir = (
            str(Path(self._model_path).parent)
            if self._model_path
            else str(Path(__file__).resolve().parents[2] / "osim" / "model")
        )
        path, _ = QFileDialog.getOpenFileName(
            self, "OpenSim モデルを開く", start_dir,
            "OpenSim Model Files (*.osim);;All Files (*)"
        )
        if path:
            self._output_path = None
            self._load_model(path)

    def _on_save(self):
        """上書き保存"""
        if self._model is None:
            return
        if self._output_path and Path(self._output_path).parent.exists():
            self._save_model(self._output_path)
        else:
            self._on_save_as()

    def _on_save_as(self):
        """名前を付けて保存"""
        if self._model is None:
            return
        start = (
            self._output_path
            or (str(Path(self._model_path).parent / "output.osim") if self._model_path else "")
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "名前を付けて保存", start,
            "OpenSim Model Files (*.osim);;All Files (*)"
        )
        if path:
            if not path.lower().endswith(".osim"):
                path += ".osim"
            self._save_model(path)

    def _on_list_selection_changed(
        self,
        current: "QListWidgetItem | None",
        _prev,
    ):
        """
        リストで別のマーカーが選択されたとき、
        右パネルのスピンボックスを更新し 3D ビューをハイライトする。
        """
        if current is None or self._model is None:
            self._clear_edit_panel()
            return

        name = current.data(Qt.UserRole)
        self._populate_edit_panel(name)

        if PYVISTA_AVAILABLE:
            self._viewport.select_marker(name)

    def _on_3d_marker_picked(self, name: str):
        """
        3D ビューポートでマーカーがクリックされたとき、
        リストとスピンボックスを同期する。
        """
        if not name:
            return  # 空文字 = 選択解除

        # リストで対応アイテムを選択
        items = self._marker_list.findItems(name, Qt.MatchContains)
        # findItems で不完全一致が出ることがあるので UserRole で確認
        for item in items:
            if item.data(Qt.UserRole) == name:
                self._marker_list.blockSignals(True)
                self._marker_list.setCurrentItem(item)
                self._marker_list.blockSignals(False)
                break

        self._populate_edit_panel(name)
        if PYVISTA_AVAILABLE:
            self._viewport.select_marker(name)

    def _on_spinbox_changed(self, _value):
        """
        スピンボックスの値が変わったとき、デバウンスして 3D プレビューを更新する。
        モデル自体はまだ更新しない。
        """
        if self._block_spinbox:
            return
        # デバウンス：連続入力をまとめて処理
        self._preview_timer.start(self._PREVIEW_DELAY_MS)

    def _on_preview_timeout(self):
        """デバウンス後に 3D マーカー位置をプレビュー更新する。"""
        if not PYVISTA_AVAILABLE or self._model_state is None:
            return
        name = self._get_selected_name()
        if not name:
            return

        x = self._spin_x.value()
        y = self._spin_y.value()
        z = self._spin_z.value()

        # ローカル → World 変換（body 変換行列を使用）
        mk = self._model.getMarkerSet().get(name)
        body_name = mk.getParentFrameName().split("/")[-1]
        T = self._model_state.body_transforms.get(body_name, np.eye(4))
        local = np.array([x, y, z, 1.0])
        world = (T @ local)[:3]

        self._viewport.update_marker_position(name, world)
        self._lbl_hint.setText(
            f"プレビュー中  ({x:.4f}, {y:.4f}, {z:.4f})\n"
            "「変更をモデルに適用」で確定します"
        )

    def _on_apply(self):
        """
        「変更をモデルに適用」ボタン押下時に、スピンボックスの値を
        OpenSim モデルに書き込み 3D ビューと一覧を更新する。
        """
        name = self._get_selected_name()
        if name is None or self._model is None:
            return

        x = self._spin_x.value()
        y = self._spin_y.value()
        z = self._spin_z.value()

        try:
            mk = self._model.getMarkerSet().get(name)
            mk.set_location(osim.Vec3(x, y, z))
            self._model.finalizeConnections()
            self._dirty = True
        except Exception as exc:
            self._show_error("マーカー更新エラー", str(exc))
            return

        # World 座標を再計算して 3D を更新
        if PYVISTA_AVAILABLE and self._model_state:
            world = self._model_state.marker_world_position(name)
            self._viewport.update_marker_position(name, world)

        # リストを更新（座標が変わったので表示テキストを更新）
        self._refresh_marker_list()
        # 同じマーカーを再選択
        for i in range(self._marker_list.count()):
            item = self._marker_list.item(i)
            if item.data(Qt.UserRole) == name:
                self._marker_list.blockSignals(True)
                self._marker_list.setCurrentItem(item)
                self._marker_list.blockSignals(False)
                break

        self._lbl_hint.setText(
            f"✅ {name} を更新しました\n"
            f"({x:.6f}, {y:.6f}, {z:.6f})  ※ 未保存"
        )
        self._update_title()
        self._status_bar.showMessage(
            f"{name} → ({x:.4f}, {y:.4f}, {z:.4f})  ※ 未保存"
        )

    # =========================================================================
    # 3D ドラッグ移動スロット
    # =========================================================================

    def _world_to_local(self, marker_name: str, world_pos: np.ndarray) -> np.ndarray:
        """
        World 座標 → マーカー親ボディのローカル座標系に変換する。

        Body transform T_body の逆行列を使用:
            local_h = T_body^{-1} @ [wx, wy, wz, 1]
        """
        if self._model is None or self._model_state is None:
            return world_pos.copy()
        try:
            mk = self._model.getMarkerSet().get(marker_name)
            body_name = mk.getParentFrameName().split("/")[-1]
        except Exception:
            return world_pos.copy()
        T = self._model_state.body_transforms.get(body_name, np.eye(4))
        T_inv = np.linalg.inv(T)
        local_h = T_inv @ np.append(world_pos, 1.0)
        return local_h[:3]

    def _on_marker_dragged(self, name: str, wx: float, wy: float, wz: float):
        """
        3D ビューポートでマーカーをドラッグ中に呼ばれる。
        World 座標 → ローカル座標に変換してスピンボックスをリアルタイム更新。
        """
        local = self._world_to_local(name, np.array([wx, wy, wz]))
        self._block_spinbox = True
        self._spin_x.setValue(float(local[0]))
        self._spin_y.setValue(float(local[1]))
        self._spin_z.setValue(float(local[2]))
        self._block_spinbox = False

    def _on_marker_drag_ended(self, name: str, wx: float, wy: float, wz: float):
        """
        3D ビューポートでのドラッグ終了時に呼ばれる。
        最終 World 座標をローカル座標に変換し、OpenSim モデルに書き込む。
        """
        if self._model is None:
            return
        local = self._world_to_local(name, np.array([wx, wy, wz]))
        x, y, z = float(local[0]), float(local[1]), float(local[2])

        try:
            mk = self._model.getMarkerSet().get(name)
            mk.set_location(osim.Vec3(x, y, z))
            self._model.finalizeConnections()
            self._dirty = True
        except Exception as exc:
            self._show_error("ドラッグ適用エラー", str(exc))
            return

        # 3D ビュー: モデル確定後の World 座標でアクタを更新
        if PYVISTA_AVAILABLE and self._model_state:
            world = self._model_state.marker_world_position(name)
            self._viewport.update_marker_position(name, world)

        # スピンボックスを確定値で更新
        self._block_spinbox = True
        self._spin_x.setValue(x)
        self._spin_y.setValue(y)
        self._spin_z.setValue(z)
        self._block_spinbox = False

        # リスト表示を更新して同じマーカーを再選択
        self._refresh_marker_list()
        for i in range(self._marker_list.count()):
            item = self._marker_list.item(i)
            if item.data(Qt.UserRole) == name:
                self._marker_list.blockSignals(True)
                self._marker_list.setCurrentItem(item)
                self._marker_list.blockSignals(False)
                break

        self._update_title()
        self._status_bar.showMessage(
            f"{name} ドラッグ完了 → ({x:.4f}, {y:.4f}, {z:.4f})  ※ 未保存"
        )
        self._lbl_hint.setText(
            f"🖱 {name} をドラッグ移動\n({x:.6f}, {y:.6f}, {z:.6f})  ※ 未保存"
        )

    def _on_search(self, text: str):
        """マーカーリストを部分一致で絞り込む。"""
        for i in range(self._marker_list.count()):
            item = self._marker_list.item(i)
            item.setHidden(text.lower() not in item.text().lower())

    # =========================================================================
    # UI ヘルパー
    # =========================================================================

    def _populate_edit_panel(self, name: str):
        """指定マーカーの情報を右パネルに反映する。"""
        if self._model is None:
            return
        try:
            mk = self._model.getMarkerSet().get(name)
            loc = mk.get_location()
            body = mk.getParentFrameName().split("/")[-1]
        except Exception:
            return

        self._lbl_name.setText(name)
        self._lbl_body.setText(body)

        self._block_spinbox = True
        self._spin_x.setValue(loc.get(0))
        self._spin_y.setValue(loc.get(1))
        self._spin_z.setValue(loc.get(2))
        self._block_spinbox = False

        self._btn_apply.setEnabled(True)
        self._lbl_hint.setText("")

    def _clear_edit_panel(self):
        """右パネルをクリアする。"""
        self._lbl_name.setText("—")
        self._lbl_body.setText("—")
        self._block_spinbox = True
        for sp in (self._spin_x, self._spin_y, self._spin_z):
            sp.setValue(0.0)
        self._block_spinbox = False
        self._btn_apply.setEnabled(False)
        self._lbl_hint.setText("")

    def _get_selected_name(self) -> str | None:
        """現在選択中のマーカー名を返す。"""
        item = self._marker_list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _update_ui_state(self, loaded: bool):
        """ボタンの有効/無効を切り替える。"""
        self._btn_save_as.setEnabled(loaded)

    def _update_title(self):
        """ウィンドウタイトルを更新する。"""
        if self._model_path:
            name = Path(self._model_path).name
            dirty = " ●" if self._dirty else ""
            self.setWindowTitle(f"{self.APP_TITLE} — {name}{dirty}")
        else:
            self.setWindowTitle(self.APP_TITLE)

    def _confirm_discard(self) -> bool:
        """未保存変更がある場合に確認ダイアログを出す。"""
        if not self._dirty:
            return True
        reply = QMessageBox.question(
            self, "未保存の変更",
            "未保存の変更があります。変更を破棄して続けますか?",
            QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        return reply == QMessageBox.Discard

    def _show_error(self, title: str, msg: str):
        QMessageBox.critical(self, title, msg)
        logger.error("%s: %s", title, msg)

    def _show_warning(self, title: str, msg: str):
        QMessageBox.warning(self, title, msg)
        logger.warning("%s: %s", title, msg)

    # =========================================================================
    # ウィンドウ終了時の処理
    # =========================================================================
    def closeEvent(self, event):
        """終了時の処理。"""
        if not self._confirm_discard():
            event.ignore()
            return
        event.accept()


# =============================================================================
# CLI エントリポイント
# =============================================================================
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """コマンドライン引数をパースする。"""
    parser = argparse.ArgumentParser(
        prog="marker-editor",
        description="OpenSim マーカーエディタ — PyQt5 + PyVista インタラクティブ GUI",
    )
    parser.add_argument("--model",    "-m", metavar="MODEL.osim",
                        help="起動時に開く .osim ファイルのパス")
    parser.add_argument("--output",   "-o", metavar="OUTPUT.osim",
                        help="デフォルトの保存先 .osim ファイルパス")
    parser.add_argument("--geometry", "-g", metavar="GEOMETRY_DIR",
                        help="VTP ファイルが入った Geometry フォルダのパス")
    parser.add_argument("--verbose",  "-v", action="store_true",
                        help="デバッグログを有効にする")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """
    アプリケーションのメインエントリポイント。

    Returns:
        int: 終了コード（0 = 正常終了）
    """
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("OpenSim Marker Editor")
    app.setOrganizationName("harmo-semg")

    window = MarkerEditorWindow(
        initial_model_path=getattr(args, "model", None),
        initial_output_path=getattr(args, "output", None),
        geometry_dir=getattr(args, "geometry", None),
    )
    window.show()

    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
