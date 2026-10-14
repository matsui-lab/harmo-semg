#!/usr/bin/env python3
"""Build consolidated supplementary material from versioned manuscript sources."""
from pathlib import Path
import argparse, re, subprocess, os, shutil, json

ROOT = Path(__file__).resolve().parents[2]

def build(version, lang, part="combined", first_page=1, output_dir=None):
    assert part in {"combined", "methods", "figures_tables"}
    source_dir = ROOT / "paper/revision_plans"
    build_dir = ROOT / "tmp/pdfs" / version / ("supplement_" + part + "_" + lang)
    output_dir = Path(output_dir) if output_dir else ROOT / "output/pdf" / version
    build_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    cache = build_dir / "tex-cache"
    cache.mkdir(exist_ok=True)
    env = dict(os.environ, TEXMFVAR=str(cache), TEXMFCACHE=str(cache))
    source_path = source_dir / f"scientific_reuse_supplement_combined_{lang}_{version}.md"
    source = source_path.read_text()
    ja = lang == "ja"
    method_heading, table_heading, figure_heading = (
        ("補足方法", "補足表", "補足図") if ja else
        ("Supplementary Methods", "Supplementary Tables", "Supplementary Figures")
    )
    methods = source.split("# " + method_heading + "\n", 1)[1].split("# " + table_heading + "\n", 1)[0]
    tables = source.split("# " + table_heading + "\n", 1)[1].split("# " + figure_heading + "\n", 1)[0]
    figures = source.split("# " + figure_heading + "\n", 1)[1]

    def fragment(s):
        s = s.replace("\u2011", "-")
        s = re.sub(r"(?<!doi.org/)(10\.\d{4,9}/[A-Za-z0-9._-]+)", lambda m: chr(96) + m.group(1) + chr(96), s)
        text = subprocess.check_output(
            ["/opt/homebrew/bin/pandoc", "-f", "markdown", "-t", "latex", "--wrap=none"],
            input=s, text=True,
        )
        text = text.replace(r"\def\LTcaptype{none}", "")
        text = re.sub(r"\\texttt\{([^}]+)\}", lambda m: r"\nolinkurl{" + m.group(1).replace(r"\_", "_") + "}", text)
        # Avoid a 0.02% overflow caused by rounded Pandoc column fractions.
        text = text.replace(r"\real{0.1429}", r"\real{0.14285}").replace(r"\real{0.1905}", r"\real{0.19047}")
        return text

    preamble = r"""\documentclass[10pt,a4paper]{article}
\usepackage[margin=18mm]{geometry}
\usepackage{luatexja-fontspec}
\setmainfont[Path=/System/Library/Fonts/Supplemental/,BoldFont={Times New Roman Bold.ttf},ItalicFont={Times New Roman Italic.ttf},BoldItalicFont={Times New Roman Bold Italic.ttf}]{Times New Roman.ttf}
\setmainjfont[BoldFont=HaranoAjiMincho-Bold.otf,ItalicFont=HaranoAjiMincho-Regular.otf,BoldItalicFont=HaranoAjiMincho-Bold.otf,Scale=0.95]{HaranoAjiMincho-Regular.otf}
\usepackage{graphicx,booktabs,longtable,array,calc,url}
\usepackage[hidelinks,unicode]{hyperref}
\usepackage{microtype}
\providecommand{\tightlist}{\setlength{\itemsep}{0pt}\setlength{\parskip}{0pt}}
\setlength{\parskip}{4pt}\setlength{\parindent}{0pt}
\setcounter{secnumdepth}{-2}
\setlength{\LTpre}{4pt}\setlength{\LTpost}{6pt}
\renewcommand{\arraystretch}{1.18}
\emergencystretch=2em
\urlstyle{same}\Urlmuskip=0mu plus 1mu
\hypersetup{pdfcreator={LaTeX},pdfproducer={LuaLaTeX},pdfauthor={Kosuke Ota; Masaaki Okabe; Yusuke Matsui}}
\begin{document}
"""
    intro = source.split("# " + method_heading + "\n", 1)[0]
    if part == "methods":
        intro = intro.replace("# Supplementary material", "# Supplementary Methods").replace("# 補足資料", "# 補足本文")
        intro = intro.replace("Supplementary Methods M1–M5, Tables S1–S7 and Figures S1–S5.", "Supplementary Methods M1–M5. Supplementary tables and figures are appended to the main manuscript PDF.")
        intro = intro.replace("補足方法M1–M5、補足表S1–S7、補足図S1–S5。表の見出し・数値は英語版と共通。", "補足方法M1–M5。補足表・補足図は本文PDFの末尾に収録。")
    parts = [preamble, r"\setcounter{page}{" + str(first_page) + "}"]
    if part != "figures_tables":
        parts.extend([fragment(intro), fragment(methods)])
    for chunk in re.split(r"(?=^## Supplementary Table S\d+\.)", tables if part != "methods" else "", flags=re.M):
        if not chunk.strip():
            continue
        number = re.search(r"Table S(\d+)\.", chunk).group(1)
        parts.append(r"\clearpage")
        landscape = number in {"1", "7"}
        if landscape:
            parts.append(r"\paperwidth=297mm\paperheight=210mm\pagewidth=297mm\pageheight=210mm\newgeometry{textwidth=261mm,textheight=174mm,left=18mm,top=18mm}")
        body = fragment(chunk)
        if number == "1":
            widths = iter([0.085, 0.22, 0.17, 0.20, 0.08, 0.035, 0.07, 0.14] * 4)
            body = re.sub(r"\\real\{0.1250\}", lambda m: r"\real{" + str(next(widths)) + "}", body)
        parts.extend([r"\small", body, r"\normalsize"])
        if landscape:
            parts.append(r"\clearpage\paperwidth=210mm\paperheight=297mm\pagewidth=210mm\pageheight=297mm\restoregeometry")
    found = re.findall(r"^## Supplementary Figure S(\d+)\n(.*?)(?=^## Supplementary Figure |^## Related data|^## 関連|\Z)", figures, re.M | re.S)
    assert len(found) == 5, len(found)
    for number, content in found if part != "methods" else []:
        rel = re.search(r"!\[[^]]*\]\(([^)]+)\)", content).group(1)
        img = (source_dir / rel).resolve()
        local = f"FigureS{number}" + img.suffix
        shutil.copy2(img, build_dir / local)
        cap = re.search(r"\*\*(.*?)\*\*(.*)", content, re.S)
        legend = cap.group(2).split("\n## ", 1)[0].strip()
        parts.extend([
            r"\clearpage", r"\section*{Supplementary Figure S" + number + "}",
            r"\begin{center}",
            r"\includegraphics[width=\textwidth,height=.70\textheight,keepaspectratio]{" + local + "}",
            r"\end{center}", fragment("**" + cap.group(1) + "** " + legend),
        ])
    parts.append(r"\end{document}")
    stem = f"harmo-semg_supplement_{lang}_{version}" if part == "combined" else f"harmo-semg_supplement_{part}_{lang}_{version}"
    (build_dir / (stem + ".tex")).write_text("\n\n".join(parts))
    for _ in range(2):
        result = subprocess.run(
            ["/usr/local/bin/lualatex", "-interaction=nonstopmode", "-halt-on-error", stem + ".tex"],
            cwd=build_dir, env=env, text=True, capture_output=True,
        )
        if result.returncode:
            raise RuntimeError(result.stdout[-6000:] + result.stderr[-1000:])
    log = (build_dir / (stem + ".log")).read_text()
    warnings = [line for line in log.splitlines() if any(x in line for x in ["Overfull", "Missing character", "undefined", "Float too large"])]
    for ext in [".pdf", ".tex"]:
        shutil.copy2(build_dir / (stem + ext), output_dir / (stem + ext))
    return dict(language=lang, source=str(source_path.relative_to(ROOT)),
                pdf=str((output_dir / (stem + ".pdf")).relative_to(ROOT)),
                warnings=warnings, methods="M1–M5" if part != "figures_tables" else None,
                tables="S1–S7" if part != "methods" else None, figures="S1–S5" if part != "methods" else None,
                table_cells_changed=False)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    records = [build(args.version, lang) for lang in ["en", "ja"]]
    report = dict(version=args.version, outputs=records, scope="Consolidated supplementary methods, shared English tables and language-specific captions")
    (ROOT / "output/pdf" / args.version / "supplement_build_manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
