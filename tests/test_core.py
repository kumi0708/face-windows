"""Qt 画面やカメラを使わずに検証できる部分のテスト（設定・切り抜き幾何・シミュレーション）。"""
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from facewindows import config, geometry as geo  # noqa: E402
from facewindows.engine import Engine  # noqa: E402
from facewindows.tracker import PartState, Snapshot  # noqa: E402


def make_snap(parts=("face", "left_eye", "right_eye", "nose", "mouth")):
    img = object()  # 描画しないので画像は何でもよい
    return Snapshot(parts={p: PartState(img, (0.5, 0.5), 0.2, True) for p in parts},
                    face_center=(0.5, 0.5))


def make_engine(**over):
    s = config.defaults()
    s.update(over)
    e = Engine(s)
    e.rect = (0.0, 0.0, 1600.0, 900.0)
    e.running = True
    return e


# ---------- config ----------
def test_sanitize_rejects_bad_values():
    s = config.sanitize({"max_windows": "50", "motion_mode": "nope", "unknown": 1, "mirror": 0,
                         "spawn_rate": "abc"})
    assert s["max_windows"] == 50
    assert s["motion_mode"] == config.DEFAULTS["motion_mode"]
    assert "unknown" not in s
    assert s["mirror"] is False
    assert s["spawn_rate"] == config.DEFAULTS["spawn_rate"]


def test_save_load_roundtrip(tmp_path):
    s = config.defaults()
    s["max_windows"] = 321
    s["window_style"] = "retro"
    p = tmp_path / "s.json"
    config.save(s, p)
    assert config.load(p) == s


def test_preset_keeps_camera_settings():
    cur = config.defaults()
    cur["camera_index"] = 2
    cur["mirror"] = False
    out = config.apply_preset(cur, "高速増殖")
    assert out["camera_index"] == 2 and out["mirror"] is False
    assert out["max_windows"] == config.BUILTIN_PRESETS["高速増殖"]["max_windows"]


def test_builtin_presets_are_valid_keys():
    for vals in config.BUILTIN_PRESETS.values():
        assert set(vals) <= set(config.DEFAULTS)


# ---------- geometry ----------
def test_box_aspect_and_padding():
    pts = np.array([[100, 100], [200, 100], [200, 150], [100, 150]], dtype=np.float32)
    for part in ("mouth", "face", "nose"):
        b = geo.box_from_points(pts, part)
        assert b.w / b.h == pytest.approx(geo.PART_SHAPE[part][0], rel=1e-3)
        assert b.w >= 100 and b.h >= 50
        assert (b.cx, b.cy) == pytest.approx((150, 125))


def test_rotated_box_contains_points():
    ang = math.radians(20)
    pts = np.array([[0, 0], [100, 0], [100, 40], [0, 40]], dtype=np.float32)
    rot = np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]])
    pts = pts @ rot.T + 300
    b = geo.box_from_points(pts, "mouth", ang)
    # 回転を戻した座標で全点が枠内
    c, s = math.cos(ang), math.sin(ang)
    for x, y in pts:
        dx, dy = x - b.cx, y - b.cy
        assert abs(dx * c + dy * s) <= b.w / 2 + 1e-3
        assert abs(-dx * s + dy * c) <= b.h / 2 + 1e-3


def test_crop_mirror_and_orientation():
    frame = np.zeros((100, 200, 3), np.uint8)
    frame[:, :100] = (255, 0, 0)       # 左半分が青
    box = geo.Box(100, 50, 100, 50)
    plain = geo.crop(frame, box, (40, 20), mirror=False)
    mir = geo.crop(frame, box, (40, 20), mirror=True)
    assert plain[10, 5, 0] == 255 and plain[10, 35, 0] == 0
    assert mir[10, 5, 0] == 0 and mir[10, 35, 0] == 255


def test_norm_center_mirror():
    b = geo.Box(40, 50, 10, 10)
    assert geo.norm_center(b, 200, 100, False) == pytest.approx((0.2, 0.5))
    assert geo.norm_center(b, 200, 100, True) == pytest.approx((0.8, 0.5))


def test_left_right_eye_landmark_sets_are_person_based():
    # 人物の右目は 33 側（生画像では画面左）
    assert 33 in geo.RIGHT_EYE and 263 in geo.LEFT_EYE


