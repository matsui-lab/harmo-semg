from __future__ import annotations

# 必要なライブラリをインポートします
import streamlit as st
import pandas as pd
import plotly.express as px
import io
import zipfile
# 'database.py'から、データベースを操作するための関数をインポートします
from harmo.warehouse.query import (
    get_distinct_values,
    get_filtered_distinct_values,
    get_time_series_data,
    get_filtered_osim_names,
    get_summary_data,
    get_all_osim_value_data_for_run
)

# ページのレイアウトをワイドモード（広め）に設定します
st.set_page_config(layout="wide")

# アプリケーションのタイトルを表示します
st.title("Harmo-sEMG データ可視化ツール")

# --- セッションステートの初期化 ---
# 全チャンネルダウンロード機能のために使用
if 'all_channels_csv_data' not in st.session_state:
    st.session_state.all_channels_csv_data = None
if 'all_channels_filename' not in st.session_state:
    st.session_state.all_channels_filename = ""
# ZIPダウンロード機能のために使用
if 'zip_buffer' not in st.session_state:
    st.session_state.zip_buffer = None
if 'zip_filename' not in st.session_state:
    st.session_state.zip_filename = ""

# --- キャッシュ機能付きのヘルパー関数 ---
@st.cache_data
def cached_get_distinct_values(table, column):
    """指定されたテーブルとカラムから重複しない値のリストを取得します（キャッシュ利用）"""
    return get_distinct_values(table, column)

@st.cache_data
def cached_get_filtered_distinct_values(table, column, filters):
    """フィルタを適用した後、指定されたテーブルとカラムから重複しない値のリストを取得します（キャッシュ利用）"""
    frozen_filters = tuple(sorted(filters.items()))
    return get_filtered_distinct_values(table, column, filters)

@st.cache_data
def cached_get_filtered_osim_names(name_to_get, filters):
    """フィルタを適用した後、Osimのメソッド/アウトプットの名前とIDのペアを取得します（キャッシュ利用）"""
    frozen_filters = tuple(sorted(filters.items()))
    return get_filtered_osim_names(name_to_get, filters)

@st.cache_data
def cached_get_summary_data(table, filters):
    """フィルタを適用した後、サマリーテーブルのデータを取得します（キャッシュ利用）"""
    frozen_filters = tuple(sorted(filters.items()))
    return get_summary_data(table, filters)

@st.cache_data
def cached_get_all_osim_value_data_for_run(run_id, session_id):
    """指定されたRunとSessionの全osim_valueデータを一括取得します（キャッシュ利用）"""
    return get_all_osim_value_data_for_run(run_id, session_id)

# --- フィルタ選択用のサイドバー ---
st.sidebar.header("データフィルタ")

# 1. データテーブルの選択
data_tables = {
    "（生）Raw Data": "raw_data",
    "（生）Processed Data": "processed_data",
    "（Osim前）OpenSim Input": "osim_input_value",
    "（Osim後）OpenSim Results": "osim_value"
}
selected_table_name = st.sidebar.selectbox(
    "データソースを選択",
    options=list(data_tables.keys())
)
selected_table = data_tables[selected_table_name]

# 2. Studyの選択
study_ids = cached_get_distinct_values(selected_table, 'study_id')
selected_study = st.sidebar.selectbox("Study ID を選択", options=study_ids, index=0 if study_ids else None)

# 3. Subjectの選択
subjects = []
if selected_study:
    subjects = cached_get_filtered_distinct_values(selected_table, 'subject_id', {'study_id': selected_study})
selected_subject = st.sidebar.selectbox("Subject ID を選択", options=subjects, index=0 if subjects else None, disabled=not subjects)

# 4. Sessionの選択
sessions = []
if selected_study and selected_subject:
    sessions = cached_get_filtered_distinct_values(selected_table, 'session_id', {'study_id': selected_study, 'subject_id': selected_subject})
selected_session = st.sidebar.selectbox("Session ID を選択", options=sessions, index=0 if sessions else None, disabled=not sessions)

# --- 条件付きフィルタ ---
# 変数を初期化
selected_run = None
selected_method = None
selected_output = None
selected_sensor_type = None
channel_column = 'channel' # デフォルト
channels = []

base_filters = {'study_id': selected_study, 'subject_id': selected_subject, 'session_id': selected_session}

# 5. Run ID の選択 (osim_input_value と osim_value の場合のみ)
if selected_table in ['osim_input_value', 'osim_value']:
    if selected_session:
        runs = cached_get_filtered_distinct_values(selected_table, 'run_id', base_filters)
        if runs:
            runs = sorted(runs, reverse=True)
        selected_run = st.sidebar.selectbox("Run ID を選択", options=runs, index=0 if runs else None, disabled=not runs)

