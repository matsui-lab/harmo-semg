"""Check the bounded Ota-comment revision without rerunning scientific analyses."""
import csv
import hashlib
import importlib.util
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "v026_2026.09.25-r1"
OLD = "v025_2026.09.16-r13"
SUPPLEMENT_OLD = "paper/supplementary_results_v024_2026.09.16-r12.md"
SUPPLEMENT_NEW = f"paper/supplementary_results_{VERSION}.md"
BASELINE = {
    f"paper/versions/harmo-semg_manuscript_{OLD}.md": "70b4dddb2d45bfdb5a2f9117a879d1c723552a0211501fa6086819ac8ad57792",
    f"paper/versions/harmo-semg_manuscript_{OLD}.tex": "6afaec4981cb89de5c9684665b32db88b000cc35adafbdb2c35fb3b47da10f3d",
    f"paper/versions/harmo-semg_manuscript_{OLD}.pdf": "c5bce058919f4b83bdc6907c95af9026872b5e81f582ebae1d010e4ee7a839d7",
    SUPPLEMENT_OLD: "f4eea34a1ff2c84eedb28bb0a5b42f600c7163c4c85286a22b87cf5b63169353",
    "paper/supplementary_methods_v024_2026.09.16-r12.md": "b73875eef69c32194102d4620b67ebfaa9b628a55b74a4f81036096e24a95e5b",
    "paper/tables/table1_public_studies.csv": "3652da34f0308d95a5fdf59820cd8b805aca1d63f8dc2aec3a31db97a4c9a2c9",
    "paper/results/validation/full_record_materialization_benchmark.json": "255c7e75c3930699eee73510f1d61660afd8fc6e10879f909c434fc565a8c000",
    "paper/results/validation/corpus_execution.json": "d1c09a0c071be22980253aed23d89a2ad0ae863ae987365c18c9a376f6088e0f",
    "paper/results/validation/mapping_outcomes.json": "a194516d5af21bdc5d9814a36b4279017db5e5ed086b976ef189417cd16fcd4d",
    "paper/figures/figure1_caption.md": "6c32df45ec17a944d3daa39ddf5e7b50f51de531c138d1bbccbb30a8e9ef3b77",
    "paper/figures/figure2_caption.md": "e2784d8658d807f415c8af29e7f2e4e34a93af20145e5754d1553681a7e79a38",
    "paper/figures/figure3_caption.md": "2def170b1c540978aa5f6ec744522297eb9244cd254e458f9d2f27117b866583",
    "paper/figures/editable/figure1_four_panel_v018_2026.09.16-r1.pdf": "1f02a0bdbe3a75daa962e42b961dc1057924f05063514d560dea38e9e19573f0",
    "paper/figures/candidates/figure2_implementation_gui_v016_2026.09.16-r5/figure2_implementation_gui_v016_2026.09.16-r5.pdf": "95de29389d73e8a250af40b2e44fea277b03d2da787ae1763cf440b97039ddd5",
    "paper/figures/candidates/figure3_integrated_results_v008_2026.09.16-r8/figure3_integrated_results_v008_2026.09.16-r8.pdf": "66e00e2e7b0841794f9e928003c5f86fde967aab16ced16d1fa69e742fd5a09c",
}


def text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def section(document, start, stop):
    return document.split(start, 1)[1].split(stop, 1)[0]


