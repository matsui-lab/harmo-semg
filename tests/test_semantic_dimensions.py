import json
import subprocess
import sys

import pytest

from harmo.evidence.io import write_json_atomic
from harmo.evidence.models import (
    AnatomicalEntity,
    OntologyTerm,
    SemanticMapping,
    TaskDescriptor,
)
from harmo.ingest.semantic_dimensions import build_semantic_dimension_frames
from harmo.warehouse.schema import (
    CHANNEL_ANATOMY,
    DIM_ANATOMY,
    DIM_TASK,
    SESSION_TASK,
)
from test_evidence_contracts import bundle


def _mapping(identifier):
    return SemanticMapping(
        "curated-source-dictionary",
        "test-mapping/1.0.0",
        "mapped",
        ["test:mapping:sha256:{}".format(identifier * 64)],
        1.0,
    )


def _bundle():
    value = bundle()
    value.schema_version = "1.2.0"
    value.task = TaskDescriptor(
        "walking",
        "LevelWalking",
        [OntologyTerm("HED", "8.4.0", "Walk", "Walk")],
        {
            "terrain": "level",
            "direction": "forward",
            "speed": "self-selected",
        },
        _mapping("1"),
        "walking/level/forward",
    )
    anatomy = (
        (
            "tibialis-anterior",
            "HARMOMUSCLE:tibialis_anterior",
        ),
        (
            "gastrocnemius-medialis",
            "HARMOMUSCLE:gastrocnemius_medialis",
        ),
    )
    for index, (label, term_id) in enumerate(anatomy):
        value.channels[index].anatomy = AnatomicalEntity(
            "muscle",
            label,
            OntologyTerm(
                "HarmoAnatomy",
                "1.0.0",
                term_id,
                label.replace("-", " ") + " muscle",
            ),
            "left",
            value.channels[index].name,
            _mapping(str(index + 2)),
        )
    value.__post_init__()
    return value


def test_semantic_frames_are_complete_and_checksum_bound():
    value = _bundle()

    frames = build_semantic_dimension_frames(value)

    assert set(frames) == {
        DIM_TASK,
        SESSION_TASK,
        DIM_ANATOMY,
        CHANNEL_ANATOMY,
    }
    assert len(frames[DIM_TASK]) == 1
    assert len(frames[SESSION_TASK]) == 1
    assert len(frames[DIM_ANATOMY]) == 2
    assert len(frames[CHANNEL_ANATOMY]) == 2
    assert frames[SESSION_TASK].iloc[0]["recording_sha256"]
    assert set(frames[CHANNEL_ANATOMY]["mapping_status"]) == {"mapped"}


def test_dimension_identity_ignores_source_alias_but_retains_it():
    left = _bundle()
    right = _bundle()
    right.task.source_label = "Walking"
    right.channels[0].anatomy.source_label = "TibialisAnterior"

    left_frames = build_semantic_dimension_frames(left)
    right_frames = build_semantic_dimension_frames(right)

    assert (
        left_frames[DIM_TASK].iloc[0]["task_id"]
        == right_frames[DIM_TASK].iloc[0]["task_id"]
    )
    assert set(left_frames[DIM_ANATOMY]["anatomy_id"]) == set(
        right_frames[DIM_ANATOMY]["anatomy_id"]
    )
    assert right_frames[SESSION_TASK].iloc[0]["source_label"] == "Walking"
    assert (
        right_frames[CHANNEL_ANATOMY].iloc[0]["source_label"]
        == "TibialisAnterior"
    )


def test_task_context_changes_semantic_identity():
    level = _bundle()
    stairs = _bundle()
    stairs.task.facets["terrain"] = "stairs"
    stairs.task.compatibility_group = "walking/stairs/upward"

    level_id = build_semantic_dimension_frames(level)[DIM_TASK].iloc[0][
        "task_id"
    ]
    stair_id = build_semantic_dimension_frames(stairs)[DIM_TASK].iloc[0][
        "task_id"
    ]

    assert level_id != stair_id


def test_missing_emg_anatomy_stops_materialization():
    value = _bundle()
    value.channels[0].anatomy = None

    with pytest.raises(ValueError, match="lack first-class anatomy"):
        build_semantic_dimension_frames(value)


def test_semantic_dimension_cli_dry_run(tmp_path):
    path = write_json_atomic(
        str(tmp_path / "recording.bundle.json"), _bundle()
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "harmo.cli",
            "ingest-bundle-semantics",
            "--bundle",
            path,
            "--dry-run",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    summary = json.loads(result.stdout)
    assert summary["mode"] == "dry-run"
    assert summary["tables"][DIM_TASK]["rows"] == 1
    assert summary["tables"][CHANNEL_ANATOMY]["rows"] == 2
