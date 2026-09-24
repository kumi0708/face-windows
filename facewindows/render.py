"""描画：透明オーバーレイ上の疑似ウィンドウ / OS実ウィンドウ。"""
from __future__ import annotations

import ctypes
import time

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPen, QPixmap, QRegion
from PySide6.QtWidgets import QWidget

STYLES = {
    #               title_h, bg,         border,      text,        accent
    "win11_light": (22, "#f3f3f3", "#7a7a7a", "#1b1b1b", None),
    "win11_dark": (22, "#202020", "#4a4a4a", "#f0f0f0", None),
    "retro": (18, "#c0c0c0", "#000000", "#ffffff", "#000080"),
    "macos": (22, "#e9e7e5", "#9a9a9a", "#3a3a3a", None),
    "frameless": (0, "#000000", "#ffffff", "#ffffff", None),
}


def current_image(w, snap, tracker) -> QImage | None:
    """窓に表示する画像。取得できない場合は直前の画像（破損画像を出さない）。"""
    img = None
    if w.image_mode == "snap" and w.frozen is not None:
        img = w.frozen
    elif w.part in snap.parts:
        if w.image_mode == "delay" and tracker is not None:
            img = tracker.delayed_image(w.part, w.delay)
        if img is None:
            img = snap.parts[w.part].image
    if img is None:
        img = w.last_image
    w.last_image = img
    return img


def make_titlebar(style: str, w: int, title: str) -> QPixmap | None:
    """タイトルバーだけの不透明な画像（窓ごとに一度だけ作る）。
    映像部に透明な大きい画像を重ねるとアルファ合成が重いため、枠線は別に描く。"""
    th, bg, border, text, accent = STYLES[style]
    if th == 0:
        return None
    pw = w + 2
    pm = QPixmap(pw, th + 1)
    pm.fill(QColor(bg))
    p = QPainter(pm)
    if style == "retro":
        p.fillRect(2, 2, pw - 4, th - 3, QColor(accent))
        bw = th - 6
        for i in range(3):
            bx = pw - 4 - (i + 1) * (bw + 2)
            if bx < 40:
                break
            p.fillRect(bx, 4, bw, bw - 1, QColor("#c0c0c0"))
            p.setPen(QColor("#000000"))
            p.drawRect(bx, 4, bw - 1, bw - 2)
            if i == 0:
                p.drawLine(bx + 3, 7, bx + bw - 4, bw)
                p.drawLine(bx + bw - 4, 7, bx + 3, bw)
        reserve, tx = 60, 6
    elif style == "macos":
        p.setRenderHint(QPainter.Antialiasing)
        for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840")):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c))
            p.drawEllipse(QPointF(10 + i * 14, th / 2 + 0.5), 4.5, 4.5)
        reserve, tx = 56, 52
    else:
        p.setPen(QPen(QColor(text), 1))
        gx, cy, gw = pw - 12, th // 2, 26
        if w >= 110:
            p.drawLine(gx - 4, cy - 4, gx + 3, cy + 3)
            p.drawLine(gx + 3, cy - 4, gx - 4, cy + 3)
            p.drawRect(gx - gw - 4, cy - 4, 7, 7)
            p.drawLine(gx - 2 * gw - 4, cy, gx - 2 * gw + 4, cy)
        reserve, tx = (90 if w >= 110 else 6), 7
    p.setPen(QColor(text))
    f = QFont()
    f.setFamilies(["Segoe UI", "Hiragino Sans", "Helvetica Neue"])   # Segoe UI が無い macOS 用の控え
    f.setPointSize(8)
    f.setBold(style == "retro")
    p.setFont(f)
    avail = max(10, pw - reserve - (tx if style == "macos" else 0))
    align = Qt.AlignVCenter | (Qt.AlignHCenter if style == "macos" else Qt.AlignLeft)
    p.drawText(QRect(tx, 0, avail, th), align, p.fontMetrics().elidedText(title, Qt.ElideRight, avail))
    p.end()
    return pm


