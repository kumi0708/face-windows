"""OSネイティブウィンドウ vs 透明オーバーレイ疑似ウィンドウ の性能比較。
usage: python bench/window_bench.py native|overlay N [seconds]"""
import sys, time, math, random, json
import numpy as np, ctypes
from PySide6.QtCore import Qt, QTimer, QRect
from PySide6.QtGui import QImage, QPainter, QColor, QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget

mode, N = sys.argv[1], int(sys.argv[2]); DUR = float(sys.argv[3]) if len(sys.argv) > 3 else 5
app = QApplication(sys.argv)
scr = QGuiApplication.primaryScreen().availableGeometry()
img_np = (np.random.rand(120, 160, 3) * 255).astype(np.uint8)
def qimg(t):
    a = np.roll(img_np, int(t * 50) % 160, axis=1).copy()
    return QImage(a.data, 160, 120, 480, QImage.Format_RGB888).copy()
frames = 0; t0 = time.perf_counter(); worst = 0; last = t0
pos = [(random.uniform(0, scr.width() - 200), random.uniform(0, scr.height() - 160), random.uniform(0, 6.28)) for _ in range(N)]

class Native(QWidget):
    def __init__(s, i):
        super().__init__(None, Qt.Tool); s.i = i; s.img = None
        s.setWindowTitle(f"eye {i}"); s.resize(160, 120); s.show()
    def paintEvent(s, e):
        if s.img is not None: QPainter(s).drawImage(s.rect(), s.img)

class Overlay(QWidget):
    def __init__(s):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowTransparentForInput)
        s.setAttribute(Qt.WA_TranslucentBackground); s.setGeometry(scr); s.img = None; s.rects = []; s.show()
    def paintEvent(s, e):
        p = QPainter(s)
        for (x, y) in s.rects:
            r = QRect(int(x), int(y), 160, 142)
            p.fillRect(r, QColor(235, 235, 235)); p.fillRect(r.x(), r.y(), 160, 22, QColor(60, 60, 70))
            p.drawImage(QRect(r.x(), r.y() + 22, 160, 120), s.img)

wins = [Native(i) for i in range(N)] if mode.startswith("native") else None
ov = Overlay() if mode == "overlay" else None
def tick():
    global frames, worst, last
    now = time.perf_counter(); t = now - t0
    worst = max(worst, now - last); last = now
    im = qimg(t)
    xy = [(x + 80 * math.sin(t * 2 + ph), y + 60 * math.cos(t * 2 + ph)) for x, y, ph in pos]
    if wins:
        if mode == "native":
            for w, (x, y) in zip(wins, xy):
                w.img = im; w.move(int(x), int(y)); w.update()
        else:  # native-defer: Win32 DeferWindowPos で一括移動
            u = ctypes.windll.user32; h = u.BeginDeferWindowPos(len(wins))
            for w, (x, y) in zip(wins, xy):
                w.img = im; h = u.DeferWindowPos(h, int(w.winId()), None, int(x), int(y), 0, 0, 0x0001 | 0x0004 | 0x0010)
            u.EndDeferWindowPos(h)
            for w in wins: w.repaint()
    else:
        ov.img = im; ov.rects = xy; ov.update()
    frames += 1
    if t > DUR:
        print(json.dumps({"mode": mode, "n": N, "fps": round(frames / t, 1), "worst_ms": round(worst * 1000, 1)})); app.quit()
tm = QTimer(); tm.timeout.connect(tick); tm.start(0)
app.exec()
