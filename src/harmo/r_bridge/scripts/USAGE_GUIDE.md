# harmo r_bridge 使用ガイド

> Icebergウェアハウスのデータを PhysioExperiment オブジェクトに変換し、
> 可視化・統計解析を行うためのワークフロー例です。

## 前提条件

```r
# R パッケージのインストール（初回のみ）
source("install_packages.R")

# 関数の読み込み
source("iceberg_to_physio.R")
source("analysis_visualization.R")
```

環境変数 `HARMO_WAREHOUSE_PATH` にウェアハウスのパスを設定するか、
`connectToIceberg()` の引数で直接指定できます。

---

## シナリオ 1: 1トライアルの結果を可視化する

1つのセッション（トライアル）の EMG 信号と OpenSim 解析結果を
それぞれ可視化する例です。

### 1-1. EMG 信号の可視化

```r
# ---- 接続 ----
con <- connectToIceberg()

# ---- データ取得 ----
pe_emg <- fetchPhysioExperiment(
  con        = con,
  study_id   = 31,
  subject_id = 1,
  session_id = "ses-V3-T5",
  table_name = "raw_data",
  sensor_type = "Surface-EMG"
)

# ---- オブジェクト概要 ----
dim(pe_emg)                # [サンプル数, チャネル数]
samplingRate(pe_emg)       # サンプリングレート (Hz)
colnames(pe_emg)           # チャネル名一覧
head(colData(pe_emg))      # チャネルメタデータ (label, type, unit)

# ---- 多チャネル時系列プロット（段差表示）----
plotMultiChannel(pe_emg, style = "stacked", assay_name = "measured")

# ---- 多チャネル蝶形プロット（重ね書き）----
plotMultiChannel(pe_emg, style = "butterfly", assay_name = "measured")

# ---- 単一チャネル表示 ----
# 第1チャネルの信号を表示
plotSignal(pe_emg, channel = 1, assay_name = "measured")
```

### 1-2. OpenSim 解析結果の可視化

```r
# ---- OpenSim 全変数を取得 ----
pe_osim <- fetchOsimPhysioExperiment(
  con        = con,
  run_id     = "20260209_0223_default",
  study_id   = 31,
  subject_id = 1,
  session_id = "ses-V3-T5"
)

dim(pe_osim)          # [時間点, 変数数]
colnames(pe_osim)     # 関節角度・筋活性度 等

# ---- 全変数の蝶形プロット ----
plotMultiChannel(pe_osim, style = "butterfly")

# ---- 筋活性度のみ抽出して可視化 ----
pe_act <- fetchOsimPhysioExperiment(
  con             = con,
  run_id          = "20260209_0223_default",
  study_id        = 31,
  subject_id      = 1,
  session_id      = "ses-V3-T5",
  variable_filter = "%activation%"
)

plotMultiChannel(pe_act, style = "stacked")
```

### 1-3. EMG 可視化 + NMF シナジー解析（一括実行）

`run_analysis_pipeline()` を使うと EMG 可視化と NMF シナジー解析を
一括で実行できます。

```r
run_analysis_pipeline(
  con        = con,
  study_id   = 31,
  subject_id = 1,
  session_id = "ses-V3-T5",
  run_id     = "20260209_0223_default",
  n_synergies = 4,
  emg_output_dir     = "~/output/emg_plots",
  synergy_output_dir = "~/output/synergy_plots"
)

disconnectFromIceberg(con)
```

---

## シナリオ 2: 複数トライアルの結果を可視化する

同一被験者の複数セッションを読み込み、比較可視化する例です。

### 2-1. 複数セッションの一括取得

```r
con <- connectToIceberg()

# ---- 利用可能なセッション一覧 ----
sessions <- listAvailableSessions(con, study_id = 31, subject_id = 1)
print(sessions)

# ---- バッチ取得（全セッション）----
pe_batch <- fetchPhysioExperimentBatch(
  con         = con,
  study_id    = 31,
  subject_id  = 1,
  session_ids = sessions$session_id,
  table_name  = "raw_data",
  sensor_type = "Surface-EMG"
)

names(pe_batch)   # 取得できたセッションIDの一覧
```

### 2-2. セッション間の重ね合わせ比較

