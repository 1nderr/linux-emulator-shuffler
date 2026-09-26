#!/usr/bin/env bash
# Bundle the shuffler into a self-contained binary under dist/.
#
#   ./build.sh              one directory, fast startup (default)
#   ./build.sh --onefile    a single file, slower startup (extracts on each run)
set -euo pipefail

cd "$(dirname "$0")"

APP_NAME="linux-emulator-shuffler"
MODE="onedir"
[[ "${1:-}" == "--onefile" ]] && MODE="onefile"

# PyInstaller is in the dev dependency group, which uv syncs by default.
uv sync --locked

echo "Building $APP_NAME ($MODE) ..."
uv run --locked python -m PyInstaller \
    --noconfirm --clean \
    --name "$APP_NAME" \
    --"$MODE" \
    --windowed \
    --exclude-module tkinter \
    --exclude-module PySide6.QtWebEngineCore \
    --exclude-module PySide6.QtWebEngineWidgets \
    --exclude-module PySide6.QtQuick \
    --exclude-module PySide6.QtQml \
    --exclude-module PySide6.Qt3DCore \
    --exclude-module PySide6.QtMultimedia \
    --exclude-module PySide6.QtCharts \
    --exclude-module PySide6.QtDataVisualization \
    main.py

# Some Python builds (mise, pyenv) produce a libpython marked as needing an
# executable stack, which hardened kernels refuse to load. Clear it on the copy
# PyInstaller just made, or the bundle dies at startup.
echo
echo "Checking bundled objects for executable-stack markers ..."
uv run --locked python packaging/fix_execstack.py dist

if [[ "$MODE" == "onefile" ]]; then
    BINARY="dist/$APP_NAME"
else
    BINARY="dist/$APP_NAME/$APP_NAME"
fi

echo
echo "Built: $BINARY"
du -sh "$(dirname "$BINARY")" | awk '{print "Size:  " $1}'
echo
echo "Run it with:      $BINARY"
echo "Add to the menu:  ./install.sh"
