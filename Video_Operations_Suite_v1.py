# -*- coding: utf-8 -*-
# ------------------------------ core imports ------------------------------
import os
import sys
import json
import time
import re
import subprocess
import shutil
import hashlib
from pathlib import Path
from typing import Optional, Dict, List, Any

# Optioneel: extra Pythonpad via env var (geen lokale paden hardcoderen)
_EXTRA_PYTHONPATH = os.getenv("VOS_PYTHONPATH", "").strip()
if _EXTRA_PYTHONPATH:
    sys.path.insert(0, _EXTRA_PYTHONPATH)

# Probeer winsound voor piepjes (Windows only)
try:
    import winsound
    HAS_SOUND = True
except ImportError:
    HAS_SOUND = False

# Onderdruk OpenCV log noise (moet voor import cv2)
os.environ["OPENCV_LOG_LEVEL"] = "OFF"

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWidgets import QStyle, QListWidgetItem, QAbstractItemView

# --- Optioneel: OpenCV voor thumbnails ---
try:  # type: ignore
    import cv2
    import numpy as np  # noqa: F401
    HAS_OPENCV = True
except Exception:  # pragma: no cover
    HAS_OPENCV = False

# Optioneel: psutil voor live pauze/suspend van ffmpeg-proces
try:  # type: ignore
    import psutil
except Exception:  # pragma: no cover
    psutil = None

# Optioneel: VLC
try:
    import vlc  # python-vlc
except Exception:  # pragma: no cover
    vlc = None


# ------------------------------ app config ------------------------------
APP_ORG = "VideoOperationsSuite"
APP_NAME = "Video Operations Suite"
APP_TITLE = "Video Operations Suite v1.0"
APP_VER = "1.0"
SETTINGS_SCOPE = f"{APP_ORG}/{APP_NAME}"

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
if not ASSETS_DIR.exists():
    ASSETS_DIR = BASE_DIR

APP_ICON_PATH = ASSETS_DIR / "Video_Operations_Suite_.ico"
STARTUP_VIDEO = ASSETS_DIR / "VOS-Neon_Zwaardsnede_Video_-2sNOsound.mp4"
STARTUP_POSTER = ASSETS_DIR / "VOS-Neon_Zwaardsnede_Video_-2sNOsound_splash_poster.png"

AUTHOR_NAME = "Rymnda"
AUTHOR_URL = "https://github.com/Rymnda"

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".ts", ".avi", ".wmv", ".flv", ".webm"}

MODE_TRIM_FIRST = "Trim eerste seconden"
MODE_TRIM_LAST  = "Trim laatste seconden"
MODE_ROLL_FIRST = "Roll eerste seconden → einde"
MODE_REMUX_COPY = "Remux copy-only (container)"
MODE_TRANSCODE  = "Transcode naar MP4 (H.264)"

COL_SEL, COL_SEC, COL_MMSS, COL_FILE, COL_TYPE, COL_DUR, COL_SIZE, COL_PCT, COL_REMAIN, COL_STATUS = range(10)
HEADER = ["✓", "Sec", "Tijd", "Bestand (Titel)", "Type", "Duur", "Grootte", "Perc.", "Rest.", "Status"]


# ------------------------------ Custom UI Widgets ------------------------------
class CenteredCheckBox(QtWidgets.QWidget):
    """
    Een container widget die een luxe, gecentreerde check-knop in een tabelcel plaatst.
    Laat bewust een echt vinkje zien in plaats van alleen een ingekleurd vakje.
    """
    toggled = QtCore.Signal(bool)

    def __init__(self, parent=None, checked=False):
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(QtCore.Qt.AlignCenter)

        self.checkbox = QtWidgets.QCheckBox()
        self.checkbox.setText("")
        self.checkbox.setFocusPolicy(QtCore.Qt.NoFocus)
        self.checkbox.setCursor(QtCore.Qt.PointingHandCursor)
        self.checkbox.setFixedSize(24, 24)
        self.checkbox.toggled.connect(self.toggled.emit)

        self.setCursor(QtCore.Qt.PointingHandCursor)
        layout.addWidget(self.checkbox)
        self.setLayout(layout)
        self.setFixedHeight(30)
        self.setChecked(checked)

    def isChecked(self):
        return self.checkbox.isChecked()

    def setChecked(self, state):
        self.checkbox.setChecked(state)

    def mousePressEvent(self, event):
        # Zorg dat klikken naast de checkbox (maar in de cel) ook toggled
        self.checkbox.setChecked(not self.checkbox.isChecked())
        super().mousePressEvent(event)


class NumericItem(QtWidgets.QTableWidgetItem):
    """Zorgt dat kolommen op getalwaarde sorteren i.p.v. alfabetisch."""
    def __lt__(self, other):
        try:
            # Haal tekst op, strip ' MB' etc indien nodig, en vervang komma
            t1 = self.text().split()[0].replace(',', '.')
            t2 = other.text().split()[0].replace(',', '.')
            return float(t1) < float(t2)
        except ValueError:
            return super().__lt__(other)


# ------------------------------ helpers ------------------------------
def play_sound(is_batch_done: bool = False):
    """Speelt een comfortabel geluidje af (Windows)."""
    if not HAS_SOUND:
        return
    try:
        if is_batch_done:
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        else:
            winsound.MessageBeep(winsound.MB_OK)
    except Exception:
        pass


def which(cmd: str) -> Optional[str]:
    p = shutil.which(cmd)
    if p:
        return p
    guesses = [
        Path(r"C:\\Program Files\\FFMPEG\\bin") / cmd,
        Path(sys.argv[0]).resolve().parent / (cmd + (".exe" if os.name == "nt" else "")),
    ]
    for g in guesses:
        if g.exists():
            return str(g)
    return None


def fmt_hms(total_seconds: float | int | None) -> str:
    if total_seconds is None:
        return "—"
    try:
        s = int(max(0, round(float(total_seconds))))
    except Exception:
        s = 0
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_size(num_bytes: float | int | None) -> str:
    if num_bytes is None:
        return "—"
    try:
        b = float(num_bytes)
    except Exception:
        b = 0.0
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while b >= 1024 and i < len(units) - 1:
        b /= 1024.0
        i += 1
    return f"{b:.2f} {units[i]}"


def parse_hhmmss_to_seconds(hhmmss: str) -> float:
    if not hhmmss:
        return 0.0
    s = hhmmss.strip()
    if s.isdigit():
        if len(s) <= 2:
            return float(int(s))
        if len(s) == 3:  # mss
            m = int(s[0])
            ss = int(s[1:])
            return float(m * 60 + ss)
        if len(s) == 4:  # mmss
            m = int(s[:2])
            ss = int(s[2:])
            return float(m * 60 + ss)
        return float(int(s))
    parts = s.split(":")
    try:
        if len(parts) == 3:
            h, m, sec = int(parts[0]), int(parts[1]), float(parts[2])
            return h * 3600 + m * 60 + sec
        if len(parts) == 2:
            m, sec = int(parts[0]), float(parts[1])
            return m * 60 + sec
        if len(parts) == 1:
            return float(parts[0])
    except Exception:
        return 0.0
    return 0.0


def seconds_to_mmss(sec: float | int) -> str:
    try:
        s = int(round(float(sec)))
    except Exception:
        s = 0
    s = max(0, s)
    m, s = divmod(s, 60)
    return f"{m}:{s:02d}"


def mmss_to_seconds(text: str) -> int:
    t = text.strip()
    if not t:
        return 0
    if t.isdigit():
        return int(t)
    if ":" in t:
        m, s = t.split(":", 1)
        try:
            return int(m) * 60 + int(s)
        except Exception:
            return 0
    return 0


def detect_intro_ffmpeg(ffmpeg_path: str, file_path: str) -> float:
    """Zoekt naar blackframe-einde tussen 10s en 240s (intro-detectie)."""
    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel", "error",
        "-fflags", "+discardcorrupt",
        "-err_detect", "ignore_err",
        "-i", file_path,
        "-vf", "blackdetect=d=0.3:pix_th=0.1",
        "-an",
        "-f", "null",
        "-t", "300",
        "-",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        stderr = result.stderr or ""
        matches = re.findall(r"black_end:(\d+\.?\d*)", stderr)
        if not matches:
            return 0.0

        for m in matches:
            t = float(m)
            if 10.0 < t < 240.0:
                return t
        return 0.0
    except Exception:
        return 0.0


# ------------------------------ theming ------------------------------
THEMES = {
    "Modern Dark": {
        "win": "#121212",
        "base": "#1E1E1E",
        "alt": "#252525",
        "accent": "#00B4D8",     # Cyan-ish blue
        "sel": "#3A3A3A",
        "text": "#E0E0E0",
        "btn_bg": "#2C2C2C",
        "btn_hover": "#383838",
    }
}
DEFAULT_THEME = "Modern Dark"
WHITE = QtGui.QColor("#FFFFFF")


class ForceWhiteDelegate(QtWidgets.QStyledItemDelegate):
    def paint(self, p: QtGui.QPainter, opt: QtWidgets.QStyleOptionViewItem, idx: QtCore.QModelIndex) -> None:
        o = QtWidgets.QStyleOptionViewItem(opt)
        self.initStyleOption(o, idx)
        o.state &= ~QtWidgets.QStyle.State_HasFocus
        pal = o.palette
        for g in (QtGui.QPalette.Active, QtGui.QPalette.Inactive, QtGui.QPalette.Disabled):
            pal.setBrush(g, QtGui.QPalette.Text, QtGui.QBrush(WHITE))
            pal.setBrush(g, QtGui.QPalette.WindowText, QtGui.QBrush(WHITE))
            pal.setBrush(g, QtGui.QPalette.HighlightedText, QtGui.QBrush(WHITE))
        o.palette = pal
        super().paint(p, o, idx)


# ------------------------------ persist ------------------------------
APPDATA_DIR = Path.home() / ".ultimate_suite_v7"
APPDATA_DIR.mkdir(parents=True, exist_ok=True)
QUEUE_JSON = APPDATA_DIR / "queue.json"
CUSTOM_THUMB_DIR = APPDATA_DIR / "custom_thumbs"
CUSTOM_THUMB_DIR.mkdir(parents=True, exist_ok=True)
SESSION_EXT = ".vos"


def save_queue(table: QtWidgets.QTableWidget, ui_state: dict):
    items = []
    for r in range(table.rowCount()):
        it_file = table.item(r, COL_FILE)
        it_sec = table.item(r, COL_SEC)
        
        # Ophalen van custom widget state ipv standaard checkbox
        chk_widget = table.cellWidget(r, COL_SEL)
        checked = chk_widget.isChecked() if chk_widget else False

        if not it_file:
            continue

        path = it_file.toolTip()
        secs = it_sec.text() if it_sec else "0"
        
        items.append({
            "path": path,
            "secs": secs,
            "checked": checked
        })
    obj = {"items": items, **ui_state}
    try:
        QUEUE_JSON.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    except Exception:
        pass


def load_queue() -> dict:
    if not QUEUE_JSON.exists():
        return {}
    try:
        return json.loads(QUEUE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ------------------------------ VLC player ------------------------------
class VLCVideo(QtWidgets.QFrame):
    time_changed = QtCore.Signal(float, float)

    def __init__(self, parent=None, force_software_decoding: bool = False, aspect: Optional[str] = "16:9"):
        super().__init__(parent)
        self.instance = None
        self.player = None
        self._available = False
        self._aspect = aspect
        opts = [
            "--no-video-title-show",
            "--embedded-video",
            "--no-video-on-top",
            "--no-qt-video-autoresize",
            "--intf=dummy",
            "--quiet",
        ]
        if force_software_decoding:
            opts.append("--avcodec-hw=none")
        if vlc:
            try:
                self.instance = vlc.Instance(opts)
                self.player = self.instance.media_player_new()
                self._available = True
            except Exception:
                self._available = False
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._tick)
        self.setMinimumHeight(300)
        self.setStyleSheet("background-color: #000000;")
        self._last_nonzero_volume = 60

    def is_available(self) -> bool:
        return self._available

    def _apply_aspect(self):
        if not self._available or not self.player:
            return
        try:
            if not self._aspect or self._aspect == "Auto":
                self.player.video_set_aspect_ratio(None)
            else:
                self.player.video_set_aspect_ratio(self._aspect.encode("utf-8"))
            self.player.video_set_scale(0)
        except Exception:
            pass

    def set_aspect(self, aspect: Optional[str]):
        self._aspect = aspect
        self._apply_aspect()

    def set_media_load(self, path: str, start_paused: bool = True):
        if not self._available:
            return
        try:
            self.player.stop()
        except Exception:
            pass
        m = self.instance.media_new(path)
        self.player.set_media(m)
        wid = int(self.winId())
        if sys.platform.startswith("win"):
            self.player.set_hwnd(wid)
        elif sys.platform == "darwin":
            self.player.set_nsobject(wid)
        else:
            self.player.set_xwindow(wid)
        self._apply_aspect()
        self.player.play()
        self._timer.start()
        if start_paused:
            QtCore.QTimer.singleShot(120, lambda: self.player.set_pause(1))

    def set_rate(self, rate: float):
        if not self._available or not self.player:
            return
        try:
            self.player.set_rate(float(rate))
        except Exception:
            pass

    def pause(self):
        if self._available:
            try:
                self.player.set_pause(1)
            except Exception:
                pass

    def stop(self):
        if self._available:
            self.player.stop()
            self._timer.stop()
            self.time_changed.emit(0.0, self.get_length_seconds())

    def get_time_ms(self) -> int:
        try:
            return int(self.player.get_time() or 0)
        except Exception:
            return 0

    def get_length_ms(self) -> int:
        try:
            return int(self.player.get_length() or 0)
        except Exception:
            return 0

    def get_length_seconds(self) -> float:
        return self.get_length_ms() / 1000.0

    def set_position(self, pos01: float):
        if self._available:
            try:
                self.player.set_position(max(0.0, min(1.0, float(pos01))))
            except Exception:
                pass

    def seek_relative(self, seconds: int):
        if not self._available:
            return
        cur = self.get_time_ms()
        dur = max(1, self.get_length_ms())
        new_ms = max(0, min(dur, cur + seconds * 1000))
        try:
            self.player.set_time(new_ms)
        except Exception:
            self.set_position(new_ms / float(dur))

    def _tick(self):
        try:
            t = float(self.get_time_ms() / 1000.0)
            dur = float(self.get_length_ms() / 1000.0)
            pos = 0.0 if dur <= 0 else (t / dur)
            self.time_changed.emit(pos, dur)
        except Exception:
            pass

    def _win_get_volume(self) -> Optional[int]:
        if not sys.platform.startswith("win"):
            return None
        try:
            import ctypes
            vol = ctypes.c_uint()
            if ctypes.windll.winmm.waveOutGetVolume(0, ctypes.byref(vol)) != 0:
                return None
            left = vol.value & 0xFFFF
            right = (vol.value >> 16) & 0xFFFF
            avg = (left + right) // 2
            return int(round((avg / 0xFFFF) * 100))
        except Exception:
            return None

    def _win_set_volume(self, pct: int) -> bool:
        if not sys.platform.startswith("win"):
            return False
        try:
            import ctypes
            p = max(0, min(100, int(pct)))
            val = int((p / 100.0) * 0xFFFF)
            packed = (val << 16) | val
            return ctypes.windll.winmm.waveOutSetVolume(0, packed) == 0
        except Exception:
            return False

    def get_volume_percent(self) -> int:
        sys_vol = self._win_get_volume()
        if sys_vol is not None:
            return sys_vol
        if self._available and self.player:
            try:
                v = int(self.player.audio_get_volume())
                return max(0, min(100, v))
            except Exception:
                pass
        return self._last_nonzero_volume

    def set_volume_percent(self, pct: int):
        p = max(0, min(100, int(pct)))
        if p > 0:
            self._last_nonzero_volume = p
        if self._win_set_volume(p):
            return
        if self._available and self.player:
            try:
                self.player.audio_set_volume(p)
            except Exception:
                pass

    def toggle_mute(self):
        current = self.get_volume_percent()
        if current <= 0:
            self.set_volume_percent(max(1, self._last_nonzero_volume))
        else:
            self._last_nonzero_volume = current
            self.set_volume_percent(0)

