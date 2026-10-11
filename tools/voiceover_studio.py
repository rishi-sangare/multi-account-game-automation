"""Voice-over Studio: a teleprompter that records your narration in sync with the demo video.

    .venv\\Scripts\\python tools\\voiceover_studio.py [recordings\\<folder>]

* Plays demo_captioned.mp4 (muted) and shows the line to say right now, word by word, plus the next line.
* Records your microphone while the video plays (WAV, 48 kHz) - sync is measured automatically.
* "Merge into video" puts the voice on the video (rumble filter + loudness to -16 LUFS) -> demo_final.mp4.

Keys: Space = record / stop · R = rehearse (no recording) · Esc = stop.
"""
from __future__ import annotations

import json
import os
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia*=false")
os.environ.setdefault("QT_FFMPEG_DEBUG", "0")

import numpy as np  # noqa: E402
from PySide6.QtCore import QProcess, QRectF, Qt, QTimer, QUrl  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen  # noqa: E402
from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices, QMediaPlayer  # noqa: E402
from PySide6.QtMultimediaWidgets import QVideoWidget  # noqa: E402
from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QLabel, QMainWindow, QMessageBox,  # noqa: E402
                               QPushButton, QVBoxLayout, QWidget)

from commander.ui import theme as T  # noqa: E402

RATE = 48000


def mmss(sec: float) -> str:
    sec = max(sec, 0)
    return f"{int(sec // 60)}:{int(sec % 60):02d}"


def latest_recording() -> Path | None:
    recs = sorted((ROOT / "recordings").glob("*/scenes.json"))
    return recs[-1].parent if recs else None


# --------------------------------------------------------------------------- widgets


