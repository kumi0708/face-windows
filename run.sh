#!/bin/bash
cd "$(dirname "$0")"

# macOS：make_mac_app.sh で作った .app を open（LaunchServices）で起動する。
# ターミナルから python を直接動かすと、macOS はアプリとして扱わずカメラ許可の
# ダイアログを出さないまま拒否する。.app 経由なら FACE WINDOWS として許可を求められ、
# 「プライバシーとセキュリティ → カメラ」の一覧にも載る。ログは build/facewindows.log。
APP="build/FACE WINDOWS.app"
if [ "$(uname -s)" = "Darwin" ] && [ -d "$APP" ]; then
  if [ $# -gt 0 ]; then
    exec open -a "$PWD/$APP" --args "$@"
  fi
  exec open -a "$PWD/$APP"
fi

exec .venv/bin/python main.py "$@"
