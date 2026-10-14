#!/usr/bin/env python3
"""Typeset the integrated v023 manuscripts without changing retained submissions."""

from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
VERSION = "v023_2026.10.01-r1"
SOURCE_VERSION = VERSION
AUTHOR_METADATA = None
CHECK_CITATION_ORDER = False
SOURCES = ROOT / "paper/revision_plans"
BUILD = ROOT / "tmp/pdfs" / VERSION
OUTPUT = ROOT / "output/pdf" / VERSION


def run(args, text=None, cwd=ROOT):
    cache = BUILD / "tex-cache"
    cache.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TEXMFVAR=str(cache), TEXMFCACHE=str(cache))
    result = subprocess.run(args, input=text, text=True, capture_output=True, cwd=cwd, env=env)
    if result.returncode:
        raise RuntimeError(result.stdout[-6000:] + result.stderr[-3000:])
    return result.stdout


def fragment(text):
    text = text.replace("\u2011", "-")
    return run(["/opt/homebrew/bin/pandoc", "-f", "markdown", "-t", "latex", "--wrap=none"], text).strip()


def validate_citation_order(body, refs):
    listed = [int(n) for n in re.findall(r"^(\d+)\. ", refs, re.M)]
    assert listed == list(range(1, len(listed) + 1)), listed
    seen = []
    for match in re.finditer(r"\[([\d,\s–-]+)\]|［文献([\d,、\s–-]+)］", body):
        value = match.group(1) or match.group(2)
        for part in re.split(r"[,、]\s*", value):
            ends = [int(n) for n in re.split(r"[–-]", part.strip())]
            numbers = range(ends[0], ends[-1] + 1) if len(ends) == 2 else ends
            for number in numbers:
                assert number in listed, f"Citation absent from bibliography: {number}"
                if number not in seen:
                    seen.append(number)
    assert seen == listed, f"References must follow first citation order: {seen}"
    return seen


def split_source(path):
    source = path.read_text()
    abstract = source.split("## Abstract\n", 1)[1].split("# 1. Introduction", 1)[0].strip()
    body = "# 1. Introduction" + source.split("# 1. Introduction", 1)[1].split("# References", 1)[0]
    body = re.sub(r"^(#{1,2}) \d+(?:\.\d+)?\. ", r"\1 ", body, flags=re.M)
    refs = source.split("# References\n", 1)[1].split("# Figures" if "# Figures" in source else "# 主図", 1)[0]
    figures = []
    for number, content in re.findall(r"^## Figure (\d+)\n(.*?)(?=^## Figure |^# Tables|^# 表|\Z)", source, re.M | re.S):
        image_path = re.search(r"!\[[^]]*\]\(([^)]+)\)", content).group(1)
        caption = re.search(r"\*\*(.*?)\*\*(.*)", content, re.S)
        title, legend = caption.group(1), caption.group(2).strip()
        title = re.sub(r"^(?:Figure \d+\. |図\d+ )", "", title)
        figures.append((int(number), (path.parent / image_path).resolve(), title, legend))
    tables = []
    for number, content in re.findall(r"^## Table (\d+)\n(.*?)(?=^## Table |\Z)", source, re.M | re.S):
        title = re.search(r"\*\*(.*?)\*\*", content, re.S).group(1)
        title = re.sub(r"^(?:Table \d+\. |表\d+ )", "", title)
        lines = content.splitlines()
        rows = [line.strip().strip("|").split("|") for line in lines if line.startswith("|")]
        rows = [[cell.strip() for cell in row] for row in rows]
        end = max(i for i, line in enumerate(lines) if line.startswith("|"))
        note = "\n".join(lines[end + 1:]).strip()
        tables.append((int(number), title, rows[0], rows[2:], note))
    assert len(figures) == 4 and len(tables) == 3
    return source, abstract, body, refs, figures, tables