class Prompter(QWidget):
    """Current line (words light up as you should be saying them), the previous and the next line."""

    def __init__(self, scenes: list[dict], total: float):
        super().__init__()
        self.scenes, self.total = scenes, total
        self.pos = 0.0
        self.countdown: int | None = None
        self.setMinimumWidth(560)

    def set_pos(self, sec: float) -> None:
        self.pos = sec
        self.update()

    def index(self) -> int:
        i = 0
        for k, sc in enumerate(self.scenes):
            if sc["t"] <= self.pos + 0.05:
                i = k
        return i

    def scene_end(self, i: int) -> float:
        return self.scenes[i + 1]["t"] if i + 1 < len(self.scenes) else self.total

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(T.BG))
        r = QRectF(self.rect()).adjusted(34, 26, -34, -26)
        if self.countdown is not None:
            p.setFont(T.ui_font(80, QFont.Weight.Bold))
            p.setPen(QColor(T.ACCENT))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, str(self.countdown) if self.countdown > 0 else "Go")
            p.setFont(T.ui_font(13))
            p.setPen(QColor(T.TEXT_2))
            first = self.scenes[0]["narration"].split(". ")[0]
            p.drawText(r.adjusted(0, 230, 0, 0), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
                       | Qt.TextFlag.TextWordWrap, f"First line:  “{first}…”")
            return
        i = self.index()
        sc = self.scenes[i]
        start, end = sc["t"], self.scene_end(i)
        speak = sc.get("duration", end - start) - 0.6
        frac = 0.0 if self.pos < start else min(1.0, (self.pos - start) / max(speak, 0.5))
        # header: scene number + caption + time left in this scene
        p.setFont(T.caps_font(8))
        p.setPen(QColor(T.ACCENT))
        p.drawText(QRectF(r.left(), r.top(), r.width(), 18), Qt.AlignmentFlag.AlignLeft,
                   f"Scene {i + 1:02d} / {len(self.scenes):02d}   ·   {mmss(start)} – {mmss(end)}")
        p.setFont(T.mono_font(10))
        p.setPen(QColor(T.TEXT_2))
        p.drawText(QRectF(r.left(), r.top(), r.width(), 18), Qt.AlignmentFlag.AlignRight,
                   f"{max(end - self.pos, 0):4.1f}s left")
        p.setFont(T.ui_font(9.5))
        p.setPen(QColor(T.TEXT_3))
        p.drawText(QRectF(r.left(), r.top() + 24, r.width(), 20), Qt.AlignmentFlag.AlignLeft, sc["caption"])
        # scene progress bar
        bar = QRectF(r.left(), r.top() + 52, r.width(), 4)
        p.fillRect(bar, QColor(T.SURFACE_3))
        p.fillRect(QRectF(bar.left(), bar.top(), bar.width() * min(1, max(0, (self.pos - start) / max(end - start, .1))),
                          bar.height()), QColor(T.ACCENT))
        # current line, word by word
        words = sc["narration"].split()
        lit = int(round(frac * len(words)))
        font = T.ui_font(21, QFont.Weight.DemiBold)
        fm = QFontMetricsF(font)
        p.setFont(font)
        x, y = r.left(), r.top() + 84
        line_h = fm.height() * 1.28
        space = fm.horizontalAdvance(" ")
        for k, w in enumerate(words):
            ww = fm.horizontalAdvance(w)
            if x + ww > r.right() and x > r.left():
                x, y = r.left(), y + line_h
            if k < lit:
                col = QColor(T.TEXT)
            elif k == lit:
                col = QColor(T.ACCENT)
            else:
                col = QColor("#5E656F")
            p.setPen(col)
            p.drawText(QRectF(x, y, ww + 4, line_h), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, w)
            x += ww + space
        y += line_h + 12
        if frac >= 1.0 and self.pos < end:
            p.setFont(T.ui_font(11, QFont.Weight.DemiBold))
            p.setPen(QColor(T.OK))
            p.drawText(QRectF(r.left(), y, r.width(), 24), Qt.AlignmentFlag.AlignLeft,
                       "✓ done - pause until the next line")
            y += 30
        # next line preview
        if i + 1 < len(self.scenes):
            nx = self.scenes[i + 1]
            y = max(y + 18, r.bottom() - 130)
            p.setPen(QPen(QColor(T.BORDER), 1))
            p.drawLine(int(r.left()), int(y), int(r.right()), int(y))
            p.setFont(T.caps_font(7.5))
            p.setPen(QColor(T.TEXT_3))
            p.drawText(QRectF(r.left(), y + 10, r.width(), 16), Qt.AlignmentFlag.AlignLeft,
                       f"Next · at {mmss(nx['t'])}  (in {max(nx['t'] - self.pos, 0):.0f}s)")
            p.setFont(T.ui_font(12))
            p.setPen(QColor(T.TEXT_2))
            p.drawText(QRectF(r.left(), y + 32, r.width(), r.bottom() - y - 30),
                       Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, nx["narration"])


class Meter(QWidget):
    def __init__(self):
        super().__init__()
        self.level = 0.0
        self.peak = 0.0
        self.setFixedHeight(10)

    def set_level(self, db: float) -> None:
        self.level = min(1.0, max(0.0, (db + 60) / 60))
        self.peak = max(self.level, self.peak * 0.97)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        r = QRectF(self.rect())
        p.fillRect(r, QColor(T.SURFACE_3))
        col = T.OK if self.level < 0.85 else ("#E2B046" if self.level < 0.95 else T.DANGER)
        p.fillRect(QRectF(0, 0, r.width() * self.level, r.height()), QColor(col))
        p.fillRect(QRectF(r.width() * self.peak - 2, 0, 2, r.height()), QColor(T.TEXT_2))


# --------------------------------------------------------------------------- main window


