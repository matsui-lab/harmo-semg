#!/usr/bin/env python3
"""Generate deterministic G8 manuscript figures, tables, and hash manifests."""

from __future__ import annotations

import argparse
import re
import csv
import hashlib
import json
import os
import tempfile
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

os.environ.setdefault(
    "MPLCONFIGDIR",
    str(Path(tempfile.gettempdir()) / "harmo-semg-matplotlib-cache"),
)
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import patches
import numpy as np
import yaml


COLORS = {
    "navy": "#16324F",
    "blue": "#2D6A9F",
    "cyan": "#58A6A6",
    "green": "#2A7F62",
    "light_green": "#A8D5BA",
    "amber": "#D89B2B",
    "light_amber": "#F5D99A",
    "red": "#B84A4A",
    "light_red": "#E6B4B4",
    "gray": "#8A94A0",
    "light_gray": "#D9DEE3",
    "ink": "#1D2731",
    "white": "#FFFFFF",
}

plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.labelsize": 10,
        "figure.titlesize": 14,
        "svg.hashsalt": "harmo-semg-g8",
        "pdf.fonttype": 42,
    }
)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def save_figure(figure: plt.Figure, basename: Path) -> List[Path]:
    basename.parent.mkdir(parents=True, exist_ok=True)
    svg = basename.with_suffix(".svg")
    png = basename.with_suffix(".png")
    figure.savefig(
        svg,
        bbox_inches="tight",
        metadata={"Date": None, "Creator": "harmo-semg G8 artifact generator"},
    )
    figure.savefig(
        png,
        dpi=180,
        bbox_inches="tight",
        metadata={"Software": "harmo-semg G8 artifact generator"},
    )
    plt.close(figure)
    return [svg, png]


def _box(
    axis: plt.Axes,
    x: float,
    y: float,
    width: float,
    height: float,
    text: str,
    facecolor: str,
    edgecolor: str = COLORS["navy"],
    fontsize: float = 9,
) -> None:
    box = patches.FancyBboxPatch(
        (x, y),
        width,
        height,
        boxstyle="round,pad=0.018,rounding_size=0.025",
        linewidth=1.4,
        edgecolor=edgecolor,
        facecolor=facecolor,
    )
    axis.add_patch(box)
    axis.text(
        x + width / 2,
        y + height / 2,
        text,
        ha="center",
        va="center",
        color=COLORS["ink"],
        fontsize=fontsize,
        linespacing=1.25,
    )


def _arrow(
    axis: plt.Axes,
    start: Tuple[float, float],
    end: Tuple[float, float],
    color: str = COLORS["gray"],
) -> None:
    axis.annotate(
        "",
        xy=end,
        xytext=start,
        arrowprops={
            "arrowstyle": "-|>",
            "color": color,
            "lw": 1.5,
            "shrinkA": 3,
            "shrinkB": 3,
        },
    )


def figure_evidence_framework(output: Path) -> List[Path]:
    figure, axis = plt.subplots(figsize=(15, 7.4))
    axis.set_xlim(0, 1)
    axis.set_ylim(0, 1)
    axis.axis("off")
    figure.suptitle(
        "Scientific reusability is an evidence state, not a file format",
        x=0.5,
        y=0.98,
        fontweight="bold",
        color=COLORS["navy"],
    )

    _box(
        axis,
        0.02,
        0.49,
        0.14,
        0.23,
        "Public evidence\n\nsource files\nrepositories\npublications",
        "#E8EEF3",
        fontsize=9.0,
    )

    stages = [
        (
            0.19,
            "R",
            "Recording bundle",
            "signal • time\nchannels • task",
            "#DDEAF4",
        ),
        (
            0.35,
            "M",
            "Evidence manifest",
            "source • processing\nlineage • digests",
            "#E6F2EE",
        ),
        (
            0.51,
            "T",
            "Trust report",
            "semantics • quality evidence\nreproducibility • traceability",
            "#F8EED8",
        ),
        (
            0.68,
            "C",
            "Comparability assertion",
            "named variables\npurpose-specific relation",
            "#E8EEF3",
        ),
        (
            0.85,
            "D",
            "Release decision",
            "evidence + comparison\n+ explicit policy",
            "#DDEAF4",
        ),
    ]

    for x, symbol, title, detail, color in stages:
        _box(
            axis,
            x,
            0.49,
            0.13,
            0.23,
            "{}\n{}\n\n{}".format(symbol, title, detail),
            color,
            edgecolor=COLORS["navy"],
            fontsize=8.0,
        )

    _arrow(axis, (0.16, 0.605), (0.19, 0.605))
    for index in range(len(stages) - 1):
        start = stages[index][0] + 0.13
        end = stages[index + 1][0]
        _arrow(axis, (start, 0.605), (end, 0.605))

    _box(
        axis,
        0.68,
        0.78,
        0.13,
        0.10,
        "Declared\nintended use",
        "#E6F2EE",
        edgecolor=COLORS["green"],
        fontsize=8.5,
    )
    _arrow(axis, (0.745, 0.78), (0.745, 0.72))

    states = [
        (0.35, "release", COLORS["light_green"]),
        (0.50, "conditional\nrelease", COLORS["light_amber"]),
        (0.65, "quarantine", COLORS["light_red"]),
        (0.80, "deny", COLORS["light_gray"]),
    ]
    for x, label, color in states:
        _box(
            axis,
            x,
            0.20,
            0.12,
            0.10,
            label,
            color,
            fontsize=8.5,
        )
        _arrow(axis, (0.915, 0.49), (x + 0.06, 0.30))

    axis.text(
        0.50,
        0.08,
        "convertible  ≠  traceable  ≠  comparable for a declared use  ≠  releasable",
        ha="center",
        va="center",
        fontsize=10.0,
        fontweight="bold",
        color=COLORS["navy"],
    )
    paths = save_figure(figure, output)
    svg = paths[0]
    svg.write_text(
        "\n".join(
            line.rstrip()
            for line in svg.read_text(encoding="utf-8").splitlines()
        )
        + "\n",
        encoding="utf-8",
    )
    return paths


