from harmo.purpose_requirements import summarize


def test_unassessed_still_exposes_processing_and_normalization_conditions():
    decision = dict(assertion={"status":"unassessed"},assertion_sha256="a"*64,check_groups=[
        dict(check_id="purpose:activation-timing",execution="executed",findings=[
            dict(kind="condition",reason="establish a validated processing compatibility group for activation timing")]),
        dict(check_id="purpose:amplitude-pooling",execution="not_selected",findings=[]),
    ])
    result = summarize(decision)
    assert result["status"] == "unassessed"
    assert len(result["requirements"]) == 1
    assert "bandpass alone is insufficient" in result["requirements"][0]["action"]


def test_no_findings_does_not_become_approval():
    result = summarize(dict(assertion={"status":"unassessed"},assertion_sha256="a"*64,
        check_groups=[dict(check_id="anatomy",execution="executed",findings=[])]))
    assert result["status"] == "unassessed"
    assert result["requirements"] == []
