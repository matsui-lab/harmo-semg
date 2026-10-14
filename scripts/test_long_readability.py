"""Direct per-file long-readability validator.

A file is considered readable when:
1) path is resolved by config mapping pattern
2) it can be converted into pandas long DataFrame with columns
   ['time', 'channel', 'value'] and at least one row.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from harmo.config.loader import load_config
from harmo.ingest.readers.c3d import read_c3d_file
from harmo.ingest.readers.sto import read_opensim_sto_trc


def _prepare_rule(rule_config: dict) -> dict:
    rule = dict(rule_config)
    spark_regex_str = rule["input_path_template"]
    patterns = rule.get("path_patterns", {})
    for ph, pattern in patterns.items():
        spark_regex_str = spark_regex_str.replace("{{{}}}".format(ph), pattern)
    rule["spark_native_regex_pattern"] = spark_regex_str
    return rule


def _find_matching_paths(rule: dict, base_input_dir: str) -> List[str]:
    py_regex = rule["spark_native_regex_pattern"].replace("/", r"[\\/]")
    compiled = re.compile(py_regex)

    ext = os.path.splitext(rule["input_path_template"])[-1]
    if not ext:
        ext = ".txt"

    base = Path(base_input_dir)
    matches: List[str] = []
    for p in base.rglob("*{}".format(ext)):
        rel = str(p.relative_to(base)).replace(os.path.sep, "/")
        if compiled.search(rel):
            matches.append(str(p.resolve()))
    return sorted(matches)


def _dedupe_columns(df: pd.DataFrame) -> pd.DataFrame:
    if len(df.columns) == len(set(df.columns)):
        return df
    cnt = Counter()
    new_cols = []
    for c in df.columns:
        idx = cnt[c]
        cnt[c] += 1
        new_cols.append(c if idx == 0 else "{}_{}".format(c, idx))
    out = df.copy()
    out.columns = new_cols
    return out


def _melt_to_long(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["time", "channel", "value"])

    work = _dedupe_columns(df)
    time_col = None
    for c in ["time", "Time", "TIME", "Header", "frame_num", "Frame#"]:
        if c in work.columns:
            time_col = c
            break

    if time_col is None:
        work = work.copy()
        work["time"] = range(len(work))
        time_col = "time"

    numeric_cols = [c for c in work.columns if c != time_col and pd.api.types.is_numeric_dtype(work[c])]
    if not numeric_cols:
        for c in work.columns:
            if c == time_col:
                continue
            coerced = pd.to_numeric(work[c], errors="coerce")
            if coerced.notna().any():
                work[c] = coerced
                numeric_cols.append(c)

    if not numeric_cols:
        return pd.DataFrame(columns=["time", "channel", "value"])

    long_df = work.melt(id_vars=[time_col], value_vars=numeric_cols, var_name="channel", value_name="value")
    if time_col != "time":
        long_df = long_df.rename(columns={time_col: "time"})

    long_df["time"] = pd.to_numeric(long_df["time"], errors="coerce")
    long_df["value"] = pd.to_numeric(long_df["value"], errors="coerce")
    long_df = long_df.dropna(subset=["time", "value"])
    if long_df.empty:
        return pd.DataFrame(columns=["time", "channel", "value"])

    return long_df[["time", "channel", "value"]]


def _read_opensim_quick(file_path: str) -> pd.DataFrame:
    _hdr, df = read_opensim_sto_trc(file_path)
    return df.head(40)


def _read_csv_quick(file_path: str, rule: dict) -> pd.DataFrame:
    opts = dict(rule.get("reader_options", {}))
    sep = opts.get("sep", ",")
    comment = opts.get("comment", None)
    header = 0 if opts.get("header", True) else None
    return pd.read_csv(file_path, sep=sep, comment=comment, header=header, nrows=40)


def _read_with_plugin(file_path: str, rule: dict) -> pd.DataFrame:
    plugin_name = rule.get("reader_plugin")
    if not plugin_name:
        raise ValueError("reader_plugin is required for MAT readability test")

    module = importlib.import_module("harmo.ingest.plugins.{}".format(plugin_name))
    plugin_cls = getattr(module, "Plugin", None) or getattr(module, "PluginInstance", None)
    if plugin_cls is None:
        raise AttributeError("plugin '{}' has no Plugin class".format(plugin_name))
    plugin = plugin_cls() if callable(plugin_cls) else plugin_cls

    plugin_options = dict(rule.get("plugin_options", {}))
    plugin_options["_rule_config"] = rule
    plugin_options.setdefault("max_rows", 40)
    return plugin.extract(file_path, plugin_options)


def _read_file_to_long(file_path: str, rule: dict) -> pd.DataFrame:
    fmt = (rule.get("file_format") or "csv").lower()

    if fmt == "mat":
        out = _read_with_plugin(file_path, rule)
        required = {"time", "channel", "value"}
        if not required.issubset(out.columns):
            out = _melt_to_long(out)
        return out[["time", "channel", "value"]]

    if fmt == "c3d":
        data_source = rule.get("c3d_data_source", "point")
        ch_keywords = rule.get("analog_channel_keywords")
        pdf = read_c3d_file(file_path, data_source=data_source, channel_keywords=ch_keywords)
        return _melt_to_long(pdf.head(40))

    if fmt in ("opensim", "sto", "mot", "trc"):
        pdf = _read_opensim_quick(file_path)
        return _melt_to_long(pdf)

    if fmt in ("csv", "tsv"):
        pdf = _read_csv_quick(file_path, rule)
        return _melt_to_long(pdf)

    raise ValueError("Unsupported file_format '{}'".format(fmt))


def _test_mapping(config_path: str, base_input_dir: str, mapping: dict) -> Dict[str, Any]:
    rule = _prepare_rule(mapping)
    matched = _find_matching_paths(rule, base_input_dir)

    out: Dict[str, Any] = {
        "mapping_name": mapping.get("name", "?"),
        "file_format": mapping.get("file_format", "csv"),
        "matched_count": len(matched),
        "checked_count": 0,
        "status": "failed",
        "sample_file": matched[0] if matched else None,
        "row_count": 0,
        "error": None,
        "failed_files": [],
    }

    if not matched:
        out["error"] = "No file matched input_path_template"
        return out

    total_rows = 0
    failed_files: List[Dict[str, str]] = []

    for fp in matched:
        try:
            long_df = _read_file_to_long(fp, rule)
            if long_df.empty:
                failed_files.append({"file": fp, "error": "Long DataFrame is empty"})
                continue
            required = {"time", "channel", "value"}
            if not required.issubset(set(long_df.columns)):
                failed_files.append({"file": fp, "error": "Missing required long columns"})
                continue
            total_rows += int(len(long_df))
        except Exception as exc:
            failed_files.append({"file": fp, "error": str(exc)})

    out["checked_count"] = len(matched)
    out["row_count"] = total_rows
    out["failed_files"] = failed_files[:20]

    if failed_files:
        out["status"] = "failed"
        out["error"] = "{}/{} files failed".format(len(failed_files), len(matched))
    else:
        out["status"] = "passed"
        out["error"] = None

    return out


def run(study_configs: List[str]) -> Dict[str, Any]:
    results: Dict[str, Any] = {"studies": [], "summary": {}}

    total = 0
    passed = 0

    for config_path in study_configs:
        print("[study] {}".format(config_path), flush=True)
        cfg = load_config(config_path)
        aliases = cfg.get("input_dir_aliases", {})

        study_entry: Dict[str, Any] = {
            "config": config_path,
            "mappings": [],
            "passed": 0,
            "failed": 0,
        }

        for mapping in cfg.get("file_mappings", []):
            print("  [mapping] {}".format(mapping.get("name", "?")), flush=True)
            alias = mapping.get("base_input_dir_alias")
            if not alias or alias not in aliases:
                study_entry["mappings"].append({
                    "mapping_name": mapping.get("name", "?"),
                    "file_format": mapping.get("file_format", "csv"),
                    "matched_count": 0,
                    "checked_count": 0,
                    "status": "failed",
                    "sample_file": None,
                    "row_count": 0,
                    "error": "base_input_dir_alias not found",
                    "failed_files": [],
                })
                study_entry["failed"] += 1
                total += 1
                continue

            res = _test_mapping(config_path, aliases[alias], mapping)
            study_entry["mappings"].append(res)
            print(
                "    -> {} (matched={}, failed_files={})".format(
                    res.get("status"),
                    res.get("matched_count", 0),
                    len(res.get("failed_files", [])),
                ),
                flush=True,
            )
            if res["status"] == "passed":
                study_entry["passed"] += 1
                passed += 1
            else:
                study_entry["failed"] += 1
            total += 1

        results["studies"].append(study_entry)
        print(
            "[study_done] passed={} failed={}".format(study_entry["passed"], study_entry["failed"]),
            flush=True,
        )

    results["summary"] = {
        "total_mappings": total,
        "passed_mappings": passed,
        "failed_mappings": total - passed,
    }
    print(
        "[summary] total={} passed={} failed={}".format(total, passed, total - passed),
        flush=True,
    )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Per-file long-format readability test")
    parser.add_argument("--output", required=True, help="Path to JSON output")
    parser.add_argument("configs", nargs="+", help="Study config paths")
    args = parser.parse_args()

    result = run(args.configs)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
