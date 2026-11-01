#!/usr/bin/env bash
# Install Sthanik for the current user (no root needed):  ./install.sh      Remove it:  ./install.sh --uninstall
# Your chats, documents and models live in ~/.local/share/sthanik and are never touched by either.
set -euo pipefail
here="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
opt="${XDG_DATA_HOME:-$HOME/.local/share}/sthanik-app"
bin="$HOME/.local/bin"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
icons="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/512x512/apps"

if [ "${1:-}" = "--uninstall" ]; then
  rm -rf "$opt" "$bin/sthanik" "$apps/sthanik.desktop" "$icons/sthanik.png"
  echo "Sthanik removed. Your data is still in ${XDG_DATA_HOME:-$HOME/.local/share}/sthanik (delete that folder to remove it too)."
  exit 0
fi

mkdir -p "$opt" "$bin" "$apps" "$icons"
rm -rf "$opt"/*
cp -a "$here/sthanik/." "$opt/"
ln -sf "$opt/sthanik" "$bin/sthanik"
cp "$here/sthanik.png" "$icons/sthanik.png"
cat > "$apps/sthanik.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Sthanik
GenericName=Local AI studio
Comment=Chat, vision, voice and documents with LFM3 models, on your own computer
Exec=$opt/sthanik
Icon=$icons/sthanik.png
Terminal=false
Categories=Utility;
StartupNotify=false
DESKTOP
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$apps" >/dev/null 2>&1 || true
echo "Installed. Start it from your applications menu, or run: sthanik"
case ":$PATH:" in *":$bin:"*) ;; *) echo "(add $bin to your PATH to run it from a terminal)";; esac
