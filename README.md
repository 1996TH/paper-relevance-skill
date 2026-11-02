# 금융 시계열 AI 연구

## 연구 방향
금융 시계열을 다루는 AI 방법을 연구한다. 지금은 아래 분야를 함께 본다.

## 연구 분야
- 금융공학: 트레이딩 전략과 매매 시그널 설계·검증
- 표현학습: 시계열 표현학습을 금융 데이터에 적용한다.
- 시계열 모델: 비선형 금융 시계열 모델링

## 지금 막힌 문제
- 하류 과제의 라벨을 어떻게 정의할지 정하지 못했다. 예측 기간과 임계값으로 방향을 나눌지, 매매 시그널을 라벨로 쓸지에 따라 결과가 달라진다.

## 도구
Python, PyTorch

## paper-relevance 확장판 (2026-11-02)

논문 PDF·링크와 현재 연구 질문을 받아 근거, 적용 조건, 다음 행동을 정리한다. 위 연구 소개와 기존 `references/research.md`는 보존했으며, 실행할 때 사용자가 제공한 현재 조건을 우선한다.

- 스킬: [.agents/skills/paper-relevance/SKILL.md](.agents/skills/paper-relevance/SKILL.md)
- 실행 형식: [references/workflow.md](.agents/skills/paper-relevance/references/workflow.md)
- 이전 버전: [2026-10-19의 97175f5](https://github.com/1996TH/paper-relevance-skill/tree/97175f55a60cf03803c0217172143c1451630a13)

에이전트에 논문을 첨부하고 “paper-relevance로 이 논문을 읽고 내 연구와 연결해 줘. 현재 연구 질문은 …야”라고 요청한다. 에이전트가 먼저 범위·원자료 보존·출력·검증 계획을 제시한다. 사용자가 확인하면 원문 준비, 내부 JSON 작성, 검사와 보고서 생성을 맡는다. 이미 확인한 동일 계획은 다시 묻지 않는다.

### 포함된 실행 도구

| 파일 | 역할 |
| --- | --- |
| `scripts/prepare_source.py` | 로컬 PDF/UTF-8 문서를 페이지별 근거 자료로 준비 |
| `scripts/check_result.py` | 원본 재추출·해시·근거 위치·결과 상태 검사 |
| `scripts/render_report.py` | 검사를 통과한 JSON에서 한국어 보고서 생성 |
| `scripts/guard_tool.py` | Claude Code의 지정된 도구 호출에서 원자료 변경·삭제·임의 셸 실행 차단 |

Python 3가 필요하며 PDF 추출에는 `pypdf`가 필요하다. 텍스트 입력은 표준 라이브러리만 사용한다. 자동 다운로드·OCR·의존성 설치는 하지 않는다.

프로젝트의 `.claude/settings.json`에 보호 훅을 연결했다. [설정·지원 도구·제한된 명령 형식](.agents/skills/paper-relevance/references/guard.md)을 따른다. 원문은 프로젝트 안에 보관하고 결과는 `runs/` 안의 새 파일로 만든다. 아래 `/project`는 실제 절대 경로로 치환하며 각 명령을 따로 실행한다.

```sh
python3 -E -s -B /project/.agents/skills/paper-relevance/scripts/prepare_source.py /project/inputs/paper.pdf --output /project/runs/example/source.json --scope full_text
python3 -E -s -B /project/.agents/skills/paper-relevance/scripts/check_result.py --result /project/runs/example/review.json --source /project/runs/example/source.json
python3 -E -s -B /project/.agents/skills/paper-relevance/scripts/render_report.py --result /project/runs/example/review.json --source /project/runs/example/source.json --output /project/runs/example/report.md
```

종료 코드 `0`은 완전 상태의 형식·근거 검사 통과, `3`은 유효한 부분 분석/접근 불가 기록이다. `1`은 결과 결함, `2`는 자료·실행 조건 오류다. 원본과 기존 출력은 덮어쓰지 않는다. 자동 검사는 논문의 진실성이나 연구 성과를 보장하지 않으므로 의미 검토를 별도로 수행한다.

### 검증

시험은 사용자의 별도 개발 터미널에서 실행한다. 보호 훅의 Bash 허용 목록에는 시험 명령을 넣지 않는다.

```sh
python3 -B -m unittest discover -s .agents/skills/paper-relevance/tests -v
```

52개 시험(기존 42개와 훅 10개)을 통과했다. 훅 시험은 설정의 명령을 실행하고 거절된 작업이 파일을 바꾸지 않는지 확인하는 프로토콜 통합 시험이다. 현재 검증 환경에는 Claude Code가 없어 실제 Claude 세션의 설정 로드·종단 실행까지 확인하지는 않았다. 사용 환경에서 `/hooks`로 로드 상태를 확인해야 한다. 이 훅은 지정한 도구 호출에 적용되며 OS 전체나 다른 MCP 도구를 격리하지 않는다.

TS2Vec 동일 입력의 원본/확장본 각 1회 비교와 의도적인 조건 누락·잘못된 인용 검사는 보완 전 `e3e1a7e` 버전에서 수행했다. 이번 계획·훅 보완의 시험은 그 비교와 구분한다. 비교에서 확인한 추가 가치는 근거 추적과 검증 기록이며, 정확성·속도 향상을 입증한 실험으로 해석하지 않는다. 10월 19일 이력은 그대로 남아 있다.
