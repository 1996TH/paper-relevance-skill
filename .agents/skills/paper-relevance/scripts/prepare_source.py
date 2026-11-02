#!/usr/bin/env python3
"""Extract a local PDF or UTF-8 text into a page-addressable evidence packet."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile

MAX_BYTES = 50 * 1024 * 1024
MAX_PAGES = 500
SCOPES = ('full_text', 'excerpt', 'abstract_only')


class SourceError(ValueError):
    pass


def normalize(text):
    return text.replace('\r\n', '\n').replace('\r', '\n')


def extract_pdf(raw):
    try:
        import pypdf
    except ImportError as error:
        raise SourceError('PDF 추출에는 pypdf가 필요합니다. 텍스트 추출본을 제공하거나 pypdf를 설치하세요.') from error
    try:
        reader = pypdf.PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            raise SourceError('암호화된 PDF입니다. 사용자가 잠금을 해제한 사본을 제공해야 합니다.')
        count = len(reader.pages)
        if not 1 <= count <= MAX_PAGES:
            raise SourceError(f'PDF 페이지 수 {count}: 허용 범위는 1~{MAX_PAGES}쪽입니다. 나눈 사본의 범위를 명시하세요.')
        pages = [{'page': i, 'text': normalize(page.extract_text() or '')}
                 for i, page in enumerate(reader.pages, 1)]
    except SourceError:
        raise
    except Exception as error:
        raise SourceError(f'PDF 추출 실패: {type(error).__name__}: {str(error)[:200]}') from error
    return pages, f'pypdf {pypdf.__version__}'


def prepare(input_path, output_path, origin=None, scope=None):
    input_path, output_path = Path(input_path), Path(output_path)
    if scope is not None and scope not in SCOPES:
        raise SourceError('지원하지 않는 원문 범위입니다.')
    if output_path.exists() or output_path.is_symlink():
        raise SourceError('출력 경로가 이미 존재합니다. 원자료와 기존 결과를 덮어쓰지 않습니다.')
    try:
        source_path = input_path.resolve(strict=True)
        if not source_path.is_file():
            raise SourceError('입력은 로컬 일반 파일이어야 합니다.')
        if source_path.stat().st_size > MAX_BYTES:
            raise SourceError('입력이 50 MiB를 초과합니다. 범위를 명시한 더 작은 사본을 사용하세요.')
        with source_path.open('rb') as handle:
            raw = handle.read(MAX_BYTES + 1)
    except SourceError:
        raise
    except (OSError, RuntimeError, ValueError) as error:
        raise SourceError(f'로컬 입력을 읽을 수 없습니다: {error}') from error
    if not raw:
        raise SourceError('입력 파일이 비어 있습니다.')
    if len(raw) > MAX_BYTES:
        raise SourceError('읽는 동안 입력이 50 MiB를 초과했습니다.')
    is_pdf = source_path.suffix.lower() == '.pdf' or raw.startswith(b'%PDF-')
    if is_pdf:
        pages, extractor = extract_pdf(raw)
        media_type = 'application/pdf'
        scope = scope or 'full_text'
    else:
        if source_path.suffix.lower() not in ('', '.txt', '.text', '.md', '.markdown'):
            raise SourceError('지원 형식은 PDF 또는 UTF-8 TXT/Markdown입니다. 웹 문서는 먼저 텍스트로 확보하세요.')
        try:
            text = normalize(raw.decode('utf-8-sig'))
        except UnicodeError as error:
            raise SourceError('입력이 UTF-8 텍스트가 아닙니다. 인코딩을 확인한 사본이 필요합니다.') from error
        if '\x00' in text:
            raise SourceError('텍스트에 NUL 바이트가 있어 일반 UTF-8 문서로 처리할 수 없습니다.')
        pages, extractor = [{'page': 1, 'text': text}], 'utf8'
        media_type = 'text/plain'
        scope = scope or 'excerpt'
    blank = [page['page'] for page in pages if not page['text'].strip()]
    if len(blank) == len(pages):
        raise SourceError('추출 가능한 텍스트가 없습니다. 스캔 PDF라면 OCR 또는 원문 시각 확인이 필요합니다.')
    warnings = ['텍스트 추출은 실제 읽기·그림 확인·내용 검증의 증거가 아닙니다.']
    if blank:
        warnings.append('텍스트가 추출되지 않은 페이지: ' + ', '.join(map(str, blank))
                        + '. 빈 페이지인지 스캔·추출 실패인지 확인하세요.')
    packet = {
        'schema_version': 1,
        'source': {
            'file': os.path.relpath(source_path, output_path.parent.resolve()),
            'sha256': hashlib.sha256(raw).hexdigest(),
            'origin': origin.strip() if isinstance(origin, str) and origin.strip() else None,
            'media_type': media_type,
            'available_scope': scope,
            'extractor': extractor,
            'page_count': len(pages),
        },
        'pages': pages,
        'warnings': warnings,
    }
    write_exclusive(output_path, packet)
    return packet


def write_exclusive(path, packet):
    """Publish a complete file atomically; link() refuses existing destinations."""
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.source-', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(packet, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    except (OSError, ValueError) as error:
        raise SourceError(f'출력을 독점 생성할 수 없습니다(기존 파일은 보존됨): {error}') from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--origin', help='출처 URL 등 식별 정보. 자동으로 접속하지 않습니다.')
    parser.add_argument('--scope', choices=SCOPES,
                        help='PDF 기본 full_text, TXT/MD 기본 excerpt. 실제 확보 범위를 지정하세요.')
    args = parser.parse_args()
    try:
        packet = prepare(args.input, args.output, args.origin, args.scope)
    except (SourceError, OSError, ValueError, RecursionError) as error:
        print(f'FAIL SOURCE: {error}')
        return 2
    print(f'PREPARED: {args.output}')
    print(f"pages={len(packet['pages'])}; lines={sum(len(p['text'].splitlines()) for p in packet['pages'])}; scope={packet['source']['available_scope']}")
    for warning in packet['warnings']:
        print(f'NOTE: {warning}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