class Studio(QMainWindow):
    def __init__(self, folder: Path):
        super().__init__()
        self.folder = folder
        data = json.loads((folder / "scenes.json").read_text(encoding="utf-8"))
        self.scenes, self.total = data["scenes"], data["total"]
        self.video_path = folder / "demo_captioned.mp4"
        self.setWindowTitle("Voice-over Studio · ProTanki Commander")
        self.setWindowIcon(T.icon("bolt", T.ACCENT))

        # media
        self.player = QMediaPlayer(self)
        self.video = QVideoWidget()
        self.video.setStyleSheet("background:#000;")
        self.player.setVideoOutput(self.video)
        self.player.setSource(QUrl.fromLocalFile(str(self.video_path)))
        self.player.mediaStatusChanged.connect(self._media_status)
        self.source: QAudioSource | None = None
        self.io = None
        self.chunks: list[np.ndarray] = []
        self.samples = 0
        self.mode = "idle"                     # idle | countdown | rehearse | record
        self.fmt = QAudioFormat()
        self.sync_offset: float | None = None
        self.last_wav: Path | None = None

        # layout
        root = QWidget()
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        left = QWidget()
        left.setStyleSheet(f"background:{T.SURFACE};")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(16, 16, 16, 16)
        lv.setSpacing(10)
        self.video.setMinimumSize(640, 360)
        lv.addWidget(self.video, 1)
        self.time_lbl = QLabel(f"0:00 / {mmss(self.total)}")
        self.time_lbl.setFont(T.mono_font(10))
        self.state_lbl = QLabel("Ready")
        self.state_lbl.setStyleSheet(f"color:{T.TEXT_2};")
        row = QHBoxLayout()
        row.addWidget(self.time_lbl)
        row.addStretch(1)
        row.addWidget(self.state_lbl)
        lv.addLayout(row)

        mic_row = QHBoxLayout()
        mic_row.addWidget(QLabel("Microphone"))
        self.mic = QComboBox()
        for d in QMediaDevices.audioInputs():
            self.mic.addItem(d.description(), d)
        default = QMediaDevices.defaultAudioInput().description()
        if self.mic.findText(default) >= 0:
            self.mic.setCurrentText(default)
        self.mic.currentIndexChanged.connect(lambda *_: self._start_monitor())
        mic_row.addWidget(self.mic, 1)
        lv.addLayout(mic_row)
        self.meter = Meter()
        lv.addWidget(self.meter)
        hint = QLabel("Speak to test your level: aim for the green zone, without hitting red.")
        hint.setStyleSheet(f"color:{T.TEXT_3};")
        lv.addWidget(hint)

        btns = QHBoxLayout()
        self.b_rehearse = QPushButton(" Rehearse")
        self.b_rehearse.setIcon(T.icon("play"))
        self.b_rehearse.setToolTip("Play the video with the prompter, without recording  (R)")
        self.b_record = QPushButton(" Record")
        self.b_record.setProperty("kind", "primary")
        self.b_record.setIcon(T.icon("bolt", "#17130B"))
        self.b_record.setToolTip("3-2-1, then record your voice while the video plays  (Space)")
        self.b_stop = QPushButton(" Stop")
        self.b_stop.setIcon(T.icon("stop"))
        self.b_stop.setEnabled(False)
        btns.addWidget(self.b_rehearse)
        btns.addWidget(self.b_record)
        btns.addWidget(self.b_stop)
        lv.addLayout(btns)
        btns2 = QHBoxLayout()
        self.b_listen = QPushButton(" Listen to take")
        self.b_listen.setIcon(T.icon("eye"))
        self.b_listen.setEnabled(False)
        self.b_merge = QPushButton(" Merge into video")
        self.b_merge.setIcon(T.icon("save"))
        self.b_merge.setEnabled(False)
        self.b_open = QPushButton(" Open final video")
        self.b_open.setIcon(T.icon("window"))
        self.b_open.setEnabled((folder / "demo_final.mp4").exists())
        btns2.addWidget(self.b_listen)
        btns2.addWidget(self.b_merge)
        btns2.addWidget(self.b_open)
        lv.addLayout(btns2)
        self.take_lbl = QLabel("")
        self.take_lbl.setWordWrap(True)
        self.take_lbl.setStyleSheet(f"color:{T.TEXT_3};")
        lv.addWidget(self.take_lbl)

        self.prompter = Prompter(self.scenes, self.total)
        h.addWidget(left, 5)
        line = QWidget()
        line.setFixedWidth(1)
        line.setStyleSheet(f"background:{T.BORDER};")
        h.addWidget(line)
        h.addWidget(self.prompter, 6)

        self.b_rehearse.clicked.connect(lambda: self._begin("rehearse"))
        self.b_record.clicked.connect(lambda: self._begin("record"))
        self.b_stop.clicked.connect(self._stop)
        self.b_listen.clicked.connect(self._listen)
        self.b_merge.clicked.connect(self._merge)
        self.b_open.clicked.connect(lambda: os.startfile(str(self.folder / "demo_final.mp4")))

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(33)
        self._start_monitor()

    # ---------------------------------------------------------- audio

    def _fmt(self) -> QAudioFormat:
        f = QAudioFormat()
        f.setSampleRate(RATE)
        f.setChannelCount(1)
        f.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        return f

    def _start_monitor(self) -> None:
        """Open the microphone (always on, for the level meter); recording just keeps the samples."""
        if self.source:
            self.source.stop()
        dev = self.mic.currentData()
        if dev is None:
            self.state_lbl.setText("No microphone found")
            return
        fmt = self._fmt()
        if not dev.isFormatSupported(fmt):
            fmt = dev.preferredFormat()
        self.fmt = fmt
        self.source = QAudioSource(dev, fmt, self)
        self.source.setBufferSize(int(fmt.sampleRate() * fmt.bytesPerFrame() * 0.03))
        self.io = self.source.start()
        self.io.readyRead.connect(self._read)

    def _read(self) -> None:
        data = bytes(self.io.readAll())
        if not data:
            return
        f = self.fmt
        if f.sampleFormat() == QAudioFormat.SampleFormat.Float:
            a = np.frombuffer(data, dtype=np.float32)
        elif f.sampleFormat() == QAudioFormat.SampleFormat.Int32:
            a = np.frombuffer(data, dtype=np.int32).astype(np.float32) / 2 ** 31
        else:
            a = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768
        ch = f.channelCount()
        if ch > 1:
            a = a[: len(a) // ch * ch].reshape(-1, ch).mean(axis=1)
        if a.size:
            rms = float(np.sqrt(np.mean(a * a)) + 1e-9)
            self.meter.set_level(20 * np.log10(rms))
        if self.mode == "record":
            self.chunks.append(a.copy())
            self.samples += a.size

    # ---------------------------------------------------------- flow

    def _begin(self, mode: str) -> None:
        if self.mode != "idle":
            return
        self.player.stop()
        self.player.setPosition(0)
        self.pending = mode
        self.mode = "countdown"
        self.count = 3
        self.prompter.countdown = 3
        self.prompter.update()
        self._set_buttons(running=True)
        self.state_lbl.setText("Get ready…")
        QTimer.singleShot(1000, self._count)

    def _count(self) -> None:
        if self.mode != "countdown":
            return
        self.count -= 1
        self.prompter.countdown = self.count
        self.prompter.update()
        if self.count > 0:
            QTimer.singleShot(1000, self._count)
            return
        QTimer.singleShot(250, self._go)

    def _go(self) -> None:
        if self.mode != "countdown":
            return
        self.prompter.countdown = None
        self.chunks, self.samples, self.sync_offset = [], 0, None
        self.mode = self.pending
        self.t_play = time.perf_counter()
        self.player.play()
        self.state_lbl.setText("● RECORDING" if self.mode == "record" else "Rehearsal (not recording)")
        self.state_lbl.setStyleSheet(f"color:{T.DANGER if self.mode == 'record' else T.TEXT_2};font-weight:600;")

    def _tick(self) -> None:
        pos = self.player.position() / 1000.0
        if self.mode in ("record", "rehearse") and self.sync_offset is None and pos > 0.05:
            # time since recording began vs video position at the same instant = sync offset
            self.sync_offset = (time.perf_counter() - self.t_play) - pos
        self.prompter.set_pos(pos if self.mode != "idle" or pos > 0 else 0.0)
        self.time_lbl.setText(f"{mmss(pos)} / {mmss(self.total)}")

    def _media_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.mode in ("record", "rehearse"):
            self._finish(complete=True)

    def _stop(self) -> None:
        if self.mode == "countdown":
            self.mode = "idle"
            self.prompter.countdown = None
            self._set_buttons(running=False)
            self.state_lbl.setText("Cancelled")
            self.state_lbl.setStyleSheet(f"color:{T.TEXT_2};")
            return
        if self.mode in ("record", "rehearse"):
            self._finish(complete=False)

    def _finish(self, complete: bool) -> None:
        was = self.mode
        self.mode = "idle"
        self.player.pause()
        self._set_buttons(running=False)
        self.state_lbl.setStyleSheet(f"color:{T.TEXT_2};")
        if was != "record":
            self.state_lbl.setText("Rehearsal finished")
            return
        if not self.chunks:
            self.state_lbl.setText("No audio captured - check the microphone")
            return
        audio = np.concatenate(self.chunks)
        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = self.folder / f"voice_{stamp}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.fmt.sampleRate())
            w.writeframes(pcm.tobytes())
        self.last_wav = path
        self.offset = max(0.0, self.sync_offset or 0.0)
        secs = len(pcm) / self.fmt.sampleRate()
        self.state_lbl.setText("Take saved" + ("" if complete else " (stopped early)"))
        self.take_lbl.setText(f"{path.name} · {secs:.1f}s · sync {self.offset * 1000:.0f} ms trimmed at the start. "
                              "Listen back, record again, or merge it into the video.")
        self.b_listen.setEnabled(True)
        self.b_merge.setEnabled(True)

    def _set_buttons(self, running: bool) -> None:
        self.b_record.setEnabled(not running)
        self.b_rehearse.setEnabled(not running)
        self.b_stop.setEnabled(running)
        self.mic.setEnabled(not running)
        self.b_merge.setEnabled(not running and self.last_wav is not None)
        self.b_listen.setEnabled(not running and self.last_wav is not None)

    def _listen(self) -> None:
        if self.last_wav:
            os.startfile(str(self.last_wav))

    # ---------------------------------------------------------- merge

    def _merge(self) -> None:
        if not self.last_wav:
            return
        try:
            import imageio_ffmpeg
            ff = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            ff = "ffmpeg"
        out = self.folder / "demo_final.mp4"
        dur = self.player.duration() / 1000.0 or self.total
        af = (f"atrim=start={self.offset:.3f},asetpts=PTS-STARTPTS,highpass=f=80,"
              f"loudnorm=I=-16:TP=-1.5:LRA=11,apad=whole_dur={dur:.3f}")
        args = ["-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(self.video_path),
                "-i", str(self.last_wav), "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-af", af,
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{dur:.3f}", "-movflags", "+faststart",
                str(out)]
        self.proc = QProcess(self)
        self.proc.finished.connect(lambda code, _s: self._merged(code, out))
        self.state_lbl.setText("Merging…")
        self.b_merge.setEnabled(False)
        self.proc.start(ff, args)

    def _merged(self, code: int, out: Path) -> None:
        self.b_merge.setEnabled(True)
        if code != 0:
            err = bytes(self.proc.readAllStandardError()).decode(errors="replace")
            QMessageBox.warning(self, "Merge failed", err[-800:] or f"ffmpeg exit {code}")
            self.state_lbl.setText("Merge failed")
            return
        self.state_lbl.setText("Final video ready")
        self.take_lbl.setText(f"Saved {out}")
        self.b_open.setEnabled(True)

    # ---------------------------------------------------------- keys

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Space:
            self._stop() if self.mode != "idle" else self._begin("record")
        elif e.key() == Qt.Key.Key_R and self.mode == "idle":
            self._begin("rehearse")
        elif e.key() == Qt.Key.Key_Escape:
            self._stop()
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e):
        if self.source:
            self.source.stop()
        self.player.stop()
        e.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    T.init_fonts()
    app.setFont(T.ui_font(9.5))
    app.setStyleSheet(T.stylesheet())
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    folder = Path(args[0]).resolve() if args else latest_recording()
    if not folder or not (folder / "scenes.json").exists() or not (folder / "demo_captioned.mp4").exists():
        QMessageBox.critical(None, "Voice-over Studio",
                             "No demo recording found.\nRun: run.py --demo, then tools/make_demo_video.py <folder>")
        return 1
    win = Studio(folder)
    screen = app.primaryScreen().availableGeometry()
    win.resize(min(1500, screen.width() - 40), min(800, screen.height() - 40))
    win.move(screen.center() - win.rect().center())
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
