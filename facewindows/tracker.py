"""検出スレッド：1フレームにつき1回だけ検出・切り抜きを行い、結果を全ウィンドウで共有する。

GUI と GIL を取り合わないよう、別プロセス（remote.py）の中で動かす。
ここで作る画像は numpy（BGRA、描画用に premultiplied）で、GUI 側で QImage にする。
"""
from __future__ import annotations

import collections
import math
import threading
import time
from dataclasses import dataclass, field

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions, vision

from . import geometry as geo
from .config import MODEL_DIR

PREVIEW_W = 480
MOTION_W, MOTION_H = 160, 90
BOX_COLORS = {  # BGR
    "face": (80, 220, 80), "left_eye": (255, 180, 60), "right_eye": (60, 180, 255),
    "nose": (200, 120, 255), "mouth": (80, 80, 255), "body": (220, 220, 220),
    "left_arm": (0, 200, 200), "right_arm": (0, 160, 255),
    "left_hand": (255, 255, 0), "right_hand": (255, 0, 255),
}


def to_bgra(bgr: np.ndarray, alpha: np.ndarray | None = None) -> np.ndarray:
    """BGR（+アルファ）→ BGRA。Qt の RGB32 / ARGB32_Premultiplied と同じ並びで、描画が最も速い。"""
    bgra = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
    if alpha is not None:
        a = alpha.astype(np.uint16)
        bgra[..., :3] = (bgra[..., :3].astype(np.uint16) * a[..., None] // 255).astype(np.uint8)
        bgra[..., 3] = alpha
    return bgra


@dataclass
class PartState:
    image: object                 # 検出側: BGRA numpy / GUI側: QImage
    pos: tuple[float, float]      # 表示座標系(0..1、mirror 適用済み)
    size: float                   # 枠の幅 / フレーム幅
    live: bool                    # このフレームで検出できたか（False=最終映像を保持中）


@dataclass
class Snapshot:
    t_capture: float = 0.0
    t_done: float = 0.0
    parts: dict = field(default_factory=dict)          # name -> PartState
    status: dict = field(default_factory=dict)         # name -> "OK"/"HOLD"/"LOST"
    face_center: tuple | None = None
    face_vel: tuple = (0.0, 0.0)                       # 顔幅/秒
    face_w: float = 0.0
    mouth_open: float = 0.0
    motion_energy: float = 0.0
    motion_center: tuple | None = None
    motion_dir: tuple = (0.0, 0.0)
    preview: object = None
    frame_size: tuple = (0, 0)
    people: int = 0                                    # 検出できた顔の数


class Tracker(threading.Thread):
    def __init__(self, camera, settings: dict):
        super().__init__(daemon=True, name="tracker")
        self.camera = camera
        self.settings = settings            # 共有 dict（管理画面が書き換える）
        self._halt = threading.Event()
        self._lock = threading.Lock()
        self._snap = Snapshot()
        self.events: collections.deque = collections.deque(maxlen=64)
        self.new_snap = threading.Event()
        self.error: str | None = None
        self.infer_fps = 0.0
        self.infer_ms = 0.0
        self.extra_ms = 0.0
        self.paused_detection = False
        self._face = self._pose = self._hands = None
        self._det_key = None
        self._ts = 0
        self._boxes: dict[str, geo.Box] = {}
        self._filters: dict[str, geo.BoxFilter] = {}
        self._last_proc = 0.0
        self._last_seen: dict[str, float] = {}
        self._held: dict[str, PartState] = {}
        self._prev_gray = None
        self._prev_motion_c = None
        self._prev_face = None
        self._mouth_open: dict[int, bool] = {}
        self._people: dict[int, tuple] = {}   # 人の番号 → (中心x, 中心y, 顔の幅, 最後に見えた時刻)
        self._last_head_burst = 0.0
        self._pose_cache = None
        self._hand_cache = None
        self._frame_i = 0

    # ---------- detectors ----------
    def _build(self, conf: float, people: int, need_pose: bool, need_hands: bool) -> None:
        if self._det_key != (conf, people):   # しきい値・人数が変わったら作り直す
            for d in (self._face, self._pose, self._hands):
                if d is not None:
                    d.close()
            self._face = self._pose = self._hands = None
            self._det_key = (conf, people)
        base = lambda f: BaseOptions(model_asset_path=str(MODEL_DIR / f))
        if self._face is None:
            self._face = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
                base_options=base("face_landmarker.task"), running_mode=vision.RunningMode.VIDEO,
                num_faces=people, min_face_detection_confidence=conf,
                min_face_presence_confidence=conf, min_tracking_confidence=conf))
        if need_pose and self._pose is None:
            self._pose = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
                base_options=base("pose_landmarker_lite.task"), running_mode=vision.RunningMode.VIDEO,
                num_poses=people, output_segmentation_masks=True, min_pose_detection_confidence=conf,
                min_pose_presence_confidence=conf, min_tracking_confidence=conf))
        if need_hands and self._hands is None:
            self._hands = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
                base_options=base("hand_landmarker.task"), running_mode=vision.RunningMode.VIDEO,
                num_hands=2 * people, min_hand_detection_confidence=conf,
                min_hand_presence_confidence=conf, min_tracking_confidence=conf))

    def _next_ts(self) -> int:
        self._ts = max(self._ts + 1, int(time.perf_counter() * 1000))
        return self._ts

    # ---------- main loop ----------
    def run(self) -> None:
        last_seq = -1
        count, t_win = 0, time.perf_counter()
        try:
            while not self._halt.is_set():
                seq, t_cap, frame = self.camera.latest()
                if frame is None or seq == last_seq:
                    time.sleep(0.002)
                    continue
                last_seq = seq
                try:
                    self._process(frame, t_cap)
                    self.error = None
                except Exception as e:  # 1フレームの失敗でスレッドを落とさない
                    self.error = f"検出エラー: {e!r}"
                    time.sleep(0.05)
                count += 1
                now = time.perf_counter()
                if now - t_win >= 1.0:
                    self.infer_fps = count / (now - t_win)
                    count, t_win = 0, now
        finally:
            for d in (self._face, self._pose, self._hands):
                if d is not None:
                    d.close()

    def _process(self, frame: np.ndarray, t_cap: float) -> None:
        s = self.settings
        mirror = bool(s["mirror"])
        fh, fw = frame.shape[:2]
        need_pose = s["track_body"] or s["track_arms"]
        need_hands = s["track_hands"]
        self._build(round(float(s["det_confidence"]), 2), max(1, int(s["max_faces"])), need_pose, need_hands)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mpimg = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        now = time.perf_counter()
        self._frame_i += 1
        hold = float(s["lost_hold_s"])

        t0 = time.perf_counter()
        res = self._face.detect_for_video(mpimg, self._next_ts())
        self.infer_ms = (time.perf_counter() - t0) * 1000

        detected: dict[str, geo.Box] = {}
        snap = Snapshot(t_capture=t_cap, frame_size=(fw, fh))
        # 顔：全員分。前フレームの人に近い順に番号を引き継ぐ
        faces = []
        for lm in res.face_landmarks:
            pts = np.array([(p.x * fw, p.y * fh) for p in lm], dtype=np.float32)
            x0, y0 = pts[:468].min(axis=0)
            x1, y1 = pts[:468].max(axis=0)
            faces.append((pts, ((x0 + x1) / 2, (y0 + y1) / 2, x1 - x0)))
        self._people = {pid: v for pid, v in self._people.items() if now - v[3] <= hold}
        pids = geo.assign_people({pid: v[:3] for pid, v in self._people.items()}, [f[1] for f in faces])
        mouths: dict[int, float] = {}
        for (pts, (cx, cy, w)), pid in zip(faces, pids):
            self._people[pid] = (cx, cy, w, now)
            roll = geo.face_roll(pts) if s["rotate_crops"] else 0.0
            detected[geo.part_key("face", pid)] = geo.box_from_points(pts[:468], "face", roll)
            for name, idx in geo.FACE_LANDMARK_SETS.items():
                detected[geo.part_key(name, pid)] = geo.box_from_points(pts[idx], name, roll)
            mouths[pid] = geo.mouth_openness(pts)
        face_centers = {pid: (cx, cy) for pid, (cx, cy, _w, t) in self._people.items() if t == now}

        def nearest_person(x: float, y: float, order: int) -> int:
            """身体・手を、いちばん近い顔の人に割り当てる（顔が無ければ検出順）。"""
            if not face_centers:
                return order
            return min(face_centers, key=lambda pid: (face_centers[pid][0] - x) ** 2 + (face_centers[pid][1] - y) ** 2)

        # Body / Arms / Hands は間引いて実行（顔の速度を優先）
        interval = max(1, int(s["extra_detect_interval"]))
        t1 = time.perf_counter()
        masks: dict[str, np.ndarray] = {}
        if need_pose and self._pose is not None:
            if self._frame_i % interval == 0 or self._pose_cache is None:
                self._pose_cache = self._pose.detect_for_video(mpimg, self._next_ts())
            pr = self._pose_cache
            for i, plm in enumerate(pr.pose_landmarks):
                ppts = np.array([(p.x * fw, p.y * fh) for p in plm], dtype=np.float32)
                vis = np.array([p.visibility or 0 for p in plm])
                pid = nearest_person(ppts[0][0], ppts[0][1], i)   # 0 = 鼻
                if pr.segmentation_masks and i < len(pr.segmentation_masks):
                    mask = np.squeeze(pr.segmentation_masks[i].numpy_view())
                    ys, xs = np.nonzero(mask[::4, ::4] > 0.5)
                    if len(xs) > 20:
                        mpts = np.stack([xs * 4, ys * 4], axis=1).astype(np.float32)
                        key = geo.part_key("body", pid)
                        detected[key] = geo.box_from_points(mpts, "body")
                        masks[key] = mask
                for name, idx in (("left_arm", [11, 13, 15, 17, 19]), ("right_arm", [12, 14, 16, 18, 20])):
                    if (vis[idx] > 0.5).sum() >= 3:
                        detected[geo.part_key(name, pid)] = geo.box_from_points(ppts[idx], name)
        else:
            self._pose_cache = None
        if need_hands and self._hands is not None:
            if self._frame_i % interval == 0 or self._hand_cache is None:
                self._hand_cache = self._hands.detect_for_video(mpimg, self._next_ts())
            hr = self._hand_cache
            for i, (hl, hd) in enumerate(zip(hr.hand_landmarks, hr.handedness)):
                hpts = np.array([(p.x * fw, p.y * fh) for p in hl], dtype=np.float32)
                # MediaPipe の左右判定は反転画像前提。生画像なので逆にする
                name = "right_hand" if hd[0].category_name == "Left" else "left_hand"
                key = geo.part_key(name, nearest_person(hpts[0][0], hpts[0][1], i // 2))
                if key not in detected:   # 同じ人の同じ側の手が2つ出たら最初のものだけ
                    detected[key] = geo.box_from_points(hpts, name)
        else:
            self._hand_cache = None
        self.extra_ms = (time.perf_counter() - t1) * 1000

        # 平滑化 → 切り抜き（部位のキーごと）
        smooth = float(s["box_smoothing"])
        stabilize = bool(s["stabilize"])
        dt = min(max(now - self._last_proc, 1e-3), 0.2) if self._last_proc else 1 / 30
        self._last_proc = now
        enabled = {p for t, parts in geo.TOGGLE_PARTS.items() if s[f"track_{t}"] for p in parts}
        long_side = int(s["crop_size"])
        for key in sorted(set(detected) | set(self._last_seen)):
            base = geo.base_part(key)
            box = detected.get(key)
            if box is not None:
                prev = self._boxes.get(key)
                if stabilize:   # One Euro：静止時のブレを抑え、動いた時は素早く追従
                    flt = self._filters.get(key) if prev is not None else None
                    if flt is None:
                        flt = self._filters[key] = geo.BoxFilter()
                    box = flt(box, dt, float(s["stabilize_cutoff"]), float(s["stabilize_beta"]))
                elif prev is not None and smooth > 0:
                    box = prev.lerp(box, 1.0 - min(smooth, 0.95))
                self._boxes[key] = box
                self._last_seen[key] = now
                if base not in enabled:
                    continue
                img = geo.crop(frame, box, geo.crop_size(base, long_side), mirror, s["rotate_crops"])
                alpha = None
                if key in masks:
                    alpha = geo.crop((masks[key] * 255).astype(np.uint8), box, geo.crop_size(base, long_side),
                                     mirror, False)
                st = PartState(to_bgra(img, alpha), geo.norm_center(box, fw, fh, mirror), box.w / fw, True)
                snap.parts[key] = st
                self._held[key] = st
                snap.status[key] = "OK"
            else:
                lost_for = now - self._last_seen.get(key, 0)
                if lost_for > hold:   # 見失って保持時間を過ぎた部位は忘れる
                    for d in (self._boxes, self._filters, self._last_seen, self._held):
                        d.pop(key, None)
                    continue
                if base in enabled and key in self._held:
                    h = self._held[key]
                    snap.parts[key] = PartState(h.image, h.pos, h.size, False)
                    snap.status[key] = "HOLD"
        for base in enabled:   # 1人も検出できていない部位は LOST と表示
            if not any(geo.base_part(k) == base for k in snap.status):
                snap.status[base] = "LOST"
        snap.people = len(faces)

        # 顔の位置・速度（窓の生成位置や頭の動きの反応に使う）：いちばん大きく写っている顔
        if faces:
            main = max(range(len(faces)), key=lambda i: faces[i][1][2])
            fbox = self._boxes[geo.part_key("face", pids[main])]
            pos, size = geo.norm_center(fbox, fw, fh, mirror), fbox.w / fw
            snap.face_center, snap.face_w = pos, size
            if self._prev_face is not None and self._prev_face[3] == pids[main]:
                px, py, pt, _ = self._prev_face
                dtf = max(now - pt, 1e-3)
                vx = (pos[0] - px) / dtf / max(size, 0.05)
                vy = (pos[1] - py) / dtf / max(size, 0.05)
                ov = self._snap.face_vel
                snap.face_vel = (ov[0] * 0.6 + vx * 0.4, ov[1] * 0.6 + vy * 0.4)
            self._prev_face = (pos[0], pos[1], now, pids[main])
            snap.mouth_open = max(mouths.values())
        else:
            self._prev_face = None

        # イベント（誰かが口を開けた / 頭を素早く動かした）
        thr = float(s["mouth_open_threshold"])
        for pid, m in mouths.items():
            if m > thr and not self._mouth_open.get(pid):
                self._mouth_open[pid] = True
                self.events.append(("mouth_open", now, geo.part_key("mouth", pid)))
            elif m < thr * 0.6:
                self._mouth_open[pid] = False
        speed = math.hypot(*snap.face_vel)
        if speed > float(s["head_speed_threshold"]) and now - self._last_head_burst > 0.8:
            self._last_head_burst = now
            self.events.append(("head_move", now))

        # モーション（フレーム差分）
        small = cv2.cvtColor(cv2.resize(frame, (MOTION_W, MOTION_H), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        small = cv2.GaussianBlur(small, (5, 5), 0)
        if self._prev_gray is not None:
            diff = cv2.absdiff(small, self._prev_gray)
            m = diff > int(s["motion_threshold"])
            snap.motion_energy = float(m.mean())
            ys, xs = np.nonzero(m)
            if len(xs) > 8:
                cx, cy = xs.mean() / MOTION_W, ys.mean() / MOTION_H
                if mirror:
                    cx = 1 - cx
                snap.motion_center = (cx, cy)
                if self._prev_motion_c is not None:
                    snap.motion_dir = (cx - self._prev_motion_c[0], cy - self._prev_motion_c[1])
                self._prev_motion_c = (cx, cy)
            else:
                self._prev_motion_c = None
        self._prev_gray = small

        snap.preview = self._make_preview(frame, mirror, enabled, snap)
        snap.t_done = time.perf_counter()

        with self._lock:
            self._snap = snap
        self.new_snap.set()

    def _make_preview(self, frame, mirror, enabled, snap) -> np.ndarray:
        fh, fw = frame.shape[:2]
        sc = PREVIEW_W / fw
        pv = cv2.resize(frame, (PREVIEW_W, int(fh * sc)), interpolation=cv2.INTER_AREA)
        labels = []
        for key, box in self._boxes.items():
            base = geo.base_part(key)
            if base not in enabled:
                continue
            st = snap.status.get(key)
            col = BOX_COLORS.get(base, (255, 255, 255)) if st == "OK" else (120, 120, 120)
            pts = cv2.boxPoints(((box.cx * sc, box.cy * sc), (box.w * sc, box.h * sc), math.degrees(box.angle)))
            cv2.polylines(pv, [pts.astype(np.int32)], True, col, 1 if base != "face" else 2, cv2.LINE_AA)
            if base in ("face", "body") or st != "OK":   # 複数人でも見やすいよう、文字は顔・身体と異常時だけ
                labels.append((key, box, col, st))
        if mirror:
            pv = cv2.flip(pv, 1)
        for name, box, col, st in labels:
            x = box.cx * sc
            x = PREVIEW_W - x if mirror else x
            y = (box.cy - box.h / 2) * sc - 3
            txt = geo.part_label(name) + ("" if st == "OK" else f" [{st}]")
            cv2.putText(pv, txt, (int(x - 20), int(max(y, 10))), cv2.FONT_HERSHEY_SIMPLEX, 0.35, col, 1, cv2.LINE_AA)
        if snap.motion_center is not None and self.settings["motion_enabled"]:
            mx, my = snap.motion_center
            r = int(4 + snap.motion_energy * 200)
            cv2.circle(pv, (int(mx * PREVIEW_W), int(my * pv.shape[0])), min(r, 60), (0, 200, 255), 1, cv2.LINE_AA)
        if snap.people > 1:
            cv2.putText(pv, f"{snap.people} PEOPLE", (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (80, 220, 80), 2, cv2.LINE_AA)
        if not self._boxes:
            cv2.putText(pv, "NO FACE DETECTED", (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (60, 60, 255), 2, cv2.LINE_AA)
        return to_bgra(pv)

    def snapshot(self) -> Snapshot:
        with self._lock:
            return self._snap

    def stop(self, timeout: float = 2.0) -> None:
        self._halt.set()
        if self.is_alive():
            self.join(timeout)
