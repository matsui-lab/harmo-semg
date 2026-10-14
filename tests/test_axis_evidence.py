import pytest
from harmo.axis_evidence import extract, bind


def test_axis_limits_are_not_grid_evidence():
    from harmo.axis_evidence import inspect_registered_plot_code
    with pytest.raises(ValueError, match="unreviewed_plot_code_version"):
        inspect_registered_plot_code(b"plot(y); xlim([0 100]);")


def document(scope="s.Data", negation="", count=100):
    return (f'<article><front><article-id pub-id-type="doi">example</article-id></front><body>'
        f'<p>The angles were time normalized to {count} points.</p><table-wrap id="T1"><table>'
        f'<tr><th>Field of {scope} structure</th><th>Description</th></tr>'
        f'<tr><th>.Ang</th><td>Matrix (12 × 101). {negation}The columns are the sample points expressed as percentage of the stride duration.</td></tr>'
        '</table></table-wrap></body></article>').encode()


def test_axis_kind_is_separate_from_grid():
    claims = extract(document(), "example")
    assert len(claims) == 1
    assert claims[0]["conflicting_prose_counts"] == [100]
    result = bind(claims, "Ang", (12, 101), "sourcehash", "s.Data.0")
    assert result["axis_kind"] == "stride_duration_percentage"
    assert result["grid_status"] == "not_established"
    assert result["reason"] == "prose_table_count_conflict"


def test_standing_not_promoted():
    assert not extract(document(scope="s.StandingData"), "example")
    assert bind(extract(document(), "example"), "Ang", (12, 101), "hash", "s.StandingData.0")["reason"] == "record_scope_mismatch"


def test_negation_not_promoted():
    assert not extract(document(negation="Not applicable. "), "example")


def test_wrong_article():
    with pytest.raises(ValueError, match="article_identity"):
        extract(document(), "different")


def test_shape_mismatch_and_duplicate_claims():
    claims = extract(document(), "example")
    assert bind(claims, "Ang", (101, 12), "hash", "s.Data.0")["reason"] == "document_array_shape_conflict"
    assert bind(claims*2, "Ang", (12, 101), "hash", "s.Data.0")["reason"] == "missing_or_competing_table_claim"


def test_matching_count_does_not_invent_grid():
    claims = extract(document(count=101), "example")
    assert bind(claims, "Ang", (12, 101), "hash", "s.Data.0")["reason"] == "exact_grid_not_explicit"
