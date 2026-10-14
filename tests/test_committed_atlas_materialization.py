import json
from pathlib import Path

import duckdb
import pytest

from harmo.evidence.io import read_json, sha256_contract, sha256_file
from harmo.evidence.models import (
    ComparabilityAssertion,
    EvidenceManifest,
    HarmoRecordingBundle,
    ReleaseDecision,
    TrustReport,
)


jsonschema = pytest.importorskip("jsonschema")
ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "paper/results/atlas_materialization"


def _load(relative):
    return read_json(str(RESULT / relative))


def test_committed_materialization_index_and_denominators():
    index = _load("atlas.materialization.index.json")
    schema = json.loads(
        (
            ROOT / "schemas/atlas-materialization-index-1.0.0.json"
        ).read_text(encoding="utf-8")
    )

    jsonschema.Draft202012Validator(schema).validate(index)

    assert index["implementation_commit"] == (
        "9156fbc7b83ce6361dd4f351a544adc36f1cc6fd"
    )
    assert index["code_dirty"] is False
    assert index["summary"] == {
        "conditional_comparisons": 6,
        "conditional_releases": 12,
        "exchange_exports": 12,
        "excluded_source_channels": 17,
        "frozen_first_digest_passed": 6,
        "mapping_count": 6,
        "pass": True,
        "qualified_channels": 143,
        "recording_count": 12,
        "source_channels": 160,
    }
    assert len(index["source_assets"]) == 3
    assert all(asset["pass"] for asset in index["source_assets"])
    assert {
        asset["verification_mode"] for asset in index["source_assets"]
    } == {"frozen-complete-archive+current-member-crc-sha256"}
    assert {asset["license"] for asset in index["source_assets"]} == {
        "CC0 1.0",
        "CC BY 4.0",
    }
    assert all(asset["attribution"] for asset in index["source_assets"])


def test_committed_r_m_t_c_d_hash_chain_and_exchange_packages():
    index = _load("atlas.materialization.index.json")
    for mapping in index["mappings"]:
        comparison_data = _load(mapping["comparison_path"])
        comparison = ComparabilityAssertion.from_dict(comparison_data)
        assert comparison.status.value == "conditionally_comparable"
        assert sha256_contract(comparison) == mapping["comparison_sha256"]
        decisions = {
            item["subject_id"]: ReleaseDecision.from_dict(item)
            for item in mapping["release_decisions"]
        }
        for record in mapping["records"]:
            bundle = HarmoRecordingBundle.from_dict(
                _load(record["recording_path"])
            )
            manifest = EvidenceManifest.from_dict(
                _load(record["manifest_path"])
            )
            trust = TrustReport.from_dict(_load(record["trust_path"]))
            decision = decisions[bundle.recording_id]

            assert sha256_contract(bundle) == record["recording_sha256"]
            assert sha256_contract(manifest) == record["manifest_sha256"]
            assert sha256_contract(trust) == record["trust_sha256"]
            assert manifest.code_commit == index["implementation_commit"]
            assert manifest.code_dirty is False
            assert trust.subject_id == bundle.recording_id
            assert set(record["trust_dimensions"].values()) == {"pass"}
            assert decision.status.value == "conditional"
            assert decision.export_permitted is True
            assert decision.recording_sha256 == sha256_contract(bundle)
            assert decision.manifest_sha256 == sha256_contract(manifest)
            assert decision.trust_report_sha256 == sha256_contract(trust)
            assert (
                decision.comparability_assertion_sha256
                == sha256_contract(comparison)
            )
            input_artifacts = {
                artifact.artifact_id: artifact for artifact in manifest.inputs
            }
            assert input_artifacts[
                "source-release-rights"
            ].sha256 == sha256_file(str(ROOT / "paper/source_releases.yaml"))
            assert any(
                evidence.rule_id == "source.rights.redistribution"
                and evidence.status.value == "pass"
                for evidence in trust.evidence
            )

            output_artifacts = {
                artifact.artifact_id: artifact for artifact in manifest.outputs
            }
            record_dir = (RESULT / record["recording_path"]).parent
            assert output_artifacts["normalized-samples"].sha256 == sha256_file(
                str(record_dir / "normalized.samples.csv")
            )
            assert output_artifacts[
                "normalized-timestamps"
            ].sha256 == sha256_file(
                str(record_dir / "normalized.timestamps.csv")
            )
            assert (
                output_artifacts["recording-bundle"].sha256
                == sha256_contract(bundle)
            )
            assert (
                output_artifacts["trust-report"].sha256
                == sha256_contract(trust)
            )

            exchange = _load(
                "exchange/{}/{}/atlas.exchange.json".format(
                    mapping["mapping_id"], record["role"]
                )
            )
            exchange_schema = json.loads(
                (ROOT / "schemas/exchange-2.0.0.json").read_text(
                    encoding="utf-8"
                )
            )
            jsonschema.Draft202012Validator(exchange_schema).validate(exchange)
            assert exchange["recording_id"] == bundle.recording_id
            assert exchange["release_status"] == "conditional"
            exchange_dir = (
                RESULT
                / "exchange"
                / mapping["mapping_id"]
                / record["role"]
            )
            for artifact_name, file_name in exchange["files"].items():
                assert exchange["digests"][artifact_name] == sha256_contract(
                    read_json(str(exchange_dir / file_name))
                )


def test_committed_parquet_tables_are_queryable():
    index = _load("atlas.materialization.index.json")
    tables = RESULT / "tables"
    connection = duckdb.connect()
    try:
        counts = {
            name: connection.execute(
                "SELECT count(*) FROM read_parquet(?)",
                [str(tables / "{}.parquet".format(name))],
            ).fetchone()[0]
            for name in (
                "atlas_record",
                "atlas_channel",
                "atlas_task",
                "atlas_comparability",
                "atlas_release",
            )
        }
        walking_release_count = connection.execute(
            """
            SELECT count(*)
            FROM read_parquet(?) AS r
            JOIN read_parquet(?) AS t USING (recording_id, mapping_id)
            WHERE r.release_status = 'conditional'
              AND t.activity = 'walking'
            """,
            [
                str(tables / "atlas_record.parquet"),
                str(tables / "atlas_task.parquet"),
            ],
        ).fetchone()[0]
        anatomy_count = connection.execute(
            """
            SELECT count(DISTINCT term_id)
            FROM read_parquet(?)
            WHERE ontology = 'UBERON'
            """,
            [str(tables / "atlas_channel.parquet")],
        ).fetchone()[0]
    finally:
        connection.close()

    assert counts == {
        "atlas_record": 12,
        "atlas_channel": 143,
        "atlas_task": 12,
        "atlas_comparability": 6,
        "atlas_release": 12,
    }
    assert walking_release_count == 12
    assert anatomy_count == index["duckdb_queries"]["uberon_term_count"] == 12
