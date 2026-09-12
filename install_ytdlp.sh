#!/bin/sh
set -eu
BASE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
BIN="$BASE/bin/yt-dlp"
mkdir -p "$BASE/bin"
URL="https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
if command -v curl >/dev/null 2>&1; then
  curl -fL "$URL" -o "$BIN"
elif command -v wget >/dev/null 2>&1; then
  wget -O "$BIN" "$URL"
else
  echo "curl 또는 wget이 필요합니다." >&2
  exit 1
fi
chmod 755 "$BIN"
"$BIN" --version
printf '%s\n' "yt-dlp 설치 완료: $BIN"
