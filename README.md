# Market Radar Desktop

텔레그램 주식 채널을 실시간 수집 → LLM(one-pass) JSON 추출 → 태그 정규화 → SQLite 저장 → 흐름/분석 GUI를 제공하는 **Windows/Linux 동시 지원** PySide6 단독 데스크톱 앱.

```
Telegram 채널 → Telethon → feed_items → LLM Extractor → feed_signals
                                                       ↓
                              ┌─────────────────────────────┴────┐
                              ↓                                  ↓
                    Tag Normalizer (5 layers)         Cluster/Daily/Flow Analytics
                              ↓                                  ↓
                    canonical_tags / signal_tags              GUI (9개 탭)
```

## 주요 기능

- **실시간 텔레그램 채널 수집**: Telethon, 다중 채널, 켜기/끄기, 원문 + 메시지 링크 저장
- **히스토리 백필**: 앱 시작 시 또는 새 채널 추가 시 최근 7일 메시지 자동 fetch
- **LLM one-pass 추출**: OpenAI-compatible HTTP (LM Studio, OpenAI, z.ai, MiniMax, Codex 등)
- **다중 LLM 엔진 + 폴백**: 우선순위 1/2/3순, 자동 failover
- **Codex OAuth 자동 토큰 갱신**: `~/.codex/auth.json` 자동 읽기 + refresh
- **태그 정규화 5-레이어**: exact → alias → normalized → fuzzy → new
- **관심분야 가중치**: 산업/종목별 가중치로 `interest_score` 재계산
- **알림**: `importance ≥ 80` AND `interest ≥ 70` + 동일 주제 cooldown + 시스템 트레이
- **7개 분석 탭 + 일자별 주제 2-stage LLM**:
  - 실시간 DB 피드 (검색/필터/정렬, FTS5)
  - 흐름 대시보드 (heatmap + 변화 해석)
  - 일자별 주제 (2-stage LLM: 클러스터링 → 종합, MD 저장)
  - 태그/주제 분석 (on-demand LLM)
  - 주제 클러스터 (Jaccard 매칭)
  - LLM 프롬프트 관리
  - 일간 리포트 (자동 생성 + 텔레그램 봇 전송)
  - 주가 교차검증 (feed_ticker_links × market_bars)
  - 설정 (관심분야, 태그사전, 알림, 히스토리, export, 리포트, LLM 엔진)
- **다크/라이트 토글** (toolbar 우측)
- **Export**: CSV / Markdown / HTML
- **데이터/세션 보호**: `.env`, `data/*.session`, `data/*.sqlite` 모두 `.gitignore` 처리

## 9개 탭

| 탭 | 기능 | 단축키 |
|---|---|---|
| 실시간 DB 피드 | 최신 신호 테이블, FTS5 검색, 필터, 정렬, 원문 팝업 | F5 새로고침, Enter=원문, Ctrl+F=검색 |
| 흐름 대시보드 | 태그별 일자 heatmap, 변화 해석, 내러티브 감지 | 재계산 버튼 |
| 일자별 주제 | 2-stage LLM: 클러스터링 → 종합. MD 저장. 메시지 → 원문 팝업 | ⚡ 실행 / 🔄 재실행 |
| 태그/주제 분석 | on-demand LLM 분석 (5줄 요약 + 타임라인 + 변화) | 분석 실행 버튼 |
| 주제 클러스터 | 유사 피드 묶음 (Jaccard) | 재클러스터링 버튼 |
| LLM 프롬프트 | 버전 / 실패율 / 실패 샘플 | (읽기 전용) |
| 일간 리포트 | 매일 자동 LLM 리포트 + 텔레그램 봇 전송 + 누적 보기 | 즉시 생성 / 봇으로 전송 |
| 주가 교차검증 | feed_ticker_links × market_bars 시각화 | yfinance 업데이트 |
| 설정 | 모든 설정 통합 | — |

## 설치

```bash
pip install -r requirements.txt
```

`requirements.txt`:
- PySide6 ≥ 6.6
- telethon ≥ 1.36
- httpx ≥ 0.27
- python-dotenv ≥ 1.0
- jsonschema ≥ 4.21
- yfinance ≥ 0.2

## 설정

### 1. 텔레그램 API 키 발급

1. https://my.telegram.org 접속 → 로그인
2. "API development tools" 클릭
3. 앱 이름 입력 → `api_id`(숫자), `api_hash`(32자 문자열) 받기

### 2. LLM 엔드포인트 (OpenAI 호환)

