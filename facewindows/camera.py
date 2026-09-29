"""カメラ取得スレッド。最新フレームのみ保持し、古いフレームは溜めない。"""
from __future__ import annotations

import contextlib
import os
import sys
import threading
import time

import cv2

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
# Windows: DirectShow（開くのが速く MJPG が使える）/ macOS: AVFoundation / その他: OpenCV に任せる
BACKEND = cv2.CAP_DSHOW if IS_WINDOWS else cv2.CAP_AVFOUNDATION if IS_MAC else cv2.CAP_ANY


@contextlib.contextmanager
def _quiet_stderr():
    """番号の総当たりでは存在しない番号で必ず失敗し、OpenCV が C 側から直接
    「camera failed to properly initialize!」と書く。異常に見えるので探索中だけ捨てる。"""
    sys.stderr.flush()
    saved, devnull = os.dup(2), os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(devnull)
        os.close(saved)


def _mac_camera_names() -> list[str]:
    """AVFoundation が返すカメラ名。内蔵カメラと iPhone の連係カメラを見分けるために使う。"""
    try:
        import AVFoundation as avf
    except ImportError:
        return []
    names = ("AVCaptureDeviceTypeBuiltInWideAngleCamera", "AVCaptureDeviceTypeExternal",
             "AVCaptureDeviceTypeContinuityCamera", "AVCaptureDeviceTypeDeskViewCamera")
    kinds = [getattr(avf, n) for n in names if hasattr(avf, n)]
    session = avf.AVCaptureDeviceDiscoverySession.discoverySessionWithDeviceTypes_mediaType_position_(
        kinds, avf.AVMediaTypeVideo, 0)
    return [str(d.localizedName()) for d in session.devices()]


def list_cameras(max_probe: int = 4) -> list[tuple[int, str]]:
    """(index, 名前) の一覧。Windows は DirectShow、macOS は AVFoundation の名前を使い、
    それ以外は番号を順に開いて確かめる。"""
    if IS_WINDOWS:
        try:
            from pygrabber.dshow_graph import FilterGraph
            return list(enumerate(FilterGraph().get_input_devices()))
        except Exception:
            pass
    mac_names = _mac_camera_names() if IS_MAC else []
    found = []
    with _quiet_stderr():
        for i in range(max_probe):
            cap = cv2.VideoCapture(i, BACKEND)
            ok = cap.isOpened()
            cap.release()
            if ok:
                found.append((i, mac_names[i] if i < len(mac_names) else f"Camera {i}"))
            elif IS_MAC and found:
                break   # AVFoundation の番号は連番。見つかった分の先は存在しない
                        # （0 番が使用中・権限待ちで開けない場合があるので、1台も無い間は続ける）
    return found


def warmup_mac_authorization(index: int, timeout: float = 60.0) -> None:
    """macOS のカメラ許可をここで取り切る。呼び出し元はプロセスのメインスレッドであること。

    OpenCV も許可を要求はするが、返事を待たずに 1 秒未満で諦めるため、ダイアログに答える間が
    ない（OS 更新などで許可が消えると、以後ずっと開けなくなる）。そこで AVFoundation に直接
    要求し、完了ハンドラが呼ばれるまで run loop を回して待つ。"""
    if not IS_MAC:
        return
    try:
        import AVFoundation as avf
        from CoreFoundation import CFRunLoopRunInMode, kCFRunLoopDefaultMode
    except ImportError:   # pyobjc が無い環境では OpenCV 任せにする
        cv2.VideoCapture(index, BACKEND).release()
        return
    if avf.AVCaptureDevice.authorizationStatusForMediaType_(avf.AVMediaTypeVideo) != 0:
        return   # 許可済み・拒否済み・制限中。拒否なら Camera 側がエラーを出す
    answered: list = []
    avf.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
        avf.AVMediaTypeVideo, lambda granted: answered.append(bool(granted)))
    limit = time.monotonic() + timeout
    while not answered and time.monotonic() < limit:
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.1, False)


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