def table_tex(number, title, headers, rows, note):
    widths = {1: [1.0, 1.55, 1.5, 1.5], 2: [0.95, 0.55, 1.25, 2.35], 3: [1.15, 1.3, 1.25, 1.6]}[number]
    total = sum(widths)
    spec = "@{}" + "".join(r">{\hsize=" + f"{w / total * len(widths):.5f}" + r"\hsize\linewidth=\hsize\raggedright\arraybackslash}X" for w in widths) + "@{}"
    line = lambda row: " & ".join(fragment(cell) for cell in row) + r" \\"
    return "\n".join([
        r"\clearpage", r"\begin{table}[p]", r"\centering", r"\small",
        r"\renewcommand{\arraystretch}{1.35}", r"\setlength{\tabcolsep}{5pt}",
        r"\caption{" + fragment(title) + "}", r"\label{tab:" + str(number) + "}",
        r"\begin{tabularx}{\textwidth}{" + spec + "}", r"\toprule",
        line(["**" + h + "**" for h in headers]), r"\midrule",
        *[line(row) for row in rows], r"\bottomrule", r"\end{tabularx}",
        r"\par\vspace{6pt}\begin{minipage}{\textwidth}\footnotesize " + fragment(note) + r"\end{minipage}",
        r"\end{table}", r"\clearpage",
    ])