class VideoControlBar(QtWidgets.QWidget):
    request_seek = QtCore.Signal(float)
    nudge = QtCore.Signal(int)
    toggle_play = QtCore.Signal()
    prev_file = QtCore.Signal()
    next_file = QtCore.Signal()
    seek_and_play = QtCore.Signal(float)
    capture_mark = QtCore.Signal(int)  # stuurt huidige tijd in seconden naar Main
    mute_toggle = QtCore.Signal()
    volume_level = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)

        def make_btn(txt: str, tt: str = "", sc: Optional[str] = None, primary: bool = False) -> QtWidgets.QPushButton:
            b = QtWidgets.QPushButton(txt)
            b.setMinimumHeight(40)
            b.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))

            base_style = (
                "QPushButton{"
                "padding: 4px 10px;"
                "border-radius: 6px;"
                "border: 1px solid #333;"
                "background: #222;"
                "color: #EEE;"
                "font-size: 12px;"
                "font-weight: 600;"
                "}"
                "QPushButton:hover{background:#2C2C2C;}"
                "QPushButton:pressed{background:#181818;}"
            )
            primary_style = (
                "QPushButton{"
                "padding: 4px 14px;"
                "border-radius: 6px;"
                "border: 1px solid #00B4D8;"
                "background: rgba(0,180,216,0.25);"
                "color:#EFFFFF;"
                "font-size: 13px;"
                "font-weight: 700;"
                "}"
                "QPushButton:hover{background:rgba(0,180,216,0.35);}"
                "QPushButton:pressed{background:rgba(0,180,216,0.18);}"
            )

            b.setStyleSheet(primary_style if primary else base_style)

            if tt:
                b.setToolTip(tt + (f"  •  {sc}" if sc else ""))
            if sc:
                QtGui.QShortcut(QtGui.QKeySequence(sc), self, activated=b.click)
            return b

        def set_std_icon(btn: QtWidgets.QPushButton, icon_role: QStyle.StandardPixmap):
            base_icon = self.style().standardIcon(icon_role)
            px = base_icon.pixmap(28, 28)
            if not px.isNull():
                tinted = QtGui.QPixmap(px.size())
                tinted.fill(QtCore.Qt.transparent)
                p = QtGui.QPainter(tinted)
                p.drawPixmap(0, 0, px)
                p.setCompositionMode(QtGui.QPainter.CompositionMode_SourceIn)
                p.fillRect(tinted.rect(), QtGui.QColor("#FFFFFF"))
                p.end()
                btn.setIcon(QtGui.QIcon(tinted))
            else:
                btn.setIcon(base_icon)
            btn.setIconSize(QtCore.QSize(26, 26))
            btn.setFixedWidth(56)

        def set_no_hover(btn: QtWidgets.QPushButton):
            btn.setStyleSheet(
                "QPushButton{"
                "padding: 4px 10px;"
                "border-radius: 6px;"
                "border: 1px solid #333;"
                "background: #222;"
                "color: #EEE;"
                "font-size: 12px;"
                "font-weight: 600;"
                "}"
            )

        # hoofdknoppen
        self.btn_prev = make_btn("⏮ Vorige", "⏮ Vorige bestand", "PgUp")
        self.btn_back30 = make_btn("⏪ -30s", "30 seconden achteruitspoelen", "Shift+J")
        self.btn_back5 = make_btn("◀ -5s", "-5s terug", "Left")
        self.btn_playpa = make_btn("▶ Play/Pauze", "⏯ Afspelen/Pauzeren", "Space", primary=True)
        self.btn_fwd5 = make_btn("▶ +5s", "+5s vooruit", "Right")
        self.btn_fwd30 = make_btn("⏩ +30s", "30 seconden vooruitspoelen", "Shift+L")
        self.btn_next = make_btn("⏭ Volgende", "⏭ Volgende bestand", "PgDown")
        self.btn_mute = make_btn("", "🔇 Dempen / 🔊 Geluid")
        self.vol_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(60)
        self.vol_slider.setFixedWidth(130)
        self.vol_slider.setToolTip("Volume")
        self.lbl_volume = QtWidgets.QLabel("Geluid")
        self.lbl_volume.setStyleSheet("color:#9CC8DB; font-weight:600;")

        # nieuwe markeerknop: gebruikt huidige tijd als Sec voor geselecteerde bestanden
        self.btn_mark_sec = make_btn(
            "→Sec",
            "Gebruik huidige tijd als Sec voor geselecteerde bestanden",
            "Ctrl+M",
        )
        self.btn_mark_sec.setFixedWidth(80)

        # play/pause iconen
        self.icon_play = self.style().standardIcon(QStyle.SP_MediaPlay)
        self.icon_pause = self.style().standardIcon(QStyle.SP_MediaPause)
        self.icon_mute = self.style().standardIcon(QStyle.SP_MediaVolumeMuted)
        self.icon_volume = self.style().standardIcon(QStyle.SP_MediaVolume)
        play_px = self.icon_play.pixmap(20, 20)
        pause_px = self.icon_pause.pixmap(20, 20)
        if not play_px.isNull():
            pmap = QtGui.QPixmap(play_px.size())
            pmap.fill(QtCore.Qt.transparent)
            p = QtGui.QPainter(pmap)
            p.drawPixmap(0, 0, play_px)
            p.setCompositionMode(QtGui.QPainter.CompositionMode_SourceIn)
            p.fillRect(pmap.rect(), QtGui.QColor("#FFFFFF"))
            p.end()
            self.icon_play = QtGui.QIcon(pmap)
        if not pause_px.isNull():
            pmap = QtGui.QPixmap(pause_px.size())
            pmap.fill(QtCore.Qt.transparent)
            p = QtGui.QPainter(pmap)
            p.drawPixmap(0, 0, pause_px)
            p.setCompositionMode(QtGui.QPainter.CompositionMode_SourceIn)
            p.fillRect(pmap.rect(), QtGui.QColor("#FFFFFF"))
            p.end()
            self.icon_pause = QtGui.QIcon(pmap)
        for icon_name in ("icon_mute", "icon_volume"):
            ic = getattr(self, icon_name)
            px = ic.pixmap(20, 20)
            if px.isNull():
                continue
            pmap = QtGui.QPixmap(px.size())
            pmap.fill(QtCore.Qt.transparent)
            p = QtGui.QPainter(pmap)
            p.drawPixmap(0, 0, px)
            p.setCompositionMode(QtGui.QPainter.CompositionMode_SourceIn)
            p.fillRect(pmap.rect(), QtGui.QColor("#FFFFFF"))
            p.end()
            setattr(self, icon_name, QtGui.QIcon(pmap))

        self.btn_playpa.setIcon(self.icon_play)
        self.btn_playpa.setIconSize(QtCore.QSize(26, 26))
        # laat breedte meeschalen met icoon+tekst
        for w in (
            self.btn_prev,
            self.btn_back30,
            self.btn_back5,
            self.btn_playpa,
            self.btn_fwd5,
            self.btn_fwd30,
            self.btn_next,
        ):
            w.setFixedWidth(w.sizeHint().width() + 8)
        self.btn_mute.setIcon(self.icon_volume)
        self.btn_mute.setIconSize(QtCore.QSize(26, 26))
        self.btn_mute.setFixedWidth(62)

        # slider
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(0, 1000)

        # tijdlabel
        self.lbl_time = QtWidgets.QLabel("00:00 / 00:00")
        self.lbl_time.setStyleSheet("color: #AAAAAA; font-weight: bold;")
        mono = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        if mono.pointSize() <= 0:
            mono.setPointSize(10)
        self.lbl_time.setFont(mono)

        # goto veld
        self.ed_goto = QtWidgets.QLineEdit()
        self.ed_goto.setPlaceholderText("hh:mm:ss")
        self.ed_goto.setFixedWidth(80)
        self.ed_goto.setAlignment(QtCore.Qt.AlignCenter)
        self.ed_goto.setToolTip("Spring naar tijd (Enter)")
        self.ed_goto.setStyleSheet(
            "background: #1A1A1A; border: 1px solid #333; border-radius: 4px; color: #EEE;"
        )

        self._length_s = 0.0
        self._cur_s = 0.0  # huidige positie in seconden

        # layout: eerst tijd/slider/goto/mark, daaronder transportknoppen
        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(6)
        row2.addWidget(self.lbl_time)
        row2.addWidget(self.slider)
        row2.addWidget(self.ed_goto)
        row2.addWidget(self.btn_mark_sec)

        row1 = QtWidgets.QHBoxLayout()
        row1.setSpacing(6)
        left_group = QtWidgets.QHBoxLayout()
        left_group.setSpacing(6)
        for w in (
            self.btn_prev,
            self.btn_back30,
            self.btn_back5,
            self.btn_playpa,
            self.btn_fwd5,
            self.btn_fwd30,
            self.btn_next,
        ):
            left_group.addWidget(w)

        right_group = QtWidgets.QHBoxLayout()
        right_group.setSpacing(6)
        right_group.addWidget(self.btn_mute)
        right_group.addWidget(self.lbl_volume)
        right_group.addWidget(self.vol_slider)

        row1.addStretch(1)
        row1.addLayout(left_group)
        row1.addStretch(1)
        row1.addLayout(right_group)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addLayout(row2)
        lay.addLayout(row1)

        # verbindingen
        self.slider.sliderMoved.connect(lambda v: self.request_seek.emit(v / 1000.0))
        self.slider.sliderReleased.connect(lambda: self.request_seek.emit(self.slider.value() / 1000.0))

        self.btn_back5.clicked.connect(lambda: self.nudge.emit(-5))
        self.btn_fwd5.clicked.connect(lambda: self.nudge.emit(+5))
        self.btn_back30.clicked.connect(lambda: self.nudge.emit(-30))
        self.btn_fwd30.clicked.connect(lambda: self.nudge.emit(+30))
        self.btn_prev.clicked.connect(self.prev_file.emit)
        self.btn_next.clicked.connect(self.next_file.emit)
        self.btn_playpa.clicked.connect(self.toggle_play.emit)
        self.btn_mute.clicked.connect(self.mute_toggle.emit)
        self.vol_slider.valueChanged.connect(lambda v: self.volume_level.emit(int(v)))

        for b in (self.btn_back30, self.btn_back5, self.btn_fwd5, self.btn_fwd30):
            set_no_hover(b)

        self.btn_mark_sec.clicked.connect(self._emit_capture_mark)

        self.ed_goto.returnPressed.connect(self._goto_entered)
        self.ed_goto.textChanged.connect(self._validate_goto)

    def update_time(self, pos01: float, length_s: float):
        self._length_s = float(max(0.0, length_s))
        self.slider.blockSignals(True)
        self.slider.setValue(int(max(0.0, min(1.0, pos01)) * 1000))
        self.slider.blockSignals(False)
        cur = int(max(0.0, min(length_s, pos01 * length_s)))
        self._cur_s = float(cur)
        self.lbl_time.setText(self.format_hms(cur) + " / " + self.format_hms(int(length_s)))

    def set_playing(self, playing: bool):
        self.btn_playpa.setText("")
        self.btn_playpa.setIcon(self.icon_pause if playing else self.icon_play)
        self.btn_playpa.setToolTip(("Pauzeren" if playing else "Afspelen") + "  •  Space")

    def set_muted_state(self, muted: bool):
        self.btn_mute.setIcon(self.icon_mute if muted else self.icon_volume)
        self.btn_mute.setToolTip("🔊 Geluid" if muted else "🔇 Dempen")

    @staticmethod
    def format_hms(sec: int) -> str:
        h = sec // 3600
        m = (sec % 3600) // 60
        s = sec % 60
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    @staticmethod
    def _parse_hms(s: str) -> float:
        return parse_hhmmss_to_seconds(s)

    def _validate_goto(self):
        s = (self.ed_goto.text() or "").strip()
        secs = self._parse_hms(s)
        valid = (secs >= 0.0) and (self._length_s <= 0.0 or secs <= self._length_s)
        self.ed_goto.setStyleSheet(
            "background: #1A1A1A; border: 1px solid #333; border-radius: 4px; color: #EEE;"
            if valid or not s
            else "background: #331111; border: 1px solid #D44; border-radius: 4px; color: #FFF;"
        )

    def _goto_entered(self):
        s = (self.ed_goto.text() or "").strip()
        secs = self._parse_hms(s)
        if secs < 0.0 or self._length_s <= 0.0:
            self._validate_goto()
            return
        secs = max(0.0, min(self._length_s, secs))
        pos01 = 0.0 if self._length_s == 0 else secs / self._length_s
        self.seek_and_play.emit(pos01)

    def _emit_capture_mark(self):
        # rond af op hele seconden en stuur naar Main
        self.capture_mark.emit(int(round(self._cur_s)))


