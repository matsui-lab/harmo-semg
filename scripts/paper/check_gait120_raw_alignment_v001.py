"""Check source-code-defined sample intervals; do not estimate treatment effects."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import urllib.request
import zipfile

import numpy as np
from scipy.io import loadmat

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))
from check_scientific_reuse_readiness_v001 import ASSETS, digests, protected_inputs
from harmo.physiology_control import _numeric_mat_value, load_physiology_protocol
from harmo.ingest.plugins.mat_mcos_explicit import _get_nested_mat_data, _expand_template

CODE_URL = "https://ndownloader.figshare.com/files/53172812"
CODE_SHA = "fba7a633f65066242903129c274c4dde3cb60fb314034345fc62619c7f7b0075"


def integers(value):
    values = np.asarray(value, dtype=float).reshape(-1)
    if not np.isfinite(values).all() or not np.all(values == np.round(values)):
        raise ValueError("noninteger frame or index")
    return values.astype(int)


def sample_interval(total, target, fs, marker_fs, n_samples):
    """Python half-open translation of public MATLAB's one-based inclusive rule."""
    total, target = integers(total), integers(target)
    if len(total) != 2 or len(target) != 2 or not 0 < marker_fs <= fs:
        raise ValueError("invalid metadata")
    ratio = fs / marker_fs
    if not np.isfinite(ratio) or ratio != round(ratio):
        raise ValueError("noninteger sampling ratio")
    ratio = int(ratio)
    if not total[0] <= target[0] < target[1] <= total[1]:
        raise ValueError("cycle outside trial bounds")
    if n_samples != (total[1] - total[0] + 1) * ratio:
        raise ValueError("trial length inconsistent with source frame rule")
    start = ratio * (target[0] - total[0])
    stop = ratio * (target[1] - total[0] + 1)
    return int(start), int(stop)


def field(data, key):
    return _get_nested_mat_data(data, key)


