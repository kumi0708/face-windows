"""ランドマーク → 切り抜き枠 の計算（Qt/MediaPipe 非依存、テスト可能）。

座標の約束:
- 検出は常に「反転していない生のカメラ画像」で行う。
- パーツ名は「人物から見た左右」。MediaPipe の landmark 33 側は人物の右目で、
  生画像では画面左に写る。
- 左右反転（mirror）は、切り抜き画像の flip と正規化座標 x → 1-x でのみ扱う。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

RIGHT_EYE = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246,
             70, 63, 105, 66, 107, 55, 65, 52, 53, 46]
LEFT_EYE = [263, 249, 390, 373, 374, 380, 381, 382, 362, 398, 384, 385, 386, 387, 388, 466,
            300, 293, 334, 296, 336, 285, 295, 282, 283, 276]
NOSE = [168, 6, 197, 195, 5, 4, 1, 19, 94, 2, 98, 327, 48, 278, 64, 294, 129, 358, 102, 331]
MOUTH = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 185, 40, 39, 37, 0, 267, 269,
         270, 409, 78, 308, 13, 14]
FACE_LANDMARK_SETS = {"left_eye": LEFT_EYE, "right_eye": RIGHT_EYE, "nose": NOSE, "mouth": MOUTH}

# パーツ → (横/縦の比, 余白倍率)
PART_SHAPE = {
    "face": (0.8, 1.12),
    "left_eye": (1.6, 1.25),
    "right_eye": (1.6, 1.25),
    "nose": (1.0, 1.35),
    "mouth": (1.8, 1.35),
    "body": (0.75, 1.08),
    "left_arm": (1.0, 1.2),
    "right_arm": (1.0, 1.2),
    "left_hand": (1.0, 1.4),
    "right_hand": (1.0, 1.4),
}
PART_LABEL = {
    "face": "Face", "left_eye": "Left Eye", "right_eye": "Right Eye", "nose": "Nose",
    "mouth": "Mouth", "body": "Body", "left_arm": "Left Arm", "right_arm": "Right Arm",
    "left_hand": "Left Hand", "right_hand": "Right Hand",
}
# 管理画面のトグル → 実パーツ
TOGGLE_PARTS = {
    "face": ["face"], "left_eye": ["left_eye"], "right_eye": ["right_eye"], "nose": ["nose"],
    "mouth": ["mouth"], "body": ["body"], "arms": ["left_arm", "right_arm"],
    "hands": ["left_hand", "right_hand"],
}
CROP_LONG_SIDE = 240


@dataclass
class Box:
    """生画像ピクセル座標の回転矩形。angle はラジアン（画像座標、時計回り正）。"""
    cx: float
    cy: float
    w: float
    h: float
    angle: float = 0.0

    def lerp(self, other: "Box", a: float) -> "Box":
        """self から other へ a の割合だけ寄せる（a=1 で other）。"""
        da = math.atan2(math.sin(other.angle - self.angle), math.cos(other.angle - self.angle))
        return Box(self.cx + (other.cx - self.cx) * a, self.cy + (other.cy - self.cy) * a,
                   self.w + (other.w - self.w) * a, self.h + (other.h - self.h) * a,
                   self.angle + da * a)


class OneEuro:
    """One Euro フィルタ（Casiez et al. 2012）。止まっている時はブレを強く抑え、速く動くと追従が速くなる。
    min_cutoff[Hz]: 小さいほど静止時のブレが減る（遅れは増える）。beta: 速さに応じて遅れを減らす量。"""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.0, d_cutoff: float = 1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x: float | None = None
        self.dx = 0.0

    @staticmethod
    def _alpha(dt: float, cutoff: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: float, dt: float) -> float:
        if self.x is None or dt <= 0:
            self.x = x
            return x
        dx = (x - self.x) / dt
        self.dx += self._alpha(dt, self.d_cutoff) * (dx - self.dx)
        cutoff = self.min_cutoff + self.beta * abs(self.dx)
        self.x += self._alpha(dt, cutoff) * (x - self.x)
        return self.x


class BoxFilter:
    """枠（中心・大きさ・角度）を One Euro で安定させる。大きさは位置より強めに抑える。
    beta は枠の幅に対する相対速度で効くので、顔の遠近に関係なく同じ感覚で調整できる。"""

    def __init__(self):
        self.f = [OneEuro() for _ in range(5)]

    def __call__(self, box: Box, dt: float, min_cutoff: float, beta: float) -> Box:
        scale = max(box.w, 1.0)
        for i, f in enumerate(self.f):
            size_like = i in (2, 3)
            f.min_cutoff = min_cutoff * (0.5 if size_like else 1.0)
            f.beta = beta / scale * (0.5 if size_like else 1.0)
        return Box(self.f[0](box.cx, dt), self.f[1](box.cy, dt), self.f[2](box.w, dt),
                   self.f[3](box.h, dt), self.f[4](box.angle, dt))


def box_from_points(pts: np.ndarray, part: str, angle: float = 0.0) -> Box:
    """点群（N×2, ピクセル）を、角度 angle で回した座標系で囲む枠を作る。"""
    aspect, pad = PART_SHAPE[part]
    c, s = math.cos(angle), math.sin(angle)
    # 点を -angle 回転した座標で外接矩形を取る
    rx = pts[:, 0] * c + pts[:, 1] * s
    ry = -pts[:, 0] * s + pts[:, 1] * c
    x0, x1, y0, y1 = rx.min(), rx.max(), ry.min(), ry.max()
    w, h = (x1 - x0) * pad, (y1 - y0) * pad
    w, h = max(w, 4.0), max(h, 4.0)
    if w / h < aspect:
        w = h * aspect
    else:
        h = w / aspect
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    return Box(mx * c - my * s, mx * s + my * c, w, h, angle)


def crop_size(part: str, long_side: int = CROP_LONG_SIDE) -> tuple[int, int]:
    aspect = PART_SHAPE[part][0]
    if aspect >= 1:
        return long_side, max(8, int(round(long_side / aspect)))
    return max(8, int(round(long_side * aspect))), long_side


def crop(frame: np.ndarray, box: Box, size: tuple[int, int], mirror: bool,
         use_angle: bool = True) -> np.ndarray:
    """回転矩形を切り抜いて size(w,h) にリサイズ。はみ出しは端の画素で埋める。"""
    ow, oh = size
    a = box.angle if use_angle else 0.0
    s = ow / box.w
    c, sn = math.cos(a), math.sin(a)
    m = np.array([
        [s * c, s * sn, ow / 2 - s * (c * box.cx + sn * box.cy)],
        [-s * sn, s * c, oh / 2 - s * (-sn * box.cx + c * box.cy)],
    ], dtype=np.float32)
    out = cv2.warpAffine(frame, m, (ow, oh), flags=cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_REPLICATE)
    if mirror:
        out = cv2.flip(out, 1)
    return out


def norm_center(box: Box, fw: int, fh: int, mirror: bool) -> tuple[float, float]:
    """枠中心を 0..1 の表示座標へ（mirror 時は x を反転）。"""
    x, y = box.cx / fw, box.cy / fh
    return (1.0 - x if mirror else x), y


def face_roll(pts: np.ndarray) -> float:
    """両目尻（33, 263）を結ぶ線の傾き。"""
    dx, dy = pts[263] - pts[33]
    return math.atan2(dy, dx)


def mouth_openness(pts: np.ndarray) -> float:
    """口の縦開き / 横幅。閉じていると ~0.02、大きく開くと 0.5 以上。"""
    width = np.linalg.norm(pts[291] - pts[61]) + 1e-6
    return float(np.linalg.norm(pts[14] - pts[13]) / width)
