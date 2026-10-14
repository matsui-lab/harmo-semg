from pathlib import Path

import yaml

from harmo.evidence.anatomy_crosswalk import verify_anatomy_crosswalk
from harmo.evidence.io import sha256_file
from harmo.semantic_coverage import (
    audit_active_mapping_semantics,
    build_semantic_audit_frames,
)
from harmo.warehouse.schema import (
    ANATOMY_CROSSWALK,
    ONTOLOGY_RELEASE,
    SEMANTIC_COVERAGE_AUDIT,
)


ROOT = Path(__file__).resolve().parents[1]
CROSSWALK = (
    ROOT
    / "vocabularies/anatomy/harmo-muscle-to-uberon-2026-06.yaml"
)


def _verified_fixture(tmp_path):
    value = yaml.safe_load(CROSSWALK.read_text(encoding="utf-8"))
    data_version = "test/releases/1/anatomy.owl"
    lines = ["format-version: 1.2", "data-version: " + data_version, ""]
    for entry in value["entries"]:
        lines.extend(
            [
                "[Term]",
                "id: " + entry["target_term_id"],
                "name: " + entry["target_label"],
            ]
        )
        if entry["match_basis"] == "exact-synonym":
            lines.append(
                'synonym: "{}" EXACT []'.format(entry["matched_text"])
            )
        lines.append("")
    obo = tmp_path / "anatomy.obo"
    obo.write_text("\n".join(lines), encoding="utf-8")
    value["target_release"].update(
        {
            "release_tag": "test-v1",
            "version_iri": "https://example.test/releases/1/anatomy.owl",
            "data_version": data_version,
            "asset_url": "https://example.test/anatomy.obo",
            "asset_sha256": sha256_file(str(obo)),
            "asset_size_bytes": obo.stat().st_size,
        }
    )
    crosswalk = tmp_path / "crosswalk.yaml"
    crosswalk.write_text(
        yaml.safe_dump(value, sort_keys=False), encoding="utf-8"
    )
    return verify_anatomy_crosswalk(str(crosswalk), str(obo))


def test_committed_crosswalk_covers_declared_project_muscles():
    crosswalk = yaml.safe_load(CROSSWALK.read_text(encoding="utf-8"))
    source_ids = {entry["source_term_id"] for entry in crosswalk["entries"]}
    configured = set()
    excluded = set()
    for channel_path in sorted(ROOT.glob("studies/*/definitions/channels.yaml")):
        value = yaml.safe_load(channel_path.read_text(encoding="utf-8")) or {}
        for row in value.get("channels") or []:
            muscle = str(row.get("muscle", "")).strip()
            if not muscle:
                continue
            if "ignore_channel" in muscle:
                excluded.add(muscle)
            else:
                configured.add("HARMOMUSCLE:{}".format(muscle))

    assert configured <= source_ids
    assert excluded == {"soleus(ignore_channel)"}
    assert len(source_ids) == 16


def test_semantic_audit_has_fixed_denominators(tmp_path):
    verified = _verified_fixture(tmp_path)
    audit = audit_active_mapping_semantics(
        str(ROOT),
        str(ROOT / "paper/validation_registry.yaml"),
        str(ROOT / "paper/results/validation/mapping_outcomes.json"),
        verified,
    )

    assert audit["summary"]["mapping_denominator"] == 78
    assert audit["summary"]["emg_config_ready_for_recording_materialization"] == 6
    assert len(audit["mapping_rows"]) == 78
    assert audit["summary"]["unique_emg_channel_definitions"] == 133
    assert audit["summary"]["mapped_emg_channel_definitions"] == 68
    assert audit["summary"]["explicitly_excluded_emg_channel_definitions"] == 1
    assert audit["summary"]["unresolved_emg_channel_definitions"] == 64
    assert audit["study_anatomy"]["study_32"]["status"] == "unresolved"
    assert all(
        row["recording_bundle_status"] == "not-materialized-by-this-audit"
        for row in audit["mapping_rows"]
    )

    frames = build_semantic_audit_frames(audit, verified)
    assert set(frames) == {
        ONTOLOGY_RELEASE,
        ANATOMY_CROSSWALK,
        SEMANTIC_COVERAGE_AUDIT,
    }
    assert len(frames[ONTOLOGY_RELEASE]) == 1
    assert len(frames[ANATOMY_CROSSWALK]) == 16
    assert len(frames[SEMANTIC_COVERAGE_AUDIT]) == 78


def test_frozen_official_semantic_audit_retains_claim_boundary():
    import json

    audit = json.loads(
        (
            ROOT
            / "paper/results/validation/active_mapping_semantic_coverage.json"
        ).read_text(encoding="utf-8")
    )
    crosswalk = json.loads(
        (
            ROOT
            / "paper/results/validation/anatomy_crosswalk_validation.json"
        ).read_text(encoding="utf-8")
    )

    assert crosswalk["pass"] is True
    assert crosswalk["code_dirty"] is False
    assert crosswalk["verified_exact_matches"] == 16
    assert audit["implementation_commit"] == (
        "feb90aafe07fd0c3d9403abb068d75a9c2690ddc"
    )
    assert audit["code_dirty"] is False
    assert audit["summary"]["mapping_denominator"] == 78
    assert audit["summary"]["config_ready_for_recording_materialization"] == 53
    assert audit["summary"]["emg_config_ready_for_recording_materialization"] == 6
    assert all(
        row["recording_bundle_status"] == "not-materialized-by-this-audit"
        for row in audit["mapping_rows"]
    )
