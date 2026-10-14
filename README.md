# harmo-semg

Purpose-dependent harmonization and reuse of public electromyography data.

This is the curated public release accompanying **A purpose-dependent framework
for harmonizing and reusing public electromyography data**, by Kosuke Ota,
Masaaki Okabe and Yusuke Matsui. It contains the software, tests, processing
protocols, analysis scripts and selected numerical results. The development
repository, manuscript drafts, downloaded source files and review history are
not included.

## What it does

harmo-semg connects recordings to evidence about their channels, anatomy, tasks,
time axes and processing. It checks requirements for a declared use, keeps
unresolved conditions visible, executes supported timing transformations, and
stores linked curves, source references and decisions. Timing and amplitude
comparison are evaluated separately: agreement in file structure does not
establish scientific comparability.

The local browser interface supports evidence review, diagnostic processing and
local storage of prepared projects. URL acquisition is available through the CLI,
not through the current GUI. New studies may require a reader/configuration,
document-to-record mapping and human confirmation. Neither an LLM suggestion nor
a user click establishes scientific validity automatically.

## Install and start

Use Python 3.12 in a separate environment. From this directory:

```sh
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install '.[timing,c3d]'
harmo-local
```

Choose **Try a demonstration** for a synthetic example, or open a prepared
`.harmo` project. Real study data is not distributed with the software. Processing
and storage run on your own computer; the browser connects to a local service.
Quit from the interface when finished. Do not expose the service to the internet.

For command-line options, run `harmo --help`. Selected workflow documentation is
in `docs/`; `DOCUMENTATION.md` also describes the earlier configuration-driven
pipeline. R/PhysioIO, OpenSim, Spark and local language models are optional external
components, not installed by the basic command above. Support for a format or
repository is not evidence of automatic semantic interpretation for every study.

## Reproduce the numerical displays

The checked-in `paper/results/` files are historical numerical outputs, not new
experiments run at release time. Figures 3 and 4 can be regenerated from the
participant-level results without downloading raw data:

```sh
Rscript -e 'install.packages(c("ggplot2", "jsonlite", "patchwork"), repos="https://cloud.r-project.org")'
Rscript scripts/paper/plot_scientific_reuse_figures3_4_v001.R \
  paper/results/scientific_reuse_activity_distribution_v001_2026.09.30-r1/report.json \
  paper/results/scientific_reuse_common_processing_v001_2026.09.28-r1/report.json \
  reproduced_figures
```

The output directory must not already exist. Public source identifiers, versions,
licenses and processing choices are recorded in `paper/source_releases.yaml`,
`paper/physiology_positive_control_protocol.yaml` and the included protocols.
Raw-data reanalysis requires obtaining those sources under their original terms
and preparing the expected inputs. This archive is not a self-contained raw-data
mirror or a one-command reproduction of every historical experiment. Some
development scripts/tests refer to excluded inputs or manuscript assets.

## Verification and scope

`RELEASE_CHECKS.json` records the tests performed on this public snapshot.
`SOURCE_MANIFEST.json` distinguishes copied scientific source from redacted local
path metadata; package version metadata is updated to 0.3.1. A release test is not
an independent replication of the paper's scientific results. The two-study timing
comparison does not establish population equivalence, clinical validity, or
amplitude comparability. Windows and Linux require native acceptance testing.

## Citation and license

- Public repository: https://github.com/matsui-lab/harmo-semg-public
- Archived release: https://doi.org/10.5281/zenodo.23362516
- Software authors: Kosuke Ota; Masaaki Okabe; Yusuke Matsui.

See `CITATION.cff`. Original software is licensed under Apache-2.0. Third-party
datasets and any derived material retain the applicable source license and
attribution requirements; Apache-2.0 does not relicense them. See
`THIRD_PARTY_DATA.md` and the source registry. No original repository data archives,
article full texts, participant demographic tables or manuscript PDFs are bundled.

Supported by JSPS KAKENHI Grant Numbers 26K23912, 23K27934 and 23K18505.
