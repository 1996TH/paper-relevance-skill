#!/usr/bin/env python3
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/prepare_source.py'
spec = importlib.util.spec_from_file_location('prepare_source', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
HAS_PYPDF = importlib.util.find_spec('pypdf') is not None


def pdf_file(path, texts, password=None):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                             NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    for text in texts:
        page = writer.add_blank_page(200, 200)
        if text:
            page[NameObject('/Resources')] = DictionaryObject({
                NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(f'BT /F1 12 Tf 10 150 Td ({text}) Tj ET'.encode('ascii'))
            page[NameObject('/Contents')] = writer._add_object(stream)
    if password:
        writer.encrypt(password)
    with path.open('wb') as handle:
        writer.write(handle)


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'input.txt'
        self.output = self.root / 'run/source.json'
        self.source.write_text('First line\nSecond line\n', encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, source=None, *extras):
        return subprocess.run([sys.executable, str(SCRIPT), str(source or self.source),
                               '--output', str(self.output), *extras], capture_output=True, text=True)

    def assert_failure(self, result):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('FAIL SOURCE:', result.stdout)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(self.output.exists())

    def test_utf8_bom_crlf_and_relative_source(self):
        raw = b'\xef\xbb\xbf' + '논문\r\n다음 행\r마지막'.encode('utf-8')
        self.source.write_bytes(raw)
        result = self.run_cli(None, '--origin', 'https://example.org/paper')
        self.assertEqual(result.returncode, 0, result.stdout)
        packet = json.loads(self.output.read_text())
        self.assertEqual(packet['pages'], [{'page': 1, 'text': '논문\n다음 행\n마지막'}])
        self.assertEqual(packet['source']['available_scope'], 'excerpt')
        self.assertEqual(packet['source']['file'], '../input.txt')
        self.assertEqual(packet['source']['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(packet['source']['origin'], 'https://example.org/paper')
        self.assertEqual(self.source.read_bytes(), raw)
        self.assertNotIn('다음 행', result.stdout)

    def test_explicit_abstract_scope(self):
        packet = module.prepare(self.source, self.output, scope='abstract_only')
        self.assertEqual(packet['source']['available_scope'], 'abstract_only')

    def test_existing_output_preserved(self):
        self.output.parent.mkdir()
        self.output.write_text('KEEP')
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.output.read_text(), 'KEEP')
        self.assertEqual(list(self.output.parent.glob('.source-*')), [])

    def test_same_source_output_preserved(self):
        original = self.source.read_bytes()
        with self.assertRaises(module.SourceError):
            module.prepare(self.source, self.source)
        self.assertEqual(self.source.read_bytes(), original)

    def test_output_symlink_preserved(self):
        self.output.parent.mkdir()
        self.output.symlink_to(self.source)
        original = self.source.read_bytes()
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertTrue(self.output.is_symlink())
        self.assertEqual(self.source.read_bytes(), original)

    def test_directory_input_rejected(self):
        self.assert_failure(self.run_cli(self.root))

    def test_empty_and_whitespace_text_rejected(self):
        for raw in (b'', b' \r\n\t'):
            with self.subTest(raw=raw):
                self.source.write_bytes(raw)
                self.assert_failure(self.run_cli())

    def test_bad_utf8_and_nul_rejected(self):
        for raw in (b'\xff\xfe', b'abc\x00def'):
            with self.subTest(raw=raw):
                self.source.write_bytes(raw)
                self.assert_failure(self.run_cli())

    def test_file_size_limit(self):
        with self.source.open('wb') as handle:
            handle.truncate(module.MAX_BYTES + 1)
        self.assert_failure(self.run_cli())

    def test_unsupported_type(self):
        other = self.root / 'input.html'
        other.write_text('<html>content</html>')
        self.assert_failure(self.run_cli(other))

    @unittest.skipUnless(HAS_PYPDF, 'pypdf optional dependency is unavailable')
    def test_pdf_pages_preserved_and_input_unchanged(self):
        source = self.root / 'paper.pdf'
        pdf_file(source, ['Page one.', 'Page two.'])
        original = source.read_bytes()
        result = self.run_cli(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        packet = json.loads(self.output.read_text())
        self.assertEqual([p['page'] for p in packet['pages']], [1, 2])
        self.assertIn('Page one.', packet['pages'][0]['text'])
        self.assertIn('Page two.', packet['pages'][1]['text'])
        self.assertEqual(packet['source']['available_scope'], 'full_text')
        self.assertEqual(packet['source']['page_count'], 2)
        self.assertEqual(source.read_bytes(), original)

    @unittest.skipUnless(HAS_PYPDF, 'pypdf optional dependency is unavailable')
    def test_image_only_pdf_blocked(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, NumberObject, DecodedStreamObject
        source = self.root / 'scan.pdf'
        writer = PdfWriter()
        page = writer.add_blank_page(200, 200)
        image = DecodedStreamObject()
        image.set_data(b'\xff\xff\xff')
        image.update({NameObject('/Type'): NameObject('/XObject'),
                      NameObject('/Subtype'): NameObject('/Image'),
                      NameObject('/Width'): NumberObject(1), NameObject('/Height'): NumberObject(1),
                      NameObject('/ColorSpace'): NameObject('/DeviceRGB'),
                      NameObject('/BitsPerComponent'): NumberObject(8)})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/XObject'):
            DictionaryObject({NameObject('/Im0'): writer._add_object(image)})})
        content = DecodedStreamObject()
        content.set_data(b'q 100 0 0 100 0 0 cm /Im0 Do Q')
        page[NameObject('/Contents')] = writer._add_object(content)
        with source.open('wb') as handle:
            writer.write(handle)
        self.assert_failure(self.run_cli(source))

    @unittest.skipUnless(HAS_PYPDF, 'pypdf optional dependency is unavailable')
    def test_partial_blank_pdf_warns_without_dropping_page(self):
        source = self.root / 'partial.pdf'
        pdf_file(source, ['Has text.', ''])
        result = self.run_cli(source)
        self.assertEqual(result.returncode, 0, result.stdout)
        packet = json.loads(self.output.read_text())
        self.assertEqual(packet['source']['page_count'], 2)
        self.assertEqual(packet['pages'][1], {'page': 2, 'text': ''})
        self.assertTrue(any('페이지: 2' in w for w in packet['warnings']))

    @unittest.skipUnless(HAS_PYPDF, 'pypdf optional dependency is unavailable')
    def test_encrypted_pdf_blocked(self):
        source = self.root / 'locked.pdf'
        pdf_file(source, ['Private.'], password='test-only')
        self.assert_failure(self.run_cli(source))

    @unittest.skipUnless(HAS_PYPDF, 'pypdf optional dependency is unavailable')
    def test_pdf_page_limit(self):
        source = self.root / 'large.pdf'
        pdf_file(source, [''] * (module.MAX_PAGES + 1))
        self.assert_failure(self.run_cli(source))

    @unittest.skipUnless(HAS_PYPDF, 'pypdf optional dependency is unavailable')
    def test_malformed_pdf(self):
        source = self.root / 'bad.pdf'
        source.write_bytes(b'%PDF-1.7\nnot a valid PDF')
        self.assert_failure(self.run_cli(source))


if __name__ == '__main__':
    unittest.main(verbosity=2)
