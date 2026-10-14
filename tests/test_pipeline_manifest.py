from contextlib import ExitStack
import sys
import types
from unittest.mock import Mock, patch

import pytest

from harmo.ingest.pipeline import run_workflow


def _active_config():
    return {"dataset_status": "active", "warehouse_path": "/tmp/warehouse"}


def test_successful_ingest_fails_if_manifest_cannot_be_written():
    spark = Mock()
    osim_io = types.ModuleType("harmo.model.osim_io")
    osim_io.main = Mock()

    with ExitStack() as stack:
        stack.enter_context(
            patch("harmo.ingest.pipeline.load_config", return_value=_active_config())
        )
        stack.enter_context(
            patch(
                "harmo.ingest.pipeline.get_warehouse_path",
                return_value="/tmp/warehouse",
            )
        )
        stack.enter_context(
            patch("harmo.ingest.pipeline.get_spark_session", return_value=spark)
        )
        stack.enter_context(
            patch("harmo.ingest.pipeline.validate_config", return_value=[])
        )
        stack.enter_context(
            patch("harmo.ingest.pipeline.validate_study", return_value=[])
        )
        stack.enter_context(patch("harmo.ingest.pipeline.data_converter"))
        stack.enter_context(patch.dict(sys.modules, {"harmo.model.osim_io": osim_io}))
        stack.enter_context(
            patch(
                "harmo.evidence.run.capture_run_manifest",
                side_effect=OSError("read-only evidence directory"),
            )
        )
        with pytest.raises(RuntimeError, match="evidence manifest generation failed"):
            run_workflow("study/config.yaml")

    spark.stop.assert_called_once_with()


def test_manifest_failure_does_not_mask_ingest_failure():
    spark = Mock()

    with ExitStack() as stack:
        stack.enter_context(
            patch("harmo.ingest.pipeline.load_config", return_value=_active_config())
        )
        stack.enter_context(
            patch(
                "harmo.ingest.pipeline.get_warehouse_path",
                return_value="/tmp/warehouse",
            )
        )
        stack.enter_context(
            patch("harmo.ingest.pipeline.get_spark_session", return_value=spark)
        )
        stack.enter_context(
            patch("harmo.ingest.pipeline.validate_config", return_value=[])
        )
        stack.enter_context(
            patch("harmo.ingest.pipeline.validate_study", return_value=[])
        )
        stack.enter_context(
            patch(
                "harmo.ingest.pipeline.data_converter",
                side_effect=ValueError("invalid source data"),
            )
        )
        stack.enter_context(
            patch(
                "harmo.evidence.run.capture_run_manifest",
                side_effect=OSError("read-only evidence directory"),
            )
        )
        with pytest.raises(ValueError, match="invalid source data"):
            run_workflow("study/config.yaml")

    spark.stop.assert_called_once_with()


def test_discovered_sources_are_forwarded_to_run_manifest():
    spark = Mock()
    osim_io = types.ModuleType("harmo.model.osim_io")
    osim_io.main = Mock()
    digest = "a" * 64

    def discover_sources(_spark, _config_path, source_digests):
        source_digests["/data/raw.c3d"] = digest

    with ExitStack() as stack:
        stack.enter_context(
            patch("harmo.ingest.pipeline.load_config", return_value=_active_config())
        )
        stack.enter_context(
            patch(
                "harmo.ingest.pipeline.get_warehouse_path",
                return_value="/tmp/warehouse",
            )
        )
        stack.enter_context(
            patch("harmo.ingest.pipeline.get_spark_session", return_value=spark)
        )
        stack.enter_context(
            patch("harmo.ingest.pipeline.validate_config", return_value=[])
        )
        stack.enter_context(
            patch("harmo.ingest.pipeline.validate_study", return_value=[])
        )
        stack.enter_context(
            patch(
                "harmo.ingest.pipeline.data_converter",
                side_effect=discover_sources,
            )
        )
        stack.enter_context(patch.dict(sys.modules, {"harmo.model.osim_io": osim_io}))
        capture = stack.enter_context(
            patch(
                "harmo.evidence.run.capture_run_manifest",
                return_value="/tmp/run.manifest.json",
            )
        )
        run_workflow("study/config.yaml")

    assert capture.call_args.kwargs["source_digests"] == {"/data/raw.c3d": digest}


def test_invalid_config_is_rejected_before_spark_starts():
    with ExitStack() as stack:
        stack.enter_context(
            patch("harmo.ingest.pipeline.load_config", return_value=_active_config())
        )
        stack.enter_context(
            patch(
                "harmo.ingest.pipeline.validate_config",
                return_value=["file_mappings is required"],
            )
        )
        stack.enter_context(
            patch("harmo.ingest.pipeline.validate_study", return_value=[])
        )
        get_spark = stack.enter_context(
            patch("harmo.ingest.pipeline.get_spark_session")
        )
        with pytest.raises(ValueError, match="dataset config validation failed"):
            run_workflow("study/config.yaml")

    get_spark.assert_not_called()
