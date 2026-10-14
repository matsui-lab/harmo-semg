"""Extract bounded table-scoped phase-axis evidence, without inventing a grid."""
import re
from lxml import etree
from harmo.config.automap import require, digest


def inspect_registered_plot_code(raw):
    """A reviewed fixed-release rule, not a MATLAB parser or code executor."""
    require(digest(raw) == "34ed15619d49620119110c963c3adb6cbcf0c7c102b866649c7b4bcbc9b61a60",
            "unreviewed_plot_code_version")
    lines = raw.decode("utf-8").splitlines()
    plots = [dict(line=i+1, code=line.strip()) for i, line in enumerate(lines)
             if line.strip().startswith("plot(s.Data(")]
    return dict(doi="10.6084/m9.figshare.9759092.v1", sha256=digest(raw),
        rule="reviewed-walking-fig-v1", executed=False, plot_calls=plots,
        explicit_phase_vector=False, grid_status="not_established",
        reason="single-vector plotting and display limits do not define sample-to-phase mapping",
        applicability="Subject6 Walking RX; selected sagittal Ang/Mom/Pwr channels; not Com or all tasks")


def extract(raw, doi):
    require(len(raw) <= 2*1024*1024, "document_size_limit")
    tree = etree.fromstring(raw, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))
    require(doi in tree.xpath(".//article-id[@pub-id-type='doi']/text()"), "article_identity_mismatch")
    text = lambda node: re.sub(r"\s+", " ", "".join(node.itertext())).strip()
    claims = []
    for table in tree.findall(".//table-wrap"):
        scope = None
        for row_index, row in enumerate(table.findall(".//tr")):
            cells = [cell for cell in row if cell.tag in ("td", "th")]
            words = [text(cell) for cell in cells]
            whole = " ".join(words)
            if "Field of s.StandingData structure" in whole:
                scope = "s.StandingData"
                continue
            if "Field of s.Data structure" in whole:
                scope = "s.Data"
                continue
            if scope != "s.Data" or len(words) != 2:
                continue
            field, description = words
            if not re.fullmatch(r"\.(Ang|Mom|Pwr|Com)", field):
                continue
            if re.search(r"\b(not|except|may|might|some|only)\b", description, re.I):
                continue
            shape = re.search(r"Matrix\s*\(\s*(\d+)\s*[×x]\s*(\d+)\s*\)", description)
            if not shape or "columns are the sample points expressed as percentage of the stride duration" not in description:
                continue
            subject = {"Ang": "angles", "Mom": "joint moments", "Pwr": "powers", "Com": "center of mass"}[field[1:]]
            counts = [int(m.group(1)) for paragraph in tree.findall(".//body//p")
                      if subject in text(paragraph).lower()
                      for m in re.finditer(r"time normalized to (\d+) points", text(paragraph))]
            claims.append(dict(field=field[1:], scope=scope, axis_kind="stride_duration_percentage",
                rows=int(shape[1]), columns=int(shape[2]), document_sha256=digest(raw), doi=doi,
                locator="table/"+str(table.get("id"))+"/row/"+str(row_index), quote=description,
                conflicting_prose_counts=sorted(set(n for n in counts if n != int(shape[2]))),
                grid_status="not_established", scientific_use="not_evaluated"))
    return claims


def bind(claims, field, shape, source_sha256, record_key):
    candidates = [c for c in claims if c["field"] == field and c["scope"] == "s.Data"]
    result = dict(source_sha256=source_sha256, record_key=record_key, field=field,
                  shape=list(shape), status="needs_review", grid_status="not_established")
    if not re.fullmatch(r"s\.Data\.\d+", record_key):
        return dict(result, reason="record_scope_mismatch")
    if len(candidates) != 1:
        return dict(result, reason="missing_or_competing_table_claim")
    claim = candidates[0]
    if list(shape) != [claim["rows"], claim["columns"]]:
        return dict(result, reason="document_array_shape_conflict", evidence=claim)
    return dict(result, status="axis_kind_supported_grid_held", axis_kind=claim["axis_kind"], evidence=claim,
                reason="prose_table_count_conflict" if claim["conflicting_prose_counts"] else "exact_grid_not_explicit")
