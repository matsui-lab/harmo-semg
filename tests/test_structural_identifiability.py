from pathlib import Path

from harmo.structural_identifiability import (
    assess_structural_identifiability,
    evaluate_structural_identifiability,
    model_inventory,
)


def _requirements():
    return {
        "target_window_s": [20.355, 21.015],
        "minimum_observed_landmark_groups": 6,
        "minimum_articulated_body_groups": 6,
        "minimum_observed_coordinate_groups": 8,
        "required_paired_model_roles": [
            "static_derived_model",
            "articulated_full_body_model",
            "arm_lumped_matched_model",
        ],
        "upper_limb_marker_aliases": {
            "left_shoulder": ["LSHO"],
            "left_elbow": ["LELB"],
            "left_wrist": ["LWRA"],
            "right_shoulder": ["RSHO"],
            "right_elbow": ["RELB"],
            "right_wrist": ["RWRA"],
        },
        "articulated_body_aliases": {
            "left_upper_arm": ["humerus_l"],
            "left_forearm": ["ulna_l"],
            "left_hand": ["hand_l"],
            "right_upper_arm": ["humerus_r"],
            "right_forearm": ["ulna_r"],
            "right_hand": ["hand_r"],
        },
        "upper_limb_coordinate_aliases": {
            "left_shoulder_flexion": ["arm_flex_l"],
            "left_shoulder_adduction": ["arm_add_l"],
            "left_shoulder_rotation": ["arm_rot_l"],
            "left_elbow_flexion": ["elbow_flex_l"],
            "right_shoulder_flexion": ["arm_flex_r"],
            "right_shoulder_adduction": ["arm_add_r"],
            "right_shoulder_rotation": ["arm_rot_r"],
            "right_elbow_flexion": ["elbow_flex_r"],
        },
    }


def _source(observed=True):
    markers = (
        ["LSHO", "LELB", "LWRA", "RSHO", "RELB", "RWRA"]
        if observed
        else ["LASIS", "RASIS", "C7", "T10"]
    )
    bodies = (
        [
            "pelvis",
            "torso",
            "humerus_l",
            "ulna_l",
            "hand_l",
            "humerus_r",
            "ulna_r",
            "hand_r",
        ]
        if observed
        else ["pelvis", "torso", "femur_l", "femur_r"]
    )
    coordinates = (
        [
            "arm_flex_l",
            "arm_add_l",
            "arm_rot_l",
            "elbow_flex_l",
            "arm_flex_r",
            "arm_add_r",
            "arm_rot_r",
            "elbow_flex_r",
        ]
        if observed
        else ["pelvis_tilt", "hip_flexion_l", "hip_flexion_r"]
    )
    roles = ["static_derived_model"]
    if observed:
        roles += ["articulated_full_body_model", "arm_lumped_matched_model"]
    return {
        "source_bundle_verified": True,
        "same_subject_and_activity_sources": True,
        "subject_specific_upper_limb_scale_lineage": observed,
        "paired_model_lower_body_equivalence": observed,
        "paired_model_mass_com_inertia_equivalence": observed,
        "available_source_roles": roles,
        "trc_marker_labels": markers,
        "c3d_marker_labels": markers,
        "ik_columns": coordinates,
        "ground_reaction_time_range_s": [0.0, 30.0],
        "model": {"body_count": len(bodies), "bodies": bodies},
    }


def test_identifiability_rejects_same_subject_data_without_upper_limbs():
    observed = assess_structural_identifiability(
        _source(observed=False), requirements=_requirements()
    )
    assert observed["source_ready"] is True
    assert observed["observational_identifiability"] is False
    assert observed["paired_model_ready"] is False
    assert observed["full_body_rra_authorized"] is False
    assert observed["counts"]["trc_upper_limb_landmark_groups_observed"] == 0
    assert observed["counts"]["articulated_upper_limb_body_groups_present"] == 0
    assert observed["counts"]["upper_limb_coordinate_groups_observed"] == 0
    assert observed["classification"] == (
        "subject-activity-matched-upper-limb-effect-unidentifiable"
    )


def test_identifiability_authorizes_only_complete_observation_and_model_pair():
    observed = assess_structural_identifiability(
        _source(observed=True), requirements=_requirements()
    )
    assert all(observed["checks"].values())
    assert observed["observational_identifiability"] is True
    assert observed["paired_model_ready"] is True
    assert observed["full_body_rra_authorized"] is True
    assert observed["causal_structural_attribution"] is False


def test_model_inventory_reads_nested_coordinates_and_mass(tmp_path: Path):
    model = tmp_path / "model.osim"
    model.write_text(
        """\
<OpenSimDocument>
  <Model>
    <BodySet><objects>
      <Body name="torso"><mass>40</mass></Body>
      <Body name="humerus_l"><mass>2</mass></Body>
    </objects></BodySet>
    <JointSet><objects><CustomJoint name="shoulder">
      <coordinates>
        <Coordinate name="arm_flex_l"><default_value>0</default_value></Coordinate>
      </coordinates>
    </CustomJoint></objects></JointSet>
    <MarkerSet><objects><Marker name="LSHO"/></objects></MarkerSet>
  </Model>
</OpenSimDocument>
""",
        encoding="utf-8",
    )
    observed = model_inventory(model)
    assert observed["bodies"] == ["humerus_l", "torso"]
    assert observed["coordinates"] == ["arm_flex_l"]
    assert observed["markers"] == ["LSHO"]
    assert observed["total_mass_kg"] == 42.0


def test_evaluator_retains_completed_unidentifiability_as_failure():
    plan = {
        "input_sha256": {"markers": "a" * 64},
        "prior_result_sha256": {"s6b": "b" * 64},
        "claim_boundary": "preflight only",
        "prohibited_substitutions": ["generic-arms"],
    }
    worker = {
        "scope": "g6c-s6c-structural-identifiability-worker",
        "input_sha256": plan["input_sha256"],
        "prior_result_sha256": plan["prior_result_sha256"],
        "analysis_complete": True,
        "artifacts_complete": True,
        "diagnosis": {"full_body_rra_authorized": False},
    }
    observed = evaluate_structural_identifiability(plan, worker)
    assert observed["diagnostic_complete"] is True
    assert observed["pass"] is False
    assert observed["full_body_rra_authorized"] is False
    assert observed["causal_next_gate"] == (
        "blocked-requires-upper-limb-observed-subject-activity-data"
    )
    assert observed["diagnostic_next_gate"] == (
        "g6c-s6d-preregistered-bounded-arm-kinematics-sensitivity"
    )
