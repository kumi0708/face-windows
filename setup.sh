#!/bin/bash
# FACE WINDOWS 初回セットアップ（macOS / Linux、Python 3.11 を使用）
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3.11}
if ! command -v "$PY" >/dev/null; then
  echo "Python 3.11 が必要です（例: brew install python@3.11）"; exit 1
fi
"$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
mkdir -p models
BASE=https://storage.googleapis.com/mediapipe-models
[ -f models/face_landmarker.task ] || curl -L -o models/face_landmarker.task "$BASE/face_landmarker/face_landmarker/float16/latest/face_landmarker.task"
[ -f models/pose_landmarker_lite.task ] || curl -L -o models/pose_landmarker_lite.task "$BASE/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task"
[ -f models/hand_landmarker.task ] || curl -L -o models/hand_landmarker.task "$BASE/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
echo "セットアップ完了。./run.sh で起動します。"
