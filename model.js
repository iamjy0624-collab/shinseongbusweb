/*
 * model.js — 브라우저에서 PyTorch LSTM 예측을 재현하는 순수 JavaScript 모듈
 *
 * build_site.py 가 내보낸 가중치(window.BUS_DATA.lstm)로 아래 구조를 그대로 계산합니다.
 *   LSTM(입력 7, 은닉 H, 1층) → 마지막 시점 은닉상태 → Linear(H,16) → ReLU → Linear(16,1) → Sigmoid
 * PyTorch 게이트 순서: i(입력), f(망각), g(셀 후보), o(출력)
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.BusModel = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const sigmoid = (x) => 1 / (1 + Math.exp(-x));

  function dot(row, vec) {
    let s = 0;
    for (let k = 0; k < vec.length; k++) s += row[k] * vec[k];
    return s;
  }

  /** MinMaxScaler(sklearn): x' = x * scale + min */
  function scaleRow(scaler, values) {
    return values.map((v, k) => v * scaler.scale[k] + scaler.min[k]);
  }

  /** t 시점의 seqLen 길이 윈도우 (앞부분은 첫 시점 값으로 패딩) — pipeline.make_window 와 동일 */
  function makeWindow(feats, t, seqLen) {
    if (t < seqLen - 1) {
      const pad = [];
      for (let i = 0; i < seqLen - 1 - t; i++) pad.push(feats[0]);
      return pad.concat(feats.slice(0, t + 1));
    }
    return feats.slice(t - seqLen + 1, t + 1);
  }

  /** 스케일된 윈도우 → 위험확률(0~1) */
  function lstmForward(m, windowSeq) {
    const H = m.hidden;
    let h = new Array(H).fill(0);
    let c = new Array(H).fill(0);

    for (const x of windowSeq) {
      const nh = new Array(H);
      const nc = new Array(H);
      const gates = new Array(4 * H);
      for (let r = 0; r < 4 * H; r++) {
        gates[r] = m.b_ih[r] + m.b_hh[r] + dot(m.w_ih[r], x) + dot(m.w_hh[r], h);
      }
      for (let j = 0; j < H; j++) {
        const i = sigmoid(gates[j]);
        const f = sigmoid(gates[H + j]);
        const g = Math.tanh(gates[2 * H + j]);
        const o = sigmoid(gates[3 * H + j]);
        nc[j] = f * c[j] + i * g;
        nh[j] = o * Math.tanh(nc[j]);
      }
      h = nh;
      c = nc;
    }

    const a = m.fc1_w.map((row, r) => Math.max(0, dot(row, h) + m.fc1_b[r]));
    return sigmoid(dot(m.fc2_w[0], a) + m.fc2_b[0]);
  }

  /** 하루치 행(featureRows: 시간순, 각 행은 FEATURE_COLS 순서의 값)에서 t 시점 예측(%) */
  function predictPct(data, featureRows, t) {
    const feats = featureRows.map((row) => scaleRow(data.scaler, row));
    const win = makeWindow(feats, t, data.meta.seq_len);
    const pct = lstmForward(data.lstm, win) * 100;
    return Math.min(100, Math.max(0, pct));
  }

  return { sigmoid, scaleRow, makeWindow, lstmForward, predictPct };
});
