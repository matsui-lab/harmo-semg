# Surface-EMG 可視化とシナジー解析ガイド

## 概要

このドキュメントは、Icebergウェアハウスから読み込んだ表面筋電図(Surface-EMG)データとOpenSim解析結果の以下の分析を説明します：

1. **Surface-EMGデータの可視化** - 神経筋のシグナル計測値の複合的な視覚化
2. **筋肉シナジー解析** - 非負行列因数分解(NMF)を用いた協調筋活動パターンの抽出

## ファイル構成

### `analysis_visualization.R`
コア分析関数を含むメインスクリプト：

#### Part 1: EMG可視化
- **`visualize_surface_emg()`** - Surface-EMGデータの複数プロット生成
  - マルチチャネル時系列プロット
  - ヒートマップ（時間 × チャネル）
  - 統計情報（ボックスプロット、RMS、標準偏差など）
  - 記述統計テーブル

#### Part 2: シナジー解析
- **`.fetch_activation_data()`** - OpenSimのactivation列を抽出
  - フィルタ: MocoInverse、coord_* output、*activation*列
  - 時系列activation値を取得
  
- **`perform_nmf_synergy_analysis()`** - 非負行列因数分解実行
  - 筋活動パターンを複数の基本シナジーに分解
  - 説明分散量を算出
  
- **`visualize_synergy_analysis()`** - シナジー分析結果の可視化
  - シナジーベクトル（筋の重み）
  - シナジー時間係数（活動パターン）
  - 実測値vs再構成値の比較
  - ヒートマップ

- **`run_analysis_pipeline()`** - 全体統合実行関数

### `analysis_quickstart.R`
使用例とクイックスタートガイド

## 分析手法の詳細

### 1. Surface-EMG可視化

#### 出力プロット

**01_emg_timeseries_all_channels.png**
- 全チャネルの時系列データ
- 各チャネルの個別グラフで詳細確認が可能
- 振幅単位: mV

**02_emg_heatmap.png**
- 時間軸（X）とチャネル軸（Y）のヒートマップ
- 振幅の大きさを色で表現（青=低、赤=高）
- 全体的なパターン認識に有用

**03_emg_statistics.png**
- ボックスプロット形式の統計情報
- 各チャネル内の値の分布を表示
- 外れ値や異常値の検出

#### 記述統計

出力のテーブルに以下の統計量が含まれます：

| 統計量 | 説明 |
|--------|------|
| Mean | 平均振幅 |
| SD | 標準偏差（バラツキ） |
| Min | 最小値 |
| Max | 最大値 |
| RMS | Root Mean Square（実効値） |

### 2. 筋肉シナジー解析

#### 原理

筋肉シナジーは、脳が複雑な運動を制御するために使う「基本的な筋活動パターン」です。

個別の筋活動 ≈ Σ(シナジー重み × シナジー活動) です。

非負行列因数分解(NMF)により、以下に分解します：

```
Activation Matrix (時間 × 筋) 
  = Synergy Vectors (筋 × シナジー数) 
    × Synergy Coefficients (シナジー数 × 時間)
```

#### 数学的背景

与えられた行列Xを以下のように因数分解：

$$X \approx W \times H$$

ここで：
- **X**: 筋活動行列 (n_samples × n_muscles)
- **W**: シナジーベクトル行列 (n_muscles × n_synergies)  
- **H**: シナジー時間係数行列 (n_synergies × n_samples)

制約条件：
- W ≥ 0（全て非負）
- H ≥ 0（全て非負）

目的関数：
$$\text{minimize} \quad ||X - WH||_F$$

#### 出力プロット

**01_synergy_vectors.png**
- 各シナジーに対する筋の重み（寄与度）
- 棒グラフで各筋の相対的な重要度を表示
- 同じシナジー内で大きな重みを持つ筋は協調的に活動した

**02_synergy_time_coefficients.png**
- 各シナジーの時間的な活動パターン
- 係数が大きい時間帯 = そのシナジーが活発に使用されている
- 複数シナジーの活動タイミングの重複パターンから運動制御戦略が推測できる

**03_actual_vs_reconstructed.png**
- 実測の筋活動値（黒線）vs NMFで再構成した値（赤線）
- 再構成が実測に近いほどモデルの精度が高い
- 大きなズレがある場合、シナジー数の増減を検討

**04_synergy_heatmap.png**
- 筋（Y軸）とシナジー（X軸）の関係を色で表現
- 赤：その筋がそのシナジーで重要（大きな重み）
- 白：その筋がそのシナジーで不要（小さな重み）
- パターン認識：同じ色が濃い行（筋）はシナジーをシェア

