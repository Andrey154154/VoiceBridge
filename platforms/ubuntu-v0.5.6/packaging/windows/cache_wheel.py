"""Persistent PyTorch wheel transfer, standard library only.

The only entry points select pinned filenames on official HTTPS indexes. The
publisher's SHA256 is resolved from that index and checked before every use.
An interruption never hands a .partial file to pip. No TLS/security bypasses.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import socket
import ssl
import sys
import time
from typing import Callable, TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urldefrag, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ALLOWED_HOSTS = frozenset({'download.pytorch.org', 'download-r2.pytorch.org'})
PROFILES = {'cuda': 'cu128', 'cpu': 'cpu'}
TORCH_VERSION = '2.9.1'
CHUNK = 1024 * 1024
MAX_INDEX = 4 * 1024 * 1024
RETRY_DELAYS = (3.0, 8.0, 20.0)
T = TypeVar('T')


class Cancelled(RuntimeError):
    pass


class IntegrityError(RuntimeError):
    pass


class TransferError(RuntimeError):
    pass


def log(message: str) -> None:
    print(message, flush=True)


def check_cancel(cancel: Path | None) -> None:
    if cancel is not None and cancel.exists():
        raise Cancelled('Установка отменена. Частичная загрузка сохранена для следующей попытки.')


def pause(seconds: float, cancel: Path | None) -> None:
    end = time.monotonic() + seconds
    while True:
        check_cancel(cancel)
        remaining = end - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.2, remaining))


def retry_lock(operation: Callable[[], T], cancel: Path | None = None,
               delays: tuple[float, ...] = RETRY_DELAYS) -> T:
    """Only Win32 sharing/lock violations are transient; never hide ACL errors."""
    for attempt in range(len(delays) + 1):
        check_cancel(cancel)
        try:
            return operation()
        except OSError as exc:
            if getattr(exc, 'winerror', None) not in (32, 33) or attempt == len(delays):
                raise
            log(f'Файл временно занят (WinError {exc.winerror}); повтор через {delays[attempt]:g} с.')
            pause(delays[attempt], cancel)
    raise AssertionError('unreachable')


def validate_url(url: str) -> str:
    value = urlsplit(url)
    if (value.scheme != 'https' or value.hostname not in ALLOWED_HOSTS
            or value.username or value.password or value.port not in (None, 443)):
        raise ValueError('Допустимы только официальные HTTPS-серверы PyTorch.')
    return url


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(url: str, headers: dict[str, str] | None = None):
    validate_url(url)
    request = Request(url, headers={'User-Agent': 'VoiceBridge-Setup/0.5.0',
                                   'Accept-Encoding': 'identity', **(headers or {})})
    # Default verified TLS context; redirects cannot escape the official hosts.
    return build_opener(SafeRedirect()).open(request, timeout=30)


class Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            href = dict(attrs).get('href')
            if href:
                self.hrefs.append(href)


def parse_index(html: str, index: str, filename: str) -> dict[str, str]:
    parser = Links()
    parser.feed(html)
    matches = set()
    for href in parser.hrefs:
        url = urljoin(index, href)
        if unquote(urlsplit(url).path.rsplit('/', 1)[-1]) != filename:
            continue
        validate_url(url)
        clean, fragment = urldefrag(url)
        digest = parse_qs(fragment).get('sha256', [])
        if len(digest) != 1 or re.fullmatch('[0-9a-fA-F]{64}', digest[0]) is None:
            raise IntegrityError('В официальном индексе нет однозначного SHA256 выбранного wheel.')
        matches.add((clean, digest[0].lower()))
    if len(matches) != 1:
        raise IntegrityError(f'Не найден единственный wheel {filename} с SHA256.')
    url, digest = matches.pop()
    return {'filename': filename, 'url': url, 'sha256': digest, 'index': index}


def transient(exc: BaseException) -> bool:
    if isinstance(exc, HTTPError):
        return exc.code in (408, 429, 500, 502, 503, 504)
    if isinstance(exc, URLError):
        return not isinstance(exc.reason, (ssl.SSLCertVerificationError, ssl.CertificateError))
    return isinstance(exc, (TimeoutError, ConnectionError, http.client.IncompleteRead,
                            TransferError, socket.gaierror))


def resolve(profile: str, cancel: Path | None = None,
            opener: Callable = open_url, delays=RETRY_DELAYS) -> dict[str, str]:
    build = PROFILES[profile]
    filename = f'torch-{TORCH_VERSION}+{build}-cp313-cp313-win_amd64.whl'
    index = f'https://download.pytorch.org/whl/{build}/torch/'
    for attempt in range(len(delays) + 1):
        check_cancel(cancel)
        try:
            log('Проверка версии и SHA256 по официальному индексу PyTorch…')
            with opener(index) as response:
                data = response.read(MAX_INDEX + 1)
            if len(data) > MAX_INDEX:
                raise IntegrityError('Индекс PyTorch превышает допустимый размер.')
            return parse_index(data.decode('utf-8'), index, filename)
        except Exception as exc:
            if not transient(exc) or attempt == len(delays):
                raise
            log(f'Ошибка сети: {exc}; повтор получения метаданных.')
            pause(delays[attempt], cancel)
    raise AssertionError('unreachable')


def sha256(path: Path, cancel: Path | None = None) -> str:
    def calculate() -> str:
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            while block := stream.read(CHUNK):
                check_cancel(cancel)
                digest.update(block)
        return digest.hexdigest()
    return retry_lock(calculate, cancel)


def remove(path: Path, cancel: Path | None = None) -> None:
    retry_lock(lambda: path.unlink(missing_ok=True), cancel)


def download_once(url: str, partial: Path, cancel: Path | None, opener: Callable) -> None:
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {'Range': f'bytes={offset}-'} if offset else {}
    if offset:
        log(f'Продолжение сохранённой загрузки с {offset / 1024**2:.1f} МиБ.')
    with opener(url, headers) as response:
        status = response.status
        if status == 206:
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',
                                 response.headers.get('Content-Range', '').strip())
            if (match is None or int(match[1]) != offset
                    or int(match[2]) < offset or int(match[3]) <= int(match[2])):
                raise IntegrityError('Сервер вернул неверный Content-Range; файл не изменён.')
            end, total = int(match[2]), int(match[3])
            mode = 'ab' if offset else 'wb'
            expected = end - offset + 1
        elif status == 200:
            if offset:
                log('Сервер не поддержал продолжение: загрузка будет начата заново.')
            offset = 0
            mode = 'wb'
            size = response.headers.get('Content-Length')
            total = expected = int(size) if size is not None else None
        else:
            raise TransferError(f'Неподходящий HTTP-ответ: {status}')
        if response.headers.get('Content-Encoding', 'identity') not in ('', 'identity'):
            raise IntegrityError('Неожиданное HTTP-сжатие wheel; установка остановлена.')
        stream = retry_lock(lambda: partial.open(mode), cancel)
        received = 0
        last_log = 0.0
        with stream:
            while True:
                check_cancel(cancel)
                block = response.read(CHUNK)
                if not block:
                    break
                received += len(block)
                if expected is not None and received > expected:
                    raise IntegrityError('Получено больше данных, чем указано сервером.')
                stream.write(block)
                if time.monotonic() - last_log >= 2:
                    done = offset + received
                    suffix = f' / {total / 1024**2:.1f} МиБ ({100 * done / total:.1f}%)' if total else ' МиБ'
                    log(f'PyTorch: {done / 1024**2:.1f}' + suffix)
                    last_log = time.monotonic()
            stream.flush()
            os.fsync(stream.fileno())
        if expected is not None and received != expected:
            raise TransferError('Загрузка оборвалась: сохранённые байты будут использованы при повторе.')
        if status == 206 and offset + received < total:
            raise TransferError('Сервер вернул часть диапазона; продолжение загрузки.')


def cached_wheel(entry: dict[str, str], cache: Path, cancel: Path | None = None,
                 opener: Callable = open_url, delays=RETRY_DELAYS) -> Path:
    # Content-addressed directory prevents CPU/CUDA/version collisions.
    if re.fullmatch('[0-9a-f]{64}', entry['sha256']) is None:
        raise IntegrityError('Некорректный SHA256 wheel.')
    if not re.fullmatch(r'torch-[A-Za-z0-9+_.-]+\.whl', entry['filename']):
        raise IntegrityError('Некорректное имя wheel.')
    validate_url(entry['url'])
    folder = cache / entry['sha256']
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / entry['filename']
    partial = target.with_suffix(target.suffix + '.partial')
    if target.exists():
        log('Проверка SHA256 ранее загруженного wheel…')
        if sha256(target, cancel) == entry['sha256']:
            log('КЭШ ПОДТВЕРЖДЁН: повторная загрузка PyTorch не требуется.')
            return target
        log('Кэш повреждён: неверный SHA256, файл не будет установлен.')
        remove(target, cancel)
    # The previous run may have finished downloading but been cancelled while hashing.
    if partial.exists() and sha256(partial, cancel) == entry['sha256']:
        log('Частичный файл уже полный; SHA256 подтверждён.')
    else:
        for attempt in range(len(delays) + 1):
            check_cancel(cancel)
            try:
                download_once(entry['url'], partial, cancel, opener)
                log('Загрузка завершена. Проверка SHA256…')
                if sha256(partial, cancel) != entry['sha256']:
                    remove(partial, cancel)
                    raise IntegrityError('SHA256 PyTorch не совпал. Непроверенный wheel не будет установлен.')
                break
            except HTTPError as exc:
                if exc.code == 416:
                    # A stale/oversized partial cannot be appended safely.
                    remove(partial, cancel)
                elif not transient(exc):
                    raise
                if attempt == len(delays):
                    raise
                log(f'Ошибка HTTP {exc.code}; сохранённая загрузка будет повторена.')
                pause(delays[attempt], cancel)
            except Exception as exc:
                # A sharing violation must not discard or redownload a complete file.
                if not transient(exc) or attempt == len(delays):
                    raise
                log(f'Загрузка прервана: {exc}; повтор с сохранённых данных.')
                pause(delays[attempt], cancel)
    retry_lock(lambda: os.replace(partial, target), cancel)
    log('SHA256 ПОДТВЕРЖДЁН: ' + entry['sha256'])
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=sorted(PROFILES), required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--result', type=Path, required=True)
    parser.add_argument('--cancel', type=Path)
    args = parser.parse_args()
    try:
        entry = resolve(args.profile, args.cancel)
        wheel = cached_wheel(entry, args.cache, args.cancel)
        check_cancel(args.cancel)
        # Written only after a successful digest check. Parent must also check exit status.
        temp = args.result.with_suffix('.tmp')
        temp.write_text(json.dumps({**entry, 'path': str(wheel)}, ensure_ascii=True), encoding='utf-8')
        retry_lock(lambda: os.replace(temp, args.result), args.cancel)
        return 0
    except Exception as exc:
        log('ОШИБКА загрузки PyTorch: ' + str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
