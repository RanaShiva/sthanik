#!/usr/bin/env bash
# Try a built Linux package in other distributions (needs podman or docker):
#   packaging/linux/test-distros.sh Sthanik-<version>-linux-<arch>.tar.gz
# For each distribution it checks that the program starts and passes its own self-test, and that install.sh installs and uninstalls cleanly.
# Distributions older than glibc 2.35 must be refused by install.sh with a clear message instead.
set -u
tarball="$(readlink -f "${1:?usage: test-distros.sh <Sthanik-...-linux-<arch>.tar.gz>}")"
rt="$(command -v podman || command -v docker || true)"; [ -n "$rt" ] || { echo "Needs podman or docker." >&2; exit 2; }
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
tar -xzf "$tarball" -C "$work"; pkg="$(find "$work" -maxdepth 1 -mindepth 1 -type d | head -1)"
supported=(ubuntu:22.04 ubuntu:24.04 debian:12 fedora:latest archlinux:latest registry.opensuse.org/opensuse/tumbleweed:latest)
refused=(ubuntu:20.04 debian:11 rockylinux:9)   # glibc older than 2.35
inside_ok='
  set -e
  /pkg/sthanik/sthanik --version
  STHANIK_HOME=/tmp/data /pkg/sthanik/sthanik --selftest | tail -1
  cp -r /pkg /tmp/pkg && export HOME=/tmp/home && mkdir -p $HOME && cd /tmp/pkg && ./install.sh >/dev/null
  test -x $HOME/.local/share/sthanik-app/sthanik && test -f $HOME/.local/share/applications/sthanik.desktop && test -f $HOME/.local/share/icons/hicolor/256x256/apps/sthanik.png
  cd / && rm -rf /tmp/pkg               # the extracted folder is gone; uninstalling must still work
  mkdir -p $HOME/.local/share/sthanik && echo keep > $HOME/.local/share/sthanik/data.txt
  $HOME/.local/bin/sthanik-uninstall >/dev/null
  test ! -e $HOME/.local/share/sthanik-app && test ! -e $HOME/.local/share/applications/sthanik.desktop && test -f $HOME/.local/share/sthanik/data.txt
  echo "installed, uninstalled, data kept"'
inside_refused='cp -r /pkg /tmp/pkg && cd /tmp/pkg && HOME=/tmp/h ./install.sh 2>&1 | head -1; test ${PIPESTATUS[0]} -ne 0 && echo "refused as expected"'
img() { case "$1" in */*) echo "$1";; *) echo "docker.io/library/$1";; esac; }
bad=0
for i in "${supported[@]}"; do
  case "$i" in archlinux*) [ "$(uname -m)" = x86_64 ] || { echo "SKIPPED $i (there is no arm64 image)"; continue; };; esac
  out="$("$rt" run --rm -v "$pkg":/pkg:ro,Z "$(img "$i")" bash -c "$inside_ok" 2>&1 | tail -3 | tr '\n' ' ')"
  case "$out" in *"selftest ok"*"data kept"*) echo "OK      $i";; *) echo "FAIL    $i  $out"; bad=1;; esac
done
for i in "${refused[@]}"; do
  out="$("$rt" run --rm -v "$pkg":/pkg:ro,Z "$(img "$i")" bash -c "$inside_refused" 2>&1 | tail -2 | tr '\n' ' ')"
  case "$out" in *"needs 2.35 or newer"*"refused as expected"*) echo "REFUSED $i (glibc too old, as expected)";; *) echo "FAIL    $i  $out"; bad=1;; esac
done
exit $bad
