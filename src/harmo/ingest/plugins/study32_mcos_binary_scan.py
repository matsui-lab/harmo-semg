"""Study 32 MAT fallback plugin.

This plugin explicitly targets diverse Study 32 MAT structures by
scanning candidate numeric time-series keys and converting the selected
array into tidy long format.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.io import loadmat

from harmo.ingest.plugins.base import BaseIngestPlugin
from harmo.ingest.readers.mat import find_timeseries_in_mat, read_mat_file


class Plugin(BaseIngestPlugin):
    name = "study32_mcos_binary_scan"
    version = "1.0.0"

    def _rank_candidates(
        self,
        candidates: List[Tuple[str, Tuple[int, ...], str]],
        preferred_patterns: List[str],
    ) -> List[str]:
        compiled = [re.compile(p) for p in preferred_patterns if p]

        def score(item: Tuple[str, Tuple[int, ...], str]) -> Tuple[int, int]:
            key, shape, _dtype = item
            preferred = 1 if any(p.search(key) for p in compiled) else 0
            volume = int(np.prod(shape)) if shape else 0
            return (preferred, volume)

        ranked = sorted(candidates, key=score, reverse=True)
        return [k for k, _shape, _dtype in ranked]

    def _resolve_best_dataframe(self, file_path: str, options: Dict[str, Any]) -> pd.DataFrame:
        min_rows = int(options.get("candidate_min_rows", 50))
        preferred = options.get("prefer_key_patterns", [r"EMG", r"Sensor", r"Raw", r"Data"])
        if isinstance(preferred, str):
            preferred = [preferred]

        candidates = find_timeseries_in_mat(file_path, min_rows=min_rows)
        ranked_keys = self._rank_candidates(candidates, preferred)

        explicit_keys = options.get("data_keys")
        if isinstance(explicit_keys, str):
            explicit_keys = [explicit_keys]
        if isinstance(explicit_keys, list):
            ranked_keys = list(explicit_keys) + ranked_keys

        seen = set()
        ordered = []
        for key in ranked_keys:
            if key not in seen:
                ordered.append(key)
                seen.add(key)

        trial_errors: List[str] = []
        for data_key in ordered:
            for transpose in (False, True):
                reader_options = dict(options.get("reader_options", {}))
                reader_options["data_key"] = data_key
                reader_options["transpose_data"] = transpose
                try:
                    return read_mat_file(file_path, reader_options)
                except Exception as exc:  # pragma: no cover - diagnostic path
                    trial_errors.append("{} (transpose={}): {}".format(data_key, transpose, exc))

        # Fallback for deeply nested struct/object MAT files.
        raw_mat = loadmat(file_path, squeeze_me=False)
        fallback_candidates: List[Tuple[str, np.ndarray]] = []

        def walk(obj: Any, path: str, depth: int = 0) -> None:
            if depth > 8:
                return

            if isinstance(obj, dict):
                for key, val in obj.items():
                    if key.startswith("__"):
                        continue
                    next_path = "{}.{}".format(path, key) if path else key
                    walk(val, next_path, depth + 1)
                return

            if isinstance(obj, np.ndarray):
                if obj.dtype.names is not None:
                    if obj.size > 0:
                        base = obj.flat[0]
                        for field in obj.dtype.names:
                            try:
                                walk(base[field], "{}.{}".format(path, field), depth + 1)
                            except Exception:
                                continue
                    return

                if obj.dtype == object:
                    for idx, item in enumerate(obj.flat):
                        walk(item, "{}[{}]".format(path, idx), depth + 1)
                    return

                if obj.dtype.kind in ("f", "i", "u", "c") and obj.ndim >= 2:
                    if max(obj.shape) >= min_rows:
                        fallback_candidates.append((path, obj))
                return

            if isinstance(obj, np.void) and obj.dtype.names is not None:
                for field in obj.dtype.names:
                    walk(obj[field], "{}.{}".format(path, field), depth + 1)

        walk(raw_mat, "")
        if fallback_candidates:
            preferred_patterns = [re.compile(p) for p in preferred if p]

            def _score(item: Tuple[str, np.ndarray]) -> Tuple[int, int]:
                pth, arr = item
                preferred_hit = 1 if any(r.search(pth) for r in preferred_patterns) else 0
                return (preferred_hit, int(arr.size))

            best_path, best_arr = sorted(fallback_candidates, key=_score, reverse=True)[0]
            trial_errors.append("fallback_array={}".format(best_path))
            arr = np.asarray(best_arr)
            if arr.ndim == 1:
                arr = arr.reshape(-1, 1)
            if arr.shape[0] < arr.shape[1]:
                arr = arr.T

            channel_prefix = str(options.get("channel_prefix", "ch"))
            ch_cols = ["{}{}".format(channel_prefix, i + 1) for i in range(arr.shape[1])]
            wide_df = pd.DataFrame(arr, columns=ch_cols)
            wide_df["time"] = np.arange(arr.shape[0], dtype=float)
            return wide_df

        raise RuntimeError(
            "No readable MAT key found for {}. Tried keys: {}".format(
                file_path,
                "; ".join(trial_errors[:8]),
            )
        )

    def _to_long(self, wide_df: pd.DataFrame, max_rows: Optional[int] = None) -> pd.DataFrame:
        if max_rows is not None and max_rows > 0:
            wide_df = wide_df.head(int(max_rows)).copy()
        time_col_candidates = ["time", "Time", "TIME", "frame", "Frame", "Header"]
        time_col: Optional[str] = None
        for col in time_col_candidates:
            if col in wide_df.columns:
                time_col = col
                break

        work_df = wide_df.copy()
        if time_col is None:
            work_df["time"] = np.arange(len(work_df), dtype=float)
            time_col = "time"

        numeric_cols = [
            c for c in work_df.columns
            if c != time_col and pd.api.types.is_numeric_dtype(work_df[c])
        ]
        if not numeric_cols:
            raise ValueError("No numeric channel columns found after MAT extraction")

        long_df = work_df.melt(
            id_vars=[time_col],
            value_vars=numeric_cols,
            var_name="channel",
            value_name="value",
        )
        if time_col != "time":
            long_df = long_df.rename(columns={time_col: "time"})

        long_df["time"] = pd.to_numeric(long_df["time"], errors="coerce")
        long_df["value"] = pd.to_numeric(long_df["value"], errors="coerce")
        long_df = long_df.dropna(subset=["time", "value"])
        return long_df[["time", "channel", "value"]]

    def extract(self, file_path: str, options: Dict[str, Any]) -> pd.DataFrame:
        wide_df = self._resolve_best_dataframe(file_path, options)
        max_rows = options.get("max_rows")
        return self._to_long(wide_df, max_rows=max_rows)