class Overlay(QWidget):
    """画面全体を覆う透明・クリック透過のウィンドウ。疑似ウィンドウを一括描画する。"""

    def __init__(self, app_state):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.st = app_state
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)   # macOS: アプリが背面でも隠さない
        self.setWindowTitle("FACE WINDOWS overlay")
        self._chrome: dict[int, tuple] = {}
        self.paint_ms = 0.0
        self.frames = 0
        self.last_latency = None
        self.exclude: QRect | None = None

    def place_on(self, screen) -> None:
        self.setScreen(screen)
        self.setGeometry(screen.geometry())

    def forget(self, win_id: int) -> None:
        self._chrome.pop(win_id, None)

    def paintEvent(self, e):
        t0 = time.perf_counter()
        st = self.st
        s = st.settings
        snap = st.tracker.snapshot() if st.tracker else None
        p = QPainter(self)
        if s["smooth_scaling"]:
            p.setRenderHint(QPainter.SmoothPixmapTransform)
        if self.exclude is not None and s["avoid_panel"]:
            local = QRect(self.exclude.topLeft() - self.geometry().topLeft(), self.exclude.size())
            p.setClipRegion(QRegion(self.rect()).subtracted(QRegion(local.adjusted(-2, -2, 2, 2))))
        style = s["window_style"]
        th, _bg, border, _t, _a = STYLES[style]
        border_pen = QPen(QColor(border) if th else QColor(255, 255, 255, 110), 1)
        base_op = float(s["opacity"])
        shadow = s["shadow"]
        off = self.geometry().topLeft()
        ox, oy = off.x(), off.y()
        shadow_col = QColor(0, 0, 0, 60)
        black = QColor(0, 0, 0)
        p.setPen(border_pen)
        p.setBrush(Qt.NoBrush)
        if snap is not None:
            for w in st.engine.wins:
                if w.backend != "overlay":
                    continue
                img = current_image(w, snap, st.tracker)
                iw, ih = int(w.w), int(w.h)
                # ミラー表示では毎フレーム幅が変わるので、タイトルバーは 16px 刻みで作って伸縮させる
                bw = iw if w.mirror_key is None else max(16, int(round(iw / 16)) * 16)
                key = self._chrome.get(w.id)
                if key is None or key[0] != style or key[1] != bw:
                    key = (style, bw, make_titlebar(style, bw, w.title))
                    self._chrome[w.id] = key
                bar = key[2]
                cw, ch = iw + 2, ih + th + 2
                p.setOpacity(base_op * w.alpha)
                cx, cy = w.x - ox, w.y - oy
                if w.scale != 1.0:
                    p.save()
                    p.translate(cx, cy)
                    p.scale(w.scale, w.scale)
                    cx = cy = 0.0
                # 映像部の中心を (x, y) に合わせる（タイトルバーはその上に付く）
                left, top = int(cx - cw / 2), int(cy - ih / 2 - th - 1)
                if shadow:  # 右と下の帯だけ（全面の半透明塗りは重い）
                    p.fillRect(left + cw, top + 5, 4, ch, shadow_col)
                    p.fillRect(left + 5, top + ch, cw - 1, 4, shadow_col)
                if img is not None:
                    p.drawImage(QRect(left + 1, top + th + 1, iw, ih), img)
                else:
                    p.fillRect(left + 1, top + th + 1, iw, ih, black)
                if bar is not None:
                    if bar.width() == cw:
                        p.drawPixmap(left, top, bar)
                    else:
                        p.drawPixmap(QRect(left, top, cw, bar.height()), bar)
                p.drawRect(left, top, cw - 1, ch - 1)
                if w.scale != 1.0:
                    p.restore()
            if snap.t_capture:
                self.last_latency = time.perf_counter() - snap.t_capture
        p.end()
        self.frames += 1
        self.paint_ms = (time.perf_counter() - t0) * 1000


class NativeWin(QWidget):
    """OSの実ウィンドウ（タイトルバーはOSが描く）。"""
    closed_by_user = Signal(int)

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.win_id = -1
        self.img: QImage | None = None
        self.size_key = None
        self._opacity = 1.0

    def paintEvent(self, e):
        p = QPainter(self)
        if self.img is not None:
            # 窓の縦横比に合わせて中央を切り出す（再利用した窓はサイズを変えないため）
            iw, ih = self.img.width(), self.img.height()
            ww, wh = max(1, self.width()), max(1, self.height())
            sc = max(ww / iw, wh / ih)
            sw, sh = ww / sc, wh / sc
            p.drawImage(QRectF(0, 0, ww, wh), self.img, QRectF((iw - sw) / 2, (ih - sh) / 2, sw, sh))
        else:
            p.fillRect(self.rect(), Qt.black)
        p.end()

    def closeEvent(self, e):
        # 個別の窓を閉じてもアプリは終了しない。シミュレーションから外すだけ
        e.ignore()
        self.hide()
        self.closed_by_user.emit(self.win_id)


