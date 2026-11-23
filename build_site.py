# -*- coding: utf-8 -*-
"""
GitHub Pages(정적 웹앱)용 데이터 빌드 스크립트

엑셀 전처리 → LSTM·선형회귀 학습 → 결과와 LSTM 가중치를 docs/data.js 로 저장합니다.
브라우저(docs/app.js)는 이 파일만 읽어서 시뮬레이터·차트·표를 모두 그립니다.

사용법
    python build_site.py                 # data/ 폴더의 엑셀 사용
    DATA_PATH=/경로/파일.xlsx python build_site.py
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import pipeline as pl

OUT_FILE = pl.BASE_DIR / "docs" / "data.js"


# ------------------------------------------------------------------------------
# JSON 변환 도우미
# ------------------------------------------------------------------------------
def _clean(value):
    """numpy/pandas 값을 JSON으로 저장 가능한 파이썬 기본형으로 바꿉니다."""
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        value = float(value)
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, np.ndarray):
        return [_clean(v) for v in value.tolist()]
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if value is pd.NA or value is None:
        return None
    return value


def table(df: pd.DataFrame) -> dict:
    """DataFrame → {"columns": [...], "rows": [[...], ...]}"""
    return {
        "columns": [str(c) for c in df.columns],
        "rows": [[_clean(v) for v in row] for row in df.itertuples(index=False, name=None)],
    }


def records(df: pd.DataFrame, cols: list[str] | None = None) -> list[dict]:
    cols = cols or list(df.columns)
    return [{c: _clean(v) for c, v in zip(cols, row)} for row in df[cols].itertuples(index=False, name=None)]


# ------------------------------------------------------------------------------
# LSTM 가중치 내보내기 + 검증
# ------------------------------------------------------------------------------
def export_lstm(model) -> dict:
    """PyTorch state_dict → 브라우저에서 계산할 수 있는 순수 숫자 배열."""
    sd = {k: v.detach().cpu().numpy() for k, v in model.state_dict().items()}
    return {
        "hidden": int(sd["lstm.weight_hh_l0"].shape[1]),
        "w_ih": _clean(sd["lstm.weight_ih_l0"]),   # (4H, 입력)  게이트 순서: i, f, g, o
        "w_hh": _clean(sd["lstm.weight_hh_l0"]),   # (4H, H)
        "b_ih": _clean(sd["lstm.bias_ih_l0"]),
        "b_hh": _clean(sd["lstm.bias_hh_l0"]),
        "fc1_w": _clean(sd["fc.0.weight"]),        # (16, H)
        "fc1_b": _clean(sd["fc.0.bias"]),
        "fc2_w": _clean(sd["fc.2.weight"]),        # (1, 16)
        "fc2_b": _clean(sd["fc.2.bias"]),
    }


def numpy_lstm_predict(w: dict, window: np.ndarray) -> float:
    """내보낸 가중치로 LSTM을 numpy로 다시 계산 (브라우저 app/model.js 와 같은 수식)."""
    sigmoid = lambda z: 1.0 / (1.0 + np.exp(-z))
    w_ih, w_hh = np.array(w["w_ih"]), np.array(w["w_hh"])
    bias = np.array(w["b_ih"]) + np.array(w["b_hh"])
    hid = w["hidden"]
    h, c = np.zeros(hid), np.zeros(hid)
    for x in window:
        gates = w_ih @ x + w_hh @ h + bias
        i, f, g, o = (gates[k * hid:(k + 1) * hid] for k in range(4))
        c = sigmoid(f) * c + sigmoid(i) * np.tanh(g)
        h = sigmoid(o) * np.tanh(c)
    a = np.maximum(0.0, np.array(w["fc1_w"]) @ h + np.array(w["fc1_b"]))
    return float(sigmoid(np.array(w["fc2_w"]) @ a + np.array(w["fc2_b"]))[0])


def verify_export(state: dict, weights: dict) -> float:
    """PyTorch 예측값과 '내보낸 가중치 기반 계산'이 일치하는지 확인합니다. (최대 오차 %p 반환)"""
    df_seq, scaler = state["df_seq"], state["scaler"]
    worst = 0.0
    for _, grp in df_seq.groupby("day_idx"):
        feats = scaler.transform(grp[pl.FEATURE_COLS])
        for t in range(len(feats)):
            pred = numpy_lstm_predict(weights, pl.make_window(feats, t)) * 100.0
            torch_pred = float(grp["LSTM_예측지수(%)"].iloc[t])
            worst = max(worst, abs(round(pred, 2) - torch_pred))
    return worst


# ------------------------------------------------------------------------------
# 화면용 데이터 구성
# ------------------------------------------------------------------------------
def build_payload(state: dict, weights: dict, source_name: str) -> dict:
    df_seq, md, df2, st = state["df_seq"], state["merged_daily"], state["df2"], state["stats"]

    # 시뮬레이터 기본값: 하교 승차가 가장 많은 날 / 그날 승차가 가장 많은 하교 시간대
    peak_day_seq = df_seq[
        (df_seq["기준일자"] == st["peak_date"]) & (df_seq["시간"].isin(pl.AFTER_SCHOOL_HOURS))
    ]
    default_hour = peak_day_seq.loc[peak_day_seq["신성여고_승차"].idxmax(), "시간대"]

    stop_cols = ["정류장순번", "정류장명", "16시", "17시", "18시", "하교재차합(16-18시)"]
    scaler = state["scaler"]

    return {
        "meta": {
            "source": source_name,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "n_days": int(state["n_days"]),
            "val_days": pl.VAL_DAYS,
            "seq_len": pl.SEQ_LEN,
            "epochs": pl.EPOCHS,
            "danger": pl.DANGER_THRESHOLD,
            "caution": pl.CAUTION_THRESHOLD,
            "target_hours": pl.TARGET_HOURS,
            "after_school_hours": list(pl.AFTER_SCHOOL_HOURS),
            "default_date": st["peak_date"],
            "default_hour": default_hour,
            "slider_max": {
                "board": max(35, int(df_seq["신성여고_승차"].max())),
                "alight": max(15, int(df_seq["신성여고_하차"].max())),
                "cum": max(45, int(df_seq["하교누적승차"].max())),
            },
        },
        "feature_cols": pl.FEATURE_COLS,
        "scaler": {"scale": _clean(scaler.scale_), "min": _clean(scaler.min_)},  # x' = x * scale + min
        "lstm": weights,
        "stats": _clean(st),
        "losses": {"train": _clean(state["train_losses"]), "val": _clean(state["val_losses"])},
        "seq": records(df_seq),
        "stops": records(df2, stop_cols),
        "metrics": table(state["metrics_df"]),
        "prep_report": table(state["prep_report_df"]),
        "derived_kpi": table(state["derived_kpi_df"]),
        "daily": table(md),
    }


def write_data_js(payload: dict, path: Path = OUT_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    path.write_text(f"window.BUS_DATA = {text};\n", encoding="utf-8")


def main() -> None:
    excel_path = pl.find_excel()
    state = pl.build_state(excel_path)

    print("\n[모델 성능 비교]")
    print(state["metrics_df"].to_string(index=False))

    weights = export_lstm(state["lstm_model"])
    worst = verify_export(state, weights)
    print(f"\n[검증] PyTorch ↔ 내보낸 가중치 최대 오차: {worst:.4f}%p")
    if worst > 0.05:
        raise SystemExit("❌ 내보낸 LSTM 가중치로 계산한 값이 PyTorch와 다릅니다. 빌드를 중단합니다.")

    payload = build_payload(state, weights, excel_path.name)
    write_data_js(payload)
    size_kb = OUT_FILE.stat().st_size / 1024
    print(f"\n✅ 저장 완료: {OUT_FILE.relative_to(pl.BASE_DIR)} ({size_kb:,.0f} KB)")
    print("   이제 docs/ 폴더를 GitHub에 올리면 됩니다.")


if __name__ == "__main__":
    main()