# 6. Sensor Type の選択 (raw_data と processed_data の場合のみ)
if selected_table in ['raw_data', 'processed_data']:
    if selected_session:
        sensor_types = cached_get_filtered_distinct_values(selected_table, 'sensor_type', base_filters)
        selected_sensor_type = st.sidebar.selectbox("Sensor Type を選択", options=sensor_types, index=0 if sensor_types else None, disabled=not sensor_types)

# 7. Method ID と Output ID の選択 (osim_value の場合のみ)
if selected_table == 'osim_value':
    channel_column = 'variable'
    if selected_run:
        # Methodの選択
        method_filters = {**base_filters, 'run_id': selected_run}
        methods = cached_get_filtered_osim_names('method', method_filters)
        selected_method = st.sidebar.selectbox(
            "Method を選択",
            options=[m[1] for m in methods], # IDのリストを渡す
            format_func=lambda x: next((m[0] for m in methods if m[1] == x), x), # 表示名を設定
            index=0 if methods else None,
            disabled=not methods
        )

        # Outputの選択
        if selected_method:
            output_filters = {**method_filters, 'method_id': selected_method}
            outputs = cached_get_filtered_osim_names('output', output_filters)
            selected_output = st.sidebar.selectbox(
                "Output を選択",
                options=[o[1] for o in outputs], # IDのリスト
                format_func=lambda x: next((o[0] for o in outputs if o[1] == x), x), # 表示名
                index=0 if outputs else None,
                disabled=not outputs
            )

        # --- ZIP一括ダウンロード機能 ---
        st.sidebar.markdown("---")
        st.sidebar.subheader("一括ダウンロード (.zip)")

        # run_idが選択されている場合のみボタンを表示
        if selected_run and selected_session:
            if st.sidebar.button("このRun IDの全結果を準備"):
                with st.spinner('ZIPファイルを準備中... (データ量により時間がかかることがあります)'):
                    # 1. 全データを一度に取得
                    all_data = cached_get_all_osim_value_data_for_run(selected_run, selected_session)
                    
                    if all_data.empty:
                        st.sidebar.warning("このRun IDに紐づくデータが見つかりません。")
                        st.session_state.zip_buffer = "EMPTY"
                    else:
                        zip_buffer = io.BytesIO()
                        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                            # 2. output_idとoutput_nameでグループ化してループ
                            for (output_id, output_name), group_df in all_data.groupby(['output_id', 'output_name']):
                                if group_df.empty:
                                    continue

                                # 3. ワイド形式に変換
                                df_wide = group_df.pivot_table(index='time', columns='channel', values='value').reset_index()
                                
                                # 4. CSVファイルとしてZIPに追加
                                safe_output_name = output_name.replace(" ", "_").replace("/", "-")
                                csv_filename = f"{selected_run}_{selected_session}_{safe_output_name}.csv"
                                
                                zf.writestr(csv_filename, df_wide.to_csv(index=False))

                        st.session_state.zip_buffer = zip_buffer.getvalue()
                        st.session_state.zip_filename = f"{selected_study}_{selected_subject}_{selected_session}_{selected_run}_osim_results.zip"

        # ZIPファイルの準備ができていればダウンロードボタンを表示
        if st.session_state.get('zip_buffer'):
            if st.session_state.zip_buffer == "EMPTY":
                st.sidebar.error("ダウンロード対象のデータがありませんでした。")
                # 状態をリセット
                st.session_state.pop('zip_buffer', None)
            else:
                st.sidebar.download_button(
                    label="準備完了: .zipをダウンロード",
                    data=st.session_state.zip_buffer,
                    file_name=st.session_state.zip_filename,
                    mime="application/zip",
                    on_click=lambda: st.session_state.pop('zip_buffer', None) # ダウンロード後に状態をクリア
                )
        st.sidebar.markdown("---")
        # --- ここまでZIP一括ダウンロード機能 ---


# 8. Channel/Variable の選択
# テーブルに応じたチャンネルカラム名を設定
if selected_table == 'osim_input_value':
    channel_column = 'column'

# チャンネルリストを取得するための条件を設定
can_get_channels = False
filter_for_channels = {k: v for k, v in base_filters.items() if v is not None}

if selected_table in ['raw_data', 'processed_data'] and selected_sensor_type:
    filter_for_channels['sensor_type'] = selected_sensor_type
    can_get_channels = True
elif selected_table == 'osim_input_value' and selected_run:
    filter_for_channels['run_id'] = selected_run
    can_get_channels = True
elif selected_table == 'osim_value' and selected_output:
    filter_for_channels.update({'run_id': selected_run, 'method_id': selected_method, 'output_id': selected_output})
    can_get_channels = True

if can_get_channels:

    channels = cached_get_filtered_distinct_values(selected_table, channel_column, filter_for_channels)



selected_channels = st.sidebar.multiselect("プロットするチャンネル/変数を選択", options=channels, disabled=not channels)

