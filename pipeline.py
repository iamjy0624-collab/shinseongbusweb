# -*- coding: utf-8 -*-
"""
411-1 하교 만차 예측 파이프라인 (전처리 · 통합 · 모델 학습)

웹 화면(Gradio/Plotly)과 무관한 '계산 전용' 모듈입니다.
build_site.py 가 이 모듈로 결과를 계산해 docs/data.js 로 내보냅니다.
"""
from __future__ import annotations

import os
import time
import unicodedata
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.preprocessing import MinMaxScaler

warnings.filterwarnings("ignore")

# ==============================================================================
# [설정] 경로 · 하이퍼파라미터 · 임계값
# ==============================================================================
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

TARGET_EXCEL = "411-1_세개데이터_순서대로_전체나열.xlsx"

TARGET_HOURS = list(range(16, 23))      # 분석 대상 시간대 (16~22시)
AFTER_SCHOOL_HOURS = (16, 17, 18)       # 하교 시간대
FEATURE_COLS = [
    "시간", "노선_시간이용량", "신성여고_승차", "신성여고_하차",
    "승차편중비율", "신성여고_재차", "하교누적승차",
]

SEQ_LEN = 3          # LSTM 입력 윈도우 길이(시간)
VAL_DAYS = 2         # 마지막 N일을 검증 데이터로 사용
EPOCHS = 250
LEARNING_RATE = 0.015
HIDDEN_SIZE = 32
SEED = 42

DANGER_THRESHOLD = 85.0    # 이상이면 '만차·무정차 고위험' → 우회 권장
CAUTION_THRESHOLD = 65.0   # 이상이면 '혼잡 주의'

# 혼잡 위험지수 산식의 정규화 상수
RISK_BOARD_NORM = 13.0
RISK_CUM_NORM = 28.0
RISK_OCC_NORM = 5.0


# ==============================================================================
# [1단계] 엑셀 파일 찾기
# ==============================================================================
def _nfc(text: str) -> str:
    """macOS에서 만든 한글 파일명(NFD)도 비교할 수 있도록 NFC로 통일합니다."""
    return unicodedata.normalize("NFC", text)


def find_excel() -> Path:
    """data/ 폴더(또는 저장소 루트)에서 원천 엑셀 파일을 찾습니다."""
    env_path = os.environ.get("DATA_PATH")
    if env_path:
        path = Path(env_path).expanduser()
        if path.is_file():
            print(f"✅ 엑셀 파일 로드: {path}")
            return path
        raise FileNotFoundError(f"DATA_PATH로 지정한 파일이 없습니다: {path}")

    files = [
        p
        for folder in (DATA_DIR, BASE_DIR)
        if folder.is_dir()
        for p in sorted(folder.glob("*.xlsx"))
        if not p.name.startswith("~$")  # 엑셀 임시(잠금) 파일 제외
    ]

    chosen = (
        next((p for p in files if _nfc(p.name) == TARGET_EXCEL), None)
        or next((p for p in files if "전체나열" in _nfc(p.name)), None)
    )
    if chosen is None:
        in_data = [p for p in files if p.parent == DATA_DIR]
        if len(in_data) == 1:
            chosen = in_data[0]

    if chosen is None:
        raise FileNotFoundError(
            f"엑셀 파일을 찾을 수 없습니다. '{TARGET_EXCEL}' 파일을 "
            f"'{DATA_DIR}' 폴더에 넣거나 DATA_PATH 환경 변수로 경로를 지정해 주세요."
        )
    print(f"✅ 엑셀 파일 로드: {chosen}")
    return chosen


