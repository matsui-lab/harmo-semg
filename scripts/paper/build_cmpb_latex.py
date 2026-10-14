#!/usr/bin/env python3
"""Build a flat, submission-oriented CMPB LaTeX manuscript directory."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence


FIGURE_SOURCES = {
    1: "paper/figures/editable/figure1_four_panel_v018_2026.09.16-r1.pdf",
    2: "paper/figures/candidates/figure2_implementation_gui_v018_2026.09.26-r1/figure2_implementation_gui_v018_2026.09.26-r1.pdf",
    3: "paper/figures/candidates/figure3_integrated_results_v009_2026.09.26-r1/figure3_integrated_results_v009_2026.09.26-r1.pdf",
}

SUPPLEMENTARY_DIRECTORY = "paper/figures/supplementary_v002_2026.09.26-r1"
SUPPLEMENTARY_FIGURE_SOURCES = {
    "S1": f"{SUPPLEMENTARY_DIRECTORY}/figure_s1_participant_envelopes_v002.pdf",
    "S2": "paper/figures/supplementary_v001_2026.09.16-r12/figure_s2_boundary_processing_v001.pdf",
}
SUPPLEMENTARY_TABLE_S3 = "paper/tables/table_s3_additional_evaluations_v003_2026.09.26-r3.md"

DISPLAY_LABELS = {
    row["internal_id"]: row["figure_label"]
    for row in json.loads((Path(__file__).resolve().parents[2] /
        "paper/dataset_display_names_v001_2026.09.26-r3.json").read_text())["datasets"]
}

KEYWORDS = [
    "Electromyography",
    "Data harmonization",
    "Scientific provenance",
    "Reproducibility",
    "Data reuse",
    "Biomedical informatics",
]

HIGHLIGHTS = [
    "An evidence layer links public EMG to meaning, provenance, and intended use.",
    "All 78 conversion rules reproduced reference values across two environments.",
    "Publications increased supported study-attribute pairs from 24 to 43.",
    "A 130-record cohort was stored and queried with identical repeated outputs.",
    "Unsupported amplitude pooling and mechanics export remained restricted.",
]

REPRODUCIBLE_EPOCH = "946684800"
MANUSCRIPT_VERSION_PATTERN = re.compile(
    r"^v\d{3,}_\d{4}\.\d{2}\.\d{2}-r[1-9]\d*$"
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


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def write_json(path: Path, value: Mapping[str, object]) -> None:
    write_text(
        path,
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
    )


def run(
    command: Sequence[str],
    *,
    cwd: Path,
    input_text: str | None = None,
) -> str:
    environment = os.environ.copy()
    environment.update(
        {
            "FORCE_SOURCE_DATE": "1",
            "SOURCE_DATE_EPOCH": REPRODUCIBLE_EPOCH,
            "TZ": "UTC",
        }
    )
    result = subprocess.run(
        list(command),
        cwd=str(cwd),
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=environment,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Command failed ({}):\n{}\n{}".format(
                " ".join(command),
                result.stdout,
                result.stderr,
            )
        )
    return result.stdout


def pandoc_fragment(markdown: str, root: Path) -> str:
    return run(
        [
            "pandoc",
            "--from=markdown+citations",
            "--to=latex",
            "--natbib",
            "--wrap=none",
        ],
        cwd=root,
        input_text=markdown,
    ).strip()


def tex_escape(value: object) -> str:
    text = str(value)
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(character, character) for character in text)


def breakable_hash(value: str) -> str:
    chunks = [value[index : index + 16] for index in range(0, len(value), 16)]
    return r"{\ttfamily " + r"\allowbreak{}".join(chunks) + "}"


def doi_links(value: str) -> str:
    parts = [item.strip() for item in value.split(";") if item.strip()]
    return r"; ".join(
        r"\href{https://doi.org/"
        + tex_escape(item)
        + r"}{\nolinkurl{"
        + tex_escape(item)
        + "}}"
        for item in parts
    )


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def read_manuscript_version(root: Path) -> str:
    version = (root / "paper/MANUSCRIPT_VERSION").read_text(
        encoding="utf-8"
    ).strip()
    if not MANUSCRIPT_VERSION_PATTERN.fullmatch(version):
        raise ValueError(
            "paper/MANUSCRIPT_VERSION must use vNNN_YYYY.MM.DD-rN"
        )
    return version


def table_public_studies(rows: Sequence[Mapping[str, str]]) -> str:
    body = []
    for row in rows:
        body.append(
            " & ".join(
                [
                    tex_escape(DISPLAY_LABELS[row["study_id"]]) + r"\newline " + tex_escape(row["study_id"]),
                    tex_escape(row["dataset"]),
                    doi_links(row["article_doi"]),
                    doi_links(row["release_doi"]),
                    tex_escape(row["license"]),
                    tex_escape(row["emg"]),
                    tex_escape(row["enabled_mappings"]),
                    tex_escape(row["validation_role"]),
                ]
            )
            + r" \\"
        )
    return "\n".join(
        [
            r"\begin{table*}[p]",
            r"\caption{Public datasets and registered releases used in the validation corpus.}",
            r"\label{tab:public-studies}",
            r"\centering",
            r"\scriptsize",
            r"\setlength{\tabcolsep}{2.2pt}",
            r"\renewcommand{\arraystretch}{1.12}",
            r"\begin{tabularx}{\textwidth}{@{}>{\raggedright\arraybackslash}p{27mm}>{\raggedright\arraybackslash}X"
            r">{\raggedright\arraybackslash}p{21mm}"
            r">{\raggedright\arraybackslash}p{27mm}lcr"
            r">{\raggedright\arraybackslash}p{27mm}@{}}",
            r"\toprule",
            r"Label / internal ID & Public dataset & Article DOI & Release DOI & Licence & EMG & Mappings & Validation role \\",
            r"\midrule",
            *body,
            r"\bottomrule",
            r"\end{tabularx}",
            r"\end{table*}",
        ]
    )


def table_purpose_specific_rules(rows: Sequence[Mapping[str, str]]) -> str:
    body = []
    for row in rows:
        body.append(
            " & ".join(
                [
                    tex_escape(row["intended_use"]),
                    tex_escape(row["additional_evidence_required"]),
                    tex_escape(row["permitted_conditional_resolution"]),
                    tex_escape(row["claim_boundary"]),
                ]
            )
            + r" \\"
        )
    return "\n".join(
        [
            r"\begin{table*}[t]",
            r"\caption{Purpose-specific EMG comparability rules implemented in the current study. The common requirements in the text were applied before these rules.}",
            r"\label{tab:purpose-specific-rules}",
            r"\centering",
            r"\small",
            r"\setlength{\tabcolsep}{4pt}",
            r"\renewcommand{\arraystretch}{1.15}",
            r"\begin{tabularx}{\textwidth}{@{}p{25mm}YYY@{}}",
            r"\toprule",
            r"Intended use & Additional evidence required & Permitted conditional resolution & Claim boundary \\",
            r"\midrule",
            *body,
            r"\bottomrule",
            r"\end{tabularx}",
            r"\end{table*}",
        ]
    )


def read_endpoint_table(path: Path) -> List[Dict[str, str]]:
    """Read publication wording without changing original execution records."""
    lines = [line for line in path.read_text(encoding="utf-8").splitlines()
             if line.startswith("|")]
    keys = ["id", "question", "denominator", "result", "boundary"]
    rows = []
    for line in lines[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != len(keys):
            raise ValueError(f"Invalid endpoint-table row: {line}")
        rows.append(dict(zip(keys, cells)))
    if [row["id"] for row in rows] != list("ABCDEFGH"):
        raise ValueError("Endpoint table must retain experiments A-H")
    return rows


def table_validation_endpoints(rows: Sequence[Mapping[str, str]]) -> str:
    body = []
    for row in rows:
        body.append(
            " & ".join(
                [
                    tex_escape(row["id"]),
                    tex_escape(row["question"]),
                    tex_escape(row["denominator"]),
                    tex_escape(row["result"]),
                    tex_escape(row["boundary"]),
                ]
            )
            + r" \\"
        )
    return "\n".join(
        [
            r"\begin{table*}[p]",
            r"\caption{Prespecified validation endpoints and their interpretation.}",
            r"\label{tab:validation-endpoints}",
            r"\centering",
            r"\scriptsize",
            r"\setlength{\tabcolsep}{2.5pt}",
            r"\renewcommand{\arraystretch}{1.13}",
            r"\begin{tabularx}{\textwidth}{@{}c"
            r">{\raggedright\arraybackslash}p{31mm}"
            r">{\raggedright\arraybackslash}p{27mm}"
            r">{\raggedright\arraybackslash}p{42mm}"
            r">{\raggedright\arraybackslash}X@{}}",
            r"\toprule",
            r"Exp. & Question & Evaluated sample & Result & Interpretation \\",
            r"\midrule",
            *body,
            r"\bottomrule",
            r"\end{tabularx}",
            r"\end{table*}",
        ]
    )


def table_additional_evaluations(root: Path) -> str:
    text = (root / SUPPLEMENTARY_TABLE_S3).read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.startswith("|")]
    body = []
    for line in lines[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 5:
            raise ValueError("Supplementary Table S3 requires five columns")
        body.append(" & ".join(supplementary_references(pandoc_fragment(cell, root)) for cell in cells) + r" \\")
    if len(body) != 7:
        raise ValueError("Supplementary Table S3 requires seven evaluations")
    note = text.split(lines[-1], 1)[1].strip()
    return "\n".join([
        r"\begin{table}[!ht]",
        r"\caption{Additional evaluations, units of observation, and limits.}",
        r"\label{tab:additional-evaluations}",
        r"\centering\small",
        r"\setlength{\tabcolsep}{3pt}",
        r"\renewcommand{\arraystretch}{1.16}",
        r"\begin{tabularx}{\textwidth}{@{}>{\raggedright\arraybackslash}p{24mm}YYYY@{}}",
        r"\toprule",
        r"Evaluation & Data and unit & Change or check & Observed result & Interpretation limit \\",
        r"\midrule",
        *body,
        r"\bottomrule\end{tabularx}",
        r"\end{table}",
        r"\noindent\small " + supplementary_references(pandoc_fragment(note, root)),
    ])


def supplementary_references(text: str) -> str:
    for identifier, label in (("S1", "public-studies"), ("S2", "validation-endpoints"),
                              ("S3", "additional-evaluations")):
        text = text.replace(f"Supplementary Table {identifier}", rf"Supplementary Table~\ref{{tab:{label}}}")
    for identifier in SUPPLEMENTARY_FIGURE_SOURCES:
        text = text.replace(f"Supplementary Figure {identifier}", rf"Supplementary Fig.~\ref{{fig:{identifier}}}")
    return text


def supplementary_figures(root: Path) -> str:
    text = (root / SUPPLEMENTARY_DIRECTORY / "captions.md").read_text(encoding="utf-8")
    captions = dict(re.findall(r"^## Figure (S\d+)\. (.*?)(?=^## Figure |\Z)", text, re.M | re.S))
    blocks = [r"\clearpage", r"\setcounter{figure}{0}",
              r"\renewcommand{\thefigure}{S\arabic{figure}}",
              r"\renewcommand{\theHfigure}{supp.\arabic{figure}}",
              r"\section*{Supplementary figures}"]
    for identifier in SUPPLEMENTARY_FIGURE_SOURCES:
        title, body = captions[identifier].strip().split("\n\n", 1)
        caption = pandoc_fragment(title + ". " + " ".join(body.split()), root)
        blocks.extend([
            r"\begin{figure}[!ht]", r"\centering",
            rf"\includegraphics[width=\textwidth]{{Fig{identifier}.pdf}}",
            rf"\caption{{{caption}}}", rf"\label{{fig:{identifier}}}",
            r"\end{figure}", r"\clearpage",
        ])
    return "\n".join(blocks)


def supplementary_tables(root: Path) -> str:
    """Retain source/endpoint detail outside the main scientific argument."""
    studies = table_public_studies(
        read_csv(root / "paper/tables/table1_public_studies.csv")
    )
    endpoints = table_validation_endpoints(
        read_endpoint_table(root / "paper/tables/table_s2_validation_endpoints.md")
    )

    def one_column_table(text: str) -> str:
        return text.replace(
            r"\begin{table*}[p]", r"\begin{table}[!ht]"
        ).replace(r"\end{table*}", r"\end{table}")

    return "\n".join([
        r"\clearpage",
        r"\onecolumn",
        r"\nolinenumbers",
        r"\setcounter{table}{0}",
        r"\renewcommand{\thetable}{S\arabic{table}}",
        r"\renewcommand{\theHtable}{supp.\arabic{table}}",
        r"\section*{Supplementary tables}",
        one_column_table(studies),
        r"\noindent\small Source identifiers, releases and licences are retained "
        r"for reproducibility. Mapping counts describe predefined conversion "
        r"rules, not participant or distinct-file counts or exhaustive release coverage. "
        r"Labels use public names or first author and year. Internal IDs link "
        r"these labels to the released study settings.",
        r"\clearpage",
        one_column_table(endpoints),
        r"\noindent\small These are the prespecified A--H endpoints, not "
        r"a summary of additional development tests. The centroid "
        r"checks used an author-defined exploratory threshold; a pass is not "
        r"evidence of biological equivalence. The post hoc bootstrap analysis "
        r"is reported separately in Figure~\ref{fig:3}E and the main text.",
        r"\clearpage",
        table_additional_evaluations(root),
        r"\clearpage",
    ])


def parse_figure_captions(path: Path) -> Dict[int, str]:
    text = path.read_text(encoding="utf-8")
    matches = list(
        re.finditer(
            r"^## Figure (\d+)\. (.+?)\n\n(.*?)(?=^## Figure |\Z)",
            text,
            flags=re.MULTILINE | re.DOTALL,
        )
    )
    captions: Dict[int, str] = {}
    for match in matches:
        number = int(match.group(1))
        title = " ".join(match.group(2).split())
        body = " ".join(match.group(3).split())
        captions[number] = f"{title}. {body}".strip()
    return captions


def figure_environment(
    number: int,
    caption_markdown: str,
    root: Path,
) -> str:
    caption = pandoc_fragment(caption_markdown, root)
    if number in (2, 3):
        title, legend = caption_markdown.split('. ', 1)
        return "\n".join([
            r"\begin{figure*}[p]",
            r"\centering",
            rf"\makebox[\textwidth][c]{{\includegraphics[width=190mm,height=0.90\textheight,keepaspectratio]{{Fig{number}.pdf}}}}",
            rf"\caption{{{pandoc_fragment(title, root)}. Legend on the following page.}}",
            rf"\label{{fig:{number}}}",
            r"\end{figure*}",
            r"\begin{figure*}[t]",
            rf"\small\textbf{{Figure {number} (continued).}}\par\medskip",
            pandoc_fragment(legend, root),
            r"\end{figure*}",
        ])
    return "\n".join(
        [
            r"\begin{figure*}[p]",
            r"\centering",
            rf"\makebox[\textwidth][c]{{\includegraphics[width=190mm]{{Fig{number}.pdf}}}}",
            rf"\caption{{{caption}}}",
            rf"\label{{fig:{number}}}",
            r"\end{figure*}",
        ]
    )


def extract_title(markdown: str) -> str:
    match = re.search(r'^title:\s*"(.+)"\s*$', markdown, flags=re.MULTILINE)
    if not match:
        raise ValueError("Manuscript title is missing from YAML front matter")
    return match.group(1)


def replace_once(text: str, pattern: str, replacement: str) -> str:
    updated, count = re.subn(pattern, lambda _: replacement, text, count=1)
    if count != 1:
        raise ValueError(f"Expected exactly one match for {pattern!r}; found {count}")
    return updated


def build_tex(root: Path, output: Path) -> str:
    source_path = root / "paper/paper.md"
    markdown = source_path.read_text(encoding="utf-8")
    title = extract_title(markdown)
    converted = run(
        [
            "pandoc",
            "paper/paper.md",
            "--from=markdown+citations",
            "--to=latex",
            "--natbib",
            "--wrap=none",
        ],
        cwd=root,
    )
    abstract_match = re.search(
        r"\\section\{Abstract\}\\label\{abstract\}\n\n"
        r"(.*?)\n\n"
        r"(?=\\section\{Introduction\})",
        converted,
        flags=re.DOTALL,
    )
    if not abstract_match:
        raise ValueError("Could not isolate the abstract from Pandoc output")
    abstract = abstract_match.group(1).strip()
    body = converted[abstract_match.end() :].lstrip()

    body = replace_once(
        body,
        r"\\href\{tables/table3_purpose_specific_rules\.md\}\{.*?\}",
        table_purpose_specific_rules(
            read_csv(root / "paper/tables/table3_purpose_specific_rules.csv")
        ),
    )

    for number in FIGURE_SOURCES:
        body = body.replace(
            f"Figure {number}",
            rf"Fig.~\ref{{fig:{number}}}",
        )
    body = body.replace(
        "Supplementary Tables S1 and S2",
        r"Supplementary Tables~\ref{tab:public-studies} and~\ref{tab:validation-endpoints}",
    )
    body = body.replace(
        "Supplementary Table S1",
        r"Supplementary Table~\ref{tab:public-studies}",
    )
    body = body.replace(
        "Supplementary Table S2",
        r"Supplementary Table~\ref{tab:validation-endpoints}",
    )
    body = body.replace(
        "Table 1",
        r"Table~\ref{tab:purpose-specific-rules}",
    )

    body = supplementary_references(body)
    body = body.replace("Supplementary Results.", r"Supplementary Results (p.~\pageref{sec:supplementary-results}).")
    body = body.replace("Supplementary Methods", r"Supplementary Methods (p.~\pageref{sec:supplementary-methods})")

    body = re.sub(
        r"\\texttt\{([0-9a-f]{40,64})\}",
        lambda match: breakable_hash(match.group(1)),
        body,
    )

    def break_long_code(match: re.Match[str]) -> str:
        value = match.group(1)
        if any(token in value for token in (r"\_", "/", ".", "-")):
            return r"\path{" + value.replace(r"\_", "_") + "}"
        return match.group(0)

    body = re.sub(
        r"\\texttt\{([^{}]{24,})\}",
        break_long_code,
        body,
    )

    captions = parse_figure_captions(root / "paper/figures/README.md")
    figure_counter = iter(FIGURE_SOURCES)

    def replace_figure(_: re.Match[str]) -> str:
        number = next(figure_counter)
        if number not in captions:
            raise ValueError(f"Missing Figure {number} caption")
        return figure_environment(number, captions[number], root)

    body, figure_count = re.subn(
        r"\\begin\{figure\}.*?\\end\{figure\}",
        replace_figure,
        body,
        flags=re.DOTALL,
    )
    if figure_count != len(FIGURE_SOURCES):
        raise ValueError(f"Expected {len(FIGURE_SOURCES)} manuscript figures; found {figure_count}")

    supplementary = supplementary_tables(root) + "\n" + supplementary_figures(root)
    for section, source, label in (
        ("Supplementary Methods", "paper/supplementary_methods_v036_2026.09.27-r3.md", "supplementary-methods"),
        ("Supplementary Results", "paper/supplementary_results_v029_2026.09.26-r3.md", "supplementary-results"),
    ):
        extra = (root / source).read_text(encoding="utf-8").split("\n", 1)[1].lstrip()
        supplementary += "\n" + "\n".join([
            r"\clearpage",
            r"\normalsize",
            r"\nolinenumbers",
            rf"\section*{{{section}}}",
            rf"\label{{sec:{label}}}",
            re.sub(
                r"(?m)^(\\textbf\{[MS]\d+\.[^\n]*\})$",
                lambda match: match.group(1) + r"\par\nopagebreak[4]",
                supplementary_references(pandoc_fragment(extra, root)),
            ),
        ])
    keyword_text = r" \sep ".join(tex_escape(item) for item in KEYWORDS)
    return "\n".join(
        [
            r"\documentclass[final,5p,times,twocolumn]{elsarticle}",
            "",
            r"\usepackage[T1]{fontenc}",
            r"\usepackage[utf8]{inputenc}",
            r"\usepackage{graphicx}",
            r"\usepackage{booktabs}",
            r"\usepackage{tabularx}",
            r"\usepackage{array}",
            r"\usepackage{longtable}",
            r"\usepackage{url}",
            r"\usepackage[hidelinks]{hyperref}",
            r"\usepackage{microtype}",
            r"\usepackage[switch]{lineno}",
            r"\providecommand{\tightlist}{\setlength{\itemsep}{0pt}\setlength{\parskip}{0pt}}",
            "",
            r"\DeclareUnicodeCharacter{00D7}{\ensuremath{\times}}",
            r"\DeclareUnicodeCharacter{00B0}{\ensuremath{^\circ}}",
            r"\DeclareUnicodeCharacter{2013}{--}",
            r"\DeclareUnicodeCharacter{2264}{\ensuremath{\leq}}",
            "",
            r"\newcolumntype{Y}{>{\raggedright\arraybackslash}X}",
            r"\urlstyle{same}",
            r"\def\UrlBreaks{\do\/\do\-\do\_\do\.}",
            r"\Urlmuskip=0mu plus 1mu",
            r"\emergencystretch=2em",
            r"\modulolinenumbers[5]",
            r"\journal{Computer Methods and Programs in Biomedicine}",
            r"\biboptions{sort&compress}",
            "",
            r"\begin{document}",
            r"\begin{frontmatter}",
            rf"\title{{{tex_escape(title)}}}",
            r"\author[aff1]{Author list pending confirmation\corref{cor1}}",
            r"\cortext[cor1]{Corresponding author details pending confirmation.}",
            r"\address[aff1]{Affiliations pending confirmation.}",
            r"\begin{abstract}",
            abstract,
            r"\end{abstract}",
            r"\begin{keyword}",
            keyword_text,
            r"\end{keyword}",
            r"\end{frontmatter}",
            r"\linenumbers",
            "",
            body.rstrip(),
            "",
            r"\bibliographystyle{elsarticle-num}",
            r"\bibliography{references}",
            "",
            supplementary,
            r"\end{document}",
            "",
        ]
    )


def copy_sources(root: Path, output: Path) -> None:
    shutil.copy2(root / "paper/references.bib", output / "references.bib")
    for number, relative in {**FIGURE_SOURCES, **SUPPLEMENTARY_FIGURE_SOURCES}.items():
        source = root / relative
        if not source.is_file():
            raise FileNotFoundError(source)
        shutil.copy2(source, output / f"Fig{number}.pdf")


def write_highlights(output: Path) -> None:
    invalid = [item for item in HIGHLIGHTS if len(item) > 85]
    if invalid:
        raise ValueError(f"CMPB highlights exceed 85 characters: {invalid}")
    write_text(
        output / "highlights.txt",
        "\n".join(f"- {item}" for item in HIGHLIGHTS) + "\n",
    )


def validate_manuscript_source(root: Path) -> None:
    markdown = (root / "paper/paper.md").read_text(encoding="utf-8")
    match = re.search(
        r"^# Abstract\s+(.*?)(?=^# Introduction)",
        markdown,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise ValueError("Could not identify the manuscript abstract")
    abstract = re.sub(r"\*\*[^*]+:\*\*", "", match.group(1))
    word_count = len(re.findall(r"\b[\w–-]+\b", abstract))
    if word_count > 250:
        raise ValueError(
            f"CMPB abstract exceeds 250 words ({word_count} words)"
        )
    if not 3 <= len(KEYWORDS) <= 6:
        raise ValueError("CMPB requires 3--6 keywords")
    if not 3 <= len(HIGHLIGHTS) <= 5:
        raise ValueError("CMPB highlights require 3--5 bullets")


def compile_manuscript(output: Path) -> None:
    run(
        [
            "latexmk",
            "-pdf",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "manuscript.tex",
        ],
        cwd=output,
    )
    log = (output / "manuscript.log").read_text(
        encoding="utf-8",
        errors="ignore",
    )
    failures = []
    for marker in [
        "LaTeX Warning: There were undefined references",
        "LaTeX Warning: Citation",
        "Overfull \\hbox",
        "Overfull \\vbox",
    ]:
        if marker in log:
            failures.append(marker)
    if failures:
        raise RuntimeError(
            "Compiled manuscript contains blocking layout/reference warnings: "
            + ", ".join(failures)
        )


def compiler_version(command: Sequence[str], root: Path) -> str:
    return run(command, cwd=root).splitlines()[0]


def build_manifest(
    root: Path, output: Path, manuscript_version: str
) -> Dict[str, object]:
    source_paths = [
        root / "paper/MANUSCRIPT_VERSION",
        root / "paper/paper.md",
        root / "paper/supplementary_methods_v036_2026.09.27-r3.md",
        root / "paper/supplementary_results_v029_2026.09.26-r3.md",
        root / SUPPLEMENTARY_TABLE_S3,
        root / SUPPLEMENTARY_DIRECTORY / "captions.md",
        root / "paper/figures/supplementary_v001_2026.09.16-r12/plot_data_v001.json",
        root / "paper/figures/supplementary_v001_2026.09.16-r12/verification_v001.json",
        root / "paper/dataset_display_names_v001_2026.09.26-r3.json",
        root / "scripts/paper/generate_supplementary_figures_v002.R",
        root / "scripts/paper/prepare_supplementary_figures_v001.py",
        root / "scripts/paper/generate_supplementary_figures_v001.R",
        root / "paper/figures/README.md",
        root / "paper/figures/figure1_caption.md",
        root / "paper/figures/FIGURE1_SELECTION.json",
        root / "paper/figures/editable/figure1_four_panel_v018_2026.09.16-r1_editable.pptx",
        root / "paper/figures/figure2_caption.md",
        root / "paper/figures/FIGURE2_SELECTION.json",
        root / "paper/figures/candidates/figure2_implementation_gui_v018_2026.09.26-r1/figure2_implementation_gui_v018_2026.09.26-r1_editable.pptx",
        root / "paper/figures/figure3_caption.md",
        root / "paper/figures/FIGURE3_SELECTION.json",
        root / "paper/figures/candidates/figure3_integrated_results_v009_2026.09.26-r1/figure3_integrated_results_v009_2026.09.26-r1_editable.pptx",
        root / "paper/figures/candidates/figure3_integrated_results_v009_2026.09.26-r1/figure3_integrated_results_v009_2026.09.26-r1_evidence.json",
        root / "paper/results/centroid_bootstrap_v001_2026.09.16-r1/summary.json",
        root / "paper/results/validation/physiology_positive_control.json",
        root / "paper/results/validation/fault_detection.json",
        root / "paper/results/validation/corpus_execution.json",
        root / "paper/results/validation/mapping_outcomes.json",
        root / "paper/results/validation/full_record_materialization_benchmark.json",
        root / "paper/results/registered_reuse_clean_v001_2026.09.13-r1/timing-001/summary.json",
        root / "paper/results/registered_reuse_clean_v001_2026.09.13-r1/timing-001/catalog.binding.json",
        root / "paper/results/registered_reuse_clean_v001_2026.09.13-r1/timing-001/selection.json",
        root / "paper/results/document_field_join_v003_2026.09.15-r3/verification.json",
        root / "paper/results/document_field_join_v003_2026.09.15-r3/REPORT_ja_v001.md",
        root / "paper/results/local_browser_distribution_v001_2026.09.16-r1/REPORT_ja_v001.md",
        root / "scripts/paper/bootstrap_centroid_difference_v001.py",
        root / "paper/references.bib",
        root / "paper/tables/table1_public_studies.csv",
        root / "paper/tables/table2_validation_endpoints.csv",
        root / "paper/tables/table3_purpose_specific_rules.csv",
        root / "paper/tables/table_s1_reporting_evidence.csv",
        root / "paper/tables/table_s1_public_studies.md",
        root / "paper/tables/table_s2_validation_endpoints.md",
        root / "paper/tables/table3_purpose_specific_rules.md",
        root / "scripts/paper/build_cmpb_latex.py",
        *(root / relative for relative in FIGURE_SOURCES.values()),
        *(root / relative for relative in SUPPLEMENTARY_FIGURE_SOURCES.values()),
    ]
    artifact_names = [
        "manuscript.tex",
        "manuscript.pdf",
        "manuscript.bbl",
        "references.bib",
        "highlights.txt",
        *(f"Fig{number}.pdf" for number in FIGURE_SOURCES),
        *(f"Fig{number}.pdf" for number in SUPPLEMENTARY_FIGURE_SOURCES),
    ]
    manifest: Dict[str, object] = {
        "schema_version": "1.0",
        "manuscript_version": manuscript_version,
        "target_journal": "Computer Methods and Programs in Biomedicine",
        "journal_issn": "0169-2607",
        "latex_class": "elsarticle",
        "layout": "final,5p,times,twocolumn",
        "author_metadata_status": "pending-confirmation",
        "requirements": {
            "abstract_max_words": 250,
            "keyword_count": len(KEYWORDS),
            "highlight_count": len(HIGHLIGHTS),
            "highlight_max_characters": 85,
            "figure_width_mm": 190,
            "figure_text_size_policy": "Preserve selected artwork typography; inspect at final width.",
            "flat_submission_directory": True,
        },
        "toolchain": {
            "pandoc": compiler_version(["pandoc", "--version"], root),
            "latexmk": compiler_version(["latexmk", "--version"], root),
            "pdflatex": compiler_version(["pdflatex", "--version"], root),
        },
        "sources": [
            {
                "path": str(path.relative_to(root)),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in source_paths
        ],
        "artifacts": [
            {
                "path": name,
                "bytes": (output / name).stat().st_size,
                "sha256": sha256_file(output / name),
            }
            for name in artifact_names
        ],
    }
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    manifest["result_sha256"] = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()
    return manifest


def write_versioned_snapshot(
    root: Path, output: Path, manuscript_version: str
) -> List[Path]:
    snapshot_directory = root / "paper/versions"
    snapshot_directory.mkdir(parents=True, exist_ok=True)
    sources = {
        "md": root / "paper/paper.md",
        "tex": output / "manuscript.tex",
        "pdf": output / "manuscript.pdf",
    }
    targets = []
    for suffix, source in sources.items():
        target = snapshot_directory / (
            f"harmo-semg_manuscript_{manuscript_version}.{suffix}"
        )
        if target.exists() and sha256_file(target) != sha256_file(source):
            raise ValueError(
                f"Version {manuscript_version} already contains different "
                "content; increment paper/MANUSCRIPT_VERSION before rebuilding"
            )
        shutil.copy2(source, target)
        targets.append(target)
    return targets


def build_zip(output: Path) -> None:
    names = [
        "manuscript.tex",
        "manuscript.pdf",
        "manuscript.bbl",
        "references.bib",
        "highlights.txt",
        "manifest.json",
        *(f"Fig{number}.pdf" for number in FIGURE_SOURCES),
        *(f"Fig{number}.pdf" for number in SUPPLEMENTARY_FIGURE_SOURCES),
    ]
    target = output / "cmpb_submission.zip"
    with zipfile.ZipFile(
        target,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for name in sorted(names):
            data = (output / name).read_bytes()
            info = zipfile.ZipInfo(name)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)


def clean_auxiliary(output: Path) -> None:
    for suffix in [
        ".aux",
        ".blg",
        ".fdb_latexmk",
        ".fls",
        ".log",
        ".out",
        ".spl",
    ]:
        candidate = output / f"manuscript{suffix}"
        if candidate.exists():
            candidate.unlink()


def build(root: Path, output: Path) -> Dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    manuscript_version = read_manuscript_version(root)
    validate_manuscript_source(root)
    copy_sources(root, output)
    write_highlights(output)
    write_text(output / "manuscript.tex", build_tex(root, output))
    compile_manuscript(output)
    manifest = build_manifest(root, output, manuscript_version)
    write_json(output / "manifest.json", manifest)
    build_zip(output)
    write_versioned_snapshot(root, output, manuscript_version)
    clean_auxiliary(output)
    return manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    result.add_argument(
        "--output",
        type=Path,
        default=Path("paper/cmpb"),
    )
    return result


def main() -> int:
    arguments = parser().parse_args()
    root = arguments.root.resolve()
    output = (
        arguments.output.resolve()
        if arguments.output.is_absolute()
        else (root / arguments.output).resolve()
    )
    manifest = build(root, output)
    print(
        json.dumps(
            {
                "output": str(output.relative_to(root)),
                "manuscript_version": manifest["manuscript_version"],
                "artifact_count": len(manifest["artifacts"]),
                "result_sha256": manifest["result_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
