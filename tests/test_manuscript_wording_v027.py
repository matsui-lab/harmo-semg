"""Guard the language-only v027 revision against scientific drift."""

from collections import Counter
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


class WordingRevisionTests(unittest.TestCase):
    pairs = (
        ("paper/versions/harmo-semg_manuscript_v026_2026.09.25-r1.md", "paper/paper.md"),
        ("paper/supplementary_methods_v024_2026.09.16-r12.md", "paper/supplementary_methods_v027_2026.09.26-r1.md"),
        ("paper/supplementary_results_v026_2026.09.25-r1.md", "paper/supplementary_results_v027_2026.09.26-r1.md"),
    )

    def test_numerical_values_equations_and_citations_unchanged(self):
        for old, new in self.pairs:
            with self.subTest(source=new):
                before, after = read(old), read(new)
                for pattern in (r"\d+(?:[.,]\d+)*", r"\$(.*?)\$", r"@([A-Za-z][A-Za-z0-9_-]+)"):
                    self.assertEqual(Counter(re.findall(pattern, before)), Counter(re.findall(pattern, after)))
                self.assertEqual(re.findall(r"^#{1,3} .*", before, re.M), re.findall(r"^#{1,3} .*", after, re.M))

    def test_evaluation_roles_replace_vague_chronology(self):
        en = read("paper/paper.md")
        ja = read("paper/japanese/v002_2026.09.26-r1/manuscript.md")
        self.assertNotIn("当初", ja)
        for phrase in ("The conversion corpus", "prespecified experiments", "physiological evaluation (Experiment G)", "post hoc", "retrospective controlled tests"):
            self.assertIn(phrase, en)
        for phrase in ("変換評価", "事前規定", "事後", "後ろ向き", "生理学的評価（実験G）"):
            self.assertIn(phrase, ja)

    def test_current_snapshot_and_new_supplements_selected(self):
        self.assertEqual(read("paper/MANUSCRIPT_VERSION").strip(), "v027_2026.09.26-r1")
        self.assertEqual(read("paper/paper.md"), read("paper/versions/harmo-semg_manuscript_v027_2026.09.26-r1.md"))
        builder = read("scripts/paper/build_cmpb_latex.py")
        for name in ("methods", "results"):
            self.assertIn(f"supplementary_{name}_v027_2026.09.26-r1.md", builder)


if __name__ == "__main__":
    unittest.main()
