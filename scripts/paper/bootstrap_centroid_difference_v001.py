"""Post hoc participant bootstrap of signed study-median centroid differences."""
import hashlib
import json
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "paper/results/validation/physiology_positive_control.json"
OUT = ROOT / "paper/results/centroid_bootstrap_v001_2026.09.16-r1"
MUSCLES = ("tibialis anterior", "medial gastrocnemius")
STUDIES = ("study_38", "study_120")
SEED = 20260916
RESAMPLES = 100000

def calculate(source, seed=SEED, resamples=RESAMPLES):
    rows = source["metrics"]
    assert len(rows) == 40
    assert sum(not r["pattern_pass"] for r in rows) == 2
    arrays, identities = [], {}
    for study in STUDIES:
        ids = sorted({r["subject_id"] for r in rows if r["study_id"] == study})
        assert len(ids) == 10
        values = []
        for subject in ids:
            pair = []
            for muscle in MUSCLES:
                match = [r for r in rows if (r["study_id"], r["subject_id"], r["muscle"]) == (study, subject, muscle)]
                assert len(match) == 1 and match[0]["outcome"] == "assessed"
                pair.append(match[0]["activation_centroid_percent_gait_cycle"])
            values.append(pair)
        array = np.asarray(values, dtype=np.float64)
        assert array.shape == (10, 2) and np.isfinite(array).all()
        arrays.append(array)
        identities[study] = ids
    rng = np.random.default_rng(seed)
    # Independent draws between studies, shared participant draws across muscles.
    boot_medians = [np.median(a[rng.integers(0, len(a), size=(resamples, len(a)))], axis=1) for a in arrays]
    replicates = boot_medians[1] - boot_medians[0]
    observed = np.median(arrays[1], axis=0) - np.median(arrays[0], axis=0)
    ci = np.quantile(replicates, [0.025, 0.975], axis=0, method="linear")
    estimates = []
    for j, muscle in enumerate(MUSCLES):
        original = next(r for r in source["cross_study_centroid_concordance"] if r["muscle"] == muscle)
        assert abs(abs(observed[j]) - original["absolute_cross_study_difference_percent"]) < 1e-10
        estimates.append(dict(muscle=muscle, signed_median_difference_pp=float(observed[j]),
            ci_lower_pp=float(ci[0,j]), ci_upper_pp=float(ci[1,j]),
            bootstrap_standard_error_pp=float(np.std(replicates[:,j], ddof=1)),
            study38_median=float(np.median(arrays[0][:,j])),
            gait120_median=float(np.median(arrays[1][:,j]))))
    return dict(analysis_id="centroid_bootstrap_v001_2026.09.16-r1",
        status="post_hoc_additional_analysis", direction="Gait120 minus Study 38",
        unit="percentage points of gait cycle", resampling_unit="participant",
        n_per_study=10, n_participant_muscle=40, pattern_failures_retained=2,
        independent_study_resampling=True, shared_subject_draws_across_muscles=True,
        resamples=resamples, seed=seed, rng="NumPy default_rng PCG64",
        numpy_version=np.__version__, interval_method="percentile",
        confidence_level=0.95, interval_scope="pointwise per muscle, not simultaneous",
        quantile_method="linear", participant_order=identities, estimates=estimates,
        bootstrap_replicates_sha256=hashlib.sha256(replicates.astype("<f8").tobytes()).hexdigest(),
        limitations=["Ten participants per study; bootstrap coverage is approximate.",
            "Participant summaries are fixed; cycles are not resampled.",
            "No measurement-error, preprocessing-uncertainty or between-study heterogeneity model.",
            "An interval containing zero does not establish equivalence.",
            "No change to original protocol, data, exclusions, or release decisions."])

def main():
    result = calculate(json.loads(SOURCE.read_text()))
    result["sources"] = [{"path":str(p.relative_to(ROOT)), "sha256":hashlib.sha256(p.read_bytes()).hexdigest()} for p in (SOURCE, Path(__file__))]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["estimates"], indent=2))

if __name__ == "__main__":
    main()
