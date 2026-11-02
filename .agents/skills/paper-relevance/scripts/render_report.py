#!/usr/bin/env python3
"""Render a validated review without duplicating its source of truth."""
import argparse
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote, urlsplit
from check_result import Invalid, status_message, validate_review


def md(value):
    value = ' '.join(str(value).split())
    value = value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return re.sub(r'([\\`*_{}\[\]()#+.!|])', r'\\\1', value)


def render(review, packet):
    status = {'complete': '완료', 'partial': '부분 완료', 'blocked': '자료 확보 단계에서 중단'}
    scope = {'full_text': '제공된 전문', 'excerpt': '발췌 범위',
             'abstract_only': '초록만', 'unavailable': '본문 접근 불가'}
    paper = review['paper']
    review_mode = {'self': '자체 검토', 'independent': '독립 검토', 'not_performed': '미수행'}
    verification_scope = ('기계 검사는 결과 구조·상태, 원본 재추출 결과·파일 해시·인용 위치를 확인한다.'
                          if packet else '기계 검사는 결과 구조와 상태를 확인한다. 원자료·해시·인용 위치 검사는 수행하지 않았다.')
    lines = ['# 논문과 연구 관련성 검토', '',
             f"**상태: {status[review['status']]} · 읽은 범위: {scope[review['reading']['scope']]}**", '',
             f"논문: {md(paper['title'] or '제목 미확인')}", '',
             f"저자: {md(', '.join(paper['authors']) or '미확인')} · 연도: {paper['year'] or '미확인'}", '',
             f"식별자: {md(paper['identifier'] or '미확인')}", '',
             '## 1. 판단', '', f"**관련도: {review['relevance']['level']}**", '',
             f"[해석] {md(review['relevance']['reason'])}", '',
             f"연구 맥락: {md(review['research_context'] or '제공되지 않음 — 관련성 판단 보류')}", '',
             '## 2. 문제와 핵심 방법', '',
             f"문제: {md(review['summary']['problem'] or '본문을 확보하지 못해 정리하지 않음')}", '',
             f"방법: {md(review['summary']['method'] or '본문을 확보하지 못해 정리하지 않음')}", '',
             '## 3. 원문 주장과 근거', '']
    prefix = 'PDF 물리 쪽' if packet and packet['source']['media_type'] == 'application/pdf' else '텍스트 페이지'
    for claim in review['claims']:
        positions = '; '.join(f"{prefix} {e['page']}, 추출 행 {e['start_line']}–{e['end_line']}" for e in claim['evidence'])
        lines += [f"### {md(claim['id'])}. {md(claim['statement'])}", '',
                  f'근거: {positions}', '', f"조건·한계: {md(claim['conditions'])}", '']
    if not review['claims']:
        lines += ['본문 근거를 확보하지 못해 주장을 작성하지 않았다.', '']
    lines += ['## 4. 연구 연결과 검증할 것', '']
    for index, item in enumerate(review['connections'], 1):
        test = item['test']
        lines += [f"### 제안 {index}. {md(item['idea'])}", '',
                  f"조건 차이: {md(item['difference'])}", '',
                  f"비교 대상: {md(test['baseline'])}", '',
                  f"측정할 것: {md(test['metric'])}", '',
                  f"지지되지 않는 것으로 판단할 조건: {md(test['failure_condition'])}", '']
    if not review['connections']:
        lines += ['확인된 연구 연결을 제시하지 않았다. 이유는 판단 항목을 참조한다.', '']
    lines += ['## 5. 한계와 다음 행동', '']
    lines += [f'- {md(item)}' for item in review['limitations']]
    lines += ['', f"**다음 행동:** {md(review['next_action'])}", '', '검증 범위:', '',
              f"- 의미 대조 방식: {review_mode[review['semantic_review']['mode']]}",
              f"- 의미 대조 기록: {md(review['semantic_review']['notes'])}",
              f'- {verification_scope} 의미 정확성과 논문의 과학적 타당성을 자동 보장하지 않는다.',
              '- 읽은 범위와 의미 대조 기록은 작업자의 수행 기록이며, 스크립트가 실제 독해를 증명하지 않는다.', '']
    if packet:
        origin = packet['source']['origin']
        source_label = md(origin or Path(packet['source']['file']).name)
        if origin:
            try:
                parsed = urlsplit(origin)
                if parsed.scheme in ('http', 'https') and parsed.netloc:
                    source_label = '[원문 출처](<' + quote(origin, safe=':/?&=#%[]@+') + '>)'
            except ValueError:
                pass  # Preserve malformed identifiers as escaped text, never as active links.
        lines += [f"출처: {source_label}", '',
                  f"읽은 페이지: {', '.join(map(str, review['reading']['pages_read']))}", '',
                  f"시각 확인 페이지: {', '.join(map(str, review['reading']['visual_pages'])) or '없음'}", '']
        if packet['warnings']:
            lines += ['추출 주의사항:', ''] + [f'- {md(x)}' for x in packet['warnings']] + ['']
    return '\n'.join(lines)


def write_new(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.paper-review-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
        os.link(temporary, path)  # Atomic create; an existing file or symlink is never replaced.
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result', required=True, type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        review, packet, code = validate_review(args.result, args.source)
        write_new(args.output, render(review, packet))
    except Invalid as exc:
        print(f'FAIL: {exc}')
        return exc.code
    except (OSError, ValueError) as exc:
        print(f'FAIL OUTPUT: 새 파일을 만들 수 없습니다. 기존 파일은 덮어쓰지 않습니다. ({exc})')
        return 2
    print(status_message(code))
    print(f'REPORT: {args.output}')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