| 변수 | 예시 |
|---|---|
| `TG_LLM_BASE_URL` | `http://127.0.0.1:18085/v1` |
| `TG_LLM_API_KEY` | `not-needed` (로컬) 또는 `sk-...` |
| `TG_LLM_MODEL` | 모델명 (실제 서버 모델과 달라도 자동 해결) |

> 자세한 내용 및 다중 엔진 등록은 설정 탭의 **"LLM 엔진"** 패널에서. OpenAI Codex는 OAuth 토큰을 `~/.codex/auth.json`에서 자동 읽고 만료 시 refresh.

### 3. 환경변수 등록 (Linux bash)

`~/.bashrc`에 추가:

```bash
export TG_API_ID='12345678'
export TG_API_HASH='abcdef0123456789abcdef0123456789'
export TG_PHONE='+821012345678'
export TG_LLM_BASE_URL='http://127.0.0.1:18085/v1'
export TG_LLM_API_KEY='not-needed'
export TG_LLM_MODEL='gemma-4-12b-agentic-v2'
```

저장 후 `source ~/.bashrc` 또는 새 터미널.

`.env` 파일도 지원 (python-dotenv 자동 로드). `.gitignore`에 `.env` 추가 필수.

## 실행

```bash
python run.py
```

### 첫 실행 흐름

1. `data/channels.json`이 비어있으면 `@kiwoom_us_toktok` (키움증권 미국주식 톡톡) 자동 등록
2. `data/market_radar.session`이 없으면 텔레그램 로그인 다이얼로그
3. Telegram이 SMS/Telegram 앱으로 5자리 코드 발송
4. 코드 입력 → 2FA 켜둔 경우 추가 비밀번호 입력
5. 세션 파일 저장 → 이후 자동 로그인
6. **활성 채널의 최근 7일 메시지 자동 fetch + LLM 추출 시작**
7. 좌측 nav → "일간 주제" 탭에서 **⚡ 2-stage LLM 실행**으로 일간 주제 리포트 생성

## 단축키

| 키 | 동작 |
|---|---|
| F5 | 라이브 피드 새로고침 |
| Enter (테이블 행) | 원문 팝업 |
| Esc | 모달 닫기 |
| Ctrl+F | 검색 포커스 |
| 더블클릭 (태그/주제) | 해당 항목의 타임라인 모달 |

## 다중 LLM 엔진 + 폴백

설정 탭 → "LLM 엔진" 패널:

- **provider 프리셋**: openai_compatible, openai, openai_codex (OAuth 자동), z.ai, MiniMax, anthropic, google, custom
- **추가**: 이름 + base_url + api_key + model + priority(자동 1, 2, 3…)
- **편집**: 모든 필드 (이름/provider/base_url/api_key/model/timeout/extra_headers/priority/enabled)
- **우선순위 ▲/▼**: 1순위(primary) / 2순위 / 3순위…
- **헬스 체크**: 각 엔진의 `/v1/models` 응답으로 동작 확인
- **Codex OAuth**: `use_codex_oauth=True` 설정 시 `~/.codex/auth.json` 자동 사용 (만료 60초 전 자동 갱신)

LLM 호출 흐름:
1. priority 1 엔진 시도
2. 실패 시 priority 2
3. 모두 실패 시 last_error 기록

## 히스토리 백필

기본값: **mode=`since_date`, days=7, limit=500**

- 앱 시작 시 모든 활성 채널에 대해 자동 fetch
- 새 채널 추가 시에도 즉시 fetch
- "ingest_state 초기화" 버튼으로 강제 재수신

## 일자별 주제 리포트 (2-stage LLM)

일자별 주제 탭 → **⚡ 2-stage LLM 실행**:

1. **Stage 1 (Clustering)**: 해당 날짜의 N개 신호 → LLM이 M개 주제로 묶음 (5~20, 슬라이더로 조절)
2. **Stage 2 (Per-topic Summary)**: 각 주제별로 member 신호들의 message_text 전문 → LLM이 종합 정리 (summary 3-5문장, body 200-400자, timeline, watchlist)
3. **DB 저장**: `daily_topic_clusters` + `daily_topic_reports`
4. **MD 파일 저장**:
   ```
   data/reports/YYYY-MM-DD/
   ├── index.md
   ├── 01-HBM엔비디아-공급망.md
   ├── 02-유리기판.md
   └── ...
   ```
5. **카드 클릭으로 펼침**: 요약 + 본문 + 타임라인 + 내일 주시 + **관련 원문 메시지 카드**
6. **메시지 카드의 "원문 보기"** → RawFeedModal 팝업

