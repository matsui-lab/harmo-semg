#!/usr/bin/env python3
"""Build the versioned Japanese companion without changing the English build.

Translation prose is reviewed Markdown, not machine translation at build time.
Original figure PDFs and the English bibliography are reused without alteration.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]
REVISIONS = {
    "v001_2026.09.26-r1": (
        "v026_2026.09.25-r1",
        "paper/supplementary_methods_v024_2026.09.16-r12.md",
        "paper/supplementary_results_v026_2026.09.25-r1.md",
    ),
    "v002_2026.09.26-r1": (
        "v027_2026.09.26-r1",
        "paper/supplementary_methods_v027_2026.09.26-r1.md",
        "paper/supplementary_results_v027_2026.09.26-r1.md",
    ),
    "v003_2026.09.26-r2": (
        "v028_2026.09.26-r2",
        "paper/supplementary_methods_v027_2026.09.26-r1.md",
        "paper/supplementary_results_v027_2026.09.26-r1.md",
    ),
    "v004_2026.09.26-r3": (
        "v029_2026.09.26-r3",
        "paper/supplementary_methods_v029_2026.09.26-r3.md",
        "paper/supplementary_results_v029_2026.09.26-r3.md",
    ),
    "v005_2026.09.26-r4": (
        "v030_2026.09.26-r4",
        "paper/supplementary_methods_v029_2026.09.26-r3.md",
        "paper/supplementary_results_v029_2026.09.26-r3.md",
    ),
    "v006_2026.09.26-r5": (
        "v031_2026.09.26-r5",
        "paper/supplementary_methods_v029_2026.09.26-r3.md",
        "paper/supplementary_results_v029_2026.09.26-r3.md",
    ),
    "v007_2026.09.26-r6": (
        "v032_2026.09.26-r6",
        "paper/supplementary_methods_v032_2026.09.26-r6.md",
        "paper/supplementary_results_v029_2026.09.26-r3.md",
    ),
    "v008_2026.09.26-r7": (
        "v033_2026.09.26-r7",
        "paper/supplementary_methods_v033_2026.09.26-r7.md",
        "paper/supplementary_results_v029_2026.09.26-r3.md",
    ),
    "v009_2026.09.27-r1": (
        "v034_2026.09.27-r1",
        "paper/supplementary_methods_v034_2026.09.27-r1.md",
        "paper/supplementary_results_v029_2026.09.26-r3.md",
    ),
    "v010_2026.09.27-r2": (
        "v035_2026.09.27-r2",
        "paper/supplementary_methods_v034_2026.09.27-r1.md",
        "paper/supplementary_results_v029_2026.09.26-r3.md",
    ),
    "v011_2026.09.27-r3": (
        "v036_2026.09.27-r3",
        "paper/supplementary_methods_v036_2026.09.27-r3.md",
        "paper/supplementary_results_v029_2026.09.26-r3.md",
    ),
    "v012_2026.09.27-r4": (
        "v037_2026.09.27-r4",
        "paper/supplementary_methods_v036_2026.09.27-r3.md",
        "paper/supplementary_results_v029_2026.09.26-r3.md",
    ),
}
DEFAULT_VERSION = "v012_2026.09.27-r4"


def configure(version):
    """Select a version without modifying an earlier published companion."""
    global VERSION, ENGLISH_VERSION, SOURCE, STEM, METHODS_SOURCE, RESULTS_SOURCE
    VERSION = version
    ENGLISH_VERSION, methods, results = REVISIONS[version]
    SOURCE = ROOT / "paper/japanese" / VERSION
    STEM = f"harmo-semg_manuscript_ja_{VERSION}"
    METHODS_SOURCE, RESULTS_SOURCE = ROOT / methods, ROOT / results


configure(DEFAULT_VERSION)
MODULE_SPEC = importlib.util.spec_from_file_location(
    "english_manuscript_builder", ROOT / "scripts/paper/build_cmpb_latex.py"
)
EN = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(EN)


def read(path):
    return path.read_text(encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def refs(text):
    for num, label in (("S1", "public-studies"), ("S2", "validation-endpoints"),
                       ("S3", "additional-evaluations"), ("1", "purpose-specific-rules")):
        text = re.sub(rf"(?<![A-Za-z])表{num}(?!\d)",
                      lambda _: rf"表~\ref{{tab:{label}}}", text)
    for num in ("S1", "S2", "1", "2", "3"):
        text = re.sub(rf"図{num}(?!\d)", lambda _: rf"図~\ref{{fig:{num}}}", text)
    return text


def fragment(text):
    return refs(EN.pandoc_fragment(text, ROOT))


def parse_table(path):
    text = read(path)
    title = text.splitlines()[0].split("．", 1)[1]
    lines = [line for line in text.splitlines() if line.startswith("|")]
    cells = lambda line: [cell.strip() for cell in line.strip("|").split("|")]
    headers = cells(lines[0])
    rows = [cells(line) for line in lines[2:]]
    assert all(len(row) == len(headers) for row in rows), path
    note = text.split(lines[-1], 1)[1].strip()
    return title, headers, rows, note


def table(path, label, spec, *, main=False, size=r"\small"):
    title, headers, rows, note = parse_table(path)
    introduction = read(path).split("\n", 1)[1].split("\n|", 1)[0].strip()
    if introduction:
        title += "。" + " ".join(introduction.split())
    environment = "table*" if main else "table"
    out = [rf"\begin{{{environment}}}[{'t' if main else '!ht'}]",
           rf"\caption{{{EN.tex_escape(title)}}}", rf"\label{{tab:{label}}}",
           r"\centering", size, r"\setlength{\tabcolsep}{3pt}",
           r"\renewcommand{\arraystretch}{1.22}",
           rf"\begin{{tabularx}}{{\textwidth}}{{{spec}}}", r"\toprule",
           " & ".join(EN.tex_escape(cell) for cell in headers) + r" \\", r"\midrule"]
    for row in rows:
        out.append(" & ".join(
            EN.doi_links(cell) if label == "public-studies" and i in (2, 3)
            else fragment(cell)
            for i, cell in enumerate(row)) + r" \\")
    out += [r"\bottomrule", r"\end{tabularx}", rf"\end{{{environment}}}"]
    if note:
        out.append(r"\noindent\small " + fragment(note))
    return "\n".join(out)


def captions():
    pattern = r"^## 図(S?\d+)．([^\n]+)\n\n(.*?)(?=^## 図|\Z)"
    return {num: (title, body.strip()) for num, title, body in
            re.findall(pattern, read(SOURCE / "captions.md"), re.M | re.S)}


def figure(num, title, text):
    if num in ("2", "3"):
        return "\n".join([
            r"\begin{figure*}[p]\centering",
            rf"\makebox[\textwidth][c]{{\includegraphics[width=190mm,height=0.90\textheight,keepaspectratio]{{Fig{num}.pdf}}}}",
            rf"\caption{{{fragment(title)}。説明は次ページに示す。}}",
            rf"\label{{fig:{num}}}", r"\end{figure*}",
            r"\begin{figure*}[t]",
            rf"\small\textbf{{図{num}（続き）}}\par\medskip", fragment(text),
            r"\end{figure*}"])
    return "\n".join([
        r"\begin{figure*}[p]\centering",
        rf"\makebox[\textwidth][c]{{\includegraphics[width=190mm]{{Fig{num}.pdf}}}}",
        rf"\caption{{{fragment(' '.join((title + '。' + text).split()))}}}", rf"\label{{fig:{num}}}",
        r"\end{figure*}"])


def validate_sources():
    english = ROOT / "paper/versions" / f"harmo-semg_manuscript_{ENGLISH_VERSION}.md"
    if not english.exists():
        assert read(ROOT / "paper/MANUSCRIPT_VERSION").strip() == ENGLISH_VERSION
        english = ROOT / "paper/paper.md"
    en_text, ja_text = read(english), read(SOURCE / "manuscript.md")
    if read(ROOT / "paper/MANUSCRIPT_VERSION").strip() == ENGLISH_VERSION:
        assert en_text == read(ROOT / "paper/paper.md")
    citations = lambda text: re.findall(r"@([a-zA-Z][a-zA-Z0-9_-]+)", text)
    assert citations(en_text) == citations(ja_text), "Main citation order changed"
    en_headings = re.findall(r"^(#{1,3}) ", en_text, re.M)
    ja_headings = re.findall(r"^(#{1,3}) ", ja_text, re.M)
    assert en_headings == ja_headings, "Main heading topology changed"
    en_methods = read(METHODS_SOURCE)
    ja_methods = read(SOURCE / "supplementary_methods.md")
    assert citations(en_methods) == citations(ja_methods)
    for file, prefix, count in (("supplementary_methods.md", "M", 9),
                                 ("supplementary_results.md", "S", 7)):
        assert re.findall(rf"\*\*{prefix}(\d+)[．.]", read(SOURCE / file)) == [str(n) for n in range(1, count + 1)]
    assert list(captions()) == ["1", "2", "3", "S1", "S2"]
    for file, count in (("table1.md", 3), ("table_s1.md", 8), ("table_s2.md", 8), ("table_s3.md", 7)):
        assert len(parse_table(SOURCE / "tables" / file)[2]) == count
    en_rows = EN.read_csv(ROOT / "paper/tables/table1_public_studies.csv")
    ja_rows = parse_table(SOURCE / "tables/table_s1.md")[2]
    for en_row, ja_row in zip(en_rows, ja_rows):
        expected = (EN.DISPLAY_LABELS[en_row["study_id"]] + " / " + en_row["study_id"]
                    if VERSION >= "v004" else re.sub(r"^study_?(\d+)$", r"Study \1", en_row["study_id"]))
        assert ja_row[0] == expected
        for index, key in ((1, "dataset"), (2, "article_doi"), (3, "release_doi"),
                           (4, "license"), (6, "enabled_mappings")):
            assert ja_row[index] == en_row[key]
    return {"main_heading_count": len(en_headings), "main_citation_occurrences": len(citations(en_text)),
            "supplementary_methods": 9, "supplementary_results": 7,
            "figure_count": 5, "table_rows": [3, 8, 8, 7], "english_version": ENGLISH_VERSION}


def build_tex():
    markdown = read(SOURCE / "manuscript.md")
    title = EN.extract_title(markdown)
    converted = EN.pandoc_fragment(markdown, ROOT)
    match = re.search(r"\\section\{要旨\}\\label\{abstract\}\s+(.*?)\s*(?=\\section\{序論\})", converted, re.S)
    assert match, "Abstract not found"
    abstract = match.group(1)
    body = converted[match.end():]
    body = re.sub(r"\\href\{tables/table1\.md\}\{.*?\}", lambda _: table(
        SOURCE / "tables/table1.md", "purpose-specific-rules", r"@{}p{24mm}YYY@{}", main=True), body)
    body = refs(body)
    all_captions = captions()
    nums = iter(("1", "2", "3"))
    body, count = re.subn(r"\\begin\{figure\}.*?\\end\{figure\}",
                         lambda _: figure((num := next(nums)), *all_captions[num]), body, flags=re.S)
    assert count == 3
    supplements = [r"\clearpage\onecolumn\nolinenumbers",
                   r"\setcounter{table}{0}\renewcommand{\thetable}{S\arabic{table}}",
                   r"\renewcommand{\theHtable}{supp.\arabic{table}}",
                   r"\section*{補足表}",
                   table(SOURCE / "tables/table_s1.md", "public-studies",
                         r"@{}>{\raggedright\arraybackslash}p{27mm}Y>{\raggedright\arraybackslash}p{21mm}>{\raggedright\arraybackslash}p{27mm}lcr>{\raggedright\arraybackslash}p{22mm}@{}",
                         size=r"\scriptsize"),
                   r"\clearpage",
                   table(SOURCE / "tables/table_s2.md", "validation-endpoints",
                         r"@{}c>{\raggedright\arraybackslash}p{30mm}>{\raggedright\arraybackslash}p{27mm}>{\raggedright\arraybackslash}p{48mm}Y@{}",
                         size=r"\small"), r"\clearpage",
                   table(SOURCE / "tables/table_s3.md", "additional-evaluations", r"@{}>{\raggedright\arraybackslash}p{24mm}YYYY@{}"),
                   r"\clearpage\normalsize",
                   r"\setcounter{figure}{0}\renewcommand{\thefigure}{S\arabic{figure}}",
                   r"\renewcommand{\theHfigure}{supp.\arabic{figure}}",
                   r"\section*{補足図}"]
    for num in ("S1", "S2"):
        title_cap, text = all_captions[num]
        supplements.extend([
            r"\begin{figure}[!ht]\centering",
            rf"\includegraphics[width=\textwidth]{{Fig{num}.pdf}}",
            rf"\caption{{{fragment(' '.join((title_cap + '。' + text).split()))}}}", rf"\label{{fig:{num}}}",
            r"\end{figure}\clearpage"])
    for name, label in (("methods", "supplementary-methods"), ("results", "supplementary-results")):
        contents = read(SOURCE / f"supplementary_{name}.md")
        heading, extra = contents.split("\n", 1)
        supplementary_text = fragment(extra)
        if int(VERSION[1:4]) >= 9:
            supplementary_text = re.sub(
                r"(?m)^(\\textbf\{[MS]\d+．[^\n]*\})$",
                lambda match: match.group(1) + r"\par\nopagebreak[4]",
                supplementary_text,
            )
        supplements.extend([r"\clearpage\normalsize", rf"\section*{{{heading[2:]}}}",
                            rf"\label{{sec:{label}}}", supplementary_text])
    preamble = r"""\documentclass[final,5p,times,twocolumn]{elsarticle}
