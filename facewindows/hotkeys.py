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
}


class GlobalHotkeys(QObject):
    triggered = Signal(str)

    def __init__(self):
        super().__init__()
        self.registered: list[str] = []
        self.failed: list[str] = []
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
            if user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
                self.registered.append(label)
            else:
                self.failed.append(label)
        self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in HOTKEYS:
                self.triggered.emit(HOTKEYS[msg.wParam][0])
        for hid in HOTKEYS:
            user32.UnregisterHotKey(None, hid)

    def stop(self) -> None:
        if self._tid:
            ctypes.WinDLL("user32").PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
