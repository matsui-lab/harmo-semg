# Iceberg to PhysioExperiment - Quick Reference Guide

## 概要

`iceberg_to_physio.R` は、Apache Icebergテーブルからデータを読み込んで、PhysioExperimentオブジェクトを生成するRの関数セットです。RStudio Serverで対話的に実行できます。

---

## セットアップ

### 1. RStudio Serverで実行

```r
# RStudio Server内で関数を読み込む
source("iceberg_to_physio.R")

# または、ファイルをセッションにアップロードして
source("~/path/to/iceberg_to_physio.R")
```

### 2. 前提条件

- **PhysioExperimentパッケージ:** インストール済み
- **duckdb/DBI:** インストール済み
- **Icebergウェアハウス:** `warehouse/warehouse/` ディレクトリが存在

---

## 主要な関数

### 接続管理

#### `connectToIceberg(warehouse_path = NULL)`
Icebergウェアハウスに接続します。

```r
con <- connectToIceberg()
# カスタムパスの場合
con <- connectToIceberg(warehouse_path = "/custom/path/warehouse")
```

**戻り値:** DBI接続オブジェクト

#### `disconnectFromIceberg(con)`
接続を閉じます。

```r
disconnectFromIceberg(con)
```

---

### データ探索

#### `listAvailableStudies(con)`
利用可能な研究一覧を取得します。

```r
studies <- listAvailableStudies(con)
head(studies)
# study_id, dataset_name, doi, lab, date, notes
```

#### `listAvailableSessions(con, study_id, subject_id = NULL)`
セッション一覧を取得します。

```r
# 特定の研究内のすべてのセッション
sessions <- listAvailableSessions(con, study_id = 1)

# 特定の被験者のセッションのみ
sessions <- listAvailableSessions(con, study_id = 1, subject_id = 101)
```

---

### メインの関数

#### `fetchPhysioExperiment(...)`
Icebergからデータを読み込んでPhysioExperimentオブジェクトを生成します。

**基本的な例：**

```r
pe <- fetchPhysioExperiment(
  con = con,
  study_id = 1,
  subject_id = 101,
  session_id = "S001",
  table_name = "raw_data",       # "raw_data" または "processed_data"
  sensor_type = "Surface-EMG",   # NULL で全センサー
  include_osim = FALSE,          # OpenSim結果を含めるか
  run_id = NULL                  # include_osim=TRUE の場合は必須
)
```

**パラメータ:**

| パラメータ | 説明 | 型 | デフォルト |
|-----------|------|-----|---------|
| `con` | Icebergへの接続 | connection | 必須 |
| `study_id` | 研究ID | numeric | 必須 |
| `subject_id` | 被験者ID | character/numeric | 必須 |
| `session_id` | セッションID | character | 必須 |
| `table_name` | クエリ対象のテーブル | character | "raw_data" |
| `sensor_type` | センサータイプフィルタ | character/NULL | NULL |
| `include_osim` | OpenSim結果を含める | logical | FALSE |
| `run_id` | OpenSim run ID | character | NULL |

**戻り値:** PhysioExperimentオブジェクト

**属性へのアクセス:**

```r
dim(pe)                          # [時間サンプル, チャネル数]
samplingRate(pe)                 # サンプリングレート (Hz)
colnames(pe)                     # チャネル名
assay(pe)                        # 信号マトリクス
colData(pe)                      # チャネルメタデータ
rowData(pe)                      # 時間情報
metadata(pe)                     # 実験メタデータ
```

---

#### `fetchPhysioExperimentBatch(...)`
複数セッションをまとめて読み込みます。

```r
# 被験者101のすべてのセッション
sessions <- listAvailableSessions(con, study_id = 1, subject_id = 101)
session_list <- sessions$session_id

pe_list <- fetchPhysioExperimentBatch(
  con = con,
  study_id = 1,
  subject_id = 101,
  session_ids = session_list,
  table_name = "raw_data",
  sensor_type = "Surface-EMG"
)

# リスト内にアクセス
pe_session_01 <- pe_list[["S001"]]
pe_session_02 <- pe_list[["S002"]]
```

---

## 使用例

### 例1: 生EMGデータの読み込み

```r
con <- connectToIceberg()

pe <- fetchPhysioExperiment(
  con = con,
  study_id = 1,
  subject_id = 101,
  session_id = "S001",
  table_name = "raw_data",
  sensor_type = "Surface-EMG"
)

# 情報表示
cat(sprintf("Channels: %d, Samples: %d\n", ncol(pe), nrow(pe)))
cat(sprintf("Sampling Rate: %.0f Hz\n", samplingRate(pe)))
cat(sprintf("Duration: %.2f sec\n", nrow(pe) / samplingRate(pe)))

disconnectFromIceberg(con)
```