# ==============================================================================
# [2단계] 전처리 · 통합
# ==============================================================================
def load_and_clean(excel_path: Path) -> dict:
    """3개 시트를 읽어 정제하고 일자 기준으로 병합합니다."""
    # 각 시트의 1행은 제목 배너이므로 header=1(엑셀 2행)로 읽습니다.
    df1_raw = pd.read_excel(excel_path, sheet_name="1_노선별_이용량", header=1)
    df2_raw = pd.read_excel(excel_path, sheet_name="2_노선별_차내재차인원", header=1)
    df3_raw = pd.read_excel(excel_path, sheet_name="3_정류장별_이용량", header=1)
    for df in (df1_raw, df2_raw, df3_raw):
        df.columns = [str(c).strip() for c in df.columns]

    # --- [시트 1] 상단 '합계' 행 제외, 평일 10일 추출 ---
    df1 = df1_raw[df1_raw["노선"].astype(str).str.strip() != "합계"].copy()
    df1 = df1[df1["일"].notna()].copy().reset_index(drop=True)
    df1["기준일자"] = df1["일"].astype(str).str.strip()
    df1["노선"] = "411-1"
    hours_f1 = [f"{h:02d}" for h in range(6, 23)]
    for c in ["합계"] + hours_f1:
        df1[c] = pd.to_numeric(df1[c], errors="coerce").fillna(0).astype(int)

    df1["노선_등교이용량(07-08시)"] = df1["07"] + df1["08"]
    df1["노선_하교이용량(16-18시)"] = df1["16"] + df1["17"] + df1["18"]
    df1["노선_하교집중률(%)"] = (df1["노선_하교이용량(16-18시)"] / df1["합계"] * 100).round(2)

    # --- [시트 2] 정류장순번 41~66번 추출, 56번 vs 57번 비교 ---
    df2 = df2_raw[df2_raw["정류장순번"].notna()].copy()
    df2["정류장순번"] = pd.to_numeric(df2["정류장순번"]).astype(int)
    df2 = df2[(df2["정류장순번"] >= 41) & (df2["정류장순번"] <= 66)].copy().reset_index(drop=True)
    hours_f2 = [f"{h:02d}시" for h in range(4, 24)] + ["00시", "01시", "02시", "03시"]
    for c in hours_f2:
        df2[c] = pd.to_numeric(df2[c], errors="coerce").fillna(0).astype(int)
    df2["하교재차합(16-18시)"] = df2["16시"] + df2["17시"] + df2["18시"]

    stop_56 = df2[df2["정류장순번"] == 56].iloc[0]  # 영평초등학교차오름체육관
    stop_57 = df2[df2["정류장순번"] == 57].iloc[0]  # 신성여자중고등학교

    # --- [시트 3] 주말((토),(일)) 제거, 16~22시 승·하차 추출 ---
    df3 = df3_raw[df3_raw["일자"].notna()].copy()
    df3["기준일자"] = df3["일자"].astype(str).str.strip()
    df3 = df3[~df3["기준일자"].str.contains(r"\(토\)|\(일\)")].copy().reset_index(drop=True)
    for h in TARGET_HOURS:
        df3[f"{h:02d}(승차)"] = pd.to_numeric(df3[f"{h:02d}(승차)"], errors="coerce").fillna(0).astype(int)
        df3[f"{h:02d}(하차)"] = pd.to_numeric(df3[f"{h:02d}(하차)"], errors="coerce").fillna(0).astype(int)

    df3["신성여고_하교승차(16-18시)"] = df3["16(승차)"] + df3["17(승차)"] + df3["18(승차)"]
    df3["신성여고_하교하차(16-18시)"] = df3["16(하차)"] + df3["17(하차)"] + df3["18(하차)"]
    df3["신성여고_순탑승(16-18시)"] = df3["신성여고_하교승차(16-18시)"] - df3["신성여고_하교하차(16-18시)"]
    df3["신성여고_승차편중률(%)"] = (
        df3["신성여고_하교승차(16-18시)"]
        / (df3["신성여고_하교승차(16-18시)"] + df3["신성여고_하교하차(16-18시)"])
        * 100
    ).round(2)

    # --- [3개 데이터 일자별 병합] ---
    merged_daily = pd.merge(
        df1[
            ["기준일자", "노선", "합계", "노선_등교이용량(07-08시)", "노선_하교이용량(16-18시)", "노선_하교집중률(%)"]
            + [f"{h:02d}" for h in TARGET_HOURS]
        ],
        df3[
            ["기준일자", "신성여고_하교승차(16-18시)", "신성여고_하교하차(16-18시)",
             "신성여고_순탑승(16-18시)", "신성여고_승차편중률(%)"]
            + [col for h in TARGET_HOURS for col in (f"{h:02d}(승차)", f"{h:02d}(하차)")]
        ],
        on="기준일자",
        how="inner",
    ).rename(columns={"합계": "노선_일별이용량"})

    merged_daily["57번_신성여고_하교재차"] = int(stop_57["하교재차합(16-18시)"])
    merged_daily["56번_영평초체육관_하교재차"] = int(stop_56["하교재차합(16-18시)"])
    merged_daily["상류정류장_재차여유도(명)"] = (
        merged_daily["57번_신성여고_하교재차"] - merged_daily["56번_영평초체육관_하교재차"]
    )

    return {
        "df1_raw": df1_raw, "df2_raw": df2_raw, "df3_raw": df3_raw,
        "df1": df1, "df2": df2, "df3": df3,
        "stop_56": stop_56, "stop_57": stop_57,
        "merged_daily": merged_daily,
    }