def figure_validation_overview(
    figures: Path,
    corpus: Mapping[str, Any],
    fidelity: Mapping[str, Any],
    faults: Mapping[str, Any],
    reproducibility: Mapping[str, Any],
    adapter: Mapping[str, Any],
    atlas: Mapping[str, Any],
    full_record: Mapping[str, Any],
) -> List[Path]:
    expected = {
        "mapping denominator": (corpus["mapping_denominator"], 78),
        "corpus pass": (corpus["pass"], True),
        "corpus run count": (reproducibility["run_count"], 6),
        "environment count": (reproducibility["environment_count"], 2),
        "maximum numeric difference": (
            reproducibility["max_observed_numeric_difference"],
            0,
        ),
        "held-out trials": (adapter["trial_count"], 208),
        "held-out repeats": (adapter["repeat_count"], 2),
        "core dataset-ID branches": (adapter["core_dataset_id_branches"], 0),
        "required format controls": (
            fidelity["denominator"]["required_equivalence_comparisons"],
            32,
        ),
        "required format controls passed": (
            fidelity["denominator"]["required_equivalence_passed"],
            32,
        ),
        "C3D equivalent": (fidelity["denominator"]["c3d_equivalent"], 26),
        "C3D quarantined": (
            fidelity["denominator"]["c3d_quarantined_source_discordance"],
            34,
        ),
        "C3D source absent": (
            fidelity["denominator"]["c3d_source_absence_retained"],
            4,
        ),
        "faults detected": (faults["faults_detected"], 8),
        "fault denominator": (faults["faults_total"], 8),
        "real-source mappings": (atlas["summary"]["mapping_count"], 6),
        "real-source records": (atlas["summary"]["recording_count"], 12),
        "timing assertions": (
            atlas["summary"]["conditional_comparisons"],
            6,
        ),
        "conditional packages": (
            atlas["summary"]["conditional_releases"],
            12,
        ),
        "source channels": (atlas["summary"]["source_channels"], 160),
        "qualified channels": (atlas["summary"]["qualified_channels"], 143),
        "excluded channels": (
            atlas["summary"]["excluded_source_channels"],
            17,
        ),
        "full records": (full_record["summary"]["record_count"], 130),
        "full-record comparisons": (
            full_record["summary"]["comparison_count"],
            120,
        ),
        "full-record conditional releases": (
            full_record["summary"]["conditional_release_count"],
            130,
        ),
        "full-record reproducibility": (
            full_record["summary"]["scientific_digest_identical"],
            True,
        ),
    }
    mismatches = [
        f"{label}: expected {wanted!r}, found {found!r}"
        for label, (found, wanted) in expected.items()
        if found != wanted
    ]
    if mismatches:
        raise ValueError(
            "Editable Figure 2 does not match committed evidence: "
            + "; ".join(mismatches)
        )

    assets = [
        figures / "figure2_validation_overview.svg",
        figures / "figure2_validation_overview.png",
        figures / "editable/figure2_validation_overview_editable.pptx",
        figures / "editable/figure2_validation_overview.pdf",
        figures / "editable/figure2_validation_overview_caption.txt",
    ]
    missing = [str(path) for path in assets if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing editable Figure 2 export(s): {}".format(
                ", ".join(missing)
            )
        )
    # The G8 manuscript manifest indexes the protocol-declared SVG/PNG pair.
    # Editable, PDF, and caption derivatives are checksum-indexed separately
    # in paper/results/figure2_artifacts.json.
    return assets[:2]