# ------------------------------ batch worker ------------------------------
class BatchWorker(QtCore.QThread):
    row_progress = QtCore.Signal(int, int, str, str)
    row_status = QtCore.Signal(int, str)
    row_done = QtCore.Signal(int, bool, str)
    overall_eta = QtCore.Signal(str)
    sound_notify = QtCore.Signal(bool)

    def __init__(
        self,
        jobs: List[dict],
        ffmpeg: str,
        ffprobe: str,
        parent=None,
        rename_ts_only: bool = True,
        delete_source: bool = False,
        overwrite_source: bool = False,
    ):
        super().__init__(parent)
        self.jobs = jobs
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.stop_flag = False
        self.pause_all = False
        self.pause_after_row = set()
        self._current_proc: Optional[subprocess.Popen] = None
        self._per_row_left: Dict[int, float] = {}
        self.rename_ts_only = rename_ts_only
        self.delete_source = delete_source
        self.overwrite_source = overwrite_source
        self._row_live_paused: set[int] = set()

    def request_stop_all(self):
        self.stop_flag = True
        try:
            if self._current_proc and self._current_proc.poll() is None:
                self._current_proc.terminate()
        except Exception:
            pass

    def request_pause_all(self):
        self.pause_all = True

    def request_resume_all(self):
        self.pause_all = False

    def request_pause_after_row(self, row: int):
        self.pause_after_row.add(row)

    def cancel_current_row(self):
        try:
            if self._current_proc and self._current_proc.poll() is None:
                self._current_proc.terminate()
        except Exception:
            pass

    def pause_row_live(self, row: int):
        self._row_live_paused.add(row)
        if psutil and self._current_proc and self._current_proc.poll() is None:
            try:
                p = psutil.Process(self._current_proc.pid)
                p.suspend()
            except Exception:
                pass

    def resume_row_live(self, row: int):
        self._row_live_paused.discard(row)
        if psutil and self._current_proc and self._current_proc.poll() is None:
            try:
                p = psutil.Process(self._current_proc.pid)
                p.resume()
            except Exception:
                pass

    def _emit_overall_eta(self):
        remaining = 0.0
        remaining += sum(max(0.0, v) for v in self._per_row_left.values())
        for job in self.jobs:
            if job.get("_done") or job.get("_started"):
                continue
            t = job["task"]
            mode = t["mode"]
            indur = float(t.get("in_seconds", 60.0))
            if mode in (MODE_REMUX_COPY, MODE_TRIM_FIRST, MODE_TRIM_LAST):
                remaining += max(5.0, 0.15 * indur)
            elif mode in (MODE_TRANSCODE, MODE_ROLL_FIRST):
                remaining += max(5.0, indur)
            else:
                remaining += max(5.0, indur)
        self.overall_eta.emit(f"Totale ETA: ~{fmt_hms(remaining)}")

    @staticmethod
    def _phase_for(mode: str) -> str:
        if mode == MODE_REMUX_COPY:
            return "muxing"
        if mode in (MODE_TRIM_FIRST, MODE_TRIM_LAST):
            return "copy-trim"
        if mode == MODE_ROLL_FIRST:
            return "roll-encode"
        if mode == MODE_TRANSCODE:
            return "encoding"
        return "processing"

    def run(self):
        for job in self.jobs:
            job["_done"] = False
            job["_started"] = False

        for job in self.jobs:
            if self.stop_flag:
                break
            r = job["row"]
            t = job["task"]
            mode = t["mode"]
            src, outp, sec = t["src"], t["out"], int(t["secs"])
            use_cuda = t["cuda"]
            indur = float(t.get("in_seconds", 0.0))
            in_bytes = int(t.get("in_bytes", 0))
            fallback = bool(t.get("fallback", False))
            overwrite = bool(t.get("overwrite", self.overwrite_source))
            phase = self._phase_for(mode)
            self.row_status.emit(r, f"Gestart • {phase}")
            job["_started"] = True

            if (
                self.rename_ts_only
                and Path(src).suffix.lower() == ".ts"
                and mode in (MODE_REMUX_COPY, MODE_TRIM_FIRST, MODE_TRIM_LAST)
            ):
                try:
                    out_ts = Path(outp).with_suffix(".mp4")
                    Path(src).rename(out_ts)
                    self.row_progress.emit(r, 100, "0s", "rename")
                    self.row_done.emit(r, True, str(out_ts))
                    self.sound_notify.emit(False)
                except Exception:
                    self.row_done.emit(r, False, str(outp))
                job["_done"] = True
                self._per_row_left.pop(r, None)
                self._emit_overall_eta()
                continue

            ok = self._do_job(
                r,
                mode,
                Path(src),
                Path(outp),
                sec,
                use_cuda,
                indur,
                in_bytes,
                phase,
            )
            if not ok and fallback and mode in (MODE_TRIM_FIRST, MODE_TRIM_LAST, MODE_REMUX_COPY):
                phase_fb = self._phase_for(MODE_TRANSCODE)
                self.row_status.emit(r, f"Fallback • {phase_fb}")
                ok = self._do_job(
                    r,
                    MODE_TRANSCODE,
                    Path(src),
                    Path(outp),
                    sec,
                    use_cuda,
                    indur,
                    in_bytes,
                    phase_fb,
                )

            # Overwrite logic (default) of origineel behouden
            final_out = Path(outp)
            if ok and overwrite:
                try:
                    src_p = Path(src)
                    out_p = Path(outp)
                    if out_p.exists() and out_p.stat().st_size > 0:
                        bak = src_p.with_suffix(src_p.suffix + ".bak")
                        try:
                            if bak.exists():
                                bak.unlink()
                        except Exception:
                            pass
                        if src_p.exists():
                            try:
                                src_p.rename(bak)
                            except Exception:
                                pass
                        out_p.replace(src_p)
                        final_out = src_p
                except Exception:
                    pass

            # Auto-Delete logic (alleen als origineel behouden is)
            if ok and self.delete_source and not overwrite:
                try:
                    src_p = Path(src)
                    out_p = Path(outp)
                    if src_p.exists() and out_p.exists() and src_p.resolve() != out_p.resolve() and out_p.stat().st_size > 0:
                        os.remove(src_p)
                except Exception:
                    pass

            job["_done"] = True
            self._per_row_left.pop(r, None)
            self._emit_overall_eta()
            self.row_done.emit(r, ok, str(final_out) if ok else "Mislukt")
            self.sound_notify.emit(False)

            if r in self.pause_after_row or self.pause_all:
                while self.pause_all and not self.stop_flag:
                    time.sleep(0.2)
                self.pause_after_row.discard(r)

        self.overall_eta.emit("Totale ETA: gereed")
        self.sound_notify.emit(True)

    def _ffmpeg_args(
        self,
        mode: str,
        src: Path,
        outp: Path,
        sec: int,
        use_cuda: bool,
        in_seconds: float,
        in_bytes: int,
    ) -> List[str]:
        video_bps = 4_000_000
        audio_bps = 192_000

        # Normaliseer en valideer invoer
        try:
            in_seconds = float(in_seconds or 0.0)
        except Exception:
            in_seconds = 0.0

        try:
            in_bytes = int(in_bytes or 0)
        except Exception:
            in_bytes = 0

        try:
            sec = int(sec or 0)
        except Exception:
            sec = 0
        if sec < 0:
            sec = 0

        # Clamp sec als die buiten de duur valt
        if in_seconds > 0.0 and sec > in_seconds:
            orig_sec = sec
            sec = max(0, int(in_seconds) - 1)
            print(
                f"[WARN] _ffmpeg_args: seconds={orig_sec} > in_seconds={in_seconds:.2f} "
                f"gecorrigeerd naar {sec}s"
            )

        # Schatting van bitrate op basis van bestandsgrootte en duur
        if in_seconds > 0 and in_bytes > 0:
            total_bps = (in_bytes * 8.0) / max(in_seconds, 1.0)
            target_total = total_bps * 0.8
            audio_bps = min(192_000, int(target_total * 0.25))
            video_bps = max(300_000, int(target_total - audio_bps))

        # Gemeenschappelijke prefix voor alle ffmpeg-calls
        common_prefix = [
            self.ffmpeg,
            "-hide_banner",
            "-fflags", "+discardcorrupt",
            "-err_detect", "ignore_err",
        ]

        # 1) Remux copy
        if mode == MODE_REMUX_COPY:
            return [
                *common_prefix,
                "-y",
                "-i", str(src),
                "-c", "copy",
                "-progress", "pipe:1",
                "-nostats",
                "-loglevel", "error",
                str(outp),
            ]

        # 2) Volledige transcode (kwaliteit stabiliseren)
        if mode == MODE_TRANSCODE:
            if use_cuda:
                vcodec = [
                    "-c:v", "h264_nvenc",
                    "-preset", "p4",
                    "-rc", "vbr",
                    "-b:v", str(video_bps),
                    "-maxrate", str(video_bps),
                    "-bufsize", str(video_bps * 2),
                ]
            else:
                vcodec = [
                    "-c:v", "libx264",
                    "-preset", "veryfast",
                    "-b:v", str(video_bps),
                    "-maxrate", str(video_bps),
                    "-bufsize", str(video_bps * 2),
                ]

            return [
                *common_prefix,
                "-y",
                "-i", str(src),
                *vcodec,
                "-c:a", "aac",
                "-b:a", str(audio_bps),
                "-movflags", "+faststart",
                "-progress", "pipe:1",
                "-nostats",
                "-loglevel", "error",
                str(outp),
            ]

        # 3) Eerste X seconden wegknippen (trim-from-start)
        if mode == MODE_TRIM_FIRST:
            return [
                *common_prefix,
                "-y",
                "-ss", f"{sec}",
                "-i", str(src),
                "-c", "copy",
                "-progress", "pipe:1",
                "-nostats",
                "-loglevel", "error",
                str(outp),
            ]

        # 4) Laatste X seconden wegknippen (trim-from-end)
        if mode == MODE_TRIM_LAST:
            keep = max(0.0, float(in_seconds) - sec)

            # Als er praktisch niets overblijft, maak een heel kort zwart dummy-bestand
            if keep <= 0.05:
                return [
                    *common_prefix,
                    "-y",
                    "-f", "lavfi",
                    "-i", "color=c=black:s=640x360:d=0.1",
                    "-f", "lavfi",
                    "-i", "anullsrc=r=48000:cl=stereo",
                    "-shortest",
                    "-c:v", "libx264",
                    "-crf", "28",
                    "-c:a", "aac",
                    "-b:a", "96000",
                    "-movflags", "+faststart",
                    str(outp),
                ]

            return [
                *common_prefix,
                "-y",
                "-t", f"{keep:.3f}",
                "-i", str(src),
                "-c", "copy",
                "-progress", "pipe:1",
                "-nostats",
                "-loglevel", "error",
                str(outp),
            ]

        # 5) Roll-first: begin naar achter schuiven
        if mode == MODE_ROLL_FIRST:
            fc = (
                f"[0:v]split[v0][v1];"
                f"[v0]trim=0:{sec},setpts=PTS-STARTPTS[vA];"
                f"[v1]trim={sec}:,setpts=PTS-STARTPTS[vB];"
                f"[0:a]asplit[a0][a1];"
                f"[a0]atrim=0:{sec},asetpts=PTS-STARTPTS[aA];"
                f"[a1]atrim={sec}:,asetpts=PTS-STARTPTS[aB];"
                f"[vB][aB][vA][aA]concat=n=2:v=1:a=1[v][a]"
            )

            if use_cuda:
                vcodec = [
                    "-c:v", "h264_nvenc",
                    "-preset", "p4",
                    "-rc", "vbr",
                    "-b:v", str(video_bps),
                    "-maxrate", str(video_bps),
                    "-bufsize", str(video_bps * 2),
                ]
            else:
                vcodec = [
                    "-c:v", "libx264",
                    "-preset", "veryfast",
                    "-b:v", str(video_bps),
                    "-maxrate", str(video_bps),
                    "-bufsize", str(video_bps * 2),
                ]

            return [
                *common_prefix,
                "-y",
                "-i", str(src),
                "-filter_complex", fc,
                "-map", "[v]",
                "-map", "[a]",
                *vcodec,
                "-c:a", "aac",
                "-b:a", str(audio_bps),
                "-movflags", "+faststart",
                "-progress", "pipe:1",
                "-nostats",
                "-loglevel", "error",
                str(outp),
            ]

        # Onbekende modus
        print(f"[DEBUG] _ffmpeg_args: onbekende mode={mode!r} voor {src}")
        return []


    def _do_job(
        self,
        row: int,
        mode: str,
        src: Path,
        outp: Path,
        sec: int,
        use_cuda: bool,
        in_seconds: float,
        in_bytes: int,
        phase: str,
    ) -> bool:
        try:
            args = self._ffmpeg_args(mode, src, outp, sec, use_cuda, in_seconds, in_bytes)
            if not args:
                return False

            proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            self._current_proc = proc

            last_pct = -1
            duration = max(0.001, float(in_seconds))
            out_time = 0.0
            speed_x = 1.0

            for raw in proc.stdout or []:
                if self.stop_flag:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    break

                while row in getattr(self, "_row_live_paused", set()) and not self.stop_flag:
                    self._per_row_left[row] = max(0.0, (duration - out_time))
                    self._emit_overall_eta()
                    time.sleep(0.2)
                    if self._current_proc and self._current_proc.poll() is not None:
                        break

                line = raw.strip()
                if not line:
                    continue

                if line.startswith("out_time_ms="):
                    try:
                        out_time = float(line.split("=", 1)[1]) / 1_000_000.0
                    except Exception:
                        pass
                elif line.startswith("out_time="):
                    out_time = parse_hhmmss_to_seconds(line.split("=", 1)[1])
                elif line.startswith("speed="):
                    try:
                        val = line.split("=", 1)[1].rstrip("x")
                        speed_x = float(val) if val not in ("N/A", "inf") else 1.0
                    except Exception:
                        speed_x = 1.0
                elif line == "progress=end":
                    self.row_status.emit(row, f"Finaliseren • {phase}")

                if duration > 0:
                    pct = int(max(0.0, min(1.0, out_time / duration)) * 100)
                    eff = speed_x if speed_x > 0 else 1.0
                    sec_left = max(0.0, (duration - out_time) / eff)
                    self._per_row_left[row] = sec_left
                    self._emit_overall_eta()
                    if pct != last_pct:
                        self.row_progress.emit(row, pct, fmt_hms(sec_left), phase)
                        last_pct = pct

            exit_code = proc.wait()
            self._current_proc = None
            return exit_code == 0

        except Exception:
            self._current_proc = None
            return False


# ------------------------------ UI widgets ------------------------------
class DropTable(QtWidgets.QTableWidget):
    files_dropped = QtCore.Signal(list)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.setAcceptDrops(True)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers) # Dubbelklik handled by slot
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(42)
        self.setWordWrap(False)
        self.setTextElideMode(QtCore.Qt.ElideRight)
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.setItemDelegate(ForceWhiteDelegate(self))
        self.setShowGrid(False)

        h = self.horizontalHeader()
        h.setSectionsMovable(True)
        h.setHighlightSections(False)
        h.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        h.setTextElideMode(QtCore.Qt.ElideRight)
        h.setStretchLastSection(False)
        h.setSectionsClickable(True)
        h.setMinimumSectionSize(28)

        self.verticalScrollBar().setStyleSheet(
            """
            QScrollBar:vertical {
                border: none;
                background: #1E1E1E;
                width: 10px;
                margin: 0px 0px 0px 0px;
            }
            QScrollBar::handle:vertical {
                background: #444;
                min-height: 20px;
                border-radius: 5px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            """
        )

    def dragEnterEvent(self, e: QtGui.QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dropEvent(self, e: QtGui.QDropEvent):
        paths, dirs = [], []
        for u in e.mimeData().urls():
            p = Path(u.toLocalFile())
            if p.is_dir():
                dirs.append(p)
            elif p.suffix.lower() in VIDEO_EXTS:
                paths.append(p)
        if paths:
            self.files_dropped.emit(paths)
        if dirs:
            self.files_dropped.emit(dirs)
        e.acceptProposedAction()


class LoadingOverlay(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self._active = False
        self._pct = 0
        self._eta = "—"

        self._label = QtWidgets.QLabel("Bestanden inladen...", self)
        self._bar = QtWidgets.QProgressBar(self)
        self._bar.setRange(0, 100)
        self._bar.setStyleSheet("QProgressBar{border:none; background:#333; height:8px; border-radius:4px;} QProgressBar::chunk{background:#00B4D8; border-radius:4px;}")

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addStretch(1)

        box = QtWidgets.QFrame(self)
        box.setStyleSheet(
            "QFrame{background:rgba(20,20,20,0.9); border: 1px solid #444; border-radius:16px;}"
        )
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(24, 20, 24, 20)
        self._label.setStyleSheet(
            "color:white;font-size:15px;font-weight:600;margin-bottom:8px;"
        )
        self._eta_lbl = QtWidgets.QLabel("ETA: —", box)
        self._eta_lbl.setStyleSheet("color:#9DD9F3; margin-top:4px;")
        v.addWidget(self._label, 0, QtCore.Qt.AlignCenter)
        v.addWidget(self._bar)
        v.addWidget(self._eta_lbl, 0, QtCore.Qt.AlignCenter)

        lay.addWidget(box, 0, QtCore.Qt.AlignHCenter)
        lay.addStretch(2)
        self.hide()

    def start(self, label: str = "Bestanden inladen..."):
        self._active = True
        self._pct = 0
        self._eta = "—"
        self._label.setText(label)
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._eta_lbl.setText("ETA: —")
        self.show()

    def start_busy(self, label: str = "Map scannen..."):
        self._active = True
        self._label.setText(label)
        self._bar.setRange(0, 0)
        self._eta_lbl.setText("")
        self.show()

    def set_message(self, text: str):
        if self._active:
            self._label.setText(text)

    def stop(self):
        self._active = False
        self.hide()

    def update_state(self, pct: int, eta_text: str):
        if not self._active:
            return
        if self._bar.minimum() == 0 and self._bar.maximum() == 0:
            return
        self._pct = int(max(0, min(100, pct)))
        self._eta = eta_text
        self._bar.setValue(self._pct)
        self._eta_lbl.setText(f"ETA: {eta_text}")


class StartupSplashDialog(QtWidgets.QDialog):
    def __init__(self, app_title: str):
        super().__init__(None)
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.Dialog)
        self.setModal(True)
        self.setFixedSize(560, 220)
        if APP_ICON_PATH.exists():
            self.setWindowIcon(QtGui.QIcon(str(APP_ICON_PATH)))

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(10)

        self.setStyleSheet(
            """
            QDialog {
                background: #0F141A;
                border: 1px solid #2A3A48;
                border-radius: 14px;
            }
            QLabel#Title {
                color: #EAF7FF;
                font-size: 24px;
                font-weight: 700;
            }
            QLabel#Sub {
                color: #99B8C8;
                font-size: 12px;
            }
            QProgressBar {
                border: 1px solid #304250;
                border-radius: 10px;
                background: rgba(17,24,32,0.7);
                height: 18px;
                color: #D7F2FF;
                text-align: center;
            }
            QProgressBar::chunk {
                border-radius: 9px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                            stop:0 #00A2C4, stop:1 #00D4FF);
            }
            """
        )

        self._video = None
        self._loop_timer = None
        self._has_video = False
        if STARTUP_VIDEO.exists() and vlc is not None:
            try:
                self._video = VLCVideo(self, force_software_decoding=False, aspect="16:9")
                self._video.setFixedHeight(88)
                self._video.setStyleSheet("background-color: #000000; border-radius: 8px;")
                self._video.set_media_load(str(STARTUP_VIDEO), start_paused=False)
                self._video.set_rate(2.0)
                self._video.set_volume_percent(0)
                self._loop_timer = QtCore.QTimer(self)
                self._loop_timer.setInterval(250)
                self._loop_timer.timeout.connect(self._loop_video)
                self._loop_timer.start()
                self._has_video = True
            except Exception:
                self._video = None
                self._loop_timer = None
                self._has_video = False

        lbl_title = QtWidgets.QLabel(app_title)
        lbl_title.setObjectName("Title")
        lbl_title.setAlignment(QtCore.Qt.AlignHCenter)
        lbl_sub = QtWidgets.QLabel("Laden...")
        lbl_sub.setObjectName("Sub")
        lbl_sub.setAlignment(QtCore.Qt.AlignHCenter)
        self._lbl_state = lbl_sub

        self._bar = QtWidgets.QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setFixedHeight(18)

        root.addStretch(2)
        root.addWidget(lbl_title, 0, QtCore.Qt.AlignHCenter)
        root.addSpacing(18)
        root.addWidget(self._bar)
        root.addSpacing(6)
        root.addWidget(lbl_sub, 0, QtCore.Qt.AlignHCenter)
        root.addStretch(2)

    def update_progress(self, pct: int, state_text: str):
        self._bar.setValue(max(0, min(100, int(pct))))
        self._lbl_state.setText(state_text)
        QtWidgets.QApplication.processEvents()
        if pct >= 100:
            self.close()

    def _loop_video(self):
        if not self._video or not self._video.is_available():
            return
        try:
            length_ms = self._video.get_length_ms()
            if length_ms > 0 and self._video.get_time_ms() >= max(0, length_ms - 120):
                self.close()
        except Exception:
            pass

    def has_video(self) -> bool:
        return self._has_video

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        try:
            if self._loop_timer:
                self._loop_timer.stop()
        except Exception:
            pass
        try:
            if self._video:
                self._video.stop()
        except Exception:
            pass
        super().closeEvent(event)


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent: "Main"):
        super().__init__(parent)
        self.setWindowTitle("Instellingen")
        self.resize(500, 350)
        self.parent = parent
        s = parent.settings

        self.setStyleSheet("QDialog { background: #1E1E1E; color: #FFF; } QLabel{color:#EEE;} QCheckBox{color:#EEE;}")

        root = QtWidgets.QVBoxLayout(self)

        gb_gen = QtWidgets.QGroupBox("Algemeen & Verwerking")
        gb_gen.setStyleSheet("QGroupBox{border:1px solid #444; border-radius:8px; margin-top:10px; font-weight:bold; color:#00B4D8;} QGroupBox::title{subcontrol-origin: margin; left: 10px; padding: 0 5px;}")
        form_gen = QtWidgets.QFormLayout(gb_gen)

        self.spin_secs = QtWidgets.QSpinBox()
        self.spin_secs.setRange(0, 3600)
        self.spin_secs.setValue(int(s.value("prefs/default_secs", 1)))
        self.spin_secs.setStyleSheet("background:#2C2C2C; color:white; border:none; padding:4px;")

        self.chk_cuda = QtWidgets.QCheckBox("Gebruik CUDA (NVENC hardware versnelling)")
        self.chk_cuda.setChecked(bool(int(s.value("prefs/cuda", 1))))

        self.chk_fallback = QtWidgets.QCheckBox(
            "Auto-fallback (probeer CPU als GPU faalt)"
        )
        self.chk_fallback.setChecked(bool(int(s.value("prefs/fallback", 1))))

        self.chk_overwrite = QtWidgets.QCheckBox("Originele bestanden overschrijven")
        self.chk_overwrite.setToolTip("Maakt een .bak en vervangt het origineel (uit = behouden)")
        self.chk_overwrite.setChecked(bool(int(s.value("prefs/overwrite", 1))))

        self.chk_ts_rename = QtWidgets.QCheckBox(".ts naar .mp4: alleen hernoemen (snel)")
        self.chk_ts_rename.setChecked(bool(int(s.value("prefs/ts_rename_only", 1))))

        # NEW OPTIONS
        self.chk_del_source = QtWidgets.QCheckBox("⚠️ Origineel verwijderen na succes")
        self.chk_del_source.setChecked(bool(int(s.value("prefs/delete_source", 0))))
        self.chk_del_source.setStyleSheet("color: #FF5555; font-weight: bold;")

        self.chk_auto_clear = QtWidgets.QCheckBox("✅ Lijst opschonen na succes")
        self.chk_auto_clear.setChecked(bool(int(s.value("prefs/auto_clear_list", 0))))

        form_gen.addRow("Standaard tijd (s):", self.spin_secs)
        form_gen.addRow(self.chk_cuda)
        form_gen.addRow(self.chk_fallback)
        form_gen.addRow(self.chk_overwrite)
        form_gen.addRow(self.chk_ts_rename)
        form_gen.addRow(self.chk_del_source)
        form_gen.addRow(self.chk_auto_clear)
        root.addWidget(gb_gen)

        gb_play = QtWidgets.QGroupBox("Speler & Paden")
        gb_play.setStyleSheet(gb_gen.styleSheet())
        form_play = QtWidgets.QFormLayout(gb_play)

        self.cmb_aspect = QtWidgets.QComboBox()
        self.cmb_aspect.addItems(["Auto", "16:9", "4:3", "21:9", "1:1"])
        self.cmb_aspect.setCurrentText(s.value("video/aspect", "16:9"))
        self.cmb_aspect.setStyleSheet("background:#2C2C2C; color:white; border:none; padding:4px;")

        self.ed_drive_art = QtWidgets.QLineEdit()
        self.ed_drive_art.setPlaceholderText("Bijv. D:/ART")
        self.ed_drive_art.setText(s.value("ui/drive_art", ""))
        self.ed_drive_art.setStyleSheet("background:#2C2C2C; color:white; border:none; padding:4px;")

        form_play.addRow("Aspect Ratio:", self.cmb_aspect)
        form_play.addRow("Drive ART Map:", self.ed_drive_art)

        root.addWidget(gb_play)

        hbox_btns = QtWidgets.QHBoxLayout()
        self.btn_save = QtWidgets.QPushButton("Opslaan & Sluiten")
        self.btn_save.setStyleSheet("QPushButton{background-color:#00B4D8; color:black; font-weight: bold; padding: 8px; border-radius:6px;} QPushButton:hover{background-color:#48CAE4;}")
        hbox_btns.addStretch()
        hbox_btns.addWidget(self.btn_save)
        root.addLayout(hbox_btns)

        self.btn_save.clicked.connect(self.apply)

    def apply(self):
        s = self.parent.settings
        s.setValue("prefs/default_secs", self.spin_secs.value())
        s.setValue("prefs/cuda", 1 if self.chk_cuda.isChecked() else 0)
        s.setValue("prefs/fallback", 1 if self.chk_fallback.isChecked() else 0)
        s.setValue("prefs/overwrite", 1 if self.chk_overwrite.isChecked() else 0)
        s.setValue("prefs/ts_rename_only", 1 if self.chk_ts_rename.isChecked() else 0)
        s.setValue("prefs/delete_source", 1 if self.chk_del_source.isChecked() else 0)
        s.setValue("prefs/auto_clear_list", 1 if self.chk_auto_clear.isChecked() else 0)

        s.setValue("video/aspect", self.cmb_aspect.currentText())
        s.setValue("ui/drive_art", self.ed_drive_art.text().strip())
        self.accept()