def load_builder():
    spec = importlib.util.spec_from_file_location(
        "cmpb_revision_builder", ROOT / "scripts/paper/build_cmpb_latex.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OtaRevisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = text(f"paper/versions/harmo-semg_manuscript_{OLD}.md")
        cls.new = text(f"paper/versions/harmo-semg_manuscript_{VERSION}.md")

    def test_prior_versions_figures_and_evidence_are_unchanged(self):
        for name, expected in BASELINE.items():
            with self.subTest(path=name):
                self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), expected)

    def test_unaffected_sections_are_preserved(self):
        self.assertEqual(self.old.split("# Methods\n", 1)[0], self.new.split("# Methods\n", 1)[0])
        self.assertEqual(section(self.old, "# Discussion\n", "# Limitations\n"), section(self.new, "# Discussion\n", "# Limitations\n"))
        self.assertEqual(self.old.split("# Data and code availability\n", 1)[1], self.new.split("# Data and code availability\n", 1)[1])
        added = "Retrieval capability was not verified by successful live acquisition from\nevery supported repository.\n"
        self.assertEqual(section(self.old, "# Limitations\n", "# Data and code availability\n"), section(self.new, "# Limitations\n", "# Data and code availability\n").replace(added, ""))
        self.assertEqual(re.findall(r"^#{1,3} .+$", self.old, re.M), re.findall(r"^#{1,3} .+$", self.new, re.M))

    def test_proposal_blocks_and_all_25_comments_are_accounted_for(self):
        proposal = text("paper/revision_plans/ota_comments_revision_draft_v001_2026.09.25-r1.md")
        table = section(proposal, "## 7. コメント対応一覧", "## 8.")
        self.assertEqual([int(n) for n in re.findall(r"^\| (\d+) \|", table, re.M)], list(range(2, 27)))
        replacements = proposal.split("## 4. Supplementary Tables", 1)[0]
        normalize = lambda s: " ".join(s.split())
        amended = normalize(self.new).replace("limits on download size", "limits on file size").replace("a fixed release of the UBERON anatomy ontology", "a fixed UBERON release")
        for block in re.findall(r"(?:^>.*(?:\n|$))+", replacements, re.M):
            body = "\n".join(line.removeprefix(">").lstrip() for line in block.splitlines())
            self.assertIn(normalize(body), amended)

    def test_processing_and_inference_boundaries_are_retained(self):
        processing = section(self.new, "### Processing selection and reassessment\n", "### Linked storage")
        self.assertLess(processing.index("For general intake"), processing.index("In the Figure 2A example"))
        self.assertLess(processing.index("root-mean-square (RMS)"), processing.index("125-ms RMS smoothing"))
        for phrase in ("20--350 Hz", "125-ms Hampel", "three robust standard", "101 points", "100i/(N-1)", "not assumed", "not approval for scientific use", "previously passing physiological-control result"):
            self.assertIn(phrase, processing)
        for phrase in ("10,000", "100,000", "Before outcome inspection", "15 percentage", "lacked a documented quantitative justification", "including both pattern failures", "not permitted for scientific use", "passing assessments in all", "conditional comparisons were not permitted"):
            self.assertIn(phrase, self.new)

    def test_result_additions_are_supported_by_existing_records(self):
        mappings = json.loads(text("paper/results/validation/mapping_outcomes.json"))
        self.assertEqual(len(mappings), 78)
        self.assertTrue(all(r["outcome"] == "converted" and r["logical_source_member"] and r["asset_id"] for r in mappings))
        cohort = json.loads(text("paper/results/validation/full_record_materialization_benchmark.json"))
        self.assertEqual(cohort["summary"]["record_count"], 130)
        self.assertEqual(cohort["summary"]["signal_value_count"], 1109104)
        medians = []
        for query in cohort["runs"][0]["query_benchmark"]:
            results = [r["query_benchmark"][query] for r in cohort["runs"]]
            self.assertEqual(len({r["result_sha256"] for r in results}), 1)
            self.assertTrue(all(r["repeat_count"] == 25 for r in results))
            medians += [r["median_ms"] for r in results]
        self.assertEqual((round(min(medians), 2), round(max(medians), 2)), (0.13, 3.66))
        self.assertIn("0.13 to 3.66 ms", self.new)
        for value in ("38/40", "+0.52", "+1.17", "-4.33", "+4.97", "-1.13", "+6.02", "0.02085", "44,776", "1,616"):
            self.assertIn(value, self.new)

    def test_supplement_only_changes_storage_query_paragraph(self):
        old, new = text(SUPPLEMENT_OLD), text(SUPPLEMENT_NEW)
        before = "The frozen Study 38 Walking cohort"
        after = "The real-data GUI replay"
        self.assertEqual(old.split(before, 1)[0], new.split(before, 1)[0])
        self.assertEqual(old.split(after, 1)[1], new.split(after, 1)[1])
        for span in ("0.13--0.14", "0.31--0.33", "1.22--1.25", "3.42--3.66"):
            self.assertIn(span, new)
        self.assertIn("each range spans the two execution-specific\nmedians", new)

    def test_reference_metadata_and_citation_keys(self):
        bib = text("paper/references.bib")
        entries = {k: b for k, b in re.findall(r"@\w+\{([^,]+),(.*?)(?=\n@|\Z)", bib, re.S)}
        self.assertTrue(set(re.findall(r"@([A-Za-z0-9_]+)", self.new)) <= set(entries))
        self.assertIn("author = {Moreira", entries["santos2021controlledspeed"])
        self.assertIn("pages = {103}", entries["santos2021controlledspeed"])
        ramp = entries["vielemeyer2025ramp"]
        for s in ("Abel, Rainer", "Lechler, Knut", "volume = {13}", "pages = {18}", "year = {2026}"):
            self.assertIn(s, ramp)
        sit = entries["perera2024sittowalk"]
        for s in ("A Motion Capture Dataset on Human Sitting to Walking Transitions", "Gopalai, Alpha Agape", "Ahmad, Siti Anom", "pages = {878}"):
            self.assertIn(s, sit)

    def test_study_labels_do_not_change_registry_keys(self):
        builder = load_builder()
        rows = builder.read_csv(ROOT / "paper/tables/table1_public_studies.csv")
        self.assertEqual(rows[0]["study_id"], "study31")
        labels = [re.sub(r"^study_?(\d+)$", r"Study \1", r["study_id"]) for r in rows]
        md = text("paper/tables/table_s1_public_studies.md")
        tex = builder.table_public_studies(rows)
        for label in labels:
            self.assertIn(f"| {label} |", md)
            self.assertIn(label + " & ", tex)
        endpoints = builder.read_endpoint_table(ROOT / "paper/tables/table_s2_validation_endpoints.md")
        self.assertEqual([r["id"] for r in endpoints], list("ABCDEFGH"))
        self.assertIn("source-file links retained", endpoints[0]["result"])
        self.assertIn("musculoskeletal analysis", endpoints[4]["question"])
        self.assertIn("four query results", endpoints[7]["result"])

    def test_published_artifacts_match_current_version(self):
        if text("paper/MANUSCRIPT_VERSION").strip() != VERSION:
            self.skipTest("Current manuscript has advanced.")
        for suffix, current in (("md", "paper/paper.md"), ("tex", "paper/cmpb/manuscript.tex"), ("pdf", "paper/cmpb/manuscript.pdf")):
            self.assertEqual((ROOT / f"paper/versions/harmo-semg_manuscript_{VERSION}.{suffix}").read_bytes(), (ROOT / current).read_bytes())
        manifest = json.loads(text("paper/cmpb/manifest.json"))
        self.assertEqual(manifest["manuscript_version"], VERSION)
        content = json.dumps(manifest)
        for source in (SUPPLEMENT_NEW, "paper/results/validation/full_record_materialization_benchmark.json", "paper/results/validation/mapping_outcomes.json"):
            self.assertIn(source, content)


if __name__ == "__main__":
    unittest.main()
