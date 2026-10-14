"""Expose all executed policy findings without upgrading scientific trust."""


def summarize(decision):
    requirements = []
    for group in decision["check_groups"]:
        if group["execution"] != "executed":
            continue
        for finding in group["findings"]:
            reason = finding["reason"]
            if "common event or phase reference" in reason:
                action = "Confirm cycle boundaries and reference side; validate the declared phase mapping."
            elif "processing compatibility group" in reason:
                action = "Execute and validate a compatible envelope-processing protocol; an acquisition bandpass alone is insufficient."
            elif "amplitude normalization" in reason:
                action = "Provide validated cross-record amplitude calibration or normalization; matching voltage units alone are insufficient."
            elif group["check_id"].startswith("required_evidence:"):
                action = "Review the required trust dimension using explicit evidence; do not turn it to pass because attributes were added."
            elif group["check_id"] == "anatomy":
                action = "Resolve muscle identity and measurement laterality for both channels."
            elif group["check_id"] == "task":
                action = "Resolve task equivalence and relevant task facets for both records."
            else:
                action = "Resolve this policy finding using source or validation evidence."
            requirements.append(dict(check=group["check_id"], kind=finding["kind"], reason=reason, action=action))
    return dict(status=decision["assertion"]["status"], requirements=requirements,
        assertion_sha256=decision["assertion_sha256"],
        boundary="A list of requirements, not authorization to normalize, resample, pool, or upgrade trust.")
