# Icebergテーブルのスキーマと役割

このドキュメントは、OpenSim解析パイプラインで使用されるIcebergテーブルの定義と役割をまとめたものです。

## 1. マスターデータ (Master Data)
実験設定、被験者情報、センサー定義などを管理するテーブル群です。

### `study_table`
研究プロジェクト全体のメタデータ。
*   **役割**: 研究IDとデータセットの対応付け。
*   **スキーマ**:
    *   `study_id` (Long): 研究ID
    *   `dataset_name` (String): データセット名
    *   `doi` (String): DOI
    *   `lab` (String): 研究室名
    *   `notes` (String): メモ
    *   `date` (String): 日付

### `subject_table`
被験者の属性情報。
*   **役割**: Scalingに必要な身長・体重などを提供。
*   **スキーマ**:
    *   `study_id` (Long): 研究ID
    *   `subject_id` (Long): 被験者ID
    *   `height_m` (Double): 身長 (m)
    *   `weight_kg` (Double): 体重 (kg)
    *   `age_year` (Long): 年齢
    *   `sex` (String): 性別
    *   `pathlogy` (String): 疾患情報
    *   `bmi` (Double): BMI
    *   `notes` (Double): メモ

### `session_info`
セッションとタスクの定義。
*   **役割**: 解析対象のトライアル（タスク）を特定する。`task_id=0` はStaticトライアルとして扱われる。
*   **スキーマ**:
    *   `study_id` (Long): 研究ID
    *   `subject_id` (Long): 被験者ID
    *   `session_id` (String): セッションID
    *   `task_id` (Double): タスクID (0=Static)
    *   `notes` (String): メモ

### `device_table`
計測機器の設定情報。
*   **役割**: サンプリングレートなどの機器仕様を管理。
*   **スキーマ**:
    *   `study_id` (Long): 研究ID
    *   `device_id` (Long): 機器ID
    *   `device_name` (String): 機器名
    *   `type` (String): センサータイプ (Motion-capture, Surface-EMG等)
    *   `samplingrate_Hz` (Long): サンプリング周波数
    *   `manufacture` (String): 製造元
    *   `notes` (String): メモ

### `dim_channels`
チャンネルマッピング定義。
*   **役割**: EMGチャンネル名とOpenSimの筋肉名、またはGRFのチャンネル定義などの対応付け。
*   **スキーマ**:
    *   `study_id` (Long): 研究ID
    *   `column_name` (String): 元データのカラム名
    *   `muscle` (String): 対応する筋肉名 (SENIAM/OpenSim名)
    *   `side` (String): 左右 (r/l)
    *   `sensor_type` (String): センサータイプ
    *   `physical_quantity` (String): 物理量
    *   `unit` (String): 単位
    *   `data_type` (String): データ型

### `dim_marker`
マーカーセット定義。
*   **役割**: マーカー名、身体部位、Scaling/IK時の重み付け定義。
*   **スキーマ**:
    *   `study_id` (Long): 研究ID
    *   `marker` (String): マーカー名
    *   `body` (String): 身体部位
    *   `location` (String): 配置場所
    *   `scale_apply` (Boolean): Scalingに使用するか
    *   `scale_weight` (Long): Scaling時の重み
    *   `ik_apply` (Boolean): IKに使用するか
    *   `ik_weight` (Long): IK時の重み

### `dim_coordinate`
座標系定義。
*   **役割**: 実験室座標系からOpenSim座標系への変換ルール。
*   **スキーマ**:
    *   `study_id` (Long): 研究ID
    *   `sensor_type` (String): センサータイプ
    *   `coord_sys_id` (String): 座標系ID
    *   `x_positive`, `y_positive`, `z_positive` (String): 各軸の正方向定義
    *   `reference_frame` (String): 基準座標系
    *   `physical_quantity` (String): 物理量
    *   `note` (String): メモ

---

## 2. 生データ (Raw Data)
計測機器から得られたオリジナルの時系列データです。

### `raw_data`
全てのセンサーの生データ（Long形式）。
*   **役割**: 解析の入力となる一次データ。
*   **スキーマ**:
    *   `study_id` (Integer): 研究ID
    *   `subject_id` (String): 被験者ID
    *   `session_id` (String): セッションID
    *   `sensor_type` (String): センサータイプ
    *   `time` (Double): 時間
    *   `channel` (String): チャンネル名
    *   `value` (Double): 値
    *   `unit` (String): 単位

### `processed_data`
前処理済みデータ。
*   **役割**: フィルタリングや正規化が行われたデータ。
*   **スキーマ**:
    *   `raw_data` と同様のカラム + `processed_id` (Integer)

---

## 3. 来歴・設定データ (Provenance & Configuration Data)
「どのパラメータで解析したか」を記録し、再現性を担保するためのテーブル群です。`run_id` をキーとして管理されます。

### `analysis_runs`
解析実行の親レコード。
*   **役割**: バッチ実行ごとの基本情報（OpenSimバージョンなど）を記録。
*   **スキーマ**:
    *   `run_id` (String): 実行ID
    *   `study_id` (Long): 研究ID
    *   `opensim_version` (String): OpenSimバージョン
    *   `created_at` (Timestamp): 実行日時
    *   `basemodel_id` (String): ベースモデルID
    *   `studymodel_path` (String): スタディモデルパス