def test_mouth_openness():
    pts = np.zeros((478, 2), np.float32)
    pts[61], pts[291] = (0, 0), (100, 0)
    pts[13], pts[14] = (50, -5), (50, 45)
    assert geo.mouth_openness(pts) == pytest.approx(0.5)


# ---------- engine ----------
def test_spawn_respects_max_and_recycles():
    e = make_engine(max_windows=30, spawn_rate=1000.0, motion_enabled=False, adaptive=False)
    snap = make_snap()
    t = 0.0
    for _ in range(200):
        t += 1 / 60
        e.update(1 / 60, t, snap)
        assert len(e.wins) <= 30
    assert len(e.wins) == 30


def test_burst_is_capped():
    e = make_engine(max_windows=50, adaptive=False)
    e.burst(make_snap(), 0.0, 10_000)
    assert len(e.wins) == 50


def test_all_parts_off_spawns_nothing():
    over = {f"track_{k}": False for k in config.PART_TOGGLES}
    e = make_engine(spawn_rate=500.0, motion_enabled=False, **over)
    snap = make_snap()
    for i in range(30):
        e.update(1 / 30, i / 30, snap)
    assert e.wins == []


def test_disabled_part_not_spawned():
    e = make_engine(track_face=False, track_nose=False, spawn_rate=500.0, motion_enabled=False)
    snap = make_snap()
    for i in range(30):
        e.update(1 / 30, i / 30, snap)
    assert e.wins and {w.part for w in e.wins} <= {"left_eye", "right_eye", "mouth"}


def test_windows_stay_recoverable():
    for edge in config.CHOICES["edge_mode"]:
        e = make_engine(edge_mode=edge, motion_mode="scatter", speed=4.0, scatter=3.0, damping=0.0,
                        life_s=100.0, spawn_rate=200.0, motion_enabled=False, afterimage_ratio=0.0)
        snap = make_snap()
        t = 0.0
        for _ in range(600):
            t += 1 / 60
            e.update(1 / 60, t, snap)
        for w in e.wins:
            assert -2 * w.w - 1 <= w.x <= 1600 + 2 * w.w + 1
            assert -2 * w.h - 1 <= w.y <= 900 + 2 * w.h + 1


def test_lifetime_expires():
    e = make_engine(life_s=0.5, life_jitter=0.0, afterimage_ratio=0.0, spawn_rate=0.0)
    snap = make_snap()
    e.burst(snap, 0.0, 10)
    t = 0.0
    for _ in range(90):
        t += 1 / 60
        e.update(1 / 60, t, snap)
    assert e.wins == []


def test_lost_part_windows_fade_out():
    e = make_engine(spawn_rate=0.0, life_s=100.0, snapshot_ratio=0.0, delay_ratio=0.0)
    e.burst(make_snap(("mouth",)), 0.0, 5, part="mouth")
    assert len(e.wins) == 5
    empty = make_snap(())
    t = 0.0
    for _ in range(40):
        t += 1 / 60
        e.update(1 / 60, t, empty)
    assert e.wins == []


def test_adaptive_reduces_cap_and_recovers():
    e = make_engine(max_windows=200, min_windows=10, target_fps=30, spawn_rate=0.0)
    e.burst(make_snap(), 0.0, 200)
    t = 0.0
    for _ in range(20):
        t += 0.6
        e.update(0.016, t, make_snap(), render_fps=10.0)
    assert e.effective_max() < 200 and e.cap_reason
    for _ in range(100):
        t += 0.6
        e.update(0.016, t, make_snap(), render_fps=60.0)
    assert e.effective_max() == 200 and not e.cap_reason


def test_native_mode_limits_native_count():
    e = make_engine(render_mode="native", native_max=5, max_windows=100, adaptive=False)
    e.burst(make_snap(), 0.0, 40)
    assert e.native_count() <= 5 and all(w.backend == "native" for w in e.wins)
    e2 = make_engine(render_mode="hybrid", native_max=5, max_windows=100, adaptive=False)
    e2.burst(make_snap(), 0.0, 40)
    assert e2.native_count() == 5 and len(e2.wins) == 40