def figure_physiology(
    output: Path,
    physiology: Mapping[str, Any],
) -> List[Path]:
    metrics = physiology["metrics"]
    result_lookup = {
        (row["study_id"], row["muscle"]): row
        for row in physiology["study_muscle_results"]
    }
    concordance = {
        row["muscle"]: row
        for row in physiology["cross_study_centroid_concordance"]
    }
    muscles = ["tibialis anterior", "medial gastrocnemius"]
    display = {
        "tibialis anterior": "Tibialis anterior",
        "medial gastrocnemius": "Medial gastrocnemius",
    }
    studies = ["study_38", "study_120"]
    study_names = {"study_38": "Study 38", "study_120": "Gait120"}
    study_colors = {"study_38": COLORS["blue"], "study_120": COLORS["cyan"]}

    figure, axes = plt.subplots(1, 2, figsize=(11, 5.2), sharey=True)
    for axis, muscle in zip(axes, muscles):
        for position, study in enumerate(studies):
            rows = [
                row
                for row in metrics
                if row["study_id"] == study and row["muscle"] == muscle
            ]
            values = [
                float(row["activation_centroid_percent_gait_cycle"])
                for row in rows
            ]
            pattern = [bool(row["pattern_pass"]) for row in rows]
            jitter = np.linspace(-0.10, 0.10, len(values))
            for value, passed, offset in zip(values, pattern, jitter):
                if not passed:
                    continue
                axis.scatter(
                    position + offset,
                    value,
                    s=38,
                    facecolor=study_colors[study],
                    edgecolor=study_colors[study],
                    linewidth=1.2,
                    zorder=3,
                )
            for value, passed, offset in zip(values, pattern, jitter):
                if passed:
                    continue
                axis.scatter(
                    position + offset,
                    value,
                    s=52,
                    marker="x",
                    color=COLORS["red"],
                    linewidth=1.8,
                    zorder=5,
                )
            axis.boxplot(
                [values],
                positions=[position],
                widths=0.42,
                patch_artist=True,
                showfliers=False,
                boxprops={
                    "facecolor": COLORS["white"],
                    "edgecolor": study_colors[study],
                    "linewidth": 1.4,
                },
                medianprops={"color": COLORS["ink"], "linewidth": 1.7},
                whiskerprops={"color": study_colors[study]},
                capprops={"color": study_colors[study]},
            )
            summary = result_lookup[(study, muscle)]
            axis.text(
                position,
                8,
                "{}/{} pattern pass".format(
                    int(round(summary["subject_pattern_fraction"] * 10)), 10
                ),
                ha="center",
                va="center",
                fontsize=8.2,
                color=COLORS["ink"],
            )

        difference = concordance[muscle]["absolute_cross_study_difference_percent"]
        axis.set_title(display[muscle], fontweight="bold", color=COLORS["navy"])
        axis.text(
            0.5,
            94,
            "Median centroid difference = {:.2f}%\n(frozen limit 15%)".format(
                difference
            ),
            ha="center",
            va="top",
            fontsize=8.7,
            color=COLORS["green"],
        )
        axis.set_xticks([0, 1], [study_names[value] for value in studies])
        axis.set_xlim(-0.45, 1.45)
        axis.set_ylim(0, 100)
        axis.yaxis.grid(True, color="#E5E8EB", linewidth=0.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right"]].set_visible(False)
    axes[0].set_ylabel("Activation centroid (% gait cycle)")
    figure.suptitle(
        "Cross-study physiological positive control",
        x=0.08,
        ha="left",
        fontweight="bold",
        color=COLORS["navy"],
    )
    figure.text(
        0.08,
        0.01,
        "Points are participants (n=10 per study). Filled points pass the frozen "
        "muscle-specific phase pattern; amplitudes were normalized within participant "
        "for timing only and were not pooled across studies.",
        fontsize=8.4,
        color=COLORS["gray"],
    )
    figure.subplots_adjust(left=0.09, right=0.98, top=0.86, bottom=0.18, wspace=0.18)
    return save_figure(figure, output)


def figure_mechanics_boundary(
    output: Path,
    opensim: Mapping[str, Any],
    disposition: Mapping[str, Any],
) -> List[Path]:
    figure, axis = plt.subplots(figsize=(12, 6.5))
    axis.set_xlim(0, 1)
    axis.set_ylim(0, 1)
    axis.axis("off")
    figure.suptitle(
        "G6 mechanics evidence: complete reporting does not convert failure into validity",
        y=0.97,
        fontweight="bold",
        color=COLORS["navy"],
    )

    _box(axis, 0.04, 0.67, 0.18, 0.18, "Frozen denominator\n336 trials", "#E8EEF3")
    _arrow(axis, (0.22, 0.76), (0.29, 0.76))
    _box(axis, 0.29, 0.72, 0.18, 0.14, "330 source-available", "#E8EEF3")
    _box(axis, 0.29, 0.52, 0.18, 0.14, "6 source-absent\n(retained)", COLORS["light_gray"])
    _arrow(axis, (0.22, 0.73), (0.29, 0.59))
    _arrow(axis, (0.47, 0.79), (0.54, 0.79))
    _box(
        axis,
        0.54,
        0.70,
        0.19,
        0.18,
        "Released static\ncalibration/model lineage\nabsent",
        COLORS["light_red"],
        edgecolor=COLORS["red"],
    )
    _arrow(axis, (0.73, 0.79), (0.80, 0.79))
    _box(
        axis,
        0.80,
        0.70,
        0.16,
        0.18,
        "0/330 fully assessed\n336 unassessed",
        COLORS["light_red"],
        edgecolor=COLORS["red"],
    )

    _arrow(axis, (0.63, 0.70), (0.63, 0.50))
    _box(
        axis,
        0.40,
        0.31,
        0.46,
        0.18,
        "Checksum-bound public-data diagnostics\n"
        "state-reconstruction bias localized • residual-moment failure remains\n"
        "subject/activity-matched upper-limb cause unidentifiable",
        "#F8EED8",
        edgecolor=COLORS["amber"],
        fontsize=9,
    )
    _arrow(axis, (0.63, 0.31), (0.63, 0.22))
    _box(
        axis,
        0.12,
        0.05,
        0.28,
        0.15,
        "Reporting complete\n26 artifacts • 106/106 checks",
        COLORS["light_green"],
        edgecolor=COLORS["green"],
    )
    _box(
        axis,
        0.46,
        0.05,
        0.22,
        0.15,
        "Scientific endpoint\nPASS = false",
        COLORS["light_red"],
        edgecolor=COLORS["red"],
    )
    _box(
        axis,
        0.74,
        0.05,
        0.22,
        0.15,
        "Standard AI-ready export\nmechanics = DENY",
        COLORS["light_red"],
        edgecolor=COLORS["red"],
    )
    _arrow(axis, (0.63, 0.22), (0.26, 0.20), COLORS["green"])
    _arrow(axis, (0.63, 0.22), (0.57, 0.20), COLORS["red"])
    _arrow(axis, (0.68, 0.125), (0.74, 0.125), COLORS["red"])
    return save_figure(figure, output)


def write_csv_and_markdown(
    basename: Path,
    columns: Sequence[Tuple[str, str]],
    rows: Sequence[Mapping[str, Any]],
) -> List[Path]:
    basename.parent.mkdir(parents=True, exist_ok=True)
    csv_path = basename.with_suffix(".csv")
    md_path = basename.with_suffix(".md")
    keys = [item[0] for item in columns]
    labels = [item[1] for item in columns]
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in keys})
    lines = [
        "| " + " | ".join(labels) + " |",
        "|" + "|".join("---" for _ in labels) + "|",
    ]
    for row in rows:
        values = []
        for key in keys:
            value = str(row.get(key, "")).replace("|", "\\|").replace("\n", " ")
            values.append(value)
        lines.append("| " + " | ".join(values) + " |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return [csv_path, md_path]


def table_public_studies(
    root: Path,
    registry: Mapping[str, Any],
    sources: Mapping[str, Any],
    inventory: Mapping[str, Any],
    output: Path,
) -> List[Path]:
    mappings = {
        item["directory"]: int(item["enabled_mapping_count"])
        for item in inventory["studies"]
        if item["status"] == "active"
    }
    rows = []
    for study in registry["studies"]:
        source = sources["sources"][study["id"]]
        release = study.get("source_release_doi")
        if isinstance(release, list):
            release = "; ".join(release)
        rows.append(
            {
                "study_id": study["id"],
                "dataset": source["title"],
                "article_doi": study["doi"],
                "release_doi": release,
                "license": study["license"],
                "emg": "yes" if study["emg_available"] else "no",
                "enabled_mappings": mappings[
                    Path(study["config"]).parts[-2]
                ],
                "validation_role": study.get(
                    "validation_role", "harmonization and evidence corpus"
                ),
            }
        )
    columns = [
        ("study_id", "Study"),
        ("dataset", "Public dataset"),
        ("article_doi", "Article DOI"),
        ("release_doi", "Release DOI"),
        ("license", "Licence"),
        ("emg", "EMG"),
        ("enabled_mappings", "Mappings"),
        ("validation_role", "Validation role"),
    ]
    return write_csv_and_markdown(output, columns, rows)


def table_validation_endpoints(
    output: Path,
    corpus: Mapping[str, Any],
    fidelity: Mapping[str, Any],
    faults: Mapping[str, Any],
    reproducibility: Mapping[str, Any],
    opensim: Mapping[str, Any],
    adapter: Mapping[str, Any],
    physiology: Mapping[str, Any],
    full_record: Mapping[str, Any],
) -> List[Path]:
    format_denominator = fidelity["denominator"]
    source_denominator = opensim["source_denominator"]
    patterns = sum(
        int(round(row["subject_pattern_fraction"] * row["expected_subjects"]))
        for row in physiology["study_muscle_results"]
    )
    rows = [
        {
            "id": "A",
            "question": "Can every enabled mapping execute with explicit evidence?",
            "denominator": "78 mappings",
            "result": "78 converted; 0 failed/missing; 1,321,948 sampled rows",
            "status": "pass",
            "boundary": "One deterministic representative per enabled mapping, not every source file.",
        },
        {
            "id": "B",
            "question": "Are official formats numerically interchangeable?",
            "denominator": "96 attempts",
            "result": (
                "30 ASCII–MAT equivalent; 2 absent retained (32/32 controls pass); "
                "{} C3D equivalent, {} quarantined, {} source-absent".format(
                    format_denominator["c3d_equivalent"],
                    format_denominator["c3d_quarantined_source_discordance"],
                    format_denominator["c3d_source_absence_retained"],
                )
            ),
            "status": "pass-with-quarantine",
            "boundary": "C3D discordance is exposed, not forced into equivalence.",
        },
        {
            "id": "C",
            "question": "Are prespecified semantic and lineage faults detected?",
            "denominator": "8 faults + clean control",
            "result": "8/8 detected; critical sensitivity 1.0; clean specificity 1.0",
            "status": "pass",
            "boundary": "Controlled faults do not substitute for real-data validation.",
        },
        {
            "id": "D",
            "question": "Does the frozen corpus reproduce across environments?",
            "denominator": "2 environments × 3 runs",
            "result": "6/6 pass; maximum canonical numeric difference 0",
            "status": "pass",
            "boundary": "Applies to the frozen 78-mapping corpus and dependencies.",
        },
        {
            "id": "E",
            "question": "Are OpenSim derivatives supported by released calibration lineage?",
            "denominator": "336 trials",
            "result": (
                "{} source-available, {} source-absent, 0 fully assessed".format(
                    source_denominator["source_available"],
                    source_denominator["source_absent"],
                )
            ),
            "status": "scientific-fail; reporting-complete",
            "boundary": "Mechanics and dynamic personalization remain unvalidated and denied by default.",
        },
        {
            "id": "F",
            "question": "Can an unopened dataset be added without core dataset-ID branches?",
            "denominator": "13 participants; 208 trials",
            "result": "208/208 read twice; core changed lines 0; branches 0",
            "status": "pass",
            "boundary": "Processed release only; source preprocessing and clinical validity are not validated.",
        },
        {
            "id": "G",
            "question": "Are muscle-specific gait timing patterns retained across studies?",
            "denominator": "2 studies × 10 participants × 2 muscles",
            "result": "{}/40 participant–muscle patterns; both centroid checks pass".format(
                patterns
            ),
            "status": "pass",
            "boundary": "Timing only; incompatible amplitudes are not pooled.",
        },
        {
            "id": "H",
            "question": "Can one frozen cohort be materialized without record truncation and queried reproducibly?",
            "denominator": "{} participants; {} full records; {:,} timepoints".format(
                full_record["summary"]["subject_count"],
                full_record["summary"]["record_count"],
                full_record["summary"]["timepoint_count"],
            ),
            "result": (
                "{:,} values; {} conditional releases; {}/{} clean runs with "
                "identical scientific digests and signal/record tables"
            ).format(
                full_record["summary"]["signal_value_count"],
                full_record["summary"]["conditional_release_count"],
                full_record["summary"]["clean_code_runs"],
                full_record["summary"]["reproducibility_runs"],
            ),
            "status": "pass" if full_record["summary"]["pass"] else "fail",
            "boundary": "Study 38 same-side Walking cohort; local single-machine timing, not distributed or AI utility.",
        },
    ]
    columns = [
        ("id", "Experiment"),
        ("question", "Question"),
        ("denominator", "Frozen denominator"),
        ("result", "Result"),
        ("status", "Status"),
        ("boundary", "Claim boundary"),
    ]
    return write_csv_and_markdown(output, columns, rows)


def table_reporting_evidence(root: Path, output: Path) -> List[Path]:
    roles = {
        "paper/results/validation/corpus_execution.json": "A all-study execution",
        "paper/results/validation/format_fidelity.json": "B format fidelity",
        "paper/results/validation/fault_detection.json": "C fault injection",
        "paper/results/validation/reproducibility.json": "D independent reproducibility",
        "paper/results/validation/opensim_quality_summary.json": "E frozen OpenSim denominator",
        "paper/results/validation/g6_mechanics_disposition.json": "E final mechanics disposition",
        "paper/results/validation/adapter_metrics.json": "F held-out adapter",
        "paper/results/validation/ultra_mocap_adapter_cross_process_reproducibility.json": "F adapter reproducibility",
        "paper/results/validation/physiology_positive_control.json": "G physiology positive control",
        "paper/results/validation/physiology_positive_control_reproducibility.json": "G physiology reproducibility",
        "paper/results/validation/literature_semantic_enrichment.json": "Literature semantic enrichment",
        "paper/results/validation/semantic_coverage_comparison.json": "File-only vs paper-assisted semantic coverage",
        "paper/results/validation/semantic_overlay_study28_emg.json": "Study 28 semantic overlay",
        "paper/results/validation/semantic_overlay_study33_dataset.json": "Study 33 semantic overlay",
        "paper/results/validation/semantic_overlay_materialization_audit.json": "Semantic evidence provenance-to-decision audit",
        "paper/results/validation/full_record_materialization_benchmark.json": "Study 38 full-record materialization and DuckDB benchmark",
        "paper/semantic_gold_standard_protocol.yaml": "Independent semantic gold-standard protocol",
        "paper/semantic_gold_standard_packet.json": "Blinded 43-question semantic review packet",
        "paper/results/validation/gate_audit.json": "G0-G7 submission gate",
    }
    rows = []
    for relative, role in roles.items():
        path = root / relative
        rows.append(
            {
                "role": role,
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    columns = [
        ("role", "Evidence role"),
        ("path", "Repository path"),
        ("bytes", "Bytes"),
        ("sha256", "SHA-256"),
    ]
    return write_csv_and_markdown(output, columns, rows)


def result_figure_assets(root: Path, figures: Path) -> List[Path]:
    stems = [
        "figure4_full_record_query",
        "figure5_physiology_positive_control",
        "figure6_mechanics_release_boundary",
    ]
    assets: List[Path] = []
    for stem in stems:
        assets.extend(
            [
                figures / f"{stem}.svg",
                figures / f"{stem}.png",
                figures / "editable" / f"{stem}.pdf",
            ]
        )
    assets.extend(
        [
            figures / "editable" / "figures2_to_6_results_editable.pptx",
            figures / "editable" / "figures2_to_6_captions.txt",
        ]
    )
    missing = [str(path) for path in assets if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing selected empirical result figure asset(s): {}".format(
                ", ".join(missing)
            )
        )

    manifest = read_json(root / "paper/results/result_figure_artifacts.json")
    if manifest.get("design_role") != "empirical-results-only":
        raise ValueError("Result-figure manifest must be empirical-results-only")
    if [item.get("number") for item in manifest.get("figures", [])] != [
        2,
        3,
        4,
        5,
        6,
    ]:
        raise ValueError("Result-figure manifest must select Figures 2–6")
    indexed = {
        item["path"]: item["sha256"] for item in manifest.get("files", [])
    }
    for path in assets:
        relative = str(path.relative_to(root))
        if indexed.get(relative) != sha256_file(path):
            raise ValueError(
                f"Result-figure asset is absent from or differs from its manifest: "
                f"{relative}"
            )
    # Figure 2 is the implementation diagram; Figures 3--4 are ggplot2 selections.
    # The legacy bundle supplies current Figures 5--7 without redrawing them.
    selected_assets = []
    for number in (2, 3, 4):
        selection = read_json(figures / f"FIGURE{number}_SELECTION.json")
        if selection.get("status") != "selected_for_manuscript":
            raise ValueError(f"Figure {number} requires an explicit manuscript selection")
        for item in [selection["source"], selection["caption"], *selection["exports"]]:
            path = root / item["path"]
            if path.stat().st_size != item["bytes"] or sha256_file(path) != item["sha256"]:
                raise ValueError(f"Selected Figure {number} source or export has changed: {path}")
        selected_assets.extend(root / item["path"] for item in selection["exports"])
    renumbered_assets = []
    for path in assets:
        if path.parent == figures:
            name = re.sub(r"^figure([4-6])_", lambda m: f"figure{int(m[1])+1}_", path.name)
            target = figures / name
            if not target.is_file() or sha256_file(target) != sha256_file(path):
                raise ValueError(f"Renumbered figure differs from approved artwork: {target}")
            renumbered_assets.append(target)
        else:
            renumbered_assets.append(path)
    return selected_assets + renumbered_assets


def captions(root: Path | None = None) -> str:
    """Assemble all current captions without restoring historical wording."""
    root = root or Path(__file__).resolve().parents[2]
    selected = [
        (root / f"paper/figures/figure{number}_caption.md").read_text(encoding="utf-8").strip()
        for number in range(1, 8)
    ]
    for number, caption in enumerate(selected, 1):
        if not caption.startswith(f"## Figure {number}."):
            raise ValueError(f"Caption numbering mismatch: {number}")
    return "# Figure captions and provenance\n\n" + "\n\n".join(selected) + "\n"


def build_artifacts(root: Path) -> Dict[str, Any]:
    protocol = read_yaml(root / "paper/g8_reporting_protocol.yaml")
    registry = read_yaml(root / "paper/validation_registry.yaml")
    sources = read_yaml(root / "paper/source_releases.yaml")
    inventory = read_json(root / "paper/results/config_inventory.json")
    validation = root / "paper/results/validation"
    corpus = read_json(validation / "corpus_execution.json")
    fidelity = read_json(validation / "format_fidelity.json")
    faults = read_json(validation / "fault_detection.json")
    reproducibility = read_json(validation / "reproducibility.json")
    opensim = read_json(validation / "opensim_quality_summary.json")
    disposition = read_json(validation / "g6_mechanics_disposition.json")
    adapter = read_json(validation / "adapter_metrics.json")
    physiology = read_json(validation / "physiology_positive_control.json")
    full_record = read_json(
        validation / "full_record_materialization_benchmark.json"
    )
    atlas = read_json(
        root
        / "paper/results/atlas_materialization/atlas.materialization.index.json"
    )

    generated: List[Path] = []
    figures = root / "paper/figures"
    # Figure 1 is authored in editable PowerPoint and exported to SVG/PNG.
    # Preserve these publication assets rather than replacing them with the
    # earlier Matplotlib-only schematic.
    figure1_assets = [
        figures / "figure1_evidence_framework.svg",
        figures / "figure1_evidence_framework.png",
    ]
    missing_figure1 = [str(path) for path in figure1_assets if not path.exists()]
    if missing_figure1:
        raise FileNotFoundError(
            "Missing editable Figure 1 export(s): {}".format(
                ", ".join(missing_figure1)
            )
        )
    generated.extend(figure1_assets)
    # Figure 2 explains implementation; 3--4 use ggplot2; 5--7 retain earlier artwork.
    # Validate selections without redrawing or overwriting the approved figures.
    generated.extend(result_figure_assets(root, figures))
    captions_path = figures / "README.md"
    captions_path.write_text(captions(root), encoding="utf-8")
    generated.append(captions_path)

    tables = root / "paper/tables"
    generated.extend(
        table_public_studies(
            root,
            registry,
            sources,
            inventory,
            tables / "table1_public_studies",
        )
    )
    generated.extend(
        table_validation_endpoints(
            tables / "table2_validation_endpoints",
            corpus,
            fidelity,
            faults,
            reproducibility,
            opensim,
            adapter,
            physiology,
            full_record,
        )
    )
    generated.extend(
        table_reporting_evidence(
            root, tables / "table_s1_reporting_evidence"
        )
    )

    evidence_sources = [
        "paper/g8_reporting_protocol.yaml",
        "paper/validation_registry.yaml",
        "paper/source_releases.yaml",
        "paper/results/config_inventory.json",
        "paper/results/validation/corpus_execution.json",
        "paper/results/validation/mapping_outcomes.json",
        "paper/results/validation/format_fidelity.json",
        "paper/results/validation/fault_detection.json",
        "paper/results/validation/reproducibility.json",
        "paper/results/validation/opensim_quality_summary.json",
        "paper/results/validation/g6_mechanics_disposition.json",
        "paper/results/validation/adapter_metrics.json",
        "paper/results/validation/ultra_mocap_trial_manifest.json",
        "paper/results/validation/physiology_positive_control.json",
        "paper/results/validation/literature_semantic_enrichment.json",
        "paper/results/validation/semantic_coverage_comparison.json",
        "paper/results/validation/semantic_overlay_study28_emg.json",
        "paper/results/validation/semantic_overlay_study33_dataset.json",
        "paper/results/validation/semantic_overlay_materialization_audit.json",
        "paper/results/validation/full_record_materialization_benchmark.json",
        "paper/results/atlas_materialization/atlas.materialization.index.json",
        "paper/results/result_figure_artifacts.json",
        "paper/semantic_gold_standard_protocol.yaml",
        "paper/semantic_gold_standard_packet.json",
        "scripts/paper/generate_figure1_nature.mjs",
        "scripts/paper/generate_result_figures.mjs",
        "scripts/paper/export_result_figure_pdfs.py",
        "paper/figures/assets/figure1_emg_public_data_illustration.png",
        "paper/figures/figure1_caption.md",
        "paper/figures/FIGURE1_SELECTION.json",
        "paper/figures/editable/figure1_four_panel_v018_2026.09.16-r1_editable.pptx",
        "paper/figures/editable/figure1_four_panel_v018_2026.09.16-r1.pdf",
        "paper/figures/figure2_caption.md",
        "paper/figures/FIGURE2_SELECTION.json",
        "paper/figures/editable/figure2_three_panel_v001_2026.09.09-r1_editable.pptx",
        "scripts/paper/generate_figure2_ggplot.R",
        "scripts/paper/export_figure2_editable.mjs",
        "scripts/paper/adopt_figure2_selection.py",
        "paper/figures/figure3_caption.md",
        "paper/figures/FIGURE3_SELECTION.json",
        "scripts/paper/generate_figure3_ggplot.R",
        "scripts/paper/adopt_figure3_selection.py",
        "paper/figures/figure4_caption.md",
        "paper/figures/FIGURE4_SELECTION.json",
        "paper/figures/figure5_caption.md",
        "paper/figures/figure6_caption.md",
        "paper/figures/figure7_caption.md",
        "scripts/paper/adopt_implementation_selection.py",
        "scripts/paper/generate_implementation_figure.py",
        "scripts/paper/generate_implementation_flow_v016.py",
        "scripts/paper/plot_implementation_flow_v016.R",
        "scripts/paper/build_integrated_figures_editable.mjs",
        "paper/figures/candidates/figure2_implementation_gui_v016_2026.09.16-r5/figure2_implementation_gui_v016_2026.09.16-r5_evidence.json",
        "paper/figures/candidates/figure2_implementation_gui_v016_2026.09.16-r5/figure2_implementation_gui_v016_2026.09.16-r5_editable.pptx",
        "paper/figures/assets/repository_logos/SOURCES.md",
    ]
    manifest = {
        "schema_version": "1.0",
        "protocol_id": protocol["protocol_id"],
        "generator": "scripts/paper/generate_g8_artifacts.py",
        "generator_sha256": sha256_file(
            root / "scripts/paper/generate_g8_artifacts.py"
        ),
        "source_evidence": [
            {
                "path": relative,
                "bytes": (root / relative).stat().st_size,
                "sha256": sha256_file(root / relative),
            }
            for relative in evidence_sources
        ],
        "generated_artifacts": [
            {
                "path": str(path.relative_to(root)),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in sorted(generated)
        ],
    }
    manifest["result_sha256"] = hashlib.sha256(
        canonical_json(manifest).encode("utf-8")
    ).hexdigest()
    manifest_path = root / "paper/results/g8_reporting_artifacts.json"
    write_json(manifest_path, manifest)
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", default=str(Path(__file__).resolve().parents[2])
    )
    return parser


def main(argv: Sequence[str] = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.root).resolve()
    manifest = build_artifacts(root)
    print(
        json.dumps(
            {
                "generated_artifact_count": len(
                    manifest["generated_artifacts"]
                ),
                "result_sha256": manifest["result_sha256"],
                "output": "paper/results/g8_reporting_artifacts.json",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