```r
library(ggplot2)

# 比較したいセッションを選択
target_sessions <- c("ses-V3-T5", "ses-V3-T6")
channel_idx <- 1   # 比較するチャネル番号

df_compare <- data.frame()
for (sid in target_sessions) {
  pe <- pe_batch[[sid]]
  if (is.null(pe)) next
  
  signal <- assay(pe, "measured")[, channel_idx]
  time   <- rowData(pe)$time
  ch_name <- colnames(pe)[channel_idx]
  
  df_compare <- rbind(df_compare, data.frame(
    time      = time,
    amplitude = signal,
    session   = sid,
    channel   = ch_name,
    stringsAsFactors = FALSE
  ))
}

ggplot(df_compare, aes(x = time, y = amplitude, color = session)) +
  geom_line(alpha = 0.8) +
  labs(
    title = paste("Channel:", df_compare$channel[1], "— Session Comparison"),
    x = "Time (s)", y = "Amplitude"
  ) +
  theme_minimal()
```

### 2-3. OpenSim 結果の複数セッション比較

```r
# ---- 複数セッションの筋活性度を取得して比較 ----
osim_sessions <- c("ses-V3-T5", "ses-V3-T6")

pe_osim_list <- lapply(osim_sessions, function(sid) {
  fetchOsimPhysioExperiment(
    con             = con,
    run_id          = "20260209_0223_default",
    study_id        = 31,
    subject_id      = 1,
    session_id      = sid,
    variable_filter = "%activation%"
  )
})
names(pe_osim_list) <- osim_sessions

# ---- 各セッションの蝶形プロットを並べて表示 ----
par(mfrow = c(1, length(osim_sessions)))
for (sid in osim_sessions) {
  p <- plotMultiChannel(pe_osim_list[[sid]], style = "butterfly")
  print(p + ggtitle(paste("Session:", sid)))
}

disconnectFromIceberg(con)
```

---

## シナリオ 3: 複数トライアルの結果を統計解析する

複数セッション（条件）のデータをエポック化し、
SPM や t 検定、クラスタ順列検定で統計的に比較する例です。

### 3-1. データ準備: エポック化

PhysioExperiment の統計関数はエポック化された 4D データを入力とします。
イベント時刻を基準にデータをセグメント分割します。

```r
con <- connectToIceberg()

# ---- 2 条件のデータを取得 ----
pe_cond1 <- fetchPhysioExperiment(
  con = con, study_id = 31, subject_id = 1,
  session_id = "ses-V3-T5", table_name = "raw_data",
  sensor_type = "Surface-EMG"
)

pe_cond2 <- fetchPhysioExperiment(
  con = con, study_id = 31, subject_id = 1,
  session_id = "ses-V3-T6", table_name = "raw_data",
  sensor_type = "Surface-EMG"
)

# ---- エポック化 ----
# tmin: イベント前 (秒), tmax: イベント後 (秒)
# baseline: ベースライン補正範囲
pe_epoch1 <- epochData(pe_cond1,
                       tmin     = -0.2,
                       tmax     = 0.8,
                       baseline = c(-0.2, 0))

pe_epoch2 <- epochData(pe_cond2,
                       tmin     = -0.2,
                       tmax     = 0.8,
                       baseline = c(-0.2, 0))
```

> **注意**: `epochData()` はオブジェクト内のイベント情報（`events(pe)`）を
> 使用します。イベントが未定義の場合は `events` 引数で直接指定してください。

### 3-2. SPM t 検定

Statistical Parametric Mapping のフレームワークで、
時系列全体に対する統計検定を行います。

```r
# ---- 1 標本 SPM t 検定（1 条件 vs ゼロ）----
spm_result <- spmTTest(pe_epoch1, alpha = 0.05, two_tailed = TRUE)

# ---- 結果の可視化 ----
plotSPM(spm_result, show_threshold = TRUE, show_clusters = TRUE,
        title = "SPM{t}: Condition 1 vs Zero")

# ---- 2 標本 SPM t 検定（条件 1 vs 条件 2）----
spm_result_2s <- spmTTest(pe_epoch1,
                           group1 = pe_epoch1,
                           group2 = pe_epoch2,
                           alpha = 0.05, two_tailed = TRUE)

plotSPM(spm_result_2s, show_threshold = TRUE, show_clusters = TRUE,
        title = "SPM{t}: Condition 1 vs Condition 2")
```

