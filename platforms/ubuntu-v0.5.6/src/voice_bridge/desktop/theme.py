"""Native Qt widgets, scalable layouts and a restrained dark desktop theme."""
from PySide6.QtCore import Qt, QSize, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QIcon, QPixmap
from PySide6.QtWidgets import QLabel, QFrame, QVBoxLayout, QSizePolicy

STYLE = '''
QWidget { color: #e8edf5; font-family: "Segoe UI", "Noto Sans", sans-serif; font-size: 14px; }
QMainWindow, QWidget#canvas { background: #0c111b; }
QWidget#sidebar { background: #101723; border-right: 1px solid #222d3e; }
QLabel { background: transparent; border: none; }
QLabel#brand { font-size: 21px; font-weight: 700; letter-spacing: -0.4px; }
QLabel#eyebrow { color: #68dcc6; font-size: 11px; font-weight: 700; letter-spacing: 1.6px; }
QLabel#pageTitle { font-size: 28px; font-weight: 700; letter-spacing: -0.6px; }
QLabel#heroTitle { font-size: 30px; font-weight: 700; letter-spacing: -0.6px; }
QLabel#cardTitle { font-size: 17px; font-weight: 600; }
QLabel#muted { color: #a2aec1; }
QLabel#small { color: #8696ad; font-size: 12px; }
QLabel#badge { color: #a9b9d0; background: #1a2535; border: 1px solid #30415c;
 padding: 7px 12px; border-radius: 13px; font-size: 12px; font-weight: 600; }
QLabel#badge[state="ready"] { color: #7ce5bf; background: #102e28; border-color: #205347; }
QLabel#badge[state="recording"] { color: #ffc7cd; background: #42202b; border-color: #754050; }
QLabel#badge[state="error"] { color: #ffb3ab; background: #39232b; border-color: #75404b; }
QFrame#card { background: #131c2a; border: 1px solid #283448; border-radius: 14px; }
QFrame#hero { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #142936,stop:1 #152030);
 border: 1px solid #2b4b55; border-radius: 16px; }
QFrame#step { background: #172333; border: none; border-radius: 10px; }
QPushButton { background: #1c293c; border: 1px solid #33435c; border-radius: 8px;
 padding: 10px 16px; font-weight: 600; min-height: 18px; }
QPushButton:hover { background: #273950; border-color: #58708f; }
QPushButton:pressed { background: #142131; }
QPushButton:focus { border: 1px solid #64ddc5; }
QPushButton:disabled { color: #6c7a8e; background: #17202d; border-color: #263246; }
QPushButton#primary { background: #66dfc5; border-color: #66dfc5; color: #09241f; }
QPushButton#primary:hover { background: #8ae9d5; }
QPushButton#primary:disabled { background: #254d48; border-color: #254d48; color: #83a39d; }
QPushButton#nav { text-align: left; background: transparent; border: 1px solid transparent;
 border-radius: 9px; padding: 12px; color: #a5b4c8; }
QPushButton#nav:hover { background: #192637; color: #e8edf5; }
QPushButton#nav:checked { background: #213831; border-color: #305248; color: #80e7cd; }
QPushButton#quiet { background: transparent; border: 1px solid #2a394f; color: #afc1d9; }
QPushButton#danger { background: #34212a; border-color: #68404d; color: #ffbdc6; }
QLineEdit, QPlainTextEdit, QComboBox { background: #0e1724; border: 1px solid #35435a;
 border-radius: 7px; padding: 9px 10px; selection-background-color: #285e55; }
QPlainTextEdit { font-size: 15px; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border-color: #66dfc5; }
QLineEdit:disabled { color: #77879b; }
QComboBox { min-height: 20px; padding-right: 26px; }
QComboBox::drop-down { width: 25px; border: none; }
QComboBox QAbstractItemView { background: #172334; border: 1px solid #3e526b;
 selection-background-color: #284f47; padding: 4px; }
QCheckBox, QRadioButton { spacing: 10px; padding: 5px 0; }
QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px; background: #0e1724;
 border: 1px solid #51627b; border-radius: 5px; }
QRadioButton::indicator { border-radius: 9px; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked { background: #66dfc5; border-color: #8ae9d5; }
QProgressBar { background: #0c1522; border: 1px solid #30405a; border-radius: 6px;
 min-height: 12px; max-height: 12px; color: transparent; }
QProgressBar::chunk { background: #66dfc5; border-radius: 5px; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 9px; margin: 3px; }
QScrollBar::handle:vertical { background: #35465c; min-height: 28px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QMenu { background: #162132; border: 1px solid #354960; padding: 5px; }
QMenu::item { padding: 8px 24px; }
QMenu::item:selected { background: #28473f; }
QToolTip { color: #eaf2fa; background: #243247; border: 1px solid #49607b; padding: 6px; }
QMessageBox, QFileDialog { background: #121d2b; }
'''


def label(text='', role='', wrap=True):
    result = QLabel(text)
    result.setTextFormat(Qt.PlainText)
    if role:
        result.setObjectName(role)
    result.setWordWrap(wrap)
    if wrap:
        result.setMinimumWidth(0)
        result.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return result


class ElideLabel(QLabel):
    """Paths/device names never force the window beyond the screen width."""
    def __init__(self, text='', parent=None):
        super().__init__(parent)
        self._full = ''
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text):
        self._full = str(text)
        self.setToolTip(self._full)
        self._elide()

    def _elide(self):
        super().setText(self.fontMetrics().elidedText(self._full, Qt.ElideMiddle, max(40, self.width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


def card(title='', subtitle='', hero=False):
    widget = QFrame()
    widget.setObjectName('hero' if hero else 'card')
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(22, 20, 22, 20)
    layout.setSpacing(12)
    if title:
        layout.addWidget(label(title, 'cardTitle'))
    if subtitle:
        layout.addWidget(label(subtitle, 'muted'))
    return widget, layout


def brand_icon(size=64):
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor('#203b38'))
    painter.drawRoundedRect(QRectF(1, 1, size-2, size-2), size*.23, size*.23)
    painter.setPen(QPen(QColor('#75e6cd'), size*.072, Qt.SolidLine, Qt.RoundCap))
    for i, h in enumerate((.19, .42, .64, .37, .19)):
        x = size*(.25+i*.125)
        painter.drawLine(int(x), int(size*(.5-h/2)), int(x), int(size*(.5+h/2)))
    painter.end()
    return QIcon(pix)
