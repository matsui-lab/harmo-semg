from __future__ import annotations

import os
import glob
import duckdb
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from pyspark.sql import SparkSession
from pyspark.sql.functions import lit

from harmo.warehouse.connection import get_warehouse_path, get_spark_session

# --- 1. ファイル読み込み関数 ---
def load_opensim_storage(path: str):
    header_dict = {}
    start_idx = 0
    
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            clean_line = line.strip()
            
            # 1. ヘッダー終了判定
            if clean_line == 'endheader':
                start_idx = i + 1
                break
            
            # 2. キーと値の抽出
            if '=' in clean_line:
                # 最初の'='で分割し、前後をトリミング
                key, value = map(str.strip, clean_line.split('=', 1))
                header_dict[key] = value
            
            # 3. ファイル先頭のタイプ名 (TendonPower等) を取得
            elif i == 0 and clean_line:
                header_dict['FileType'] = clean_line

    # ヘッダーをDataFrame化 (1行のテーブル)
    header_df = pd.DataFrame([header_dict])
    
    # 型推論 (数値にできる列は数値型へ変換)
    header_df = header_df.apply(pd.to_numeric, errors='coerce')
    
    # データの読み込み: engine="python" を指定して正規表現 sep を確実に扱う
    data_df = pd.read_csv(path, sep=r"\s+", skiprows=start_idx, engine="python")
    # カラム名を標準化して time の存在を保証
    data_df.columns = data_df.columns.str.strip().str.lower()
    if 'time' not in data_df.columns:
        raise ValueError(f"'time' column not found in {path}: {list(data_df.columns)}")
    
    return header_df, data_df


# --- 2. データ整形関数 ---
def to_long_format(df: pd.DataFrame, study_id: int, subject_id: str, session_id: str, method_id: str, output_id: str, run_id: str):
    """
    wide → long へ変換し、検索・パーティション用のキー列を追加する
    run_id: このバッチ実行のユニークID
    """
    long_df = df.melt(
        id_vars=["time"],
        var_name="variable",
        value_name="value"
    )
    
    # キー情報の付与 (これによりFactテーブル単体での高速な絞り込みが可能になる)
    long_df["study_id"] = study_id
    long_df["subject_id"] = subject_id
    long_df["session_id"] = session_id
    long_df["method_id"] = method_id
    long_df["output_id"] = output_id
    long_df["run_id"] = run_id
    
    # カラム順序を整えて返す
    return long_df[["study_id", "subject_id", "session_id", "method_id", 'output_id', "run_id", "time", "variable", "value"]]


def to_header_format(df: pd.DataFrame, method_id: str, study_id: int, subject_id: str, session_id: str, output_id: str, run_id: str):
    """
    header情報を獲得し、検索・パーティション用のキー列を追加する
    run_id: このバッチ実行のユニークID
    """
    
    # キー情報の付与 (これによりFactテーブル単体での高速な絞り込みが可能になる)
    df["study_id"] = study_id
    df["subject_id"] = subject_id
    df["session_id"] = session_id
    df["method_id"] = method_id
    df["output_id"] = output_id
    df["run_id"] = run_id
    
    # dfを返す
    return df

# --- 3. バッチ書き込み関数  ---
def ingest_meta(spark: SparkSession, df: pd.DataFrame, table_name: str):
    from pyspark.sql.functions import lit
    """
    大量のデータを一度に書き込む関数
    mode: "append" (データ追加) or "overwrite" (全置換/洗い替え)
    """
    spark_df = spark.createDataFrame(df)
    
    
    if not spark.catalog.tableExists(table_name):
        spark_df.writeTo(table_name).partitionedBy(
            "study_id", "subject_id", "session_id"
            ).create()
    else:
        # 3. 既存テーブルの全列名を取得
        target_columns = spark.table(table_name).columns
        
        # 4. データ側に足りない列を NULL で埋める
        for col in target_columns:
            if col not in spark_df.columns:
                spark_df = spark_df.withColumn(col, lit(None))
        
        # 5. 【重要】テーブルの列順と「完全に同じ」順番に並べ替える
        # これがないと、たとえ列数が合っていてもエラーになることがあります
        spark_df = spark_df.select(*target_columns)
        
        # 6. 書き込み実行
        spark_df.writeTo(table_name).append()