### 3-3. 対応あり SPM t 検定

同一被験者の前後比較など、対応のあるデータに適用します。

```r
# pe_epoch1 と pe_epoch2 は同一被験者の異なる条件
spm_paired <- spmPairedTTest(pe_epoch1,
                              condition1 = pe_epoch1,
                              condition2 = pe_epoch2,
                              alpha = 0.05, two_tailed = TRUE)

plotSPM(spm_paired,
        title = "SPM{t} Paired: Pre vs Post")
```

### 3-4. 時点ごとの t 検定

エポックの各時間点で独立して t 検定を実行します。

```r
t_result <- tTestEpochs(pe_epoch1,
                         condition1 = pe_epoch1,
                         condition2 = pe_epoch2,
                         paired     = FALSE,
                         alternative = "two.sided")

# t_result には各時間点の t 値・p 値が含まれる
print(t_result)
```

### 3-5. クラスタベース順列検定

多重比較補正付きのノンパラメトリック検定です。
電位や筋電図の群間差を検出するために推奨されます。

```r
cluster_result <- clusterPermutationTest(
  pe_epoch1,
  condition1       = pe_epoch1,
  condition2       = pe_epoch2,
  n_permutations   = 1000,
  cluster_threshold = 0.05,
  tail             = 0,    # 0: 両側, 1: 右側, -1: 左側
  seed             = 42
)

# 有意なクラスタの情報を表示
print(cluster_result)
```

### 3-6. ERP プロット（エポック平均波形）

エポック化されたデータから平均波形と信頼区間を可視化します。

```r
# 第1チャネルの ERP（信頼区間 95%）
plotERP(pe_epoch1, channel = 1, ci = 0.95, show_epochs = TRUE)
```

---

## Python CLI からの実行

Python 側から R 解析を呼び出す場合:

```bash
# R 環境の確認
harmo r-analyze --check-env

# 解析の実行
harmo r-analyze \
  --warehouse /path/to/warehouse/warehouse \
  --study-id 31 \
  --subject-id 1 \
  --session-id ses-V3-T5 \
  --run-id 20260209_0223_default \
  --n-synergies 4

# Posit Workbench 用セットアップ
harmo r-analyze --setup-posit
```

---

## 関数リファレンス（クイック）

| 関数 | 用途 |
|------|------|
| `connectToIceberg(warehouse_path)` | DuckDB + Iceberg 接続 |
| `disconnectFromIceberg(con)` | 接続を閉じる |
| `listAvailableStudies(con)` | 利用可能なスタディ一覧 |
| `listAvailableSessions(con, study_id, subject_id)` | セッション一覧 |
| `fetchPhysioExperiment(con, study_id, subject_id, session_id, ...)` | EMG/センサーデータ → PE |
| `fetchOsimPhysioExperiment(con, run_id, study_id, subject_id, ...)` | OpenSim 結果 → PE |
| `fetchPhysioExperimentBatch(con, study_id, subject_id, session_ids, ...)` | 複数セッション一括取得 |
| `visualize_surface_emg(pe, output_dir)` | EMG 可視化プロット出力 |
| `perform_nmf_synergy_analysis(matrix, n_synergies)` | NMF シナジー解析 |
| `run_analysis_pipeline(con, ...)` | EMG 可視化 + シナジー解析の一括実行 |
| `plotMultiChannel(pe, style)` | 多チャネルプロット（butterfly/stacked）|
| `plotSignal(pe, channel)` | 単一チャネルプロット |
| `plotERP(pe, channel, ci)` | エポック平均波形 + 信頼区間 |
| `epochData(pe, tmin, tmax, baseline)` | エポック分割 |
| `spmTTest(pe, alpha)` | SPM t 検定 |
| `spmPairedTTest(pe, condition1, condition2)` | SPM 対応あり t 検定 |
| `tTestEpochs(pe, condition1, condition2)` | 時点ごと t 検定 |
| `clusterPermutationTest(pe, condition1, condition2)` | クラスタ順列検定 |
| `plotSPM(result)` | SPM 結果の可視化 |
| `cbindPhysio(pe1, pe2)` | チャネル方向の結合 |
| `rbindPhysio(pe1, pe2)` | 時間方向の結合 |