def test_mouth_event_triggers_burst():
    e = make_engine(spawn_rate=0.0, burst_count=40, adaptive=False)
    e.update(0.016, 0.0, make_snap(), events=[("mouth_open", 0.0)])
    assert len(e.wins) == 20 and all(w.part == "mouth" for w in e.wins)


def test_paused_freezes():
    e = make_engine(spawn_rate=0.0, motion_mode="scatter")
    snap = make_snap()
    e.burst(snap, 0.0, 5)
    e.paused = True
    before = [(w.x, w.y) for w in e.wins]
    e.update(0.05, 0.05, snap)
    assert [(w.x, w.y) for w in e.wins] == before


# ---------- mirror layout ----------
def mirror_snap(parts=("face", "left_eye", "mouth"), pos=(0.5, 0.5), size=0.25):
    img = object()
    return Snapshot(parts={p: PartState(img, pos, size, True) for p in parts},
                    face_center=pos, frame_size=(1280, 720))


def make_mirror(**over):
    over.setdefault("mirror_trails", 2)
    over.setdefault("mirror_style", "track")
    e = make_engine(layout_mode="mirror", **over)
    e.screen_rect = (0.0, 0.0, 1920.0, 1200.0)
    return e


def test_mirror_target_cover_and_contain():
    snap = mirror_snap(pos=(0.25, 0.5), size=0.25)
    e = make_mirror(mirror_fit="cover")
    x, y, w, h = e.mirror_target("face", snap)
    k = max(1920 / 1280, 1200 / 720)            # 比率維持で画面を埋める
    assert x == pytest.approx(960 - 0.25 * 1280 * k)
    assert y == pytest.approx(600)
    assert w == pytest.approx(0.25 * 1280 * k)
    assert h == pytest.approx(w / geo.PART_SHAPE["face"][0])
    e.s["mirror_fit"] = "contain"
    x2, _, w2, _ = e.mirror_target("face", snap)
    assert w2 == pytest.approx(0.25 * 1280 * min(1920 / 1280, 1200 / 720))
    e.s["mirror_fit"] = "stretch"
    x3, _, w3, _ = e.mirror_target("face", snap)
    assert x3 == pytest.approx(480) and w3 == pytest.approx(0.25 * 1920)


def test_mirror_one_window_per_part_plus_trails():
    e = make_mirror(spawn_rate=500.0, motion_enabled=False)
    snap = mirror_snap()
    for i in range(10):
        e.update(1 / 60, i / 60, snap)
    assert len(e.wins) == 3 * 3            # 3部位 ×（本体1＋残像2）、自動生成なし
    anchors = [w for w in e.wins if w.mirror_key[1] == 0]
    assert {w.part for w in anchors} == {"face", "left_eye", "mouth"}
    # 顔が奥、口が手前
    order = [w.part for w in anchors]
    assert order.index("face") < order.index("mouth")
    face = next(w for w in anchors if w.part == "face")
    assert (face.x, face.y) == pytest.approx(e.mirror_target("face", snap)[:2])


def test_mirror_follows_and_trails_lag():
    e = make_mirror(mirror_glide=0.0)
    t = 0.0
    for _ in range(30):
        t += 1 / 60
        e.update(1 / 60, t, mirror_snap(pos=(0.3, 0.5)))
    moved = mirror_snap(pos=(0.7, 0.5))
    e.update(1 / 60, t + 1 / 60, moved)
    anchor = e._mirror[("face", 0)]
    trail = e._mirror[("face", 2)]
    tx = e.mirror_target("face", moved)[0]
    assert anchor.x == pytest.approx(tx)
    assert trail.x < anchor.x - 100          # 残像は遅れてついてくる
    assert trail.image_mode == "delay" and trail.delay > 0


def test_mirror_lost_part_removed_and_burst_keeps_anchors():
    e = make_mirror(max_windows=12, adaptive=False)
    snap = mirror_snap()
    e.update(1 / 60, 0.0, snap)
    e.burst(snap, 0.0, 100)                  # 上限に達してもミラー窓は再利用されない
    assert all(k in e._mirror for k in [("face", 0), ("mouth", 0), ("left_eye", 0)])
    assert len(e.wins) == 12
    t = 0.0
    for _ in range(40):
        t += 1 / 60
        e.update(1 / 60, t, mirror_snap(parts=("face",)))
    assert not any(w.part == "mouth" and w.mirror_key for w in e.wins)


