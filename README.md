# FACE WINDOWS

**日本語** | [English](#english)

Webカメラに映った顔（顔全体・左右の目・鼻・口、任意で身体・腕・手）のライブ映像を切り抜き、
デスクトップ上の**大量の小さなタイトルバー付きウィンドウ**として高速に増殖・飛散・追従させるインタラクティブアート。
画面右側の管理ウィンドウ（CONTROL PANEL）から、すべての設定を再起動なしで変更できる。

参考にした作品：https://www.instagram.com/reels/DdjqhjPhkm7/

## 2つの表示モード

| モード | 内容 |
|---|---|
| **増殖**（既定） | 顔の部位から窓が次々に生まれ、大小さまざまな大きさで飛び散る・漂う・追従する |
| **ミラー** | カメラ画像とデスクトップを **1:1** に対応させ、顔・目・鼻・口の窓を「カメラ上でその部位が写っている位置・大きさ」に置く。既定の動き（**ずれたら生成**）では窓は動かず、顔の位置・大きさがずれるたびにその時点のミラー位置へ新しい窓を生成し、置いていかれた窓はその場に残ってフェードして消える（顔を動かした跡に窓が並ぶ）。デスクトップ上に窓で組み立てた自分の顔が現れ、近づくと大きな窓になる。勝手に飛び回る窓は出さない（口/頭の反応BURSTは既定 OFF）。「窓が部位を追いかける」動き（遅れてついてくる残像の窓も重ねられる）にも切り替えられる |

管理画面の「表示モード」で切り替える。PRESETS の「ミラー（顔を再構成）」を適用すると、ミラー向けの設定（切り抜き 360px、回転補正 OFF など）がまとめて入る。
ミラーの設定（DISPLAY タブ）：ミラーの動き（ずれたら生成／追いかける）、生成するずれ量、置いていかれた窓が消えるまでの時間とフェードの長さ、カメラ→画面の合わせ方（比率を保って画面を埋める／収める／引き伸ばす）、窓の大きさ、残像の数・遅れ・濃さ、切り抜き解像度。
「追いかける」動きでは、検出枠に One Euro フィルタ（止まっている時のブレを抑え、動くと素早く追従）をかけ、検出（30fps）の間は描画（60fps）で補間して動かす（TRACKING の「ブレ補正」、DISPLAY の「窓の動きのなめらかさ」で調整）。
管理ウィンドウも含めたデスクトップ全体を 1:1 にしている（管理ウィンドウは最前面。疑似ウィンドウはその上に描かず、OS 実ウィンドウは管理ウィンドウの下に並べる）。

> **描画方式について**：既定の窓は **疑似ウィンドウ**（画面全体を覆う透明・クリック透過のウィンドウ1枚に、タイトルバー付きの窓を描いたもの）で、OS のウィンドウではない。
> 管理画面の DISPLAY → 描画方式で **OS 実ウィンドウ**（少数）や**混合**に切り替えられる。選定理由と測定値は [TECH_DECISIONS.md](TECH_DECISIONS.md)。

## 動作環境

- Windows 10/11（検証済み）
- macOS 13 以降・Apple Silicon（M1 以降）（M1 MacBook Air / macOS 26 で起動・カメラ・顔検出・窓の生成まで確認。性能は未測定。MediaPipe の Mac 版が Apple Silicon 専用のため Intel Mac は不可）
- Python 3.11（MediaPipe が対応するバージョン）
- Webカメラ（Windows の「設定 → プライバシーとセキュリティ → カメラ」でデスクトップアプリの使用を許可）
- 検証環境：Windows 11 / RTX 5070 Laptop / 2880×1800(150%) / ASUS FHD webcam

## 初回セットアップ

Windows:
```bat
setup.bat
```

macOS（Python 3.11 が必要。例: `brew install python@3.11`）:
```bash
./setup.sh
```

`.venv` の作成、`requirements.txt` のインストール、MediaPipe のモデル3つ（`models/` に face / pose_lite / hand、合計約17MB、Google の公式配布元から取得）を行う。

## 起動・終了

```bat
run.bat
```
macOS は `./run.sh`。初回はカメラの使用許可を求められる（許可しなかった場合は「システム設定 → プライバシーとセキュリティ → カメラ」で許可して再起動）。
許可ダイアログは最前面のアプリにしか出ないため、macOS ではターミナルから直接起動すること（バックグラウンドで起動すると、ダイアログが出ないまま「カメラ停止中」になる）。

1. 起動すると画面右に管理ウィンドウが開き、カメラのプレビューと検出枠が表示される。
2. **▶ START** で窓の生成が始まる（「起動時に自動START」を ON にすれば自動）。
3. **❚❚ PAUSE** で一時停止、**■ STOP** で全窓を回収してカメラを解放、**↺ RESET** で窓を全消去。
4. 終了は管理ウィンドウを閉じる／SYSTEM タブの「アプリを終了」／`Ctrl+Alt+Q`。

### 緊急停止（どのアプリが前面でも有効）

| キー | 動作 |
|---|---|
| `Ctrl+Alt+S` | STOP（生成停止・全窓回収・カメラ解放） |
| `Ctrl+Alt+Q` | アプリ終了 |
| `Ctrl+Alt+P` | PAUSE 切替 |
| 管理画面上で `Esc` / `F5` / `B` / `Ctrl+Q` | STOP / START / BURST / 終了 |

グローバルな緊急停止キーは Windows のみ。macOS では管理画面の STOP ボタン、または管理画面上の Esc / ⌘Q を使う。
他のアプリがこのキーを使っていて登録できなかった場合は、管理画面の下部に表示される。その場合も STOP ボタンと Esc は使える。
疑似ウィンドウはクリックを透過し、管理ウィンドウの上には描かない設定（既定 ON）なので、窓が大量でも管理画面は操作できる。

## 管理画面

| セクション | 内容 |
|---|---|
| 常時表示 | 状態、描画方式、検出プレビュー（部位ごとの枠・HOLD/LOST表示・動きの位置）、START/PAUSE/STOP/RESET/BURST、部位 ON/OFF、最大数・生成レート・移動速度・動きモード、窓数・入力/推論/描画FPS、警告 |
| CAMERA | カメラ選択（DirectShow の名前を表示）、解像度、入力FPS、左右反転、再接続 |
| TRACKING | Face / Left eye / Right eye / Nose / Mouth / Body / Arms / Hands、検出信頼度しきい値、ブレ補正（One Euro）、回転補正、見失った時の保持時間、Body/Hands の間引き、Motion ON/OFF・動き判定しきい値・モーション感度、口を開けたら/頭を振ったら BURST |
| GENERATION | 最大同時表示数、生成レート（個/秒）、BURST数、寿命と揺らぎ、生成位置（顔の周囲／部位付近／ランダム／動きの位置）、散らばり、拡大率、サイズと揺らぎ、顔全体の比率、同じパーツの複製比率 |
| MOTION | 追従／飛散／ランダム漂流／位置固定／混合、移動速度、追従速度、追従遅れのばらつき、飛散、ランダム性、減衰、顔の動きの影響、画面端（跳ね返り／反対側へ／消滅／再配置） |
| DISPLAY | 表示モード（増殖／ミラー）とミラーの設定、描画方式（疑似／OS実／混合）、実ウィンドウ上限、表示モニター、スタイル（Win11 ライト／ダーク／macOS風／Win95風／フレームレス。既定は動かしている OS に合わせる）、不透明度、影、スナップショット窓・ディレイ窓・残像の割合 |
| PERF | 窓数（実/疑似）、要求値と実際の上限、生成レート要求/実際、各FPS、検出時間、描画時間、遅延、メモリ、CPU、自動負荷調整、ベンチマーク |
| PRESETS/SYSTEM | プリセット（高速増殖／顔追従／ミラー（顔を再構成）／ランダム飛散＋ユーザー保存）、設定の保存・読込・初期化、管理画面の最前面固定、自動START、終了 |

- 左右は**人物から見た左右**（Left eye = 本人の左目）。左右反転は表示だけに影響する。
- 「実際に取得できない指標」は「未計測」と表示する（GPU 使用率など）。
- 設定は `config/settings.json`、ユーザープリセットは `config/presets/*.json` に保存される（保存ボタンを押したときのみ）。

## 仕組み（概要）

```
[検出プロセス]  カメラスレッド(最新1枚) → 検出スレッド(MediaPipe、1フレーム1回)
                 → 部位ごとに回転補正付き切り抜き(BGRA 240px) + 動き量 + 口の開き/頭の速度
                 ──Pipe(最新のみ)──▶
[GUIプロセス]   受信スレッド(QImage化・ディレイ用履歴) → シミュレーション(生成/移動/寿命/上限/適応制御)
                 → 透明オーバーレイに疑似ウィンドウを一括描画 / OS実ウィンドウを DeferWindowPos で一括移動
                 管理ウィンドウ（設定は共有 dict、変更は次のフレームから反映、検出側へも自動送信）
```

| ファイル | 役割 |
|---|---|
| `facewindows/app.py` | 全体制御（START/STOP、メインループ、統計、ベンチマーク） |
| `facewindows/remote.py` | 検出プロセスとの通信 |
| `facewindows/camera.py` / `tracker.py` / `geometry.py` | カメラ取得、検出・切り抜き、座標計算 |
| `facewindows/engine.py` | 窓の生成・動き・寿命・自動負荷調整 |
| `facewindows/render.py` | 疑似ウィンドウ描画、OS 実ウィンドウのプール |
| `facewindows/panel.py` | 管理画面 |
| `facewindows/hotkeys.py` | グローバルホットキー |
| `bench/window_bench.py` | 方式比較用の単体ベンチマーク |

## テスト・測定

```bat
.venv\Scripts\python -m pytest -q tests
```

設定の検証、左右・反転の座標、切り抜きの向き、上限を超えて増えないこと、全部位 OFF で生成しないこと、画面外へ消えないこと、寿命・見失い時の消滅、自動負荷調整、実ウィンドウ上限、口の BURST、PAUSE、ミラー表示の座標対応・部位ごとの窓・残像の遅れ・見失い時の消去・モード切替を検証する（38 件）。

性能測定は管理画面 PERF タブの「負荷ベンチマーク実行」、またはコマンドで行う（結果は `bench/results/` に JSON）。

```bat
run.bat --bench 20,100,200 --quit-after-bench
run.bat --bench 10,24 --render-mode native --quit-after-bench
run.bat --source face.jpg --bench 20,100,200 --quit-after-bench   :: 画像/動画を入力にして再現性のある測定
run.bat --preset "ミラー（顔を再構成）" --set mirror_trails=5 --autostart   :: プリセット・設定を指定して起動
```

実測値（実カメラ、疑似ウィンドウ）：20個 67fps／100個 54fps／200個 33fps、推論は常に約30fps、カメラ→表示の遅延 37〜57ms。詳細は [TECH_DECISIONS.md](TECH_DECISIONS.md)。

## プライバシー

- カメラ映像をディスクへ保存したり外部へ送信したりしない。スナップショット窓・残像・ディレイ窓はメモリ上だけで扱う。
- ネットワークを使うのは `setup.bat` でのパッケージとモデルのダウンロードだけ。

## 既知の制約・未実装

- macOS ではグローバル緊急停止キーなし、カメラ名は番号表示、OS 実ウィンドウの移動は1枚ずつ（Windows の一括移動 API がないため遅くなる可能性がある）。性能は未測定で、ミラー表示・OS 実ウィンドウ方式は未検証。
- macOS の MediaPipe は 0.10 系に固定している（1.0.x は CPU 推論でも Metal サービス未登録のまま落ちる[既知の回帰](https://github.com/google-ai-edge/mediapipe/issues/6356)があるため）。Windows は 1.0.1。
- macOS のカメラ許可は、Python 側に `NSCameraUsageDescription` が無いとダイアログすら出ずに拒否される。`setup.sh` が追記するが、Python を入れ直すと消えるので `setup.sh` を再実行すること。
- 疑似ウィンドウは見た目だけのウィンドウで、クリック・移動・閉じる操作はできない（クリックは背後へ透過）。OS 実ウィンドウは閉じるボタンで個別に閉じられる（アプリは終了しない）。
- ミラー表示を OS 実ウィンドウで使う場合、大きさの変化が 6% 未満ではリサイズしない（リサイズが重いため）ので、疑似ウィンドウより位置・大きさの一致が粗い。窓の回転（顔の傾き）は表現しない。
- OS 実ウィンドウは 20〜30 個程度が実用上限。再利用時にサイズを変えない（映像は中央を切り出して合わせる）。拡大縮小やフェードのアニメーションは疑似ウィンドウのみ。
- 表示は1台のモニターに限る（DISPLAY → 表示モニターで選択）。
- GPU 使用率は計測していない。CPU 使用率は描画プロセス分のみ。
- 描画 FPS の上限は約 67（タイマー 15ms）。300 個以上では 30fps を下回る（自動負荷調整で抑制可能）。
- Hands の左右判定は MediaPipe の推定に依存する。Body/Arms/Hands は既定 OFF。
- 顔が画面外に出ると、保持時間の後に該当する窓は自然に消える（最終映像のまま縮小フェード）。

---

<a id="english"></a>

# FACE WINDOWS (English)

[日本語](#face-windows) | **English**

Interactive art that crops the live webcam feed of your face — the whole face, each eye,
nose and mouth, plus optional body, arms and hands — and multiplies it across the desktop
as **swarms of small windows with title bars** that scatter, drift and follow you.
Every setting can be changed from the CONTROL PANEL on the right without restarting.

Inspired by: https://www.instagram.com/reels/DdjqhjPhkm7/

## Two display modes

| Mode | What it does |
|---|---|
| **Swarm** (default) | Windows are born from each facial part and fly apart, drift, or follow, at wildly varying sizes |
| **Mirror** | Maps the camera image **1:1** onto the desktop, placing each window exactly where that part appears on camera. In the default behaviour (**spawn on drift**) windows never move: every time your face shifts or changes size, a new window is spawned at the new mirrored position, and the windows left behind stay put and fade out — so your movements leave a trail of windows. A face assembled out of windows appears on your desktop, growing larger as you lean in. Nothing flies around on its own (mouth/head BURST reactions are off by default). You can switch to **follow** instead, where windows chase their part, optionally with lagging afterimage windows layered behind |

Switch modes under "表示モード" (Display mode) in the panel. Applying the **ミラー（顔を再構成）**
preset (Mirror / reconstruct the face) loads a matching set of values at once (360px crops,
rotation correction off, and so on).

Mirror settings live in the DISPLAY tab: mirror behaviour (spawn on drift / follow), how far the
face must drift before spawning, how long abandoned windows live and how long they fade,
camera-to-screen fitting (fill while keeping the aspect ratio / fit inside / stretch), window size,
afterimage count, lag and opacity, and crop resolution.

In **follow** mode the detection box is smoothed with a One Euro filter (suppresses jitter when
you hold still, snaps quickly when you move), and the 30fps detections are interpolated by the
60fps renderer (tune via "ブレ補正" in TRACKING and "窓の動きのなめらかさ" in DISPLAY).
The whole desktop including the control panel is mapped 1:1 — the panel stays on top, pseudo
windows are not drawn over it, and real OS windows are stacked underneath it.

> **About rendering**: by default the windows are **pseudo windows** — a single transparent,
> click-through window covering the whole screen, onto which title-barred windows are painted.
> They are not real OS windows. Switch to a small number of **real OS windows**, or a **hybrid**
> of both, under DISPLAY → rendering mode. Rationale and measurements are in
> [TECH_DECISIONS.md](TECH_DECISIONS.md).

## Requirements

- Windows 10/11 (verified)
- macOS 13+ on Apple Silicon (M1 or later) — launch, camera, face detection and window spawning
  verified on an M1 MacBook Air running macOS 26; performance not measured. Intel Macs are not
  supported because MediaPipe's macOS build is Apple Silicon only
- Python 3.11 (the version MediaPipe supports)
- A webcam (on Windows, allow desktop apps under Settings → Privacy & security → Camera)
- Reference machine: Windows 11 / RTX 5070 Laptop / 2880×1800 (150%) / ASUS FHD webcam

## First-time setup

Windows:
```bat
setup.bat
```

macOS (needs Python 3.11, e.g. `brew install python@3.11`):
```bash
./setup.sh
```

This creates `.venv`, installs `requirements.txt`, and downloads the three MediaPipe models into
`models/` (face / pose_lite / hand, about 17MB total, from Google's official distribution).

## Running and quitting

```bat
run.bat
```

On macOS use `./run.sh`. The first launch asks for camera permission (if you decline, allow it
again under System Settings → Privacy & Security → Camera and restart).
macOS only shows that dialog to the frontmost app, so launch it **directly from a terminal** —
started in the background it never gets the prompt and sits at "カメラ停止中" (camera stopped).

1. The control panel opens on the right, showing the camera preview and detection boxes.
2. **▶ START** begins spawning windows (or enable auto-start on launch).
3. **❚❚ PAUSE** pauses, **■ STOP** reclaims every window and releases the camera,
   **↺ RESET** clears all windows.
4. Quit by closing the panel, via "アプリを終了" in the SYSTEM tab, or with `Ctrl+Alt+Q`.

### Emergency stop (works whichever app is in front)

| Key | Action |
|---|---|
| `Ctrl+Alt+S` | STOP (stop spawning, reclaim windows, release the camera) |
| `Ctrl+Alt+Q` | Quit |
| `Ctrl+Alt+P` | Toggle PAUSE |
| `Esc` / `F5` / `B` / `Ctrl+Q` while the panel has focus | STOP / START / BURST / quit |

Global hotkeys are Windows only. On macOS use the STOP button in the panel, or Esc / ⌘Q while
the panel has focus. If another app already owns a hotkey, the failure is shown at the bottom of
the panel — the STOP button and Esc still work. Pseudo windows pass clicks through and are not
drawn over the panel (on by default), so the panel stays usable no matter how many windows there are.

## Control panel

| Section | Contents |
|---|---|
| Always visible | State, rendering mode, detection preview (per-part boxes, HOLD/LOST, motion position), START/PAUSE/STOP/RESET/BURST, per-part on/off, max count, spawn rate, speed, motion mode, window count, input/inference/render FPS, warnings |
| CAMERA | Camera selection (DirectShow names on Windows), resolution, input FPS, horizontal flip, reconnect |
| TRACKING | Face / Left eye / Right eye / Nose / Mouth / Body / Arms / Hands, detection confidence threshold, jitter smoothing (One Euro), rotation correction, hold time when a part is lost, Body/Hands frame skipping, motion on/off and thresholds, BURST on open mouth / head shake |
| GENERATION | Max concurrent windows, spawn rate (per second), BURST size, lifetime and variance, spawn position (around the face / near the part / random / at the motion), scatter, scale, size and variance, share of whole-face windows, share of duplicated parts |
| MOTION | Follow / scatter / random drift / fixed / mixed, speed, follow speed, follow lag variance, scatter, randomness, damping, influence of head movement, screen edges (bounce / wrap / vanish / respawn) |
| DISPLAY | Display mode (swarm / mirror) and mirror settings, rendering mode (pseudo / real / hybrid), real-window cap, target monitor, style (Win11 light / dark / macOS / Win95 / frameless — defaults to match the OS you are running on), opacity, shadow, share of snapshot / delayed / afterimage windows |
| PERF | Window counts (real/pseudo), requested vs actual caps, requested vs actual spawn rate, each FPS, detection time, render time, latency, memory, CPU, adaptive load control, benchmark |
| PRESETS/SYSTEM | Presets (fast swarm / face follow / mirror / random scatter, plus your own), save / load / reset settings, keep panel on top, auto-start, quit |

- Left and right are **from the subject's point of view** (Left eye = the person's own left eye).
  The horizontal flip affects display only.
- Metrics that cannot actually be measured are shown as "未計測" (not measured), e.g. GPU usage.
- Settings are saved to `config/settings.json` and user presets to `config/presets/*.json`
  (only when you press save).

## How it works

```
[detection process]  camera thread (latest frame only) → detection thread (MediaPipe, once per frame)
                      → per-part rotation-corrected crops (BGRA 240px) + motion + mouth opening / head speed
                      ──Pipe (latest only)──▶
[GUI process]        receive thread (to QImage, history for delayed windows) → simulation
                      (spawn / move / lifetime / caps / adaptive control)
                      → pseudo windows batch-painted onto a transparent overlay,
                        or real OS windows batch-moved via DeferWindowPos
                      control panel (settings in a shared dict, applied from the next frame,
                      forwarded to the detection process automatically)
```

| File | Role |
|---|---|
| `facewindows/app.py` | Overall control (START/STOP, main loop, statistics, benchmark) |
| `facewindows/remote.py` | Communication with the detection process |
| `facewindows/camera.py` / `tracker.py` / `geometry.py` | Camera capture, detection and cropping, coordinate math |
| `facewindows/engine.py` | Window spawning, motion, lifetime, adaptive load control |
| `facewindows/render.py` | Pseudo-window painting, real OS window pool |
| `facewindows/panel.py` | Control panel |
| `facewindows/hotkeys.py` | Global hotkeys |
| `bench/window_bench.py` | Standalone benchmark comparing rendering methods |

## Tests and measurement

```bat
.venv\Scripts\python -m pytest -q tests
```

38 tests cover settings validation, left/right and flip coordinates, crop orientation, never
exceeding the cap, spawning nothing with all parts off, windows never escaping the screen,
lifetime and loss-triggered removal, adaptive load control, the real-window cap, mouth BURST,
PAUSE, and for mirror mode the coordinate mapping, per-part windows, afterimage lag, clearing on
loss, and mode switching.

Measure performance from the PERF tab ("負荷ベンチマーク実行") or from the command line; results
are written as JSON to `bench/results/`.

```bat
run.bat --bench 20,100,200 --quit-after-bench
run.bat --bench 10,24 --render-mode native --quit-after-bench
run.bat --source face.jpg --bench 20,100,200 --quit-after-bench   :: reproducible runs from an image/video
run.bat --preset "ミラー（顔を再構成）" --set mirror_trails=5 --autostart   :: start with a preset and overrides
```

Measured on Windows (real camera, pseudo windows): 67fps at 20 windows, 54fps at 100, 33fps at
200; inference holds around 30fps; camera-to-display latency 37–57ms. Details in
[TECH_DECISIONS.md](TECH_DECISIONS.md).

## Privacy

- Camera images are never written to disk or sent anywhere. Snapshot, afterimage and delayed
  windows are held in memory only.
- The only network access is downloading packages and models during setup.

## Known limitations

- On macOS there are no global emergency hotkeys, cameras are listed by number, and real OS
  windows move one at a time (Windows' batch-move API has no equivalent, so it may be slower).
  Performance is unmeasured, and mirror mode and the real-OS-window renderer are unverified there.
- macOS pins MediaPipe to the 0.10 series: 1.0.x has a
  [known regression](https://github.com/google-ai-edge/mediapipe/issues/6356) that crashes even
  under CPU inference because the Metal service is never registered. Windows stays on 1.0.1.
- macOS denies camera access without even showing a dialog unless Python declares
  `NSCameraUsageDescription`. `setup.sh` adds it, but reinstalling Python removes it again — rerun
  `setup.sh` if that happens.
- Pseudo windows only look like windows: they cannot be clicked, moved or closed (clicks pass
  through). Real OS windows can be closed individually with their close button (this does not quit
  the app).
- Using mirror mode with real OS windows will not resize a window for a size change under 6%
  (resizing is expensive), so positions and sizes match less precisely than with pseudo windows.
  Window rotation (head tilt) is not represented.
- Real OS windows top out around 20–30 in practice. Reused windows keep their size (the image is
  centre-cropped to fit). Scaling and fade animations are pseudo-windows only.
- Output is limited to a single monitor (chosen under DISPLAY → target monitor).
- GPU usage is not measured. CPU usage covers the rendering process only.
- Render FPS is capped near 67 (15ms timer). Beyond 300 windows it drops below 30fps (adaptive
  load control can hold it back).
- Handedness for Hands relies on MediaPipe's estimate. Body/Arms/Hands are off by default.
- When your face leaves the frame, its windows disappear naturally after the hold time (shrinking
  and fading with their last image).
