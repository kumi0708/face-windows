"""カメラ取得＋検出を別プロセスで動かす。

GUI スレッドが大量のウィンドウを描いている間も、検出が GIL 待ちで遅れないようにするため。
子プロセス → 親: 切り抜き画像（BGRA numpy）とメタデータを Pipe で送る（最新のみ・溜めない）。
親 → 子: 設定の変更、停止要求。親が落ちると Pipe が切れて子も終了する。
"""
from __future__ import annotations

import collections
import multiprocessing as mp
import threading
import time

from PySide6.QtGui import QImage

CAMERA_KEYS = ("camera_index", "camera_res", "camera_fps")


# ---------------- 子プロセス ----------------
def worker_main(data_conn, ctrl_conn, settings: dict, source: str | None) -> None:
    from .camera import Camera
    from .tracker import Tracker

    def open_cam():
        c = Camera(int(settings["camera_index"]), settings["camera_res"], int(settings["camera_fps"]), source)
        c.start()
        return c

    cam = open_cam()
    tr = Tracker(cam, settings)
    tr.start()
    last = None
    t_stats = 0.0
    try:
        while True:
            while ctrl_conn.poll():
                kind, payload = ctrl_conn.recv()
                if kind == "stop":
                    return
                if kind == "settings":
                    cam_changed = any(payload.get(k) != settings.get(k) for k in CAMERA_KEYS)
                    settings.update(payload)
                    if cam_changed:
                        cam.stop()
                        cam = open_cam()
                        tr.camera = cam
            if not tr.new_snap.wait(0.1):
                now = time.perf_counter()
                if now - t_stats > 0.25:   # 顔が無くても状態（エラー・FPS）は送る
                    t_stats = now
                    data_conn.send({"snap": None, "events": [], "stats": _stats(cam, tr)})
                continue
            tr.new_snap.clear()
            snap = tr.snapshot()
            if snap is last:
                continue
            last = snap
            events = []
            while tr.events:
                events.append(tr.events.popleft())
            data_conn.send({"snap": snap, "events": events, "stats": _stats(cam, tr)})
    except (EOFError, BrokenPipeError, OSError):
        pass   # 親プロセスが終了した
    finally:
        tr.stop()
        cam.stop()


def _stats(cam, tr) -> dict:
    return {"camera_fps": cam.measured_fps, "camera_error": cam.error, "camera_size": cam.actual_size,
            "infer_fps": tr.infer_fps, "infer_ms": tr.infer_ms, "extra_ms": tr.extra_ms,
            "tracker_error": tr.error}


# ---------------- 親プロセス側 ----------------
def _qimage(arr, alpha: bool) -> QImage:
    h, w = arr.shape[:2]
    fmt = QImage.Format_ARGB32_Premultiplied if alpha else QImage.Format_RGB32
    return QImage(arr.data, w, h, w * 4, fmt).copy()


class TrackerClient:
    """Tracker と同じ使い方（snapshot / events / 各種FPS）ができる親プロセス側の窓口。"""

    def __init__(self, settings: dict, source: str | None = None):
        from .tracker import Snapshot
        self.settings = settings
        ctx = mp.get_context("spawn")
        self._data_r, data_w = ctx.Pipe(duplex=False)
        ctrl_r, self._ctrl_w = ctx.Pipe(duplex=False)
        self.proc = ctx.Process(target=worker_main, args=(data_w, ctrl_r, dict(settings), source),
                                daemon=True, name="facewindows-tracker")
        self.proc.start()
        data_w.close()
        ctrl_r.close()
        self._snap = Snapshot()
        self._lock = threading.Lock()
        self.events: collections.deque = collections.deque(maxlen=64)
        self.history: collections.deque = collections.deque()   # (t, {part: QImage})
        self.measured_fps = self.infer_fps = self.infer_ms = self.extra_ms = 0.0
        self.actual_size = (0, 0)
        self.camera_error: str | None = None
        self.error: str | None = None
        self.starting = True
        self._sent = dict(settings)
        self._halt = threading.Event()
        self._rx = threading.Thread(target=self._recv_loop, daemon=True, name="tracker-rx")
        self._rx.start()

    def _recv_loop(self) -> None:
        from .tracker import PartState
        while not self._halt.is_set():
            self._sync_settings()
            try:
                if not self._data_r.poll(0.05):
                    if not self.proc.is_alive():
                        self.error = f"検出プロセスが終了しました（終了コード {self.proc.exitcode}）"
                        return
                    continue
                msg = self._data_r.recv()
            except (EOFError, OSError):
                if not self._halt.is_set():
                    self.error = "検出プロセスとの接続が切れました"
                return
            st = msg["stats"]
            self.measured_fps, self.infer_fps = st["camera_fps"], st["infer_fps"]
            self.infer_ms, self.extra_ms = st["infer_ms"], st["extra_ms"]
            self.camera_error, self.error = st["camera_error"], st["tracker_error"]
            self.actual_size = st["camera_size"]
            self.starting = False
            snap = msg["snap"]
            if snap is None:
                continue
            snap.parts = {k: PartState(_qimage(v.image, k == "body"), v.pos, v.size, v.live)
                          for k, v in snap.parts.items()}
            if snap.preview is not None:
                snap.preview = _qimage(snap.preview, False)
            self.events.extend(msg["events"])
            now = time.perf_counter()
            if float(self.settings["delay_ratio"]) > 0:
                self.history.append((now, {k: v.image for k, v in snap.parts.items() if v.live}))
                keep = float(self.settings["delay_s"]) + 0.3
                while self.history and now - self.history[0][0] > keep:
                    self.history.popleft()
            elif self.history:
                self.history.clear()
            with self._lock:
                self._snap = snap

    def _sync_settings(self) -> None:
        try:
            cur = dict(self.settings)
        except RuntimeError:   # GUI 側で書き換え中
            return
        if cur != self._sent:
            self._sent = cur
            try:
                self._ctrl_w.send(("settings", cur))
            except (OSError, BrokenPipeError):
                pass

    def snapshot(self):
        with self._lock:
            return self._snap

    def delayed_image(self, part: str, delay: float):
        target = time.perf_counter() - delay
        best = None
        for t, imgs in list(self.history):
            if t > target:
                break
            if part in imgs:
                best = imgs[part]
        return best

    def stop(self, timeout: float = 3.0) -> None:
        self._halt.set()
        try:
            self._ctrl_w.send(("stop", None))
        except (OSError, BrokenPipeError):
            pass
        self.proc.join(timeout)
        if self.proc.is_alive():
            self.proc.terminate()
            self.proc.join(1.0)
        self._rx.join(1.0)
        for c in (self._data_r, self._ctrl_w):
            try:
                c.close()
            except OSError:
                pass
