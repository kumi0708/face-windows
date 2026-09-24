"""ウィンドウ群のシミュレーション（生成・移動・寿命・上限・適応制御）。

描画方式（疑似/OS実ウィンドウ）には依存しない。座標は Qt の論理座標（画面座標）。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from . import geometry as geo

MODES = ["follow", "scatter", "drift", "fixed"]
MIX_WEIGHTS = [0.35, 0.35, 0.2, 0.1]
PART_SCALE = {"face": 1.3, "body": 1.5}
SPAWN_ANIM_S = 0.12
DEATH_ANIM_S = 0.25
MAX_SPAWN_PER_TICK = 80
# ミラー表示の重なり順（先が奥）。顔の上に鼻・目・口、身体は一番奥、手は手前
MIRROR_Z = ["body", "left_arm", "right_arm", "face", "nose", "left_eye", "right_eye", "mouth",
            "left_hand", "right_hand"]


@dataclass
class Win:
    id: int
    part: str
    x: float                 # 中心座標
    y: float
    w: float                 # 映像部のサイズ
    h: float
    mode: str
    born: float
    life: float
    vx: float = 0.0
    vy: float = 0.0
    follow_k: float = 1.0
    ox: float = 0.0          # 追従時のオフセット
    oy: float = 0.0
    image_mode: str = "live"  # live / snap / delay
    delay: float = 0.0
    frozen: object = None     # QImage（snap / 残像）
    last_image: object = None
    backend: str = "overlay"  # overlay / native
    dying_since: float | None = None
    afterimage: bool = False
    scale: float = 1.0
    alpha: float = 1.0
    mirror_key: tuple | None = None   # ミラー表示の窓 (part, 残像の段数 0=本体)
    phase: float = field(default_factory=lambda: random.uniform(0, math.tau))

    @property
    def title(self) -> str:
        label = geo.PART_LABEL.get(self.part, self.part)
        if self.mirror_key is not None:
            return label if self.mirror_key[1] == 0 else f"{label} — echo {self.mirror_key[1]}"
        return f"{label} — {self.id % 1000:03d}"


class Engine:
    def __init__(self, settings: dict):
        self.s = settings
        self.wins: list[Win] = []
        self._next_id = 1
        self._acc = 0.0
        self._last_part: str | None = None
        self.running = False
        self.paused = False
        self.cap = float(settings["max_windows"])   # 適応制御後の実上限
        self.cap_reason = ""
        self._adapt_t = 0.0
        self._spawn_count = 0
        self._spawn_win_t = 0.0
        self.spawned_per_s = 0.0
        self.requested_rate = 0.0
        self.removed: list[Win] = []    # 今回のtickで消えた窓（native の後始末用）
        self.rect = (0.0, 0.0, 1920.0, 1080.0)          # 窓が動ける範囲（管理画面を除く）
        self.screen_rect = (0.0, 0.0, 1920.0, 1080.0)   # ミラー表示でカメラと 1:1 に対応させる画面
        self._mirror: dict[tuple, Win] = {}
        self._layout = settings["layout_mode"]

    # ---------- helpers ----------
    def to_screen(self, nx: float, ny: float) -> tuple[float, float]:
        x0, y0, x1, y1 = self.rect
        r = float(self.s["tracking_range"])
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        x = cx + (nx - 0.5) * (x1 - x0) * r
        y = cy + (ny - 0.5) * (y1 - y0) * r
        return min(max(x, x0), x1), min(max(y, y0), y1)

    def enabled_parts(self, snap) -> list[str]:
        s = self.s
        on = [p for t, parts in geo.TOGGLE_PARTS.items() if s[f"track_{t}"] for p in parts]
        return [p for p in on if p in snap.parts]

    def alive_count(self) -> int:
        return len(self.wins)

    def native_count(self) -> int:
        return sum(1 for w in self.wins if w.backend == "native")

    def effective_max(self) -> int:
        return max(0, int(min(self.s["max_windows"], self.cap)))

    def clear(self) -> None:
        self.removed.extend(self.wins)
        self.wins.clear()
        self._mirror.clear()
        self._acc = 0.0

    # ---------- spawning ----------
    def _pick_part(self, parts: list[str]) -> str:
        if self._last_part in parts and random.random() < float(self.s["duplicate_bias"]):
            return self._last_part
        fr = float(self.s["face_ratio"])
        others = [p for p in parts if p != "face"]
        if "face" in parts and (not others or random.random() < fr):
            return "face"
        return random.choice(others or parts)

    def _choose_backend(self) -> str:
        mode = self.s["render_mode"]
        if mode == "overlay":
            return "overlay"
        if self.native_count() < int(self.s["native_max"]):
            return "native"
        return "overlay" if mode == "hybrid" else ""

    def spawn(self, snap, now: float, part: str | None = None, origin=None,
              mode: str | None = None, direction=None) -> Win | None:
        parts = self.enabled_parts(snap)
        if not parts:
            return None
        if part is None or part not in parts:
            part = self._pick_part(parts)
        self._last_part = part
        s = self.s
        # 上限：古い窓を再利用（無限増殖しない）
        limit = self.effective_max()
        if limit <= 0:
            return None
        while len(self.wins) >= limit:
            old = next((w for w in self.wins if w.mirror_key is None), None)
            if old is None:
                return None
            self._remove(old)
        backend = self._choose_backend()
        if not backend:  # native モードで空きが無い → 最古の native を再利用
            olds = [w for w in self.wins if w.backend == "native" and w.mirror_key is None]
            if not olds:
                return None
            self._remove(olds[0])
            backend = "native"

        ps = snap.parts[part]
        px, py = self.to_screen(*ps.pos)
        spread = float(s["spawn_spread"])
        area = s["spawn_area"]
        if origin is not None:
            x, y = origin[0] + random.gauss(0, spread * 0.3), origin[1] + random.gauss(0, spread * 0.3)
        elif area == "face" and snap.face_center is not None:
            fx, fy = self.to_screen(*snap.face_center)
            x, y = fx + random.gauss(0, spread), fy + random.gauss(0, spread)
        elif area == "random":
            x0, y0, x1, y1 = self.rect
            x, y = random.uniform(x0, x1), random.uniform(y0, y1)
        elif area == "motion" and snap.motion_center is not None:
            mx, my = self.to_screen(*snap.motion_center)
            x, y = mx + random.gauss(0, spread), my + random.gauss(0, spread)
        else:
            x, y = px + random.gauss(0, spread * 0.6), py + random.gauss(0, spread * 0.6)

        aspect = geo.PART_SHAPE[part][0]
        w = float(s["size"]) * PART_SCALE.get(part, 1.0) * max(0.2, 1 + random.uniform(-1, 1) * float(s["size_jitter"]))
        h = w / aspect
        if mode is None:
            mode = s["motion_mode"]
            if mode == "mix":
                mode = random.choices(MODES, MIX_WEIGHTS)[0]
        life = float(s["life_s"]) * max(0.1, 1 + random.uniform(-1, 1) * float(s["life_jitter"]))
        win = Win(self._next_id, part, x, y, w, h, mode, now, life, backend=backend)
        self._next_id += 1
        speed = float(s["speed"])
        if direction is None:
            fvx, fvy = snap.face_vel
            base = math.atan2(fvy, fvx) if math.hypot(fvx, fvy) > 0.3 else None
            ang = random.uniform(0, math.tau) if base is None else random.gauss(base, 0.9)
        else:
            ang = random.gauss(math.atan2(direction[1], direction[0]), 0.6)
        if mode == "scatter":
            v = 380 * speed * float(s["scatter"]) * random.uniform(0.4, 1.6)
        elif mode == "drift":
            v = 70 * speed * random.uniform(0.3, 1.2)
        elif mode == "follow":
            v = 60 * speed * random.random()
        else:
            v = 0.0
        win.vx, win.vy = math.cos(ang) * v, math.sin(ang) * v
        jit = float(s["follow_lag_jitter"])
        win.follow_k = max(0.05, 1 + random.uniform(-1, 1) * jit)
        r = spread * float(s["scatter"]) * random.uniform(0.2, 1.0)
        a2 = random.uniform(0, math.tau)
        win.ox, win.oy = math.cos(a2) * r, math.sin(a2) * r
        u = random.random()
        if u < float(s["snapshot_ratio"]):
            win.image_mode = "snap"
            win.frozen = ps.image
        elif u < float(s["snapshot_ratio"]) + float(s["delay_ratio"]):
            win.image_mode = "delay"
            win.delay = random.uniform(0.15, 1.0) * float(s["delay_s"])
        win.last_image = ps.image
        win.scale = 0.3
        self.wins.append(win)
        self._spawn_count += 1
        return win

    def burst(self, snap, now: float, n: int | None = None, part: str | None = None,
              origin=None, mode: str | None = "scatter", direction=None) -> int:
        n = int(self.s["burst_count"] if n is None else n)
        made = 0
        for _ in range(min(n, 400)):
            if self.spawn(snap, now, part=part, origin=origin, mode=mode, direction=direction):
                made += 1
        return made

    def _remove(self, w: Win) -> None:
        try:
            self.wins.remove(w)
        except ValueError:
            return
        if w.mirror_key is not None and self._mirror.get(w.mirror_key) is w:
            del self._mirror[w.mirror_key]
        self.removed.append(w)

    def kill(self, win_id: int) -> None:
        """ユーザーが個別の窓を閉じた。"""
        for w in self.wins:
            if w.id == win_id:
                self._remove(w)
                return

    # ---------- per-frame update ----------
    def update(self, dt: float, now: float, snap, events=(), render_fps: float | None = None) -> None:
        s = self.s
        dt = min(dt, 0.1)
        self._adapt(now, render_fps)
        if self._spawn_win_t == 0.0:
            self._spawn_win_t = now
        if now - self._spawn_win_t >= 1.0:
            self.spawned_per_s = self._spawn_count / (now - self._spawn_win_t)
            self._spawn_count, self._spawn_win_t = 0, now
        if self.paused:
            return

        mirror = s["layout_mode"] == "mirror"
        if s["layout_mode"] != self._layout:
            self._layout = s["layout_mode"]
            if mirror:   # ミラーへ切り替えたら、飛び回っていた増殖の窓は消す
                for w in self.wins:
                    if w.mirror_key is None and w.dying_since is None:
                        w.dying_since = now
        if self.running and mirror:
            self._update_mirror(dt, now, snap)
        elif self._mirror:
            for w in list(self._mirror.values()):   # 表示モードを戻したらミラー窓は消える
                w.dying_since = w.dying_since or now
            self._mirror.clear()

        if self.running:
            rate = 0.0 if mirror else float(s["spawn_rate"])   # ミラー表示では BURST と反応だけで増殖
            if s["motion_enabled"]:
                sens = float(s["motion_sensitivity"])
                speed = math.hypot(*snap.face_vel)
                factor = 0.15 + snap.motion_energy * 25 * sens + speed * 0.6 * sens
                rate *= min(max(factor, 0.15), 4.0)
            self.requested_rate = rate
            self._acc += rate * dt
            n = min(int(self._acc), MAX_SPAWN_PER_TICK)
            self._acc -= int(self._acc)
            direction = None
            if s["motion_enabled"] and math.hypot(*snap.motion_dir) > 0.005:
                direction = snap.motion_dir
            for _ in range(n):
                self.spawn(snap, now, direction=direction)
            if mirror and not s["mirror_reactions"]:
                events = ()   # ミラー表示：窓はミラーの位置だけ（勝手に飛び回る窓を出さない）
            for kind, _t in events:
                if kind == "mouth_open" and s["mouth_burst"] and "mouth" in snap.parts:
                    mx, my = self.to_screen(*snap.parts["mouth"].pos)
                    self.burst(snap, now, max(1, int(s["burst_count"]) // 2), part="mouth",
                               origin=(mx, my), mode="scatter")
                elif kind == "head_move" and s["head_burst"]:
                    self.burst(snap, now, max(1, int(s["burst_count"]) // 3), mode="scatter",
                               direction=snap.face_vel)
        else:
            self.requested_rate = 0.0

        self._move(dt, now, snap)

    def _move(self, dt: float, now: float, snap) -> None:
        s = self.s
        x0, y0, x1, y1 = self.rect
        speed = float(s["speed"])
        rnd = float(s["randomness"])
        damp = math.exp(-float(s["damping"]) * dt)
        follow = float(s["follow_speed"])
        edge = s["edge_mode"]
        fvx, fvy = snap.face_vel
        push = float(s["face_push"]) * 120 * speed
        for w in list(self.wins):
            if w.mirror_key is not None:
                if w.dying_since is not None:
                    k = (now - w.dying_since) / DEATH_ANIM_S
                    if k >= 1:
                        self._remove(w)
                    else:
                        w.scale, w.alpha = 1 - 0.5 * k, min(w.alpha, 1 - k)
                continue
            age = now - w.born
            # 寿命・死亡アニメーション
            if w.dying_since is None and age > w.life:
                if not w.afterimage and random.random() < float(s["afterimage_ratio"]):
                    w.afterimage = True
                    w.frozen = w.last_image
                    w.image_mode = "snap"
                    w.vx *= 0.1
                    w.vy *= 0.1
                    w.mode = "fixed" if w.mode == "follow" else w.mode
                    w.life = age + float(s["afterimage_s"])
                else:
                    w.dying_since = now
            if w.part not in snap.parts and w.image_mode != "snap" and w.dying_since is None:
                w.dying_since = now   # 追跡を失ったパーツは自然に消す（最終映像のまま）
            if w.dying_since is not None:
                k = (now - w.dying_since) / DEATH_ANIM_S
                if k >= 1:
                    self._remove(w)
                    continue
                w.scale, w.alpha = 1 - 0.5 * k, 1 - k
            else:
                w.scale = min(1.0, 0.3 + 0.7 * age / SPAWN_ANIM_S)
                w.alpha = 1.0
                if w.afterimage:
                    remain = w.life - age
                    w.alpha = max(0.15, min(1.0, remain / max(float(s["afterimage_s"]), 0.1)))

            # 動き
            if w.mode == "follow" and w.part in snap.parts:
                tx, ty = self.to_screen(*snap.parts[w.part].pos)
                tx += w.ox
                ty += w.oy
                a = 1 - math.exp(-follow * w.follow_k * dt)
                w.x += (tx - w.x) * a + w.vx * dt
                w.y += (ty - w.y) * a + w.vy * dt
                w.vx *= damp
                w.vy *= damp
            elif w.mode != "fixed":
                if w.mode == "drift" or rnd > 0:
                    k = rnd * 300 * speed * dt * (1.0 if w.mode == "drift" else 0.3)
                    w.vx += random.gauss(0, 1) * k
                    w.vy += random.gauss(0, 1) * k
                if not w.afterimage:
                    w.vx += fvx * push * dt
                    w.vy += fvy * push * dt
                w.vx *= damp if w.mode == "scatter" else math.exp(-0.4 * dt)
                w.vy *= damp if w.mode == "scatter" else math.exp(-0.4 * dt)
                vmax = 2500 * speed
                v = math.hypot(w.vx, w.vy)
                if v > vmax:
                    w.vx, w.vy = w.vx / v * vmax, w.vy / v * vmax
                w.x += w.vx * dt
                w.y += w.vy * dt

            # 画面端
            hw, hh = w.w / 2, w.h / 2
            out = w.x - hw < x0 or w.x + hw > x1 or w.y - hh < y0 or w.y + hh > y1
            if out:
                if edge == "bounce":
                    if w.x - hw < x0:
                        w.x, w.vx = x0 + hw, abs(w.vx)
                    elif w.x + hw > x1:
                        w.x, w.vx = x1 - hw, -abs(w.vx)
                    if w.y - hh < y0:
                        w.y, w.vy = y0 + hh, abs(w.vy)
                    elif w.y + hh > y1:
                        w.y, w.vy = y1 - hh, -abs(w.vy)
                elif edge == "wrap":
                    if w.x + hw < x0:
                        w.x = x1 + hw
                    elif w.x - hw > x1:
                        w.x = x0 - hw
                    if w.y + hh < y0:
                        w.y = y1 + hh
                    elif w.y - hh > y1:
                        w.y = y0 - hh
                elif edge == "kill":
                    if w.dying_since is None and (w.x + hw < x0 or w.x - hw > x1 or w.y + hh < y0 or w.y - hh > y1):
                        w.dying_since = now
                elif edge == "respawn":
                    if w.x + hw < x0 or w.x - hw > x1 or w.y + hh < y0 or w.y - hh > y1:
                        cx, cy = self.to_screen(*(snap.face_center or (0.5, 0.5)))
                        w.x, w.y = cx + random.gauss(0, 60), cy + random.gauss(0, 60)
            # 回収不能にしない（どのモードでも画面から大きく離れない）
            w.x = min(max(w.x, x0 - w.w * 2), x1 + w.w * 2)
            w.y = min(max(w.y, y0 - w.h * 2), y1 + w.h * 2)

    # ---------- mirror layout ----------
    def mirror_target(self, part: str, snap) -> tuple[float, float, float, float]:
        """カメラ画像上の部位 → デスクトップ上の位置と大きさ（映像部の中心・幅・高さ）。"""
        s = self.s
        ps = snap.parts[part]
        fw, fh = snap.frame_size
        x0, y0, x1, y1 = self.screen_rect
        sw, sh = x1 - x0, y1 - y0
        fit = s["mirror_fit"]
        if fit == "stretch":
            sx, sy = sw / fw, sh / fh
        else:
            sx = sy = (max if fit == "cover" else min)(sw / fw, sh / fh)
        cx = (x0 + x1) / 2 + (ps.pos[0] - 0.5) * fw * sx
        cy = (y0 + y1) / 2 + (ps.pos[1] - 0.5) * fh * sy
        k = float(s["mirror_scale"])
        bw = ps.size * fw
        return cx, cy, bw * sx * k, bw / geo.PART_SHAPE[part][0] * sy * k

    def _update_mirror(self, dt: float, now: float, snap) -> None:
        """部位ごとに本体1枚＋残像N枚。本体はカメラの位置・大きさにそのまま置き、残像は段ごとに遅れて追う。"""
        s = self.s
        n = max(0, int(s["mirror_trails"]))
        lag = max(0.01, float(s["mirror_trail_lag"]))
        trail_op = float(s["mirror_trail_opacity"])
        parts = self.enabled_parts(snap) if snap.frame_size[0] else []
        wanted = set()
        order: list[Win] = []
        for part in sorted(parts, key=lambda p: MIRROR_Z.index(p) if p in MIRROR_Z else 99):
            tx, ty, tw, th = self.mirror_target(part, snap)
            for k in range(n, -1, -1):          # 奥から：古い残像 → 本体
                key = (part, k)
                wanted.add(key)
                w = self._mirror.get(key)
                if w is None or w.dying_since is not None:
                    w = Win(self._next_id, part, tx, ty, tw, th, "mirror", now, math.inf,
                            backend=self._choose_backend() or "overlay", mirror_key=key)
                    self._next_id += 1
                    w.scale = 1.0 if k else 0.3
                    self._mirror[key] = w
                    self.wins.append(w)
                if k == 0:
                    w.x, w.y, w.w, w.h = tx, ty, tw, th
                    w.image_mode = "live"
                    w.alpha = 1.0
                    w.scale = min(1.0, w.scale + dt / SPAWN_ANIM_S)
                else:
                    a = 1 - math.exp(-dt / (lag * k))
                    w.x += (tx - w.x) * a
                    w.y += (ty - w.y) * a
                    w.w += (tw - w.w) * a
                    w.h += (th - w.h) * a
                    w.image_mode = "delay"
                    w.delay = lag * k
                    w.alpha = trail_op * (1 - k / (n + 1)) + 0.1
                order.append(w)
        for key, w in list(self._mirror.items()):
            if key not in wanted:           # 部位を見失った / 残像数を減らした
                w.dying_since = w.dying_since or now
                del self._mirror[key]
        # 描画順：ミラー窓（奥）→ 消えかけのミラー窓 → 増殖窓（手前）
        ids = {id(w) for w in order}
        rest = [w for w in self.wins if id(w) not in ids]
        dying = [w for w in rest if w.mirror_key is not None]
        self.wins = order + dying + [w for w in rest if w.mirror_key is None]

    # ---------- adaptive load control ----------
    def _adapt(self, now: float, render_fps: float | None) -> None:
        s = self.s
        req = float(s["max_windows"])
        if not s["adaptive"] or render_fps is None:
            self.cap = req
            self.cap_reason = ""
            return
        if now - self._adapt_t < 0.5:
            self.cap = min(self.cap, req)
            return
        self._adapt_t = now
        target = float(s["target_fps"])
        n = len(self.wins)
        if render_fps < target * 0.85 and n > int(s["min_windows"]):
            self.cap = max(float(s["min_windows"]), min(self.cap, n) * 0.88)
        elif render_fps > target * 1.1 and self.cap < req:
            self.cap = min(req, self.cap + max(2.0, self.cap * 0.08))
        self.cap = min(self.cap, req)
        self.cap_reason = "描画FPS低下のため自動抑制中" if self.cap < req - 0.5 else ""