# ------------------------------ thumbnails ------------------------------
class ThumbnailWorker(QtCore.QThread):
    thumb_ready = QtCore.Signal(str, QtGui.QIcon)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.queue: List[str] = []
        self._running = True
        self._mutex = QtCore.QMutex()
        self._cond = QtCore.QWaitCondition()

    def add_path(self, path: str):
        if not HAS_OPENCV:
            return
        with QtCore.QMutexLocker(self._mutex):
            if path not in self.queue:
                self.queue.append(path)
                self._cond.wakeOne()

    def stop(self):
        self._running = False
        with QtCore.QMutexLocker(self._mutex):
            self._cond.wakeOne()
        self.wait()

    def run(self):
        if not HAS_OPENCV:
            return
        while self._running:
            with QtCore.QMutexLocker(self._mutex):
                while not self.queue and self._running:
                    self._cond.wait(self._mutex)
                if not self._running:
                    return
                path = self.queue.pop(0)
            if not path:
                continue
            try:
                backend = cv2.CAP_FFMPEG if os.name == 'nt' else cv2.CAP_ANY
                cap = cv2.VideoCapture(path, backend)
                if not cap.isOpened():
                    cap = cv2.VideoCapture(path)

                cap.set(cv2.CAP_PROP_POS_FRAMES, 20)
                ok, frame = cap.read()
                cap.release()
                if not ok or frame is None:
                    continue
                h, w = frame.shape[:2]
                if w <= 0 or h <= 0:
                    continue
                target_w = 240
                scale = target_w / float(w)
                frame = cv2.resize(frame, (target_w, int(h * scale)))
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = QtGui.QImage(
                    frame.data,
                    frame.shape[1],
                    frame.shape[0],
                    frame.strides[0],
                    QtGui.QImage.Format_RGB888,
                )
                icon = QtGui.QIcon(QtGui.QPixmap.fromImage(img))
                self.thumb_ready.emit(path, icon)
            except Exception:
                pass


class ThumbnailList(QtWidgets.QListWidget):
    files_dropped = QtCore.Signal(list)
    order_changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QtWidgets.QListWidget.IconMode)
        self.setResizeMode(QtWidgets.QListWidget.Adjust)
        self.setMovement(QtWidgets.QListWidget.Snap)
        self.setSpacing(12)

        self.setIconSize(QtCore.QSize(160, 90))
        self.setGridSize(QtCore.QSize(180, 120))

        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDrop)
        self.setDefaultDropAction(QtCore.Qt.MoveAction)

        self.setStyleSheet(
            """
            QListWidget{
                background-color: transparent;
                border: none;
            }
            QListWidget::item {
                border-radius: 6px;
                padding: 4px;
                color: #EEE;
            }
            QListWidget::item:selected {
                background-color: rgba(0, 180, 216, 0.2);
                border: 1px solid #00B4D8;
            }
            QListWidget::item:hover {
                background-color: rgba(255, 255, 255, 0.05);
            }
            """
        )

    def dragEnterEvent(self, e: QtGui.QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        elif e.source() == self:
            e.accept()
            super().dragEnterEvent(e)
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e: QtGui.QDragMoveEvent):
        if e.source() == self:
            e.setDropAction(QtCore.Qt.MoveAction)
            e.accept()
            super().dragMoveEvent(e)
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e: QtGui.QDropEvent):
        if e.source() == self:
            e.setDropAction(QtCore.Qt.MoveAction)
            super().dropEvent(e)
            self.order_changed.emit()
        elif e.mimeData().hasUrls():
            paths, dirs = [], []
            for u in e.mimeData().urls():
                p = Path(u.toLocalFile())
                if p.is_dir():
                    dirs.append(p)
                elif p.suffix.lower() in VIDEO_EXTS:
                    paths.append(p)
            if paths:
                self.files_dropped.emit(paths)
            if dirs:
                self.files_dropped.emit(dirs)
            e.acceptProposedAction()
        else:
            super().dropEvent(e)


class ThumbnailPickerDialog(QtWidgets.QDialog):
    thumbnail_applied = QtCore.Signal(str, QtGui.QImage)

    def __init__(self, parent, paths: List[str], current_path: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle("Thumbnail Editor")
        self.setWindowFlags(
            QtCore.Qt.Window
            | QtCore.Qt.WindowMinimizeButtonHint
            | QtCore.Qt.WindowCloseButtonHint
        )
        self.resize(1320, 760)
        self._paths = paths
        self._selected_preview: Optional[QtGui.QImage] = None

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        left_box = QtWidgets.QFrame()
        left_box.setMinimumWidth(320)
        left_lay = QtWidgets.QVBoxLayout(left_box)
        left_lay.addWidget(QtWidgets.QLabel("Bestanden"))
        self.file_list = QtWidgets.QListWidget()
        left_lay.addWidget(self.file_list, 1)
        for p in self._paths:
            it = QtWidgets.QListWidgetItem(Path(p).name)
            it.setToolTip(p)
            it.setData(QtCore.Qt.UserRole, p)
            self.file_list.addItem(it)
        root.addWidget(left_box, 2)

        center_box = QtWidgets.QFrame()
        center_lay = QtWidgets.QVBoxLayout(center_box)
        center_lay.addWidget(QtWidgets.QLabel("Geselecteerde thumbnail"))
        self.preview = QtWidgets.QLabel("Geen selectie")
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        self.preview.setMinimumSize(420, 260)
        self.preview.setStyleSheet("QLabel{background:#0F141A; border:1px solid #2A3945; border-radius:10px;}")
        center_lay.addWidget(self.preview, 1)
        row_btn = QtWidgets.QHBoxLayout()
        self.btn_apply = QtWidgets.QPushButton("Opslaan")
        self.btn_apply.setEnabled(False)
        self.btn_close = QtWidgets.QPushButton("Sluiten")
        row_btn.addWidget(self.btn_apply)
        row_btn.addWidget(self.btn_close)
        row_btn.addStretch(1)
        center_lay.addLayout(row_btn)
        root.addWidget(center_box, 3)

        right_box = QtWidgets.QFrame()
        right_lay = QtWidgets.QVBoxLayout(right_box)
        right_lay.addWidget(QtWidgets.QLabel("Miniaturen uit eerste minuut"))
        self.candidates = QtWidgets.QListWidget()
        self.candidates.setViewMode(QtWidgets.QListWidget.IconMode)
        self.candidates.setResizeMode(QtWidgets.QListWidget.Adjust)
        self.candidates.setMovement(QtWidgets.QListWidget.Static)
        self.candidates.setSpacing(8)
        self.candidates.setIconSize(QtCore.QSize(220, 124))
        self.candidates.setGridSize(QtCore.QSize(240, 170))
        self.candidates.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        right_lay.addWidget(self.candidates, 1)
        root.addWidget(right_box, 4)

        self.file_list.currentItemChanged.connect(self._on_file_changed)
        self.candidates.itemClicked.connect(self._on_candidate_clicked)
        self.btn_apply.clicked.connect(self._apply_clicked)
        self.btn_close.clicked.connect(self.reject)

        if self.file_list.count() > 0:
            idx = 0
            if current_path:
                for i in range(self.file_list.count()):
                    if self.file_list.item(i).data(QtCore.Qt.UserRole) == current_path:
                        idx = i
                        break
            self.file_list.setCurrentRow(idx)

    def selected_path(self) -> Optional[str]:
        it = self.file_list.currentItem()
        return it.data(QtCore.Qt.UserRole) if it else None

    def selected_image(self) -> Optional[QtGui.QImage]:
        return self._selected_preview

    def _set_preview(self, image: QtGui.QImage):
        self._selected_preview = image
        pix = QtGui.QPixmap.fromImage(image)
        shown = pix.scaled(
            self.preview.size(),
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
        self.preview.setPixmap(shown)
        self.btn_apply.setEnabled(True)

    def _read_frame_at(self, path: str, sec: float) -> Optional[QtGui.QImage]:
        if not HAS_OPENCV or cv2 is None:
            return None
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return None
        try:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, sec) * 1000.0)
            ok, frame = cap.read()
        finally:
            cap.release()
        if not ok or frame is None:
            return None
        h, w = frame.shape[:2]
        if w <= 0 or h <= 0:
            return None
        target_w = 320
        target_h = max(1, int(target_w * h / w))
        frame = cv2.resize(frame, (target_w, target_h))
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return QtGui.QImage(
            frame.data,
            frame.shape[1],
            frame.shape[0],
            frame.strides[0],
            QtGui.QImage.Format_RGB888,
        ).copy()

    def _on_file_changed(self, cur: Optional[QtWidgets.QListWidgetItem], _prev: Optional[QtWidgets.QListWidgetItem]):
        self.candidates.clear()
        self.preview.clear()
        self.preview.setText("Geen selectie")
        self.btn_apply.setEnabled(False)
        self._selected_preview = None
        if not cur:
            return
        path = cur.data(QtCore.Qt.UserRole)
        if not path:
            return
        marks = [0, 5, 10, 15, 20, 30, 40, 50, 60]
        added = 0
        for sec in marks:
            img = self._read_frame_at(path, sec)
            if img is None:
                continue
            icon = QtGui.QIcon(QtGui.QPixmap.fromImage(img))
            it = QtWidgets.QListWidgetItem(icon, f"{sec}s")
            it.setData(QtCore.Qt.UserRole, img)
            self.candidates.addItem(it)
            added += 1
        if added == 0:
            self.preview.setText("Geen miniaturen gevonden")
            return
        self.candidates.setCurrentRow(0)
        first = self.candidates.item(0)
        if first:
            self._on_candidate_clicked(first)

    def _on_candidate_clicked(self, item: QtWidgets.QListWidgetItem):
        img = item.data(QtCore.Qt.UserRole)
        if isinstance(img, QtGui.QImage):
            self._set_preview(img)

    def _apply_clicked(self):
        path = self.selected_path()
        img = self.selected_image()
        if not path or img is None:
            return
        self.thumbnail_applied.emit(path, img)