def test_switching_back_to_swarm_removes_mirror_windows():
    e = make_mirror()
    snap = mirror_snap()
    e.update(1 / 60, 0.0, snap)
    e.s["layout_mode"] = "swarm"
    e.s["spawn_rate"] = 0.0
    t = 0.0
    for _ in range(30):
        t += 1 / 60
        e.update(1 / 60, t, snap)
    assert not any(w.mirror_key for w in e.wins)


def test_mirror_ignores_reaction_bursts_by_default():
    e = make_mirror()
    snap = mirror_snap()
    e.update(1 / 60, 0.0, snap, events=[("head_move", 0.0), ("mouth_open", 0.0)])
    assert all(w.mirror_key is not None for w in e.wins)
    e.s["mirror_reactions"] = True
    e.update(1 / 60, 1 / 60, snap, events=[("head_move", 0.0)])
    assert any(w.mirror_key is None for w in e.wins)


def test_mirror_windows_stay_exactly_on_target_while_moving():
    e = make_mirror(mirror_trails=0, mirror_glide=0.0)
    t = 0.0
    for i in range(60):
        t += 1 / 60
        snap = mirror_snap(pos=(0.2 + i * 0.01, 0.5 + 0.1 * math.sin(i / 5)))
        e.update(1 / 60, t, snap)
        for w in e.wins:
            x, y, ww, hh = e.mirror_target(w.part, snap)
            assert (w.x, w.y, w.w, w.h) == pytest.approx((x, y, ww, hh))


def test_switch_to_mirror_fades_swarm_windows():
    e = make_engine(spawn_rate=0.0, life_s=100.0)
    e.screen_rect = (0.0, 0.0, 1920.0, 1200.0)
    snap = mirror_snap()
    e.burst(snap, 0.0, 10)
    e.s["layout_mode"] = "mirror"
    t = 0.0
    for _ in range(30):
        t += 1 / 60
        e.update(1 / 60, t, snap)
    assert e.wins and all(w.mirror_key is not None for w in e.wins)


# ---------- stabilization / glide ----------
def test_one_euro_suppresses_jitter_but_follows_motion():
    rng = np.random.default_rng(0)
    f = geo.OneEuro(min_cutoff=1.2, beta=0.02)
    dt = 1 / 30
    still = [f(500 + rng.normal(0, 2), dt) for _ in range(90)]
    assert np.std(np.diff(still[30:])) < 0.3 * np.std(np.diff(500 + rng.normal(0, 2, 90)))
    # 速く動く（1秒で600px）と遅れは小さい
    xs = [f(500 + 600 * i * dt, dt) for i in range(30)]
    assert 500 + 600 * 29 * dt - xs[-1] < 40


def test_box_filter_scale_invariant_beta():
    bf = geo.BoxFilter()
    b = geo.Box(100, 100, 200, 250)
    out = bf(b, 1 / 30, 1.2, 4.0)
    assert (out.cx, out.w) == pytest.approx((100, 200))


def test_mirror_glide_moves_smoothly_between_detections():
    e = make_mirror(mirror_trails=0, mirror_glide=0.035)
    t = 0.0
    snap = mirror_snap(pos=(0.3, 0.5))
    for _ in range(30):
        t += 1 / 60
        e.update(1 / 60, t, snap)
    moved = mirror_snap(pos=(0.35, 0.5))       # 検出が1回で大きく動いた
    target = e.mirror_target("face", moved)[0]
    xs = []
    for _ in range(12):
        t += 1 / 60
        e.update(1 / 60, t, moved)
        xs.append(e._mirror[("face", 0)].x)
    steps = np.diff([e.mirror_target("face", snap)[0]] + xs)
    assert steps.max() < (target - e.mirror_target("face", snap)[0]) * 0.5   # 一気に跳ばない
    assert abs(xs[-1] - target) < 2                                            # 0.2秒で追いつく


