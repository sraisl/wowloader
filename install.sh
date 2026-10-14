#!/bin/sh
# Installiert den Downloader als launchd-Job (Mo–Fr 16:00 und beim Login).
set -e

LABEL=at.orf.wow-downloader
DIR=$(cd "$(dirname "$0")" && pwd)
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON=$(command -v python3)
LOG="$HOME/Library/Logs/wow-downloader.log"

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
sed -e "s|__PYTHON__|$PYTHON|" -e "s|__SCRIPT__|$DIR/wow_downloader.py|" -e "s|__LOG__|$LOG|g" \
    "$DIR/$LABEL.plist" > "$PLIST"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installiert: $PLIST"
echo "Log: $LOG"
