"""Package main manuscripts with supplementary displays and separate Methods."""
from pathlib import Path
import argparse, json, importlib.util
from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parents[2]

def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/paper" / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    v = args.version
    output = ROOT / "output/pdf" / v
    stage = ROOT / "tmp/pdfs" / v / "parts"
    output.mkdir(parents=True, exist_ok=True)
    stage.mkdir(parents=True, exist_ok=True)
    main_builder = module("main_builder", "build_scientific_reuse_pdf_v023.py")
    supplement = module("supplement_builder", "build_scientific_reuse_supplement_v031.py")
    main_builder.VERSION = main_builder.SOURCE_VERSION = v
    main_builder.BUILD = ROOT / "tmp/pdfs" / v
    main_builder.OUTPUT = stage
    main_builder.CHECK_CITATION_ORDER = True
    main_builder.AUTHOR_METADATA = json.loads((ROOT / "paper/revision_plans/manuscript_authors_v002_2026.10.01-r3.json").read_text())
    records = []
    for lang in ["en", "ja"]:
        main_record = main_builder.build(lang)
        main_path = ROOT / main_record["pdf"]
        main_pages = len(PdfReader(main_path).pages)
        display_record = supplement.build(v, lang, part="figures_tables", first_page=main_pages+1, output_dir=stage)
        display_path = ROOT / display_record["pdf"]
        methods_record = supplement.build(v, lang, part="methods", output_dir=output)
        writer = PdfWriter()
        writer.append(main_path, outline_item="Main manuscript", import_outline=True)
        writer.append(display_path, outline_item="Supplementary tables and figures", import_outline=True)
        writer.add_metadata({"/Title":"A purpose-dependent framework for harmonizing and reusing public electromyography data",
                             "/Author":"Kosuke Ota; Masaaki Okabe; Yusuke Matsui",
                             "/Creator":"LaTeX", "/Producer":"LaTeX and pypdf"})
        target = output / f"harmo-semg_cmpb_{lang}_{v}.pdf"
        with target.open("wb") as f:
            writer.write(f)
        merged = PdfReader(target)
        expected = [p.extract_text() for p in PdfReader(main_path).pages] + [p.extract_text() for p in PdfReader(display_path).pages]
        assert [p.extract_text() for p in merged.pages] == expected
        record = dict(language=lang, main_pdf=str(target.relative_to(ROOT)),
                      main_pages=main_pages, appended_pages=len(merged.pages)-main_pages,
                      total_pages=len(merged.pages),
                      supplementary_methods=methods_record,
                      main_warnings=main_record["warnings"], display_warnings=display_record["warnings"],
                      citation_order=main_record["checked_first_citation_order"],
                      merged_text_preserved=True)
        records.append(record)
    report = dict(version=v, scope="Main manuscript plus supplementary tables and figures; supplementary Methods separate", outputs=records)
    (output/"build_manifest.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__ == "__main__":
    main()
