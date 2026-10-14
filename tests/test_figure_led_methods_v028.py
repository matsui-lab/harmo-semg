"""Keep the figure-led revision bounded to Methods and its actual evidence."""

from collections import Counter
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
VERSION = "v028_2026.09.26-r2"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


class FigureLedMethodsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = read("paper/versions/harmo-semg_manuscript_v027_2026.09.26-r1.md")
        cls.new = read("paper/paper.md")
        cls.methods = cls.new.split("# Methods\n", 1)[1].split("# Results\n", 1)[0]

    def test_sections_outside_methods_unchanged(self):
        self.assertEqual(self.old.split("# Methods\n", 1)[0], self.new.split("# Methods\n", 1)[0])
        self.assertEqual(self.old.split("# Results\n", 1)[1], self.new.split("# Results\n", 1)[1])

    def test_four_steps_follow_figure_order(self):
        implementation = self.methods.split("## Implementation\n")[1].split("## Evaluation\n")[0]
        self.assertEqual(re.findall(r"^### Step (\d):", implementation, re.M), ["1", "2", "3", "4"])
        for num in range(1, 5):
            self.assertIn(f"Figure 2A, step {num}", implementation)
        for panel in "BCDE":
            self.assertIn(f"Figure 2{panel}", implementation)
        for panel in "ABCDE":
            self.assertIn(f"Figure 3{panel}", self.methods)

    def test_citations_and_equations_preserved(self):
        self.assertEqual(re.findall(r"@([A-Za-z][A-Za-z0-9_-]+)", self.old), re.findall(r"@([A-Za-z][A-Za-z0-9_-]+)", self.new))
        equation = lambda text: Counter(re.sub(r"\s+", "", eq) for eq in re.findall(r"\$(.*?)\$", text))
        self.assertEqual(equation(self.old), equation(self.new))

    def test_reproducibility_parameters_and_limits_retained(self):
        for expression in ("20--350 Hz", "125-ms", "101 points", "100i/(N-1)",
                           "10,000", "100,000", "120 assessments", "336 expected trials",
                           "160 distinct intervals", "15 percentage", "70%", "post hoc",
                           "not external", "independent validation", "no human confirmations",
                           "not an", "unconfigured URL-to-analysis run", "strict policy",
                           "waveforms", "not amplitude pooling"):
            self.assertIn(expression, self.methods)
        order = re.findall(r"^\d\. .*?\*(.*?)\*", self.methods, re.M)
        self.assertEqual(order, ["not comparable", "unassessed", "conditionally comparable", "comparable"])

    def test_implementation_shorter(self):
        section = lambda text: text.split("## Implementation\n")[1].split("## Evaluation\n")[0]
        self.assertLess(len(section(self.new).split()), len(section(self.old).split()))

    def test_current_version_and_snapshot(self):
        self.assertEqual(read("paper/MANUSCRIPT_VERSION").strip(), VERSION)
        self.assertEqual(read(f"paper/versions/harmo-semg_manuscript_{VERSION}.md"), self.new)


if __name__ == "__main__":
    unittest.main()
