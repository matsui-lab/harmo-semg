# R Bridge — オプショナルR解析モジュール

## 概要

`harmo.r_bridge` は、R言語による高度な統計解析・可視化機能をオプションとして提供するモジュールです。

**コアパイプライン（ingest → OpenSim → warehouse書込み）はPython単体で完結します。**
R環境がなくても `harmo` の主要機能はすべて利用可能です。

## アーキテクチャ

```
Python (harmo)                         R (r_bridge/scripts/)
┌─────────────┐                       ┌──────────────────────┐
│ ingest      │──→ Iceberg ←──────────│ iceberg_to_physio.R  │
│ analyze     │    warehouse          │ (DuckDB読込)          │
│ warehouse   │    (DuckDB)           │                      │
│ viz (Streamlit)│                    │ analysis_viz.R       │
└─────────────┘                       │ (ggplot2 + NMF)      │
       │                              └──────────────────────┘
       │ subprocess                            ↑
       └──────── harmo r-analyze ──────────────┘
```

## 使い方

### CLI経由

```bash
# 利用可能なRスクリプト一覧
harmo r-analyze

# R環境チェック
harmo r-analyze --check-env

# 解析パイプライン実行
harmo r-analyze analysis_quickstart \
  --warehouse ./warehouse/warehouse \
  --study-id 31 \
  --subject-id 1 \
  --session-id ses-V3-T5 \
  --run-id 20260209_0223_default \
  --n-synergies 4

# 個別スクリプト実行
harmo r-analyze install_packages
```

### Posit Workbench (RStudio Server)

Posit Workbenchで対話的にR解析を行う場合:

```bash
# .Rprofile スニペット生成 + スクリプトへのシンボリックリンク作成
harmo r-analyze --setup-posit --warehouse /path/to/warehouse
```

生成された `.Rprofile` スニペットを `~/.Rprofile` に追加すると、
RStudioのコンソールから直接 `connectToIceberg()` 等が使えます。

```r
# RStudio内で:
source(file.path(.harmo_scripts, "iceberg_to_physio.R"))
source(file.path(.harmo_scripts, "analysis_visualization.R"))

con <- connectToIceberg()
pe <- fetchPhysioExperiment(con, study_id = 31, subject_id = 1, session_id = "ses-V3-T5")
visualize_surface_emg(pe)
```

### Python APIから

```python
from harmo.r_bridge import R_AVAILABLE, check_r_available
from harmo.r_bridge.runner import run_r_script, run_analysis_pipeline

# R利用可能性チェック
if R_AVAILABLE:
    result = run_r_script("analysis_quickstart", warehouse_path="./warehouse/warehouse")
```

## R環境のセットアップ

### 必要なRパッケージ

**CRAN:**
- duckdb, DBI, ggplot2, reshape2, NMF, dplyr, tidyr, signal, data.table

**Bioconductor:**
- SummarizedExperiment, S4Vectors, DelayedArray, BiocParallel
- PhysioExperiment（ローカルインストール）

### インストール

```bash
# harmo経由
harmo r-analyze install_packages

# または直接Rで
Rscript /path/to/src/harmo/r_bridge/scripts/install_packages.R
```

## 含まれるRスクリプト

| スクリプト | 説明 |
|-----------|------|
| `iceberg_to_physio.R` | Iceberg → PhysioExperimentオブジェクト変換 |
| `analysis_visualization.R` | EMG可視化 + NMFシナジー解析 |
| `analysis_quickstart.R` | 完全な解析パイプライン |
| `iceberg_to_physio_examples.R` | 使用例集 |
| `install_packages.R` | Rパッケージ一括インストール |

## Rスクリプトの環境変数

| 変数 | 説明 |
|------|------|
| `HARMO_WAREHOUSE_PATH` | Icebergウェアハウスのパス |
| `HARMO_R_PARAMS` | JSON形式の解析パラメータ（CLI経由で自動設定） |
