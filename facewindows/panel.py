"""管理ウィンドウ（CONTROL PANEL）。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                               QPushButton, QScrollArea, QSlider, QSizePolicy, QTabWidget, QVBoxLayout,
                               QWidget)

from . import config
from . import geometry as geo

CHOICE_LABELS = {
    "layout_mode": {"swarm": "増殖（顔から窓が生まれて動き回る）",
                    "mirror": "ミラー（カメラ＝デスクトップ 1:1、部位の位置と大きさに窓を置く）"},
    "mirror_style": {"stamp": "ずれたら新しい窓を生成（窓は動かない）", "track": "窓が部位を追いかける"},
    "mirror_fit": {"cover": "比率を保って画面を埋める", "contain": "比率を保って画面に収める",
                   "stretch": "画面いっぱいに引き伸ばす"},
    "crop_size": {160: "160px（軽い）", 240: "240px", 360: "360px", 480: "480px（高精細・重い）"},
    "spawn_area": {"face": "顔中心の周囲", "part": "検出部位の付近", "random": "画面内ランダム", "motion": "動きの方向・位置"},
    "motion_mode": {"follow": "追従", "scatter": "飛散", "drift": "ランダム漂流", "fixed": "位置固定", "mix": "混合"},
    "edge_mode": {"bounce": "跳ね返り", "wrap": "反対側へ", "kill": "消滅", "respawn": "顔の近くへ再配置"},
    "render_mode": {"overlay": "疑似ウィンドウ（透明オーバーレイ描画・高速）",
                    "native": "OS実ウィンドウ（少数・低速）",
                    "hybrid": "混合（実ウィンドウ＋疑似）"},
    "window_style": {"win11_light": "タイトルバー風（ライト）", "win11_dark": "タイトルバー風（ダーク）", "macos": "macOS風",
                     "retro": "レトロ（Win95風）", "frameless": "フレームレス"},
}
RENDER_DESC = {
    "overlay": "描画方式：疑似ウィンドウ（1枚の透明オーバーレイ上にタイトルバー付きの窓を描画。OSのウィンドウではありません）",
    "native": "描画方式：OSの実ウィンドウ（1窓＝1ウィンドウ。数は「実ウィンドウ上限」まで）",
    "hybrid": "描画方式：混合（上限まではOSの実ウィンドウ、超えた分は疑似ウィンドウ）",
}

STYLE = """
QWidget { background: #16181d; color: #e6e6e6; font-family: 'Segoe UI', 'Yu Gothic UI'; font-size: 9pt; }
QTabWidget::pane { border: 1px solid #2c313a; }
QTabBar::tab { background: #22262e; padding: 5px 8px; border: 1px solid #2c313a; }
QTabBar::tab:selected { background: #2f3542; color: #ffffff; }
QPushButton { background: #2a2f39; border: 1px solid #3b4250; border-radius: 4px; padding: 6px 8px; }
QPushButton:hover { background: #343b48; }
QPushButton#start { background: #1f6f3f; font-weight: bold; }
QPushButton#stop { background: #b3261e; font-weight: bold; font-size: 11pt; }
QPushButton#pause { background: #7a5c10; font-weight: bold; }
QSlider::groove:horizontal { height: 4px; background: #333a46; }
QSlider::handle:horizontal { background: #8ab4f8; width: 12px; margin: -5px 0; border-radius: 6px; }
QComboBox { background: #22262e; border: 1px solid #3b4250; padding: 3px; }
QLabel#title { font-size: 14pt; font-weight: bold; letter-spacing: 2px; }
QLabel#warn { color: #ffb74d; }
QLabel#section { color: #8ab4f8; font-weight: bold; margin-top: 6px; }
QScrollArea { border: none; }
"""


class Binder:
    """設定キー ↔ ウィジェット。どこで変えても即 settings に反映し、他の同じキーの表示も揃える。"""

    def __init__(self, settings: dict, on_change):
        self.s = settings
        self.on_change = on_change
        self._refresh: dict[str, list] = {}

    def _reg(self, key, fn):
        self._refresh.setdefault(key, []).append(fn)

    def _set(self, key, value):
        if self.s.get(key) == value:
            return
        self.s[key] = value
        for fn in self._refresh.get(key, []):
            fn()
        self.on_change(key)

    def refresh_all(self):
        for fns in self._refresh.values():
            for fn in fns:
                fn()

    def slider(self, key, label, lo, hi, step=1.0, fmt="{:.0f}", suffix=""):
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        name = QLabel(label)
        name.setFixedWidth(150)
        name.setWordWrap(True)
        sl = QSlider(Qt.Horizontal)
        sl.setMinimumWidth(60)
        n = int(round((hi - lo) / step))
        sl.setRange(0, n)
        val = QLabel()
        val.setMinimumWidth(52)
        val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        is_int = isinstance(config.DEFAULTS[key], int) and not isinstance(config.DEFAULTS[key], bool)

        def refresh():
            v = self.s[key]
            sl.blockSignals(True)
            sl.setValue(int(round((v - lo) / step)))
            sl.blockSignals(False)
            val.setText(fmt.format(v) + suffix)

        def changed(i):
            v = lo + i * step
            self._set(key, int(round(v)) if is_int else round(v, 4))
            val.setText(fmt.format(self.s[key]) + suffix)

        sl.valueChanged.connect(changed)
        self._reg(key, refresh)
        refresh()
        lay.addWidget(name)
        lay.addWidget(sl, 1)
        lay.addWidget(val)
        return row

    def check(self, key, label):
        cb = QCheckBox(label)

        def refresh():
            cb.blockSignals(True)
            cb.setChecked(bool(self.s[key]))
            cb.blockSignals(False)

        cb.toggled.connect(lambda v: self._set(key, bool(v)))
        self._reg(key, refresh)
        refresh()
        return cb

    def combo(self, key, label, options=None, labels=None):
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        name = QLabel(label)
        name.setFixedWidth(150)
        name.setWordWrap(True)
        cb = QComboBox()
        cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        cb.setMinimumContentsLength(8)
        cb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        options = list(options if options is not None else config.CHOICES[key])
        labels = labels or CHOICE_LABELS.get(key, {})
        for o in options:
            cb.addItem(str(labels.get(o, o)) if isinstance(labels, dict) else str(o), o)

        def refresh():
            cb.blockSignals(True)
            i = cb.findData(self.s[key])
            if i >= 0:
                cb.setCurrentIndex(i)
            cb.blockSignals(False)

        cb.currentIndexChanged.connect(lambda i: self._set(key, cb.itemData(i)))
        self._reg(key, refresh)
        refresh()
        lay.addWidget(name)
        lay.addWidget(cb, 1)
        row.combo = cb
        return row


def section(text):
    lb = QLabel(text)
    lb.setObjectName("section")
    return lb


def page(*widgets):
    inner = QWidget()
    lay = QVBoxLayout(inner)
    lay.setSpacing(4)
    for w in widgets:
        lay.addWidget(w)
    lay.addStretch(1)
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setWidget(inner)
    return sa


class ControlPanel(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.setWindowTitle("FACE WINDOWS — CONTROL PANEL")
        self.setStyleSheet(STYLE)
        self.b = b = Binder(ctl.settings, ctl.on_setting_changed)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(5)

        head = QHBoxLayout()
        t = QLabel("FACE WINDOWS")
        t.setObjectName("title")
        self.state_lb = QLabel("STOPPED")
        self.state_lb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        head.addWidget(t)
        head.addWidget(self.state_lb, 1)
        root.addLayout(head)
        self.render_lb = QLabel()
        self.render_lb.setWordWrap(True)
        self.render_lb.setStyleSheet("color:#9aa4b2; font-size:8pt;")
        root.addWidget(self.render_lb)

        self.preview = QLabel("カメラ停止中")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(200)
        self.preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.preview.setStyleSheet("background:#000; border:1px solid #2c313a;")
        root.addWidget(self.preview, 3)
        self.track_lb = QLabel()
        self.track_lb.setTextFormat(Qt.RichText)
        self.track_lb.setWordWrap(True)
        root.addWidget(self.track_lb)

        # 常に見える操作ボタン
        btns = QGridLayout()
        mk = lambda text, name, fn: self._btn(text, name, fn)
        btns.addWidget(mk("▶ START", "start", ctl.start), 0, 0)
        btns.addWidget(mk("❚❚ PAUSE", "pause", ctl.toggle_pause), 0, 1)
        btns.addWidget(mk("■ STOP", "stop", ctl.stop), 0, 2, 2, 1)
        btns.addWidget(mk("↺ RESET", "", ctl.reset), 1, 0)
        btns.addWidget(mk("✸ BURST", "", ctl.burst), 1, 1)
        root.addLayout(btns)

        # よく使う設定（タブを開かずに操作）
        quick = QWidget()
        ql = QVBoxLayout(quick)
        ql.setContentsMargins(0, 0, 0, 0)
        ql.setSpacing(2)
        parts_row = QGridLayout()
        for i, (k, label) in enumerate(config.PART_TOGGLES.items()):
            parts_row.addWidget(b.check(f"track_{k}", label), i // 4, i % 4)
        ql.addLayout(parts_row)
        ql.addWidget(b.combo("layout_mode", "表示モード"))
        ql.addWidget(b.slider("max_windows", "最大同時表示数", 0, 600, 1))
        ql.addWidget(b.slider("spawn_rate", "生成レート", 0, 300, 1, "{:.0f}", " 個/秒"))
        ql.addWidget(b.slider("speed", "移動速度", 0, 4, 0.05, "{:.2f}"))
        ql.addWidget(b.combo("motion_mode", "動きモード"))
        root.addWidget(quick)

        self.stats_lb = QLabel()
        self.stats_lb.setTextFormat(Qt.RichText)
        self.stats_lb.setWordWrap(True)
        root.addWidget(self.stats_lb)
        self.warn_lb = QLabel()
        self.warn_lb.setObjectName("warn")
        self.warn_lb.setWordWrap(True)
        root.addWidget(self.warn_lb)

        tabs = QTabWidget()
        tabs.setUsesScrollButtons(True)
        tabs.tabBar().setExpanding(False)
        tabs.addTab(self._tab_camera(), "CAMERA")
        tabs.addTab(self._tab_tracking(), "TRACKING")
        tabs.addTab(self._tab_generation(), "GENERATION")
        tabs.addTab(self._tab_motion(), "MOTION")
        tabs.addTab(self._tab_display(), "DISPLAY")
        tabs.addTab(self._tab_perf(), "PERF")
        tabs.addTab(self._tab_system(), "PRESETS/SYSTEM")
        root.addWidget(tabs, 4)

        hk = QLabel(ctl.hotkey_text())
        hk.setWordWrap(True)
        hk.setStyleSheet("color:#9aa4b2; font-size:8pt;")
        self.hotkey_lb = hk
        root.addWidget(hk)

        QShortcut(QKeySequence(Qt.Key_Escape), self, ctl.stop)
        QShortcut(QKeySequence("Ctrl+Q"), self, ctl.quit)
        QShortcut(QKeySequence(Qt.Key_F5), self, ctl.start)
        QShortcut(QKeySequence(Qt.Key_B), self, ctl.burst)
        self.refresh_render_label()

    def _btn(self, text, name, fn):
        bt = QPushButton(text)
        if name:
            bt.setObjectName(name)
        bt.setMinimumHeight(34)
        bt.clicked.connect(fn)
        bt.setFocusPolicy(Qt.NoFocus)
        return bt

    # ---------- tabs ----------
    def _tab_camera(self):
        b = self.b
        self.cam_combo = b.combo("camera_index", "カメラ", options=[0], labels={0: "Camera 0"})
        refresh = QPushButton("カメラ一覧を更新")
        refresh.clicked.connect(self.ctl.refresh_cameras)
        restart = QPushButton("カメラを再接続")
        restart.clicked.connect(self.ctl.restart_camera)
        self.cam_info = QLabel()
        self.cam_info.setWordWrap(True)
        return page(section("CAMERA"), self.cam_combo, refresh,
                    b.combo("camera_res", "解像度"), b.combo("camera_fps", "入力FPS"),
                    b.check("mirror", "左右反転（鏡のように表示）"), restart, self.cam_info)

    def set_cameras(self, cams):
        cb = self.cam_combo.combo
        cb.blockSignals(True)
        cb.clear()
        for i, name in cams:
            cb.addItem(f"{i}: {name}", i)
        idx = cb.findData(self.ctl.settings["camera_index"])
        if idx >= 0:
            cb.setCurrentIndex(idx)
        cb.blockSignals(False)

    def _tab_tracking(self):
        b = self.b
        parts = [b.check(f"track_{k}", f"{label}") for k, label in config.PART_TOGGLES.items()]
        return page(
            section("TRACKING / Face & Body（検出対象。すべてOFFで新規生成なし）"), *parts,
            QLabel("Body は人物切り抜き（背景透過）、Arms/Hands は追加検出。重い場合は下の間引きを増やす。"),
            section("しきい値"),
            b.slider("det_confidence", "検出信頼度しきい値", 0.1, 0.95, 0.05, "{:.2f}"),
            b.check("stabilize", "ブレ補正（止まっている時の揺れを抑え、動くと素早く追従）"),
            b.slider("stabilize_cutoff", "静止時のブレ補正（小さいほど強い）", 0.1, 6, 0.1, "{:.1f}", " Hz"),
            b.slider("stabilize_beta", "動いた時の追従性", 0, 20, 0.5, "{:.1f}"),
            b.slider("box_smoothing", "単純平滑化（ブレ補正OFF時）", 0, 0.9, 0.05, "{:.2f}"),
            b.check("rotate_crops", "顔の傾きに合わせて切り抜きを回転"),
            b.slider("lost_hold_s", "見失った時の最終映像保持", 0, 3, 0.1, "{:.1f}", " 秒"),
            b.slider("extra_detect_interval", "Body/Hands検出の間引き", 1, 6, 1, "1/{:.0f}"),
            section("Motion（動きを生成トリガーに使う）"),
            b.check("motion_enabled", "Motion ON（動き量で生成量・方向を変える）"),
            b.slider("motion_threshold", "動き判定しきい値(画素差)", 5, 100, 1),
            b.slider("motion_sensitivity", "モーション感度", 0, 4, 0.05, "{:.2f}"),
            section("反応"),
            b.check("mouth_burst", "口を開けたらBURST"),
            b.slider("mouth_open_threshold", "口の開き判定", 0.1, 0.8, 0.01, "{:.2f}"),
            b.check("head_burst", "頭を素早く動かしたらBURST"),
            b.slider("head_speed_threshold", "頭の速さ判定", 0.3, 5, 0.1, "{:.1f}", " 顔幅/秒"),
        )

    def _tab_generation(self):
        b = self.b
        return page(
            section("GENERATION"),
            b.slider("max_windows", "最大同時表示数", 0, 600, 1),
            b.slider("spawn_rate", "生成レート", 0, 300, 1, "{:.0f}", " 個/秒"),
            b.slider("burst_count", "BURST数", 1, 300, 1),
            b.slider("life_s", "寿命", 0.2, 20, 0.1, "{:.1f}", " 秒"),
            b.slider("life_jitter", "寿命の揺らぎ", 0, 1, 0.05, "{:.2f}"),
            b.combo("spawn_area", "生成位置"),
            b.slider("spawn_spread", "生成位置の散らばり", 0, 800, 5, "{:.0f}", " px"),
            b.slider("tracking_range", "顔の移動→画面の拡大率", 0.5, 3, 0.05, "{:.2f}"),
            b.slider("size", "サイズ（映像幅）", 30, 480, 2, "{:.0f}", " px"),
            b.slider("size_jitter", "サイズの揺らぎ", 0, 0.9, 0.05, "{:.2f}"),
            b.slider("face_ratio", "顔全体の出現比率", 0, 1, 0.05, "{:.2f}"),
            b.slider("duplicate_bias", "同じパーツの複製比率", 0, 1, 0.05, "{:.2f}"),
        )

    def _tab_motion(self):
        b = self.b
        return page(
            section("MOTION / ANIMATION"),
            b.combo("motion_mode", "モード"),
            b.slider("speed", "移動速度", 0, 4, 0.05, "{:.2f}"),
            b.slider("follow_speed", "追従速度", 0.1, 15, 0.1, "{:.1f}"),
            b.slider("follow_lag_jitter", "追従遅れのばらつき", 0, 1, 0.05, "{:.2f}"),
            b.slider("scatter", "飛散・散らばり", 0, 3, 0.05, "{:.2f}"),
            b.slider("randomness", "ランダム性", 0, 3, 0.05, "{:.2f}"),
            b.slider("damping", "減衰", 0, 6, 0.05, "{:.2f}"),
            b.slider("face_push", "顔の動きの影響", 0, 4, 0.05, "{:.2f}"),
            b.combo("edge_mode", "画面端の動作"),
        )

    def _tab_display(self):
        b = self.b
        return page(
            section("表示モード"),
            b.combo("layout_mode", "表示モード"),
            QLabel("ミラー：カメラ画像とデスクトップを 1:1 に対応させ、各部位をカメラ上と同じ位置・大きさの窓で表示。"
                   "口/頭の反応BURSTは既定OFF（BURSTボタンで出した窓は動き回る）。"),
            b.combo("mirror_style", "ミラーの動き"),
            b.slider("mirror_stamp_move", "生成するずれ量（窓の大きさ比）", 0.05, 1.0, 0.05, "{:.2f}"),
            b.slider("mirror_stamp_interval", "同じ部位の最短生成間隔", 0, 0.5, 0.01, "{:.2f}", " 秒"),
            b.slider("mirror_stamp_life", "置いていかれた窓が残る時間", 0.2, 10, 0.1, "{:.1f}", " 秒"),
            b.check("mirror_stamp_freeze", "置いていかれた窓の映像を止める"),
            b.combo("mirror_fit", "カメラ→画面の合わせ方"),
            b.slider("mirror_scale", "窓の大きさ（部位比）", 0.5, 2.0, 0.05, "{:.2f}"),
            b.slider("mirror_glide", "［追いかける］窓の動きのなめらかさ（0=即移動）", 0, 0.2, 0.005, "{:.3f}", " 秒"),
            b.slider("mirror_trails", "［追いかける］残像の窓の数（0=なし）", 0, 8, 1),
            b.check("mirror_reactions", "ミラーでも口/頭の反応BURSTを出す（窓が飛び回る）"),
            b.slider("mirror_trail_lag", "残像1段の遅れ", 0.02, 0.6, 0.01, "{:.2f}", " 秒"),
            b.slider("mirror_trail_opacity", "残像の濃さ", 0.1, 1, 0.05, "{:.2f}"),
            b.combo("crop_size", "切り抜き解像度"),
            section("DISPLAY"),
            b.combo("render_mode", "描画方式"),
            b.slider("native_max", "実ウィンドウ上限", 1, 120, 1),
            b.combo("target_screen", "表示モニター", options=list(range(len(self.ctl.screens()))),
                    labels={i: f"{i}: {s.name()} {s.geometry().width()}x{s.geometry().height()}"
                            for i, s in enumerate(self.ctl.screens())}),
            b.combo("window_style", "窓のスタイル"),
            b.slider("opacity", "透明度（不透明度）", 0.1, 1, 0.05, "{:.2f}"),
            b.check("shadow", "影"),
            b.check("smooth_scaling", "なめらか拡大縮小（重い）"),
            b.check("avoid_panel", "管理ウィンドウの上には描かない"),
            section("映像（最新映像 / 残像）"),
            b.slider("snapshot_ratio", "静止画スナップショット窓の割合", 0, 1, 0.05, "{:.2f}"),
            b.slider("delay_ratio", "過去映像（ディレイ）窓の割合", 0, 1, 0.05, "{:.2f}"),
            b.slider("delay_s", "ディレイ最大秒数", 0.1, 3, 0.1, "{:.1f}", " 秒"),
            b.slider("afterimage_ratio", "消える窓が静止して残る割合", 0, 1, 0.05, "{:.2f}"),
            b.slider("afterimage_s", "残像の残り時間", 0.2, 6, 0.1, "{:.1f}", " 秒"),
        )

    def _tab_perf(self):
        b = self.b
        self.perf_lb = QLabel()
        self.perf_lb.setTextFormat(Qt.RichText)
        self.perf_lb.setWordWrap(True)
        bench = QPushButton("負荷ベンチマーク実行（20/100/200個 各6秒）")
        bench.clicked.connect(self.ctl.run_benchmark)
        self.bench_lb = QLabel()
        self.bench_lb.setWordWrap(True)
        self.bench_lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return page(
            section("PERFORMANCE"), self.perf_lb,
            b.check("adaptive", "自動負荷調整（描画FPSが目標を下回ったら表示数を抑制）"),
            b.slider("target_fps", "目標描画FPS", 10, 120, 1),
            b.slider("min_windows", "抑制時の最低表示数", 0, 100, 1),
            bench, self.bench_lb,
        )

    def _tab_system(self):
        b = self.b
        self.preset_combo = QComboBox()
        self.reload_presets()
        apply = QPushButton("プリセットを適用")
        apply.clicked.connect(lambda: self.ctl.apply_preset(self.preset_combo.currentText()))
        save_p = QPushButton("現在の設定をプリセットとして保存…")
        save_p.clicked.connect(self._save_preset)
        save = QPushButton("設定を保存")
        save.clicked.connect(self.ctl.save_settings)
        load = QPushButton("設定を読み込み")
        load.clicked.connect(self.ctl.load_settings)
        init = QPushButton("設定を初期化")
        init.clicked.connect(self.ctl.reset_settings)
        quit_b = QPushButton("アプリを終了")
        quit_b.clicked.connect(self.ctl.quit)
        self.sys_lb = QLabel(f"保存先: {config.SETTINGS_PATH}")
        self.sys_lb.setWordWrap(True)
        return page(
            section("PRESETS"), self.preset_combo, apply, save_p,
            section("SYSTEM"), save, load, init,
            b.check("panel_on_top", "管理ウィンドウを常に前面"),
            b.check("autostart", "起動時に自動START"),
            self.sys_lb, quit_b,
            QLabel("プライバシー：カメラ映像はディスクへの保存・外部送信を行いません。"),
        )

    def reload_presets(self):
        cur = self.preset_combo.currentText()
        self.preset_combo.clear()
        self.preset_combo.addItems(config.list_presets())
        if cur:
            self.preset_combo.setCurrentText(cur)

    def _save_preset(self):
        name, ok = QInputDialog.getText(self, "プリセット保存", "プリセット名:")
        if ok and name.strip():
            self.ctl.save_preset(name.strip())
            self.reload_presets()

    # ---------- live updates ----------
    def refresh_render_label(self):
        self.render_lb.setText(RENDER_DESC[self.ctl.settings["render_mode"]])

    def set_preview(self, qimg):
        if qimg is None:
            return
        pm = QPixmap.fromImage(qimg)
        self.preview.setPixmap(pm.scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.FastTransformation))

    def set_tracking(self, status: dict, enabled_toggles: list[str]):
        colors = {"OK": "#4caf50", "HOLD": "#ffb74d", "LOST": "#ef5350"}
        chips = []
        for t, parts in geo.TOGGLE_PARTS.items():
            if t not in enabled_toggles:
                continue
            for p in parts:
                st = status.get(p, "—")
                chips.append(f"<span style='color:{colors.get(st, '#888')}'>{geo.PART_LABEL[p]}:{st}</span>")
        self.track_lb.setText(" ".join(chips) if chips else "<span style='color:#888'>検出対象がすべてOFF（新規生成なし）</span>")