#### 解釈ガイド

1. **シナジー数の選択**
   - デフォルト: 4個
   - 一般的な範囲: 3～6個
   - 説明分散量が85-95%になるのが目安

2. **シナジーの意味**
   - 同じシナジー内に大きな重みを持つ筋のグループ = 協調単位
   - 機能的な意味付けは運動の種類に依存

3. **時間パターンの解釈**
   - 係数の時間的なオンオフパターン = シナジーの活動段階
   - 複数シナジーの同時活動 = 複雑な運動

## 使用方法

### クイックスタート

```r
source("analysis_quickstart.R")
# スクリプト内容が実行される
# または手動で run_analysis_pipeline() を呼び出し
```

### パラメータ設定

```r
results <- run_analysis_pipeline(
  con = con,
  study_id = 31,              # あなたのスタディID
  subject_id = 1,             # あなたの被験者ID
  session_id = "ses-V3-T5",   # raw_dataのセッションID
  run_id = "20260209_0223_default",  # OpenSimのrunID
  n_synergies = 4,            # シナジー数（推奨: 4-6）
  emg_output_dir = "~/emg_analysis",
  synergy_output_dir = "~/synergy_analysis"
)
```

### 利用可能なデータの確認

```r
con <- connectToIceberg()

# 全スタディを一覧表示
studies <- listAvailableStudies(con)
print(studies)

# 特定スタディの全セッションを表示
sessions <- listAvailableSessions(con, study_id = 31, subject_id = 1)
print(sessions)

disconnectFromIceberg(con)
```

## データ要件

### raw_dataテーブル
- **sensor_type**: "Surface-EMG"
- 複数チャネルの筋電信号
- サンプリングレート: 2000 Hz以上推奨

### osim_valueテーブル
- **method_id**: MocoInverse
- **output_name**: "coord_*" で始まる座標値
- **variable**: "*activation*" を含む筋活動

## トラブルシューティング

### 問題1: "No activation data found" エラー

**原因**: 指定されたrun_id, session_idが存在しない

**解決策**:
```r
con <- connectToIceberg()
available_sessions <- listAvailableSessions(con, study_id = 31, subject_id = 1)
print(available_sessions)  # セッションIDを確認
disconnectFromIceberg(con)
```

### 問題2: "all assays must have the same nrow and ncol" エラー

**原因**: raw_dataとosim_dataのサンプリングレートまたはサンプル数が異なる

**解決策**: 分析を分けて実行（EMGとシナジーを別々に）

### 問題3: 再構成精度が低い（explanation < 70%）

**原因**: シナジー数が不足している可能性

**解決策**: `n_synergies` を増やして試す（5, 6, 8など）

## 出力ファイル

### EMG解析 (~emg_analysis/)
- `01_emg_timeseries_all_channels.png` - 時系列プロット
- `02_emg_heatmap.png` - ヒートマップ
- `03_emg_statistics.png` - 統計図

### シナジー解析 (~synergy_analysis/)
- `01_synergy_vectors.png` - 筋重み
- `02_synergy_time_coefficients.png` - 時間係数
- `03_actual_vs_reconstructed.png` - 再構成比較
- `04_synergy_heatmap.png` - 筋-シナジーマップ

## 参考文献

### 筋肉シナジーの理論
- Tresch, M. C., Cheung, V. C., & d'Avella, A. (2006). Matrix factorization algorithms for the identification of muscle synergies: evaluation on simulated and experimental data sets. *Journal of neurophysiology*, 95(4), 2199-2212.

### NMFアルゴリズム
- Lee, D. D., & Seung, H. S. (1999). Learning the parts of objects by non-negative matrix factorization. *Nature*, 401(6755), 788-791.

### 推奨シナジー数
- Steele, K. M., Tresch, M. C., & Perreault, E. J. (2013). The number and choice of muscles for coding hand grip transformations revealed by principal components and independent components analyses. In *Neuroscience Meeting Planner*. San Diego, CA: Society for Neuroscience.

## その他

### パッケージの必要性

**分析に必要なR パッケージ**:
- `duckdb`: データベース接続
- `DBI`: データベースインターフェース
- `ggplot2`: グラフィック
- `reshape2`: データ整形
- `NMF`: 行列因数分解
- `dplyr`: データ処理
- `tidyr`: データ変形

### 動作環境
- R version 4.0 以上推奨
- メモリ: 8GB 以上推奨（大規模データの場合）
- 実行時間: 典型的には 2-5分（データサイズと n_synergies に依存）

---

作成日: 2026-02-15
更新版: analysis_visualization.R v1.0