\usepackage{luatexja-fontspec}
\setmainfont[Path=/System/Library/Fonts/Supplemental/,BoldFont={Times New Roman Bold.ttf},ItalicFont={Times New Roman Italic.ttf},BoldItalicFont={Times New Roman Bold Italic.ttf}]{Times New Roman.ttf}
\setmainjfont[BoldFont=HaranoAjiMincho-Bold.otf,ItalicFont=HaranoAjiMincho-Regular.otf,BoldItalicFont=HaranoAjiMincho-Bold.otf,Scale=0.95]{HaranoAjiMincho-Regular.otf}
\setsansjfont[BoldFont=HaranoAjiGothic-Bold.otf,ItalicFont=HaranoAjiGothic-Regular.otf,BoldItalicFont=HaranoAjiGothic-Bold.otf,Scale=0.95]{HaranoAjiGothic-Regular.otf}
\usepackage{graphicx,booktabs,tabularx,array,longtable,url}
\usepackage[hidelinks,unicode]{hyperref}
\usepackage{microtype}
\usepackage[switch]{lineno}
\providecommand{\tightlist}{\setlength{\itemsep}{0pt}\setlength{\parskip}{0pt}}
\newcolumntype{Y}{>{\raggedright\arraybackslash}X}
\urlstyle{same}
\def\UrlBreaks{\do\/\do\-\do\_\do\.}
\Urlmuskip=0mu plus 1mu
\emergencystretch=2em
\modulolinenumbers[5]
\journal{Computer Methods and Programs in Biomedicine}
\biboptions{sort&compress}
\abstracttitle{要旨}
\keywordtitle{キーワード}
\renewcommand{\figurename}{図}
\renewcommand{\tablename}{表}
\renewcommand{\refname}{参考文献}
\renewcommand{\baselinestretch}{1.16}
\begin{document}
\begin{frontmatter}
"""
    return "\n".join([
        preamble, rf"\title{{{EN.tex_escape(title)}}}",
        rf"\tnotetext[translation]{{日本語参考版 {VERSION.replace('_', ' ')}。英語版 {ENGLISH_VERSION.replace('_', ' ')} に対応。図中の表記と参考文献は原文を保持。}}",
        r"\author[aff1]{著者一覧は確認待ち\corref{cor1}}",
        r"\cortext[cor1]{責任著者情報は確認待ち。}", r"\address[aff1]{所属情報は確認待ち。}",
        r"\begin{abstract}", abstract, r"\end{abstract}",
        r"\begin{keyword}筋電図 \sep データハーモナイゼーション \sep 科学的出典情報 \sep 再現性 \sep データ再利用 \sep 生物医学情報学\end{keyword}",
        r"\end{frontmatter}\linenumbers", body,
        r"\input{references_en.bbl}", *supplements, r"\end{document}", ""])


def build(output):
    validation = validate_sources()
    output.mkdir(parents=True, exist_ok=True)
    source_paths = [*SOURCE.rglob("*.md"),
                    (ROOT / "paper/versions" / f"harmo-semg_manuscript_{ENGLISH_VERSION}.md"
                     if (ROOT / "paper/versions" / f"harmo-semg_manuscript_{ENGLISH_VERSION}.md").exists()
                     else ROOT / "paper/paper.md"),
                    ROOT / "paper/references.bib", ROOT / "paper/cmpb/manuscript.bbl",
                    METHODS_SOURCE, RESULTS_SOURCE]
    for num, path in {**EN.FIGURE_SOURCES, **EN.SUPPLEMENTARY_FIGURE_SOURCES}.items():
        shutil.copy2(ROOT / path, output / f"Fig{num}.pdf")
        source_paths.append(ROOT / path)
    shutil.copy2(ROOT / "paper/cmpb/manuscript.bbl", output / "references_en.bbl")
    shutil.copy2(ROOT / "paper/references.bib", output / "references.bib")
    (output / "manuscript.tex").write_text(build_tex(), encoding="utf-8")
    cache = output / "tex-cache"
    cache.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update(TEXMFVAR=str(cache), TEXMFCACHE=str(cache), SOURCE_DATE_EPOCH="946684800", FORCE_SOURCE_DATE="1", TZ="UTC")
    for pass_index in range(3):
        result = subprocess.run(["lualatex", "-interaction=nonstopmode", "-halt-on-error", "manuscript.tex"],
                                cwd=output, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (output / f"build-{pass_index + 1}.txt").write_text(result.stdout, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(result.stdout[-8000:])
    log = read(output / "manuscript.log")
    warnings = [line for line in log.splitlines() if any(marker in line for marker in (
        "Overfull", "Missing character", "undefined", "Font Warning", "Float too large"))]
    manifest = {"translation_version": VERSION, "source_version": ENGLISH_VERSION,
                "checks": validation, "layout_warnings": warnings,
                "sources": {str(path.relative_to(ROOT)): sha(path) for path in source_paths},
                "outputs": {name: sha(output / name) for name in ("manuscript.pdf", "manuscript.tex")}}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "checks": validation, "warnings": warnings}, ensure_ascii=False, indent=2))


def publish(output):
    """Preserve the published snapshot: require a new version for changed output."""
    for extension, source in (("pdf", output / "manuscript.pdf"), ("tex", output / "manuscript.tex"),
                              ("md", SOURCE / "manuscript.md")):
        target = ROOT / "paper/versions" / f"{STEM}.{extension}"
        if target.exists() and sha(target) != sha(source):
            raise FileExistsError(f"Refusing to overwrite versioned output: {target}")
    destination = SOURCE / "build"
    destination.mkdir(exist_ok=True)
    for name in ("manuscript.tex", "manuscript.pdf", "manifest.json", "references.bib", "references_en.bbl",
                 "Fig1.pdf", "Fig2.pdf", "Fig3.pdf", "FigS1.pdf", "FigS2.pdf"):
        shutil.copy2(output / name, destination / name)
    for extension, source in (("pdf", output / "manuscript.pdf"), ("tex", output / "manuscript.tex"),
                              ("md", SOURCE / "manuscript.md")):
        shutil.copy2(source, ROOT / "paper/versions" / f"{STEM}.{extension}")
    print(f"Published Japanese companion: {STEM}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--version", choices=REVISIONS, default=DEFAULT_VERSION)
    args = parser.parse_args()
    configure(args.version)
    if args.publish:
        publish(args.output.resolve())
    else:
        build(args.output.resolve())


if __name__ == "__main__":
    main()
