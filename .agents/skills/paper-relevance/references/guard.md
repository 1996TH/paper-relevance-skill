# 실행 전 보호 훅

이 설정은 macOS/Linux의 Claude Code 프로젝트용이다. 논문 분석 요청에 파일 삭제·덮어쓰기·임의 프로그램 실행을 포함시키지 않도록, 지원 도구 호출을 실행 전에 거절한다. 계획 확인은 SKILL.md의 대화 절차이며 이 훅이 사용자 확인 응답의 진위를 판정하지는 않는다.

## 연결

프로젝트의 `.agents/skills/paper-relevance/`에 스킬 전체를 둔다. `assets/claude-settings.json`의 `hooks.PreToolUse` 항목을 프로젝트 루트 `.claude/settings.json`에 연결한다. 기존 설정이 있으면 내용을 보존하여 병합하고 전역 설정을 덮어쓰지 않는다. 이 과제 저장소에는 같은 설정을 `.claude/settings.json`으로 포함했다. 개인 스킬을 설치한 것만으로 Claude Code 훅이 활성화되지는 않는다.

설정의 명령은 해당 스킬 안의 `scripts/guard_tool.py`의 전체 경로를 사용한다. 프로젝트에서 새 Claude Code 세션을 열어 `/hooks`에서 이 명령이 로드되었는지 확인한다. 프로젝트 신뢰나 설정 적용이 보류되면 훅 활성화를 주장하지 않는다. `onFailure: "block"`을 지원하는 환경에서 훅 실행 오류·시간 초과도 차단하도록 설정했다. 설정이 인식되지 않는 구버전에서는 보호 모드로 사용하지 않는다.

## 차단 범위

| 도구 | 처리 |
|---|---|
| `Write` | 프로젝트 `runs/` 안의 존재하지 않는 파일만 허용한다. 기존 파일, 다른 경로, 심볼릭 링크를 통한 접근은 거절한다. |
| `Edit`, `MultiEdit`, `NotebookEdit` | 기존 파일 변경을 거절한다. 수정 결과는 새 이름으로 작성한다. |
| `Bash` | 아래 세 분석 스크립트의 제한된 호출만 허용한다. 삭제, 셸 연결·치환·리다이렉션, 다른 프로그램·스크립트 실행을 거절한다. |

명령에 직접 지정하는 입력 파일은 프로젝트 안의 일반 파일이어야 한다. 원문 묶음 내부의 원본 참조는 기존 검사기가 읽어 대조하며, 이 훅은 읽기 기밀성을 제한하는 장치가 아니다. `inputs/`에 원문을 두고 `references/research.md`를 보존한다. 스킬 코드와 설정도 `runs/` 밖이므로 이 도구 호출들로 수정할 수 없다. 사용자나 외부 프로세스가 동시에 경로·프로그램을 바꾸는 공격을 막는 OS 격리는 아니다. 목록에 없는 MCP 도구와 훅 밖의 직접 명령은 이 설정의 보호 대상이 아니다.

훅의 정상 종료 `0`은 해당 훅이 거절하지 않았다는 뜻이다. Claude Code의 기존 권한 확인을 자동 승인하지 않는다. 거절은 종료 `2`와 표준 오류의 이유로 전달한다. 기계적으로 추정 가능한 파일·명령 경계만 검사한다. 근거 없는 값·인용·과장은 `check_result.py`와 원문 의미 대조로 확인한다.

## 허용되는 명령

계획 확인 뒤 입력을 확보한다. 아래 `/project`를 실제 프로젝트의 절대 경로로 **문자열 치환**하여 각 명령을 따로 실행한다. `$paper_skill_dir`, `&&`, 리다이렉션 등을 명령에 넣지 않는다. 경로에 공백이 있으면 따옴표로 감싼다. 따옴표 안이라도 괄호·물음표·앰퍼샌드 같은 셸 특수문자는 보수적으로 거절한다. 그런 파일명은 원본을 보존한 채 단순한 이름의 사본을 입력으로 준비하고, URL의 쿼리 문자열은 명령 대신 별도 출처 기록에 남긴다. 출력은 실행마다 새 폴더·파일을 사용한다.

```sh
python3 -E -s -B /project/.agents/skills/paper-relevance/scripts/prepare_source.py /project/inputs/paper.pdf --output /project/runs/example/source.json --scope full_text
python3 -E -s -B /project/.agents/skills/paper-relevance/scripts/check_result.py --result /project/runs/example/review.json --source /project/runs/example/source.json
python3 -E -s -B /project/.agents/skills/paper-relevance/scripts/render_report.py --result /project/runs/example/review.json --source /project/runs/example/source.json --output /project/runs/example/report.md
```

`review.json`은 분석 후 `Write`로 새로 만든다. `source_packet_sha256`에는 원문 준비 명령이 출력한 `SOURCE_PACKET_SHA256` 값을 넣으므로 별도 셸 해시 계산은 필요 없다. `-E -s -B`는 Python 환경 변수·사용자 site 패키지·바이트코드 출력을 제한하면서 신뢰된 스크립트의 형제 모듈 import를 유지한다. 훅 자체는 표준 라이브러리만 사용하므로 `-I -B`로 실행한다. PDF의 `pypdf`는 실행 Python에서 이미 사용할 수 있어야 한다. 시각 확인은 제공된 PDF 읽기 도구를 사용하고, 도구가 없으면 별도 렌더링이 완료되기 전까지 그 페이지를 확인했다고 기록하지 않는다.

## 시험과 적용 확인

`tests/test_guard_tool.py`는 실제 설정의 연결 명령을 읽어 임시 프로젝트에서 훅을 실행한다. 훅이 허용한 뒤에만 시험 호스트가 파일 작성·분석 명령을 수행하여 차단된 작업의 원자료가 유지되는지 검사한다. 이는 훅 프로토콜 통합 시험이며 Claude Code 세션 자체의 종단 시험은 아니다.

개발자나 사용자가 별도 터미널에서 실행한다. 보호 중인 `Bash`의 허용 명령에는 시험 실행이 포함되지 않는다.

```sh
python3 -B -m unittest discover -s .agents/skills/paper-relevance/tests -v
```

실제 적용 환경에서는 `/hooks`의 로드 확인 뒤, 버려도 되는 별도 시험 폴더로 새 결과 쓰기 허용과 원문 덮어쓰기·삭제 거절을 확인한다. 원문 보존 해시와 거절 메시지를 기록한다. 승인되지 않은 실제 원자료를 파괴 시험에 사용하지 않는다.

## 공식 규약 근거

- [Claude Code hooks](https://code.claude.com/docs/en/hooks): `PreToolUse`, 종료 코드 2, `CLAUDE_PROJECT_DIR`, `onFailure`.
- [프로젝트 설정 범위](https://code.claude.com/docs/en/settings): 프로젝트 `.claude/settings.json`과 사용자 전역 설정의 구분.

2026-11-02에 공식 문서를 확인했다. 실제 설치 버전의 설정 지원 여부와 적용 상태는 사용 환경에서 확인한다.
