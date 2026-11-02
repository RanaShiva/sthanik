#!/usr/bin/env bash
# install.sh must close a running Sthanik before it replaces the program's folder (a copy left running reads the new files at the old files'
# positions and fails with "unknown compression method"), and must change nothing if that copy will not close.
set -euo pipefail
here="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
t="$(mktemp -d)"; trap 'kill -9 $(jobs -p) 2>/dev/null || true; rm -rf "$t"' EXIT
mkdir -p "$t/pkg/sthanik" "$t/pkg/icons" "$t/home"
cp "$here/install.sh" "$t/pkg/install.sh"; cp "$(command -v sleep)" "$t/pkg/sthanik/sthanik"
for n in 48 64 128 256 512; do : > "$t/pkg/icons/sthanik-$n.png"; done; : > "$t/pkg/icons/sthanik.svg"
export HOME="$t/home" XDG_DATA_HOME="$t/home/.local/share" STHANIK_SKIP_CHECK=1
app="$XDG_DATA_HOME/sthanik-app"
fail() { echo "FAIL: $1" >&2; exit 1; }

# 1. a running copy is closed, then the update goes through
mkdir -p "$app"; cp "$(command -v sleep)" "$app/sthanik"; echo old > "$app/marker"
"$app/sthanik" 300 & pid=$!
sleep 0.5
bash "$t/pkg/install.sh" > "$t/out1" 2>&1 || { cat "$t/out1"; fail "the update did not finish"; }
grep -q "closing it before the update" "$t/out1" || fail "it did not say it was closing Sthanik"
kill -0 "$pid" 2>/dev/null && fail "the old copy is still running"
[ ! -e "$app/marker" ] || fail "the old files are still there"
echo "ok   a running copy is closed and the update goes through"

# 2. a copy that will not close: nothing is changed, and the exit status says so
if command -v cc >/dev/null 2>&1; then
  printf '#include <signal.h>\n#include <unistd.h>\nint main(){signal(SIGTERM,SIG_IGN);sleep(120);return 0;}\n' > "$t/stubborn.c"
  cc -o "$t/stubborn" "$t/stubborn.c"
  rm -rf "$app"; mkdir -p "$app"; cp "$t/stubborn" "$app/sthanik"; echo old > "$app/marker"
  "$app/sthanik" & pid=$!
  sleep 0.5
  if bash "$t/pkg/install.sh" > "$t/out2" 2>&1; then fail "the update went ahead over a running copy"; fi
  grep -q "Nothing was changed" "$t/out2" || fail "it did not say nothing was changed"
  [ "$(cat "$app/marker")" = old ] || fail "files were replaced"
  echo "ok   a copy that will not close stops the update, with nothing changed"
else
  echo "skip a copy that will not close (no C compiler here)"
fi