def make_sequence_table(merged_daily: pd.DataFrame, stop_56: pd.Series, stop_57: pd.Series) -> pd.DataFrame:
    """일자 × 시간대(16~22시) 시계열 관측치와 혼잡 위험지수(라벨)를 만듭니다."""
    records = []
    for d_idx, row in merged_daily.iterrows():
        cum_aft_board = 0
        for h in TARGET_HOURS:
            h_str, h_kor = f"{h:02d}", f"{h:02d}시"
            b_val = int(row[f"{h_str}(승차)"])
            a_val = int(row[f"{h_str}(하차)"])
            r_vol = int(row[h_str])
            occ_57 = int(stop_57[h_kor])
            occ_56 = int(stop_56[h_kor])
            bias = b_val / (b_val + a_val) if (b_val + a_val) > 0 else 0.0

            if h in AFTER_SCHOOL_HOURS:
                cum_aft_board += b_val
            else:
                cum_aft_board = b_val

            is_peak_window = 1.0 if h in AFTER_SCHOOL_HOURS else 0.35
            raw_risk = (
                (b_val / RISK_BOARD_NORM) * 45.0
                + (cum_aft_board / RISK_CUM_NORM) * 40.0
                + (occ_57 / RISK_OCC_NORM) * 15.0
            ) * (0.55 + 0.45 * bias) * is_peak_window
            risk_pct = round(float(np.clip(raw_risk, 0.0, 100.0)), 2)

            records.append({
                "day_idx": d_idx,
                "기준일자": row["기준일자"],
                "시간": h,
                "시간대": h_kor,
                "노선_시간이용량": r_vol,
                "신성여고_승차": b_val,
                "신성여고_하차": a_val,
                "승차편중비율": round(bias, 4),
                "신성여고_재차": occ_57,
                "영평초체육관_재차": occ_56,
                "하교누적승차": cum_aft_board,
                "실제_혼잡위험지수(%)": risk_pct,
            })
    return pd.DataFrame(records)


# ==============================================================================
# [3단계] 모델: PyTorch LSTM vs 선형 회귀
# ==============================================================================
class BusCongestionLSTM(nn.Module):
    def __init__(self, input_size: int, hidden_size: int = 32, num_layers: int = 1):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True)
        self.fc = nn.Sequential(
            nn.Linear(hidden_size, 16),
            nn.ReLU(),
            nn.Linear(16, 1),
            nn.Sigmoid(),
        )

    def forward(self, x):
        out, _ = self.lstm(x)
        return self.fc(out[:, -1, :]).squeeze(-1)


