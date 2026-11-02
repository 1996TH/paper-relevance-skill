#!/usr/bin/env python3
"""Validate paper-relevance records; text matching does not prove a claim true."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from prepare_source import SourceError, extract_pdf, normalize


class Invalid(ValueError):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def require(condition, message, code=1):
    if not condition:
        raise Invalid(message, code)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def integer(value):
    return type(value) is int


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def normalized(value):
    return ' '.join(value.split())


def read_bounded(path, limit):
    try:
        info = path.stat()
        require(stat.S_ISREG(info.st_mode), f'Not a regular file: {path}', 2)
        require(info.st_size <= limit, f'File exceeds {limit} bytes: {path}', 2)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            require(stat.S_ISREG(opened.st_mode) and opened.st_size <= limit,
                    f'Opened file is not regular or exceeds size limit: {path}', 2)
            raw = stream.read(limit + 1)
        require(len(raw) <= limit, f'File grew beyond {limit} bytes: {path}', 2)
        return raw
    except (OSError, ValueError) as exc:
        if isinstance(exc, Invalid):
            raise
        raise Invalid(f'Cannot read {path}: {exc}', 2) from exc


def load_json(path, code):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'duplicate key: {key!r}')
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f'nonstandard JSON number: {value}')

    raw = read_bounded(path, 64 * 1024 * 1024)
    try:
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
                          parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise Invalid(f'Invalid JSON in {path}: {exc}', code) from exc
    require(isinstance(data, dict), f'JSON root must be an object: {path}', code)
    pending = [data]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            try:
                value.encode('utf-8')
            except UnicodeError as exc:
                raise Invalid(f'JSON contains a string not encodable as UTF-8: {path}', code) from exc
        elif isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return data, raw


def strings(value, label, code=1):
    require(isinstance(value, list) and all(text(x) for x in value),
            f'{label}: expected a list of nonempty strings', code)


def read_source(path):
    packet, raw = load_json(path, 2)
    require(type(packet.get('schema_version')) is int and packet['schema_version'] == 1,
            'Source schema_version must be 1', 2)
    source = packet.get('source')
    require(isinstance(source, dict), 'Source metadata must be an object', 2)
    for key in ('file', 'extractor'):
        require(text(source.get(key)), f'source.{key}: required text', 2)
    require(source.get('origin') is None or text(source.get('origin')),
            'source.origin: text or null required', 2)
    require(source.get('media_type') in ('application/pdf', 'text/plain'),
            'Unsupported source media_type', 2)
    require(source.get('available_scope') in ('full_text', 'excerpt', 'abstract_only'),
            'Invalid available_scope', 2)
    require(isinstance(source.get('sha256'), str)
            and re.fullmatch(r'[0-9a-f]{64}', source['sha256']), 'Invalid source hash', 2)
    count = source.get('page_count')
    require(integer(count) and 1 <= count <= 500, 'Invalid source page_count', 2)
    pages = packet.get('pages')
    require(isinstance(pages, list) and len(pages) == count, 'Source page count mismatch', 2)
    for number, page in enumerate(pages, 1):
        require(isinstance(page, dict) and type(page.get('page')) is int
                and page['page'] == number and isinstance(page.get('text'), str),
                f'Invalid source page {number}', 2)
    require(any(text(p['text']) for p in pages), 'No readable source text', 2)
    strings(packet.get('warnings'), 'source warnings', 2)
    relative = Path(source['file'])
    require(not relative.is_absolute(), 'source.file must be relative to the packet', 2)
    original_path = path.parent / relative
    original = read_bounded(original_path, 50 * 1024 * 1024)
    require(digest(original) == source['sha256'], 'Original source hash changed', 2)
    # A packet hash alone cannot bind its extracted text to the original bytes.
    is_pdf = original_path.suffix.lower() == '.pdf' or original.startswith(b'%PDF-')
    actual_type = 'application/pdf' if is_pdf else 'text/plain'
    require(source['media_type'] == actual_type, 'Source media_type disagrees with original', 2)
    try:
        if is_pdf:
            actual_pages, actual_extractor = extract_pdf(original)
        else:
            require(original_path.suffix.lower() in ('', '.txt', '.text', '.md', '.markdown'),
                    'Unsupported original text format', 2)
            content = normalize(original.decode('utf-8-sig'))
            require('\x00' not in content, 'NUL byte in original text', 2)
            actual_pages, actual_extractor = [{'page': 1, 'text': content}], 'utf8'
    except (SourceError, UnicodeError, RecursionError) as exc:
        raise Invalid(f'Cannot re-extract original source: {exc}', 2) from exc
    require(source['extractor'] == actual_extractor,
            'Extractor differs; regenerate the source packet in this environment', 2)
    require(pages == actual_pages, 'Source packet text does not match original extraction', 2)
    return packet, digest(raw)


def validate_review(result_path, source_path=None):
    """Return (review, packet_or_none, status_code); raise Invalid for defects."""
    result_path = Path(result_path)
    review, _ = load_json(result_path, 1)
    require(type(review.get('schema_version')) is int and review['schema_version'] == 1,
            'Review schema_version must be 1')
    status = review.get('status')
    require(status in ('complete', 'partial', 'blocked'), 'Invalid review status')
    paper = review.get('paper')
    require(isinstance(paper, dict), 'paper must be an object')
    for key in ('title', 'identifier'):
        require(key in paper and (paper[key] is None or text(paper[key])),
                f'paper.{key}: nonempty text or null required')
    strings(paper.get('authors'), 'paper.authors')
    require('year' in paper and (paper['year'] is None or
            (integer(paper['year']) and 1 <= paper['year'] <= 9999)),
            'paper.year: integer year or null required')
    reading = review.get('reading')
    require(isinstance(reading, dict), 'reading must be an object')
    scope = reading.get('scope')
    require(scope in ('full_text', 'excerpt', 'abstract_only', 'unavailable'),
            'Invalid reading scope')
    for key in ('pages_read', 'visual_pages'):
        values = reading.get(key)
        require(isinstance(values, list) and all(integer(x) and x >= 1 for x in values),
                f'reading.{key}: positive integer list required')
        require(values == sorted(set(values)), f'reading.{key}: sorted unique pages required')
    require(set(reading['visual_pages']) <= set(reading['pages_read']),
            'Visual pages must be included in pages_read')
    require('research_context' in review and (review['research_context'] is None
            or text(review['research_context'])), 'research_context: text or null required')
    relevance = review.get('relevance')
    require(isinstance(relevance, dict) and relevance.get('level') in
            ('높음', '부분', '낮음', '보류') and text(relevance.get('reason')),
            'Invalid relevance judgment')
    summary = review.get('summary')
    require(isinstance(summary, dict) and all(isinstance(summary.get(k), str)
            for k in ('problem', 'method')), 'summary requires problem and method strings')
    claims = review.get('claims')
    connections = review.get('connections')
    require(isinstance(claims, list) and len(claims) <= 5, 'claims: at most 5 required')
    require(isinstance(connections, list), 'connections must be a list')
    strings(review.get('limitations'), 'limitations')
    require(text(review.get('next_action')), 'next_action must be nonempty')
    semantic = review.get('semantic_review')
    require(isinstance(semantic, dict) and semantic.get('mode') in
            ('self', 'independent', 'not_performed') and text(semantic.get('notes')),
            'Invalid semantic_review record')
    strings(semantic.get('checked_claim_ids'), 'semantic_review.checked_claim_ids')
    require(len(semantic['checked_claim_ids']) == len(set(semantic['checked_claim_ids'])),
            'Duplicate checked claim IDs')
    if review['research_context'] is None:
        require(relevance['level'] == '보류' and not connections,
                'Missing research context requires relevance hold and no connections')
    if status == 'blocked':
        require(source_path is None and review.get('source_packet_sha256') is None,
                'Blocked review must not assert an available source packet')
        require(scope == 'unavailable' and not reading['pages_read'] and
                not reading['visual_pages'] and not claims and not connections and
                relevance['level'] == '보류' and semantic['mode'] == 'not_performed'
                and not semantic['checked_claim_ids'], 'Inconsistent blocked review')
        return review, None, 3
    require(source_path is not None, '--source is required for an available source', 2)
    packet, packet_hash = read_source(Path(source_path))
    require(review.get('source_packet_sha256') == packet_hash, 'Source packet hash mismatch')
    require(scope != 'unavailable' and bool(reading['pages_read']), 'Available source needs a reading scope and pages')
    require(scope != 'full_text' or packet['source']['available_scope'] == 'full_text',
            'Partial source cannot be reported as full_text')
    require(packet['source']['available_scope'] != 'abstract_only' or scope == 'abstract_only',
            'An abstract-only source must retain abstract_only scope')
    require(set(reading['pages_read']) <= set(range(1, packet['source']['page_count'] + 1)),
            'Reading page is outside source bounds')
    require(scope != 'full_text' or reading['pages_read'] == list(range(1, len(packet['pages']) + 1)),
            'full_text reading scope requires every source page, regardless of completion status')
    require(all(text(summary[k]) for k in ('problem', 'method')), 'Available-source summary cannot be empty')
    require(1 <= len(claims) <= 5, 'Available source needs 1 to 5 supported claims')
    ids = []
    for index, claim in enumerate(claims):
        label = f'claims[{index}]'
        require(isinstance(claim, dict), f'{label}: object required')
        for key in ('id', 'statement', 'conditions'):
            require(text(claim.get(key)), f'{label}.{key}: nonempty text required')
        ids.append(claim['id'])
        evidence = claim.get('evidence')
        require(isinstance(evidence, list) and evidence, f'{label}: evidence required')
        for item in evidence:
            require(isinstance(item, dict), f'{label}: invalid evidence object')
            page, start, end = (item.get(k) for k in ('page', 'start_line', 'end_line'))
            require(all(integer(x) for x in (page, start, end)), f'{label}: integer evidence positions required')
            require(page in reading['pages_read'], f'{label}: evidence page was not recorded as read')
            lines = packet['pages'][page - 1]['text'].splitlines()
            require(1 <= start <= end <= len(lines), f'{label}: evidence line range outside source')
            quote = item.get('quote')
            require(text(quote) and len(quote) <= 1200, f'{label}: nonempty quote up to 1200 characters required')
            require(normalized(quote) in normalized('\n'.join(lines[start - 1:end])),
                    f'{label}: quote not found within cited lines')
    require(len(ids) == len(set(ids)), 'Claim IDs must be unique')
    require(set(semantic['checked_claim_ids']) <= set(ids), 'Unknown checked claim IDs')
    require(semantic['mode'] != 'not_performed' or not semantic['checked_claim_ids'],
            'Unperformed semantic review cannot list checked claims')
    for index, item in enumerate(connections):
        require(isinstance(item, dict) and text(item.get('idea')) and text(item.get('difference')),
                f'connections[{index}]: idea and difference required')
        test = item.get('test')
        require(isinstance(test, dict) and all(text(test.get(k)) for k in
                ('baseline', 'metric', 'failure_condition')), f'connections[{index}]: test definition required')
    if status == 'complete':
        require(scope == 'full_text' and reading['pages_read'] == list(range(1, len(packet['pages']) + 1)),
                'Complete requires full_text and every source page read')
        require(all(text(p['text']) for p in packet['pages']), 'Blank extraction pages prevent complete status')
        require(text(review['research_context']), 'Complete requires research context')
        require(semantic['mode'] != 'not_performed' and set(semantic['checked_claim_ids']) == set(ids),
                'Complete requires semantic review of every claim')
    return review, packet, 0 if status == 'complete' else 3


def status_message(code):
    return ('PASS COMPLETE: 구조·원자료/추출본 해시·근거 위치 확인' if code == 0 else
            'VALID INCOMPLETE: 기록 형식은 유효하지만 작업은 부분 완료 또는 중단 상태')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result', required=True, type=Path)
    parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    try:
        _, _, code = validate_review(args.result, args.source)
    except Invalid as exc:
        print(f'FAIL: {exc}')
        return exc.code
    print(status_message(code))
    print('LIMIT: 문자열 일치·해시는 의미의 정확성, 논문 신뢰성 또는 일반화를 보장하지 않습니다.')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
