# -*- coding: utf-8 -*-
import sys
import os

PREFERRED_PYTHON = r"C:\Users\ansem\Documents\Github\_venvs\venv_cuda_py310_np2\Scripts\python.exe"


def _ensure_preferred_python():
    if os.environ.get("THUMBNAIL_EDITOR_BOOTSTRAPPED") == "1":
        return
    if not os.path.exists(PREFERRED_PYTHON):
        return
    if os.path.normcase(sys.executable) == os.path.normcase(PREFERRED_PYTHON):
        return
    env = os.environ.copy()
    env["THUMBNAIL_EDITOR_BOOTSTRAPPED"] = "1"
    os.execve(PREFERRED_PYTHON, [PREFERRED_PYTHON, __file__, *sys.argv[1:]], env)


_ensure_preferred_python()

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets
try:
    from PySide6.QtWinExtras import QWinTaskbarButton  # type: ignore
except Exception:
    QWinTaskbarButton = None

try:
    import cv2  # type: ignore
except Exception:
    cv2 = None


APP_TITLE = "Thumbnail Editor (Standalone)"
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".ts", ".avi", ".wmv", ".flv", ".webm"}
MODE_GPU = "GPU (CUDA)"
MODE_CPU = "CPU"
SCAN_MODE_DEFAULT = "Standaard (frames uit eerste minuut)"
SCAN_MODE_CUSTOM = "Aangepast"
SETTINGS_ORG = "TopazUltimate"
SETTINGS_APP = "ThumbnailEditorStandalone"
ROLE_PATH = QtCore.Qt.UserRole
ROLE_IMAGE = QtCore.Qt.UserRole
ROLE_SECONDS = QtCore.Qt.UserRole + 1
ROLE_OUTPUT_DIR = QtCore.Qt.UserRole + 10