def make_window(feats: np.ndarray, t: int) -> np.ndarray:
    """t시점의 SEQ_LEN 길이 슬라이딩 윈도우 (앞부분은 첫 시점 값으로 패딩)."""
    if t < SEQ_LEN - 1:
        pad_count = (SEQ_LEN - 1) - t
        return np.vstack([np.repeat(feats[0:1], pad_count, axis=0), feats[: t + 1]])
    return feats[t - SEQ_LEN + 1: t + 1]


def train_models(df_seq: pd.DataFrame, n_days: int) -> dict:
    scaler = MinMaxScaler()
    scaled = scaler.fit_transform(df_seq[FEATURE_COLS])
    df_scaled = pd.DataFrame(scaled, columns=FEATURE_COLS)
    df_scaled["day_idx"] = df_seq["day_idx"].values
    df_scaled["target"] = df_seq["실제_혼잡위험지수(%)"].values / 100.0

    X_seq, X_flat, y_all, day_ids = [], [], [], []
    for d_id, grp in df_scaled.groupby("day_idx"):
        feats = grp[FEATURE_COLS].values
        targs = grp["target"].values
        for t in range(len(feats)):
            X_seq.append(make_window(feats, t))
            X_flat.append(feats[t])
            y_all.append(targs[t])
            day_ids.append(d_id)

    X_seq = np.array(X_seq, dtype=np.float32)
    X_flat = np.array(X_flat, dtype=np.float32)
    y_all = np.array(y_all, dtype=np.float32)
    day_ids = np.array(day_ids)

    # 앞쪽 일자는 학습, 마지막 VAL_DAYS일은 검증
    train_mask = day_ids < (n_days - VAL_DAYS)
    val_mask = ~train_mask

    # 1) 비교군: 선형 회귀 (단일 시점 정적 입력)
    t0 = time.time()
    lr_model = LinearRegression()
    lr_model.fit(X_flat[train_mask], y_all[train_mask] * 100.0)
    lr_sec = round(time.time() - t0, 3)
    lr_pred_val = np.clip(lr_model.predict(X_flat[val_mask]), 0, 100)
    lr_pred_all = np.clip(lr_model.predict(X_flat), 0, 100)

    # 2) 주력 모델: LSTM (연속 3시간 시계열 윈도우)
    torch.manual_seed(SEED)
    np.random.seed(SEED)
    t0 = time.time()
    lstm_model = BusCongestionLSTM(input_size=len(FEATURE_COLS), hidden_size=HIDDEN_SIZE)
    optimizer = torch.optim.Adam(lstm_model.parameters(), lr=LEARNING_RATE)
    criterion = nn.MSELoss()

    X_tr_t = torch.tensor(X_seq[train_mask])
    y_tr_t = torch.tensor(y_all[train_mask])
    X_va_t = torch.tensor(X_seq[val_mask])
    y_va_t = torch.tensor(y_all[val_mask])

    train_losses, val_losses = [], []
    for _ in range(EPOCHS):
        lstm_model.train()
        optimizer.zero_grad()
        loss = criterion(lstm_model(X_tr_t), y_tr_t)
        loss.backward()
        optimizer.step()

        lstm_model.eval()
        with torch.no_grad():
            v_loss = criterion(lstm_model(X_va_t), y_va_t)
        train_losses.append(float(loss.item()))
        val_losses.append(float(v_loss.item()))

    lstm_sec = round(time.time() - t0, 2)
    lstm_model.eval()
    with torch.no_grad():
        lstm_pred_val = lstm_model(X_va_t).numpy() * 100.0
        lstm_pred_all = lstm_model(torch.tensor(X_seq)).numpy() * 100.0

    df_seq = df_seq.copy()
    df_seq["LSTM_예측지수(%)"] = np.round(lstm_pred_all, 2)
    df_seq["선형회귀_예측지수(%)"] = np.round(lr_pred_all, 2)

    def _metrics(y_true, y_pred):
        mae = round(float(mean_absolute_error(y_true, y_pred)), 2)
        rmse = round(float(np.sqrt(mean_squared_error(y_true, y_pred))), 2)
        return mae, rmse

    y_val_pct, y_all_pct = y_all[val_mask] * 100.0, y_all * 100.0
    l_val, l_all = _metrics(y_val_pct, lstm_pred_val), _metrics(y_all_pct, lstm_pred_all)
    r_val, r_all = _metrics(y_val_pct, lr_pred_val), _metrics(y_all_pct, lr_pred_all)
    val_label = f"{n_days - VAL_DAYS + 1}~{n_days}일차"

    metrics_df = pd.DataFrame([
        {
            "모델 구분": "PyTorch LSTM (제안 딥러닝 모델)",
            "입력 구조": f"연속 {SEQ_LEN}시간 시계열 윈도우 (누적 흐름 반영)",
            f"검증 MAE ({val_label})": l_val[0],
            f"검증 RMSE ({val_label})": l_val[1],
            f"전체 MAE (평일 {n_days}일)": l_all[0],
            f"전체 RMSE (평일 {n_days}일)": l_all[1],
            "학습 소요시간": f"{lstm_sec}초",
        },
        {
            "모델 구분": "선형 회귀 (Linear Regression 비교군)",
            "입력 구조": "단일 시간대 정적 피처 (시간 연속성 미반영)",
            f"검증 MAE ({val_label})": r_val[0],
            f"검증 RMSE ({val_label})": r_val[1],
            f"전체 MAE (평일 {n_days}일)": r_all[0],
            f"전체 RMSE (평일 {n_days}일)": r_all[1],
            "학습 소요시간": f"{lr_sec}초",
        },
    ])

    return {
        "df_seq": df_seq,
        "scaler": scaler,
        "lstm_model": lstm_model,
        "train_losses": train_losses,
        "val_losses": val_losses,
        "metrics_df": metrics_df,
    }


