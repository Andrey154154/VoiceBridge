"""Desktop composition; no audio, model loads or network calls in this module."""
import platform
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QFormLayout, QFrame,
    QPushButton, QLineEdit, QComboBox, QCheckBox, QPlainTextEdit, QStackedWidget,
    QScrollArea, QButtonGroup, QRadioButton, QProgressBar, QSizePolicy)
from .. import __version__
from ..models import CATALOG, default_model_dir
from .theme import label, card, brand_icon, ElideLabel


def button(text, callback, primary=False, role=''):
    item = QPushButton(text)
    if primary or role:
        item.setObjectName('primary' if primary else role)
    item.setCursor(Qt.PointingHandCursor)
    item.clicked.connect(callback)
    return item


def row(*widgets):
    w = QWidget(); layout = QHBoxLayout(w)
    layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(10)
    for widget in widgets:
        layout.addWidget(widget)
    return w


def form_layout():
    form = QFormLayout()
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.WrapLongRows)
    form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    form.setHorizontalSpacing(20); form.setVerticalSpacing(12)
    return form


def build_ui(w):
    root = QWidget(); root.setObjectName('canvas')
    shell = QHBoxLayout(root); shell.setContentsMargins(0, 0, 0, 0); shell.setSpacing(0)
    side = QWidget(); side.setObjectName('sidebar'); side.setFixedWidth(206)
    sidebar = QVBoxLayout(side); sidebar.setContentsMargins(18, 26, 18, 18); sidebar.setSpacing(10)
    logo = label('', wrap=False); logo.setPixmap(brand_icon(50).pixmap(50, 50)); sidebar.addWidget(logo)
    sidebar.addWidget(label('Voice Bridge', 'brand', False))
    sidebar.addWidget(label('ГОЛОС → ТЕКСТ', 'eyebrow', False)); sidebar.addSpacing(30)
    w.nav_group = QButtonGroup(w); w.nav_group.setExclusive(True); w.nav_buttons = []
    w.pages = QStackedWidget()
    for i, title in enumerate(('Диктовка', 'Модели', 'Настройки', 'Проверка вставки')):
        nav = button(title, lambda checked=False, index=i: w._navigate(index), role='nav')
        nav.setCheckable(True); w.nav_group.addButton(nav, i); sidebar.addWidget(nav); w.nav_buttons.append(nav)
    sidebar.addStretch()
    sidebar.addWidget(label('Распознавание\nна вашем компьютере', 'small'))
    sidebar.addSpacing(10)
    sidebar.addWidget(label(f'Версия {__version__}', 'small', False))
    sidebar.addWidget(button('Открыть журнал', w._open_log, role='quiet'))
    shell.addWidget(side)
    content = QWidget(); content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(28, 22, 28, 16); content_layout.setSpacing(14)
    top = QHBoxLayout()
    w.page_title = label('Диктовка', 'pageTitle'); top.addWidget(w.page_title, 1)
    w.status_badge = label('Подготовка', 'badge', False); top.addWidget(w.status_badge, 0, Qt.AlignTop)
    content_layout.addLayout(top)
    w.status = label('Подготавливаем механизм вставки…', 'muted')
    w.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
    content_layout.addWidget(w.status)
    w.vocabulary_warning = label("", "small")
    w.vocabulary_warning.setTextInteractionFlags(Qt.TextSelectableByMouse)
    w.vocabulary_warning.hide()
    content_layout.addWidget(w.vocabulary_warning)
    content_layout.addWidget(w.pages, 1)
    bottom = QHBoxLayout()
    w.footer = ElideLabel('Микрофон включается только при удержании выбранной кнопки.'); w.footer.setObjectName('small')
    bottom.addWidget(w.footer, 1)
    bottom.addWidget(button('Свернуть в трей', w._hide_to_tray, role='quiet'))
    content_layout.addLayout(bottom)
    shell.addWidget(content, 1); w.setCentralWidget(root)

    def page():
        page_widget = QWidget(); page_widget.setObjectName('canvas')
        layout = QVBoxLayout(page_widget); layout.setContentsMargins(0, 0, 9, 6); layout.setSpacing(16)
        area = QScrollArea(); area.setWidgetResizable(True); area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setWidget(page_widget); w.pages.addWidget(area)
        return layout

    home = page()
    hero, h = card(hero=True)
    h.addWidget(label('ЛОКАЛЬНЫЙ ГОЛОСОВОЙ ВВОД', 'eyebrow'))
    w.hero_title = label('Говорите.\nТекст появится в нужном поле.', 'heroTitle'); h.addWidget(w.hero_title)
    w.hero_hint = label('Один раз выберите модель, микрофон и кнопку записи. '
                        'Затем диктуйте в браузере, редакторе или мессенджере.', 'muted'); h.addWidget(w.hero_hint)
    action_row = QHBoxLayout()
    w.home_action = button('Выбрать модель', w._home_action, True)
    w.toggle = button('Приостановить', w._toggle_runtime, role='quiet'); w.toggle.setEnabled(False)
    w.cancel_operation = button('Отменить', w._cancel_operation, role='quiet'); w.cancel_operation.hide()
    action_row.addWidget(w.home_action); action_row.addWidget(w.toggle); action_row.addWidget(w.cancel_operation); action_row.addStretch(); h.addLayout(action_row)
    home.addWidget(hero)
    setup, st = card('Подготовка к первой диктовке')
    steps = QHBoxLayout(); steps.setSpacing(10)
    w.model_summary = ElideLabel('Не выбрана'); w.mic_summary = ElideLabel('По умолчанию'); w.key_summary = ElideLabel('Боковая кнопка X1')
    for num, title, summary, target in ((1, 'Модель', w.model_summary, 1), (2, 'Микрофон и кнопка', w.mic_summary, 2), (3, 'Вставка текста', None, 3)):
        step = QFrame(); step.setObjectName('step'); sl = QVBoxLayout(step); sl.setContentsMargins(12, 12, 12, 12); sl.setSpacing(8)
        sl.addWidget(label(f'0{num}  {title}', 'small'))
        if summary is None:
            w.paste_summary = label('Проверьте до записи', 'muted'); sl.addWidget(w.paste_summary)
        else:
            summary.setObjectName('muted'); sl.addWidget(summary)
        sl.addWidget(button('Открыть', lambda checked=False, index=target: w._navigate(index), role='quiet'))
        steps.addWidget(step, 1)
    st.addLayout(steps); st.addWidget(w.key_summary)
    w.target_summary = label('', 'small'); st.addWidget(w.target_summary)
    home.addWidget(setup)
    result, r = card('Последний текст', 'Расшифровка остаётся здесь, даже когда вставка не удалась.')
    w.last_text = QPlainTextEdit(); w.last_text.setReadOnly(True); w.last_text.setMinimumHeight(110)
    w.last_text.setPlaceholderText('Здесь появится результат вашей диктовки.'); r.addWidget(w.last_text)
    rb = QHBoxLayout(); rb.addWidget(button('Копировать', w._copy_last)); rb.addWidget(button('Вставить через 5 секунд', w._retry_last)); rb.addStretch(); r.addLayout(rb)
    home.addWidget(result); home.addStretch()

    models = page()
    intro, i = card('Откуда взять модель?', 'Выберите уже скачанную модель или поручите загрузку приложению.')
    w.source_group = QButtonGroup(w)
    w.source_local = QRadioButton('У меня уже есть модель'); w.source_download = QRadioButton('Скачать Qwen3-ASR')
    w.source_group.addButton(w.source_local); w.source_group.addButton(w.source_download)
    i.addWidget(row(w.source_local, w.source_download)); models.addWidget(intro)
    w.model_sources = QStackedWidget(); models.addWidget(w.model_sources)
    local, lf = card('Подключить локальную модель', 'Файлы не копируются и не изменяются. Нужна папка HF-native Qwen3-ASR, в которой находится config.json.')
    w.model = QLineEdit(); w.model.setPlaceholderText('Папка модели на этом компьютере'); w.model.setClearButtonEnabled(True)
    w.browse_model = button('Обзор…', w._browse_model)
    lr = row(w.model, w.browse_model); lr.layout().setStretch(0, 1); lf.addWidget(lr)
    w.local_status = label('Проверка не загружает модель в память и не обращается к интернету.', 'small'); lf.addWidget(w.local_status)
    w.use_local = button('Проверить и выбрать', w._use_local, True); lf.addWidget(w.use_local, 0, Qt.AlignLeft)
    w.model_sources.addWidget(local)
    download, dl = card('Официальная модель Qwen3-ASR', 'Загрузка с Hugging Face. Аудиозаписи никуда не отправляются.')
    w.catalog_choice = QComboBox()
    for spec in CATALOG:
        w.catalog_choice.addItem(f'{spec.label}   ·   около {spec.approximate_gb:g} ГБ', spec.repo)
    dl.addWidget(w.catalog_choice)
    w.catalog_description = label(CATALOG[0].description, 'muted'); dl.addWidget(w.catalog_description)
    w.catalog_choice.currentIndexChanged.connect(lambda index: w.catalog_description.setText(CATALOG[index].description))
    dl.addWidget(label('Папка для моделей', 'small'))
    w.download_root = QLineEdit(str(default_model_dir()))
    w.browse_download = button('Изменить…', w._browse_download)
    dr = row(w.download_root, w.browse_download); dr.layout().setStretch(0, 1); dl.addWidget(dr)
    dl.addWidget(label('Внутри будет создана папка выбранной модели. '
                       'Обновление программы не удаляет веса. Можно выбрать другой диск.', 'small'))
    w.download_progress = QProgressBar(); w.download_progress.setRange(0, 1000); w.download_progress.setValue(0); w.download_progress.setTextVisible(False)
    dl.addWidget(w.download_progress)
    w.download_status = label('Готово к загрузке. Нужен доступ к huggingface.co.', 'muted'); dl.addWidget(w.download_status)
    w.download_detail = ElideLabel('Версия фиксируется перед загрузкой; файлы проверяются по контрольным суммам.'); w.download_detail.setObjectName('small'); dl.addWidget(w.download_detail)
    w.download_button = button('Скачать и выбрать', w._download_model, True)
    w.cancel_download = button('Приостановить', w._cancel_download); w.cancel_download.setEnabled(False)
    dl.addWidget(row(w.download_button, w.cancel_download))
    dl.addWidget(label('Модели: Qwen · Apache 2.0. Ссылка на источник и лицензия — ниже.', 'small'))
    dl.addWidget(button('Страница модели и лицензия', w._open_model_page, role='quiet'), 0, Qt.AlignLeft)
    w.model_sources.addWidget(download)
    active, a = card('Выбранная модель')
    w.active_model_path = ElideLabel('Пока не выбрана'); a.addWidget(w.active_model_path)
    w.active_model_status = label('Выбор модели и запуск распознавания — отдельные действия.', 'small'); a.addWidget(w.active_model_status)
    w.load_model_button = button('Включить диктовку', w._load_model_now, True); a.addWidget(w.load_model_button, 0, Qt.AlignLeft)
    models.addWidget(active); models.addStretch()
    w.source_local.toggled.connect(lambda checked: w.model_sources.setCurrentIndex(0) if checked else None)
    w.source_download.toggled.connect(lambda checked: w.model_sources.setCurrentIndex(1) if checked else None)
    w.source_download.setChecked(True)

    settings = page()
    audio, au = card('Звук и текст')
    af = form_layout(); w.mic = QComboBox(); w.mic.setMinimumContentsLength(15); w.mic.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    w.refresh_mics = button('Обновить', w._populate_devices)
    mr = row(w.mic, w.refresh_mics); mr.layout().setStretch(0, 1); af.addRow('Микрофон', mr)
    w.placement = QComboBox()
    for title, value in [('Автоматически · GPU / CPU', 'adaptive'), ('Только видеокарта', 'gpu'), ('Только процессор', 'cpu')]: w.placement.addItem(title, value)
    af.addRow('Вычисления', w.placement)
    w.mode = QComboBox()
    for title, value in [('Диктовка · пунктуация и команды', 'dictation'), ('Дословно · без команд', 'raw'), ('Умный · консервативная обработка', 'smart')]: w.mode.addItem(title, value)
    af.addRow('Обработка текста', w.mode)
    w.language = QLineEdit(); w.language.setPlaceholderText('Russian · оставьте пустым для автоопределения'); af.addRow('Язык речи', w.language)
    au.addLayout(af); settings.addWidget(audio)
    control, co = card('Управление записью', 'Удерживайте кнопку, пока говорите. Отпустите — запись отправится на распознавание.')
    cf = form_layout(); w.button = QComboBox(); w.button.addItem('Мышь · боковая кнопка X1', 'x1'); w.button.addItem('Мышь · боковая кнопка X2', 'x2')
    if platform.system() == 'Windows':
        for key in ('f8', 'f9', 'f10', 'f12'): w.button.addItem(f'Клавиатура · {key.upper()}', key)
    cf.addRow('Кнопка записи', w.button)
    w.mouse_device = QComboBox()
    if platform.system() == 'Linux': cf.addRow('Мышь Linux', w.mouse_device)
    w.consume = QCheckBox('Использовать выбранную кнопку только для диктовки'); cf.addRow('', w.consume)
    w.beep_cb = QCheckBox('Звуковой сигнал при записи и отправке текста'); cf.addRow('', w.beep_cb)
    w.autostart_cb = QCheckBox('Запускать Voice Bridge при входе в систему'); cf.addRow('', w.autostart_cb)
    w.auto_model_cb = QCheckBox('Включать выбранную модель при запуске'); cf.addRow('', w.auto_model_cb)
    co.addLayout(cf); co.addWidget(label('Оверлей поверх других приложений не используется: фокус остаётся в поле ввода.', 'small')); settings.addWidget(control)
    advanced, ad = card('Дополнительные параметры')
    advanced_toggle = button('Показать параметры памяти и словарь', lambda: advanced_body.setVisible(not advanced_body.isVisible()), role='quiet'); ad.addWidget(advanced_toggle)
    advanced_body = QWidget(); av = form_layout(); advanced_body.setLayout(av)
    w.reserve = QLineEdit(); w.cpu_ram = QLineEdit(); w.prompt = QLineEdit()
    av.addRow('Резерв GPU, ГиБ', w.reserve); av.addRow('Лимит RAM, ГиБ', w.cpu_ram); av.addRow('Имена и термины', w.prompt)
    w.vocabulary_file = QLineEdit(); w.vocabulary_file.setClearButtonEnabled(True)
    w.vocabulary_file.setPlaceholderText('Необязательно · пусто = без словаря замен')
    w.browse_vocabulary = button('Обзор…', w._browse_vocabulary)
    vr = row(w.vocabulary_file, w.browse_vocabulary); vr.layout().setStretch(0, 1)
    av.addRow('Файл словаря', vr)
    av.addRow('', label('Текстовый файл UTF-8: произношение = замена, по одной паре на строку. '
                        'Пустое поле отключает словарь; модель и пунктуация продолжают работать.', 'small'))
    ad.addWidget(advanced_body); advanced_body.hide(); settings.addWidget(advanced)
    w.save_settings = button('Сохранить и применить', w._save_and_restart, True)
    w.save_restart = w.save_settings
    settings.addWidget(w.save_settings, 0, Qt.AlignLeft); settings.addStretch()

    diagnostic = page()
    delivery, de = card('Текст должен появиться в поле', 'Этот тест не требует модели и микрофона. Он проверяет буфер обмена и отправку сочетания клавиш.')
    df = form_layout(); w.keyboard_backend = QComboBox(); w.paste_combo = QComboBox()
    if platform.system() == 'Windows':
        w.keyboard_backend.addItem('Windows · SendInput', 'auto'); w.keyboard_backend.setEnabled(False)
    else:
        for title, value in [('Автоматически · Wayland / X11', 'auto'), ('Виртуальная клавиатура · uinput', 'uinput'), ('ydotool · совместимость', 'ydotool'), ('X11 · XTest', 'pynput')]: w.keyboard_backend.addItem(title, value)
    for title, value in [('Ctrl+V · браузеры и редакторы', 'ctrl+v'), ('Ctrl+Shift+V · терминалы', 'ctrl+shift+v'), ('Shift+Insert · альтернативная вставка', 'shift+insert')]: w.paste_combo.addItem(title, value)
    df.addRow('Механизм', w.keyboard_backend); df.addRow('Вставка', w.paste_combo)
    w.target_policy = QComboBox()
    for title, value in [('Активное поле после распознавания · как CLI', 'active'),
                         ('Только окно, где начата запись', 'window'),
                         ('Только исходное окно и внутренний фокус · строго', 'strict')]:
        w.target_policy.addItem(title, value)
    df.addRow('Куда вставлять', w.target_policy)
    w.target_policy_hint = label('', 'small')
    hints = {
        'active': 'Текст уйдёт в поле, активное к концу распознавания. Можно переключиться в другое приложение до этого момента. Во время самой вставки окно и поле менять нельзя.',
        'window': 'Окно должно остаться тем же, где начата запись. Внутренний фокус может измениться. При смене окна текст сохраняется для ручной вставки.',
        'strict': 'Сравниваются исходное окно и Win32-фокус. Некоторые приложения пересоздают внутренние элементы; это может отложить вставку. Это не проверка DOM-поля браузера.'}
    w.target_policy.currentIndexChanged.connect(lambda: w.target_policy_hint.setText(hints[w.target_policy.currentData()]))
    w.target_policy_hint.setText(hints['active'])
    df.addRow('', w.target_policy_hint)
    if platform.system() != 'Windows':
        w.target_policy.setEnabled(False)
        w.target_policy_hint.setText('Привязка к окну доступна в Windows. В Linux используется активное поле.')
    de.addLayout(df)
    w.delivery_info = label('Подготовка механизма…', 'small'); de.addWidget(w.delivery_info)
    w.apply_delivery = button('Применить способ вставки', w._apply_delivery); de.addWidget(w.apply_delivery, 0, Qt.AlignLeft)
    w.test_internal = button('Проверить здесь', w._test_here, True)
    w.test_external = button('В другом окне через 5 с', w._test_elsewhere)
    de.addWidget(row(w.test_internal, w.test_external))
    w.test_editor = QPlainTextEdit(); w.test_editor.setObjectName('voice_bridge_delivery_test'); w.test_editor.setMinimumHeight(100)
    w.test_editor.setPlaceholderText('Тестовая строка должна появиться здесь через системную вставку.'); de.addWidget(w.test_editor)
    w.test_status = label('Тест ещё не запускался.', 'muted'); de.addWidget(w.test_status)
    diagnostic.addWidget(delivery)
    help_card, he = card('Когда нужна помощь')
    he.addWidget(label('Сначала проверьте вставку здесь, затем в Блокноте или браузере. '
                       'Внешний тест не отмечается успешным автоматически: появление текста проверяете вы.', 'muted'))
    he.addWidget(button('Открыть папку журналов', w._open_log_folder), 0, Qt.AlignLeft)
    diagnostic.addWidget(help_card); diagnostic.addStretch()
    w._populate_devices()
    w._navigate(0)