## 일간 자동 리포트 (텔레그램 봇)

설정 탭 → "일간 리포트" 패널:
- ON/OFF, 시각 (HH:mm), 봇 토큰, chat_id, 관심분야 포함 여부
- ON 시 매일 설정 시각에 어제 신호/태그 흐름을 LLM으로 요약
- 텔레그램 봇으로 전송 + "일간 리포트" 탭에 누적

## 트러블슈팅

### 텔레그램 로그인 안 됨

- `api_id` / `api_hash` 정확성 (my.telegram.org 재확인)
- 전화번호 국제 형식 (`+8210...`)
- 세션 파일 삭제 후 재시도: `rm data/market_radar.session`
- 코드 다이얼로그에서 한국 SMS 미수신 시 Telegram 앱 직접 확인 (Settings → Devices)

### LLM 응답 실패

- `TG_LLM_BASE_URL` 살아있는지: `curl $TG_LLM_BASE_URL/models`
- 설정 탭 → LLM 프롬프트 탭에서 "실패 샘플" 확인
- LLM 엔진 패널에서 "헬스 체크"로 각 엔진 상태 점검

### 채널 추가 안 됨

- public 채널만 username으로 추가 가능
- `@` 없이 입력해도 자동 추가
- 텔레그램 로그인 완료 후 시도

### DB 락

- WAL 모드로 동시 읽기/쓰기 가능
- 다른 프로세스가 같은 DB 열면 `database is locked` 가능 → 앱 종료 후 진행

## 디렉토리

```
telegram_radar/
├── docs/                  # 개발계획서, mockup
├── prompts/               # LLM 프롬프트 (feed_extract, topic_cluster, topic_summary, daily_report)
├── src/
│   ├── app/
│   │   ├── main.py
│   │   ├── ui/
│   │   │   ├── main_window.py
│   │   │   ├── right_pane.py
│   │   │   ├── theme.py
│   │   │   ├── tabs/
│   │   │   │   ├── live_feed.py
│   │   │   │   ├── flow_dashboard.py
│   │   │   │   ├── daily_topics.py
│   │   │   │   ├── analysis.py
│   │   │   │   ├── clusters.py
│   │   │   │   ├── prompt.py
│   │   │   │   ├── reports.py
│   │   │   │   ├── cross_validate.py
│   │   │   │   ├── settings.py
│   │   │   │   └── placeholder_tab.py
│   │   │   └── widgets/
│   │   │       ├── left_nav.py
│   │   │       ├── channel_manager.py
│   │   │       ├── raw_feed_modal.py
│   │   │       ├── tag_timeline.py
│   │   │       └── market_panel.py
│   │   └── workers/
│   │       ├── ingest_worker.py
│   │       ├── llm_worker.py
│   │       └── report_worker.py
│   └── core/
│       ├── config.py
│       ├── db/
│       │   ├── schema.sql
│       │   ├── connection.py
│       │   ├── schema.py
│       │   └── repositories.py
│       ├── telegram/
│       │   ├── session.py
│       │   ├── collector.py
│       │   └── history.py
│       ├── llm/
│       │   ├── prompts.py
│       │   ├── topic_prompts.py
│       │   ├── extractor.py
│       │   ├── validator.py
│       │   ├── analysis.py
│       │   ├── engines.py
│       │   └── codex_auth.py
│       ├── normalize/
│       │   ├── tags.py
│       │   ├── interest.py
│       │   └── interest_score.py
│       ├── analytics/
│       │   ├── flow.py
│       │   ├── cluster.py
│       │   ├── alert.py
│       │   └── narrative.py
│       ├── models/channel.py
│       ├── export.py
│       ├── market.py
│       ├── ticker_link.py
│       ├── report.py
│       └── topic_report.py
├── data/                  # .gitignore 대상
├── .env.example
├── .gitignore
├── config.example.json
├── requirements.txt
└── run.py
```

## Phase 로드맵

- ✅ Phase 0: ingest + LLM + DB + 라이브 피드
- ✅ Phase 1: 흐름 대시보드 / 일자별 주제 / 태그 정규화 / 검색·필터
- ✅ Phase 2: 분석 / 알림 / 태그사전 / 프롬프트 / 재처리 / 클러스터
- ✅ Phase 2.8: 히스토리 백필
- ✅ Phase 3: 종목/태그 타임라인 / 내러티브 / export / 주가 교차검증
- ⏸ Phase 3.4: PyInstaller 인스톨러 (보류)
- ⏸ Phase 3.6: 팀 공유 (보류)

## 라이선스

내부 사용.