def run(out, code_path=None):
    if out.exists():
        raise FileExistsError(out)
    before = protected_inputs(ROOT)
    protocol = load_physiology_protocol(ROOT / "paper/physiology_positive_control_protocol.yaml")
    if code_path is None:
        with urllib.request.urlopen(CODE_URL, timeout=30) as stream:
            code_bytes = stream.read()
    else:
        code_bytes = code_path.read_bytes()
    if hashlib.sha256(code_bytes).hexdigest() != CODE_SHA:
        raise ValueError("public code differs from inspected version")
    with zipfile.ZipFile(io.BytesIO(code_bytes)) as source:
        construction = source.read("emgProcessedDataConstruction.m").decode("utf-8-sig")
        signal_code = source.read("signalProcess.m").decode("utf-8-sig")
        source_readme = source.read("README.md").decode("utf-8-sig")
    expected = [
        "target_emg_frame_start = emg_trajectory_fs_multiplier * (target_frame_start - csv_frame_start) + 1;",
        "target_emg_frame_end = emg_trajectory_fs_multiplier * (target_frame_end - csv_frame_start+1);",
    ]
    if any(x not in construction for x in expected):
        raise ValueError("source cutting rule not found")
    locators = {x.split(" = ")[0]: next(i for i, line in enumerate(construction.splitlines(), 1) if x in line)
                for x in expected}
    archive_path = ROOT / ASSETS / "Gait120_011_to_020.zip"
    identity = {"bytes": archive_path.stat().st_size, **digests(archive_path)}
    spec = protocol["source_assets"]["study_120"][archive_path.name]
    if identity["bytes"] != spec["bytes"] or identity["md5"] != spec["md5"]:
        raise ValueError("raw archive identity mismatch")
    rows, participants = [], []
    patterns = {"trial": r"Trial\d+", "step": r"Step\d+"}
    with zipfile.ZipFile(archive_path) as archive:
        for sid in protocol["required_studies"]["study_120"]["subjects"]:
            raw = loadmat(io.BytesIO(archive.read(f"{sid}/EMG/RawData.mat")))
            processed = loadmat(io.BytesIO(archive.read(f"{sid}/EMG/ProcessedData.mat")))
            fs, = integers(field(raw, "EMGs_info.fs"))
            marker_fs, = integers(field(raw, "Markers_info.fs"))
            trials = integers(field(raw, "LevelWalking.AvailableTrialIdx"))
            proc_trials = integers(field(processed, "LevelWalking.AvailableTrialIdx"))
            if not np.array_equal(trials, proc_trials) or len(set(trials)) != len(trials):
                raise ValueError("raw and processed trial sets differ")
            if int(integers(field(raw, "LevelWalking.nTrials"))[0]) != len(trials):
                raise ValueError("nTrials inconsistent")
            expected_steps = set()
            for tid in trials:
                trial = f"LevelWalking.Trial{tid:02d}"
                array, columns = _numeric_mat_value(raw, trial + ".EMGs_raw")
                if columns is None or len(set(columns)) != 12 or array.ndim != 2 or array.shape[1] != 12:
                    raise ValueError("unresolved raw channel table")
                n_steps, = integers(field(raw, trial + ".nSteps"))
                if n_steps != integers(field(processed, trial + ".nSteps"))[0]:
                    raise ValueError("step counts differ")
                total = integers(field(raw, trial + ".TotalFrame"))
                for step in range(1, n_steps + 1):
                    prefix = trial + f".Step{step:02d}"
                    expected_steps.add(prefix)
                    target = integers(field(raw, prefix + ".TargetFrame"))
                    start, stop = sample_interval(total, target, fs, marker_fs, len(array))
                    if not np.isfinite(array[start:stop]).all():
                        raise ValueError("nonfinite raw cycle")
                    shapes = {}
                    for stage in ("EMGs_filt", "EMGs_env", "EMGs_norm", "EMGs_interpolated"):
                        values, stage_columns = _numeric_mat_value(processed, prefix + "." + stage)
                        shape = (101, 12) if stage == "EMGs_interpolated" else (stop - start, 12)
                        if stage_columns != columns or values.shape != shape or not np.isfinite(values).all():
                            raise ValueError("processed table does not align with source interval/labels")
                        shapes[stage] = list(values.shape)
                    rows.append({"participant": sid, "cycle": prefix, "fs": int(fs),
                                 "marker_fs": int(marker_fs), "trial_frames": total.tolist(),
                                 "cycle_frames": target.tolist(), "raw_trial_samples": len(array),
                                 "python_start": start, "python_stop_exclusive": stop,
                                 "cycle_samples": stop - start, "source_labels": columns,
                                 "processed_shapes": shapes})
            observed_raw = {k.rsplit(".", 1)[0] for k, _ in _expand_template(raw, "LevelWalking.{trial}.{step}.TargetFrame", patterns)}
            observed_processed = {k.rsplit(".", 1)[0] for k, _ in _expand_template(processed, "LevelWalking.{trial}.{step}.EMGs_interpolated", patterns)}
            if observed_raw != expected_steps or observed_processed != expected_steps:
                raise ValueError("unmatched or unlisted cycle")
            participants.append({"participant": sid, "trials": len(trials), "cycles": len(expected_steps)})
            print(f"Aligned {sid}: {len(trials)} trials, {len(expected_steps)} cycles", flush=True)
    if before != protected_inputs(ROOT):
        raise ValueError("protected input changed")
    result = {"status": "source_defined_intervals_and_tables_aligned",
              "not_tested": ["numerical reproduction of original MATLAB processing", "new harmonization effects"],
              "code": {"url": CODE_URL, "bytes": len(code_bytes), "sha256": CODE_SHA,
                       "license": "CC BY 4.0; retain attribution to Junyo Boo (2025)",
                       "file": "emgProcessedDataConstruction.m", "line_locators": locators},
              "raw_archive": identity, "participants": participants, "cycles": rows,
              "n_participants": len(participants), "n_trials": sum(x["trials"] for x in participants),
              "n_cycles": len(rows), "protected_inputs_unchanged": True,
              "analysis_sha256": digests(Path(__file__))["sha256"]}
    out.mkdir(parents=True, exist_ok=False)
    (out / "Matlab_Codes_for_Processing_source.zip").write_bytes(code_bytes)
    (out / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    report = f"""# Gait120原信号と公開処理後周期の対応確認

公開リリースv1の処理コード（Junyo Boo, 2025）に従い、{len(participants)}参加者・{result['n_trials']}試行・{len(rows)}周期を確認した。全対象で標本化周波数比、試行・周期集合、切り出し範囲、12筋ラベル、4段階の処理後表の長さと有限値が一致した。

## 切り出し規則

EMG周波数と動作計測周波数の比をr、試行開始フレームをT、周期の開始・終了フレームをa・bとすると、Pythonの半開区間は[r(a−T), r(b−T+1))。元コードはMATLABの1始まり・両端を含む区間で、開始標本に1を加える。S011 Trial01 Step01の場合はPython標本200〜2459、計2260標本となる。

これは標本数から推測した対応ではなく、公開コードで定義された対応である。ただし、元MATLAB処理を数値再現したことや、別処理条件の結果が妥当であることまでは確認していない。

## 処理比較で分ける必要がある点

公開コードは試行全体を処理してから周期を切り出す。現在のLencioni経路は提供済みの分割周期を処理するため、前後の信号の有無が異なる。また、Gait120コードには線形トレンド除去、2次20–500 Hzフィルタ、Hampel、移動平均によるRMS、スプライン補間があり、現行のLencioni設定と同一ではない。原著の簡潔な記述だけでは完全な同一処理を主張できない。

今回の端点RMS対照とは別に、同じコード・パラメータを両研究に適用する対照と、Gait120内で試行全体を処理する場合と周期切り出し後に処理する場合の対照を設計できる。ただしLencioniに存在しない前後信号は回復できず、どの比較も生理学的真値の保証とはしない。

## 出典

- [公開コード](https://ndownloader.figshare.com/files/53172812)：emgProcessedDataConstruction.mの切り出し式（{locators['target_emg_frame_start']}〜{locators['target_emg_frame_end']}行）とsignalProcess.m。原アーカイブを改変せず同梱した。
- [Gait120原著](https://www.nature.com/articles/s41597-025-05391-0)：Methodsの同期計測、右脚のセンサー、同側踵接地間周期の説明。

正式原稿・既存検証結果は変更していない。
"""
    (out / "REPORT.md").write_text(report)
    print(json.dumps({k: result[k] for k in ("n_participants", "n_trials", "n_cycles", "status")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--code-archive", type=Path)
    args = parser.parse_args()
    run(args.output if args.output.is_absolute() else ROOT / args.output, args.code_archive)
