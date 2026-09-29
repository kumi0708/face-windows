#!/bin/bash
# macOS 用に FACE WINDOWS.app を作る。
#
# なぜ必要か：Homebrew などの Python は org.python.python という共用の識別子しか持たず、
# 汎用インタプリタとして扱われるため、macOS はカメラ許可のダイアログを出さず、
# 「プライバシーとセキュリティ → カメラ」の一覧にも載せてくれない。
# 専用の識別子とカメラ利用の説明文を持つ .app を作り、その中の実行ファイルとして動かすと、
# 「FACE WINDOWS」として許可を求められ、一覧で ON/OFF できるようになる。
set -e
cd "$(dirname "$0")"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "macOS 専用です。"; exit 1
fi
if [ ! -x .venv/bin/python ]; then
  echo "先に ./setup.sh を実行してください。"; exit 1
fi

APP="build/FACE WINDOWS.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

# バンドルの実行ファイルは、プロジェクトの python を起動するだけのスクリプト。
# open（LaunchServices）で起動すると、この .app が「責任を持つプロセス」になり、
# 子の python が出すカメラ要求も FACE WINDOWS のものとして扱われる。
cat > "$APP/Contents/MacOS/facewindows" <<'LAUNCHER'
#!/bin/bash
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"
exec .venv/bin/python main.py "$@" >> "$ROOT/build/facewindows.log" 2>&1
LAUNCHER
chmod +x "$APP/Contents/MacOS/facewindows"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleExecutable</key>
	<string>facewindows</string>
	<key>CFBundleIdentifier</key>
	<string>com.facewindows.app</string>
	<key>CFBundleName</key>
	<string>FACE WINDOWS</string>
	<key>CFBundleDisplayName</key>
	<string>FACE WINDOWS</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleShortVersionString</key>
	<string>1.0</string>
	<key>CFBundleVersion</key>
	<string>1.0</string>
	<key>LSMinimumSystemVersion</key>
	<string>13.0</string>
	<key>NSHighResolutionCapable</key>
	<true/>
	<key>NSCameraUsageDescription</key>
	<string>顔を検出して、その部位をデスクトップ上の窓として表示するために使用します。</string>
</dict>
PLIST
echo "</plist>" >> "$APP/Contents/Info.plist"

codesign --force --sign - "$APP"
codesign -v "$APP" && echo "署名を確認しました。"
echo "作成しました: $APP"
echo "./run.sh で起動すると、この .app 経由で動きます。"
