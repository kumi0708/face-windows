"""アプリ全体の制御：カメラ/検出スレッド、シミュレーション、描画、管理画面をつなぐ。"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

import psutil
from PySide6.QtCore import QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox, QSystemTrayIcon

from . import config
from .camera import list_cameras
from .engine import Engine
from .hotkeys import HOTKEYS, GlobalHotkeys
from .panel import ControlPanel, MiniBar
from .render import NativePool, Overlay

PANEL_W = 460
TICK_MS = 15
BENCH_DIR = config.ROOT / "bench" / "results"


class Controller:
    def __init__(self, app: QApplication, args):
        self.app = app
        self.args = args
        self.settings = config.load() if config.SETTINGS_PATH.exists() else config.defaults()
        if args.preset:
            self.settings = config.apply_preset(self.settings, args.preset)
        if args.render_mode:
            self.settings["render_mode"] = args.render_mode
        for kv in args.set or []:   # --set key=value（テスト・展示用）
            k, _, v = kv.partition("=")
            if k in config.DEFAULTS:
                if isinstance(config.DEFAULTS[k], bool):
                    v = v.lower() in ("1", "true", "on", "yes")
                self.settings[k] = config.sanitize({k: v})[k]
        self.tracker = None   # remote.TrackerClient（カメラ＋検出の別プロセス）
        self.engine = Engine(self.settings)
        self.warnings: dict[str, str] = {}
        self.state = "STOPPED"
        self._proc = psutil.Process()
        self._proc.cpu_percent(None)
        self._last_tick = time.perf_counter()
        self._tick_count = 0
        self._fps_t = time.perf_counter()
        self.render_fps: float | None = None
        self.tick_ms = 0.0
        self._bench = None

        self.hotkeys = GlobalHotkeys()
        self.hotkeys.triggered.connect(self._on_hotkey)
        self.hotkeys.start()

        self.overlay = Overlay(self)
        self.native = NativePool(self._on_native_closed)
        self.panel = ControlPanel(self)
        self.panel.closeEvent = self._panel_close
        self.mini = MiniBar(self)
        self.tray = self._make_tray()
        self.refresh_cameras()
        self._place_windows()
        self.set_panel_mode(self.settings["panel_mode"], initial=True)

        self.timer = QTimer()
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(TICK_MS)
        self.ui_timer = QTimer()
        self.ui_timer.timeout.connect(self.update_ui)
        self.ui_timer.start(66)

        self.start_capture()
        if self.settings["autostart"] or args.autostart:
            QTimer.singleShot(300, self.start)
        if args.bench:
            sizes = [int(v) for v in args.bench.split(",")]
            QTimer.singleShot(2500, lambda: self.run_benchmark(sizes, quit_after=args.quit_after_bench))
        if args.shot:
            for spec in args.shot:
                sec, path = spec.split(":", 1)
                QTimer.singleShot(int(float(sec) * 1000), lambda p=path: self.screenshot(p))
        if args.quit_after:
            QTimer.singleShot(int(args.quit_after * 1000), self.quit)

    # ---------- screens / placement ----------
    def screens(self):
        return self.app.screens()

    def target_screen(self):
        scr = self.screens()
        i = int(self.settings["target_screen"])
        return scr[i] if 0 <= i < len(scr) else self.app.primaryScreen()

    def _place_windows(self):
        scr = self.target_screen()
        av = scr.availableGeometry()
        w = min(PANEL_W, av.width() // 2)
        self.panel.setGeometry(av.right() - w - 8, av.top() + 32, w, av.height() - 40)
        self.mini.adjustSize()
        mw = max(self.mini.sizeHint().width(), 380)
        self.mini.setGeometry(av.right() - mw - 8, av.top() + 32, mw, self.mini.sizeHint().height())
        self.overlay.place_on(scr)

    def control_window(self):
        """今表示している操作用ウィンドウ（フル / ミニ）。非表示なら None。"""
        mode = self.settings["panel_mode"]
        w = self.panel if mode == "full" else self.mini if mode == "mini" else None
        return w if w is not None and w.isVisible() else None

    def _apply_panel_on_top(self):
        on = bool(self.settings["panel_on_top"])
        for w in (self.panel, self.mini):
            if bool(w.windowFlags() & Qt.WindowStaysOnTopHint) != on:
                visible = w.isVisible()
                w.setWindowFlag(Qt.WindowStaysOnTopHint, on)   # フラグ変更で隠れるので表示中なら出し直す
                if visible:
                    w.show()
        cw = self.control_window()
        if on and cw is not None:
            cw.raise_()

    # ---------- 管理画面の表示モード（フル / ミニ / 非表示） ----------
    def can_hide_panel(self) -> bool:
        """非表示にしても戻す手段（トレイアイコン / グローバルホットキー）があるか。"""
        return self.tray is not None or self.hotkeys.label("panel") is not None

    def set_panel_mode(self, mode: str, initial: bool = False):
        if mode == "hidden" and not self.can_hide_panel():
            mode = "mini"
            self.warnings["panel"] = "トレイアイコンもホットキーも使えないため、非表示の代わりにミニ表示にしました"
        s = self.settings
        if s["panel_mode"] != mode:
            s["panel_mode"] = mode
            self.panel.b.refresh_all()
        if mode == "full":
            self.mini.hide()
            self.panel.show()
            self.panel.activateWindow()
        elif mode == "mini":
            self.panel.hide()
            self.mini.show()
        else:
            self.panel.hide()
            self.mini.hide()
            if self.tray is not None and not initial:
                key = self.hotkeys.label("panel")
                how = "トレイのアイコン" + (f"、または {key} " if key else "")
                self.tray.showMessage("FACE WINDOWS", f"管理画面を隠しました。{how}で戻せます。",
                                      QSystemTrayIcon.Information, 3000)
        self._apply_panel_on_top()
        self._update_tray_menu()

    def toggle_panel(self):
        self.set_panel_mode("hidden" if self.settings["panel_mode"] == "full" else "full")

    def _make_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        pm = QPixmap(64, 64)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#16181d"))
        p.drawRoundedRect(QRectF(2, 2, 60, 60), 12, 12)
        for (x, y, w, h), c in (((10, 12, 30, 24), "#8ab4f8"), ((24, 28, 30, 24), "#f3f3f3")):
            p.setBrush(QColor(c))
            p.drawRect(x, y, w, h)
            p.setBrush(QColor("#3a3f4b"))
            p.drawRect(x, y, w, 5)
        p.end()
        tray = QSystemTrayIcon(QIcon(pm))
        tray.setToolTip("FACE WINDOWS")
        menu = QMenu()
        self._tray_actions = {}
        items = (("full", "管理画面を表示", lambda: self.set_panel_mode("full")),
                 ("mini", "ミニ表示", lambda: self.set_panel_mode("mini")),
                 ("hidden", "管理画面を隠す", lambda: self.set_panel_mode("hidden")),
                 (None, None, None),
                 ("start", "▶ START", self.start), ("pause", "❚❚ PAUSE", self.toggle_pause),
                 ("stop", "■ STOP", self.stop), (None, None, None),
                 ("quit", "終了", self.quit))
        for key, text, fn in items:
            if key is None:
                menu.addSeparator()
                continue
            act = QAction(text, menu)
            act.triggered.connect(fn)
            menu.addAction(act)
            self._tray_actions[key] = act
        tray.setContextMenu(menu)
        self._tray_menu = menu
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        return tray

    def _on_tray_activated(self, reason):
        # Windows はアイコンのクリックで表示/非表示。macOS はクリックでメニューが開くのでメニューから操作
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) and sys.platform != "darwin":
            self.toggle_panel()

    def _update_tray_menu(self):
        if self.tray is None:
            return
        mode = self.settings["panel_mode"]
        for key in ("full", "mini", "hidden"):
            self._tray_actions[key].setEnabled(key != mode)
        self.tray.setToolTip(f"FACE WINDOWS — {self.state}")

    def art_rect(self):
        """作品の描画範囲（管理ウィンドウを避ける）。"""
        av = self.target_screen().availableGeometry()
        x0, y0, x1, y1 = av.left(), av.top(), av.right(), av.bottom()
        if self.settings["avoid_panel"] and self.control_window() is self.panel:   # ミニ/非表示なら画面全体
            pg = self.panel.frameGeometry()
            if pg.intersects(av):
                if pg.center().x() > av.center().x():
                    x1 = min(x1, pg.left() - 4)
                else:
                    x0 = max(x0, pg.right() + 4)
        if x1 - x0 < 200:
            x0, x1 = av.left(), av.right()
        return float(x0), float(y0), float(x1), float(y1)

    # ---------- capture ----------
    def start_capture(self):
        if self.tracker is not None:
            return
        from .remote import TrackerClient
        self.tracker = TrackerClient(self.settings, source=self.args.source)

    def stop_capture(self):
        if self.tracker is not None:
            self.tracker.stop()
        self.tracker = None

    def restart_camera(self):
        self.stop_capture()
        self.start_capture()

    def refresh_cameras(self):
        cams = list_cameras() or [(0, "Camera 0")]
        self.panel.set_cameras(cams)
        self.panel.cam_info.setText("検出されたカメラ: " + ", ".join(f"{i}:{n}" for i, n in cams))

    # ---------- actions ----------
    def start(self):
        self.start_capture()
        self.engine.running = True
        self.engine.paused = False
        self.overlay.place_on(self.target_screen())
        self.overlay.show()
        self.state = "RUNNING"
        self._apply_panel_on_top()

    def toggle_pause(self):
        if self.state == "STOPPED":
            return
        self.engine.paused = not self.engine.paused
        self.state = "PAUSED" if self.engine.paused else "RUNNING"

    def stop(self):
        """緊急停止：生成停止 → 窓を回収 → カメラ解放。"""
        self.engine.running = False
        self.engine.paused = False
        self.engine.clear()
        self._flush_removed()
        self.native.release_all()
        self.overlay.hide()
        self.stop_capture()
        self.state = "STOPPED"
        self.panel.preview.clear()
        self.panel.preview.setText("停止中（カメラ解放済み）— START で再開")
        self.panel.track_lb.setText("<span style='color:#888'>検出停止中</span>")
        if self._bench:
            self._bench = None

    def reset(self):
        self.engine.clear()
        self._flush_removed()
        self.engine.cap = float(self.settings["max_windows"])

    def burst(self):
        if self.tracker is None or not self.engine.running:
            self.warnings["burst"] = "BURST は START 中のみ有効です"
            return
        self.warnings.pop("burst", None)
        self.engine.burst(self.tracker.snapshot(), time.perf_counter())

    def quit(self):
        try:
            self.stop()
        finally:
            if self.tray is not None:
                self.tray.hide()
            self.mini.hide()
            self.hotkeys.stop()
            self.native.destroy()
            self.overlay.close()
            self.app.quit()

    def _panel_close(self, e):
        e.accept()
        QTimer.singleShot(0, self.quit)

    def _on_hotkey(self, name):
        if name == "stop":
            self.stop()
        elif name == "quit":
            self.quit()
        elif name == "pause":
            self.toggle_pause()
        elif name == "panel":
            self.toggle_panel()

    def hotkey_text(self):
        if sys.platform != "win32":
            return ("グローバルな緊急停止キーは Windows のみ対応。管理画面の STOP ボタン、または管理画面上で "
                    "Esc=STOP, F5=START, B=BURST, ⌘Q/Ctrl+Q=終了。")
        ok = ", ".join(self.hotkeys.registered) or "なし"
        msg = (f"緊急停止: {HOTKEYS[1][3]}（STOP） / {HOTKEYS[2][3]}（終了） / {HOTKEYS[3][3]}（PAUSE）"
               f" / {self.hotkeys.label('panel') or HOTKEYS[4][3]}（管理画面の表示/非表示）"
               f" — どのアプリが前面でも有効。管理画面上では Esc=STOP, F5=START, B=BURST, Ctrl+Q=終了。")
        if self.hotkeys.failed:
            msg += f"  ※登録失敗: {', '.join(self.hotkeys.failed)}（他アプリが使用中）。管理画面のSTOP / Escを使用してください。"
        return msg + f"  [登録済み: {ok}]"

    def _on_native_closed(self, win_id):
        self.engine.kill(win_id)

    # ---------- settings ----------
    def on_setting_changed(self, key):
        s = self.settings
        if key == "render_mode":   # カメラ設定は検出プロセスへ自動で送られ、そこで再接続する
            self.panel.refresh_render_label()
            if s["render_mode"] == "overlay":
                for w in self.engine.wins:
                    w.backend = "overlay"
            elif s["render_mode"] == "native":
                for w in self.engine.wins:
                    if w.backend == "overlay":
                        w.dying_since = w.dying_since or time.perf_counter()
        elif key == "native_max":
            extra = self.engine.native_count() - int(s["native_max"])
            for w in [w for w in self.engine.wins if w.backend == "native"][:max(0, extra)]:
                w.backend = "overlay" if s["render_mode"] == "hybrid" else w.backend
                if s["render_mode"] == "native":
                    self.engine.kill(w.id)
        elif key == "target_screen":
            self._place_windows()
        elif key == "panel_on_top":
            self._apply_panel_on_top()
        elif key == "panel_mode":   # コンボボックスの操作中に自分自身を隠さないよう、次のループで切り替える
            QTimer.singleShot(0, lambda: self.set_panel_mode(s["panel_mode"]))
        elif key == "max_windows":
            self.engine.cap = max(self.engine.cap, float(s["min_windows"]))

    def _replace_settings(self, new: dict):
        screen_changed = self.settings["target_screen"] != new["target_screen"]
        self.settings.clear()
        self.settings.update(new)   # 同じ dict を共有しているので参照先はそのまま
        self.panel.b.refresh_all()
        self.panel.refresh_render_label()
        self.set_panel_mode(self.settings["panel_mode"])
        if screen_changed:
            self._place_windows()

    def save_settings(self):
        config.save(self.settings)
        self.panel.sys_lb.setText(f"保存しました: {config.SETTINGS_PATH}")

    def load_settings(self):
        path, _ = QFileDialog.getOpenFileName(self.panel, "設定を読み込み", str(config.CONFIG_DIR), "JSON (*.json)")
        if path:
            self._replace_settings(config.load(Path(path)))
            self.panel.sys_lb.setText(f"読み込みました: {path}")

    def reset_settings(self):
        if QMessageBox.question(self.panel, "初期化", "すべての設定を初期値に戻しますか？") == QMessageBox.Yes:
            self._replace_settings(config.defaults())

    def apply_preset(self, name):
        if name:
            self._replace_settings(config.apply_preset(self.settings, name))
            self.panel.sys_lb.setText(f"プリセット「{name}」を適用しました")

    def save_preset(self, name):
        p = config.save_preset(name, dict(self.settings))
        self.panel.sys_lb.setText(f"プリセットを保存しました: {p}")

    # ---------- main loop ----------
    def _flush_removed(self):
        for w in self.engine.removed:
            self.overlay.forget(w.id)
            if w.backend == "native":
                self.native.release(w.id)
        self.engine.removed.clear()

    def tick(self):
        t0 = time.perf_counter()
        dt = t0 - self._last_tick
        self._last_tick = t0
        try:
            self.engine.rect = self.art_rect()
            sg = self.target_screen().geometry()
            self.engine.screen_rect = (float(sg.left()), float(sg.top()),
                                       float(sg.left() + sg.width()), float(sg.top() + sg.height()))
            snap = self.tracker.snapshot() if self.tracker else None
            if snap is not None:
                events = []
                while self.tracker.events:
                    events.append(self.tracker.events.popleft())
                self.engine.update(dt, t0, snap, events, self.render_fps)
            self._flush_removed()
            cw = self.control_window()
            if self.overlay.isVisible():
                self.overlay.exclude = cw.frameGeometry() if cw is not None else None
                self.overlay.repaint()
            if self.settings["render_mode"] != "overlay" or self.native.used:
                below = int(cw.winId()) if cw is not None and self.settings["panel_on_top"] else None
                restacked = self.native.sync(self.engine.wins, snap, self.tracker, self.target_screen(),
                                             float(self.settings["opacity"]), below)
                if restacked and cw is not None and self.settings["panel_on_top"]:
                    cw.raise_()   # Windows 以外：実ウィンドウの上に管理ウィンドウを戻す
            self.warnings.pop("tick", None)
        except Exception as e:  # 描画ループは止めない
            self.warnings["tick"] = f"描画ループエラー: {e!r}"
            traceback.print_exc()
        self.tick_ms = (time.perf_counter() - t0) * 1000
        self._tick_count += 1
        now = time.perf_counter()
        if now - self._fps_t >= 0.5:
            fps = self._tick_count / (now - self._fps_t)
            self.render_fps = fps if self.state == "RUNNING" else None
            self._last_fps = fps
            self._tick_count, self._fps_t = 0, now
        if self._bench:
            self._bench_step(now)

    # ---------- UI refresh ----------
    def update_ui(self):
        p = self.panel
        s = self.settings
        tr = cam = self.tracker
        if tr is not None and tr.camera_error:
            self.warnings["camera"] = tr.camera_error + "（カメラタブで選択/再接続できます）"
        else:
            self.warnings.pop("camera", None)
        if tr is not None and tr.error:
            self.warnings["tracker"] = tr.error
        else:
            self.warnings.pop("tracker", None)
        snap = tr.snapshot() if tr else None
        if snap is not None and snap.preview is not None:
            if p.isVisible():   # 管理画面を隠している間はプレビューの更新を省く
                p.set_preview(snap.preview)
            enabled = [t for t in config.PART_TOGGLES if s[f"track_{t}"]]
            p.set_tracking(snap.status, enabled)
            if not enabled:
                self.warnings["parts"] = "検出対象がすべてOFFのため新規生成しません"
            else:
                self.warnings.pop("parts", None)
            if snap.face_center is None and not tr.camera_error and tr.measured_fps > 0:
                self.warnings["face"] = "顔が検出できません（カメラに顔を映してください / 明るさ・しきい値を確認）"
            else:
                self.warnings.pop("face", None)
        if self.engine.cap_reason:
            self.warnings["adapt"] = self.engine.cap_reason
        else:
            self.warnings.pop("adapt", None)

        state_col = {"RUNNING": "#4caf50", "PAUSED": "#ffb74d", "STOPPED": "#ef5350"}[self.state]
        p.state_lb.setText(f"<b style='color:{state_col}'>{self.state}</b>")
        e = self.engine
        n, nn = e.alive_count(), e.native_count()
        mem = self._proc.memory_info().rss / 2**20
        mem_child = 0.0
        if tr is not None and tr.proc.pid:
            try:
                mem_child = psutil.Process(tr.proc.pid).memory_info().rss / 2**20
            except psutil.Error:
                pass
        cpu = self._proc.cpu_percent(None) / max(1, psutil.cpu_count())
        rfps = getattr(self, "_last_fps", 0.0)
        cam_fps = f"{cam.measured_fps:.1f}" if cam and cam.measured_fps else "未計測"
        inf_fps = f"{tr.infer_fps:.1f}" if tr and tr.infer_fps else "未計測"
        lat = self.overlay.last_latency
        lat_s = f"{lat * 1000:.0f} ms" if lat is not None and self.state == "RUNNING" else "未計測"
        capped = e.effective_max() < int(s["max_windows"])
        p.stats_lb.setText(
            f"窓 <b>{n}</b> / 要求 {s['max_windows']}"
            + (f" <span style='color:#ffb74d'>（実上限 {e.effective_max()}）</span>" if capped else "")
            + f"　実ウィンドウ {nn} / 疑似 {n - nn}<br>"
            f"入力 {cam_fps} fps　推論 {inf_fps} fps　描画 {rfps:.1f} fps　"
            f"生成 {e.spawned_per_s:.0f}/s（要求 {e.requested_rate:.0f}/s）")
        det = f"{tr.infer_ms:.1f} ms（顔） + {tr.extra_ms:.1f} ms（Body/Hands）" if tr else "未計測"
        p.perf_lb.setText(
            f"稼働中の窓: {n}（OS実ウィンドウ {nn} / 疑似 {n - nn}）<br>"
            f"最大数 要求値 {s['max_windows']} → 実際の上限 {e.effective_max()}<br>"
            f"生成レート 要求 {e.requested_rate:.1f}/s → 実際 {e.spawned_per_s:.1f}/s<br>"
            f"入力FPS {cam_fps} / 推論FPS {inf_fps} / 描画FPS {rfps:.1f}<br>"
            f"検出時間 {det}<br>"
            f"1フレーム処理 {self.tick_ms:.1f} ms（オーバーレイ描画 {self.overlay.paint_ms:.1f} ms / 実ウィンドウ更新 {self.native.paint_ms:.1f} ms）<br>"
            f"カメラ取得→表示の遅延 {lat_s}（カメラ内部の露光・転送時間は含まない）<br>"
            f"メモリ {mem:.0f} MB（描画） + {mem_child:.0f} MB（検出プロセス） / CPU {cpu:.0f}%（描画プロセス、全コア比） / GPU 未計測")
        p.warn_lb.setText("\n".join(f"⚠ {w}" for w in self.warnings.values()))
        if self.mini.isVisible():
            self.mini.update_stats(f"<b style='color:{state_col}'>{self.state}</b>",
                                   f"窓 {n}　入力 {cam_fps}　推論 {inf_fps}　描画 {rfps:.0f} fps",
                                   list(self.warnings.values()))
        if self.tray is not None:
            self.tray.setToolTip(f"FACE WINDOWS — {self.state}（窓 {n}）")

    # ---------- benchmark ----------
    def run_benchmark(self, sizes=None, quit_after=False):
        if self._bench:
            return
        sizes = sizes or [20, 100, 200]
        self._bench = {"sizes": list(sizes), "i": -1, "saved": dict(self.settings), "results": [],
                       "quit": quit_after, "phase_t": 0.0}
        self.start()
        self.panel.bench_lb.setText("ベンチマーク実行中…（顔をカメラに映してください）")
        self._bench_next(time.perf_counter())

    def _bench_next(self, now):
        b = self._bench
        b["i"] += 1
        if b["i"] >= len(b["sizes"]):
            self._bench_finish()
            return
        n = b["sizes"][b["i"]]
        s = self.settings
        s.update({"max_windows": n, "spawn_rate": 300.0, "life_s": 60.0, "life_jitter": 0.0,
                  "adaptive": False, "afterimage_ratio": 0.0, "motion_enabled": False,
                  "motion_mode": "mix"})
        self.engine.clear()
        self._flush_removed()
        b.update(phase_t=now, samples=[], frames=0, meas_t=None)

    def _bench_step(self, now):
        b = self._bench
        el = now - b["phase_t"]
        if el < 3.0:
            return
        if b["meas_t"] is None:
            b["meas_t"] = now
            b["frames"] = 0
            self._proc.cpu_percent(None)
        b["frames"] += 1
        b["samples"].append((self.tick_ms, self.overlay.paint_ms, self.native.paint_ms,
                             self.overlay.last_latency or 0))
        if el >= 6.0:
            dur = now - b["meas_t"]
            sm = b["samples"]
            avg = lambda i: sum(x[i] for x in sm) / max(1, len(sm))
            res = {
                "requested": b["sizes"][b["i"]],
                "actual_windows": self.engine.alive_count(),
                "native_windows": self.engine.native_count(),
                "render_mode": self.settings["render_mode"],
                "render_fps": round(b["frames"] / dur, 1),
                "tick_ms_avg": round(avg(0), 2),
                "tick_ms_max": round(max(x[0] for x in sm), 2),
                "overlay_paint_ms": round(avg(1), 2),
                "native_update_ms": round(avg(2), 2),
                "latency_ms_avg": round(avg(3) * 1000, 1),
                "camera_fps": round(self.tracker.measured_fps, 1) if self.tracker else None,
                "infer_fps": round(self.tracker.infer_fps, 1) if self.tracker else None,
                "mem_mb": round(self._proc.memory_info().rss / 2**20),
                "cpu_pct_all_cores": round(self._proc.cpu_percent(None) / psutil.cpu_count(), 1),
                "face_detected": bool(self.tracker and self.tracker.snapshot().face_center),
            }
            b["results"].append(res)
            self.panel.bench_lb.setText("\n".join(json.dumps(r, ensure_ascii=False) for r in b["results"]))
            self._bench_next(now)

    def _bench_finish(self):
        b = self._bench
        self._bench = None
        self._replace_settings(b["saved"])
        self.engine.clear()
        self._flush_removed()
        scr = self.target_screen()
        meta = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "screen": scr.name(),
                "screen_logical": [scr.geometry().width(), scr.geometry().height()],
                "dpr": scr.devicePixelRatio(), "results": b["results"]}
        BENCH_DIR.mkdir(parents=True, exist_ok=True)
        path = BENCH_DIR / f"bench_{time.strftime('%Y%m%d_%H%M%S')}_{b['results'][0]['render_mode'] if b['results'] else 'x'}.json"
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(meta, ensure_ascii=False), flush=True)
        self.panel.bench_lb.setText(self.panel.bench_lb.text() + f"\n保存: {path}")
        if b["quit"]:
            QTimer.singleShot(200, self.quit)

    def screenshot(self, path):
        scr = self.target_screen()
        scr.grabWindow(0).save(path)
        print(f"screenshot saved: {path}", flush=True)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="FACE WINDOWS")
    ap.add_argument("--autostart", action="store_true", help="起動直後に START")
    ap.add_argument("--render-mode", choices=config.CHOICES["render_mode"])
    ap.add_argument("--set", action="append", metavar="KEY=VALUE", help="設定を上書き（複数可）")
    ap.add_argument("--preset", choices=config.list_presets(), help="起動時にプリセットを適用")
    ap.add_argument("--source", help="カメラの代わりに画像/動画ファイルを入力にする（テスト・デモ用）")
    ap.add_argument("--bench", help="ベンチマークを実行（例: 20,100,200）")
    ap.add_argument("--quit-after-bench", action="store_true")
    ap.add_argument("--quit-after", type=float, help="指定秒数後に終了（テスト用）")
    ap.add_argument("--shot", action="append", help="秒:保存先.png でスクリーンショット（テスト用）")
    args = ap.parse_args(argv)

    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication(sys.argv[:1])
    app.setApplicationName("FACE WINDOWS")
    app.setQuitOnLastWindowClosed(False)
    ctl = Controller(app, args)

    def excepthook(t, v, tb):  # 予期しない例外でも窓を残さない・落ちない
        traceback.print_exception(t, v, tb)
        ctl.warnings["exception"] = f"{t.__name__}: {v}"
    sys.excepthook = excepthook
    code = app.exec()
    ctl.stop_capture()
    return code
