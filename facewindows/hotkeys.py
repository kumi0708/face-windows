"""Windows のグローバルホットキー（どのアプリが前面でも効く緊急停止）。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading

from PySide6.QtCore import QObject, Signal

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012

HOTKEYS = {
    1: ("stop", MOD_CONTROL | MOD_ALT, ord("S"), "Ctrl+Alt+S"),
    2: ("quit", MOD_CONTROL | MOD_ALT, ord("Q"), "Ctrl+Alt+Q"),
    3: ("pause", MOD_CONTROL | MOD_ALT, ord("P"), "Ctrl+Alt+P"),
    4: ("panel", MOD_CONTROL | MOD_ALT, ord("W"), "Ctrl+Alt+W"),
}
# 他のアプリが使っていて登録できなかった時に試す予備のキー
ALTERNATIVES = {
    4: [(MOD_CONTROL | MOD_ALT, 0x7B, "Ctrl+Alt+F12")],
}


class GlobalHotkeys(QObject):
    triggered = Signal(str)

    def __init__(self):
        super().__init__()
        self.registered: list[str] = []
        self.failed: list[str] = []
        self.labels: dict[str, str] = {}   # 動作名 → 実際に登録できたキー
        self._tid = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="hotkeys")

    def start(self) -> None:
        if not hasattr(ctypes, "WinDLL"):
            return
        self._thread.start()
        self._ready.wait(2)

    def _run(self) -> None:
        user32 = ctypes.WinDLL("user32")
        kernel32 = ctypes.WinDLL("kernel32")
        self._tid = kernel32.GetCurrentThreadId()
        for hid, (name, mods, vk, label) in HOTKEYS.items():
            for m, v, lb in [(mods, vk, label)] + ALTERNATIVES.get(hid, []):
                if user32.RegisterHotKey(None, hid, m | MOD_NOREPEAT, v):
                    self.registered.append(lb)
                    self.labels[name] = lb
                    break
            else:
                self.failed.append(label)
        self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in HOTKEYS:
                self.triggered.emit(HOTKEYS[msg.wParam][0])
        for hid in HOTKEYS:
            user32.UnregisterHotKey(None, hid)

    def label(self, name: str) -> str | None:
        """その動作に実際に割り当てられたキー（登録できなかったら None）。"""
        return self.labels.get(name)

    def stop(self) -> None:
        if self._tid:
            ctypes.WinDLL("user32").PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
