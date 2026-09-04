#!/usr/bin/env bash
# Remove everything install.sh created for the current user.
set -euo pipefail

APP_NAME="linux-emulator-shuffler"

rm -rf  "$HOME/.local/share/$APP_NAME"
rm -f   "$HOME/.local/bin/$APP_NAME"
rm -f   "$HOME/.local/share/applications/$APP_NAME.desktop"
rm -f   "$HOME/.local/share/icons/hicolor/scalable/apps/$APP_NAME.svg"

command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" || true
command -v kbuildsycoca6           >/dev/null && kbuildsycoca6 --noincremental >/dev/null 2>&1 || true

echo "Removed the Linux Emulator Shuffler binary, launcher entry, and icon."
