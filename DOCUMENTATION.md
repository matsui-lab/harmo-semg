# パイプライン活用フロー

harmo パッケージは、sEMG・バイオメカニクスデータの **取り込み → モデル編集 → 解析 → 可視化** を統一 CLI で管理します。

---

## 全体像

```
                   config.yaml
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
     ┌─────────┐ ┌──────────┐ ┌──────────┐
     │ validate │ │  ingest  │ │   init   │
     └─────────┘ └────┬─────┘ └──────────┘
                      │
              Iceberg テーブル
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
   ┌─────────────┐ ┌─────────┐ ┌─────┐
   │ edit-markers │ │ analyze │ │ viz │
   └─────────────┘ └─────────┘ └─────┘
```

---

## Phase 1: データ取り込み

### 1.1 スタディの準備

```bash
# テンプレートから新規スタディを作成
harmo init --type mat my_study

# 生成される構造:
# my_study/
# ├── config.yaml
# └── definitions/
#     ├── studies.yaml
#     ├── subjects.yaml
#     └── channels.csv
```

### 1.2 設定ファイルの編集

`config.yaml` を編集し、以下を記述します:

- `input_dir_aliases`: データのルートディレクトリ
- `warehouse_path`: Iceberg テーブル出力先
- `definitions`: ディメンションテーブル (subject, device, channel 等)
- `file_mappings`: ファクトテーブル (raw_data, processed_data, event_data) の変換ルール

設定項目の詳細は [docs/CONFIGURATION.md](docs/CONFIGURATION.md) を参照。

MAT を扱う場合の運用原則:

- 標準 reader で到達先を一意に記述できる MAT は、`reader_options.data_key` または `mat_data_key_template` を明示します。
- MCOS / opaque MAT / 研究固有構造は、必ず `reader_plugin` を `file_mappings` に固定します。
- `mat_preconvert` は補助的な前処理であり、プラグイン宣言の代わりにはなりません。

### 1.3 バリデーション

```bash
harmo validate --config my_study/config.yaml
```

チェック内容:
- config.yaml の JSON Schema バリデーション
- 定義テーブルの存在確認とカラム型チェック
- テーブル間の ID 整合性 (study_id, subject_id)

### 1.4 取り込み実行

```bash
# ドライランで確認
harmo ingest --config my_study/config.yaml --dry-run

# 本実行
harmo ingest --config my_study/config.yaml
```

input:
- 計測データファイル (MAT, C3D, CSV, TRC, STO)
- メタデータ (定義テーブル)

output:
- Iceberg テーブル (raw_data, processed_data, event_data 等)
- 研究に対応した OpenSim モデル雛形 (`osim/model/`)

---

## Phase 2: OpenSim モデル編集

Iceberg に登録されたマーカーデータを基に OpenSim モデルを調整します。

```bash
harmo edit-markers --model osim/model/study31.osim
```

3D GUI が起動し、マーカー位置をドラッグ&ドロップで編集できます。
編集後、自動的に `study31_post_GUI.osim` として保存されます。

---

## Phase 3: OpenSim 解析

```bash
# デフォルトラベルで実行
harmo analyze --config my_study/config.yaml
# → run_id: 20260215_1030_default

# カスタムラベル付き実行
harmo analyze --config my_study/config.yaml --label scaling_v2
# → run_id: 20260215_1030_scaling_v2
```

実行される解析パイプライン:
1. **Scaling** - 被験者の身体寸法に合わせたモデルスケーリング
2. **IK (Inverse Kinematics)** - マーカーデータから関節角度を算出
3. **ID (Inverse Dynamics)** - 関節モーメントを算出
4. **Moco** - 最適制御による筋活動推定

解析結果は自動的に Iceberg テーブル (`osim_value`, `osim_output`) に書き戻されます。

---

## Phase 4: 結果確認

### 4.1 GUI で確認

```bash
harmo viz
```

Streamlit アプリが起動し、以下の操作が可能です:
- スタディ/被験者/セッションの選択
- 時系列データのインタラクティブプロット
- 生データと解析結果の比較表示
- データのエクスポート

### 4.2 直接クエリ

Jupyter Notebook から DuckDB 経由で直接クエリも可能です:

```python
from harmo.warehouse.connection import get_duckdb_connection, iceberg_table_path

con = get_duckdb_connection()
df = con.sql(f"""
    SELECT time, channel, value
    FROM iceberg_scan('{iceberg_table_path("raw_data")}', allow_moved_paths = true)
    WHERE study_id = 31 AND subject_id = 'sub-01'
    ORDER BY time
""").df()
```

---

## CLI リファレンス

| コマンド | 説明 | 必須オプション |
|----------|------|----------------|
| `harmo validate` | config.yaml のバリデーション | `--config PATH` |
| `harmo ingest` | データ取り込み | `--config PATH` |
| `harmo edit-markers` | マーカー編集 GUI | `--model PATH` (推奨) |
| `harmo analyze` | OpenSim バッチ解析 | `--config PATH` |
| `harmo viz` | Streamlit 可視化 | (なし) |
| `harmo init` | スタディテンプレート生成 | `--type {c3d,mat,csv}` `DIR` |

共通オプション:
- `--verbose` / `-v`: 詳細ログ出力

---

## 環境変数

| 変数名 | デフォルト | 説明 |
|--------|-----------|------|
| `HARMO_WAREHOUSE_PATH` | `./warehouse` | Iceberg データ保存先パス |
| `HARMO_SPARK_TMP` | `/tmp/spark_tmp` | Spark 一時ファイル |
| `HARMO_OSIM_DIR` | `./osim` | OpenSim モデル・結果ディレクトリ |

---

## よくあるエラー

### `No module named 'opensim'`

`harmo analyze` の実行には OpenSim Python バインディングが必要です。
Docker コンテナ内で実行するか、OpenSim を手動でインストールしてください。

### `No module named 'pyspark'`

Iceberg テーブルへの書き込みには PySpark が必要です:
```bash
pip install -e ".[spark]"
```

### `C3D読み込みにはezc3dが必要です`

Python 3.10以上で `pip install -e ".[c3d]"` を実行します。
Python 3.8/3.9のSpark環境から利用する場合は、呼び出し元にも
`harmo[c3d]`（Parquet転送用）を入れ、同じ追加依存を入れたPython 3.10
以上の実行ファイルを `HARMO_C3D_PYTHON` で指定します。

### バリデーションエラー

```bash
harmo validate --config my_study/config.yaml
```
を実行し、エラーメッセージに従って config.yaml を修正してください。

---

## ディレクトリ規約

```
harmo-semg/
├── src/harmo/            # Python パッケージ
├── studies/              # スタディ別設定
│   ├── study31/
│   │   ├── config.yaml
│   │   └── definitions/
│   └── gait120/
│       ├── config.yaml
│       └── definitions/
├── osim/                 # OpenSim モデル・セットアップ・結果
├── warehouse/            # Iceberg テーブル (gitignore)
├── notebooks/            # Jupyter Notebook
├── mat_data/             # MAT テストデータ (gitignore)
├── scripts/              # ユーティリティスクリプト
├── tests/                # テスト
│   ├── unit/
│   └── integration/
└── docs/                 # 詳細ドキュメント
```