# ==============================================================================
# [4단계] 리포트 · 통계 · 상태(state) 구성
# ==============================================================================
def build_state(excel_path: Path) -> dict:
    data = load_and_clean(excel_path)
    df1, df2, df3 = data["df1"], data["df2"], data["df3"]
    stop_56, stop_57 = data["stop_56"], data["stop_57"]
    merged_daily = data["merged_daily"]
    n_days = len(merged_daily)

    df_seq = make_sequence_table(merged_daily, stop_56, stop_57)
    trained = train_models(df_seq, n_days)

    # --- 핵심 통계 (화면의 인사이트 문구에 그대로 사용) ---
    tot_route_vol = int(merged_daily["노선_일별이용량"].sum())
    tot_route_aft = int(merged_daily["노선_하교이용량(16-18시)"].sum())
    tot_board = int(merged_daily["신성여고_하교승차(16-18시)"].sum())
    tot_alight = int(merged_daily["신성여고_하교하차(16-18시)"].sum())
    peak_idx = merged_daily["신성여고_하교승차(16-18시)"].idxmax()
    s56_total = int(stop_56["하교재차합(16-18시)"])
    s57_total = int(stop_57["하교재차합(16-18시)"])

    stats = {
        "n_days": n_days,
        "route_mean_vol": merged_daily["노선_일별이용량"].mean(),
        "route_mean_aft": merged_daily["노선_하교이용량(16-18시)"].mean(),
        "aft_share": tot_route_aft / tot_route_vol * 100,
        "tot_board": tot_board,
        "tot_alight": tot_alight,
        "board_bias": tot_board / (tot_board + tot_alight) * 100,
        "net_board_mean": merged_daily["신성여고_순탑승(16-18시)"].mean(),
        "peak_date": merged_daily.loc[peak_idx, "기준일자"],
        "peak_board": int(merged_daily.loc[peak_idx, "신성여고_하교승차(16-18시)"]),
        "s56_name": stop_56["정류장명"],
        "s57_name": stop_57["정류장명"],
        "s56_total": s56_total,
        "s57_total": s57_total,
        "s57_by_hour": " + ".join(f"{h}시 {int(stop_57[f'{h}시'])}명" for h in AFTER_SCHOOL_HOURS),
    }

    prep_report_df = pd.DataFrame([
        {
            "원본 시트명": "1_노선별_이용량",
            "원본 크기": f"{len(data['df1_raw'])}행 × {len(data['df1_raw'].columns)}열",
            "전처리(자르기) 내용": "상단 '합계' 요약 행 제거, 노선 코드 통일, 하교·등교 파생변수 생성",
            "정제 후 크기": f"{len(df1)}행(평일 {len(df1)}일) × {df1.shape[1]}열",
            "연결 키": "기준일자 (일)",
        },
        {
            "원본 시트명": "2_노선별_차내재차인원",
            "원본 크기": f"{len(data['df2_raw'])}행 × {len(data['df2_raw'].columns)}열",
            "전처리(자르기) 내용": "정류장순번 41번(대기고)~66번(제주시청) 구간 추출 및 56·57번 비교",
            "정제 후 크기": f"{len(df2)}개 정류장 × {df2.shape[1]}열",
            "연결 키": "정류장순번 (56번·57번)",
        },
        {
            "원본 시트명": "3_정류장별_이용량",
            "원본 크기": f"{len(data['df3_raw'])}행 × {len(data['df3_raw'].columns)}열",
            "전처리(자르기) 내용": "주말((토),(일)) 행 제거, 16~22시 승·하차 열만 사용",
            "정제 후 크기": f"{len(df3)}행(평일 {len(df3)}일) × {df3.shape[1]}열",
            "연결 키": "기준일자 (일자)",
        },
    ])

    derived_kpi_df = pd.DataFrame([
        {
            "파생 변수명": "노선 하교 집중률 (%)",
            "산출 수식": "하교(16~18시) 노선 이용량 ÷ 일별 노선 전체 이용량 × 100",
            "산출 결과": f"{stats['aft_share']:.2f}% (일평균 {stats['route_mean_aft']:.1f}명 / {stats['route_mean_vol']:.1f}명)",
            "의미": f"411-1 노선 하루 이용객의 약 {stats['aft_share']:.0f}%가 하교 3시간에 집중됨",
        },
        {
            "파생 변수명": "신성여고 하교 승차 편중률 (%)",
            "산출 수식": "신성여고 하교 승차 ÷ (신성여고 하교 승차 + 하차) × 100",
            "산출 결과": f"{stats['board_bias']:.2f}% (총 승차 {tot_board}명 vs 하차 {tot_alight}명)",
            "의미": "내리는 승객이 거의 없어 차내 빈자리가 생기지 않는 일방향 병목 유발",
        },
        {
            "파생 변수명": "신성여고 하교 순탑승량 (명)",
            "산출 수식": "신성여고 하교(16~18시) 승차 인원 − 하차 인원",
            "산출 결과": f"{n_days}일 누적 +{tot_board - tot_alight}명 (평일 일평균 +{stats['net_board_mean']:.1f}명)",
            "의미": "신성여고 정류장 통과 직후 차내 혼잡도가 급격히 증가함",
        },
        {
            "파생 변수명": "상류 정류장 재차 여유도 (명)",
            "산출 수식": f"57번 신성여고 하교 재차({s57_total}명) − 56번 영평초체육관 하교 재차({s56_total}명)",
            "산출 결과": f"+{s57_total - s56_total}명 여유 (영평초 체육관 하교 재차 {s56_total}명)",
            "의미": "만차 경보 시 도보 10분 거리 영평초 체육관 정류장에서 여유 탑승 가능",
        },
    ])

    return {
        **data,
        **trained,
        "n_days": n_days,
        "stats": stats,
        "prep_report_df": prep_report_df,
        "derived_kpi_df": derived_kpi_df,
    }


