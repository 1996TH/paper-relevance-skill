#!/usr/bin/env python3
"""Small regression suite for record integrity and deterministic reports."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from check_result import Invalid, validate_review
from render_report import render, write_new
from prepare_source import prepare


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.raw = self.root / 'paper.txt'
        self.raw.write_text('Title\nLearning representations\nfrom time series.\n', encoding='utf-8')
        self.source = self.root / 'source.json'
        self.result = self.root / 'review.json'
        self.packet = {
            'schema_version': 1,
            'source': {'file': 'paper.txt', 'sha256': sha(self.raw.read_bytes()), 'origin': None,
                       'media_type': 'text/plain', 'available_scope': 'full_text',
                       'extractor': 'utf8', 'page_count': 1},
            'pages': [{'page': 1, 'text': self.raw.read_text()}], 'warnings': []}
        self.save(self.source, self.packet)
        self.review = {
            'schema_version': 1, 'status': 'complete',
            'paper': {'title': None, 'authors': [], 'year': None, 'identifier': None},
            'source_packet_sha256': sha(self.source.read_bytes()),
            'reading': {'scope': 'full_text', 'pages_read': [1], 'visual_pages': []},
            'research_context': '시계열 표현학습',
            'relevance': {'level': '부분', 'reason': '방법의 연결점을 검토한다.'},
            'summary': {'problem': '시계열 학습', 'method': '표현을 학습한다.'},
            'claims': [{'id': 'C1', 'statement': '시계열로부터 표현을 학습한다.',
                        'evidence': [{'page': 1, 'start_line': 2, 'end_line': 3,
                                      'quote': 'Learning representations from time series.'}],
                        'conditions': '효과 수치는 제시되지 않았다.'}],
            'connections': [{'idea': '같은 조건으로 비교한다.', 'difference': '대상 데이터 차이',
                             'test': {'baseline': '원시 입력', 'metric': '분류 지표',
                                      'failure_condition': '차이가 없으면 지지하지 않음'}}],
            'limitations': ['이 fixture는 검증기 시험용이다.'], 'next_action': '비교 조건을 확정한다.',
            'semantic_review': {'mode': 'self', 'checked_claim_ids': ['C1'], 'notes': '근거와 대조했다.'}}
        self.save(self.result, self.review)

    def save(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def pdf_source(self, second_text):
        try:
            from pypdf import PdfWriter
            from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        except ImportError:
            self.skipTest('pypdf is optional and unavailable')
        writer = PdfWriter()
        for content in ('Title\nLearning representations\nfrom time series.', second_text):
            page = writer.add_blank_page(width=200, height=200)
            if content:
                font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                         NameObject('/Subtype'): NameObject('/Type1'),
                                         NameObject('/BaseFont'): NameObject('/Helvetica')})
                page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
                    DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
                stream = DecodedStreamObject()
                commands = 'BT /F1 10 Tf 10 170 Td '
                commands += ' 0 -12 Td '.join('(' + line + ') Tj' for line in content.splitlines())
                stream.set_data((commands + ' ET').encode('ascii'))
                page[NameObject('/Contents')] = writer._add_object(stream)
        self.raw = self.root / 'paper.pdf'
        with self.raw.open('wb') as output:
            writer.write(output)
        self.source = self.root / 'pdf-source.json'
        self.packet = prepare(self.raw, self.source, scope='full_text')
        self.review['source_packet_sha256'] = sha(self.source.read_bytes())

    def check(self, review=None, packet=None):
        if packet is not None:
            self.save(self.source, packet)
        if review is not None:
            self.save(self.result, review)
        return validate_review(self.result, self.source)

    def assertInvalid(self, code, review=None, packet=None):
        with self.assertRaises(Invalid) as found:
            self.check(review, packet)
        self.assertEqual(found.exception.code, code)

    def cli(self, script, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)],
                              capture_output=True, text=True)

    def blocked(self):
        value = copy.deepcopy(self.review)
        value.update(status='blocked', source_packet_sha256=None, research_context=None,
                     claims=[], connections=[])
        value['reading'] = {'scope': 'unavailable', 'pages_read': [], 'visual_pages': []}
        value['relevance']['level'] = '보류'
        value['summary'] = {'problem': '', 'method': ''}
        value['semantic_review'] = {'mode': 'not_performed', 'checked_claim_ids': [], 'notes': '접근 실패'}
        return value

    def test_complete_unknown_metadata_and_multiline_quote(self):
        self.assertEqual(self.check()[2], 0)

    def test_partial_abstract_retains_scope(self):
        self.packet['source']['available_scope'] = 'abstract_only'
        self.save(self.source, self.packet)
        self.review['source_packet_sha256'] = sha(self.source.read_bytes())
        self.review['status'] = 'partial'
        self.review['reading']['scope'] = 'abstract_only'
        self.assertEqual(self.check(self.review)[2], 3)
        self.review['reading']['scope'] = 'full_text'
        self.assertInvalid(1, self.review)

    def test_partial_full_text_still_requires_all_pages(self):
        self.pdf_source('Other source text')
        self.review['status'] = 'partial'
        self.assertInvalid(1, self.review)
        self.review['reading']['pages_read'] = [1, 2]
        self.review['research_context'] = None
        self.review['relevance']['level'] = '보류'
        self.review['connections'] = []
        self.assertEqual(self.check(self.review)[2], 3)

    def test_unpaired_surrogates_rejected_in_values_and_keys(self):
        for key_case in (False, True):
            with self.subTest(key_case=key_case):
                value = copy.deepcopy(self.review)
                if key_case:
                    value['\ud800'] = 'invalid key'
                else:
                    value['paper']['title'] = '\ud800'
                self.result.write_text(json.dumps(value, ensure_ascii=True))
                output = self.root / 'invalid-utf8.md'
                proc = self.cli('render_report.py', '--result', self.result,
                                '--source', self.source, '--output', output)
                self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                self.assertEqual(proc.stderr, '')
                self.assertFalse(output.exists())
        self.save(self.result, self.review)
        for key_case in (False, True):
            with self.subTest(source_key_case=key_case):
                value = copy.deepcopy(self.packet)
                if key_case:
                    value['\ud800'] = 'invalid key'
                else:
                    value['source']['origin'] = '\ud800'
                self.source.write_text(json.dumps(value, ensure_ascii=True))
                proc = self.cli('check_result.py', '--result', self.result, '--source', self.source)
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertEqual(proc.stderr, '')

    def test_duplicate_surrogate_key_has_clean_error(self):
        self.result.write_text('{"\\ud800": 1, "\\ud800": 2}')
        proc = self.cli('check_result.py', '--result', self.result, '--source', self.source)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stderr, '')

    def test_missing_context_is_valid_hold_not_complete(self):
        self.review.update(status='partial', research_context=None, connections=[])
        self.review['relevance']['level'] = '보류'
        self.assertEqual(self.check(self.review)[2], 3)
        self.review['status'] = 'complete'
        self.assertInvalid(1, self.review)

    def test_missing_context_cannot_propose_connections(self):
        self.review.update(status='partial', research_context=None)
        self.review['relevance']['level'] = '보류'
        self.assertInvalid(1, self.review)

    def test_blocked_is_unresolved_and_can_render(self):
        self.save(self.result, self.blocked())
        self.assertEqual(validate_review(self.result)[2], 3)
        output = self.root / 'blocked.md'
        proc = self.cli('render_report.py', '--result', self.result, '--output', output)
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn('자료 확보 단계에서 중단', output.read_text())
        self.assertEqual(proc.stderr, '')

    def test_report_only_claims_checks_actually_performed(self):
        blocked = render(self.blocked(), None)
        self.assertIn('기계 검사는 결과 구조와 상태를 확인한다.', blocked)
        self.assertIn('원자료·해시·인용 위치 검사는 수행하지 않았다.', blocked)
        self.assertNotIn('원본 재추출 결과·파일 해시·인용 위치를 확인한다.', blocked)
        self.assertIn('의미 대조 방식: 미수행', blocked)
        complete = render(self.review, self.packet)
        self.assertIn('원본 재추출 결과·파일 해시·인용 위치를 확인한다.', complete)
        self.assertIn('의미 대조 방식: 자체 검토', complete)
        self.review['semantic_review']['mode'] = 'independent'
        self.assertIn('의미 대조 방식: 독립 검토', render(self.review, self.packet))
        self.assertNotIn('금융 일반화', complete)

    def test_blocked_cannot_claim_evidence_or_available_packet(self):
        value = self.blocked()
        value['claims'] = self.review['claims']
        self.save(self.result, value)
        with self.assertRaises(Invalid):
            validate_review(self.result)
        self.save(self.result, self.blocked())
        with self.assertRaises(Invalid):
            validate_review(self.result, self.source)

    def test_forged_quote_or_bad_span_rejected(self):
        for key, value in [('quote', 'Fabricated accuracy 99%'), ('start_line', 0),
                           ('end_line', 200), ('page', True)]:
            with self.subTest(key=key):
                review = copy.deepcopy(self.review)
                review['claims'][0]['evidence'][0][key] = value
                self.assertInvalid(1, review)

    def test_unread_evidence_page_rejected(self):
        self.pdf_source('Other source text')
        self.review.update(status='partial')
        self.review['reading']['scope'] = 'excerpt'
        self.review['claims'][0]['evidence'][0] = {'page': 2, 'start_line': 1, 'end_line': 1,
                                               'quote': 'Other source text'}
        self.assertInvalid(1, self.review)

    def test_original_source_change_rejected(self):
        self.raw.write_text('Changed original')
        self.assertInvalid(2)

    def test_packet_hash_change_rejected(self):
        self.packet['warnings'].append('Another extraction warning')
        self.assertInvalid(1, packet=self.packet)

    def test_forged_packet_rehashed_still_rejected(self):
        self.packet['pages'][0]['text'] = 'Fabricated accuracy 99%'
        self.save(self.source, self.packet)
        self.review['source_packet_sha256'] = sha(self.source.read_bytes())
        self.review['claims'][0]['evidence'][0] = {'page': 1, 'start_line': 1, 'end_line': 1,
                                               'quote': 'Fabricated accuracy 99%'}
        self.assertInvalid(2, self.review)

    def test_source_media_type_forgery_rejected(self):
        self.packet['source']['media_type'] = 'application/pdf'
        self.save(self.source, self.packet)
        self.review['source_packet_sha256'] = sha(self.source.read_bytes())
        self.assertInvalid(2, self.review)

    def test_nonregular_and_oversize_input_rejected(self):
        if Path('/dev/zero').exists():
            proc = self.cli('check_result.py', '--result', '/dev/zero', '--source', self.source)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stderr, '')
        if hasattr(os, 'mkfifo'):
            fifo = self.root / 'pipe'
            os.mkfifo(fifo)
            proc = subprocess.run([sys.executable, str(SCRIPTS / 'check_result.py'),
                                   '--result', str(fifo)], capture_output=True, text=True, timeout=3)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stderr, '')
        large = self.root / 'large.json'
        with large.open('wb') as handle:
            handle.truncate(65 * 1024 * 1024)
        proc = self.cli('check_result.py', '--result', large, '--source', self.source)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, '')

    def test_blank_page_prevents_complete(self):
        self.pdf_source('')
        self.review['reading']['pages_read'] = [1, 2]
        self.assertInvalid(1, self.review)
        self.review['status'] = 'partial'
        self.assertEqual(self.check(self.review)[2], 3)

    def test_semantic_review_and_claim_ids_consistent(self):
        self.review['semantic_review']['checked_claim_ids'] = []
        self.assertInvalid(1, self.review)
        self.review['status'] = 'partial'
        self.review['semantic_review']['mode'] = 'not_performed'
        self.assertEqual(self.check(self.review)[2], 3)
        self.review['claims'].append(copy.deepcopy(self.review['claims'][0]))
        self.assertInvalid(1, self.review)

    def test_bool_integer_and_missing_keys_rejected(self):
        for where, key, bad in [('paper', 'year', True), ('reading', 'pages_read', [True]),
                                ('semantic_review', 'checked_claim_ids', 'C1')]:
            with self.subTest(key=key):
                value = copy.deepcopy(self.review)
                value[where][key] = bad
                self.assertInvalid(1, value)
        value = copy.deepcopy(self.review)
        del value['paper']['title']
        self.assertInvalid(1, value)

    def test_bad_json_and_deep_json_have_clean_exit(self):
        for payload in ('{"schema_version":NaN}', '{"a":1,"a":2}', '[' * 10000 + '0' + ']' * 10000):
            self.result.write_text(payload)
            proc = self.cli('check_result.py', '--result', self.result, '--source', self.source)
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertEqual(proc.stderr, '')
            self.assertIn('FAIL:', proc.stdout)
        self.save(self.result, self.review)
        self.source.write_text('[' * 10000 + '0' + ']' * 10000)
        proc = self.cli('check_result.py', '--result', self.result, '--source', self.source)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, '')

    def test_render_is_deterministic_escaped_and_omits_raw_quote(self):
        self.review['paper']['title'] = '<script>alert(1)</script> [click](bad)'
        a = render(self.review, self.packet)
        self.assertEqual(a, render(self.review, self.packet))
        self.assertNotIn('<script>', a)
        self.assertNotIn('[click](bad)', a)
        self.assertNotIn('Learning representations', a)
        self.assertIn('텍스트 페이지 1', a)
        self.packet['source']['media_type'] = 'application/pdf'
        self.assertIn('PDF 물리 쪽 1', render(self.review, self.packet))
        self.packet['source']['origin'] = 'https://example.org/paper'
        self.assertIn('[원문 출처](<https://example.org/paper>)', render(self.review, self.packet))
        self.packet['source']['origin'] = 'javascript:alert(1)'
        self.assertNotIn('[원문 출처]', render(self.review, self.packet))

    def test_renderer_validates_before_write(self):
        self.review['claims'][0]['evidence'][0]['quote'] = 'not found'
        self.save(self.result, self.review)
        output = self.root / 'bad.md'
        proc = self.cli('render_report.py', '--result', self.result, '--source', self.source, '--output', output)
        self.assertEqual(proc.returncode, 1)
        self.assertFalse(output.exists())

    def test_output_never_overwrites_existing_or_source(self):
        output = self.root / 'report.md'
        args = ('--result', self.result, '--source', self.source, '--output', output)
        proc = self.cli('render_report.py', *args)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        original = output.read_bytes()
        self.assertEqual(self.cli('render_report.py', *args).returncode, 2)
        self.assertEqual(output.read_bytes(), original)
        before = self.raw.read_bytes()
        proc = self.cli('render_report.py', '--result', self.result, '--source', self.source, '--output', self.raw)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.raw.read_bytes(), before)
        self.assertFalse(list(self.root.glob('.paper-review-*')))

    def test_output_never_follows_symlink(self):
        link = self.root / 'link.md'
        link.symlink_to(self.root / 'absent.md')
        with self.assertRaises(FileExistsError):
            write_new(link, 'test')
        self.assertFalse((self.root / 'absent.md').exists())

    def test_missing_source_is_configuration_error(self):
        proc = self.cli('check_result.py', '--result', self.result)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, '')


if __name__ == '__main__':
    unittest.main(verbosity=2)
