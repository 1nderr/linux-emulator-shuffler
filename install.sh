#!/usr/bin/env bash
# Install the built binary plus a desktop entry, so the shuffler shows up in the
# application menu and in search (KRunner, GNOME Activities, rofi, ...).
#
#   ./install.sh              build if needed, then install for the current user
#   ./install.sh --rebuild    force a fresh build first
set -euo pipefail

cd "$(dirname "$0")"

APP_NAME="linux-emulator-shuffler"
BIN_DIR="$HOME/.local/bin"
LIB_DIR="$HOME/.local/share/$APP_NAME"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"

[[ "${1:-}" == "--rebuild" ]] && rm -rf dist build

if [[ ! -e "dist/$APP_NAME" ]]; then
    echo "No build found; building first."
    ./build.sh
fi

mkdir -p "$BIN_DIR" "$DESKTOP_DIR" "$ICON_DIR"

if [[ -d "dist/$APP_NAME" ]]; then
    # onedir build: keep the tree together and link the launcher into PATH.
    rm -rf "$LIB_DIR"
    mkdir -p "$LIB_DIR"
    cp -a "dist/$APP_NAME/." "$LIB_DIR/"
    TARGET="$LIB_DIR/$APP_NAME"
    ln -sf "$TARGET" "$BIN_DIR/$APP_NAME"
else
    # onefile build: the binary is self-contained.
    install -m 755 "dist/$APP_NAME" "$BIN_DIR/$APP_NAME"
    TARGET="$BIN_DIR/$APP_NAME"
fi

install -m 644 "packaging/$APP_NAME.svg" "$ICON_DIR/$APP_NAME.svg"

# The desktop spec does not expand $HOME, so bake in the absolute path.
sed "s|@EXEC@|$TARGET|" "packaging/$APP_NAME.desktop" > "$DESKTOP_DIR/$APP_NAME.desktop"
chmod 644 "$DESKTOP_DIR/$APP_NAME.desktop"

command -v update-desktop-database >/dev/null && update-desktop-database "$DESKTOP_DIR" || true
command -v gtk-update-icon-cache   >/dev/null && gtk-update-icon-cache -qtf "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
command -v kbuildsycoca6           >/dev/null && kbuildsycoca6 --noincremental >/dev/null 2>&1 || true

echo
echo "Installed:"
echo "  binary   $TARGET"
echo "  launcher $BIN_DIR/$APP_NAME"
echo "  entry    $DESKTOP_DIR/$APP_NAME.desktop"
echo "  icon     $ICON_DIR/$APP_NAME.svg"
echo
echo "Search for \"Emulator Shuffler\" in your application launcher."
echo "Remove it again with ./uninstall.sh"
