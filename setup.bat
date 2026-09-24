@echo off
chcp 65001 >nul
rem FACE WINDOWS 初回セットアップ（Python 3.11 を使用）
cd /d %~dp0
py -3.11 -m venv .venv || (echo Python 3.11 が必要です: https://www.python.org/ & exit /b 1)
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
if not exist models mkdir models
set BASE=https://storage.googleapis.com/mediapipe-models
if not exist models\face_landmarker.task curl -L -o models\face_landmarker.task %BASE%/face_landmarker/face_landmarker/float16/latest/face_landmarker.task
if not exist models\pose_landmarker_lite.task curl -L -o models\pose_landmarker_lite.task %BASE%/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task
if not exist models\hand_landmarker.task curl -L -o models\hand_landmarker.task %BASE%/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task
echo セットアップ完了。run.bat で起動します。
