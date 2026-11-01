#!/usr/bin/env bash
# Install Sthanik for the current user (no root needed), on any Linux desktop:
#   ./install.sh                       install (or update) Sthanik
#   sthanik-uninstall                  remove it later; this command stays installed, you do not need this folder any more
#   sthanik-uninstall --purge          also delete your chats, documents, pictures and downloaded models
# Your data lives in ~/.local/share/sthanik and is kept unless you ask for --purge.
set -euo pipefail

data="${XDG_DATA_HOME:-$HOME/.local/share}"
opt="$data/sthanik-app"          # the program
bin="$HOME/.local/bin"           # the sthanik and sthanik-uninstall commands
apps="$data/applications"        # the menu entry
icons="$data/icons/hicolor"      # the menu icon, by name, in every size
mine="$data/sthanik"             # chats, documents, models: never touched unless --purge

# Tell the desktop about the change. Each step is optional: whichever of these this system has is used.
refresh() {
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$apps" >/dev/null 2>&1 || true
  if command -v xdg-icon-resource >/dev/null 2>&1; then xdg-icon-resource forceupdate >/dev/null 2>&1 || true; fi
  [ -f "$icons/index.theme" ] && command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -f -t "$icons" >/dev/null 2>&1 || true
  for k in kbuildsycoca6 kbuildsycoca5; do  # KDE keeps its own menu cache
    if command -v "$k" >/dev/null 2>&1; then "$k" >/dev/null 2>&1 || true; break; fi
  done
}

uninstall() {
  local purge="$1"
  rm -f "$bin/sthanik" "$bin/sthanik-uninstall" "$apps/sthanik.desktop" "$icons/scalable/apps/sthanik.svg"
  for n in 48 64 128 256 512; do rm -f "$icons/${n}x${n}/apps/sthanik.png"; done
  rm -rf "$opt"
  refresh
  echo "Sthanik is removed."
  if [ "$purge" = 1 ]; then
    if [ -d "$mine" ]; then
      local size; size="$(du -sh "$mine" 2>/dev/null | cut -f1)"
      if [ -t 0 ] && [ "${STHANIK_YES:-}" != 1 ]; then
        read -r -p "Delete $mine ($size: your chats, documents, pictures and models)? Type yes to confirm: " ans
        [ "$ans" = "yes" ] || { echo "Kept $mine."; return 0; }
      fi
      rm -rf "$mine"; echo "Deleted $mine."
    fi
  elif [ -d "$mine" ]; then
    echo "Your data is still in $mine ($(du -sh "$mine" 2>/dev/null | cut -f1)). Run  sthanik-uninstall --purge  (or delete that folder) to remove it too."
  fi
}

# Run as sthanik-uninstall, or with --uninstall
case "$(basename "$0")" in sthanik-uninstall) mode=uninstall;; *) mode=install;; esac
purge=0
for a in "$@"; do
  case "$a" in
    --uninstall) mode=uninstall;;
    --purge) purge=1;;
    --yes) export STHANIK_YES=1;;
    -h|--help) sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "Unknown option: $a" >&2; exit 2;;
  esac
done

if [ "$mode" = uninstall ]; then
  # This script lives inside the folder it is about to delete: run a copy of it instead.
  if [ -z "${STHANIK_UNINSTALL_COPY:-}" ]; then
    tmp="$(mktemp)"; cp "$(readlink -f "$0")" "$tmp"
    STHANIK_UNINSTALL_COPY=1 exec bash "$tmp" --uninstall $([ "$purge" = 1 ] && echo --purge) $([ "${STHANIK_YES:-}" = 1 ] && echo --yes)
  fi
  uninstall "$purge"; rm -f "$0"; exit 0
fi

# The program and the AI engine it downloads are built for glibc 2.35 or newer (Ubuntu 22.04, Debian 12, Fedora 36, ...).
need_glibc="2.35"
case "$(uname -m)" in x86_64|aarch64|arm64) ;; *) echo "Sthanik has Linux builds for x86-64 and arm64; this machine is $(uname -m)." >&2; exit 1;; esac
if [ "${STHANIK_SKIP_CHECK:-}" != 1 ]; then
  have="$(getconf GNU_LIBC_VERSION 2>/dev/null || true)"; have="${have#glibc }"   # shell only: minimal distributions have no awk
  if [ -z "$have" ]; then
    echo "This system does not use the GNU C library (Alpine and similar). Sthanik needs a glibc-based distribution such as Ubuntu, Debian, Fedora, Arch or openSUSE." >&2; exit 1
  fi
  if [ "$(printf '%s\n%s\n' "$need_glibc" "$have" | sort -V | head -n1)" != "$need_glibc" ]; then
    echo "This system has glibc $have, and Sthanik needs $need_glibc or newer (Ubuntu 22.04, Debian 12, Fedora 36 or newer). Nothing was installed." >&2
    echo "(To try anyway: STHANIK_SKIP_CHECK=1 ./install.sh)" >&2; exit 1
  fi
fi

here="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
[ -x "$here/sthanik/sthanik" ] || { echo "Run this from the folder you extracted (it must contain the 'sthanik' folder)." >&2; exit 1; }

mkdir -p "$bin" "$apps" "$icons/scalable/apps"
rm -rf "$opt"; mkdir -p "$opt"
cp -a "$here/sthanik/." "$opt/"
cp "$here/install.sh" "$opt/install.sh"; chmod +x "$opt/install.sh"
ln -sf "$opt/sthanik" "$bin/sthanik"
ln -sf "$opt/install.sh" "$bin/sthanik-uninstall"
for n in 48 64 128 256 512; do
  mkdir -p "$icons/${n}x${n}/apps"; cp "$here/icons/sthanik-$n.png" "$icons/${n}x${n}/apps/sthanik.png"
done
cp "$here/icons/sthanik.svg" "$icons/scalable/apps/sthanik.svg"
cat > "$apps/sthanik.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Sthanik
GenericName=Local AI studio
Comment=Chat, vision, voice and documents with LFM3 models, on your own computer
Exec="$opt/sthanik"
Icon=sthanik
Terminal=false
Categories=Utility;
Keywords=AI;LLM;chat;assistant;local;
StartupNotify=false
DESKTOP
refresh
echo "Installed. Start it from your applications menu, or run: sthanik"
echo "To remove it later: sthanik-uninstall   (your chats and models are kept unless you add --purge)"
case ":$PATH:" in *":$bin:"*) ;; *) echo "(add $bin to your PATH to use these commands in a terminal)";; esac