class ThumbnailEditorWindow(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1360, 820)
        self.setAcceptDrops(True)

        self._settings = QtCore.QSettings(SETTINGS_ORG, SETTINGS_APP)
        self.last_dir = self._settings.value("last_dir", str(Path.home()), type=str)
        if not self.last_dir or not Path(self.last_dir).exists():
            self.last_dir = str(Path.home())
        self._selected_image: Optional[QtGui.QImage] = None
        self._cuda_available = self._detect_cuda()
        self._ffmpeg_path = shutil.which("ffmpeg")
        self._taskbar_button = None
        self._taskbar_progress = None
        self._scan_cache: Dict[Tuple[str, Tuple[str, float, str, float]], List[Tuple[float, QtGui.QImage]]] = {}
        self._selected_images_by_path: Dict[str, QtGui.QImage] = {}
        self._batch_running = False
        self._batch_paused = False
        self._batch_stop_requested = False

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        top = QtWidgets.QHBoxLayout()
        self.btn_add_files = QtWidgets.QPushButton("Bestanden")
        self.btn_add_folder = QtWidgets.QPushButton("Map")
        self.btn_remove = QtWidgets.QPushButton("Verwijder")
        self.btn_clear = QtWidgets.QPushButton("Leeg")
        self.combo_mode = QtWidgets.QComboBox()
        self.combo_mode.addItems([MODE_GPU, MODE_CPU])
        self.combo_mode.setCurrentText(MODE_GPU if self._cuda_available else MODE_CPU)
        self.lbl_backend = QtWidgets.QLabel(
            "CUDA beschikbaar" if self._cuda_available else "CUDA niet beschikbaar, CPU actief"
        )
        top.addWidget(self.btn_add_files)
        top.addWidget(self.btn_add_folder)
        top.addWidget(self.btn_remove)
        top.addWidget(self.btn_clear)
        top.addSpacing(16)
        top.addWidget(QtWidgets.QLabel("Verwerking:"))
        top.addWidget(self.combo_mode)
        top.addWidget(self.lbl_backend)
        top.addStretch(1)
        root.addLayout(top)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(10)

        left_box = QtWidgets.QFrame()
        left_lay = QtWidgets.QVBoxLayout(left_box)
        left_lay.addWidget(QtWidgets.QLabel("Bestandslijst"))
        self.files = QtWidgets.QListWidget()
        self.files.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.files.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.files.setAcceptDrops(True)
        left_lay.addWidget(self.files, 1)
        body.addWidget(left_box, 2)

        center_box = QtWidgets.QFrame()
        center_lay = QtWidgets.QVBoxLayout(center_box)
        center_lay.addWidget(QtWidgets.QLabel("Geselecteerde thumbnail"))
        self.preview = QtWidgets.QLabel("Geen selectie")
        self.preview.setMinimumSize(460, 280)
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        self.preview.setStyleSheet(
            "QLabel{background:#101820; border:1px solid #2A3C4A; border-radius:10px;}"
        )
        self.preview.setAcceptDrops(True)
        self.preview.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        center_lay.addWidget(self.preview, 1)
        self.btn_save = QtWidgets.QPushButton("Wijzigen")
        self.btn_save.setEnabled(False)
        center_lay.addWidget(self.btn_save)
        output_row = QtWidgets.QHBoxLayout()
        self.edit_output_dir = QtWidgets.QLineEdit()
        self.edit_output_dir.setReadOnly(True)
        self.btn_output_dir = QtWidgets.QPushButton("Outputmap")
        self.btn_output_source = QtWidgets.QPushButton("Bronmap")
        output_row.addWidget(QtWidgets.QLabel("Output"))
        output_row.addWidget(self.edit_output_dir, 1)
        output_row.addWidget(self.btn_output_dir)
        output_row.addWidget(self.btn_output_source)
        center_lay.addLayout(output_row)
        batch_row = QtWidgets.QHBoxLayout()
        self.btn_start = QtWidgets.QPushButton("Start")
        self.btn_pause = QtWidgets.QPushButton("Pauzeer")
        self.btn_stop = QtWidgets.QPushButton("Stop")
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        batch_row.addWidget(self.btn_start)
        batch_row.addWidget(self.btn_pause)
        batch_row.addWidget(self.btn_stop)
        center_lay.addLayout(batch_row)
        self.lbl_batch_status = QtWidgets.QLabel("Batch gereed")
        center_lay.addWidget(self.lbl_batch_status)
        body.addWidget(center_box, 3)

        right_box = QtWidgets.QFrame()
        right_lay = QtWidgets.QVBoxLayout(right_box)
        self.lbl_candidates_title = QtWidgets.QLabel("Frames uit eerste minuut")
        right_lay.addWidget(self.lbl_candidates_title)
        scan_controls = QtWidgets.QGridLayout()
        self.combo_scan_mode = QtWidgets.QComboBox()
        self.combo_scan_mode.addItems([SCAN_MODE_DEFAULT, SCAN_MODE_CUSTOM])
        self.spin_scan_from = QtWidgets.QDoubleSpinBox()
        self.spin_scan_from.setRange(0.0, 3600.0)
        self.spin_scan_from.setDecimals(1)
        self.spin_scan_from.setSingleStep(0.5)
        self.spin_scan_from.setValue(0.0)
        self.spin_scan_from.setSuffix(" s")
        self.edit_scan_until = QtWidgets.QLineEdit("einde")
        self.edit_scan_until.setPlaceholderText("einde of seconden")
        self.spin_prints_per = QtWidgets.QDoubleSpinBox()
        self.spin_prints_per.setRange(0.1, 300.0)
        self.spin_prints_per.setDecimals(1)
        self.spin_prints_per.setSingleStep(0.5)
        self.spin_prints_per.setValue(1.0)
        self.spin_prints_per.setSuffix(" s")
        self.btn_rescan = QtWidgets.QPushButton("Opnieuw scannen")
        self.btn_batch_scan = QtWidgets.QPushButton("Batch scannen")
        scan_controls.addWidget(QtWidgets.QLabel("Modus"), 0, 0)
        scan_controls.addWidget(self.combo_scan_mode, 0, 1, 1, 3)
        self.lbl_scan_from = QtWidgets.QLabel("Tijd")
        self.lbl_prints_per = QtWidgets.QLabel("Neem printscreens elke")
        self.lbl_scan_until_hint = QtWidgets.QLabel("")
        self.lbl_scan_until_hint.setStyleSheet("color:#9EB0BE;")
        scan_controls.addWidget(self.lbl_scan_from, 1, 0)
        scan_controls.addWidget(self.spin_scan_from, 1, 1)
        scan_controls.addWidget(QtWidgets.QLabel("Tot"), 1, 2)
        scan_controls.addWidget(self.edit_scan_until, 1, 3)
        scan_controls.addWidget(self.lbl_scan_until_hint, 2, 0, 1, 4)
        scan_controls.addWidget(self.lbl_prints_per, 3, 0, 1, 2)
        scan_controls.addWidget(self.spin_prints_per, 3, 2, 1, 2)
        scan_controls.addWidget(self.btn_rescan, 4, 0, 1, 2)
        scan_controls.addWidget(self.btn_batch_scan, 4, 2, 1, 2)
        right_lay.addLayout(scan_controls)
        self.candidates = QtWidgets.QListWidget()
        self.candidates.setViewMode(QtWidgets.QListWidget.IconMode)
        self.candidates.setResizeMode(QtWidgets.QListWidget.Adjust)
        self.candidates.setMovement(QtWidgets.QListWidget.Static)
        self.candidates.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.candidates.setSpacing(8)
        self.candidates.setIconSize(QtCore.QSize(220, 124))
        self.candidates.setGridSize(QtCore.QSize(240, 170))
        self.candidates.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.candidates.setAcceptDrops(True)
        right_lay.addWidget(self.candidates, 1)
        body.addWidget(right_box, 4)

        root.addLayout(body, 1)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("Voortgang: %p%")
        root.addWidget(self.progress)

        self.btn_add_files.clicked.connect(self.on_add_files)
        self.btn_add_folder.clicked.connect(self.on_add_folder)
        self.btn_remove.clicked.connect(self.on_remove)
        self.btn_clear.clicked.connect(self.on_clear)
        self.combo_mode.currentIndexChanged.connect(self.on_mode_changed)
        self.combo_scan_mode.currentIndexChanged.connect(self.on_scan_mode_changed)
        self.spin_scan_from.valueChanged.connect(self.on_scan_settings_changed)
        self.edit_scan_until.textChanged.connect(self.on_scan_settings_changed)
        self.edit_scan_until.editingFinished.connect(self.on_scan_settings_changed)
        self.spin_prints_per.valueChanged.connect(self.on_scan_settings_changed)
        self.btn_rescan.clicked.connect(self.on_rescan_current)
        self.btn_batch_scan.clicked.connect(self.on_batch_scan)
        self.btn_output_dir.clicked.connect(self.on_choose_output_dir)
        self.btn_output_source.clicked.connect(self.on_reset_output_dir)
        self.btn_start.clicked.connect(self.on_start_batch)
        self.btn_pause.clicked.connect(self.on_pause_batch)
        self.btn_stop.clicked.connect(self.on_stop_batch)
        self.files.currentItemChanged.connect(self.on_file_changed)
        self.files.customContextMenuRequested.connect(self.show_files_context_menu)
        self.candidates.itemClicked.connect(self.on_candidate_clicked)
        self.candidates.customContextMenuRequested.connect(self.show_candidates_context_menu)
        self.preview.customContextMenuRequested.connect(self.show_preview_context_menu)
        self.btn_save.clicked.connect(self.on_save_thumbnail)
        self.files.installEventFilter(self)
        self.candidates.installEventFilter(self)
        self.preview.installEventFilter(self)

        if cv2 is None:
            QtWidgets.QMessageBox.warning(
                self,
                APP_TITLE,
                "OpenCV is niet beschikbaar. Installeer opencv-python in je venv.",
            )
            self.combo_mode.setCurrentText(MODE_CPU)
            self.combo_mode.setEnabled(False)
            self.lbl_backend.setText("OpenCV ontbreekt")
        if self._ffmpeg_path is None:
            self.btn_save.setEnabled(False)
            self.btn_save.setToolTip("FFmpeg niet gevonden in PATH")

        self._startup_timer = QtCore.QTimer(self)
        self._startup_timer.setInterval(35)
        self._startup_timer.timeout.connect(self._finish_startup_progress)
        self._startup_timer.start()
        self._update_scan_controls()
        self._update_batch_controls()

    def _finish_startup_progress(self):
        step = min(100, self.progress.value() + 20)
        self._set_progress(step)
        if step >= 100:
            self._startup_timer.stop()

    def _set_progress(self, value: int):
        value = max(0, min(100, int(value)))
        self.progress.setValue(value)
        self._update_taskbar_progress(value)
        QtWidgets.QApplication.processEvents()

    def _update_taskbar_progress(self, value: int):
        if self._taskbar_progress is not None:
            if 0 <= value < 100:
                self._taskbar_progress.show()
                self._taskbar_progress.setValue(value)
            else:
                self._taskbar_progress.setValue(100)
                self._taskbar_progress.hide()
        if 0 <= value < 100:
            self.setWindowTitle(f"{APP_TITLE} - verwerken {value}%")
        else:
            self.setWindowTitle(APP_TITLE)

    def _init_taskbar_integration(self):
        if QWinTaskbarButton is None or self._taskbar_button is not None:
            return
        handle = self.windowHandle()
        if handle is None:
            return
        self._taskbar_button = QWinTaskbarButton(self)
        self._taskbar_button.setWindow(handle)
        self._taskbar_progress = self._taskbar_button.progress()
        self._taskbar_progress.setRange(0, 100)
        self._taskbar_progress.setValue(self.progress.value())
        if self.progress.value() >= 100:
            self._taskbar_progress.hide()
        else:
            self._taskbar_progress.show()

    def _set_last_dir(self, directory: Path):
        self.last_dir = str(directory)
        self._settings.setValue("last_dir", self.last_dir)

    def _set_batch_controls(self, running: bool):
        self._batch_running = running
        self.btn_start.setEnabled(not running)
        self.btn_pause.setEnabled(running)
        self.btn_stop.setEnabled(running)
        self.btn_pause.setText("Hervat" if self._batch_paused else "Pauzeer")

    def _update_batch_controls(self):
        self._set_batch_controls(self._batch_running)

    def _source_output_dir(self, path: str) -> str:
        return str(Path(path).parent)

    def _file_item_by_path(self, path: str) -> Optional[QtWidgets.QListWidgetItem]:
        for i in range(self.files.count()):
            item = self.files.item(i)
            if item.data(ROLE_PATH) == path:
                return item
        return None

    def _item_output_dir(self, item: QtWidgets.QListWidgetItem) -> str:
        output_dir = item.data(ROLE_OUTPUT_DIR)
        if output_dir:
            return str(output_dir)
        path = item.data(ROLE_PATH)
        return self._source_output_dir(str(path))

    def _set_item_output_dir(self, item: QtWidgets.QListWidgetItem, output_dir: str):
        item.setData(ROLE_OUTPUT_DIR, output_dir)
        item.setToolTip(f"{item.data(ROLE_PATH)}\nOutput: {output_dir}")

    def _refresh_output_dir_ui(self):
        cur = self.files.currentItem()
        if not cur:
            self.edit_output_dir.clear()
            return
        self.edit_output_dir.setText(self._item_output_dir(cur))

    def _default_marks(self) -> List[float]:
        return [0, 5, 10, 15, 20, 30, 40, 50, 60]

    def _parse_scan_until_text(self) -> str:
        text = self.edit_scan_until.text().strip().lower()
        return text or "einde"

    def _format_seconds_text(self, sec: float) -> str:
        return f"{sec:.1f}".replace(".", ",")

    def _humanize_seconds(self, sec: float) -> str:
        minutes = int(sec // 60)
        seconds = sec - (minutes * 60)
        parts = []
        if minutes > 0:
            parts.append(f"{minutes} min")
        if abs(seconds - round(seconds)) < 1e-9:
            whole_seconds = int(round(seconds))
            if whole_seconds > 0 or not parts:
                parts.append(f"{whole_seconds} sec")
        else:
            parts.append(f"{self._format_seconds_text(seconds)} sec")
        return " ".join(parts)

    def _update_scan_until_hint(self):
        if self.combo_scan_mode.currentText() != SCAN_MODE_CUSTOM:
            self.lbl_scan_until_hint.clear()
            return
        until_text = self._parse_scan_until_text()
        if until_text in {"", "einde", "end"}:
            self.lbl_scan_until_hint.setText("Tot einde van het bestand")
            return
        try:
            sec = max(0.0, float(until_text.replace(",", ".")))
        except ValueError:
            self.lbl_scan_until_hint.setText("Voer seconden in of gebruik 'einde'")
            return
        self.lbl_scan_until_hint.setText(
            f"{self._format_seconds_text(sec)} sec / {self._humanize_seconds(sec)}"
        )

    def _current_scan_signature(self) -> Tuple[str, float, str, float]:
        if self.combo_scan_mode.currentText() == SCAN_MODE_DEFAULT:
            return (SCAN_MODE_DEFAULT, 0.0, "60.0", round(float(self.spin_prints_per.value()), 3))
        scan_from = round(float(self.spin_scan_from.value()), 3)
        until = self._parse_scan_until_text()
        prints_per = round(float(self.spin_prints_per.value()), 3)
        return (SCAN_MODE_CUSTOM, scan_from, until, prints_per)

    def _video_duration(self, path: str) -> float:
        if cv2 is None:
            return 0.0
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return 0.0
        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
        finally:
            cap.release()
        if fps <= 0 or frame_count <= 0:
            return 0.0
        return frame_count / fps

    def _resolve_scan_end(self, path: str, scan_from: float) -> float:
        until_text = self._parse_scan_until_text()
        if until_text in {"", "einde", "end"}:
            duration = self._video_duration(path)
            return max(scan_from, duration if duration > 0 else scan_from)
        try:
            return max(scan_from, float(until_text.replace(",", ".")))
        except ValueError:
            duration = self._video_duration(path)
            return max(scan_from, duration if duration > 0 else scan_from)

    def _effective_scan_range(self, path: str) -> Tuple[float, float]:
        if self.combo_scan_mode.currentText() == SCAN_MODE_DEFAULT:
            duration = self._video_duration(path)
            end = 60.0
            if duration > 0:
                end = min(duration, 60.0)
            return (0.0, max(0.0, end))
        scan_from = max(0.0, float(self.spin_scan_from.value()))
        until = self._resolve_scan_end(path, scan_from)
        return (scan_from, until)

    def _current_marks(self, path: str) -> List[float]:
        if self.combo_scan_mode.currentText() == SCAN_MODE_DEFAULT and abs(float(self.spin_prints_per.value()) - 5.0) < 1e-9:
            return self._default_marks()
        scan_from, until = self._effective_scan_range(path)
        interval = max(0.1, float(self.spin_prints_per.value()))
        marks: List[float] = []
        current = scan_from
        while current <= until + 1e-9:
            marks.append(round(current, 3))
            current += interval
        return marks

    def _sync_scan_fields_for_path(self, path: str):
        duration = self._video_duration(path)
        if self.combo_scan_mode.currentText() == SCAN_MODE_DEFAULT:
            start = 0.0
            end = min(duration, 60.0) if duration > 0 else 60.0
        else:
            start = 0.0
            end = duration if duration > 0 else 0.0
        blockers = [
            QtCore.QSignalBlocker(self.spin_scan_from),
            QtCore.QSignalBlocker(self.edit_scan_until),
        ]
        try:
            self.spin_scan_from.setValue(start)
            self.edit_scan_until.setText(self._format_seconds_text(end) if end > 0 else "einde")
        finally:
            del blockers
        self._update_scan_until_hint()

    def _format_mark(self, sec: float) -> str:
        if abs(sec - round(sec)) < 1e-9:
            return f"{int(round(sec))}s"
        return f"{sec:.1f}s"

    def _update_scan_controls(self):
        self.spin_scan_from.setEnabled(True)
        self.edit_scan_until.setEnabled(True)
        self.spin_prints_per.setEnabled(True)
        if self.combo_scan_mode.currentText() == SCAN_MODE_CUSTOM:
            self.lbl_candidates_title.setText("Frames voor aangepast bereik")
        else:
            self.lbl_candidates_title.setText("Frames uit eerste minuut")
        self._update_scan_until_hint()

    def _current_path(self) -> Optional[str]:
        cur = self.files.currentItem()
        if not cur:
            return None
        path = cur.data(ROLE_PATH)
        if not path:
            return None
        return str(path)

    def _populate_candidates(self, items: List[Tuple[float, QtGui.QImage]]):
        self.candidates.clear()
        for sec, img in items:
            it = QtWidgets.QListWidgetItem(QtGui.QIcon(QtGui.QPixmap.fromImage(img)), self._format_mark(sec))
            it.setData(ROLE_IMAGE, img)
            it.setData(ROLE_SECONDS, sec)
            self.candidates.addItem(it)
        if self.candidates.count() > 0:
            self.candidates.setCurrentRow(0)
            self.on_candidate_clicked(self.candidates.item(0))

    def _scan_file(self, path: str, progress_start: int, progress_end: int) -> List[Tuple[float, QtGui.QImage]]:
        marks = self._current_marks(path)
        total = max(1, len(marks))
        results: List[Tuple[float, QtGui.QImage]] = []
        for idx, sec in enumerate(marks, start=1):
            img = self._read_frame_at(path, sec)
            if img is not None:
                results.append((sec, img))
            progress = progress_start + ((progress_end - progress_start) * idx // total)
            self._set_progress(progress)
        return results

    def _load_candidates_for_path(self, path: str, force: bool = False):
        self.preview.clear()
        self.preview.setText("Geen selectie")
        self._selected_image = None
        self.btn_save.setEnabled(False)
        signature = self._current_scan_signature()
        cache_key = (path, signature)
        if force or cache_key not in self._scan_cache:
            self._set_progress(0)
            self._scan_cache[cache_key] = self._scan_file(path, 0, 100)
        else:
            self._set_progress(100)
        self._populate_candidates(self._scan_cache.get(cache_key, []))
        self._set_progress(100)

    def _add_paths(self, paths):
        added_any = False
        first_parent: Optional[Path] = None
        for raw in paths:
            p = Path(raw)
            if not p.exists():
                continue
            if first_parent is None:
                first_parent = p.parent if p.is_file() else p
            if p.is_dir():
                for f in p.rglob("*"):
                    if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
                        before = self.files.count()
                        self._add_path(f)
                        if self.files.count() > before:
                            added_any = True
            elif p.is_file():
                before = self.files.count()
                self._add_path(p)
                if self.files.count() > before:
                    added_any = True
        if first_parent is not None:
            self._set_last_dir(first_parent)
        if added_any and self.files.currentRow() < 0:
            self.files.setCurrentRow(0)

    def _detect_cuda(self) -> bool:
        if cv2 is None:
            return False
        try:
            return hasattr(cv2, "cuda") and cv2.cuda.getCudaEnabledDeviceCount() > 0
        except Exception:
            return False

    def _add_path(self, p: Path):
        if p.suffix.lower() not in VIDEO_EXTS:
            return
        sp = str(p)
        for i in range(self.files.count()):
            if self.files.item(i).data(ROLE_PATH) == sp:
                return
        it = QtWidgets.QListWidgetItem(p.name)
        it.setData(ROLE_PATH, sp)
        self._set_item_output_dir(it, str(p.parent))
        self.files.addItem(it)

    def on_add_files(self):
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Kies videobestanden",
            self.last_dir,
            "Video files (*.*)",
        )
        if not files:
            return
        self._add_paths(files)

    def on_add_folder(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Kies map met video's",
            self.last_dir,
        )
        if not d:
            return
        self._add_paths([d])

    def on_remove(self):
        row = self.files.currentRow()
        if row >= 0:
            self.files.takeItem(row)
        if self.files.count() == 0:
            self.on_clear()

    def on_clear(self):
        self.files.clear()
        self.candidates.clear()
        self.preview.clear()
        self.preview.setText("Geen selectie")
        self._selected_image = None
        self._selected_images_by_path.clear()
        self._scan_cache.clear()
        self.edit_output_dir.clear()
        self.btn_save.setEnabled(False)
        self.lbl_batch_status.setText("Batch gereed")

    def _selected_mode_uses_gpu(self) -> bool:
        return self.combo_mode.currentText() == MODE_GPU

    def _process_frame_cpu(self, frame):
        h, w = frame.shape[:2]
        if w <= 0 or h <= 0:
            return None
        target_w = 320
        target_h = max(1, int(target_w * h / w))
        frame = cv2.resize(frame, (target_w, target_h))
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    def _process_frame_gpu(self, frame):
        # Decode blijft via VideoCapture; resize/kleurconversie gebruiken CUDA.
        h, w = frame.shape[:2]
        if w <= 0 or h <= 0:
            return None
        target_w = 320
        target_h = max(1, int(target_w * h / w))
        try:
            gpu = cv2.cuda_GpuMat()
            gpu.upload(frame)
            gpu_resized = cv2.cuda.resize(gpu, (target_w, target_h))
            if hasattr(cv2.cuda, "cvtColor"):
                gpu_rgb = cv2.cuda.cvtColor(gpu_resized, cv2.COLOR_BGR2RGB)
                return gpu_rgb.download()
            return cv2.cvtColor(gpu_resized.download(), cv2.COLOR_BGR2RGB)
        except Exception:
            # Als CUDA-pad faalt, automatisch CPU gebruiken.
            return self._process_frame_cpu(frame)

    def _read_frame_at(self, path: str, sec: float) -> Optional[QtGui.QImage]:
        if cv2 is None:
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
        if self._selected_mode_uses_gpu() and self._cuda_available:
            processed = self._process_frame_gpu(frame)
            self.lbl_backend.setText("GPU (CUDA) actief")
        else:
            processed = self._process_frame_cpu(frame)
            if self._selected_mode_uses_gpu() and not self._cuda_available:
                self.lbl_backend.setText("CUDA niet beschikbaar, CPU actief")
            else:
                self.lbl_backend.setText("CPU actief")
        if processed is None:
            return None
        return QtGui.QImage(
            processed.data,
            processed.shape[1],
            processed.shape[0],
            processed.strides[0],
            QtGui.QImage.Format_RGB888,
        ).copy()

    def on_mode_changed(self):
        if self._selected_mode_uses_gpu() and not self._cuda_available:
            self.lbl_backend.setText("CUDA niet beschikbaar, CPU actief")
        elif self._selected_mode_uses_gpu():
            self.lbl_backend.setText("GPU (CUDA) actief")
        else:
            self.lbl_backend.setText("CPU actief")
        cur = self.files.currentItem()
        if cur:
            self.on_file_changed(cur, None)

    def on_scan_settings_changed(self):
        self._update_scan_controls()

    def on_scan_mode_changed(self):
        self._update_scan_controls()
        path = self._current_path()
        if path:
            self._sync_scan_fields_for_path(path)

    def on_rescan_current(self):
        path = self._current_path()
        if not path:
            return
        self._load_candidates_for_path(path, force=True)

    def on_batch_scan(self):
        if self.files.count() == 0:
            return
        signature = self._current_scan_signature()
        total_files = self.files.count()
        self._set_progress(0)
        for i in range(total_files):
            item = self.files.item(i)
            path = item.data(ROLE_PATH)
            if not path:
                continue
            start = i * 100 // total_files
            end = (i + 1) * 100 // total_files
            self._scan_cache[(str(path), signature)] = self._scan_file(str(path), start, end)
        current_path = self._current_path()
        if current_path:
            self._populate_candidates(self._scan_cache.get((current_path, signature), []))
        self._set_progress(100)
        QtWidgets.QMessageBox.information(self, APP_TITLE, "Batch scan voltooid.")

    def on_choose_output_dir(self):
        cur = self.files.currentItem()
        if not cur:
            return
        current_dir = self._item_output_dir(cur)
        chosen = QtWidgets.QFileDialog.getExistingDirectory(self, "Kies outputmap", current_dir)
        if not chosen:
            return
        self._set_item_output_dir(cur, chosen)
        self._refresh_output_dir_ui()

    def on_reset_output_dir(self):
        cur = self.files.currentItem()
        if not cur:
            return
        source_dir = self._source_output_dir(str(cur.data(ROLE_PATH)))
        self._set_item_output_dir(cur, source_dir)
        self._refresh_output_dir_ui()

    def on_start_batch(self):
        if self.files.count() == 0:
            return
        if self._ffmpeg_path is None:
            QtWidgets.QMessageBox.warning(self, APP_TITLE, "FFmpeg niet gevonden. Plaats ffmpeg.exe in PATH.")
            return
        self._batch_paused = False
        self._batch_stop_requested = False
        self.lbl_batch_status.setText("Batch gestart")
        self._set_batch_controls(True)
        total_files = self.files.count()
        processed = 0
        skipped = 0
        for i in range(total_files):
            QtWidgets.QApplication.processEvents()
            if self._batch_stop_requested:
                self.lbl_batch_status.setText(f"Batch gestopt na {processed} bestand(en)")
                break
            while self._batch_paused and not self._batch_stop_requested:
                self.lbl_batch_status.setText("Batch gepauzeerd")
                QtWidgets.QApplication.processEvents()
                QtCore.QThread.msleep(100)
            if self._batch_stop_requested:
                self.lbl_batch_status.setText(f"Batch gestopt na {processed} bestand(en)")
                break

            item = self.files.item(i)
            path = item.data(ROLE_PATH)
            if not path:
                skipped += 1
                continue
            path = str(path)
            image = self._selected_images_by_path.get(path)
            if image is None:
                signature = self._current_scan_signature()
                cache_key = (path, signature)
                if cache_key not in self._scan_cache:
                    start = i * 100 // total_files
                    end = start + max(1, (100 // max(1, total_files)) // 2)
                    self._scan_cache[cache_key] = self._scan_file(path, start, end)
                candidates = self._scan_cache.get(cache_key, [])
                if candidates:
                    image = candidates[0][1]
                    self._selected_images_by_path[path] = image
            if image is None:
                skipped += 1
                continue

            output_dir = Path(self._item_output_dir(item))
            base_start = i * 100 // total_files
            base_end = (i + 1) * 100 // total_files
            self.lbl_batch_status.setText(f"Bezig met: {Path(path).name}")
            ok, message = self._write_thumbnail(Path(path), image, output_dir, base_start, base_end)
            if not ok:
                self.lbl_batch_status.setText(f"Fout bij: {Path(path).name}")
                QtWidgets.QMessageBox.warning(self, APP_TITLE, message)
                break
            processed += 1
        else:
            self.lbl_batch_status.setText(f"Batch klaar: {processed} verwerkt, {skipped} overgeslagen")

        self._batch_paused = False
        self._batch_stop_requested = False
        self._set_progress(100)
        self._set_batch_controls(False)

    def on_pause_batch(self):
        if not self._batch_running:
            return
        self._batch_paused = not self._batch_paused
        self.btn_pause.setText("Hervat" if self._batch_paused else "Pauzeer")
        self.lbl_batch_status.setText("Batch gepauzeerd" if self._batch_paused else "Batch hervat")

    def on_stop_batch(self):
        if not self._batch_running:
            return
        self._batch_stop_requested = True
        self.lbl_batch_status.setText("Batch stopt na huidig bestand")

    def showEvent(self, event: QtGui.QShowEvent):
        super().showEvent(event)
        self._init_taskbar_integration()

    def eventFilter(self, watched, event):
        if watched in {self.files, self.candidates, self.preview}:
            if event.type() in {QtCore.QEvent.DragEnter, QtCore.QEvent.DragMove}:
                if event.mimeData().hasUrls():
                    event.acceptProposedAction()
                    return True
            if event.type() == QtCore.QEvent.Drop:
                if event.mimeData().hasUrls():
                    local_paths = []
                    for url in event.mimeData().urls():
                        if url.isLocalFile():
                            local_paths.append(url.toLocalFile())
                    if local_paths:
                        self._add_paths(local_paths)
                        event.acceptProposedAction()
                        return True
        return super().eventFilter(watched, event)

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QtGui.QDropEvent):
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        local_paths = []
        for url in event.mimeData().urls():
            if url.isLocalFile():
                local_paths.append(url.toLocalFile())
        if local_paths:
            self._add_paths(local_paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    def show_files_context_menu(self, pos: QtCore.QPoint):
        menu = QtWidgets.QMenu(self)
        act_add_files = menu.addAction("Bestanden toevoegen")
        act_add_folder = menu.addAction("Map toevoegen")
        act_output = menu.addAction("Outputmap kiezen")
        act_output_source = menu.addAction("Outputmap = bronmap")
        act_rescan = menu.addAction("Huidig bestand opnieuw scannen")
        act_batch = menu.addAction("Alles batch scannen")
        act_start = menu.addAction("Batch starten")
        act_pause = menu.addAction("Batch pauzeren/hervatten")
        act_stop = menu.addAction("Batch stoppen")
        menu.addSeparator()
        act_remove = menu.addAction("Geselecteerd verwijderen")
        act_clear = menu.addAction("Lijst leegmaken")
        if not self._batch_running:
            act_pause.setEnabled(False)
            act_stop.setEnabled(False)
        selected = menu.exec(self.files.mapToGlobal(pos))
        if selected == act_add_files:
            self.on_add_files()
        elif selected == act_add_folder:
            self.on_add_folder()
        elif selected == act_output:
            self.on_choose_output_dir()
        elif selected == act_output_source:
            self.on_reset_output_dir()
        elif selected == act_rescan:
            self.on_rescan_current()
        elif selected == act_batch:
            self.on_batch_scan()
        elif selected == act_start:
            self.on_start_batch()
        elif selected == act_pause:
            self.on_pause_batch()
        elif selected == act_stop:
            self.on_stop_batch()
        elif selected == act_remove:
            self.on_remove()
        elif selected == act_clear:
            self.on_clear()

    def show_candidates_context_menu(self, pos: QtCore.QPoint):
        menu = QtWidgets.QMenu(self)
        item = self.candidates.itemAt(pos)
        act_select = menu.addAction("Deze frame kiezen")
        act_change = menu.addAction("Miniatuur wijzigen")
        act_output = menu.addAction("Outputmap kiezen")
        act_output_source = menu.addAction("Outputmap = bronmap")
        act_rescan = menu.addAction("Huidig bestand opnieuw scannen")
        act_batch = menu.addAction("Alles batch scannen")
        act_start = menu.addAction("Batch starten")
        act_pause = menu.addAction("Batch pauzeren/hervatten")
        act_stop = menu.addAction("Batch stoppen")
        if item is None:
            act_select.setEnabled(False)
            act_change.setEnabled(False)
        if self._selected_image is None or self._ffmpeg_path is None:
            act_change.setEnabled(False)
        if not self._batch_running:
            act_pause.setEnabled(False)
            act_stop.setEnabled(False)
        selected = menu.exec(self.candidates.mapToGlobal(pos))
        if selected == act_select and item is not None:
            self.candidates.setCurrentItem(item)
            self.on_candidate_clicked(item)
        elif selected == act_change:
            self.on_save_thumbnail()
        elif selected == act_output:
            self.on_choose_output_dir()
        elif selected == act_output_source:
            self.on_reset_output_dir()
        elif selected == act_rescan:
            self.on_rescan_current()
        elif selected == act_batch:
            self.on_batch_scan()
        elif selected == act_start:
            self.on_start_batch()
        elif selected == act_pause:
            self.on_pause_batch()
        elif selected == act_stop:
            self.on_stop_batch()

    def show_preview_context_menu(self, pos: QtCore.QPoint):
        menu = QtWidgets.QMenu(self)
        act_change = menu.addAction("Miniatuur wijzigen")
        act_output = menu.addAction("Outputmap kiezen")
        act_output_source = menu.addAction("Outputmap = bronmap")
        act_rescan = menu.addAction("Huidig bestand opnieuw scannen")
        act_batch = menu.addAction("Alles batch scannen")
        act_start = menu.addAction("Batch starten")
        act_pause = menu.addAction("Batch pauzeren/hervatten")
        act_stop = menu.addAction("Batch stoppen")
        if self._selected_image is None or self._ffmpeg_path is None:
            act_change.setEnabled(False)
        if not self._batch_running:
            act_pause.setEnabled(False)
            act_stop.setEnabled(False)
        selected = menu.exec(self.preview.mapToGlobal(pos))
        if selected == act_change:
            self.on_save_thumbnail()
        elif selected == act_output:
            self.on_choose_output_dir()
        elif selected == act_output_source:
            self.on_reset_output_dir()
        elif selected == act_rescan:
            self.on_rescan_current()
        elif selected == act_batch:
            self.on_batch_scan()
        elif selected == act_start:
            self.on_start_batch()
        elif selected == act_pause:
            self.on_pause_batch()
        elif selected == act_stop:
            self.on_stop_batch()

    def closeEvent(self, event: QtGui.QCloseEvent):
        self._settings.setValue("last_dir", self.last_dir)
        super().closeEvent(event)

    def on_file_changed(self, cur: Optional[QtWidgets.QListWidgetItem], _prev: Optional[QtWidgets.QListWidgetItem]):
        self.candidates.clear()
        self.preview.clear()
        self.preview.setText("Geen selectie")
        self._selected_image = None
        self.btn_save.setEnabled(False)
        if not cur:
            self.edit_output_dir.clear()
            self._set_progress(100)
            return
        path = cur.data(ROLE_PATH)
        if not path:
            self._set_progress(100)
            return
        self._refresh_output_dir_ui()
        self._sync_scan_fields_for_path(str(path))
        self._load_candidates_for_path(str(path), force=False)

    def on_candidate_clicked(self, item: QtWidgets.QListWidgetItem):
        img = item.data(ROLE_IMAGE)
        if not isinstance(img, QtGui.QImage):
            return
        self._selected_image = img
        path = self._current_path()
        if path:
            self._selected_images_by_path[path] = img
        pix = QtGui.QPixmap.fromImage(img)
        shown = pix.scaled(
            self.preview.size(),
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
        self.preview.setPixmap(shown)
        self.btn_save.setEnabled(self._ffmpeg_path is not None)

    def _write_thumbnail(self, src: Path, image: QtGui.QImage, output_dir: Path, progress_start: int, progress_end: int) -> Tuple[bool, str]:
        if self._ffmpeg_path is None:
            return (False, "FFmpeg niet gevonden. Plaats ffmpeg.exe in PATH.")
        ext = src.suffix.lower()
        if ext not in {".mp4", ".m4v", ".mov", ".mkv", ".webm"}:
            return (False, f"Bestandstype {ext} wordt nog niet ondersteund voor embedded miniaturen.")
        output_dir.mkdir(parents=True, exist_ok=True)
        final_output = output_dir / src.name
        same_target = final_output.resolve() == src.resolve()
        try:
            temp_root = final_output.parent
            with tempfile.TemporaryDirectory(prefix="thumb_edit_", dir=str(temp_root)) as td:
                cover_path = Path(td) / "cover.jpg"
                temp_output = Path(td) / f"{src.stem}.out{src.suffix}"
                ok = image.save(str(cover_path), "JPG", 95)
                if not ok:
                    return (False, "Kon geselecteerde afbeelding niet voorbereiden.")
                self._set_progress(progress_start + ((progress_end - progress_start) * 20 // 100))

                if ext in {".mp4", ".m4v", ".mov"}:
                    cmd = [
                        self._ffmpeg_path,
                        "-y",
                        "-i",
                        str(src),
                        "-i",
                        str(cover_path),
                        "-map",
                        "0",
                        "-map",
                        "1",
                        "-c",
                        "copy",
                        "-disposition:v:1",
                        "attached_pic",
                        "-metadata:s:v:1",
                        "title=Thumbnail",
                        "-metadata:s:v:1",
                        "comment=Cover (front)",
                        str(temp_output),
                    ]
                else:
                    cmd = [
                        self._ffmpeg_path,
                        "-y",
                        "-i",
                        str(src),
                        "-attach",
                        str(cover_path),
                        "-metadata:s:t",
                        "mimetype=image/jpeg",
                        "-metadata:s:t",
                        "filename=cover.jpg",
                        "-c",
                        "copy",
                        str(temp_output),
                    ]

                self._set_progress(progress_start + ((progress_end - progress_start) * 40 // 100))
                proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if proc.returncode != 0 or not temp_output.exists():
                    return (
                        False,
                        "Miniatuur wijzigen mislukt.\n\n"
                        f"FFmpeg exitcode: {proc.returncode}\n{proc.stderr[-1200:]}",
                    )

                self._set_progress(progress_start + ((progress_end - progress_start) * 85 // 100))
                if same_target:
                    backup = src.with_name(src.name + ".thumbbak")
                    if backup.exists():
                        backup.unlink()
                    os.replace(str(src), str(backup))
                    try:
                        os.replace(str(temp_output), str(src))
                    except Exception:
                        os.replace(str(backup), str(src))
                        raise
                    if backup.exists():
                        backup.unlink()
                    result_path = src
                else:
                    if final_output.exists():
                        final_output.unlink()
                    os.replace(str(temp_output), str(final_output))
                    result_path = final_output
                self._set_progress(progress_end)
                return (True, str(result_path))
        except Exception as exc:
            return (False, f"Miniatuur wijzigen mislukt:\n{exc}")

    def on_save_thumbnail(self):
        if self._selected_image is None:
            return
        cur = self.files.currentItem()
        if not cur:
            return
        src = Path(cur.data(ROLE_PATH))
        output_dir = Path(self._item_output_dir(cur))
        self._set_progress(0)
        ok, message = self._write_thumbnail(src, self._selected_image, output_dir, 0, 100)
        if not ok:
            QtWidgets.QMessageBox.warning(self, APP_TITLE, message)
            self._set_progress(100)
            return
        QtWidgets.QMessageBox.information(self, APP_TITLE, f"Miniatuur bijgewerkt:\n{message}")


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = ThumbnailEditorWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
