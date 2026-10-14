"""Structural and numerical safeguards for the reviewed Japanese companion."""

from collections import Counter
import importlib.util
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
JA = ROOT / "paper/japanese/v003_2026.09.26-r2"


def read(path):
    return path.read_text(encoding="utf-8")


def numeric_tokens(text):
    text = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)
    text = re.sub(r"\[@[^\]]+\]", "", text)
    text = re.sub(r"\{#[^}]+\}", "", text)
    text = re.sub(r"!\[.*?\]\([^)]*\)", "", text, flags=re.S)
    text = re.sub(r"\]\([^)]*\)", "]", text)
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    return Counter(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)*", text))


class JapaneseManuscriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("ja_builder", ROOT / "scripts/paper/build_japanese_manuscript.py")
        cls.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.builder)
        cls.builder.configure("v003_2026.09.26-r2")
        cls.pairs = [
            (ROOT / "paper/versions/harmo-semg_manuscript_v028_2026.09.26-r2.md", JA / "manuscript.md"),
            (ROOT / "paper/supplementary_methods_v027_2026.09.26-r1.md", JA / "supplementary_methods.md"),
            (ROOT / "paper/supplementary_results_v027_2026.09.26-r1.md", JA / "supplementary_results.md"),
        ]

    def test_heading_citation_table_and_figure_correspondence(self):
        checks = self.builder.validate_sources()
        self.assertEqual(checks["main_heading_count"], 35)
        self.assertEqual(checks["main_citation_occurrences"], 22)

    def test_every_source_paragraph_has_a_translation(self):
        for en, ja in self.pairs:
            with self.subTest(section=ja.name):
                self.assertEqual(len(read(en).split("\n\n")), len(read(ja).split("\n\n")))

    def test_numeric_tokens_retained_in_each_paragraph(self):
        # Written-out English numbers may become Japanese digits. No numerical
        # token from a corresponding source paragraph may disappear, however.
        for en, ja in self.pairs:
            for number, (original, translated) in enumerate(zip(read(en).split("\n\n"), read(ja).split("\n\n"))):
                with self.subTest(section=ja.name, paragraph=number):
                    self.assertFalse(numeric_tokens(original) - numeric_tokens(translated))

    def test_equations_retained(self):
        for en, ja in self.pairs:
            equations = lambda text: Counter(re.sub(r"\s+", "", s) for s in re.findall(r"\$(.*?)\$", text))
            # Japanese grammar reverses "sample i of N" to "N samples, sample i".
            # Every expression must still occur unchanged in the same paragraph.
            for original, translated in zip(read(en).split("\n\n"), read(ja).split("\n\n")):
                self.assertEqual(equations(original), equations(translated))

    def test_reference_bibliography_and_artwork_unchanged(self):
        build = JA / "build"
        if not build.exists():
            self.skipTest("Run again after publishing the checked PDF")
        self.assertEqual((build / "references_en.bbl").read_bytes(), (ROOT / "paper/cmpb/manuscript.bbl").read_bytes())
        for number, path in {**self.builder.EN.FIGURE_SOURCES, **self.builder.EN.SUPPLEMENTARY_FIGURE_SOURCES}.items():
            self.assertEqual((build / f"Fig{number}.pdf").read_bytes(), (ROOT / path).read_bytes())

    def test_provisional_author_metadata_not_inferred(self):
        tex = self.builder.build_tex()
        self.assertIn("著者一覧は確認待ち", tex)
        self.assertIn("責任著者情報は確認待ち", tex)
        self.assertIn("所属情報は確認待ち", tex)
        self.assertIn(r"\documentclass[final,5p,times,twocolumn]{elsarticle}", tex)

    def test_published_snapshots_match(self):
        build = JA / "build"
        if not build.exists():
            self.skipTest("Run again after publishing the checked PDF")
        for suffix in ("pdf", "tex", "md"):
            original = JA / "manuscript.md" if suffix == "md" else build / f"manuscript.{suffix}"
            snapshot = ROOT / "paper/versions" / f"{self.builder.STEM}.{suffix}"
            self.assertEqual(original.read_bytes(), snapshot.read_bytes())


if __name__ == "__main__":
    unittest.main()