# --- グラフ表示用のメインパネル ---
st.header("時系列データプロット")
if not selected_channels:
    st.info("サイドバーから1つ以上のチャンネル/変数を選択すると、グラフが表示されます。")
else:
    # データを取得するための最終的なフィルタを作成
    final_filters = filter_for_channels.copy()
    
    # パフォーマンス改善のため、選択されたチャンネルのデータのみを取得
    with st.spinner('データベースからデータを取得中...'):
        df = get_time_series_data(selected_table, final_filters, selected_channels, channel_column)

    if df.empty:
        st.warning("選択された条件に合致するデータが見つかりませんでした。")
    else:
        # グラフのY軸ラベルに単位を追加
        y_axis_label = "Value"
        if 'unit' in df.columns:
            unique_units = df['unit'].unique()
            # 単位が1種類で、かつ'N/A'やNoneでない場合のみラベルに追加
            valid_units = [u for u in unique_units if u and u != 'N/A']
            if len(valid_units) == 1:
                y_axis_label = f"Value ({valid_units[0]})"

        fig = px.line(
            df,
            x='time',
            y='value',
            color='channel',
            title=f"テーブル '{selected_table_name}' のデータ",
            labels={'channel': 'チャンネル/変数', 'value': y_axis_label}
        )
        fig.update_layout(legend_title_text='凡例')
        st.plotly_chart(fig, width="stretch")

        st.write("---")
        
        # --- データプレビューとダウンロード ---
        st.subheader("フィルタリングされたデータのプレビュー（選択されたチャンネル）")
        preview_df = df[['time', 'channel', 'value']]
        df_wide = preview_df.pivot_table(index='time', columns='channel', values='value').reset_index()
        st.dataframe(df_wide)

        # --- CSVダウンロード機能 ---
        st.subheader("CSVダウンロード")
        col1, col2 = st.columns(2)

        # ダウンロードボタン1: 選択したチャンネルのみ (ワイド形式)
        with col1:
            csv_selected_wide = df_wide.to_csv(index=False).encode('utf-8')
            st.download_button(
                "プレビュー結果をダウンロード",
                csv_selected_wide,
                f"{selected_study}_{selected_subject}_{selected_session}_selected_wide.csv",
                'text/csv',
                key='selected_wide_csv',
                help="現在プレビューに表示されているワイド形式のデータをダウンロードします。"
            )

        # ダウンロードボタン2: 全てのチャンネル（遅延読み込み）
        with col2:
            # 'channels' はサイドバーで取得済みの全チャンネルリスト
            if channels:
                if st.button("全チャンネルのDLを準備", key="prepare_all_csv"):
                    with st.spinner('全チャンネルのデータを準備中...'):
                        df_all = get_time_series_data(selected_table, final_filters, channels, channel_column)
                        if not df_all.empty:
                            df_all_wide = df_all.pivot_table(index='time', columns='channel', values='value').reset_index()
                            st.session_state.all_channels_csv_data = df_all_wide.to_csv(index=False).encode('utf-8')
                            st.session_state.all_channels_filename = f"{selected_study}_{selected_subject}_{selected_session}_all_wide.csv"
                        else:
                            # データがなかった場合も処理済みとしてマーク
                            st.session_state.all_channels_csv_data = "EMPTY"
                
                # 準備されたデータがあればダウンロードボタンを表示
                if 'all_channels_csv_data' in st.session_state:
                    if st.session_state.all_channels_csv_data and st.session_state.all_channels_csv_data != "EMPTY":
                        st.download_button(
                            label="全チャンネルをダウンロード",
                            data=st.session_state.all_channels_csv_data,
                            file_name=st.session_state.all_channels_filename,
                            mime='text/csv'
                        )
                    # 準備したがデータがなかった場合
                    elif st.session_state.all_channels_csv_data == "EMPTY":
                        st.warning("対象データがありませんでした。")
                        # ユーザーが再度試せるようにステートをリセット
                        del st.session_state.all_channels_csv_data
                        if 'all_channels_filename' in st.session_state:
                            del st.session_state.all_channels_filename

# --- OpenSim解析パラメータのサマリー ---
if selected_table == 'osim_value' and selected_run:
    st.write("---")
    with st.expander("OpenSim解析パラメータの概要", expanded=False):
        summary_filters = {'run_id': selected_run}
        summary_tables = [
            "workflow_steps", "session_specific_parameters", "muscle_parameters",
            "osim_validation", "marker_task_parameters"
        ]
        
        for table in summary_tables:
            with st.spinner(f"'{table}'の情報を取得中..."):
                summary_df = cached_get_summary_data(table, summary_filters)
                if not summary_df.empty:
                    st.subheader(f"`{table}`")
                    st.dataframe(summary_df)
                else:
                    st.write(f"_{table} には、このRun IDのデータがありません。_")