# ------------------------------ main ui ------------------------------
class Main(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        if APP_ICON_PATH.exists():
            self.setWindowIcon(QtGui.QIcon(str(APP_ICON_PATH)))

        # Undo / Redo stacks (slaan volledige lijsten op als snapshot)
        self.undo_stack: List[List[Dict[str, Any]]] = []
        self.redo_stack: List[List[Dict[str, Any]]] = []

        screen = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        w, h = screen.width() * 0.85, screen.height() * 0.85
        self.resize(int(w), int(h))
        self.move(screen.center() - self.rect().center())

        self.settings = QtCore.QSettings(SETTINGS_SCOPE, APP_NAME)
        self.last_dir = self.settings.value("ui/last_dir", str(Path.home()))
        self.last_session_dir = self.settings.value("ui/last_session_dir", str(Path.home()))
        self.drive_art = self.settings.value("ui/drive_art", "")
        self.custom_thumbs: Dict[str, str] = {}
        self._thumb_editors: List[ThumbnailPickerDialog] = []

        self._theme_name = self.settings.value("ui/theme", DEFAULT_THEME)
        if self._theme_name not in THEMES:
            self._theme_name = DEFAULT_THEME

        # --- Top Bar ---
        self.btn_add = QtWidgets.QPushButton("➕ Bestand")
        self.btn_add.setToolTip("Bestand toevoegen")
        self.btn_add_dir = QtWidgets.QPushButton("📂 Map")
        self.btn_add_dir.setToolTip("Map toevoegen")
        self.btn_refresh = QtWidgets.QPushButton("Refresh")
        self._set_btn_icon(self.btn_refresh, QStyle.SP_BrowserReload)
        self.btn_refresh.setToolTip("Lijst verversen")
        self.btn_autofit = QtWidgets.QPushButton("Autofit")
        self._set_btn_icon(self.btn_autofit, QStyle.SP_TitleBarShadeButton)
        self.btn_autofit.setToolTip("Kolommen automatisch passend maken")
        self.btn_remove = QtWidgets.QPushButton("🗑 Verwijder")
        self.btn_remove.setToolTip("Geselecteerde verwijderen")
        self.btn_clear = QtWidgets.QPushButton("🧹 Lijst leeg")
        self.btn_clear.setToolTip("Hele lijst leegmaken")
        for b in (self.btn_add_dir, self.btn_refresh, self.btn_remove, self.btn_clear):
            b.setMinimumWidth(120)

        # Undo/Redo knoppen
        self.btn_undo = QtWidgets.QPushButton("Undo")
        self._set_btn_icon(self.btn_undo, QStyle.SP_ArrowBack)
        self.btn_undo.setToolTip("Ongedaan maken (Ctrl+Z)")
        self.btn_undo.setEnabled(False)
        self.btn_redo = QtWidgets.QPushButton("Redo")
        self._set_btn_icon(self.btn_redo, QStyle.SP_ArrowForward)
        self.btn_redo.setToolTip("Opnieuw uitvoeren (Ctrl+Y)")
        self.btn_redo.setEnabled(False)

        self.lbl_search = QtWidgets.QLabel("Zoek:")
        self.ed_filter = QtWidgets.QLineEdit()
        self.ed_filter.setPlaceholderText("Filter naam...")

        self.spin_secs = QtWidgets.QSpinBox()
        self.spin_secs.setRange(0, 3600)
        self.spin_secs.setValue(
            int(self.settings.value("prefs/default_secs", 0))
        )
        self.spin_secs.setSuffix(" s")
        self.spin_secs.setToolTip("Standaard seconden voor nieuwe items")

        self.btn_apply = QtWidgets.QPushButton("Toepassen")
        self.btn_apply.setToolTip("Pas seconden toe op geselecteerde regels")

        # Intro Detect Button
        self.btn_detect = QtWidgets.QPushButton("Intro detect")
        self._set_btn_icon(self.btn_detect, QStyle.SP_BrowserReload)
        self.btn_detect.setToolTip("Zoek automatisch naar intro (zwart beeld) in geselecteerde bestanden")
        self.btn_thumb_edit = QtWidgets.QPushButton("📋 Thumbnail Editor")
        self.btn_thumb_edit.setToolTip("Thumbnail handmatig kiezen (eerste minuut)")

        self.cmb_mode = QtWidgets.QComboBox()
        self.cmb_mode.addItems(
            [
                MODE_TRIM_FIRST,
                MODE_TRIM_LAST,
                MODE_ROLL_FIRST,
                MODE_REMUX_COPY,
                MODE_TRANSCODE,
            ]
        )
        self.chk_cuda = QtWidgets.QCheckBox("CUDA")
        self.chk_cuda.setChecked(bool(int(self.settings.value("prefs/cuda", 1))))
        self.chk_overwrite = QtWidgets.QCheckBox("Origineel overschrijven")
        self.chk_overwrite.setToolTip("Maakt een .bak en vervangt het origineel (uit = behouden)")
        self.chk_overwrite.setChecked(
            bool(int(self.settings.value("prefs/overwrite", 1)))
        )
        self.chk_fallback = QtWidgets.QCheckBox("Fallback")
        self.chk_fallback.setToolTip("Auto fallback naar CPU als GPU faalt")
        self.chk_fallback.setChecked(
            bool(int(self.settings.value("prefs/fallback", 1)))
        )

        self.btn_start = QtWidgets.QPushButton("Start")
        self._set_btn_icon(self.btn_start, QStyle.SP_MediaPlay)
        self.btn_start.setToolTip("Start batch")
        self.btn_start.setMinimumHeight(38)
        self.btn_start.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_start.setProperty("accent", True)

        self.btn_pause_all = QtWidgets.QPushButton("Pauze")
        self._set_btn_icon(self.btn_pause_all, QStyle.SP_MediaPause)
        self.btn_pause_all.setToolTip("Pauzeer batch")
        self.btn_resume_all = QtWidgets.QPushButton("Hervat")
        self._set_btn_icon(self.btn_resume_all, QStyle.SP_MediaPlay)
        self.btn_resume_all.setToolTip("Hervat batch")
        self.btn_resume_all.setEnabled(False)
        self.btn_stop_all = QtWidgets.QPushButton("Stop")
        self._set_btn_icon(self.btn_stop_all, QStyle.SP_MediaStop)
        self.btn_stop_all.setToolTip("Stop alles")
        self.btn_stop_all.setProperty("danger", True)

        self.lbl_overall = QtWidgets.QLabel("Klaar")
        self.lbl_overall.setStyleSheet("color: #888;")

        self.cmb_aspect = QtWidgets.QComboBox()
        self.cmb_aspect.addItems(["Auto", "16:9", "4:3", "21:9", "1:1"])
        self.cmb_aspect.setCurrentText(
            self.settings.value("video/aspect", "16:9")
        )

        self.table = DropTable(0, len(HEADER), self)
        self.table.setHorizontalHeaderLabels(HEADER)
        self.table.setSortingEnabled(True)  # Sorteren aan
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.overlay = LoadingOverlay(self)

        force_sw = bool(int(self.settings.value("prefs/force_sw", 0)))
        aspect = self.settings.value("video/aspect", "16:9")
        self.video = VLCVideo(
            force_software_decoding=force_sw,
            aspect=aspect if aspect != "Auto" else None,
        )
        self.vc = VideoControlBar()
        self.vc.request_seek.connect(lambda p: self.video.set_position(p))
        self.video.time_changed.connect(self.vc.update_time)
        self.vc.nudge.connect(self.on_nudge)
        self.vc.prev_file.connect(self.on_prev_file)
        self.vc.next_file.connect(self.on_next_file)
        self.vc.toggle_play.connect(self.on_toggle_play)
        self.vc.mute_toggle.connect(self.on_toggle_mute)
        self.vc.volume_level.connect(self.on_set_volume_level)
        self.vc.seek_and_play.connect(
            lambda p: (
                self.video.set_position(p),
                self.on_play(),
                self.vc.set_playing(True),
            )
        )
        # Capture mark logic
        self.vc.capture_mark.connect(self._apply_mark_to_selection)
        start_vol = self.video.get_volume_percent()
        self.vc.vol_slider.blockSignals(True)
        self.vc.vol_slider.setValue(start_vol)
        self.vc.vol_slider.blockSignals(False)
        self.vc.set_muted_state(start_vol <= 0)

        self.thumbs = ThumbnailList()

        # --- Layout Constructie ---
        right_widget = QtWidgets.QWidget()
        right_lay = QtWidgets.QVBoxLayout(right_widget)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)

        vid_container = QtWidgets.QWidget()
        vid_container.setStyleSheet("background-color:black;")
        vc_lay = QtWidgets.QVBoxLayout(vid_container)
        vc_lay.setContentsMargins(0, 0, 0, 0)
        vc_lay.addWidget(self.video)

        right_lay.addWidget(vid_container, 3)
        right_lay.addWidget(self.vc, 0)

        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        line.setStyleSheet("background: #333; margin-top: 5px; margin-bottom: 5px;")
        right_lay.addWidget(line)

        right_lay.addWidget(self.thumbs, 2)

        split = QtWidgets.QSplitter()
        split.setHandleWidth(6)
        split.setChildrenCollapsible(False)
        split.addWidget(self.table)
        split.addWidget(right_widget)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        self.splitter = split

        # Knoppenbalk boven
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        menu = QtWidgets.QMenuBar(self)
        root.setMenuBar(menu)
        self._build_menubar(menu)

        top_container = QtWidgets.QFrame()
        top_container.setObjectName("TopContainer")
        top_layout = QtWidgets.QHBoxLayout(top_container)
        top_layout.setContentsMargins(8, 6, 8, 6)

        # Linker groep
        for w in (self.btn_add, self.btn_add_dir, self.btn_refresh, self.btn_autofit, self.btn_remove, self.btn_clear, self.btn_undo, self.btn_redo):
            top_layout.addWidget(w)

        top_layout.addSpacing(15)
        top_layout.addWidget(self.lbl_search)
        top_layout.addWidget(self.ed_filter)

        # Middengroep
        top_layout.addSpacing(15)
        top_layout.addWidget(QtWidgets.QLabel("Sec:"))
        top_layout.addWidget(self.spin_secs)
        top_layout.addWidget(self.btn_apply)
        top_layout.addWidget(self.btn_detect)  # Intro detect knop
        top_layout.addWidget(self.btn_thumb_edit)

        # Rechter groep
        top_layout.addStretch(1)
        top_layout.addWidget(self.cmb_mode)
        top_layout.addWidget(self.chk_cuda)

        # Start groep
        top_layout.addSpacing(10)
        top_layout.addWidget(self.btn_start)
        top_layout.addWidget(self.btn_pause_all)
        top_layout.addWidget(self.btn_resume_all)
        top_layout.addWidget(self.btn_stop_all)

        # Info
        top_layout.addSpacing(10)
        top_layout.addWidget(self.lbl_overall)

        root.addWidget(top_container)
        root.addWidget(split, 1)

        # Sort bar onderaan
        self.sortbar = QtWidgets.QHBoxLayout()
        self.btn_sort_name = QtWidgets.QToolButton()
        self.btn_sort_name.setText("Naam")
        self._set_tool_icon(self.btn_sort_name, QStyle.SP_ArrowUp)
        self.btn_sort_dir = QtWidgets.QToolButton()
        self.btn_sort_dir.setText("Map")
        self._set_tool_icon(self.btn_sort_dir, QStyle.SP_DirOpenIcon)
        self.btn_sort_date = QtWidgets.QToolButton()
        self.btn_sort_date.setText("Datum")
        self._set_tool_icon(self.btn_sort_date, QStyle.SP_FileDialogDetailedView)
        self.btn_sort_size = QtWidgets.QToolButton()
        self.btn_sort_size.setText("Grootte")
        self._set_tool_icon(self.btn_sort_size, QStyle.SP_DriveHDIcon)
        self.btn_sort_type = QtWidgets.QToolButton()
        self.btn_sort_type.setText("Type")
        self._set_tool_icon(self.btn_sort_type, QStyle.SP_FileDialogContentsView)
        self.btn_sort_reset = QtWidgets.QToolButton()
        self.btn_sort_reset.setText("Reset")
        self._set_tool_icon(self.btn_sort_reset, QStyle.SP_BrowserReload)

        self.sortbar.addWidget(QtWidgets.QLabel("Sorteer op:"))
        for b in (
            self.btn_sort_name,
            self.btn_sort_dir,
            self.btn_sort_date,
            self.btn_sort_size,
            self.btn_sort_type,
            self.btn_sort_reset,
        ):
            b.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setStyleSheet("QToolButton{background:transparent; color:#888; border:none; padding:4px 8px;} QToolButton:hover{color:#EEE;}")
            self.sortbar.addWidget(b)
        self.sortbar.addStretch(1)

        lbl_info = QtWidgets.QLabel("Dubbelklik op bestand = Checkbox aan/uit")
        lbl_info.setStyleSheet("color:#666; font-style:italic;")
        self.sortbar.addWidget(lbl_info)

        root.addLayout(self.sortbar)

        self.btn_add.clicked.connect(self.on_browse)
        self.btn_add_dir.clicked.connect(self.on_browse_folder)
        self.btn_refresh.clicked.connect(self.on_refresh_list)
        self.btn_remove.clicked.connect(self.on_remove)
        self.btn_clear.clicked.connect(self.on_clear)
        self.btn_apply.clicked.connect(self.on_apply)
        self.btn_start.clicked.connect(self.on_start)
        self.btn_autofit.clicked.connect(self.autofit_columns)
        self.btn_pause_all.clicked.connect(self.on_pause_all)
        self.btn_resume_all.clicked.connect(self.on_resume_all)
        self.btn_stop_all.clicked.connect(self.on_stop_all)
        self.btn_undo.clicked.connect(self.undo_action)
        self.btn_redo.clicked.connect(self.redo_action)
        self.btn_detect.clicked.connect(self.on_detect_intro)
        self.btn_thumb_edit.clicked.connect(self.on_change_thumbnail)

        self.table.files_dropped.connect(self.add_files)
        self.thumbs.files_dropped.connect(self.add_files)
        self.thumbs.order_changed.connect(self.sync_table_to_thumbs_order)

        # --- SYNC: Klik op thumb -> Selecteer rij ---
        self.thumbs.itemClicked.connect(self._thumb_clicked)

        self.table.customContextMenuRequested.connect(
            self.on_table_context_menu
        )
        self.thumbs.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.thumbs.customContextMenuRequested.connect(self.on_thumbs_context_menu)
        self.cmb_aspect.currentTextChanged.connect(self.on_aspect_changed)
        self.ed_filter.textChanged.connect(self.apply_filter)

        self.table.itemClicked.connect(self._table_click_play)
        self.table.itemDoubleClicked.connect(self._table_dbl_click_toggle)
        self.table.itemChanged.connect(self._on_item_changed)

        self.table.itemSelectionChanged.connect(
            self._sync_sel_table_to_thumbs
        )
        self.thumbs.itemSelectionChanged.connect(
            self._sync_sel_thumbs_to_table
        )
        self.thumbs.itemDoubleClicked.connect(
            self._thumbs_double_clicked
        )

        # Connect header double-click for sorting
        self.table.horizontalHeader().sectionDoubleClicked.connect(
            self._header_double_clicked
        )

        QtGui.QShortcut(
            QtGui.QKeySequence("Ctrl+A"), self, activated=self._select_all_rows
        )
        QtGui.QShortcut(
            QtGui.QKeySequence("Delete"), self, activated=self.on_remove
        )
        # Geen Return shortcut (veiligheid)

        # Navigatie/video-toetsen worden context-afhankelijk afgehandeld in keyPressEvent.
        # Undo/Redo shortcuts
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Z"), self, activated=self.undo_action)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Y"), self, activated=self.redo_action)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Shift+Z"), self, activated=self.redo_action)

        self._sort_state = {"key": None, "asc": True}
        self.btn_sort_name.clicked.connect(lambda: self._sort_table_by("name"))
        self.btn_sort_dir.clicked.connect(lambda: self._sort_table_by("dir"))
        self.btn_sort_date.clicked.connect(lambda: self._sort_table_by("date"))
        self.btn_sort_size.clicked.connect(lambda: self._sort_table_by("size"))
        self.btn_sort_type.clicked.connect(lambda: self._sort_table_by("type"))
        self.btn_sort_reset.clicked.connect(self._sort_table_reset)

        self.apply_theme(self._theme_name)
        self._autofit_start()
        self._did_splitter_fit = False
        self.worker: Optional[BatchWorker] = None

        self.thumb_worker: Optional[ThumbnailWorker] = None
        if HAS_OPENCV:
            self.thumb_worker = ThumbnailWorker(self)
            self.thumb_worker.thumb_ready.connect(self.on_thumb_ready)
            self.thumb_worker.start()

        # Restore previous session
        self._restore_queue()

    # ---------------- Undo / Redo Logic (Snapshot) ----------------
    def save_undo_snapshot(self):
        """Maak een snapshot van de huidige lijst en sla op in undo stack."""
        state = []
        for r in range(self.table.rowCount()):
            it_file = self.table.item(r, COL_FILE)
            it_sec = self.table.item(r, COL_SEC)
            
            # Gebruik custom widget ipv COL_SEL checkState
            wid = self.table.cellWidget(r, COL_SEL)
            checked = wid.isChecked() if wid else False

            if not it_file:
                continue

            path = it_file.toolTip()
            secs = it_sec.text() if it_sec else "0"

            state.append({
                "path": path,
                "secs": secs,
                "checked": checked
            })

        self.undo_stack.append(state)
        self.redo_stack.clear()  # Nieuwe actie wist redo
        self._update_undo_buttons()

    def restore_snapshot(self, state: List[Dict[str, Any]]):
        """Herbouw de tabel volledig vanuit een snapshot."""
        # 1. Tabel leegmaken
        self.table.setRowCount(0)
        self.thumbs.clear()

        # 2. Rebuild
        paths_to_load = []
        for item in state:
            paths_to_load.append(Path(item["path"]))

        if not paths_to_load:
            self._save_queue()
            return

        # Gebruik interne methode om files toe te voegen (zonder undo trigger!)
        self._add_files_internal(paths_to_load, use_overlay=False)

        # 3. Herstel specifieke settings (checkbox, seconden)
        for r in range(self.table.rowCount()):
            if r < len(state):
                setting = state[r]
                # Checkbox via widget
                wid = self.table.cellWidget(r, COL_SEL)
                if wid:
                    wid.setChecked(setting["checked"])

                # Seconden
                s_val = setting["secs"]
                it_sec = self.table.item(r, COL_SEC)
                if it_sec:
                    it_sec.setText(str(s_val))
                it_mmss = self.table.item(r, COL_MMSS)
                if it_mmss:
                    it_mmss.setText(seconds_to_mmss(int(str(s_val).split()[0])))

        self._save_queue()

    def undo_action(self):
        if not self.undo_stack:
            return

        # Huidige staat opslaan naar redo
        current_state = []
        for r in range(self.table.rowCount()):
            it_file = self.table.item(r, COL_FILE)
            it_sec = self.table.item(r, COL_SEC)
            wid = self.table.cellWidget(r, COL_SEL)
            if it_file:
                current_state.append({
                    "path": it_file.toolTip(),
                    "secs": it_sec.text() if it_sec else "0",
                    "checked": wid.isChecked() if wid else False
                })
        self.redo_stack.append(current_state)

        # Vorige staat herstellen
        prev_state = self.undo_stack.pop()
        self.restore_snapshot(prev_state)
        self._update_undo_buttons()

    def redo_action(self):
        if not self.redo_stack:
            return

        # Huidige staat opslaan naar undo
        current_state = []
        for r in range(self.table.rowCount()):
            it_file = self.table.item(r, COL_FILE)
            it_sec = self.table.item(r, COL_SEC)
            wid = self.table.cellWidget(r, COL_SEL)
            if it_file:
                current_state.append({
                    "path": it_file.toolTip(),
                    "secs": it_sec.text() if it_sec else "0",
                    "checked": wid.isChecked() if wid else False
                })
        self.undo_stack.append(current_state)

        # Volgende staat herstellen
        next_state = self.redo_stack.pop()
        self.restore_snapshot(next_state)
        self._update_undo_buttons()

    def _update_undo_buttons(self):
        self.btn_undo.setEnabled(len(self.undo_stack) > 0)
        self.btn_redo.setEnabled(len(self.redo_stack) > 0)

        style_enabled = "QPushButton{background-color: #2C2C2C; color: #EEE;}"
        style_disabled = "QPushButton{background-color: #1A1A1A; color: #555; border: 1px solid #222;}"

        self.btn_undo.setStyleSheet(style_enabled if self.btn_undo.isEnabled() else style_disabled)
        self.btn_redo.setStyleSheet(style_enabled if self.btn_redo.isEnabled() else style_disabled)

    def sync_table_to_thumbs_order(self):
        self.save_undo_snapshot()  # Save before reorder
        new_paths_order = []
        for i in range(self.thumbs.count()):
            it = self.thumbs.item(i)
            path = it.data(QtCore.Qt.UserRole)
            new_paths_order.append(path)

        rows_data = {}
        for r in range(self.table.rowCount()):
            it = self.table.item(r, COL_FILE)
            if it:
                path = it.toolTip()
                row_items = []
                # Checkbox status opslaan omdat widget verloren gaat bij reorder
                wid = self.table.cellWidget(r, COL_SEL)
                is_checked = wid.isChecked() if wid else False
                
                for c in range(self.table.columnCount()):
                    source_it = self.table.item(r, c)
                    row_items.append(QtWidgets.QTableWidgetItem(source_it) if source_it else None)
                
                rows_data[path] = {"items": row_items, "checked": is_checked}

        self.table.setRowCount(0)
        for path in new_paths_order:
            if path in rows_data:
                data = rows_data[path]
                items = data["items"]
                row = self.table.rowCount()
                self.table.insertRow(row)
                
                # Herstel widget
                chk = CenteredCheckBox(self, checked=data["checked"])
                self.table.setCellWidget(row, COL_SEL, chk)

                for c, item in enumerate(items):
                    if item:
                        self.table.setItem(row, c, item)

        self._save_queue()

    # ---------------- helper: icons ----------------
    def _set_btn_icon(self, btn: QtWidgets.QPushButton, icon_role: QStyle.StandardPixmap):
        base_icon = self.style().standardIcon(icon_role)
        px = base_icon.pixmap(26, 26)
        if not px.isNull():
            tinted = QtGui.QPixmap(px.size())
            tinted.fill(QtCore.Qt.transparent)
            p = QtGui.QPainter(tinted)
            p.drawPixmap(0, 0, px)
            p.setCompositionMode(QtGui.QPainter.CompositionMode_SourceIn)
            p.fillRect(tinted.rect(), QtGui.QColor("#FFFFFF"))
            p.end()
            btn.setIcon(QtGui.QIcon(tinted))
        else:
            btn.setIcon(base_icon)
        btn.setIconSize(QtCore.QSize(24, 24))

    def _set_tool_icon(self, btn: QtWidgets.QToolButton, icon_role: QStyle.StandardPixmap):
        btn.setIcon(self.style().standardIcon(icon_role))
        btn.setIconSize(QtCore.QSize(20, 20))

    def _custom_thumb_file_for(self, src_path: str) -> Path:
        digest = hashlib.sha1(src_path.encode("utf-8", errors="ignore")).hexdigest()
        return CUSTOM_THUMB_DIR / f"{digest}.png"

    def _set_thumb_icon_for_path(self, src_path: str, icon: QtGui.QIcon):
        for i in range(self.thumbs.count()):
            it = self.thumbs.item(i)
            if it.data(QtCore.Qt.UserRole) == src_path:
                it.setIcon(icon)
                break

    def _save_custom_thumb(self, src_path: str, image: QtGui.QImage):
        dst = self._custom_thumb_file_for(src_path)
        try:
            image.save(str(dst), "PNG")
            self.custom_thumbs[src_path] = str(dst)
        except Exception:
            return
        self._set_thumb_icon_for_path(src_path, QtGui.QIcon(QtGui.QPixmap.fromImage(image)))
        self._save_queue()

    def _apply_custom_thumb_if_exists(self, src_path: str) -> bool:
        thumb_path = self.custom_thumbs.get(src_path, "")
        if not thumb_path:
            return False
        p = Path(thumb_path)
        if not p.exists():
            return False
        icon = QtGui.QIcon(str(p))
        if icon.isNull():
            return False
        self._set_thumb_icon_for_path(src_path, icon)
        return True

    def _ensure_row_checkboxes(self):
        for r in range(self.table.rowCount()):
            if not self.table.cellWidget(r, COL_SEL):
                self.table.setCellWidget(r, COL_SEL, CenteredCheckBox(self))

    # ---------------- menubar ----------------
    def _build_menubar(self, mb: QtWidgets.QMenuBar):
        # Bestand
        m_file = mb.addMenu("Bestand")
        a_open = m_file.addAction("Bestanden openen...")
        a_open.triggered.connect(self.on_browse)

        a_open_dir = m_file.addAction("Map openen...")
        a_open_dir.triggered.connect(self.on_browse_folder)

        m_file.addSeparator()

        a_saveq = m_file.addAction("Sessie opslaan (.vos)...")
        a_saveq.triggered.connect(self._save_session_dialog)

        a_loadq = m_file.addAction("Sessie openen (.vos)...")
        a_loadq.triggered.connect(self._load_session_dialog)

        m_file.addSeparator()

        a_exit = m_file.addAction("Afsluiten")
        a_exit.triggered.connect(self.close)

        # Bewerken (Undo/Redo)
        m_edit = mb.addMenu("Bewerk")
        a_undo = m_edit.addAction("Ongedaan maken")
        a_undo.setShortcut("Ctrl+Z")
        a_undo.triggered.connect(self.undo_action)

        a_redo = m_edit.addAction("Opnieuw uitvoeren")
        a_redo.setShortcut("Ctrl+Y")
        a_redo.triggered.connect(self.redo_action)

        # Instellingen
        m_view = mb.addMenu("Instellingen")

        self.a_del_src = m_view.addAction("Origineel verwijderen na succes")
        self.a_del_src.setCheckable(True)
        self.a_del_src.setChecked(
            bool(int(self.settings.value("prefs/delete_source", 0)))
        )
        self.a_del_src.triggered.connect(self._toggle_delete_source)

        self.a_auto_clear = m_view.addAction("Lijst opschonen na succes")
        self.a_auto_clear.setCheckable(True)
        self.a_auto_clear.setChecked(
            bool(int(self.settings.value("prefs/auto_clear_list", 0)))
        )
        self.a_auto_clear.triggered.connect(self._toggle_auto_clear)

        m_view.addSeparator()
        a_settings = m_view.addAction("Instellingen (volledig)...")
        a_settings.triggered.connect(self.on_settings)

        # Help
        m_help = mb.addMenu("Help")
        a_about = m_help.addAction("Over...")
        a_about.triggered.connect(self.on_about)

    def _toggle_delete_source(self, checked):
        self.settings.setValue("prefs/delete_source", 1 if checked else 0)

    def _toggle_auto_clear(self, checked):
        self.settings.setValue("prefs/auto_clear_list", 1 if checked else 0)

    # ---------------- theming ----------------
    def apply_theme(self, key: str):
        cfg = THEMES.get("Modern Dark")
        pal = self.palette()
        pal.setColor(QtGui.QPalette.Window, QtGui.QColor(cfg["win"]))
        pal.setColor(QtGui.QPalette.Base, QtGui.QColor(cfg["base"]))
        pal.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(cfg["alt"]))
        pal.setColor(QtGui.QPalette.Text, QtGui.QColor(cfg["text"]))
        pal.setColor(QtGui.QPalette.ButtonText, QtGui.QColor(cfg["text"]))
        pal.setColor(QtGui.QPalette.WindowText, QtGui.QColor(cfg["text"]))
        pal.setColor(QtGui.QPalette.Highlight, QtGui.QColor(cfg["sel"]))
        pal.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#FFFFFF"))
        self.setPalette(pal)

        css = """
        QWidget {
            font-family: 'Segoe UI', sans-serif;
            color: #E0E0E0;
        }

        QToolTip {
            border: 1px solid #3A4650;
            background-color: #161B20;
            color: #EEF7FB;
            padding: 6px 8px;
            opacity: 235;
        }

        QMenuBar {
            background: #101317;
            border-bottom: 1px solid #1F2A33;
            padding: 4px 8px;
        }
        QMenuBar::item {
            background: transparent;
            padding: 6px 10px;
            border-radius: 6px;
        }
        QMenuBar::item:selected {
            background: rgba(0, 180, 216, 0.12);
        }
        QMenu {
            background: #151A20;
            border: 1px solid #24313A;
            padding: 6px;
        }
        QMenu::item {
            padding: 7px 18px;
            border-radius: 6px;
        }
        QMenu::item:selected {
            background: rgba(0, 180, 216, 0.18);
        }

        QFrame#TopContainer {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                        stop:0 #161B20, stop:0.55 #1B232B, stop:1 #151A20);
            border: 1px solid #25313B;
            border-radius: 14px;
        }

        QPushButton {
            min-height: 34px;
            padding: 6px 14px;
            background-color: #20262D;
            border: 1px solid #33414C;
            border-radius: 10px;
            color: #F1F5F8;
            font-weight: 600;
            text-align: left;
        }
        QPushButton:hover {
            background-color: #28323B;
            border-color: #4C6575;
        }
        QPushButton:pressed {
            background-color: #161C22;
        }
        QPushButton:disabled {
            color: #6D7880;
            background-color: #161A1F;
            border-color: #222B33;
        }
        QPushButton[accent="true"] {
            background-color: rgba(0, 180, 216, 0.16);
            border: 1px solid #00B4D8;
            color: #DFF8FF;
            font-weight: 700;
        }
        QPushButton[accent="true"]:hover {
            background-color: rgba(0, 180, 216, 0.28);
            border-color: #48CAE4;
        }
        QPushButton[danger="true"] {
            border-color: #8A4C58;
            background-color: rgba(138, 76, 88, 0.16);
        }
        QPushButton[danger="true"]:hover {
            background-color: rgba(138, 76, 88, 0.26);
            border-color: #C86E7E;
        }

        QToolButton {
            background: transparent;
            color: #95A7B4;
            border: none;
            border-radius: 8px;
        }
        QToolButton:hover {
            background: rgba(255, 255, 255, 0.05);
            color: #F0F7FB;
        }

        QLineEdit, QSpinBox, QComboBox {
            background-color: #11161B;
            border: 1px solid #313E48;
            border-radius: 8px;
            padding: 6px 8px;
            color: #EEF7FB;
        }
        QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
            border: 1px solid #00B4D8;
        }
        QComboBox::drop-down {
            border: none;
            width: 22px;
        }

        QTableWidget {
            background-color: #171C22;
            alternate-background-color: #1B2128;
            gridline-color: transparent;
            border: 1px solid #232E38;
            border-radius: 12px;
            selection-background-color: rgba(0, 180, 216, 0.18);
            selection-color: white;
        }
        QTableWidget::item {
            padding: 6px;
            border-bottom: 1px solid #222A31;
        }
        QTableWidget::item:focus {
            outline: none;
            border: none;
        }
        QTableWidget::item:selected {
            background-color: rgba(0, 180, 216, 0.2);
            color: #F3FBFF;
            border: none;
        }

        QHeaderView::section {
            background-color: #11161B;
            color: #8CCFE1;
            padding: 8px 10px;
            border: none;
            border-bottom: 1px solid #25313B;
            border-right: 1px solid #25313B;
            font-weight: bold;
        }

        QSplitter::handle {
            background-color: #2C2C2C;
        }
        QSplitter::handle:horizontal {
            width: 6px;
        }
        QSplitter::handle:vertical {
            height: 6px;
        }

        QProgressBar {
            border: none;
            background-color: #252C34;
            border-radius: 4px;
            text-align: center;
            color: white;
        }
        QProgressBar::chunk {
            background-color: #00B4D8;
            border-radius: 4px;
        }

        QCheckBox {
            spacing: 8px;
            color: #DCE7ED;
            font-weight: 600;
        }
        QCheckBox::indicator {
            width: 18px;
            height: 18px;
        }
        """
        self.setStyleSheet(css)

    def _autofit_start(self):
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        h.setStretchLastSection(False)
        for col in range(self.table.columnCount()):
            h.setSectionResizeMode(col, QtWidgets.QHeaderView.Interactive)
            # default smalle kolommen
            if col == COL_SEL:
                self.table.setColumnWidth(col, 34)
            elif col == COL_SEC:
                self.table.setColumnWidth(col, 52)
            elif col == COL_MMSS:
                self.table.setColumnWidth(col, 58)
            elif col == COL_FILE:
                self.table.setColumnWidth(col, 360)
            elif col == COL_TYPE:
                self.table.setColumnWidth(col, 52)
            elif col == COL_DUR:
                self.table.setColumnWidth(col, 62)
            elif col == COL_SIZE:
                self.table.setColumnWidth(col, 80)
            elif col == COL_PCT:
                self.table.setColumnWidth(col, 55)
            elif col == COL_REMAIN:
                self.table.setColumnWidth(col, 90)
            else:
                self.table.setColumnWidth(col, 90)
        QtCore.QTimer.singleShot(0, self._fit_splitter_to_columns)

    def autofit_columns(self):
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        h.setStretchLastSection(False)
        for col in range(self.table.columnCount()):
            h.setSectionResizeMode(col, QtWidgets.QHeaderView.Interactive)
            if col == COL_SEL:
                self.table.setColumnWidth(col, 34)
            elif col == COL_FILE:
                self.table.setColumnWidth(col, 360)
        QtCore.QTimer.singleShot(0, self._fit_splitter_to_columns)

    def _fit_splitter_to_columns(self):
        if not getattr(self, "splitter", None):
            return
        try:
            h = self.table.horizontalHeader()
            total_cols = 0
            for i in range(self.table.columnCount()):
                total_cols += h.sectionSize(i)
            extra = self.table.verticalHeader().width() + self.table.frameWidth() * 2 + 16
            desired_left = total_cols + extra
            total_w = max(1, self.splitter.width())
            right_min = 380
            left = min(desired_left, max(220, total_w - right_min))
            right = max(right_min, total_w - left)
            self.splitter.setSizes([left, right])
        except Exception:
            pass

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        if not getattr(self, "_did_splitter_fit", False):
            self._did_splitter_fit = True
            QtCore.QTimer.singleShot(80, self._fit_splitter_to_columns)

    def on_aspect_changed(self, txt: str):
        self.settings.setValue("video/aspect", txt)
        self.video.set_aspect(None if txt == "Auto" else txt)

    # ---------------- file adding + overlay + thumbs ----------------
    def add_files(self, paths: List[Path]):
        # Save undo snapshot before modifying list
        self.save_undo_snapshot()
        self._add_files_internal(paths, use_overlay=True)

    def _add_files_internal(self, paths: List[Path], use_overlay=True):
        """Interne methode voor toevoegen, zodat restore_snapshot deze kan aanroepen zonder undo loop."""
        expanded: List[Path] = []
        for p in paths:
            if p.is_dir():
                expanded.extend(self._scan_folder_with_overlay(p))
            else:
                expanded.append(p)
        paths = expanded

        total = len(paths)
        do_overlay = use_overlay and (total >= 1)

        if do_overlay:
            self.overlay.start(
                f"Klaarmaken om {total} bestanden te laden..."
            )
            QtWidgets.QApplication.processEvents()

        # Sorteren tijdelijk uit om performance te boosten bij veel files
        self.table.setSortingEnabled(False)

        start_time = time.time()
        existing = {
            self.table.item(r, COL_FILE).toolTip() or ""
            for r in range(self.table.rowCount())
            if self.table.item(r, COL_FILE)
        }

        for i, p in enumerate(paths, 1):
            if do_overlay:
                self.overlay.set_message(
                    f"Inladen: {i} / {total} • {p.name}"
                )
                pct = int(i / total * 100) if total else 0
                elapsed = time.time() - start_time
                avg_time = elapsed / i if i > 0 else 0
                left_sec = avg_time * (total - i)
                self.overlay.update_state(pct, fmt_hms(left_sec))
                QtWidgets.QApplication.processEvents()

            if p.suffix.lower() not in VIDEO_EXTS:
                continue
            sp = str(p)
            if sp in existing:
                continue

            r = self.table.rowCount()
            self.table.insertRow(r)
            
            # 1. Custom Centered Checkbox Widget
            chk = CenteredCheckBox(self)
            self.table.setCellWidget(r, COL_SEL, chk)

            self.table.setItem(
                r, COL_SEC, NumericItem(str(self.spin_secs.value()))
            )
            self.table.setItem(
                r,
                COL_MMSS,
                QtWidgets.QTableWidgetItem(
                    seconds_to_mmss(self.spin_secs.value())
                ),
            )
            it_file = QtWidgets.QTableWidgetItem(p.name)
            it_file.setToolTip(sp)
            self.table.setItem(r, COL_FILE, it_file)
            self.table.setItem(
                r,
                COL_TYPE,
                QtWidgets.QTableWidgetItem(p.suffix.upper().lstrip(".")),
            )

            dsec = 0.0
            try:
                out = subprocess.check_output(
                    [
                        (which("ffprobe") or "ffprobe"),
                        "-v",
                        "error",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "default=noprint_wrappers=1:nokey=1",
                        sp,
                    ],
                    text=True,
                    timeout=6,
                )
                dsec = float(out.strip() or 0)
            except Exception:
                dsec = 0.0

            self.table.setItem(r, COL_DUR, QtWidgets.QTableWidgetItem(fmt_hms(dsec)))

            try:
                size_b = p.stat().st_size
            except Exception:
                size_b = 0

            self.table.setItem(r, COL_SIZE, NumericItem(fmt_size(size_b)))

            self.table.setItem(r, COL_PCT, QtWidgets.QTableWidgetItem("0%"))
            self.table.setItem(r, COL_REMAIN, QtWidgets.QTableWidgetItem("—"))
            self.table.setItem(r, COL_STATUS, QtWidgets.QTableWidgetItem("Toegevoegd"))

            th = QListWidgetItem(p.name)
            th.setToolTip(sp)
            th.setData(QtCore.Qt.UserRole, sp)
            th.setIcon(self.style().standardIcon(QStyle.SP_FileIcon))
            self.thumbs.addItem(th)
            has_custom_thumb = self._apply_custom_thumb_if_exists(sp)
            if self.thumb_worker and not has_custom_thumb:
                self.thumb_worker.add_path(sp)

        if do_overlay:
            self.overlay.stop()

        self._ensure_row_checkboxes()
        self.table.setSortingEnabled(True)
        self._save_queue()

    def _scan_folder_with_overlay(self, base: Path) -> List[Path]:
        self.overlay.start_busy("Map scannen...")
        QtWidgets.QApplication.processEvents()
        found: List[Path] = []
        exts = VIDEO_EXTS
        tick = 0
        try:
            for dirpath, _dirs, files in os.walk(base):
                for fn in files:
                    if Path(fn).suffix.lower() in exts:
                        found.append(Path(dirpath) / fn)
                tick += 1
                if tick % 25 == 0:
                    self.overlay.set_message(
                        f"Map scannen... ({len(found)} gevonden)"
                    )
                    QtWidgets.QApplication.processEvents(
                        QtCore.QEventLoop.AllEvents, 50
                    )
        finally:
            self.overlay.stop()
        found.sort(
            key=lambda p: (p.parent.as_posix().lower(), p.name.lower())
        )
        return found

    def on_thumb_ready(self, path: str, icon: QtGui.QIcon):
        if self._apply_custom_thumb_if_exists(path):
            return
        for i in range(self.thumbs.count()):
            item = self.thumbs.item(i)
            if item.data(QtCore.Qt.UserRole) == path:
                item.setIcon(icon)
                break

    def on_browse(self):
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Kies videobestanden",
            self.last_dir,
            "Video (*.*)",
        )
        if files:
            self.add_files([Path(f) for f in files])
            try:
                self.last_dir = str(Path(files[0]).parent)
                self.settings.setValue("ui/last_dir", self.last_dir)
            except Exception:
                pass

    def on_browse_folder(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Kies map met video's",
            self.last_dir,
        )
        if not d:
            return
        base = Path(d)
        paths = self._scan_folder_with_overlay(base)
        if paths:
            self.add_files(paths)
        try:
            self.last_dir = str(base)
            self.settings.setValue("ui/last_dir", self.last_dir)
        except Exception:
            pass

    def on_refresh_list(self):
        prev_state: Dict[str, Dict[str, Any]] = {}
        for r in range(self.table.rowCount()):
            it_file = self.table.item(r, COL_FILE)
            it_sec = self.table.item(r, COL_SEC)
            wid = self.table.cellWidget(r, COL_SEL)
            if it_file and it_file.toolTip():
                prev_state[it_file.toolTip()] = {
                    "secs": it_sec.text() if it_sec else str(self.spin_secs.value()),
                    "checked": wid.isChecked() if wid else False,
                }

        paths: List[Path] = []
        for r in range(self.table.rowCount()):
            it = self.table.item(r, COL_FILE)
            if it and it.toolTip():
                p = Path(it.toolTip())
                if p.exists():
                    paths.append(p)

        self.table.setRowCount(0)
        self.thumbs.clear()
        if paths:
            self._add_files_internal(paths, use_overlay=True)
            for r in range(self.table.rowCount()):
                it_file = self.table.item(r, COL_FILE)
                if not it_file:
                    continue
                st = prev_state.get(it_file.toolTip() or "")
                if not st:
                    continue
                secs_txt = str(st.get("secs", self.spin_secs.value()))
                try:
                    sec_val = int(str(secs_txt).split()[0])
                except Exception:
                    sec_val = int(self.spin_secs.value())
                self.table.setItem(r, COL_SEC, NumericItem(str(sec_val)))
                self.table.setItem(r, COL_MMSS, QtWidgets.QTableWidgetItem(seconds_to_mmss(sec_val)))
                wid = self.table.cellWidget(r, COL_SEL)
                if wid:
                    wid.setChecked(bool(st.get("checked", False)))

    def on_remove(self):
        self.save_undo_snapshot()  # Save before remove
        rows = sorted(
            {i.row() for i in self.table.selectedIndexes()}, reverse=True
        )
        paths_to_remove = set()
        for r in rows:
            it = self.table.item(r, COL_FILE)
            if it:
                paths_to_remove.add(it.toolTip() or "")
            self.table.removeRow(r)
        if paths_to_remove:
            for i in reversed(range(self.thumbs.count())):
                it = self.thumbs.item(i)
                if it.data(QtCore.Qt.UserRole) in paths_to_remove:
                    self.thumbs.takeItem(i)
        self._save_queue()

    def on_clear(self):
        self.save_undo_snapshot()  # Save before clear
        self.table.setRowCount(0)
        self.thumbs.clear()
        self._save_queue()

    def on_apply(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            return
        n = int(self.spin_secs.value())
        for r in rows:
            self.table.setItem(r, COL_SEC, NumericItem(str(n)))
            self.table.setItem(
                r,
                COL_MMSS,
                QtWidgets.QTableWidgetItem(seconds_to_mmss(n)),
            )
            # Auto-check widget bij aanpassen
            wid = self.table.cellWidget(r, COL_SEL)
            if wid:
                wid.setChecked(True)
                
        self._save_queue()

    def _apply_mark_to_selection(self, seconds: int):
        self.spin_secs.setValue(seconds)
        self.on_apply()

    def apply_filter(self):
        text = self.ed_filter.text().strip().lower()
        for r in range(self.table.rowCount()):
            it = self.table.item(r, COL_FILE)
            full = (it.toolTip() or "").lower() if it else ""
            name = (it.text() or "").lower() if it else ""
            visible = (not text) or (text in full) or (text in name)
            self.table.setRowHidden(r, not visible)

    # ---------------- selection helpers (table <-> thumbs) ----------------
    def _current_row(self) -> Optional[int]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return rows[0] if rows else None

    def _load_row(self, r: int):
        it = self.table.item(r, COL_FILE)
        path = (it.toolTip()) if it else ""
        if path:
            self.video.set_media_load(path, start_paused=True)
            self.vc.set_playing(False)
            self._select_thumb_by_path(path)

    def _select_row_by_path(self, path: str):
        for r in range(self.table.rowCount()):
            it = self.table.item(r, COL_FILE)
            tip = it.toolTip() if it else ""
            if tip == path:
                self.table.selectRow(r)
                self._load_row(r)
                break

    def _select_thumb_by_path(self, path: str):
        for i in range(self.thumbs.count()):
            it = self.thumbs.item(i)
            if it.data(QtCore.Qt.UserRole) == path:
                self.thumbs.blockSignals(True)
                self.thumbs.setCurrentItem(it)
                self.thumbs.scrollToItem(
                    it, QtWidgets.QAbstractItemView.PositionAtCenter
                )
                self.thumbs.blockSignals(False)
                break

    def _sync_sel_table_to_thumbs(self):
        paths = {
            self.table.item(r, COL_FILE).toolTip()
            for r in {i.row() for i in self.table.selectedIndexes()}
            if self.table.item(r, COL_FILE)
        }
        self.thumbs.blockSignals(True)
        self.thumbs.clearSelection()
        first = None
        for i in range(self.thumbs.count()):
            it = self.thumbs.item(i)
            if it.data(QtCore.Qt.UserRole) in paths:
                it.setSelected(True)
                if first is None:
                    first = it
        if first:
            self.thumbs.scrollToItem(first)
        self.thumbs.blockSignals(False)

    def _sync_sel_thumbs_to_table(self):
        items = self.thumbs.selectedItems()
        paths = {it.data(QtCore.Qt.UserRole) for it in items}
        self.table.blockSignals(True)
        self.table.clearSelection()
        first_row = None
        for r in range(self.table.rowCount()):
            it = self.table.item(r, COL_FILE)
            if it and (it.toolTip() in paths):
                self.table.selectRow(r)
                if first_row is None:
                    first_row = r
        self.table.blockSignals(False)
        if first_row is not None:
            self._load_row(first_row)

    def _thumb_clicked(self, item: QListWidgetItem):
        """Single click sync van thumbnail naar tabel"""
        path = item.data(QtCore.Qt.UserRole)
        if not path: return
        self._select_row_by_path(path)
        # Eventueel direct spelen of laden (hier laden we in pauze)
        self.vc.set_playing(False)

    def _thumbs_double_clicked(self, item: QListWidgetItem):
        path = item.data(QtCore.Qt.UserRole) or item.toolTip()
        if not path:
            return
        self._select_row_by_path(str(path))
        self.on_play()
        self.vc.set_playing(True)

    def _table_click_play(self, it: QtWidgets.QTableWidgetItem):
        r = it.row()
        self._load_row(r)

    def _table_dbl_click_toggle(self, item: QtWidgets.QTableWidgetItem):
        r = item.row()
        # Als er gedubbelklikt wordt in de selectie kolom (buiten de checkbox om), toggle hem
        if item.column() == COL_SEL:
            wid = self.table.cellWidget(r, COL_SEL)
            if wid:
                wid.setChecked(not wid.isChecked())
        else:
            # Elders dubbelklikken = afspelen
            self._load_row(r)
            self.on_play()

    def _select_all_rows(self):
        """Alles aan- of uitvinken op basis van de eerste rij"""
        if self.table.rowCount() == 0:
            return
        first_wid = self.table.cellWidget(0, COL_SEL)
        new_state = not first_wid.isChecked() if first_wid else True
        
        for r in range(self.table.rowCount()):
            wid = self.table.cellWidget(r, COL_SEL)
            if wid:
                wid.setChecked(new_state)

    # ---------------- navigation & context menu ----------------
    def on_nudge(self, seconds: int):
        if not self.video.is_available():
            return
        self.video.seek_relative(int(seconds))
        QtWidgets.QToolTip.showText(
            QtGui.QCursor.pos(), f"{'+' if seconds > 0 else ''}{seconds}s", self
        )

    def on_toggle_mute(self):
        self.video.toggle_mute()
        now = self.video.get_volume_percent()
        self.vc.vol_slider.blockSignals(True)
        self.vc.vol_slider.setValue(now)
        self.vc.vol_slider.blockSignals(False)
        self.vc.set_muted_state(now <= 0)

    def on_set_volume_level(self, level: int):
        self.video.set_volume_percent(level)
        self.vc.set_muted_state(level <= 0)
        self.vc.lbl_volume.setText(f"Geluid {level}%")

    def on_table_context_menu(self, pos: QtCore.QPoint):
        menu = QtWidgets.QMenu(self)
        idx = self.table.indexAt(pos)
        has_row = idx.isValid()
        rows = sorted(
            {i.row() for i in self.table.selectedIndexes()}
        ) or ([idx.row()] if has_row else [])
        row_for_thumb = rows[0] if rows else None

        act_load = menu.addAction("Laden (pauze)")
        act_play = menu.addAction("Afspelen")
        act_thumb_edit = menu.addAction("Thumbnail Editor (dit bestand)")
        menu.addSeparator()
        act_toggle = menu.addAction("Checkbox aan/uit")
        act_all_on = menu.addAction("Alles aanvinken")
        act_all_off = menu.addAction("Alles uitvinken")
        menu.addSeparator()
        act_reveal = menu.addAction("Toon in Verkenner")
        act_remove = menu.addAction("Verwijder uit lijst")

        a = menu.exec(self.table.viewport().mapToGlobal(pos))
        if not a:
            return

        if a == act_load and rows:
            self._load_row(rows[0])
            self.vc.set_playing(False)
        elif a == act_play and rows:
            self._load_row(rows[0])
            self.on_play()
            self.vc.set_playing(True)
        elif a == act_thumb_edit and row_for_thumb is not None:
            it = self.table.item(row_for_thumb, COL_FILE)
            if it and it.toolTip():
                self._open_thumbnail_editor(single_path=it.toolTip())
        elif a == act_reveal:
            for r in rows:
                it = self.table.item(r, COL_FILE)
                if it:
                    p = Path(it.toolTip() or "")
                    try:
                        if sys.platform.startswith("win"):
                            os.startfile(p.parent)
                        elif sys.platform == "darwin":
                            subprocess.run(["open", p.parent])
                        else:
                            subprocess.run(["xdg-open", p.parent])
                    except Exception:
                        pass
        elif a == act_toggle:
            for r in rows:
                wid = self.table.cellWidget(r, COL_SEL)
                if wid:
                    wid.setChecked(not wid.isChecked())
        elif a == act_all_on:
            for r in range(self.table.rowCount()):
                wid = self.table.cellWidget(r, COL_SEL)
                if wid: wid.setChecked(True)
        elif a == act_all_off:
            for r in range(self.table.rowCount()):
                wid = self.table.cellWidget(r, COL_SEL)
                if wid: wid.setChecked(False)
        elif a == act_remove:
            self.on_remove()

    def on_thumbs_context_menu(self, pos: QtCore.QPoint):
        it = self.thumbs.itemAt(pos)
        if not it:
            return
        clicked_path = it.data(QtCore.Qt.UserRole)
        if not clicked_path:
            return

        selected_paths = [
            t.data(QtCore.Qt.UserRole)
            for t in self.thumbs.selectedItems()
            if t and t.data(QtCore.Qt.UserRole)
        ]
        paths = selected_paths if selected_paths else [clicked_path]
        if clicked_path not in paths:
            paths = [clicked_path]

        rows: List[int] = []
        for path in paths:
            for r in range(self.table.rowCount()):
                file_it = self.table.item(r, COL_FILE)
                if file_it and (file_it.toolTip() or "") == path:
                    rows.append(r)
                    break
        rows = sorted(set(rows))
        if not rows:
            return

        menu = QtWidgets.QMenu(self)
        act_load = menu.addAction("Laden (pauze)")
        act_play = menu.addAction("Afspelen")
        act_edit = menu.addAction("Thumbnail Editor (dit bestand)")
        menu.addSeparator()
        act_toggle = menu.addAction("Checkbox aan/uit")
        act_all_on = menu.addAction("Alles aanvinken")
        act_all_off = menu.addAction("Alles uitvinken")
        menu.addSeparator()
        act_reveal = menu.addAction("Toon in Verkenner")
        act_remove = menu.addAction("Verwijder uit lijst")
        chosen = menu.exec(self.thumbs.viewport().mapToGlobal(pos))
        if not chosen:
            return

        if chosen == act_load and rows:
            self._load_row(rows[0])
            self.vc.set_playing(False)
        elif chosen == act_play and rows:
            self._load_row(rows[0])
            self.on_play()
            self.vc.set_playing(True)
        elif chosen == act_edit:
            self._open_thumbnail_editor(single_path=clicked_path)
        elif chosen == act_toggle:
            for r in rows:
                wid = self.table.cellWidget(r, COL_SEL)
                if wid:
                    wid.setChecked(not wid.isChecked())
        elif chosen == act_all_on:
            for r in range(self.table.rowCount()):
                wid = self.table.cellWidget(r, COL_SEL)
                if wid:
                    wid.setChecked(True)
        elif chosen == act_all_off:
            for r in range(self.table.rowCount()):
                wid = self.table.cellWidget(r, COL_SEL)
                if wid:
                    wid.setChecked(False)
        elif chosen == act_reveal:
            for r in rows:
                file_it = self.table.item(r, COL_FILE)
                if not file_it:
                    continue
                p = Path(file_it.toolTip() or "")
                try:
                    if sys.platform.startswith("win"):
                        os.startfile(p.parent)
                    elif sys.platform == "darwin":
                        subprocess.run(["open", p.parent])
                    else:
                        subprocess.run(["xdg-open", p.parent])
                except Exception:
                    pass
        elif chosen == act_remove:
            self.table.clearSelection()
            sel_model = self.table.selectionModel()
            if sel_model:
                for r in rows:
                    idx = self.table.model().index(r, COL_FILE)
                    sel_model.select(
                        idx,
                        QtCore.QItemSelectionModel.Select
                        | QtCore.QItemSelectionModel.Rows,
                    )
            self.on_remove()

    # ---------------- Intro Detection ----------------
    def on_detect_intro(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            QtWidgets.QMessageBox.information(self, "Intro", "Selecteer eerst regels om te scannen.")
            return

        ffmpeg = which("ffmpeg") or "ffmpeg"

        self.btn_detect.setEnabled(False)
        self.btn_detect.setText("Scannen...")
        QtWidgets.QApplication.processEvents()

        self.table.setSortingEnabled(False)

        count = 0
        for r in rows:
            path = self.table.item(r, COL_FILE).toolTip()
            found_sec = detect_intro_ffmpeg(ffmpeg, path)
            if found_sec > 0:
                self.table.setItem(r, COL_SEC, NumericItem(str(int(found_sec))))
                self.table.setItem(r, COL_MMSS, QtWidgets.QTableWidgetItem(seconds_to_mmss(found_sec)))
                self.table.setItem(r, COL_STATUS, QtWidgets.QTableWidgetItem(f"Intro: {int(found_sec)}s"))
                count += 1

        self.table.setSortingEnabled(True)
        self.btn_detect.setText("Intro")
        self.btn_detect.setEnabled(True)
        QtWidgets.QMessageBox.information(self, "Intro Scan", f"Klaar. {count} intro's gevonden en ingesteld.")

    def on_change_thumbnail(self):
        self._open_thumbnail_editor(single_path=None)

    def _open_thumbnail_editor(self, single_path: Optional[str] = None):
        if not HAS_OPENCV or cv2 is None:
            QtWidgets.QMessageBox.information(self, APP_TITLE, "OpenCV niet beschikbaar voor thumbnail-bewerking.")
            return
        if single_path:
            paths = [single_path]
            current_path = single_path
        else:
            paths: List[str] = []
            for r in range(self.table.rowCount()):
                it = self.table.item(r, COL_FILE)
                if it and it.toolTip():
                    paths.append(it.toolTip())
            if not paths:
                QtWidgets.QMessageBox.information(self, APP_TITLE, "Geen bestanden in de lijst.")
                return
            current_path = None
            cur_row = self._current_row()
            if cur_row is not None:
                it = self.table.item(cur_row, COL_FILE)
                if it:
                    current_path = it.toolTip()

        dlg = ThumbnailPickerDialog(self, paths, current_path=current_path)
        dlg.thumbnail_applied.connect(self._save_custom_thumb)
        dlg.finished.connect(lambda _result, d=dlg: self._thumb_editors.remove(d) if d in self._thumb_editors else None)
        self._thumb_editors.append(dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    # ---------------- queue starten & worker koppelen ----------------
    def on_start(self):
        if self.worker and self.worker.isRunning():
            QtWidgets.QMessageBox.information(
                self, APP_TITLE, "Queue is al actief."
            )
            return
        jobs = []
        for r in range(self.table.rowCount()):
            # Checkbox via widget controleren
            wid = self.table.cellWidget(r, COL_SEL)
            if not wid or not wid.isChecked():
                continue

            itf = self.table.item(r, COL_FILE)
            s_item = self.table.item(r, COL_SEC)
            if not (itf and s_item):
                continue
            src = Path(itf.toolTip() or "")
            overwrite = self.chk_overwrite.isChecked()
            if overwrite:
                out = src.with_name(src.stem + ".vos_tmp" + src.suffix)
            else:
                out = src.with_name(src.stem + "-out.mp4")
            try:
                secs = int(str(s_item.text()).split()[0])
            except Exception:
                secs = int(self.spin_secs.value())
            in_seconds = 60.0
            try:
                outp = subprocess.check_output(
                    [
                        (which("ffprobe") or "ffprobe"),
                        "-v",
                        "error",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "default=noprint_wrappers=1:nokey=1",
                        str(src),
                    ],
                    text=True,
                    timeout=6,
                )
                in_seconds = float(outp.strip() or 60.0)
            except Exception:
                in_seconds = 60.0
            try:
                in_bytes = src.stat().st_size
            except Exception:
                in_bytes = 0
            self.table.setItem(
                r, COL_STATUS, QtWidgets.QTableWidgetItem("Wachtrij")
            )
            jobs.append(
                {
                    "row": r,
                    "task": {
                        "mode": self.cmb_mode.currentText(),
                        "src": str(src),
                        "out": str(out),
                        "secs": secs,
                        "cuda": self.chk_cuda.isChecked(),
                        "fallback": self.chk_fallback.isChecked(),
                        "overwrite": overwrite,
                        "in_seconds": in_seconds,
                        "in_bytes": in_bytes,
                    },
                }
            )
        if not jobs:
            QtWidgets.QMessageBox.information(
                self, APP_TITLE, "Geen geselecteerde rijen"
            )
            return

        rename_ts_only = bool(
            int(self.settings.value("prefs/ts_rename_only", 1))
        )

        delete_source = self.a_del_src.isChecked()

        self.worker = BatchWorker(
            jobs,
            which("ffmpeg") or "ffmpeg",
            which("ffprobe") or "ffprobe",
            self,
            rename_ts_only=rename_ts_only,
            delete_source=delete_source,
            overwrite_source=self.chk_overwrite.isChecked(),
        )
        self.worker.row_progress.connect(self._ui_row_progress)
        self.worker.row_status.connect(self._ui_row_status)
        self.worker.row_done.connect(self._ui_row_done)
        self.worker.overall_eta.connect(self.lbl_overall.setText)
        self.worker.sound_notify.connect(play_sound)
        self.worker.finished.connect(self._on_batch_finished)

        self.btn_pause_all.setEnabled(True)
        self.btn_resume_all.setEnabled(False)
        self.worker.start()

    def on_pause_all(self):
        if self.worker and self.worker.isRunning():
            self.worker.request_pause_all()
            self.btn_pause_all.setEnabled(False)
            self.btn_resume_all.setEnabled(True)

    def on_resume_all(self):
        if self.worker and self.worker.isRunning():
            self.worker.request_resume_all()
            self.btn_pause_all.setEnabled(True)
            self.btn_resume_all.setEnabled(False)

    def on_stop_all(self):
        if self.worker and self.worker.isRunning():
            self.worker.request_stop_all()

    def _ui_row_progress(self, row: int, pct: int, eta_text: str, phase: str):
        self.table.setItem(row, COL_PCT, QtWidgets.QTableWidgetItem(f"{pct}%"))
        self.table.setItem(row, COL_REMAIN, QtWidgets.QTableWidgetItem(eta_text))
        self.table.setItem(
            row,
            COL_STATUS,
            QtWidgets.QTableWidgetItem(f"{phase}"),
        )

    def _ui_row_status(self, row: int, text: str):
        self.table.setItem(row, COL_STATUS, QtWidgets.QTableWidgetItem(text))

    def _ui_row_done(self, row: int, ok: bool, outpath: str):
        # Update Icon & Status
        status_txt = "✅ Gereed" if ok else "⚠️ Mislukt"
        
        self.table.setItem(
            row,
            COL_PCT,
            QtWidgets.QTableWidgetItem("100%" if ok else "—"),
        )
        self.table.setItem(
            row,
            COL_REMAIN,
            QtWidgets.QTableWidgetItem("0s" if ok else "—"),
        )
        self.table.setItem(
            row,
            COL_STATUS,
            QtWidgets.QTableWidgetItem(status_txt),
        )
        
        # Uncheck als klaar
        if ok:
            wid = self.table.cellWidget(row, COL_SEL)
            if wid: wid.setChecked(False)
        
        self._save_queue()

    def _on_batch_finished(self):
        if self.a_auto_clear.isChecked():
            self.table.setSortingEnabled(False)
            paths_to_remove = set()

            for r in range(self.table.rowCount() - 1, -1, -1):
                status_item = self.table.item(r, COL_STATUS)
                # Check op nieuwe icon teksten
                if status_item and ("Gereed" in status_item.text() or "Klaar" in status_item.text()):
                    it_file = self.table.item(r, COL_FILE)
                    if it_file:
                        paths_to_remove.add(it_file.toolTip())
                    self.table.removeRow(r)

            if self.table.rowCount() == 0:
                self.thumbs.clear()
            else:
                for i in range(self.thumbs.count() - 1, -1, -1):
                    it = self.thumbs.item(i)
                    if it.data(QtCore.Qt.UserRole) in paths_to_remove:
                        self.thumbs.takeItem(i)

            self.table.setSortingEnabled(True)
            self._save_queue()

    # ---------------- settings / about / persist ----------------
    def on_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            dlg.apply()
            self.spin_secs.setValue(
                int(self.settings.value("prefs/default_secs", 1))
            )
            self.chk_cuda.setChecked(
                bool(int(self.settings.value("prefs/cuda", 1)))
            )
            self.chk_fallback.setChecked(
                bool(int(self.settings.value("prefs/fallback", 1)))
            )
            self.chk_overwrite.setChecked(
                bool(int(self.settings.value("prefs/overwrite", 1)))
            )
            aspect = self.settings.value("video/aspect", "16:9")
            self.cmb_aspect.blockSignals(True)
            self.cmb_aspect.setCurrentText(aspect)
            self.cmb_aspect.blockSignals(False)
            self.on_aspect_changed(aspect)
            self.drive_art = self.settings.value("ui/drive_art", "")

            self.a_del_src.setChecked(bool(int(self.settings.value("prefs/delete_source", 0))))
            self.a_auto_clear.setChecked(bool(int(self.settings.value("prefs/auto_clear_list", 0))))

    def on_about(self):
        try:
            main_file = str(Path(__file__).resolve())
        except Exception:
            main_file = "(onbekend)"
        txt = (
            f"<b>{APP_NAME}</b><br>Versie: {APP_VER}<br>"
            f"Mode: Modern + Undo/Redo<br>"
            f"Auteur: <a href='{AUTHOR_URL}'>{AUTHOR_NAME}</a><br>"
            f"Appdata: {APPDATA_DIR}<br>"
            f"Hoofdbestand: {main_file}<br>"
        )
        QtWidgets.QMessageBox.about(self, "Over", txt)

    def _save_queue(self):
        ui_state = {
            "theme": self._theme_name,
            "aspect": self.cmb_aspect.currentText(),
            "custom_thumbs": self.custom_thumbs,
        }
        save_queue(self.table, ui_state)

    def _load_from_state(self, data: dict):
        if not data:
            return
        items = data.get("items", [])
        self.custom_thumbs = data.get("custom_thumbs", {}) or {}
        for it in items:
            p = Path(it.get("path", ""))
            if not p.exists():
                continue
            self.add_files([p])
            r = self.table.rowCount() - 1
            if r >= 0 and it.get("secs"):
                secs = int(it.get("secs").split()[0])
                self.table.setItem(
                    r, COL_SEC, NumericItem(str(secs))
                )
                self.table.setItem(
                    r,
                    COL_MMSS,
                    QtWidgets.QTableWidgetItem(seconds_to_mmss(secs)),
                )

            if r >= 0 and "checked" in it:
                wid = self.table.cellWidget(r, COL_SEL)
                if wid:
                    wid.setChecked(it.get("checked"))
            self._apply_custom_thumb_if_exists(str(p))

        th = data.get("theme")
        if th in THEMES:
            self.apply_theme(th)
        asp = data.get(
            "aspect", self.settings.value("video/aspect", "16:9")
        )
        if asp:
            self.cmb_aspect.setCurrentText(asp)
            self.on_aspect_changed(asp)
        self._ensure_row_checkboxes()

    def _restore_queue(self):
        self._load_from_state(load_queue())

    def _save_session_file(self, target: Path):
        ui_state = {
            "theme": self._theme_name,
            "aspect": self.cmb_aspect.currentText(),
            "custom_thumbs": self.custom_thumbs,
        }
        items = []
        for r in range(self.table.rowCount()):
            it_file = self.table.item(r, COL_FILE)
            it_sec = self.table.item(r, COL_SEC)
            if not it_file:
                continue
            wid = self.table.cellWidget(r, COL_SEL)
            checked = wid.isChecked() if wid else False
            items.append({
                "path": it_file.toolTip() or "",
                "secs": it_sec.text() if it_sec else "0",
                "checked": checked,
            })
        obj = {"items": items, **ui_state}
        target.write_text(json.dumps(obj, indent=2), encoding="utf-8")

    def _save_session_dialog(self):
        start_dir = str(self.last_session_dir or self.last_dir or Path.home())
        fn, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Sessie opslaan",
            str(Path(start_dir) / f"session{SESSION_EXT}"),
            f"Video Operations Session (*{SESSION_EXT})",
        )
        if not fn:
            return
        p = Path(fn)
        if p.suffix.lower() != SESSION_EXT:
            p = p.with_suffix(SESSION_EXT)
        try:
            self._save_session_file(p)
            self.last_session_dir = str(p.parent)
            self.settings.setValue("ui/last_session_dir", self.last_session_dir)
        except Exception as ex:
            QtWidgets.QMessageBox.warning(self, APP_TITLE, f"Sessie opslaan mislukt:\n{ex}")

    def _load_session_dialog(self):
        start_dir = str(self.last_session_dir or self.last_dir or Path.home())
        fn, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Sessie openen",
            start_dir,
            f"Video Operations Session (*{SESSION_EXT})",
        )
        if not fn:
            return
        p = Path(fn)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as ex:
            QtWidgets.QMessageBox.warning(self, APP_TITLE, f"Sessie openen mislukt:\n{ex}")
            return
        self.table.setRowCount(0)
        self.thumbs.clear()
        self._load_from_state(data)
        self.last_session_dir = str(p.parent)
        self.settings.setValue("ui/last_session_dir", self.last_session_dir)

    def keyPressEvent(self, event: QtGui.QKeyEvent):
        fw = QtWidgets.QApplication.focusWidget()
        if fw is None:
            return super().keyPressEvent(event)

        # Als focus in bestandslijst of miniaturen staat: normale pijltjestoetsnavigatie laten.
        if self.table.isAncestorOf(fw) or self.thumbs.isAncestorOf(fw):
            return super().keyPressEvent(event)

        # In videoplayer-context: pijltjes en spatie als playback-controls.
        if self.video.isAncestorOf(fw) or self.vc.isAncestorOf(fw):
            k = event.key()
            if k == QtCore.Qt.Key_Left:
                self.on_nudge(-5)
                event.accept()
                return
            if k == QtCore.Qt.Key_Right:
                self.on_nudge(+5)
                event.accept()
                return
            if k == QtCore.Qt.Key_Up:
                self.on_next_file()
                event.accept()
                return
            if k == QtCore.Qt.Key_Down:
                self.on_prev_file()
                event.accept()
                return
            if k == QtCore.Qt.Key_Space:
                self.on_toggle_play()
                event.accept()
                return

        super().keyPressEvent(event)

    # ---------------- playback helpers ----------------
    def _is_playing(self) -> bool:
        try:
            return bool(
                self.video
                and getattr(self.video, "player", None)
                and self.video.player.is_playing()
            )
        except Exception:
            return False

    def on_toggle_play(self):
        # FIX VOOR RECURSION ERROR: We roepen de logica aan, maar we emitten het signaal NIET opnieuw.
        if self._is_playing():
            self.on_pause()
        else:
            self.on_play()
        self.vc.set_playing(self._is_playing())

    def on_play(self):
        if (
            self.video
            and self.video.is_available()
            and getattr(self.video, "player", None)
        ):
            try:
                self.video.player.set_pause(0)
                self.video.player.play()
            finally:
                self.vc.set_playing(True)

    def on_pause(self):
        if self.video:
            self.video.pause()
            self.vc.set_playing(False)

    def _row_after(self, r: int) -> int:
        for i in range(r + 1, self.table.rowCount()):
            it = self.table.item(i, COL_FILE)
            if (
                it
                and (it.toolTip() or it.text())
                and not self.table.isRowHidden(i)
            ):
                return i
        return r

    def _row_before(self, r: int) -> int:
        for i in range(r - 1, -1, -1):
            it = self.table.item(i, COL_FILE)
            if (
                it
                and (it.toolTip() or it.text())
                and not self.table.isRowHidden(i)
            ):
                return i
        return r

    def on_next_file(self):
        r = self._current_row()
        if r is None:
            if self.table.rowCount() == 0:
                return
            r = -1
        n = self._row_after(r)
        self.table.selectRow(n)
        self._load_row(n)
        self.vc.set_playing(False)

    def on_prev_file(self):
        r = self._current_row()
        if r is None:
            if self.table.rowCount() == 0:
                return
            r = 1
        p = self._row_before(r)
        self.table.selectRow(p)
        self._load_row(p)
        self.vc.set_playing(False)

    def _header_double_clicked(self, logical_idx: int):
        colmap = {
            COL_FILE: "name",
            COL_SIZE: "size",
            COL_TYPE: "type",
            COL_DUR: "dur",
        }
        key = colmap.get(logical_idx, "name")
        self._sort_table_by(key)

    def _collect_table_rows(self):
        rows = []
        for r in range(self.table.rowCount()):
            it_file = self.table.item(r, COL_FILE)
            if not it_file:
                continue
            full = Path(it_file.toolTip() or it_file.text())
            name = full.name
            d = full.parent.name
            try:
                size = Path(full).stat().st_size
            except Exception:
                size = 0
            try:
                mtime = Path(full).stat().st_mtime
            except Exception:
                mtime = 0
            ext = full.suffix.lower()
            it_dur = self.table.item(r, COL_DUR)
            dur_s = (
                parse_hhmmss_to_seconds(it_dur.text()) if it_dur else 0.0
            )
            rows.append(
                {
                    "r": r,
                    "name": name.lower(),
                    "dir": d.lower(),
                    "size": size,
                    "date": mtime,
                    "type": ext,
                    "dur": dur_s,
                }
            )
        return rows

    def _reorder_table(self, new_row_order: List[int]):
        data = []
        for r in range(self.table.rowCount()):
            row_items = []
            
            # Bewaar widget state
            wid = self.table.cellWidget(r, COL_SEL)
            checked = wid.isChecked() if wid else False
            
            for c in range(self.table.columnCount()):
                it = self.table.item(r, c)
                clone = QtWidgets.QTableWidgetItem(it) if it else None
                row_items.append(clone)
            
            data.append({"items": row_items, "checked": checked})
            
        self.table.setRowCount(0)
        for r in new_row_order:
            self.table.insertRow(self.table.rowCount())
            row_idx = self.table.rowCount() - 1
            
            # Herstel widget
            row_data = data[r]
            chk = CenteredCheckBox(self, checked=row_data["checked"])
            self.table.setCellWidget(row_idx, COL_SEL, chk)
            
            for c, it in enumerate(row_data["items"]):
                if it is not None:
                    self.table.setItem(row_idx, c, it)
                    
        self.autofit_columns()

    def _reorder_thumbs(self, new_order: List[int]):
        items = []
        while self.thumbs.count() > 0:
            items.append(self.thumbs.takeItem(0))
        if len(items) == len(new_order):
            for old_idx in new_order:
                self.thumbs.addItem(items[old_idx])
        else:
            for it in items:
                self.thumbs.addItem(it)

    def _sort_table_by(self, key: str):
        self.save_undo_snapshot()  # Save before sort
        rows = self._collect_table_rows()
        if not rows:
            return
        if self._sort_state["key"] == key:
            self._sort_state["asc"] = not self._sort_state["asc"]
        else:
            self._sort_state["key"] = key
            self._sort_state["asc"] = True
        asc = self._sort_state["asc"]

        rows.sort(key=lambda x: x.get(key, ""), reverse=not asc)

        new_order = [x["r"] for x in rows]

        self._reorder_table(new_order)
        self._reorder_thumbs(new_order)

    def _sort_table_reset(self):
        self.save_undo_snapshot()  # Save before reset
        rows = []
        for r in range(self.table.rowCount()):
            it = self.table.item(r, COL_FILE)
            key = (it.toolTip() or it.text() or "") + f"#{r:06d}"
            rows.append((key, r))
        rows.sort(key=lambda x: x[0])

        new_order = [r for _, r in rows]
        self._reorder_table(new_order)
        self._reorder_thumbs(new_order)
        self._sort_state = {"key": None, "asc": True}

    def _on_item_changed(self, item: QtWidgets.QTableWidgetItem):
        r, c = item.row(), item.column()
        if c == COL_SEC:
            try:
                secs = int(item.text().split()[0])
            except Exception:
                secs = 0
            self.table.blockSignals(True)
            self.table.setItem(
                r, COL_MMSS, QtWidgets.QTableWidgetItem(seconds_to_mmss(secs))
            )
            self.table.blockSignals(False)
        elif c == COL_MMSS:
            secs = mmss_to_seconds(item.text())
            self.table.blockSignals(True)
            self.table.setItem(
                r, COL_SEC, NumericItem(str(secs))
            )
            self.table.blockSignals(False)

    # ---------------- close ----------------
    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self.thumb_worker and self.thumb_worker.isRunning():
            self.thumb_worker.stop()
        if self.worker and self.worker.isRunning():
            self.worker.request_stop_all()
            self.worker.wait()
        super().closeEvent(event)


# ---------------------------------- main ----------------------------------
def main():
    try:
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtCore import Qt

        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass

    app = QtWidgets.QApplication(sys.argv)
    if APP_ICON_PATH.exists():
        app.setWindowIcon(QtGui.QIcon(str(APP_ICON_PATH)))
    splash = StartupSplashDialog(APP_TITLE)
    splash.show()
    splash.update_progress(8, "Voorbereiden...")
    time.sleep(0.04)
    splash.update_progress(24, "UI componenten laden...")
    time.sleep(0.04)
    splash.update_progress(42, "Media modules initialiseren...")
    time.sleep(0.04)
    w = Main()
    splash.update_progress(74, "Venster opbouwen...")
    time.sleep(0.04)
    w.show()
    splash.update_progress(100, "Gereed")
    QtWidgets.QApplication.processEvents()
    time.sleep(0.06)
    if not splash.has_video():
        splash.close()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
