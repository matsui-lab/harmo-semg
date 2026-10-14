"""Bounded executable rules for explicit PhysioNet protocol descriptions.

These rules are not a general NLP extractor. Dataset names do not select a
rule; document sections, explicit quantifiers and the record shape do.
"""
import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path

VERSION = "html-definitions/0.1.0"


class Sections(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.section = ""
        self.blocks = []
        self.tag = None
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if not self.skip and tag in ("h1", "h2", "h3", "h4", "p"):
            self.tag, self.parts = tag, []

    def handle_data(self, value):
        if self.tag and not self.skip:
            self.parts.append(value)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip-1)
        if tag == self.tag:
            text = re.sub(r"\s+", " ", "".join(self.parts)).strip()
            if tag != "p":
                self.section = text.casefold()
            elif text:
                self.blocks.append(dict(section=self.section, text=text, paragraph=len(self.blocks)))
            self.tag = None


def extract_html_definitions(path, uri, bundle, record_name, header_sha256):
    from harmo.semantic_promotion import FORMAT
    expected_uri = "https://physionet.org/content/" + bundle.study_id + "/"
    if uri != expected_uri:
        raise ValueError("description URL must match the exact dataset release")
    raw = Path(path).read_bytes()
    parser = Sections()
    parser.feed(raw.decode("utf8"))
    sections = [p for p in parser.blocks if p["section"] in ("methods", "data description")]
    rows, origins, rejected = [], {}, []
    emg = [c for c in bundle.channels if re.match(r"^(?:s?emg)(?:\s|$)", c.name, re.I)]
    uncertainty = re.compile(r"\b(?:no|not|never|neither|without|except|some|only|may|might|could|hypothetical|previous|if)\b", re.I)

    def emit(field, value, channel, block, match, rule, scope):
        index = len(rows)
        rows.append(dict(record=record_name, header_sha256=header_sha256, channel=channel, field=field, value=value))
        origins[index] = dict(pointer="paragraph/"+str(block["paragraph"]), section=block["section"],
            quote=block["text"], start=match.start(), end=match.end(), rule=rule, rule_version=VERSION,
            applicability=scope, target_record=record_name, header_sha256=header_sha256)

    # An explicit dataset-level description binds the methods to the versioned
    # file collection. Without this statement, no walking protocol is inherited.
    walking_scope = next((p for p in sections if p["section"] == "data description" and
        "The dataset is composed of walking signals" in p["text"] and
        "For each subject, two WFDB files are provided" in p["text"] and not uncertainty.search(p["text"])), None)
    for block in sections:
        text = block["text"]
        if uncertainty.search(text):
            rejected.append(dict(paragraph=block["paragraph"], reason="uncertainty_negation_or_scope_qualifier"))
            continue
        needle = re.search(r"\b(?:A|An) [^.]*\bneedle electrode was placed into the (?P<muscle>tibialis anterior) muscle of each subject\.", text)
        if block["section"] == "data description" and needle and len(emg) == 1 and len(bundle.channels) == 1:
            channel = emg[0].name
            emit("sensor_type", "needle-emg", channel, block, needle, "each-subject-single-emg", "each subject; one EMG channel in target record")
            name = needle["muscle"]
            emit("anatomy", dict(entity_type="muscle",canonical_label=name,
                 ontology_term=dict(ontology="HarmoSourceTerms",ontology_version="1.0",term_id="tibialis-anterior",label=name)),
                 channel,block,needle,"each-subject-single-emg","each subject; one EMG channel; no inferred laterality")
            task = re.search(r"The patient was then asked to dorsiflex the foot gently against resistance\.",text)
            relax = re.search(r"the patient was asked to relax",text)
            if task and relax:
                emit("task",dict(activity="resisted-dorsiflexion-and-relaxation-protocol",
                    ontology_terms=[dict(ontology="HarmoSourceTerms",ontology_version="1.0",term_id="dorsiflexion-relaxation-protocol",label="resisted dorsiflexion followed by relaxation")],
                    facets={"annotation_scope":"recording-protocol-not-sample-labels"},compatibility_group=None),
                    "",block,task,"explicit-protocol-sequence","same each-subject acquisition paragraph; no event times inferred")
        if walking_scope:
            task = re.search(r"Subjects were asked to walk barefoot on level ground for around 5 min at their natural speed and pace, following an eight-shaped path which included rectilinear segments and curves\.",text)
            if block["section"] == "methods" and task:
                emit("task",dict(activity="walking",ontology_terms=[dict(ontology="HarmoSourceTerms",ontology_version="1.0",term_id="walking",label="walking")],
                    facets={"terrain":"level", "footwear":"barefoot", "speed":"self-selected", "path":"straight-segments-and-curves", "annotation_scope":"recording-protocol-not-sample-labels"},compatibility_group=None),
                    "",block,task,"explicit-walking-protocol", "dataset-wide walking records; straight segments and turns retained")
                origins[len(rows)-1]["scope_evidence"] = walking_scope
            sensor = re.search(r"sEMG signals have been detected with single differential probes",text)
            if block["section"] == "methods" and sensor:
                # sEMG abbreviation must be expanded in this same document.
                expanded = next((p for p in parser.blocks if "surface electromyographic (sEMG)" in p["text"]), None)
                if expanded:
                    for channel in emg:
                        emit("sensor_type","surface-emg",channel.name,block,sensor,"explicit-semgsensor","dataset EMG channels; no electrode geometry equality inferred")
                        origins[len(rows)-1]["scope_evidence"] = walking_scope
                        origins[len(rows)-1]["abbreviation_evidence"] = expanded
            unit = re.search(r"sEMG signals are expressed in ([µμu]V|mV|V)",text)
            if block["section"] == "data description" and unit:
                for channel in emg:
                    emit("unit",unit[1],channel.name,block,unit,"explicit-emg-unit","dataset EMG channels; must agree with header")
    return dict(data=dict(format=FORMAT,dataset=bundle.study_id,definitions=rows),uri=uri,
                sha256=hashlib.sha256(raw).hexdigest(), origins=origins,
                extraction=dict(version=VERSION, rejected=rejected, coverage="bounded-English-protocol-rules; no PDF or arbitrary tables"))
