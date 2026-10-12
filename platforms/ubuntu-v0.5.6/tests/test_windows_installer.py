"""Installer transfer tests; simulated HTTP/WinError, NOT native Windows E2E."""
from __future__ import annotations
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import ssl
from urllib.error import HTTPError, URLError

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('cache_wheel', ROOT / 'packaging/windows/cache_wheel.py')
cw = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cw)

NAME = 'torch-2.9.1+cu128-cp313-cp313-win_amd64.whl'
INDEX = 'https://download.pytorch.org/whl/cu128/torch/'
URL = 'https://download-r2.pytorch.org/whl/cu128/' + NAME.replace('+', '%2B')
DATA = b'wheel-test-content-' * 31
DIGEST = hashlib.sha256(DATA).hexdigest()
ENTRY = {'filename': NAME, 'url': URL, 'sha256': DIGEST, 'index': INDEX}


class Response(io.BytesIO):
    def __init__(self, body=DATA, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = {'Content-Length': str(len(body))} if headers is None else headers


class Server:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
    def __call__(self, url, headers=None):
        self.calls.append((url, headers or {}))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def part(cache):
    path = cache / DIGEST / (NAME + '.partial')
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def locked(code=32):
    exc = PermissionError(f'[WinError {code}] simulated')
    exc.winerror = code
    return exc


def test_index_exact_filename_and_publisher_hash():
    html = f'<a href="{URL}#sha256={DIGEST}">{NAME}</a>'
    assert cw.parse_index(html, INDEX, NAME) == ENTRY


@pytest.mark.parametrize('fragment', ['', '#sha256=bad', '#sha256=' + 'z'*64,
                                      '#sha256=' + DIGEST + '&sha256=' + DIGEST])
def test_index_rejects_missing_or_invalid_digest(fragment):
    with pytest.raises(cw.IntegrityError):
        cw.parse_index(f'<a href="{URL}{fragment}">x</a>', INDEX, NAME)


def test_index_rejects_conflicting_artifacts():
    html = f'<a href="{URL}#sha256={DIGEST}">x</a><a href="{URL}#sha256={"0"*64}">x</a>'
    with pytest.raises(cw.IntegrityError):
        cw.parse_index(html, INDEX, NAME)


def test_cpu_and_cuda_resolve_separate_filenames():
    name = NAME.replace('cu128', 'cpu')
    url = URL.replace('cu128', 'cpu')
    server = Server(Response(f'<a href="{url}#sha256={DIGEST}">x</a>'.encode()))
    result = cw.resolve('cpu', opener=server, delays=())
    assert result['filename'] == name
    assert server.calls[0][0] == INDEX.replace('cu128', 'cpu')


@pytest.mark.parametrize('url', ['http://download.pytorch.org/file',
    'https://evil.example/file', 'https://download.pytorch.org.evil.example/file',
    'https://user:secret@download.pytorch.org/file', 'https://download.pytorch.org:8080/file'])
def test_no_untrusted_hosts_or_plaintext(url):
    with pytest.raises(ValueError): cw.validate_url(url)


def test_untrusted_redirect_blocked():
    with pytest.raises(ValueError):
        cw.SafeRedirect().redirect_request(None, None, 302, '', {}, 'http://evil.example/file')


def test_download_then_verified_cache_reuse_no_second_network(tmp_path):
    server = Server(Response())
    wheel = cw.cached_wheel(ENTRY, tmp_path, opener=server, delays=())
    assert wheel.read_bytes() == DATA
    assert not part(tmp_path).exists()
    wheel2 = cw.cached_wheel(ENTRY, tmp_path, opener=server, delays=())
    assert wheel2 == wheel and len(server.calls) == 1


def test_partial_resume_uses_range_and_full_hash(tmp_path):
    offset = 70
    part(tmp_path).write_bytes(DATA[:offset])
    server = Server(Response(DATA[offset:], 206, {'Content-Range': f'bytes {offset}-{len(DATA)-1}/{len(DATA)}'}))
    assert cw.cached_wheel(ENTRY, tmp_path, opener=server, delays=()).read_bytes() == DATA
    assert server.calls[0][1] == {'Range': f'bytes={offset}-'}


def test_interrupted_transfer_resumes_without_losing_first_bytes(tmp_path):
    offset = 85
    server = Server(Response(DATA[:offset], headers={'Content-Length': str(len(DATA))}),
                    Response(DATA[offset:], 206, {'Content-Range': f'bytes {offset}-{len(DATA)-1}/{len(DATA)}'}))
    assert cw.cached_wheel(ENTRY, tmp_path, opener=server, delays=(0,)).read_bytes() == DATA
    assert server.calls[1][1]['Range'] == f'bytes={offset}-'


def test_server_ignores_range_restarts_instead_of_appending(tmp_path):
    part(tmp_path).write_bytes(DATA[:90])
    server = Server(Response())
    assert cw.cached_wheel(ENTRY, tmp_path, opener=server, delays=()).read_bytes() == DATA


def test_already_complete_partial_is_promoted_without_network(tmp_path):
    part(tmp_path).write_bytes(DATA)
    server = Server()
    assert cw.cached_wheel(ENTRY, tmp_path, opener=server, delays=()).read_bytes() == DATA
    assert not server.calls


@pytest.mark.parametrize('content_range', ['bytes 0-10/20', 'bad', 'bytes 20-0/100', 'bytes 20-100/100'])
def test_invalid_range_does_not_modify_partial(tmp_path, content_range):
    partial = part(tmp_path)
    partial.write_bytes(DATA[:20])
    server = Server(Response(DATA[20:], 206, {'Content-Range': content_range}))
    with pytest.raises(cw.IntegrityError):
        cw.cached_wheel(ENTRY, tmp_path, opener=server, delays=())
    assert partial.read_bytes() == DATA[:20]


def test_hash_mismatch_never_publishes_wheel(tmp_path):
    server = Server(Response(b'corrupt'))
    with pytest.raises(cw.IntegrityError, match='SHA256'):
        cw.cached_wheel(ENTRY, tmp_path, opener=server, delays=())
    assert not list(tmp_path.rglob('*.whl'))
    assert not list(tmp_path.rglob('*.partial'))


def test_corrupt_cached_wheel_is_not_trusted(tmp_path):
    target = part(tmp_path).with_suffix('')
    target.write_bytes(b'tampered')
    server = Server(Response())
    assert cw.cached_wheel(ENTRY, tmp_path, opener=server, delays=()).read_bytes() == DATA
    assert len(server.calls) == 1


def test_http416_discards_stale_partial_and_restarts(tmp_path):
    part(tmp_path).write_bytes(DATA + b'stale')
    server = Server(HTTPError(URL, 416, 'range', {}, None), Response())
    assert cw.cached_wheel(ENTRY, tmp_path, opener=server, delays=(0,)).read_bytes() == DATA
    assert server.calls[1][1] == {}


def test_persistent_network_error_is_bounded_and_keeps_partial(tmp_path):
    part(tmp_path).write_bytes(DATA[:30])
    server = Server(URLError('down'), URLError('down'), URLError('down'))
    with pytest.raises(URLError):
        cw.cached_wheel(ENTRY, tmp_path, opener=server, delays=(0, 0))
    assert len(server.calls) == 3 and part(tmp_path).read_bytes() == DATA[:30]


def test_tls_verification_failure_is_not_retried_or_bypassed(tmp_path):
    server = Server(URLError(ssl.SSLCertVerificationError('bad certificate')))
    with pytest.raises(URLError):
        cw.cached_wheel(ENTRY, tmp_path, opener=server, delays=(0,0,0))
    assert len(server.calls) == 1


def test_http404_is_not_retried(tmp_path):
    server = Server(HTTPError(URL, 404, 'missing', {}, None))
    with pytest.raises(HTTPError): cw.cached_wheel(ENTRY, tmp_path, opener=server, delays=(0,0))
    assert len(server.calls) == 1


def test_cancel_keeps_partial_and_never_publishes(tmp_path):
    partial = part(tmp_path)
    partial.write_bytes(DATA[:25])
    cancel = tmp_path / 'cancel'
    cancel.touch()
    server = Server()
    with pytest.raises(cw.Cancelled):
        cw.cached_wheel(ENTRY, tmp_path, cancel=cancel, opener=server, delays=())
    assert not server.calls and partial.read_bytes() == DATA[:25]


@pytest.mark.parametrize('code', [32,33])
def test_lock_retry_is_bounded_and_recovers(code):
    calls=[]
    def operation():
        calls.append(1)
        if len(calls)<3: raise locked(code)
        return 'ok'
    assert cw.retry_lock(operation, delays=(0,0,0)) == 'ok'
    assert len(calls) == 3


def test_access_denied_is_not_mistaken_for_temporary_lock():
    calls=[]
    def operation():
        calls.append(1)
        raise locked(5)
    with pytest.raises(PermissionError): cw.retry_lock(operation, delays=(0,0,0))
    assert len(calls) == 1


def test_lock_exhaustion_raises_not_success():
    calls=[]
    def operation():
        calls.append(1)
        raise locked()
    with pytest.raises(PermissionError): cw.retry_lock(operation, delays=(0,0))
    assert len(calls) == 3


def test_busy_rename_does_not_redownload_complete_wheel(tmp_path, monkeypatch):
    original = cw.os.replace
    calls=[]
    def rename(a,b):
        calls.append((a,b))
        if len(calls) == 1: raise locked()
        return original(a,b)
    monkeypatch.setattr(cw.os, 'replace', rename)
    monkeypatch.setattr(cw, 'pause', lambda *_:None)
    server=Server(Response())
    assert cw.cached_wheel(ENTRY, tmp_path, opener=server, delays=()).read_bytes() == DATA
    assert len(calls) == 2 and len(server.calls) == 1


def test_digest_path_cannot_traverse_cache(tmp_path):
    with pytest.raises(cw.IntegrityError):
        cw.cached_wheel({**ENTRY,'sha256':'../escape'}, tmp_path)
    with pytest.raises(cw.IntegrityError):
        cw.cached_wheel({**ENTRY,'filename':'../escape.whl'}, tmp_path)


def test_installer_uses_local_wheel_and_preserves_cache():
    source=(ROOT / 'packaging/windows/setup-worker.ps1').read_text(encoding='utf-8-sig')
    assert "'torch==2.9.1','--report'" not in source
    assert "($pipBase+@([string]$torch.path" in source
    assert "'cache_wheel.py'" in source
    assert "'downloads\\wheels'" in source
    assert 'Remove-Item -LiteralPath $wheelCache' not in source
    assert 'Remove-Item -LiteralPath $Data' not in source
    assert "--force-reinstall" in source
    assert "WinError (32|33)" in source
    assert '$env:TEMP=$oldTemp;$env:TMP=$oldTmp' in source
    assert "'--user'" not in source
    assert source.index("'-m','pip','--isolated','check'") < source.index("'activate','--root',$Root")


def test_helper_included_in_installer_payload():
    source=(ROOT / 'packaging/windows/build_release.py').read_text()
    assert "'cache_wheel.py'" in source