### 例2: 処理済みデータの読み込み

```r
con <- connectToIceberg()

# フィルター済み・正規化済みデータ
pe_processed <- fetchPhysioExperiment(
  con = con,
  study_id = 1,
  subject_id = 101,
  session_id = "S001",
  table_name = "processed_data",
  sensor_type = "Surface-EMG"
)

disconnectFromIceberg(con)
```

### 例3: OpenSim結果の追加

```r
con <- connectToIceberg()

pe_with_osim <- fetchPhysioExperiment(
  con = con,
  study_id = 1,
  subject_id = 101,
  session_id = "S001",
  table_name = "raw_data",
  include_osim = TRUE,
  run_id = "run_20240101_001"
)

# 複数のassayにアクセス
measured <- assay(pe_with_osim, "measured")        # 測定データ
osim_results <- assay(pe_with_osim, "OpenSim")    # OpenSim結果

disconnectFromIceberg(con)
```

### 例4: 複数セッションの分析

```r
con <- connectToIceberg()

# 被験者のすべてのセッション
sessions <- listAvailableSessions(con, study_id = 1, subject_id = 101)

pe_list <- fetchPhysioExperimentBatch(
  con = con,
  study_id = 1,
  subject_id = 101,
  session_ids = sessions$session_id,
  table_name = "raw_data"
)

# 各セッションを処理
for (session_id in names(pe_list)) {
  pe <- pe_list[[session_id]]
  cat(sprintf("%s: %d channels, %.2f sec\n",
              session_id, ncol(pe), nrow(pe)/samplingRate(pe)))
}

disconnectFromIceberg(con)
```

---

## FAQ

### Q1: Icebergウェアハウスが見つからないエラー

**解決:** `warehouse_path` パラメータを正しく指定してください。デフォルトは `warehouse/warehouse/` です。

```r
con <- connectToIceberg(warehouse_path = "/absolute/path/to/warehouse/warehouse")
```

### Q2: 特定のセンサーデータのみが必要

**解決:** `sensor_type` パラメータを使用してフィルターしてください。

```r
pe <- fetchPhysioExperiment(
  con = con,
  study_id = 1,
  subject_id = 101,
  session_id = "S001",
  sensor_type = "Surface-EMG"  # または "IMU", "MoCap" など
)
```

### Q3: すべてのセンサーを読み込みたい

**解決:** `sensor_type = NULL` を指定します。

```r
pe <- fetchPhysioExperiment(
  con = con,
  study_id = 1,
  subject_id = 101,
  session_id = "S001",
  sensor_type = NULL
)
```

### Q4: オブジェクトから被験者情報を取得したい

**解決:** `metadata()` で実験レベルメタデータにアクセスします。

```r
pe <- fetchPhysioExperiment(...)
metadata(pe)$subject_weight_kg
metadata(pe)$subject_age_years
metadata(pe)$subject_sex
```

### Q5: 時間ベクトルを取得したい

**解決:** `rowData()` から時間列を抽出します。

```r
time_vector <- rowData(pe)$time
```

---

## PhysioExperiment オブジェクトでの処理

PhysioExperimentオブジェクト作成後、PhysioExperimentパッケージの関数で処理できます：

```r
pe <- fetchPhysioExperiment(...)

# フィルタリング（存在する場合）
# pe_filtered <- butterworthFilter(pe, lowcut = 10, highcut = 500)

# 正規化（存在する場合）
# pe_norm <- batchNormalize(pe, method = "minmax")

# エポッキング（存在する場合）
# pe_epoched <- averageEpochs(pe, ...)

# 可視化
# plot(pe)
```

詳細はPhysioExperimentパッケージのドキュメントをご参照ください。

---

## トラブルシューティング

### ネットワークタイムアウト

```r
# より大きなタイムアウト値でリトライ
options(timeout = 600)
```

### キャラクタエンコーディングの問題

```r
# 日本語文字が含まれる場合
Sys.setlocale("LC_ALL", "ja_JP.UTF-8")
```

### メモリ不足エラー

大規模なセッションの場合、1セッション単位で処理してください。

```r
sessions <- listAvailableSessions(con, study_id = 1, subject_id = 101)

for (session_id in sessions$session_id) {
  pe <- fetchPhysioExperiment(
    con = con,
    study_id = 1,
    subject_id = 101,
    session_id = session_id
  )
  # 処理
  # ...
  # メモリ解放
  rm(pe)
  gc()
}
```

---

## 作成日
2024年

## 参考ドキュメント
- `iceberg_to_physio.R` - 関数実装
- `schema.md` - Icebergテーブルスキーマ
- `database.py` - Python参考実装
