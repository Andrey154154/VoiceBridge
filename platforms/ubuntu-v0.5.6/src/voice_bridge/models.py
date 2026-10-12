"""Explicit, resumable, verified downloads of the two supported Qwen checkpoints.

No Torch, Qt, Hub cache, access token, remote code or network activity on import.
A managed model is published by a directory rename only after every digest and
its local layout have been checked. External user models are read-only.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import struct
import threading
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, HTTPRedirectHandler, build_opener

from .instance import UserInstanceLock
from .platform_paths import data_dir


@dataclass(frozen=True)
class ModelSpec:
    repo: str
    label: str
    description: str
    approximate_gb: float

    @property
    def folder(self) -> str:
        return self.repo.split('/')[1]


CATALOG = (
    ModelSpec('Qwen/Qwen3-ASR-1.7B-hf', 'Qwen3-ASR · 1.7B',
              'Основной вариант. Более крупная модель для повседневной диктовки.', 4.1),
    ModelSpec('Qwen/Qwen3-ASR-0.6B-hf', 'Qwen3-ASR · 0.6B',
              'Компактный вариант. Меньше загрузка и потребление памяти.', 1.6),
)
_ALLOWED = {'config.json', 'generation_config.json', 'processor_config.json',
            'preprocessor_config.json', 'tokenizer.json', 'tokenizer_config.json',
            'special_tokens_map.json', 'added_tokens.json', 'vocab.json', 'merges.txt',
            'chat_template.jinja', 'chat_template.json', 'model.safetensors.index.json',
            'README.md', 'LICENSE', 'LICENSE.txt', 'NOTICE'}
_REVISION = re.compile(r'^[a-f0-9]{40}$')
_WEIGHTS = re.compile(r'^model(?:-\d{5}-of-\d{5})?\.safetensors$')
_GIB = 1024 ** 3
_JSON_LIMIT = 8 * 1024 ** 2


class ModelError(RuntimeError):
    pass


class DownloadCancelled(ModelError):
    pass


@dataclass(frozen=True)
class LocalModel:
    path: Path
    weights_bytes: int
    shards: int
    managed: bool


@dataclass(frozen=True)
class ModelFile:
    name: str
    size: int
    digest: str
    algorithm: str


@dataclass(frozen=True)
class ModelManifest:
    repo: str
    revision: str
    files: tuple[ModelFile, ...]

    @property
    def total(self) -> int:
        return sum(f.size for f in self.files)


@dataclass(frozen=True)
class DownloadProgress:
    phase: str
    name: str = ''
    completed: int = 0
    total: int = 0
    bytes_per_second: float = 0.0


def default_model_dir() -> Path:
    """Stable sibling of versions/ on a managed Windows install, not inside venv."""
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
        return base / 'Programs' / 'VoiceBridge' / 'models'
    return data_dir() / 'models'


def validate_download_root(root: str | Path) -> Path:
    """Never place downloaded weights inside disposable application versions."""
    path = Path(root).expanduser().resolve()
    persistent = default_model_dir().resolve()
    application = persistent.parent
    if (path == application or application in path.parents) and not (
            path == persistent or persistent in path.parents):
        raise ModelError('Внутри папки программы сохраняйте модели в models, а не в versions или runtime. '
                         'Либо выберите отдельную папку на другом диске.')
    return path


def human_size(size: int | float) -> str:
    value = float(size)
    for unit in ('Б', 'КиБ', 'МиБ', 'ГиБ', 'ТиБ'):
        if value < 1024 or unit == 'ТиБ':
            return f'{value:.1f} {unit}' if unit != 'Б' else f'{int(value)} Б'
        value /= 1024
    return str(size)


def _json_file(path: Path, limit: int = _JSON_LIMIT) -> dict:
    try:
        if path.stat().st_size > limit:
            raise ModelError(f'Слишком большой служебный файл: {path.name}')
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('expected object')
        return value
    except (OSError, ValueError) as exc:
        raise ModelError(f'Не удалось прочитать {path.name}. Проверьте папку модели.') from exc


def _check_safetensors(path: Path) -> None:
    try:
        size = path.stat().st_size
        with path.open('rb') as stream:
            raw = stream.read(8)
            if len(raw) != 8:
                raise ValueError('short header')
            header_len = struct.unpack('<Q', raw)[0]
            if not 2 <= header_len <= 16 * 1024 ** 2 or header_len + 8 >= size:
                raise ValueError('invalid header size')
            header = json.loads(stream.read(header_len))
        if not isinstance(header, dict):
            raise ValueError('invalid header')
        tensors = [v for k, v in header.items() if k != '__metadata__']
        if not tensors:
            raise ValueError('no tensors')
        data_len = size - header_len - 8
        spans = []
        for tensor in tensors:
            offsets = tensor.get('data_offsets') if isinstance(tensor, dict) else None
            if (not isinstance(offsets, list) or len(offsets) != 2
                    or any(type(v) is not int for v in offsets)
                    or not 0 <= offsets[0] <= offsets[1] <= data_len):
                raise ValueError('truncated tensor data')
            spans.append(tuple(offsets))
        # Full tensor semantics are checked by the ASR runtime; this catches
        # LFS pointer files, truncation and malformed offsets without loading GB.
        if max(end for _, end in spans) != data_len:
            raise ValueError('unexpected data length')
    except (OSError, ValueError, struct.error, TypeError) as exc:
        raise ModelError(f'Файл весов повреждён или скачан не полностью: {path.name}') from exc


def validate_local_model(folder: str | Path) -> LocalModel:
    """Bounded structural check; no network, no writes, no model/GPU allocation."""
    if not str(folder).strip():
        raise ModelError('Выберите папку модели или скачайте модель во вкладке «Модели».')
    path = Path(folder).expanduser().resolve()
    if not path.is_dir():
        raise ModelError('Папка модели не найдена. Выберите существующую папку на этом компьютере.')
    if not (path / 'config.json').is_file():
        raise ModelError('В папке нет config.json. Выберите папку самой модели, а не общий каталог.')
    config = _json_file(path / 'config.json')
    if (config.get('model_type') != 'qwen3_asr' or not isinstance(config.get('audio_config'), dict)
            or not isinstance(config.get('text_config'), dict)):
        raise ModelError('Нужна HF-native версия Qwen3-ASR (-hf). Эта папка имеет другой формат. '
                         'Рабочую локальную модель менять не нужно; поддерживаемую можно скачать здесь.')
    for name in ('tokenizer_config.json',):
        _json_file(path / name)
    if not (path / 'tokenizer.json').is_file():
        if not all((path / n).is_file() for n in ('vocab.json', 'merges.txt')):
            raise ModelError('В модели отсутствует tokenizer.json или пара vocab.json / merges.txt.')
    elif (path / 'tokenizer.json').stat().st_size < 2:
        raise ModelError('Пустой файл tokenizer.json.')
    processor = next((path / n for n in ('processor_config.json', 'preprocessor_config.json')
                      if (path / n).is_file()), None)
    if processor is None:
        raise ModelError('В модели отсутствует конфигурация аудиопроцессора.')
    _json_file(processor)
    index = path / 'model.safetensors.index.json'
    if index.is_file():
        mapping = _json_file(index).get('weight_map')
        if not isinstance(mapping, dict) or not mapping or not all(isinstance(x, str) for x in mapping.values()):
            raise ModelError('Некорректный индекс весов модели.')
        names = sorted(set(mapping.values()))
        if any(not _WEIGHTS.fullmatch(n) for n in names):
            raise ModelError('Недопустимый путь к части модели в индексе весов.')
    else:
        names = ['model.safetensors']
    for name in names:
        _check_safetensors(path / name)
    return LocalModel(path, sum((path / n).stat().st_size for n in names), len(names),
                      (path / '.voicebridge-complete.json').is_file())


def _allowed_name(name: str) -> bool:
    return name in _ALLOWED or bool(_WEIGHTS.fullmatch(name))


def parse_manifest(raw: dict, repo: str) -> ModelManifest:
    if repo not in {s.repo for s in CATALOG}:
        raise ModelError('Скачивание разрешено только для моделей из каталога Voice Bridge.')
    if not isinstance(raw, dict) or raw.get('id') != repo or not _REVISION.fullmatch(str(raw.get('sha', ''))):
        raise ModelError('Сервер вернул некорректные сведения о версии модели.')
    siblings = raw.get('siblings')
    if not isinstance(siblings, list) or len(siblings) > 1000:
        raise ModelError('Некорректный список файлов модели.')
    files = []
    for entry in siblings:
        if not isinstance(entry, dict):
            raise ModelError('Некорректная запись файла модели.')
        name = entry.get('rfilename', '')
        if not isinstance(name, str) or not _allowed_name(name):
            continue  # scripts, pickle, other weights/formats are never fetched
        lfs = entry.get('lfs') or {}
        algorithm = 'sha256' if lfs else 'git-sha1'
        digest = lfs.get('sha256') if lfs else entry.get('blobId')
        size = lfs.get('size', entry.get('size'))
        if (type(size) is not int or not 0 < size <= 24 * _GIB or not isinstance(digest, str)
                or not re.fullmatch(r'[a-f0-9]{64}' if lfs else r'[a-f0-9]{40}', digest)):
            raise ModelError(f'Не получены размер или контрольная сумма файла {name}. Загрузка остановлена.')
        files.append(ModelFile(name, size, digest, algorithm))
    names = {f.name for f in files}
    if (len(names) != len(files) or not {'config.json', 'tokenizer_config.json'} <= names
            or not ({'processor_config.json', 'preprocessor_config.json'} & names)
            or not ('tokenizer.json' in names or {'vocab.json', 'merges.txt'} <= names)
            or not any(_WEIGHTS.fullmatch(n) for n in names)
            or sum(f.size for f in files) > 30 * _GIB):
        raise ModelError('Неполный или некорректный набор файлов модели на сервере.')
    return ModelManifest(repo, raw['sha'], tuple(sorted(files, key=lambda f: (_WEIGHTS.fullmatch(f.name) is not None, f.name))))


class _HTTPSRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if parsed.scheme != 'https' or parsed.username or parsed.password:
            raise ModelError('Небезопасный адрес переадресации. Загрузка остановлена.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _atomic_json(path: Path, data: dict) -> None:
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


class ModelDownloader:
    def __init__(self, *, cancel: threading.Event | None = None,
                 progress: Callable[[DownloadProgress], None] = lambda _: None,
                 opener=None, attempts: int = 3):
        self.cancel = cancel or threading.Event()
        self.progress = progress
        self.opener = opener or build_opener(_HTTPSRedirect())
        self.attempts = attempts
        self._last_progress = 0.0

    def _check_cancel(self):
        if self.cancel.is_set():
            raise DownloadCancelled('Загрузка приостановлена. Уже полученные файлы сохранены для продолжения.')

    def _open(self, url: str, headers: dict | None = None):
        self._check_cancel()
        if urlparse(url).scheme != 'https':
            raise ModelError('Для скачивания требуется HTTPS.')
        req = Request(url, headers={'User-Agent': 'VoiceBridge/0.5.4', 'Accept-Encoding': 'identity', **(headers or {})})
        return self.opener.open(req, timeout=10)

    def manifest(self, repo: str) -> tuple[ModelManifest, dict]:
        if repo not in {s.repo for s in CATALOG}:
            raise ModelError('Неизвестная модель.')
        self.progress(DownloadProgress('metadata', 'Получаем список файлов'))
        url = f'https://huggingface.co/api/models/{repo}/revision/main?blobs=true'
        with self._open(url) as response:
            payload = response.read(_JSON_LIMIT + 1)
        self._check_cancel()
        if len(payload) > _JSON_LIMIT:
            raise ModelError('Ответ сервера слишком большой.')
        try:
            raw = json.loads(payload)
        except ValueError as exc:
            raise ModelError('Сервер вернул не JSON. Проверьте подключение к Hugging Face.') from exc
        return parse_manifest(raw, repo), raw

    def _verify(self, path: Path, file: ModelFile) -> bool:
        self._check_cancel()
        if not path.is_file() or path.stat().st_size != file.size:
            return False
        digest = hashlib.sha256() if file.algorithm == 'sha256' else hashlib.sha1()
        if file.algorithm == 'git-sha1':
            digest.update(f'blob {file.size}\0'.encode('ascii'))
        with path.open('rb') as stream:
            while chunk := stream.read(1024 ** 2):
                self._check_cancel()
                digest.update(chunk)
        return digest.hexdigest() == file.digest

    def _rename(self, source: Path, destination: Path):
        for attempt in range(4):
            self._check_cancel()
            try:
                os.replace(source, destination)
                return
            except OSError as exc:
                if getattr(exc, 'winerror', None) not in (32, 33) or attempt == 3:
                    raise
                self.progress(DownloadProgress('retry', 'Файл временно занят Windows. Повторяем…'))
                self.cancel.wait(attempt + 1)

    def _download_file(self, folder: Path, file: ModelFile, manifest: ModelManifest, completed: int):
        destination = folder / file.name
        partial = folder / (file.name + '.partial')
        if destination.is_symlink() or partial.is_symlink():
            raise ModelError('Символические ссылки в управляемой загрузке не допускаются.')
        self.progress(DownloadProgress('checking', file.name, completed, manifest.total))
        if self._verify(destination, file):
            return
        if destination.exists():
            destination.unlink()
        url = f'https://huggingface.co/{manifest.repo}/resolve/{manifest.revision}/{quote(file.name)}?download=true'
        for attempt in range(self.attempts):
            self._check_cancel()
            try:
                offset = partial.stat().st_size if partial.is_file() else 0
                if offset > file.size:
                    partial.unlink(); offset = 0
                if offset == file.size:
                    if self._verify(partial, file):
                        self._rename(partial, destination); return
                    partial.unlink(); offset = 0
                headers = {'Range': f'bytes={offset}-'} if offset else {}
                with self._open(url, headers) as response:
                    status = response.status
                    if status == 206:
                        match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                        if (not match or int(match[1]) != offset or int(match[3]) != file.size
                                or int(match[2]) < offset or int(match[2]) >= file.size):
                            raise ModelError('Сервер вернул неверный диапазон для докачки.')
                    elif status == 200:
                        offset = 0  # server ignored Range: rewrite, never append duplicate bytes
                    else:
                        raise ModelError(f'Неожиданный ответ сервера: HTTP {status}')
                    received = offset
                    start = time.monotonic()
                    with partial.open('ab' if offset else 'wb') as stream:
                        while True:
                            self._check_cancel()
                            chunk = response.read(512 * 1024)
                            if not chunk:
                                break
                            if received + len(chunk) > file.size:
                                raise ModelError('Размер файла превышает сведения сервера.')
                            stream.write(chunk); received += len(chunk)
                            now = time.monotonic()
                            if now - self._last_progress >= .15:
                                self.progress(DownloadProgress('downloading', file.name, completed + received,
                                    manifest.total, (received - offset) / max(.001, now - start)))
                                self._last_progress = now
                        stream.flush(); os.fsync(stream.fileno())
                if received != file.size:
                    raise ConnectionError('Соединение прервалось до конца файла.')
                self.progress(DownloadProgress('verifying', file.name, completed + received, manifest.total))
                if not self._verify(partial, file):
                    partial.unlink()
                    raise ModelError(f'Контрольная сумма {file.name} не совпала. Нажмите «Продолжить», чтобы скачать заново.')
                self._rename(partial, destination)
                return
            except (HTTPError, URLError, ConnectionError, TimeoutError, socket.timeout, http.client.IncompleteRead) as exc:
                transient = not isinstance(exc, HTTPError) or exc.code in (408, 429, 500, 502, 503, 504)
                if isinstance(exc, URLError) and isinstance(exc.reason, ssl.SSLCertVerificationError):
                    transient = False
                if not transient or attempt == self.attempts - 1:
                    raise ModelError('Не удалось скачать модель. Проверьте доступ к Hugging Face и нажмите '
                                     '«Продолжить». Полученная часть сохранена.') from exc
                self.progress(DownloadProgress('retry', f'Связь прервалась. Повтор {attempt + 2}/{self.attempts}', completed, manifest.total))
                self.cancel.wait(attempt + 1)

    def download(self, repo: str, root: str | Path) -> Path:
        spec = next((s for s in CATALOG if s.repo == repo), None)
        if spec is None:
            raise ModelError('Неизвестная модель.')
        root = validate_download_root(root)
        root.mkdir(parents=True, exist_ok=True)
        cache = root / '.downloads'
        if cache.is_symlink():
            raise ModelError('Каталог загрузок не должен быть символической ссылкой.')
        cache.mkdir(exist_ok=True)
        final = root / spec.folder
        stage = cache / spec.folder
        if final.is_symlink() or stage.is_symlink():
            raise ModelError('Каталог модели не должен быть символической ссылкой.')
        with UserInstanceLock(spec.folder, directory=cache):
            if final.exists():
                marker = final / '.voicebridge-complete.json'
                if not marker.is_file():
                    raise ModelError('Папка уже существует и не принадлежит загрузчику. '
                                     'Подключите её как локальную или выберите другое место; файлы не изменены.')
                raw = _json_file(final / '.voicebridge-manifest.json')
                manifest = parse_manifest(raw, repo)
                completed = 0
                for f in manifest.files:
                    self.progress(DownloadProgress('verifying', f.name, completed, manifest.total))
                    if not self._verify(final / f.name, f):
                        raise ModelError('Ранее скачанная модель повреждена. Выберите другую папку для новой загрузки.')
                    completed += f.size
                validate_local_model(final)
                self.progress(DownloadProgress('complete', str(final), completed, manifest.total))
                return final
            stage.mkdir(exist_ok=True)
            manifest_path = stage / '.voicebridge-manifest.json'
            if manifest_path.is_file():
                raw = _json_file(manifest_path)
                manifest = parse_manifest(raw, repo)
            else:
                manifest, raw = self.manifest(repo)
                self._check_cancel()
                _atomic_json(manifest_path, raw)
            # Don't silently follow a local symlink in a managed directory.
            if any(p.is_symlink() for p in stage.iterdir()):
                raise ModelError('В каталоге загрузки обнаружена символическая ссылка.')
            received = sum(min((stage / (f.name + '.partial')).stat().st_size, f.size)
                           if (stage / (f.name + '.partial')).is_file() else
                           min((stage / f.name).stat().st_size, f.size) if (stage / f.name).is_file() else 0
                           for f in manifest.files)
            if shutil.disk_usage(root).free < manifest.total - received + 256 * 1024 ** 2:
                raise ModelError(f'Недостаточно места. Для продолжения нужно около '
                                 f'{human_size(manifest.total - received + 256 * 1024 ** 2)}.')
            completed = 0
            for file in manifest.files:
                self._download_file(stage, file, manifest, completed)
                completed += file.size
            self._check_cancel()
            validate_local_model(stage)
            _atomic_json(stage / '.voicebridge-complete.json', {
                'repo': repo, 'revision': manifest.revision, 'bytes': manifest.total,
                'verified_at': time.time(), 'version': '0.5.4',
            })
            self._check_cancel()
            self._rename(stage, final)
            self.progress(DownloadProgress('complete', str(final), manifest.total, manifest.total))
            return final
