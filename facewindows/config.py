"""設定値・プリセット・保存/読込。

設定はフラットな dict で持ち、エンジン/トラッカー/描画は毎フレーム参照する。
そのため管理画面で値を変えると再起動なしで反映される。
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
PRESET_DIR = CONFIG_DIR / "presets"
MODEL_DIR = ROOT / "models"

# 検出対象（管理画面の TRACKING / Face & Body）。キー → 表示名
PART_TOGGLES = {
    "face": "Face",
    "left_eye": "Left eye",
    "right_eye": "Right eye",
    "nose": "Nose",
    "mouth": "Mouth",
    "body": "Body",
    "arms": "Arms",
    "hands": "Hands",
}

DEFAULTS: dict = {
    # CAMERA
    "camera_index": 0,
    "camera_res": "1280x720",
    "camera_fps": 30,
    "mirror": True,
    # TRACKING / Face & Body
    "track_face": True,
    "track_left_eye": True,
    "track_right_eye": True,
    "track_nose": True,
    "track_mouth": True,
    "track_body": False,
    "track_arms": False,
    "track_hands": False,
    "det_confidence": 0.5,        # 検出信頼度しきい値（MediaPipe min_*_confidence）
    "motion_enabled": True,       # 動きを生成トリガーに使う
    "motion_threshold": 25,       # フレーム差分の画素しきい値（0-255）
    "motion_sensitivity": 1.0,    # 動き量 → 生成量のゲイン
    "box_smoothing": 0.35,        # 切り抜き枠の平滑化（0=なし）。ブレ補正OFFの時に使う
    "stabilize": True,            # ブレ補正（One Euro フィルタ）
    "stabilize_cutoff": 1.2,      # 静止時のブレ補正 [Hz]（小さいほど強い）
    "stabilize_beta": 4.0,        # 動いた時の追従性（大きいほど遅れが少ない）
    "rotate_crops": True,         # 顔の傾きに合わせて切り抜きを回転
    "extra_detect_interval": 2,   # Body/Arms/Hands 検出の間引き（Nフレームに1回）
    "lost_hold_s": 0.6,           # 追跡失敗時に最終映像を保持する秒数
    "mouth_burst": True,          # 口を開けたらBURST
    "mouth_open_threshold": 0.35,
    "head_burst": True,           # 頭を素早く動かしたらBURST
    "head_speed_threshold": 1.2,  # 顔幅/秒
    # GENERATION
    "max_windows": 100,
    "spawn_rate": 30.0,           # 個/秒
    "burst_count": 40,
    "life_s": 4.0,
    "life_jitter": 0.4,
    "spawn_area": "part",         # face / part / random / motion
    "spawn_spread": 160,          # 生成位置の散らばり(px)
    "size": 150,                  # 映像部の幅(px)
    "size_jitter": 0.45,
    "face_ratio": 0.3,            # 顔全体 : 部分 の出現比率
    "duplicate_bias": 0.35,       # 直前と同じパーツを複製する確率
    "tracking_range": 1.3,        # カメラ座標 → 画面座標の拡大率
    # MOTION
    "motion_mode": "mix",         # follow / scatter / drift / fixed / mix
    "speed": 1.0,
    "follow_speed": 3.0,
    "follow_lag_jitter": 0.7,
    "scatter": 1.0,
    "randomness": 0.5,
    "damping": 1.2,
    "edge_mode": "bounce",        # bounce / wrap / kill / respawn
    "face_push": 1.0,             # 顔の移動が窓に与える勢い
    # LAYOUT（swarm: 顔から窓が増殖して動き回る / mirror: カメラ＝デスクトップ 1:1 で部位の位置・大きさに窓を置く）
    "layout_mode": "swarm",
    "mirror_style": "stamp",      # stamp: ずれたら新しい窓を生成（窓は動かない）/ track: 窓が部位を追いかける
    "mirror_stamp_move": 0.2,     # 窓の大きさの何割ずれたら新しい窓を生成するか
    "mirror_stamp_interval": 0.05,  # 同じ部位の生成間隔の最小値(秒)
    "mirror_stamp_life": 2.5,     # 置いていかれた窓が残る秒数
    "mirror_stamp_freeze": False, # 置いていかれた窓の映像を止める（OFF=ライブ映像のまま）
    "mirror_fit": "cover",        # cover（比率維持で画面を埋める）/ contain（比率維持で収める）/ stretch
    "mirror_scale": 1.0,          # 部位の大きさに対する窓の大きさ
    "mirror_trails": 0,           # 部位ごとの残像窓の数（遅れて追従＋過去映像）。0=ミラーの位置だけに表示
    "mirror_reactions": False,    # ミラー表示でも口/頭の反応BURST（飛び回る窓）を出す
    "mirror_trail_lag": 0.12,     # 残像1段ごとの遅れ(秒)
    "mirror_trail_opacity": 0.8,
    "mirror_glide": 0.035,        # 検出の間を補間して窓をなめらかに動かす時定数(秒)。0=検出位置へ即移動
    "crop_size": 240,             # 切り抜き解像度（長辺px）。ミラー表示で大きく映すなら 360〜480
    # DISPLAY
    "render_mode": "overlay",     # overlay（疑似）/ native（OS実ウィンドウ）/ hybrid
    "native_max": 24,
    "target_screen": 0,
    "window_style": "win11_light",  # win11_light / win11_dark / macos / retro / frameless
    "opacity": 1.0,
    "shadow": True,
    "snapshot_ratio": 0.1,        # 静止画スナップショット窓の割合
    "delay_ratio": 0.15,          # 過去映像（ディレイ）窓の割合
    "delay_s": 1.0,
    "afterimage_ratio": 0.25,     # 消える窓のうち静止して残る割合
    "afterimage_s": 1.5,
    "avoid_panel": True,          # 管理ウィンドウの上に描かない
    "smooth_scaling": False,
    # PERFORMANCE
    "adaptive": True,
    "target_fps": 30,
    "min_windows": 10,
    # SYSTEM
    "panel_on_top": True,
    "autostart": False,
}

CHOICES = {
    "camera_res": ["640x480", "1280x720", "1920x1080"],
    "camera_fps": [15, 30, 60],
    "spawn_area": ["face", "part", "random", "motion"],
    "motion_mode": ["follow", "scatter", "drift", "fixed", "mix"],
    "edge_mode": ["bounce", "wrap", "kill", "respawn"],
    "render_mode": ["overlay", "native", "hybrid"],
    "layout_mode": ["swarm", "mirror"],
    "mirror_fit": ["cover", "contain", "stretch"],
    "mirror_style": ["stamp", "track"],
    "crop_size": [160, 240, 360, 480],
    "window_style": ["win11_light", "win11_dark", "macos", "retro", "frameless"],
}

BUILTIN_PRESETS: dict[str, dict] = {
    "高速増殖": {
        "max_windows": 220, "spawn_rate": 90.0, "burst_count": 80, "life_s": 2.2,
        "motion_mode": "scatter", "speed": 1.6, "scatter": 1.6, "size": 120,
        "size_jitter": 0.6, "spawn_area": "part", "afterimage_ratio": 0.3,
    },
    "顔追従": {
        "max_windows": 70, "spawn_rate": 20.0, "burst_count": 25, "life_s": 6.0,
        "motion_mode": "follow", "follow_speed": 4.0, "follow_lag_jitter": 0.9,
        "spawn_spread": 220, "speed": 1.0, "scatter": 0.6, "size": 170,
        "spawn_area": "face", "delay_ratio": 0.25,
    },
    "ミラー（顔を再構成）": {
        "layout_mode": "mirror", "mirror_trails": 0, "mirror_reactions": False, "crop_size": 360,
        "box_smoothing": 0.2, "rotate_crops": False, "shadow": True,
    },
    "ランダム飛散": {
        "max_windows": 150, "spawn_rate": 45.0, "burst_count": 60, "life_s": 5.0,
        "motion_mode": "drift", "randomness": 1.4, "speed": 1.3, "scatter": 1.2,
        "spawn_area": "random", "edge_mode": "wrap", "size": 140, "size_jitter": 0.7,
    },
}


# プリセット適用時に上書きしない環境依存の設定
PRESET_EXCLUDE = {"camera_index", "camera_res", "camera_fps", "mirror", "target_screen",
                  "render_mode", "native_max", "panel_on_top", "autostart"}


def defaults() -> dict:
    return copy.deepcopy(DEFAULTS)


def sanitize(data: dict) -> dict:
    """未知のキーを捨て、型を既定値に合わせる。壊れた設定ファイルでも落ちない。"""
    out = defaults()
    for k, v in (data or {}).items():
        if k not in DEFAULTS:
            continue
        d = DEFAULTS[k]
        try:
            if isinstance(d, bool):
                out[k] = bool(v)
            elif isinstance(d, int):
                out[k] = int(v)
            elif isinstance(d, float):
                out[k] = float(v)
            else:
                out[k] = str(v)
        except (TypeError, ValueError):
            continue
        if k in CHOICES and out[k] not in CHOICES[k]:
            out[k] = d
    return out


def save(settings: dict, path: Path = SETTINGS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load(path: Path = SETTINGS_PATH) -> dict:
    try:
        return sanitize(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return defaults()


def list_presets() -> list[str]:
    names = list(BUILTIN_PRESETS)
    if PRESET_DIR.exists():
        names += sorted(p.stem for p in PRESET_DIR.glob("*.json") if p.stem not in BUILTIN_PRESETS)
    return names


def preset_values(name: str) -> dict:
    """プリセットの全設定値（組み込みは既定値との差分から作る）。"""
    if name in BUILTIN_PRESETS:
        base = defaults()
        base.update(BUILTIN_PRESETS[name])
        return base
    return load(PRESET_DIR / f"{name}.json")


def apply_preset(current: dict, name: str) -> dict:
    """現在の設定にプリセットを適用する。カメラ等の環境依存設定は維持する。"""
    out = dict(current)
    for k, v in preset_values(name).items():
        if k not in PRESET_EXCLUDE:
            out[k] = v
    return out


def save_preset(name: str, settings: dict) -> Path:
    safe = "".join(c for c in name if c not in '\\/:*?"<>|').strip() or "preset"
    path = PRESET_DIR / f"{safe}.json"
    save(settings, path)
    return path