# ---------- mirror: stamp style（ずれたら新しい窓を生成） ----------
def test_stamp_still_face_keeps_one_window_per_part():
    e = make_mirror(mirror_style="stamp")
    snap = mirror_snap()
    t = 0.0
    for _ in range(300):              # 5秒静止
        t += 1 / 60
        e.update(1 / 60, t, snap)
    assert len(e.wins) == 3
    assert all(w.mirror_key and w.mode == "mirror" for w in e.wins)


def test_stamp_moving_face_spawns_new_windows_and_old_ones_stay_put():
    e = make_mirror(mirror_style="stamp", mirror_stamp_move=0.2, mirror_stamp_interval=0.0,
                    mirror_stamp_life=10.0, max_windows=500, adaptive=False)
    t = 0.0
    positions = {}
    for i in range(60):
        t += 1 / 60
        e.update(1 / 60, t, mirror_snap(pos=(0.2 + i * 0.01, 0.5)))
        for w in e.wins:
            positions.setdefault(w.id, []).append((w.x, w.y, w.w, w.h))
    faces = [w for w in e.wins if w.part == "face"]
    assert len(faces) > 3                                   # 動いた分だけ生成された
    for track in positions.values():                        # どの窓も一度も動いていない
        assert all(p == pytest.approx(track[0]) for p in track)
    newest = e._mirror[("face", 0)]
    assert e.wins.index(newest) > e.wins.index(faces[0])    # 新しい窓が手前
    tx, ty, _, _ = e.mirror_target("face", mirror_snap(pos=(0.2 + 59 * 0.01, 0.5)))
    assert math.hypot(newest.x - tx, newest.y - ty) <= 0.2 * min(newest.w, newest.h)   # ずれはしきい値以内


def test_stamp_left_windows_expire_and_respect_max():
    e = make_mirror(mirror_style="stamp", mirror_stamp_move=0.05, mirror_stamp_interval=0.0,
                    mirror_stamp_life=0.5, max_windows=20, adaptive=False)
    t = 0.0
    for i in range(120):
        t += 1 / 60
        e.update(1 / 60, t, mirror_snap(pos=(0.2 + (i % 40) * 0.015, 0.5)))
        assert len(e.wins) <= 20
    for _ in range(120):              # 静止すると置いていかれた窓は消えて、部位ごと1枚に戻る
        t += 1 / 60
        e.update(1 / 60, t, mirror_snap(pos=(0.5, 0.5)))
    assert len(e.wins) == 3


def test_stamp_freeze_option():
    e = make_mirror(mirror_style="stamp", mirror_stamp_move=0.05, mirror_stamp_interval=0.0,
                    mirror_stamp_freeze=True)
    e.update(1 / 60, 0.0, mirror_snap(pos=(0.3, 0.5)))
    e.update(1 / 60, 0.1, mirror_snap(pos=(0.6, 0.5)))
    left = [w for w in e.wins if w.mode == "stamp"]
    assert left and all(w.image_mode == "snap" for w in left)


def test_stamp_fade_timing_and_no_flash():
    e = make_mirror(mirror_style="stamp", mirror_stamp_move=0.05, mirror_stamp_interval=0.0,
                    mirror_stamp_life=2.0, mirror_stamp_fade=1.0)
    e.update(1 / 60, 0.0, mirror_snap(pos=(0.3, 0.5)))
    e.update(1 / 60, 0.2, mirror_snap(pos=(0.6, 0.5)))       # ここで古い窓が置いていかれる
    left = next(w for w in e.wins if w.mode == "stamp")
    t, alphas = 0.2, []
    snap = mirror_snap(pos=(0.6, 0.5))
    while left in e.wins:
        t += 1 / 60
        e.update(1 / 60, t, snap)
        alphas.append((round(t - 0.2, 3), left.alpha))
    assert all(a == pytest.approx(1.0) for dt_, a in alphas if dt_ < 0.95)   # 最初の1秒は濃いまま
    assert any(0.3 < a < 0.7 for dt_, a in alphas if 1.4 < dt_ < 1.6)         # 途中で半分くらい
    assert all(b <= a + 1e-9 for (_, a), (_, b) in zip(alphas, alphas[1:]))  # 一度も濃く戻らない
    assert 2.0 <= alphas[-1][0] <= 2.0 + 0.3                                  # 約2秒で消える
