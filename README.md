# 🚌 제주 411-1번 버스 · 신성여고 하교 만차 예측 & 우회 안내 (GitHub Pages 웹앱)

신성여고 정류장의 하교(16~18시) 만차·무정차 위험을 **PyTorch LSTM**으로 예측하고,
위험지수가 85% 이상이면 상류 정류장(**56번 영평초등학교차오름체육관**) 우회 탑승을 안내하는 웹앱입니다.

GitHub Pages는 Python을 실행하지 못하는 **정적 호스팅**입니다. 그래서 이렇게 나눴습니다.

| 단계 | 어디서 | 하는 일 |
|---|---|---|
| ① 빌드 (1회) | 내 컴퓨터 / 코랩 | 엑셀 전처리 → LSTM·선형회귀 학습 → 결과와 **LSTM 가중치**를 `docs/data.js`로 저장 |
| ② 서비스 | 방문자의 브라우저 | `docs/`의 HTML·JS가 가중치로 LSTM을 **직접 계산**해 시뮬레이터·차트·표를 표시 |

서버가 필요 없고, 슬라이더를 움직이면 브라우저에서 바로 예측값이 바뀝니다.

## 폴더 구조

```
.
├─ pipeline.py        # 전처리 · 통합 · 모델 학습 (계산 전용)
├─ build_site.py      # 학습 결과를 docs/data.js 로 내보내는 스크립트
├─ requirements.txt   # 빌드용 패키지
├─ data/              # 원천 엑셀을 여기에 넣으세요 (git 제외 설정)
└─ docs/              # ← GitHub Pages 가 배포하는 폴더
    ├─ index.html
    ├─ style.css
    ├─ model.js       # 브라우저용 LSTM 추론
    ├─ app.js         # 탭 · 시뮬레이터 · 차트 · 표 · CSV 다운로드
    └─ data.js        # build_site.py 가 생성 (이 파일이 있어야 화면이 뜹니다)
```

## 1. 데이터 빌드 (처음 한 번, 데이터가 바뀔 때마다)

```bash
pip install -r requirements.txt
# data/ 폴더에 411-1_세개데이터_순서대로_전체나열.xlsx 를 넣은 뒤
python build_site.py
```

성공하면 `docs/data.js`가 만들어지고, 아래처럼 **PyTorch ↔ 내보낸 가중치 일치 검증**도 함께 출력됩니다.

```
[검증] PyTorch ↔ 내보낸 가중치 최대 오차: 0.00xx%p
✅ 저장 완료: docs/data.js
```

> 구글 코랩에서도 같은 방식으로 실행한 뒤 `docs/data.js`를 내려받아 저장소에 넣으면 됩니다.

## 2. 내 컴퓨터에서 미리보기

`docs/index.html`을 더블클릭하거나, 로컬 서버로 확인합니다.

```bash
python -m http.server 8000 -d docs     # → http://localhost:8000
```

> 차트(Plotly.js)는 CDN에서 불러오므로 인터넷 연결이 필요합니다.

## 3. GitHub Pages 배포

```bash
git init && git add . && git commit -m "411-1 웹앱"
git branch -M main
git remote add origin https://github.com/<USERNAME>/<REPO>.git
git push -u origin main
```

GitHub 저장소에서 **Settings → Pages → Build and deployment**

- Source: **Deploy from a branch**
- Branch: **main**, 폴더: **/docs** → Save

잠시 후 `https://<USERNAME>.github.io/<REPO>/` 에서 열립니다.
`docs/data.js`가 커밋되어 있는지 꼭 확인하세요(없으면 안내 문구만 표시됩니다).

## 화면 구성

1. **하교 만차 예측 & 우회 시뮬레이터** — 날짜·시간대·승하차·누적 대기 인원을 바꾸면 LSTM 위험지수 게이지와 우회 안내가 즉시 갱신
2. **AI 모델 성능 비교** — LSTM vs 선형 회귀의 MAE·RMSE, 학습 손실 곡선, 검증 구간 예측 비교
3. **통합 데이터 시각화 & 인사이트** — 3개 시트를 합친 차트 3종과 핵심 인사이트(수치는 데이터에서 자동 계산)
4. **전처리 검증 & 다운로드** — 시트별 전처리 리포트, 파생 변수 수식, 병합 데이터와 CSV 다운로드

## 모델 개요

| 구분 | 입력 | 설명 |
|---|---|---|
| PyTorch LSTM | 연속 3시간 시계열 윈도우 | 직전 시간대부터 누적된 승차·재차 흐름 반영 (은닉 32, 250 에포크) |
| 선형 회귀 | 단일 시간대 피처 | 시간 연속성을 반영하지 않는 비교군 |

- 학습/검증: 앞쪽 평일은 학습, **마지막 2일은 검증**
- 위험 등급: `85% 이상` 고위험 · `65% 이상` 주의 (`pipeline.py` 상단 상수)
- 화면 하단에 *“브라우저 LSTM 계산 검증”* 이 표시됩니다. 학습 시 예측값과 브라우저 계산값의 차이(보통 0.00%p)입니다.

## 한계

- 혼잡 위험지수(정답 라벨)는 **실제 관측된 혼잡도가 아니라**, 승차 인원·누적 승차·차내 재차 등으로 계산한 규칙 기반 점수입니다. 모델은 이 산식을 근사하도록 학습되며, 실제 만차 여부를 검증한 결과는 아닙니다.
- 평일 10일(70개 관측치)의 작은 데이터이므로 성능 수치는 참고용입니다.
