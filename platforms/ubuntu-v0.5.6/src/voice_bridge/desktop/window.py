from __future__ import annotations

import importlib.util
from copy import deepcopy
import logging
from pathlib import Path
import platform
import threading

from PySide6.QtCore import QObject, QTimer, Signal, Slot, Qt, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMenu, QMessageBox, QSystemTrayIcon

from .. import __version__
from ..audio import list_audio_devices
from ..config import Config, load_config
from ..models import (CATALOG, ModelDownloader, DownloadCancelled, DownloadProgress,
                      ModelError, validate_local_model, human_size)
from ..platform_paths import log_dir
from . import autostart
from .runtime import DesktopRuntime, RuntimeEvent
from .settings_store import save_config
from .settings_policy import change_kind
from .layout import build_ui
from .theme import brand_icon

LOG = logging.getLogger(__name__)


class BridgeSignals(QObject):
    runtime_event = Signal(int, object)
    runtime_stopped = Signal(int, str)
    download_progress = Signal(object)
    download_finished = Signal(object, str, bool)


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config, cfg_path: Path, *, diagnostics=False, start_engine=True):
        super().__init__()
        self.cfg = cfg; self.cfg_path = cfg_path
        self.runtime: DesktopRuntime | None = None
        self.signals = BridgeSignals(self)
        self.signals.runtime_event.connect(self._receive_runtime_event, Qt.QueuedConnection)
        self.signals.runtime_stopped.connect(self._runtime_stopped, Qt.QueuedConnection)
        self.signals.download_progress.connect(self._download_update, Qt.QueuedConnection)
        self.signals.download_finished.connect(self._download_done, Qt.QueuedConnection)
        self._last_state = ''; self._allow_close = False
        self._selftest_expected = None; self._external_test = False
        self._test_generation = 0; self._runtime_generation = 0
        self._download_cancel = threading.Event(); self._download_thread = None
        self._download_running = False; self._model_ready = False
        self._model_enabled = False; self._start_engine = start_engine
        self._model_loading = False
        self._operation_busy = False
        self._test_active = False
        self._test_dispatched = False
        self._delivery_ready = False
        self._config_pending = False
        self._pending_config = None
        self._applied_cfg = deepcopy(cfg)
        self._restart_pending = False
        self._stop_thread = None
        self._runtime_model_requested = False
        self._next_start_reason = 'application_start'
        self._resume_enabled = True
        self._initial_problem = '' 
        if cfg.asr.model.strip():
            try:
                validate_local_model(cfg.asr.model)
                self._model_enabled = not diagnostics and cfg.app.start_model_on_launch
            except ModelError as exc:
                self._initial_problem = str(exc)
        self.setWindowTitle(f'Voice Bridge {__version__}')
        self.setMinimumSize(820, 580)
        screen = QApplication.primaryScreen()
        area = screen.availableGeometry() if screen else None
        self.resize(min(1140, area.width()-40) if area else 1140,
                    min(850, area.height()-60) if area else 850)
        build_ui(self)
        self._build_tray()
        self._load_form()
        if diagnostics:
            self._navigate(3)
        if self._initial_problem:
            self.active_model_status.setText(self._initial_problem)
        if start_engine:
            QTimer.singleShot(50, self.start_runtime)
        else:
            self._refresh_home()

    def _navigate(self, index):
        self.pages.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)
        self.page_title.setText(('Диктовка', 'Модели', 'Настройки', 'Проверка вставки')[index])

    def _build_tray(self):
        icon = brand_icon()
        self.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip('Voice Bridge')
        menu = QMenu(self)
        show = QAction('Открыть Voice Bridge', self)
        self.enable_action = QAction('Диктовка включена', self)
        self.enable_action.setCheckable(True); self.enable_action.setEnabled(False)
        quit_action = QAction('Выйти', self)
        menu.addAction(show); menu.addAction(self.enable_action); menu.addSeparator(); menu.addAction(quit_action)
        show.triggered.connect(self._show_window)
        self.enable_action.toggled.connect(self._set_enabled)
        quit_action.triggered.connect(self._quit)
        self.tray.activated.connect(lambda reason: self._show_window()
                                   if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.setContextMenu(menu)
        if self._start_engine and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _populate_devices(self):
        current = self.mic.currentData() if self.mic.count() else self.cfg.audio.input_device
        self.mic.clear(); self.mic.addItem('Системный микрофон', '')
        try:
            for dev in list_audio_devices():
                name = dev['name']
                stable = f'{name}, {dev.get("hostapi_name", "")}' if platform.system() == 'Windows' else name
                display = f'{name} · {dev.get("hostapi_name", "")}' if platform.system() == 'Windows' else name
                self.mic.addItem(display, stable)
        except Exception as exc:
            LOG.warning('Cannot enumerate microphones: %s', exc)
        matched = False
        for i in range(self.mic.count()):
            value = self.mic.itemData(i)
            if current not in (None, '') and (value == current or str(current) in str(value)):
                self.mic.setCurrentIndex(i); matched = True; break
        if current not in (None, '') and not matched:
            self.mic.addItem(f'Сохранённый микрофон: {current}', current); self.mic.setCurrentIndex(self.mic.count()-1)
        current_mouse = self.mouse_device.currentData() if self.mouse_device.count() else self.cfg.trigger.evdev_device_match
        self.mouse_device.clear(); self.mouse_device.addItem('Определить автоматически', '')
        if platform.system() == 'Linux':
            try:
                from ..triggers.evdev_trigger import EvdevHoldTrigger
                for device_path, name in EvdevHoldTrigger.devices(mouse_only=True):
                    self.mouse_device.addItem(f'{name} · {device_path}', name)
            except Exception as exc:
                LOG.warning('Cannot enumerate evdev devices: %s', exc)
        if current_mouse:
            i = self.mouse_device.findData(current_mouse)
            if i < 0:
                self.mouse_device.addItem(f'Сохранённое устройство: {current_mouse}', current_mouse); i = self.mouse_device.count()-1
            self.mouse_device.setCurrentIndex(i)

    def _load_form(self):
        self.model.setText(self.cfg.asr.model)
        self._set_combo(self.placement, self.cfg.asr.placement)
        self._set_combo(self.mode, self.cfg.app.mode)
        self._set_combo(self.button, self.cfg.trigger.key if self.cfg.trigger.kind == 'keyboard' else self.cfg.trigger.button)
        self.language.setText(self.cfg.app.language or '')
        self.consume.setChecked(self.cfg.trigger.consume)
        self.beep_cb.setChecked(self.cfg.app.beep)
        self.auto_model_cb.setChecked(self.cfg.app.start_model_on_launch)
        self.reserve.setText(str(self.cfg.asr.gpu_reserve_gb))
        self.cpu_ram.setText(str(self.cfg.asr.cpu_max_memory_gb))
        self.prompt.setText(self.cfg.asr.prompt)
        self.vocabulary_file.setText(self.cfg.text.vocabulary_file)
        self.autostart_cb.setChecked(autostart.is_enabled())
        self._set_combo(self.keyboard_backend, self.cfg.injector.keyboard_backend)
        self._set_combo(self.paste_combo, self.cfg.injector.paste_shortcut)
        self._set_combo(self.target_policy, self.cfg.app.paste_target if platform.system() == 'Windows' else 'active')
        if self.cfg.asr.model and Path(self.cfg.asr.model).is_dir():
            self.source_local.setChecked(True)
        elif self.cfg.asr.model in {s.repo for s in CATALOG}:
            self._set_combo(self.catalog_choice, self.cfg.asr.model)
        if self.cfg.app.models_directory:
            self.download_root.setText(self.cfg.app.models_directory)
        self._set_combo(self.catalog_choice, self.cfg.app.download_repo)
        self._refresh_home()

    @staticmethod
    def _set_combo(combo, value):
        i = combo.findData(value)
        if i >= 0:
            combo.setCurrentIndex(i)

    def _busy(self):
        return (self._model_loading or self._operation_busy or self._test_active or
                self._config_pending or self._restart_pending or
                self._last_state in {'starting', 'loading', 'recording', 'processing', 'injecting', 'stopping'})

    def _busy_message(self):
        return ('Завершите загрузку модели или текущую операцию. '
                'Для отмены используйте «Отменить». Настройки не изменены.')

    def _refresh_home(self):
        chosen = bool(self.cfg.asr.model and Path(self.cfg.asr.model).is_dir())
        self.model_summary.setText(Path(self.cfg.asr.model).name if chosen else 'Не выбрана')
        self.active_model_path.setText(self.cfg.asr.model if chosen else 'Пока не выбрана')
        self.mic_summary.setText(str(self.cfg.audio.input_device or 'Системный микрофон'))
        key = self.cfg.trigger.key.upper() if self.cfg.trigger.kind == 'keyboard' else self.cfg.trigger.button.upper()
        self.key_summary.setText(f'Запись: удерживайте {key} · отпустите для распознавания')
        descriptions = {'active': 'Вставка: в активное поле после распознавания (как CLI).',
                        'window': 'Вставка: только в окно, где начата запись.',
                        'strict': 'Вставка: только при сохранении исходного окна и Win32-фокуса.'}
        self.target_summary.setText(descriptions[self.cfg.app.paste_target] if platform.system() == 'Windows'
                                    else 'Вставка: в активное поле после распознавания. Во время вставки не переключайте окно.')
        self.home_action.setText('Перейти к диктовке' if self._model_ready else 'Включить диктовку' if chosen else 'Выбрать модель')
        self.home_action.setEnabled(not self._busy())
        self.load_model_button.setEnabled(chosen and not self._model_ready and not self._busy())
        self.toggle.setEnabled(self._model_ready)
        self.enable_action.setEnabled(self._model_ready)
        self.save_settings.setEnabled(not self._busy())
        self.use_local.setEnabled(not self._busy() and not self._download_running)
        self.cancel_operation.setVisible(self._model_loading or self._operation_busy or self._test_active or self._restart_pending)
        self.cancel_operation.setEnabled(not self._restart_pending)
        self.cancel_operation.setText('Отменить загрузку' if self._model_loading else 'Отменить')
        self.toggle.setText('Приостановить' if not self.runtime or self.runtime.enabled else 'Возобновить')
        self._test_controls(not self._busy())
        self.enable_action.blockSignals(True)
        self.enable_action.setChecked(self._model_ready and bool(self.runtime and self.runtime.enabled))
        self.enable_action.blockSignals(False)
        if self._model_ready:
            self.hero_title.setText('Говорите.\nТекст появится в нужном поле.')
            self.hero_hint.setText(f'Нажмите «Перейти к диктовке», щёлкните нужное текстовое поле и удерживайте {key}. '
                                   'Отпустите кнопку, когда закончите фразу.')
        else:
            self.hero_title.setText('Ваш голос.\nБез лишних действий.')
            self.hero_hint.setText('Выберите модель и микрофон, проверьте вставку. '
                                   'Речь распознаётся локально; запись начинается только при удержании кнопки.')

    def _home_action(self):
        if not self.cfg.asr.model or not Path(self.cfg.asr.model).is_dir():
            self._navigate(1)
        elif not self._model_ready:
            self._load_model_now()
        else:
            if self.runtime and not self.runtime.enabled:
                self._set_enabled(True)
            self._hide_to_tray()

    def _browse_model(self):
        directory = QFileDialog.getExistingDirectory(self, 'Папка HF-native Qwen3-ASR', self.model.text() or str(Path.home()))
        if directory:
            self.model.setText(directory)
            self.local_status.setText('Нажмите «Проверить и выбрать», чтобы подключить эту папку.')

    def _use_local(self):
        try:
            self._select_model(self.model.text())
        except Exception as exc:
            self.local_status.setText(str(exc))
            LOG.warning('Local model selection failed: %s', exc)

    def _select_model(self, path):
        if self._busy():
            raise ModelError(self._busy_message() + ' Скачанные файлы сохранены.')
        result = validate_local_model(path)
        selected = str(result.path)
        if selected == self.cfg.asr.model:
            self.local_status.setText('Эта модель уже выбрана. Повторная загрузка не требуется.')
            return
        old = self.cfg.asr.model
        self.cfg.asr.model = selected
        try:
            save_config(self.cfg, self.cfg_path)
        except Exception:
            self.cfg.asr.model = old
            raise
        self.model.setText(selected)
        self.cfg = load_config(self.cfg_path)
        self._model_enabled = False
        self.local_status.setText(f'Структура модели проверена · веса {human_size(result.weights_bytes)}. '
                                  'Нажмите «Включить диктовку», чтобы загрузить её в память.')
        self.active_model_status.setText('Модель выбрана. Нажмите «Включить диктовку».')
        if self._start_engine:
            if self.runtime and self.runtime.running and self._runtime_model_requested:
                self._request_restart('model_selection', load_model=False)
            elif self.runtime and self.runtime.running:
                # Diagnostics process holds no model: it reads the selected
                # profile when Load is requested. No stop/start is needed.
                self._applied_cfg = deepcopy(self.cfg)
            else:
                self.start_runtime()
        self._model_ready = False
        self._refresh_home()

    def _browse_download(self):
        directory = QFileDialog.getExistingDirectory(self, 'Куда сохранять модели', self.download_root.text())
        if directory:
            self.download_root.setText(directory)

    def _open_model_page(self):
        # The URL is selected from a fixed catalog, never from downloaded metadata.
        repo = self.catalog_choice.currentData()
        if repo in {s.repo for s in CATALOG}:
            QDesktopServices.openUrl(QUrl(f'https://huggingface.co/{repo}'))

    def _download_model(self):
        if self._download_running:
            return
        root = self.download_root.text().strip()
        if not root:
            self.download_status.setText('Выберите папку для моделей.'); return
        repo = self.catalog_choice.currentData()
        try:
            self.cfg.app.models_directory = str(Path(root).expanduser().resolve())
            self.cfg.app.download_repo = repo
            save_config(self.cfg, self.cfg_path)
        except Exception as exc:
            self.download_status.setText(f'Не удалось сохранить папку загрузки: {exc}'); return
        self._download_cancel.clear(); self._download_running = True
        self.download_button.setEnabled(False); self.cancel_download.setEnabled(True)
        for widget in (self.catalog_choice, self.download_root, self.browse_download, self.use_local,
                       self.source_local, self.source_download):
            widget.setEnabled(False)
        self.download_status.setText('Подготавливаем загрузку…')
        self.download_progress.setRange(0, 0)
        def run():
            try:
                path = ModelDownloader(cancel=self._download_cancel,
                    progress=self.signals.download_progress.emit).download(repo, root)
                self.signals.download_finished.emit(path, '', False)
            except DownloadCancelled as exc:
                self.signals.download_finished.emit(None, str(exc), True)
            except Exception as exc:
                LOG.exception('Model download failed')
                message = str(exc) if isinstance(exc, ModelError) else 'Не удалось завершить загрузку. '
                if not isinstance(exc, ModelError):
                    message += 'Проверьте подключение, свободное место и права на папку. Подробности в журнале.'
                self.signals.download_finished.emit(None, message, False)
        self._download_thread = threading.Thread(target=run, name='voice-bridge-download', daemon=True)
        self._download_thread.start()

    def _cancel_download(self):
        self._download_cancel.set()
        self.cancel_download.setEnabled(False)
        self.download_status.setText('Приостанавливаем… Полученные части останутся на диске.')

    @Slot(object)
    def _download_update(self, update: DownloadProgress):
        if not self._download_running:
            return
        names = {'metadata': 'Получаем сведения о модели…', 'checking': 'Проверяем сохранённые файлы…',
                 'downloading': 'Скачиваем модель…', 'verifying': 'Проверяем контрольную сумму…',
                 'retry': 'Восстанавливаем загрузку…', 'complete': 'Модель скачана и проверена.'}
        if update.total:
            self.download_progress.setRange(0, 1000)
            self.download_progress.setValue(min(1000, int(update.completed / update.total * 1000)))
            percent = update.completed / update.total * 100
            speed = f' · {human_size(update.bytes_per_second)}/с' if update.bytes_per_second else ''
            self.download_status.setText(f'{names.get(update.phase, update.phase)} '
                                         f'{percent:.0f}% · {human_size(update.completed)} / {human_size(update.total)}{speed}')
        else:
            self.download_status.setText(names.get(update.phase, update.phase))
        self.download_detail.setText(update.name)

    @Slot(object, str, bool)
    def _download_done(self, path, error, cancelled):
        self._download_running = False
        self.download_progress.setRange(0, 1000)
        self.download_button.setEnabled(True); self.cancel_download.setEnabled(False)
        for widget in (self.catalog_choice, self.download_root, self.browse_download, self.use_local,
                       self.source_local, self.source_download):
            widget.setEnabled(True)
        self.download_button.setText('Проверить скачанную модель' if path else 'Продолжить загрузку')
        if path:
            self.download_progress.setValue(1000)
            self.download_status.setText('Модель скачана и проверена. Все файлы находятся на вашем компьютере.')
            self.model.setText(str(path))
            try:
                self._select_model(path)
            except Exception as exc:
                self.download_status.setText(f'Модель скачана. {exc} Подключите её через «У меня уже есть модель».')
        else:
            self.download_status.setText(error)
        self._refresh_home()

    def _browse_vocabulary(self):
        path, _ = QFileDialog.getOpenFileName(
            self, 'Выберите словарь замен (UTF-8)', '', 'Текстовые файлы (*.txt);;Все файлы (*)')
        if path:
            self.vocabulary_file.setText(path)

    def _apply_form(self):
        # Model paths are committed only by _select_model, never from an unvalidated text field.
        value = self.mic.currentData()
        self.cfg.audio.input_device = None if value in ('', None) else value
        self.cfg.asr.placement = self.placement.currentData()
        self.cfg.app.mode = self.mode.currentData()
        self.cfg.app.language = self.language.text().strip() or None
        self.cfg.app.beep = self.beep_cb.isChecked()
        self.cfg.app.start_model_on_launch = self.auto_model_cb.isChecked()
        selected = self.button.currentData()
        self.cfg.trigger.kind = 'keyboard' if str(selected).startswith('f') else 'mouse'
        if self.cfg.trigger.kind == 'keyboard': self.cfg.trigger.key = selected
        else: self.cfg.trigger.button = selected
        self.cfg.trigger.consume = self.consume.isChecked()
        if platform.system() == 'Windows':
            self.cfg.trigger.backend = 'windows_hook'; self.cfg.injector.backend = 'windows'
        elif platform.system() == 'Linux':
            self.cfg.trigger.backend = 'evdev'; self.cfg.injector.backend = 'linux'
            self.cfg.trigger.evdev_device_match = self.mouse_device.currentData() or ''
            self.cfg.trigger.evdev_code = 'BTN_SIDE' if self.cfg.trigger.button == 'x1' else 'BTN_EXTRA'
        try:
            self.cfg.asr.gpu_reserve_gb = float(self.reserve.text().replace(',', '.'))
            self.cfg.asr.cpu_max_memory_gb = float(self.cpu_ram.text().replace(',', '.'))
        except ValueError as exc:
            raise ValueError('Резерв видеопамяти и лимит RAM должны быть числами.') from exc
        self.cfg.asr.prompt = self.prompt.text().strip()
        self.cfg.text.vocabulary_file = self.vocabulary_file.text().strip()
        self.cfg.injector.keyboard_backend = self.keyboard_backend.currentData()
        self.cfg.injector.paste_shortcut = self.paste_combo.currentData()
        self.cfg.app.paste_target = self.target_policy.currentData()

    def _save_and_restart(self):
        # Public handler name retained for compatibility; it no longer restarts
        # on Save. Only actual ASR/model changes require releasing the process.
        if self._busy():
            self.footer.setText(self._busy_message())
            return
        old = deepcopy(self.cfg)
        try:
            self._apply_form()
            save_config(self.cfg, self.cfg_path)
            self.cfg = load_config(self.cfg_path)
        except Exception as exc:
            self.cfg = old
            QMessageBox.critical(self, 'Настройки не применены', str(exc))
            return
        try:
            autostart.set_enabled(self.autostart_cb.isChecked())
        except Exception as exc:
            QMessageBox.warning(self, 'Автозапуск не изменён',
                f'Остальные настройки сохранены. Не удалось изменить автозапуск: {exc}')
        kind = change_kind(self._applied_cfg, self.cfg)
        LOG.info('Settings saved change=%s model_requested=%s', kind, self._runtime_model_requested)
        if not self._start_engine:
            self._applied_cfg = deepcopy(self.cfg)
            self.footer.setText('Настройки сохранены.')
        elif not self.runtime or not self.runtime.running:
            self.start_runtime()
        elif kind == 'model' and self._runtime_model_requested:
            self._request_restart('model_parameters_changed', load_model=self._model_enabled)
        elif kind != 'none':
            self._config_pending = True
            self._pending_config = deepcopy(self.cfg)
            try:
                self.runtime.configure()
                self.footer.setText('Применяем настройки. Модель остаётся в памяти…')
            except Exception as exc:
                self._config_pending = False
                self.footer.setText(f'Настройки сохранены, но не применены: {exc}')
        else:
            self._applied_cfg = deepcopy(self.cfg)
            self.footer.setText('Настройки сохранены. Перезапуск не нужен.')
        self._refresh_home()

    def start_runtime(self):
        if self._restart_pending or (self.runtime and self.runtime.running):
            LOG.info('Duplicate UI start ignored')
            return
        self.vocabulary_warning.clear(); self.vocabulary_warning.hide()
        self._test_generation += 1; self._runtime_generation += 1
        generation = self._runtime_generation
        self._selftest_expected = None
        self._test_active = False
        self._test_dispatched = False
        self._delivery_ready = False
        self._model_ready = False
        self._model_loading = self._model_enabled
        self._runtime_model_requested = self._model_enabled
        self._last_state = 'starting'
        self._applied_cfg = deepcopy(self.cfg)
        self.runtime = DesktopRuntime(self.cfg,
            lambda event: self.signals.runtime_event.emit(generation, event),
            diagnostics_only=not self._model_enabled)
        self.runtime.enabled = self._resume_enabled
        self.runtime.gui_active = QApplication.applicationState() == Qt.ApplicationActive
        self.status.setText('Подготовка распознавания…' if self._model_enabled else 'Подготовка механизма вставки…')
        self._refresh_home()
        try:
            self.runtime.start(reason=self._next_start_reason)
        except Exception as exc:
            self._model_enabled = False; self._model_loading = False
            self._on_runtime_event(RuntimeEvent('error', str(exc)))
            LOG.exception('Cannot start worker')

    def _request_restart(self, reason, *, load_model):
        """Only explicit cancellation/model changes enter here. Never block Qt."""
        if self._restart_pending:
            return
        self._model_enabled = bool(load_model)
        self._model_ready = False
        self._model_loading = False
        self._operation_busy = False
        self._test_active = False
        self._config_pending = False
        self._delivery_ready = False
        self._test_generation += 1
        self._runtime_generation += 1  # reject every queued event from old child
        generation = self._runtime_generation
        self._restart_pending = True
        self._last_state = 'stopping'
        self._next_start_reason = reason
        old_runtime = self.runtime
        self._resume_enabled = bool(old_runtime.enabled) if old_runtime else True
        self.status.setText('Останавливаем прежний движок…')
        self.status_badge.setText('Остановка')
        self._refresh_home()
        LOG.info('UI requested worker replacement reason=%s load_model=%s', reason, load_model)
        def stop():
            error = ''
            try:
                if old_runtime:
                    old_runtime.stop(reason=reason)
            except Exception as exc:
                LOG.exception('Cannot stop previous worker')
                error = str(exc)
            self.signals.runtime_stopped.emit(generation, error)
        self._stop_thread = threading.Thread(target=stop, daemon=True, name='voice-bridge-stop')
        self._stop_thread.start()

    @Slot(int, str)
    def _runtime_stopped(self, generation, error):
        if generation != self._runtime_generation or self._allow_close:
            return
        self._restart_pending = False
        self._last_state = 'error' if error else 'stopped'
        if error:
            self.status.setText('Прежний процесс не остановился. Новый не запущен: ' + error)
            self._model_enabled = False
            self._refresh_home()
            return
        self.runtime = None
        try:
            self.cfg = load_config(self.cfg_path)
        except Exception as exc:
            self._model_enabled = False
            self._last_state = 'error'
            self.status.setText('Не удалось прочитать настройки: ' + str(exc))
            LOG.exception('Cannot read profile after explicit worker stop')
            self._refresh_home()
            return
        if self._start_engine:
            self.start_runtime()
        else:
            self._refresh_home()

    @Slot(int, object)
    def _receive_runtime_event(self, generation, event):
        if generation == self._runtime_generation and not self._allow_close:
            self._on_runtime_event(event)

    def _publish_focus(self, *_):
        if self.runtime:
            try:
                self.runtime.set_gui_active(QApplication.applicationState() == Qt.ApplicationActive)
            except RuntimeError:
                pass

    def _load_model_now(self):
        if self._busy() or self._model_ready:
            self.footer.setText(self._busy_message() if self._busy() else 'Модель уже загружена.')
            return
        previous = deepcopy(self.cfg)
        try:
            validate_local_model(self.cfg.asr.model)
            missing = [name for name in ('torch', 'transformers', 'accelerate', 'librosa')
                       if importlib.util.find_spec(name) is None]
            if missing:
                raise ModelError('Не установлены библиотеки распознавания. Запустите установщик и выберите '
                                 'NVIDIA или CPU. Модель повторно скачивать не нужно.')
            self._apply_form()
            save_config(self.cfg, self.cfg_path)
            self.cfg = load_config(self.cfg_path)
        except Exception as exc:
            self.cfg = previous
            self.active_model_status.setText(str(exc)); self.status.setText(str(exc))
            self._navigate(1); self._refresh_home()
            return
        self._model_enabled = True
        self._model_loading = True  # reserve synchronously before the worker ACK
        self._last_state = 'loading'
        self.active_model_status.setText('Загружаем модель в память…')
        self.status.setText('Подготовка библиотек и загрузка модели. Окно можно свернуть.')
        self.status_badge.setText('Загрузка модели')
        try:
            if self._start_engine:
                if self.runtime and self.runtime.running:
                    if change_kind(self._applied_cfg, self.cfg) == 'model' and self._runtime_model_requested:
                        self._request_restart('model_parameters_changed', load_model=True)
                    else:
                        self._runtime_model_requested = True
                        self._applied_cfg = deepcopy(self.cfg)
                        self.runtime.load_model()  # same PID, no diagnostic worker replacement
                else:
                    self._next_start_reason = 'explicit_load'
                    self.start_runtime()
        except Exception as exc:
            self._model_enabled = False; self._model_loading = False; self._last_state = 'error'
            self.active_model_status.setText(str(exc)); self.status.setText(str(exc))
            LOG.exception('Model command failed')
        self._refresh_home()

    def _apply_delivery(self):
        if self._busy():
            self.test_status.setText(self._busy_message())
            return
        old = deepcopy(self.cfg)
        try:
            self.cfg.injector.keyboard_backend = self.keyboard_backend.currentData()
            self.cfg.injector.paste_shortcut = self.paste_combo.currentData()
            self.cfg.app.paste_target = self.target_policy.currentData()
            save_config(self.cfg, self.cfg_path)
        except Exception as exc:
            self.cfg = old
            self.test_status.setText(str(exc))
            return
        try:
            if not self.runtime or not self.runtime.running:
                raise RuntimeError('Движок не запущен.')
            if (self._applied_cfg.injector == self.cfg.injector and
                    self._applied_cfg.app.paste_target == self.cfg.app.paste_target):
                self.test_status.setText('Этот способ вставки уже выбран.')
                return
            self._pending_config = deepcopy(self._applied_cfg)
            self._pending_config.injector = deepcopy(self.cfg.injector)
            self._pending_config.app.paste_target = self.cfg.app.paste_target
            self._config_pending = True
            self.runtime.configure_delivery()
            self.delivery_info.setText('Применение… Модель остаётся в памяти.')
        except Exception as exc:
            self._config_pending = False
            self.test_status.setText(str(exc))
        self._refresh_home()

    def _cancel_operation(self):
        if self._model_loading:
            self._request_restart('user_cancel_model_load', load_model=False)
        elif self._test_active and not self._test_dispatched:
            self._test_generation += 1
            self._selftest_expected = None
            self._test_active = False
            self.test_status.setText('Проверка отменена до отправки команды.')
            self._refresh_home()
        elif self.runtime and not self._restart_pending:
            try:
                self.runtime.cancel()
                self.footer.setText('Отмена запрошена. Результат распознавания не будет вставлен.')
            except Exception as exc:
                self.footer.setText(str(exc))

    def _test_controls(self, enabled):
        allowed = enabled and self._delivery_ready and not self._busy()
        self.test_internal.setEnabled(allowed)
        self.test_external.setEnabled(allowed)
        self.apply_delivery.setEnabled(allowed)

    def _send_test(self, text, *, internal=False, delay=0):
        try:
            if not self.runtime: raise RuntimeError('Движок не запущен.')
            self._test_dispatched = True
            self.runtime.paste_test(text, internal=internal, delay=delay)
        except Exception as exc:
            self._test_active = False
            self.test_status.setText(str(exc)); self._selftest_expected = None; self._refresh_home()

    def _test_here(self):
        if self._busy() or not self._delivery_ready:
            self.test_status.setText(self._busy_message())
            return
        self._test_active = True
        self._test_dispatched = False
        self._external_test = False; self._selftest_expected = 'Voice Bridge — проверка вставки 123.'
        self._refresh_home()
        self.test_editor.clear(); self.test_editor.setFocus(Qt.OtherFocusReason)
        self.test_status.setText('Не переключайте окно и не печатайте в тестовом поле.')
        expected = self._selftest_expected; generation = self._test_generation
        QTimer.singleShot(400, lambda: self._send_test(expected, internal=True)
                          if self._test_generation == generation else None)

    def _test_elsewhere(self):
        self._external_paste('Voice Bridge — проверка вставки 123.')

    def _copy_last(self):
        if self.last_text.toPlainText():
            QApplication.clipboard().setText(self.last_text.toPlainText())
            self.footer.setText('Последний текст скопирован в буфер обмена.')

    def _retry_last(self):
        if self.last_text.toPlainText():
            self._external_paste(self.last_text.toPlainText())

    def _external_paste(self, text):
        if self._busy() or not self._delivery_ready:
            self.footer.setText(self._busy_message())
            return
        self._test_active = True
        self._selftest_expected = None; self._external_test = True
        self._refresh_home()
        self.test_status.setText('Перейдите в нужное поле: вставка через 5 секунд.')
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
        else:
            self.showMinimized()
        self._send_test(text, delay=5)

    def _verify_test_field(self, attempts=12):
        expected = self._selftest_expected
        if expected is None: return
        actual = self.test_editor.toPlainText()
        if actual == expected:
            self.test_status.setText('ПОДТВЕРЖДЕНО: текст действительно появился в поле.')
            self.paste_summary.setText('Внутренний тест пройден')
            LOG.info('Self-test VERIFIED: received expected %d characters in Qt field', len(expected))
            self._selftest_expected = None; self._test_active = False; self._refresh_home()
        elif attempts:
            QTimer.singleShot(150, lambda: self._verify_test_field(attempts-1))
        else:
            self.test_status.setText('НЕ ПОДТВЕРЖДЕНО: команда отправлена, но текста в поле нет. '
                                    'Проверьте механизм и сочетание вставки.')
            self._selftest_expected = None; self._test_active = False; self._refresh_home()

    def _on_runtime_event(self, event):
        if event.state == 'delivery_error':
            self._delivery_ready = False
            self._model_loading = False
            self._model_enabled = False
            self._model_ready = False
            self._last_state = 'error'
            self.status.setText('Не удалось подготовить вставку: ' + event.detail)
            self.status_badge.setText('Ошибка вставки')
            self.delivery_info.setText(event.detail)
            self._refresh_home()
            return
        if event.state == 'warning':
            self.vocabulary_warning.setText(event.detail); self.vocabulary_warning.show()
            LOG.warning('Desktop engine warning: %s', event.detail)
            return
        if event.state in {'config_applied', 'config_error'}:
            self._config_pending = False
            if event.state == 'config_applied' and self._pending_config is not None:
                self._applied_cfg = self._pending_config
                self.delivery_info.setText('Способ вставки: ' + self.cfg.injector.paste_shortcut)
            self._pending_config = None
            self.footer.setText(event.detail)
            self._refresh_home()
            return
        if event.state == 'operation_idle':
            self._operation_busy = False
            if self._last_state in {'recording', 'processing', 'injecting'}:
                self._last_state = 'ready'
            self._refresh_home()
            return
        if event.state == 'model_stage':
            self.active_model_status.setText(event.detail)
            if self._model_loading:
                self.status.setText(event.detail)
            return
        if event.state == 'load_rejected':
            self._model_loading = False; self._model_enabled = self._model_ready
            self._last_state = 'error'
            self.status.setText(event.detail)
            self._refresh_home()
            return
        if event.state == 'model_error':
            self._model_enabled = False; self._model_ready = False; self._model_loading = False
            self.active_model_status.setText(event.detail)
            event = RuntimeEvent('error', event.detail)
        if event.state == 'transcript':
            self.last_text.setPlainText(event.detail)
            return
        if event.state == 'delivery_deferred':
            self.status.setText(event.detail)
            self.status_badge.setText('Текст готов — выберите поле')
            self.status_badge.setProperty('state', 'ready')
            self.status_badge.style().unpolish(self.status_badge)
            self.status_badge.style().polish(self.status_badge)
            self.tray.setToolTip('Voice Bridge — текст готов, вставка отложена')
            self._last_state = 'delivery_deferred'
            self._refresh_home()
            return
        if event.state == 'delivery_ready':
            self._delivery_ready = True
            self.delivery_info.setText(event.detail)
            self.test_status.setText('Механизм готов. Можно проверить вставку без модели.')
            if not self._model_enabled:
                self._last_state = 'delivery_ready'
                self.status.setText('Выберите модель и проверьте вставку — затем включите диктовку.')
                self.status_badge.setText('Настройка')
            self._refresh_home()
            return
        if event.state == 'test_pending':
            return
        if event.state == 'test_sent':
            if self._selftest_expected is not None:
                self._verify_test_field()
            else:
                self._test_active = False
                self.test_status.setText('Команда отправлена. Проверьте появление текста в другом приложении.')
                self._refresh_home()
            return
        if event.state == 'test_error':
            self.test_status.setText('Ошибка проверки: ' + event.detail)
            self._selftest_expected = None; self._test_active = False
            self._refresh_home()
            return
        if event.state == 'loading':
            self._model_loading = True
        if event.state in {'recording', 'processing', 'injecting'}:
            self._operation_busy = True
        names = {'loading': 'Загрузка модели', 'ready': 'Готово к диктовке', 'recording': 'Запись',
                 'processing': 'Распознавание', 'disabled': 'Приостановлено', 'error': 'Ошибка',
                 'injecting': 'Отправка текста', 'paste_requested': 'Готово к диктовке', 'cancelled': 'Отменено'}
        title = names.get(event.state, 'Состояние движка')
        detail = {'loading': 'Загружаем выбранную модель в память. Окно можно свернуть.',
                  'ready': 'Щёлкните нужное текстовое поле и удерживайте кнопку записи.',
                  'recording': 'Говорите. Отпустите кнопку, чтобы завершить запись.',
                  'processing': 'Распознаём записанную фразу локально.',
                  'paste_requested': 'Команда вставки отправлена. Последний текст сохранён ниже.',
                  'disabled': 'Кнопка записи отключена. Нажмите «Возобновить», чтобы продолжить.',
                  'cancelled': 'Вставка отменена. Если распознавание ещё идёт, дождитесь его завершения.',
                  'injecting': 'Отправляем текст в выбранное приложение.'}.get(event.state, event.detail)
        self.status.setText(detail)
        state = 'ready' if event.state in {'ready', 'paste_requested'} else event.state
        self.status_badge.setText(title); self.status_badge.setProperty('state', state)
        self.status_badge.style().unpolish(self.status_badge); self.status_badge.style().polish(self.status_badge)
        self.tray.setToolTip(f'Voice Bridge — {title}')
        if event.state in {'ready', 'disabled'}:
            self._model_loading = False
            self._model_ready = self._runtime_model_requested
            if self._model_ready:
                self._model_enabled = True
                self.active_model_status.setText('Модель загружена. Распознавание готово.')
        if event.state == 'error':
            LOG.error('Desktop engine: %s', event.detail)
            if not self.runtime or not self.runtime.running:
                self._model_enabled = False; self._model_ready = False; self._model_loading = False
                self._operation_busy = False; self._test_active = False; self._config_pending = False
                self._delivery_ready = False
        if self.cfg.app.beep and event.state in {'recording', 'paste_requested'}:
            QApplication.beep()
        self._last_state = event.state
        self._refresh_home()

    def _toggle_runtime(self):
        if self.runtime:
            self._set_enabled(not self.runtime.enabled)

    def _set_enabled(self, enabled):
        if self.runtime and self._model_ready:
            try:
                self.runtime.set_enabled(enabled)
            except RuntimeError as exc:
                self.status.setText(str(exc))
        self.toggle.setText('Приостановить' if enabled else 'Возобновить')
        self._refresh_home()

    def _hide_to_tray(self):
        if QSystemTrayIcon.isSystemTrayAvailable(): self.hide()
        else: self.showMinimized()

    def _show_window(self):
        self.show(); self.raise_(); self.activateWindow()

    def _open_log(self):
        path = log_dir() / 'voice-bridge.log'; path.touch(exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_log_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir())))

    def _quit(self):
        if self._download_running:
            result = QMessageBox.question(self, 'Загрузка модели',
                'Приостановить загрузку и выйти? Полученные части останутся на диске.',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if result != QMessageBox.Yes: return
            self._download_cancel.set()
        self._allow_close = True
        QApplication.quit()

    def shutdown(self):
        self._allow_close = True
        self._runtime_generation += 1
        self._test_generation += 1
        self._download_cancel.set()
        if self.runtime:
            self.runtime.stop(reason='application_exit')
        self.tray.hide()

    def closeEvent(self, event: QCloseEvent):
        if self._allow_close:
            event.accept()
        else:
            event.ignore(); self._hide_to_tray()