class NativePool:
    """OS実ウィンドウの再利用プール。移動は DeferWindowPos で一括実行。"""

    SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010

    def __init__(self, on_close):
        self.free: list[NativeWin] = []      # 非表示で待機中
        self.pending: list[NativeWin] = []   # 表示したまま次の窓へ引き継ぐ候補（hide/show は 1回数ms かかる）
        self.used: dict[int, NativeWin] = {}
        self._z_order: tuple = ()
        self.on_close = on_close
        self.paint_ms = 0.0
        self._user32 = None
        if hasattr(ctypes, "WinDLL"):
            u = ctypes.WinDLL("user32")
            vp = ctypes.c_void_p
            u.BeginDeferWindowPos.argtypes = [ctypes.c_int]
            u.BeginDeferWindowPos.restype = vp
            u.DeferWindowPos.argtypes = [vp, vp, vp, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                         ctypes.c_int, ctypes.c_uint]
            u.DeferWindowPos.restype = vp
            u.EndDeferWindowPos.argtypes = [vp]
            self._user32 = u

    def _get(self) -> NativeWin:
        if self.pending:
            return self.pending.pop()
        if self.free:
            return self.free.pop()
        w = NativeWin()
        w.closed_by_user.connect(self.on_close)
        return w

    def release(self, win_id: int) -> None:
        nw = self.used.pop(win_id, None)
        if nw is not None:
            nw.win_id = -1
            self.pending.append(nw)

    def _hide_pending(self) -> None:
        for nw in self.pending:
            nw.hide()
            nw.img = None
            self.free.append(nw)
        self.pending.clear()

    def release_all(self) -> None:
        for wid in list(self.used):
            self.release(wid)
        self._hide_pending()

    def destroy(self) -> None:
        self.release_all()
        for w in self.free:
            w.deleteLater()
        self.free.clear()

    def sync(self, wins, snap, tracker, screen, opacity: float, below_hwnd: int | None = None) -> bool:
        """below_hwnd: この窓（管理ウィンドウ）より下に並べる（Windows）。None なら最前面の一番上から。
        戻り値: 重なり順を並べ直したか（Windows 以外では呼び出し側で管理ウィンドウを前に出す）。"""
        t0 = time.perf_counter()
        active = [w for w in wins if w.backend == "native"]
        ids = {w.id for w in active}
        for wid in [i for i in self.used if i not in ids]:
            self.release(wid)
        if snap is None:
            self._hide_pending()
            return False
        geo_tl = screen.geometry().topLeft()
        dpr = screen.devicePixelRatio()
        moves = []
        for w in active:
            nw = self.used.get(w.id)
            if nw is None:
                nw = self._get()
                if nw.size_key is not None and w.mirror_key is None:
                    # resize は1回数msかかるので、再利用した窓は今のサイズのまま使う
                    w.w, w.h = nw.size_key
                nw.win_id = w.id
                if nw.windowTitle() != w.title:
                    nw.setWindowTitle(w.title)
                self.used[w.id] = nw
            # 実ウィンドウは拡大縮小・フェードのアニメーションをしない（リサイズ/透明化が重い）
            iw, ih = max(40, int(w.w)), max(24, int(w.h))
            sk = nw.size_key
            if (sk is not None and w.mirror_key is not None
                    and abs(iw - sk[0]) <= sk[0] * 0.06 and abs(ih - sk[1]) <= sk[1] * 0.06):
                iw, ih = sk   # ミラー表示：小さな大きさの変化ではリサイズしない（重いため）
            if nw.size_key != (iw, ih):
                nw.resize(iw, ih)
                nw.size_key = (iw, ih)
            op = round(opacity, 2)
            if abs(op - nw._opacity) > 0.04:
                nw.setWindowOpacity(op)
                nw._opacity = op
            nw.img = current_image(w, snap, tracker)
            lx, ly = w.x - iw / 2, w.y - ih / 2 - 24   # 24 ≒ OSタイトルバー
            if not nw.isVisible():
                nw.move(int(lx), int(ly))   # Qt の move はフレーム左上（論理座標）
                nw.show()
                self._z_order = ()          # 新しい窓は最前面に出るので並べ直す
            if self._user32 is not None:   # Win32 は物理ピクセル
                px = int(geo_tl.x() + (lx - geo_tl.x()) * dpr)
                py = int(geo_tl.y() + (ly - geo_tl.y()) * dpr)
            else:                          # Qt の move は論理座標
                px, py = int(lx), int(ly)
            moves.append((nw, px, py))
            nw.update()
        self._hide_pending()
        u = self._user32
        # 重なり順（ミラー表示で顔の上に目・口が来る／管理ウィンドウを覆わない）。順番が変わった時だけ並べ直す
        z = tuple(w.id for w in active)
        restack = z != self._z_order
        self._z_order = z
        if moves and u is not None:
            h = u.BeginDeferWindowPos(len(moves))
            flags = self.SWP_NOSIZE | self.SWP_NOACTIVATE | (0 if restack else self.SWP_NOZORDER)
            if restack:   # 手前の窓から順に、直前の窓の後ろへ挿入する
                pos = {id(nw): (px, py) for nw, px, py in moves}
                prev = ctypes.c_void_p(below_hwnd if below_hwnd else -1)   # 管理ウィンドウの下 / HWND_TOPMOST
                for w in reversed(active):
                    nw = self.used.get(w.id)
                    if nw is None or id(nw) not in pos or not h:
                        continue
                    px, py = pos[id(nw)]
                    hwnd = int(nw.winId())
                    h = u.DeferWindowPos(h, hwnd, prev, px, py, 0, 0, flags)
                    prev = ctypes.c_void_p(hwnd)
            else:
                for nw, px, py in moves:
                    if h:
                        h = u.DeferWindowPos(h, int(nw.winId()), None, px, py, 0, 0, flags)
            if h:
                u.EndDeferWindowPos(h)
        elif moves:   # Windows 以外：1枚ずつ移動し、重なり順は奥から raise_ で積む
            for nw, px, py in moves:
                nw.move(px, py)
            if restack:
                for w in active:
                    nw = self.used.get(w.id)
                    if nw is not None:
                        nw.raise_()
        self.paint_ms = (time.perf_counter() - t0) * 1000
        return restack and u is None