### `workflow_steps`
実行されたワークフローの手順。
*   **役割**: どのツール（Scaling, IK, Mocoなど）がどの順序で実行されたか。
*   **スキーマ**:
    *   `run_id` (String): 実行ID
    *   `step_order` (Long): 順序
    *   `step_type` (String): ステップ名 (scale, ik, moco等)
    *   `method_id` (Long): メソッドID

### `marker_task_parameters`
IK/Scaling時のマーカー設定。
*   **役割**: 実際に使用されたマーカーの重みや有効/無効状態。
*   **スキーマ**:
    *   `run_id` (String): 実行ID
    *   `step_type` (String): ステップ (scale/ik)
    *   `target_name` (String): マーカー名
    *   `weight` (Long): 重み
    *   `is_enabled` (Boolean): 有効フラグ

### `muscle_parameters`
Moco解析時の筋肉パラメータ。
*   **役割**: EMGトラッキングの重みや、筋活動の境界値設定など。
*   **スキーマ**:
    *   `run_id` (String): 実行ID
    *   `step_type` (String): ステップ
    *   `Moco_emg_path` (String): OpenSim上の筋肉パス
    *   `Moco_emg_name` (String): EMG名
    *   `Moco_emg_column` (String): EMGカラム名
    *   `emg_tracking_weight` (Double): トラッキング重み
    *   `Moco_emg_boundsMin`, `Moco_emg_boundsMax` (Double): 筋活動境界値
    *   `constraint_tolerance` (Double): 制約許容誤差
    *   `convergence_tolerance` (Double): 収束許容誤差

### `session_specific_parameters`
トライアルごとの可変パラメータ。
*   **役割**: 解析時間範囲やメッシュ間隔など、セッションごとに異なる設定。
*   **スキーマ**:
    *   `run_id` (String): 実行ID
    *   `session_id` (String): セッションID
    *   `study_id` (String): 研究ID
    *   `subject_id` (String): 被験者ID
    *   `Moco_start_time`, `Moco_end_time` (Double): 解析範囲
    *   `mesh_interval` (Double): メッシュ間隔
    *   `subj_model_path` (String): 使用モデルパス

### `osim_input_header` / `osim_input_value`
解析に入力されたファイル（.trc/.mot）のスナップショット。
*   **役割**: 解析時に実際にOpenSimに渡された入力データの記録。

---

## 4. 解析結果データ (Analysis Results Data)
OpenSimによる計算結果およびその品質評価データです。

### `osim_header`
出力ファイル（.sto/.mot）のヘッダー情報。
*   **役割**: 結果データのメタデータ（行数、列数、状態数など）。
*   **スキーマ**:
    *   `run_id` (String): 実行ID
    *   `session_id` (String): セッションID
    *   `FileType` (String): ファイルタイプ
    *   `nRows`, `nColumns` (Long): 行数・列数
    *   `objective` (Double): 目的関数値
    *   `success` (String): 成功可否
    *   `status` (String): ステータス
    *   `solver_duration` (Double): ソルバー実行時間
    *   `objective_emg_tracking` (Double): EMGトラッキング項
    *   `objective_excitation_effort` (Double): 努力項

### `osim_value`
解析結果の時系列数値データ。
*   **役割**: 関節角度、モーメント、筋活動度などの計算結果本体。
*   **スキーマ**:
    *   `run_id` (String): 実行ID
    *   `session_id` (String): セッションID
    *   `time` (Double): 時間
    *   `variable` (String): 変数名
    *   `value` (Double): 値
    *   `output_id`, `method_id`: 参照ID

### `osim_output`
出力変数の定義マスタ。
*   **役割**: `osim_value` の `variable` が何を意味するか（単位、物理量）の定義。
*   **スキーマ**:
    *   `output_name` (String): 変数名
    *   `unit` (String): 単位
    *   `physical_quantity` (String): 物理量
    *   `notes` (String): 説明
    *   `supports_quantity` (Boolean): フラグ

### `osim_validation`
解析結果の品質指標（サマリー）。
*   **役割**: トライアルごとの精度評価。
*   **スキーマ**:
    *   `run_id` (String): 実行ID
    *   `session_id` (String): セッションID
    *   `moco_success` (Boolean): Moco収束成功フラグ
    *   `moco_objective` (Double): 目的関数値
    *   `max_residual_force` (Double): 最大残差力
    *   `avg_residual_force` (Double): 平均残差力
    *   `max_reserve_ratio` (Double): 最大予備力比
    *   `quality_status` (String): 品質ステータス
    *   *(Note: コード上では `dtw_avg_normalized_distance` も追加される)*

### `emg_tracking_metrics`
筋肉ごとのEMG追従精度詳細。
*   **役割**: どの筋肉の予測精度が良かった/悪かったかの詳細分析用。
*   **スキーマ (コードベース)**:
    *   `run_id` (String)
    *   `session_id` (String)
    *   `muscle_path` (String): 筋肉パス
    *   `emg_column` (String): EMGカラム名
    *   `dtw_distance` (Double): DTW距離
    *   `dtw_normalized_distance` (Double): 正規化DTW距離

## 5.未実装
- task_idと名前の対応マップ
- device_idの運用（別studyで同じ機器を使っていた時に正しくidを振れるか）