"""カメラ取得スレッド。最新フレームのみ保持し、古いフレームは溜めない。"""
from __future__ import annotations

import sys
import threading
import time

import cv2

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
# Windows: DirectShow（開くのが速く MJPG が使える）/ macOS: AVFoundation / その他: OpenCV に任せる
BACKEND = cv2.CAP_DSHOW if IS_WINDOWS else cv2.CAP_AVFOUNDATION if IS_MAC else cv2.CAP_ANY


def list_cameras(max_probe: int = 4) -> list[tuple[int, str]]:
    """(index, 名前) の一覧。Windows は DirectShow の名前、それ以外は番号を順に開いて確かめる。"""
    if IS_WINDOWS:
        try:
            from pygrabber.dshow_graph import FilterGraph
            return list(enumerate(FilterGraph().get_input_devices()))
        except Exception:
            pass
    found = []
    for i in range(max_probe):
        cap = cv2.VideoCapture(i, BACKEND)
        if cap.isOpened():
            found.append((i, f"Camera {i}"))
        cap.release()
    return found


class Camera(threading.Thread):
    def __init__(self, index: int, res: str, fps: int, source: str | None = None):
        super().__init__(daemon=True, name="camera")
        self.index, self.fps = index, fps
        self.source = source   # 画像/動画ファイル（テスト・デモ用）。None ならカメラ
        self.width, self.height = (int(v) for v in res.split("x"))
        self._lock = threading.Lock()
        self._frame = None
        self._seq = 0
        self._t = 0.0
        self._halt = threading.Event()
        self.error: str | None = None
        self.opened = threading.Event()
        self.actual_size = (0, 0)
        self.measured_fps = 0.0

    def run(self) -> None:
        if self.source:
            self._run_file()
            return
        cap = cv2.VideoCapture(self.index, BACKEND)
        if not cap.isOpened():
            hint = ("macOS の「システム設定 → プライバシーとセキュリティ → カメラ」で"
                    "ターミナル（または Python）を許可してください" if IS_MAC
                    else "未接続・他アプリが使用中・権限拒否の可能性")
            self.error = f"カメラ {self.index} を開けません（{hint}）"
            self.opened.set()
            return
        if IS_WINDOWS:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.opened.set()
        fails = 0
        count, t_win = 0, time.perf_counter()
        try:
            while not self._halt.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    fails += 1
                    if fails > 60:
                        self.error = "カメラからフレームを取得できません（切断された可能性）"
                        break
                    time.sleep(0.01)
                    continue
                fails = 0
                self.error = None
                now = time.perf_counter()
                with self._lock:
                    self._frame, self._t = frame, now
                    self._seq += 1
                self.actual_size = (frame.shape[1], frame.shape[0])
                count += 1
                if now - t_win >= 1.0:
                    self.measured_fps = count / (now - t_win)
                    count, t_win = 0, now
        finally:
            cap.release()

    def _run_file(self) -> None:
        """ファイル入力。静止画は同じフレームを、動画はループして fps で供給する。"""
        img = cv2.imread(self.source)
        cap = None if img is not None else cv2.VideoCapture(self.source)
        if img is None and not cap.isOpened():
            self.error = f"入力ファイルを開けません: {self.source}"
            self.opened.set()
            return
        self.opened.set()
        period = 1.0 / max(1, self.fps)
        count, t_win = 0, time.perf_counter()
        while not self._halt.is_set():
            t0 = time.perf_counter()
            frame = img
            if cap is not None:
                ok, frame = cap.read()
                if not ok:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
            with self._lock:
                self._frame, self._t = frame, t0
                self._seq += 1
            self.actual_size = (frame.shape[1], frame.shape[0])
            count += 1
            if t0 - t_win >= 1.0:
                self.measured_fps = count / (t0 - t_win)
                count, t_win = 0, t0
            time.sleep(max(0.0, period - (time.perf_counter() - t0)))
        if cap is not None:
            cap.release()

    def latest(self):
        """(seq, capture_time, frame)。frame は共有されるので書き換えないこと。"""
        with self._lock:
            return self._seq, self._t, self._frame

    def stop(self, timeout: float = 2.0) -> None:
        self._halt.set()
        if self.is_alive():
            self.join(timeout)
