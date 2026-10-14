from __future__ import annotations

from typing import Tuple

Vec3 = Tuple[float, float, float]


def vec3_add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vec3_subtract(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def world_to_local_stub(world: Vec3, offset: Vec3 = (0.0, 0.0, 0.0)) -> Vec3:
    """Convert world coordinates to local by subtracting the frame offset."""
    return vec3_subtract(world, offset)


def local_to_world_stub(local: Vec3, offset: Vec3 = (0.0, 0.0, 0.0)) -> Vec3:
    """Convert local coordinates to world by adding the frame offset."""
    return vec3_add(local, offset)