def build(lang):
    path = SOURCES / f"scientific_reuse_integrated_draft_{lang}_{SOURCE_VERSION}.md"
    source, abstract, body, refs, figures, tables = split_source(path)
    citation_order = validate_citation_order(body, refs) if CHECK_CITATION_ORDER else None
    work = BUILD / lang
    work.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    is_ja = lang == "ja"
    preamble = r"""\documentclass[final,5p,times,twocolumn]{elsarticle}
\usepackage{luatexja-fontspec}
\setmainfont[Path=/System/Library/Fonts/Supplemental/,BoldFont={Times New Roman Bold.ttf},ItalicFont={Times New Roman Italic.ttf},BoldItalicFont={Times New Roman Bold Italic.ttf}]{Times New Roman.ttf}
\setmainjfont[BoldFont=HaranoAjiMincho-Bold.otf,ItalicFont=HaranoAjiMincho-Regular.otf,BoldItalicFont=HaranoAjiMincho-Bold.otf,Scale=0.95]{HaranoAjiMincho-Regular.otf}
\setsansjfont[BoldFont=HaranoAjiGothic-Bold.otf,Scale=0.95]{HaranoAjiGothic-Regular.otf}
\usepackage{graphicx,booktabs,tabularx,array,longtable,url}
\usepackage[hidelinks,unicode]{hyperref}
\usepackage{microtype}
\providecommand{\tightlist}{\setlength{\itemsep}{0pt}\setlength{\parskip}{0pt}}
\emergencystretch=2em
\urlstyle{same}
\Urlmuskip=0mu plus 1mu
\journal{Computer Methods and Programs in Biomedicine}
\hypersetup{pdfcreator={LaTeX},pdfproducer={LuaLaTeX},pdfauthor={}}
\raggedbottom
"""
    if is_ja:
        preamble += r"\abstracttitle{要旨}\keywordtitle{キーワード}\renewcommand{\figurename}{図}\renewcommand{\tablename}{表}\renewcommand{\refname}{参考文献}\renewcommand{\baselinestretch}{1.12}" + "\n"
    title = source.splitlines()[0].removeprefix("# ")
    if SOURCE_VERSION == "v023_2026.10.01-r1" and is_ja:
        title = "公共EMGの再利用方法"
    title_tex = fragment(title)
    if title == "A purpose-dependent framework for harmonizing and reusing public electromyography data":
        title_tex = r"\texorpdfstring{" + title_tex.replace(" and reusing", r"\\and reusing") + "}{" + fragment(title) + "}"
    note = ("日本語確認版。図中の表記は英語版と共通。" if is_ja else "Working manuscript. ") + VERSION.replace("_", " ")
    keywords = ["筋電図", "データハーモナイゼーション", "公開データの再利用", "再現性"] if is_ja else ["Electromyography", "Data harmonization", "Public data reuse", "Reproducibility"]
    author_block = []
    if AUTHOR_METADATA:
        preamble += r"\renewcommand{\theaffn}{\arabic{affn}}" + "\n"
        preamble += r"\hypersetup{pdfauthor={" + "; ".join(a["name"] for a in AUTHOR_METADATA["authors"]) + "}}\n"
        for author in AUTHOR_METADATA["authors"]:
            labels = ",".join("aff" + str(i) for i in author["affiliations"])
            corresponding = author["name"] == AUTHOR_METADATA["corresponding_author"]
            author_block.append(r"\author[" + labels + "]{" + author["name"] + (r"\corref{cor1}" if corresponding else "") + "}")
            if corresponding:
                author_block.append(r"\ead{" + AUTHOR_METADATA["corresponding_author_email"] + "}")
        for aff in AUTHOR_METADATA["affiliations"]:
            author_block.append(r"\address[aff" + str(aff["id"]) + "]{" + fragment(aff["text"]) + "}")
        author_block.append(r"\cortext[cor1]{Corresponding author.}")
    content = [preamble, r"\begin{document}\begin{frontmatter}",
               r"\title{" + title_tex + r"\tnoteref{version}}",
               r"\tnotetext[version]{" + fragment(note) + "}",
               *author_block,
               r"\begin{abstract}", fragment(abstract), r"\end{abstract}",
               r"\begin{keyword}" + r" \sep ".join(keywords) + r"\end{keyword}",
               r"\end{frontmatter}", fragment(body)]
    ref_entries = re.split(r"\n\s*\n", refs.strip())
    content.append(r"\begin{thebibliography}{9}")
    for item in ref_entries:
        match = re.match(r"(\d+)\.\s+(.*)", item, re.S)
        assert match, item
        content.append(r"\bibitem{ref" + match.group(1) + "}" + fragment(match.group(2)))
    content.append(r"\end{thebibliography}\clearpage\onecolumn")
    for number, image_path, fig_title, legend in figures:
        local = f"Fig{number}.png"
        shutil.copy2(image_path, work / local)
        height = ".78" if number == 2 else ".67"
        content.extend([
            r"\begin{figure}[p]\centering",
            r"\includegraphics[width=\textwidth,height=" + height + r"\textheight,keepaspectratio]{" + local + "}",
            r"\caption{" + fragment(fig_title + " " + legend) + "}",
            r"\label{fig:" + str(number) + "}", r"\end{figure}\clearpage",
        ])
    for table in tables:
        content.append(table_tex(*table))
    content.append(r"\end{document}")
    tex = "\n\n".join(content)
    stem = f"harmo-semg_cmpb_{lang}_{VERSION}"
    (work / (stem + ".tex")).write_text(tex)
    for _ in range(2):
        run(["/usr/local/bin/lualatex", "-interaction=nonstopmode", "-halt-on-error", stem + ".tex"], cwd=work)
    log = (work / (stem + ".log")).read_text()
    warnings = [line for line in log.splitlines() if any(x in line for x in ["Overfull", "Missing character", "undefined", "Float too large"])]
    for ext in [".pdf", ".tex"]:
        shutil.copy2(work / (stem + ext), OUTPUT / (stem + ext))
    return {"language": lang, "source": str(path.relative_to(ROOT)), "pdf": str((OUTPUT / (stem + ".pdf")).relative_to(ROOT)), "warnings": warnings,
            "checked_first_citation_order": citation_order,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "abstract_words": len(abstract.split()), "figure_count": len(figures), "table_count": len(tables)}


def main():
    global VERSION, SOURCE_VERSION, BUILD, OUTPUT, AUTHOR_METADATA, CHECK_CITATION_ORDER
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-version", default=VERSION)
    parser.add_argument("--source-version", default=SOURCE_VERSION)
    parser.add_argument("--check-citation-order", action="store_true")
    parser.add_argument("--author-metadata", type=Path)
    args = parser.parse_args()
    VERSION = args.output_version
    SOURCE_VERSION = args.source_version
    CHECK_CITATION_ORDER = args.check_citation_order
    BUILD = ROOT / "tmp/pdfs" / VERSION
    OUTPUT = ROOT / "output/pdf" / VERSION
    if args.author_metadata:
        AUTHOR_METADATA = json.loads(args.author_metadata.read_text())
    outputs = [build(lang) for lang in ["en", "ja"]]
    record = {"version": VERSION, "source_version": SOURCE_VERSION, "template": "elsarticle final 5p twocolumn; LuaLaTeX for Japanese support", "author_metadata": AUTHOR_METADATA or "omitted; formal names and affiliations not inferred", "scope": "Main manuscripts, Figures 1-4 and Tables 1-3; supplements remain separate", "outputs": outputs}
    (OUTPUT / "build_manifest.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
