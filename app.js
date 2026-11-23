/*
 * app.js — 411-1 신성여고 하교 만차 예측 & 우회 안내 (정적 웹앱)
 * 데이터: data.js (build_site.py 가 생성) / 모델 계산: model.js / 차트: Plotly.js(CDN)
 */
(function () {
  "use strict";

  const D = window.BUS_DATA;
  const M = window.BusModel;
  const $ = (id) => document.getElementById(id);

  const FONT = "Noto Sans KR, Apple SD Gothic Neo, Malgun Gothic, NanumGothic, sans-serif";
  const COLOR = {
    navy: "#1E3A5F", red: "#DC2626", green: "#16A34A", amber: "#D97706",
    blue: "#2563EB", slate: "#94A3B8", grey: "#CBD5E1",
  };

  // ---------------------------------------------------------------- 도우미
  const esc = (v) =>
    String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt1 = (n) => Number(n).toLocaleString("ko-KR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const hourLabel = (h) => String(h).padStart(2, "0") + "시";
  const round = (n, d) => Math.round(n * 10 ** d) / 10 ** d;

  function showFatal(msg) {
    const box = $("fatal");
    box.innerHTML = msg;
    box.hidden = false;
    $("app").hidden = true;
  }

  function renderTable(target, tbl) {
    const head = tbl.columns.map((c) => `<th>${esc(c)}</th>`).join("");
    const body = tbl.rows
      .map((r) => `<tr>${r.map((v) => `<td>${esc(v)}</td>`).join("")}</tr>`)
      .join("");
    target.innerHTML = `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  // Plotly 공통 설정 (Plotly 로딩에 실패해도 나머지 화면은 동작)
  function plot(id, traces, layout) {
    if (!window.Plotly) {
      $(id).innerHTML = '<p class="muted">차트 라이브러리(Plotly)를 불러오지 못했습니다. 인터넷 연결을 확인한 뒤 새로고침해 주세요.</p>';
      return;
    }
    const base = {
      font: { family: FONT, size: 12 },
      paper_bgcolor: "white",
      plot_bgcolor: "white",
      margin: { l: 60, r: 60, t: 100, b: 60 },
      legend: { orientation: "h", yanchor: "bottom", y: 1.02, xanchor: "right", x: 1 },
    };
    const lay = Object.assign({}, base, layout);
    if (layout.title) lay.title = { text: layout.title, x: 0, xanchor: "left", y: 0.97, yanchor: "top", font: { size: 15 } };
    window.Plotly.react(id, traces, lay, { responsive: true, displaylogo: false });
  }

  // ---------------------------------------------------------------- 데이터 준비
  const FEAT = () => D.feature_cols;
  // data.js 가 없을 때(D 가 undefined)도 init() 에서 안내 문구를 보여줄 수 있도록 방어합니다.
  const seqByDate = {};
  const st = D ? D.stats : null;
  const meta = D ? D.meta : null;
  if (D) {
    D.seq.forEach((r) => (seqByDate[r["기준일자"]] = seqByDate[r["기준일자"]] || []).push(r));
    Object.values(seqByDate).forEach((rows) => rows.sort((a, b) => a["시간"] - b["시간"]));
  }

  const stopRow = (no) => D.stops.find((s) => s["정류장순번"] === no);

  // ---------------------------------------------------------------- 시뮬레이터
  function simulate(date, hourText, board, alight, cum) {
    const rows = seqByDate[date];
    const hInt = parseInt(hourText, 10);
    const tIdx = rows.findIndex((r) => r["시간"] === hInt);
    const total = board + alight;

    const featureRows = rows.map((r, i) => {
      const row = i === tIdx
        ? Object.assign({}, r, {
            "신성여고_승차": board,
            "신성여고_하차": alight,
            "승차편중비율": total > 0 ? board / total : 0,
            "하교누적승차": cum,
          })
        : r;
      return FEAT().map((c) => row[c]);
    });
    return round(M.predictPct(D, featureRows, tIdx), 1);
  }

  function levelOf(pred) {
    return pred >= meta.danger ? "danger" : pred >= meta.caution ? "caution" : "ok";
  }

  function renderGauge(pred, date, hourText) {
    const color = { danger: COLOR.red, caution: COLOR.amber, ok: COLOR.green }[levelOf(pred)];
    plot("gauge", [{
      type: "indicator",
      mode: "gauge+number",
      value: pred,
      number: { suffix: "%", font: { size: 42, color } },
      title: { text: `<b>LSTM 예측 하교 혼잡 위험지수</b><br><span style="font-size:13px;color:#475569">${esc(date)} ${esc(hourText)} 기준</span>` },
      gauge: {
        axis: { range: [0, 100], tickwidth: 1 },
        bar: { color },
        bgcolor: "white",
        steps: [
          { range: [0, meta.caution], color: "#DCFCE7" },
          { range: [meta.caution, meta.danger], color: "#FEF3C7" },
          { range: [meta.danger, 100], color: "#FEE2E2" },
        ],
        threshold: { line: { color: "#991B1B", width: 4 }, thickness: 0.8, value: meta.danger },
      },
    }], { height: 320, margin: { t: 70, b: 20, l: 30, r: 30 } });
  }

  function renderAlert(pred, hourText, board, cum) {
    const s56 = esc(st.s56_name);
    const lvl = levelOf(pred);
    const html = {
      danger: `<div class="alert alert-danger"><div class="alert-title">🚨 위험 등급 (${pred}%): 만차·무정차 통과 고위험!</div>
        <p><b>57번 ${esc(st.s57_name)}</b> 정류장의 예상 승차(<b>${board}명</b>)와 누적 대기(<b>${cum}명</b>)가 임계치를 초과했습니다.<br>
        👉 <b>권장 행동:</b> 도보 약 10분 거리의 바로 앞 상류 정류장인 <b>[56번 ${s56}]</b>(하교 시간대 차내 재차인원 <b>${st.s56_total}명</b>)으로 이동하여 우회 탑승하세요!</p></div>`,
      caution: `<div class="alert alert-caution"><div class="alert-title">⚠️ 혼잡 주의 등급 (${pred}%): 입석 탑승 및 대기 지연 우려</div>
        <p><b>57번 ${esc(st.s57_name)}</b> 정류장에 하교 승객(<b>${board}명</b>)이 몰리고 있습니다.<br>
        👉 <b>권장 행동:</b> 정류장에 5~10분 조기 대기하거나, 좌석 착석을 원할 경우 <b>[56번 ${s56}]</b> 이용을 고려하세요.</p></div>`,
      ok: `<div class="alert alert-ok"><div class="alert-title">✅ 보통 등급 (${pred}%): 정상 탑승 가능</div>
        <p>현재 조건(${esc(hourText)}, 예상 승차 <b>${board}명</b>)에서는 만차 위험이 낮습니다.<br>
        👉 <b>권장 행동:</b> <b>57번 ${esc(st.s57_name)}</b> 정류장에서 정상적으로 탑승하시면 됩니다.</p></div>`,
    }[lvl];
    $("alert").innerHTML = html;
  }

  function renderStopCompare(pred) {
    const s56 = stopRow(56), s57 = stopRow(57);
    const lvl = levelOf(pred);
    const verdict57 = { danger: "🚨 만차·무정차 위험 (우회 필요)", caution: "⚠️ 혼잡 주의", ok: "✅ 정상 탑승 가능" }[lvl];
    const verdict56 = lvl === "danger"
      ? `⭐ 우회 탑승 권장 (재차 ${st.s56_total}명, 여유 좌석 확보)`
      : `대체 상류 정류장 (재차 ${st.s56_total}명)`;
    renderTable($("stop-table"), {
      columns: ["정류장 순번", "정류장명", "16시 재차(명)", "17시 재차(명)", "18시 재차(명)", "하교(16~18시) 재차합계", "신성여고 기준 거리", "시뮬레이션 판정"],
      rows: [
        [56, s56["정류장명"], s56["16시"], s56["17시"], s56["18시"], st.s56_total, "상류 1정거장 앞 (도보 약 10분)", verdict56],
        [57, s57["정류장명"], s57["16시"], s57["17시"], s57["18시"], st.s57_total, "기준 정류장 (학교 정문 앞)", verdict57],
      ],
    });
  }

  function currentInputs() {
    return {
      date: $("in-date").value,
      hour: $("in-hour").value,
      board: Number($("in-board").value),
      alight: Number($("in-alight").value),
      cum: Number($("in-cum").value),
    };
  }

  function runSimulation() {
    const v = currentInputs();
    const pred = simulate(v.date, v.hour, v.board, v.alight, v.cum);
    renderGauge(pred, v.date, v.hour);
    renderAlert(pred, v.hour, v.board, v.cum);
    renderStopCompare(pred);
  }

  function syncSliders() {
    const date = $("in-date").value;
    const hInt = parseInt($("in-hour").value, 10);
    const row = seqByDate[date].find((r) => r["시간"] === hInt);
    $("in-board").value = row["신성여고_승차"];
    $("in-alight").value = row["신성여고_하차"];
    $("in-cum").value = row["하교누적승차"];
    ["board", "alight", "cum"].forEach((k) => ($("out-" + k).textContent = $("in-" + k).value));
  }

  function initSimulator() {
    const dates = Object.keys(seqByDate);
    $("in-date").innerHTML = dates.map((d) => `<option>${esc(d)}</option>`).join("");
    $("in-hour").innerHTML = meta.target_hours.map((h) => `<option>${hourLabel(h)}</option>`).join("");
    $("in-date").value = meta.default_date;
    $("in-hour").value = meta.default_hour;

    $("in-board").max = meta.slider_max.board;
    $("in-alight").max = meta.slider_max.alight;
    $("in-cum").max = meta.slider_max.cum;
    syncSliders();

    const resync = () => { syncSliders(); runSimulation(); };
    $("in-date").addEventListener("change", resync);
    $("in-hour").addEventListener("change", resync);
    $("btn-reset").addEventListener("click", resync);
    $("btn-sim").addEventListener("click", runSimulation);
    ["board", "alight", "cum"].forEach((k) =>
      $("in-" + k).addEventListener("input", () => {
        $("out-" + k).textContent = $("in-" + k).value;
        runSimulation();
      })
    );
    runSimulation();
  }

  // ---------------------------------------------------------------- 탭 2: 모델 비교
  function renderModelTab() {
    renderTable($("metrics-table"), D.metrics);

    const epochs = D.losses.train.map((_, i) => i + 1);
    plot("loss-chart", [
      { x: epochs, y: D.losses.train, mode: "lines", name: "학습 손실 (Train MSE)", line: { color: COLOR.navy, width: 2.5 } },
      { x: epochs, y: D.losses.val, mode: "lines", name: "검증 손실 (Val MSE)", line: { color: "#E11D48", width: 2.5, dash: "dash" } },
    ], {
      title: "PyTorch LSTM 에포크별 학습 및 검증 손실(MSE Loss) 수렴 추이",
      xaxis: { title: "학습 에포크 (Epoch)" }, yaxis: { title: "손실값 (MSE)" }, height: 380,
    });

    const val = D.seq.filter((r) => r["day_idx"] >= meta.n_days - meta.val_days);
    const x = val.map((r) => r["기준일자"].slice(5) + " " + r["시간대"]);
    const first = val[0]["기준일자"], last = val[val.length - 1]["기준일자"];
    plot("cmp-chart", [
      { x, y: val.map((r) => r["실제_혼잡위험지수(%)"]), mode: "lines+markers", name: "실제 데이터 기준 위험지수(%)", line: { color: "#0F172A", width: 3 }, marker: { size: 8 } },
      { x, y: val.map((r) => r["LSTM_예측지수(%)"]), mode: "lines+markers", name: "PyTorch LSTM 예측(%)", line: { color: COLOR.blue, width: 2.5 }, marker: { size: 7 } },
      { x, y: val.map((r) => r["선형회귀_예측지수(%)"]), mode: "lines+markers", name: "선형 회귀 예측(%)", line: { color: COLOR.amber, width: 2, dash: "dot" }, marker: { size: 7 } },
    ], {
      title: `검증 데이터(${first} ~ ${last}) 실제 위험지수 vs LSTM · 선형회귀 예측 비교`,
      xaxis: { title: "날짜 및 시간대", automargin: true }, yaxis: { title: "하교 혼잡 위험지수 (%)" }, height: 420,
      shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: meta.danger, y1: meta.danger, line: { color: COLOR.red, dash: "dash", width: 1.5 } }],
      annotations: [{ xref: "paper", x: 1, y: meta.danger, xanchor: "right", yanchor: "bottom", showarrow: false, text: `만차 우회 임계치 (${meta.danger}%)`, font: { color: COLOR.red } }],
    });
  }

  // ---------------------------------------------------------------- 탭 3: 시각화 & 인사이트
  function renderInsightTab() {
    const dailyCols = D.daily.columns;
    const col = (name) => D.daily.rows.map((r) => r[dailyCols.indexOf(name)]);
    const dates = col("기준일자");
    const boardDaily = col("신성여고_하교승차(16-18시)");
    const y2max = Math.max(42, Math.floor(Math.max(...boardDaily) * 1.2) + 1);

    plot("chart1", [
      { type: "bar", x: dates, y: col("노선_일별이용량"), name: "411-1 노선 일별 전체 이용량(명)", marker: { color: COLOR.grey } },
      { type: "bar", x: dates, y: col("노선_하교이용량(16-18시)"), name: "411-1 노선 하교(16~18시) 이용량(명)", marker: { color: COLOR.navy } },
      { type: "scatter", x: dates, y: boardDaily, yaxis: "y2", mode: "lines+markers", name: "신성여고 하교 승차(명)", line: { color: COLOR.red, width: 3 }, marker: { size: 8 } },
      { type: "scatter", x: dates, y: col("신성여고_하교하차(16-18시)"), yaxis: "y2", mode: "lines+markers", name: "신성여고 하교 하차(명)", line: { color: COLOR.green, width: 2.5, dash: "dash" }, marker: { size: 7 } },
    ], {
      title: `차트 1. 평일 ${meta.n_days}일간 411-1 노선 이용량 vs 신성여고 하교(16~18시) 승·하차 추이`,
      barmode: "group", height: 440,
      xaxis: { automargin: true },
      yaxis: { title: "노선 전체/하교 이용량 (명)" },
      yaxis2: { title: "신성여고 정류장 승·하차 (명)", overlaying: "y", side: "right", range: [0, y2max] },
    });
    $("insight1").innerHTML =
      `💡 <b>핵심 인사이트 1 (시간적 집중):</b> <code>411-1</code>번 노선의 평일 일평균 이용객(<b>${fmt1(st.route_mean_vol)}명</b>) 중 ` +
      `<b>${st.aft_share.toFixed(2)}%(일평균 ${fmt1(st.route_mean_aft)}명)</b>가 하교 시간(<code>16~18시</code>)에 집중됩니다. ` +
      `특히 <code>${esc(st.peak_date)}</code>에는 신성여고 하교 승차만 <b>${st.peak_board}명</b>에 달해 만차 무정차 위험이 최고조에 이릅니다.`;

    // 차트 2: 정류장 순번별 재차인원
    const labels = D.stops.map((s) => `${s["정류장순번"]}.${s["정류장명"]}`);
    const s56 = stopRow(56), s57 = stopRow(57);
    plot("chart2", [
      { type: "scatter", x: labels, y: D.stops.map((s) => s["16시"]), mode: "lines+markers", name: "16시 차내 재차(명)", line: { color: "#3B82F6", width: 2 } },
      { type: "scatter", x: labels, y: D.stops.map((s) => s["17시"]), mode: "lines+markers", name: "17시 차내 재차(명)", line: { color: "#EF4444", width: 2.5 } },
      { type: "bar", x: labels, y: D.stops.map((s) => s["하교재차합(16-18시)"]), name: "하교(16~18시) 누적 재차(명)", marker: { color: "rgba(30,58,95,0.25)" } },
      { type: "scatter", x: [`56.${s56["정류장명"]}`], y: [s56["하교재차합(16-18시)"]], mode: "markers+text", name: `우회 권장 (56번 ${s56["정류장명"]}: ${st.s56_total}명)`, text: [`우회 권장(${st.s56_total}명)`], textposition: "top center", marker: { color: COLOR.green, size: 14, symbol: "star" } },
      { type: "scatter", x: [`57.${s57["정류장명"]}`], y: [s57["하교재차합(16-18시)"]], mode: "markers+text", name: `병목 정류장 (57번 ${s57["정류장명"]}: ${st.s57_total}명)`, text: [`병목 타깃(${st.s57_total}명)`], textposition: "top center", marker: { color: COLOR.red, size: 14, symbol: "diamond" } },
    ], {
      title: "차트 2. 정류장 순번(41번 대기고 ~ 66번 제주시청)별 차내 재차인원 변화 및 병목·우회 지점",
      xaxis: { title: "정류장 순번 및 정류장명", tickangle: -35, automargin: true },
      yaxis: { title: "차내 재차인원 (명)" }, height: 520, font: { family: FONT, size: 11 },
    });
    $("insight2").innerHTML =
      `💡 <b>핵심 인사이트 2 (상류 정류장 우회 근거):</b> 노선 순번상 <b>57번 ${esc(st.s57_name)}</b>는 하교 시간(<code>16~18시</code>) ` +
      `차내 재차인원이 <b>${st.s57_total}명(<code>${esc(st.s57_by_hour)}</code>)</b>으로 누적되는 반면, 도보 10분 거리 직전 정류장인 ` +
      `<b>56번 ${esc(st.s56_name)}</b>은 같은 시간대 차내 재차인원이 <b>${st.s56_total}명</b>으로 탑승 여유가 확인되었습니다.`;

    // 차트 3: 시간대별 평균 승·하차
    const hours = meta.target_hours;
    const avg = (key, h) => {
      const rows = D.seq.filter((r) => r["시간"] === h);
      return rows.reduce((s, r) => s + r[key], 0) / rows.length;
    };
    const hx = hours.map(hourLabel);
    const avgB = hours.map((h) => avg("신성여고_승차", h));
    const avgA = hours.map((h) => avg("신성여고_하차", h));
    const bias = avgB.map((b, i) => (b + avgA[i] > 0 ? round((b / (b + avgA[i])) * 100, 1) : 0));
    plot("chart3", [
      { type: "bar", x: hx, y: avgB.map((v) => round(v, 2)), name: "신성여고 평균 승차(명)", marker: { color: COLOR.blue } },
      { type: "bar", x: hx, y: avgA.map((v) => round(v, 2)), name: "신성여고 평균 하차(명)", marker: { color: COLOR.slate } },
      { type: "scatter", x: hx, y: bias, yaxis: "y2", name: "승차 편중률(%)", mode: "lines+markers+text", text: bias.map((v) => v + "%"), textposition: "top center", line: { color: COLOR.red, width: 3 } },
    ], {
      title: "차트 3. 신성여고 정류장 시간대별(16~22시) 평균 승·하차 및 승차 편중률(%)",
      barmode: "stack", height: 420,
      yaxis: { title: "평균 승·하차 인원 (명)" },
      yaxis2: { title: "승차 편중률 (%)", overlaying: "y", side: "right", range: [0, 115] },
    });
    $("insight3").innerHTML =
      `💡 <b>핵심 인사이트 3 (일방향 승차 편중 병목):</b> 하교 시간(<code>16~18시</code>) 신성여고 정류장에서는 총 <b>${st.tot_board}명</b>이 탑승한 반면 ` +
      `하차는 <b>${st.tot_alight}명</b>에 불과해 <b>승차 편중률이 ${st.board_bias.toFixed(2)}%</b>에 달합니다. ` +
      `내리는 사람이 없어 빈자리가 생기지 않으므로 상류 정류장 분산 탑승이 가장 효과적인 해법입니다.`;
  }

  // ---------------------------------------------------------------- 탭 4: 전처리 검증 & 다운로드
  function csvCell(v) {
    const s = v == null ? "" : String(v);
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }

  function downloadCsv() {
    const lines = [D.daily.columns.map(csvCell).join(",")].concat(D.daily.rows.map((r) => r.map(csvCell).join(",")));
    const blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "411-1_최종통합_전처리데이터.csv";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function renderDataTab() {
    renderTable($("prep-table"), D.prep_report);
    renderTable($("kpi-table"), D.derived_kpi);
    renderTable($("daily-table"), D.daily);
    $("daily-title").textContent = `📋 3개 시트 최종 병합 데이터프레임 (평일 ${meta.n_days}일 기준)`;
    $("btn-csv").addEventListener("click", downloadCsv);
  }

  // ---------------------------------------------------------------- 탭 전환 (차트는 처음 열 때 그림)
  const renderers = { model: renderModelTab, insight: renderInsightTab, data: renderDataTab };
  const rendered = {};

  function showTab(name) {
    document.querySelectorAll(".tab").forEach((b) => {
      const on = b.dataset.tab === name;
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", String(on));
    });
    document.querySelectorAll(".panel").forEach((p) => (p.hidden = p.id !== "tab-" + name));
    if (renderers[name] && !rendered[name]) {
      renderers[name]();
      rendered[name] = true;
    } else if (window.Plotly) {
      document.querySelectorAll("#tab-" + name + " .js-plotly-plot").forEach((el) => window.Plotly.Plots.resize(el));
    }
  }

  // ---------------------------------------------------------------- 모델 일치 검증 (학습 시 예측 ↔ 브라우저 계산)
  function verifyModel() {
    let worst = 0;
    Object.values(seqByDate).forEach((rows) => {
      const featureRows = rows.map((r) => FEAT().map((c) => r[c]));
      rows.forEach((r, t) => {
        const pred = round(M.predictPct(D, featureRows, t), 2);
        worst = Math.max(worst, Math.abs(pred - r["LSTM_예측지수(%)"]));
      });
    });
    return worst;
  }

  // ---------------------------------------------------------------- 시작
  function init() {
    if (!D) {
      showFatal(
        "<h2>데이터 파일(data.js)을 찾을 수 없습니다</h2>" +
        "<p>이 웹앱은 <code>python build_site.py</code> 로 만든 <code>docs/data.js</code>를 읽어서 동작합니다.<br>" +
        "저장소에서 한 번 빌드한 뒤 <code>docs/data.js</code>를 함께 커밋해 주세요.</p>"
      );
      return;
    }

    $("lead").innerHTML =
      `<b>분석 데이터:</b> <code>${esc(meta.source)}</code> (단일 통합 엑셀 데이터 기반)<br>` +
      `<b>핵심 솔루션:</b> 딥러닝(<b>PyTorch LSTM</b>)으로 하교 시간대(<code>16~18시</code>) 만차·무정차 위험을 예측하고, ` +
      `위험지수 <b>${meta.danger}% 이상</b> 시 도보 10분 거리의 <b>56번 ${esc(st.s56_name)}(하교 재차 ${st.s56_total}명)</b> 우회 탑승을 안내합니다.`;
    $("model-intro").innerHTML =
      "하교 시간대 버스 만차 현상은 단일 시점의 승차 인원뿐 아니라 <b>직전 시간대(<code>16시 → 17시 → 18시</code>)부터 누적된 대기 승객과 차내 재차 흐름</b>에 의해 발생합니다. " +
      `따라서 연속된 ${meta.seq_len}시간 시계열 윈도우를 기억하는 <b>순환신경망(LSTM)</b>과 시간 연속성을 반영하지 못하는 <b>선형 회귀</b>를 동일한 평일 데이터로 학습·비교했습니다.`;

    document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
    initSimulator();

    const worst = verifyModel();
    const built = new Date(meta.generated_at).toLocaleString("ko-KR");
    $("footer-note").textContent =
      `데이터 생성: ${built} · 브라우저 LSTM 계산 검증: 학습 시 예측값과의 최대 차이 ${worst.toFixed(2)}%p` +
      (worst > 0.1 ? " (⚠️ 차이가 큽니다. build_site.py를 다시 실행해 주세요)" : "");
    if (worst > 0.1) console.warn("LSTM 브라우저 계산 불일치:", worst);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