def ingest_data(spark: SparkSession, df: pd.DataFrame, table_name: str):
    """
    大量のデータを一度に書き込む関数
    mode: "append" (データ追加) or "overwrite" (全置換/洗い替え)
    """
    spark_df = spark.createDataFrame(df)

    if not spark.catalog.tableExists(table_name):
        spark_df.writeTo(table_name).partitionedBy(
            "study_id", "subject_id", "session_id"
        ).create()
    else:
        # スキーマ進化: 既存テーブルの列に合わせて不足列をNULLで補完し列順を揃える
        target_columns = spark.table(table_name).columns
        for col_name in target_columns:
            if col_name not in spark_df.columns:
                spark_df = spark_df.withColumn(col_name, lit(None))
        spark_df = spark_df.select(*target_columns)
        spark_df.writeTo(table_name).append()
        


# --- メイン処理 ---

def ingest_directory_bulk(input_dir: str, run_id: str, header_table: str = "local.warehouse.osim_header", 
                         value_table: str = "local.warehouse.osim_value", spark: SparkSession = None):
    """
    指定ディレクトリ内の .mot/.sto を一括読み込みし、全データを1つのスナップショットでIcebergに書き込む。
    
    動作：
    1. ディレクトリ内の全ファイルをPandasで読み込み、リストに蓄積
    2. pd.concat で Pandas 上で一括結合
    3. 結合済み DataFrame に対して1回だけ spark.createDataFrame を実行
    4. 1回だけ .writeTo().append() で書き込み → 1つのスナップショット生成
    """
    _warehouse_path = get_warehouse_path()
    created_spark = False
    if spark is None:
        spark = get_spark_session(
            warehouse_path=_warehouse_path,
            app_name="harmo-osim-bulk-register",
        )
        created_spark = True
    
    # 対象ファイルの収集
    file_paths = glob.glob(os.path.join(input_dir, "*.mot")) + glob.glob(os.path.join(input_dir, "*.sto"))
    
    if not file_paths:
        print("[Bulk Registration] No files found. Skipping.")
        if created_spark:
            spark.stop()
        return
    
    # 全ファイルのメタデータを取得 (osim_output テーブルを1回の一括クエリで取得)
    _parsed_files = []
    for file_path in file_paths:
        filename = os.path.basename(file_path)
        try:
            file_path_parts = filename.split("_")
            study_id = int(file_path_parts[0])
            subject_id = str(file_path_parts[1])
            session_id = str(file_path_parts[2])
            method_name = str(file_path_parts[3])
            output_name = "_".join(file_path_parts[4:])
            output_name = os.path.splitext(output_name)[0]
            _parsed_files.append((filename, study_id, subject_id, session_id, method_name, output_name))
        except (IndexError, ValueError) as e:
            print(f"  [SKIP] Invalid format: {filename} ({e})")
            continue

    # osim_output マスターを1回のクエリで取得 (per-file クエリを排除)
    _output_lookup = None
    try:
        _output_map_query = f"""
        SELECT method_name, output_name, method_id, output_id
        FROM iceberg_scan('{_warehouse_path}/warehouse/osim_output', allow_moved_paths = true)
        """
        _odf = duckdb.sql(_output_map_query).fetchdf()
        _output_lookup = {}
        for _, r in _odf.iterrows():
            if pd.notna(r['method_id']) and pd.notna(r['output_id']):
                _output_lookup[(str(r['method_name']), str(r['output_name']))] = (int(r['method_id']), int(r['output_id']))
    except Exception as e:
        print(f"  [Warning] Batch osim_output query failed: {e}. Falling back to per-file queries.")

    file_metadata = {}
    for filename, study_id, subject_id, session_id, method_name, output_name in _parsed_files:
        if "coord_and_emgdriven" in output_name:
            method_id, output_id = 2, 2003
        elif _output_lookup is not None:
            key = (method_name, output_name)
            if key not in _output_lookup:
                continue
            method_id, output_id = _output_lookup[key]
        else:
            query = f"""
            SELECT method_id, output_id
            FROM iceberg_scan('{_warehouse_path}/warehouse/osim_output', allow_moved_paths = true)
            WHERE method_name = '{method_name}'
            AND output_name = '{output_name}'
            """
            try:
                res = duckdb.sql(query).fetchone()
            except Exception as e:
                print(f"  [SKIP] DuckDB query failed for {filename}: {e})")
                continue
            if res is None:
                continue
            method_id, output_id = res
        file_metadata[filename] = (study_id, subject_id, session_id, method_id, output_id)
    
    # ステップ1: ThreadPoolExecutor で並列ファイル読み込み
    header_pandas_list = []
    value_pandas_list = []

    def _read_and_transform(args):
        fn, sid, subj, sess, mid, oid = args
        fp = os.path.join(input_dir, fn)
        try:
            header, value = load_opensim_storage(fp)
            hdf = to_header_format(header, mid, sid, subj, sess, oid, run_id)
            ldf = to_long_format(value, sid, subj, sess, mid, oid, run_id)
            return (hdf, ldf)
        except Exception as e:
            print(f"  [SKIP] Error processing {fn}: {e}")
            return None

    _tasks = [(fn, *meta) for fn, meta in file_metadata.items()]
    _n_workers = min(8, len(_tasks)) or 1
    with ThreadPoolExecutor(max_workers=_n_workers) as _exec:
        _results = list(_exec.map(_read_and_transform, _tasks))

    for _r in _results:
        if _r is not None:
            header_pandas_list.append(_r[0])
            value_pandas_list.append(_r[1])
    print(f"  Read {len(header_pandas_list)}/{len(file_metadata)} files successfully.")
    
    if not value_pandas_list:
        print("[Bulk Registration] No valid data. Skipping write.")
        if created_spark:
            spark.stop()
        return
    
    # ステップ2: pd.concat で Pandas 上で一括結合
    print(f"  Read complete. Concatenating {len(value_pandas_list)} DataFrames...")
    value_pd_unified = pd.concat(value_pandas_list, ignore_index=True)
    header_pd_unified = pd.concat(header_pandas_list, ignore_index=True)
    print(f"  Value rows: {len(value_pd_unified):,}, Header rows: {len(header_pd_unified):,}")
    
    # 既存テーブルのカラム情報を事前に取得
    target_value_columns = None
    target_header_columns = None
    if spark.catalog.tableExists(value_table):
        target_value_columns = spark.table(value_table).columns
    if spark.catalog.tableExists(header_table):
        target_header_columns = spark.table(header_table).columns
    
    # ステップ3-5: バッチ分割で書き込み (OOM 防止)
    BATCH_ROW_LIMIT = 500_000
    try:
        # --- value テーブル書き込み ---
        total_value_rows = len(value_pd_unified)
        for batch_start in range(0, total_value_rows, BATCH_ROW_LIMIT):
            batch_end = min(batch_start + BATCH_ROW_LIMIT, total_value_rows)
            batch_pd = value_pd_unified.iloc[batch_start:batch_end]
            print(f"  Writing value batch [{batch_start:,}:{batch_end:,}] / {total_value_rows:,}")
            batch_df = spark.createDataFrame(batch_pd)
            if target_value_columns is not None:
                for col in target_value_columns:
                    if col not in batch_df.columns:
                        batch_df = batch_df.withColumn(col, lit(None))
                batch_df = batch_df.select(*target_value_columns)
            if target_value_columns is not None:
                batch_df.writeTo(value_table).append()
            else:
                batch_df.writeTo(value_table).partitionedBy(
                    "study_id", "subject_id", "session_id"
                ).create()
                target_value_columns = spark.table(value_table).columns
            del batch_df, batch_pd
        del value_pd_unified
        
        # --- header テーブル書き込み ---
        header_df = spark.createDataFrame(header_pd_unified)
        if target_header_columns is not None:
            for col in target_header_columns:
                if col not in header_df.columns:
                    header_df = header_df.withColumn(col, lit(None))
            header_df = header_df.select(*target_header_columns)
            header_df.writeTo(header_table).append()
        else:
            header_df.writeTo(header_table).partitionedBy(
                "study_id", "subject_id", "session_id"
            ).create()
        del header_df, header_pd_unified
        
    except Exception as e:
        print(f"[Bulk Registration] ERROR during write operation: {e}")
        raise
    
    finally:
        if created_spark:
            spark.stop()

"""
if __name__ == "__main__":
    input_dir = os.environ.get('HARMO_OSIM_DIR', '/workspace/src/harmo/osim') + "/all_results"
    run_id = "20260115_1200_manual"  # テスト用
    header_table = "local.warehouse.osim_header"
    value_table = "local.warehouse.osim_value"
    ingest_directory_bulk(input_dir, run_id=run_id, header_table=header_table, value_table=value_table)
"""