#!/usr/bin/env bash
# Run the full skillbox test suite (all hermetic — never touches the real fleet).
# Exits nonzero if any test file fails. Usage: bash tests/run_all.sh
cd "$(dirname "$0")"
fail=0 ran=0

# The suite needs stdlib tomllib (Python 3.11+). A user-site tomli is not
# enough: tests run skillbox under a sandboxed HOME, where it disappears.
# Use python3 when it qualifies, so a CI or venv interpreter is what gets
# tested; else try python3.12, then python3.11.
# Shell tests call `python3` by name, so the chosen interpreter is placed
# first on PATH under that name.
pick_python() {
  local c bin
  for c in python3 python3.12 python3.11; do
    bin=$(command -v "$c" 2>/dev/null) || continue
    if "$bin" -c 'import tomllib' 2>/dev/null; then
      printf '%s\n' "$bin"
      return 0
    fi
  done
  return 1
}

PY=$(pick_python) || {
  echo 'skillbox tests need Python 3.11+ (stdlib tomllib) as python3, python3.12 or python3.11' >&2
  exit 1
}
printf 'python3 -> %s (%s)\n' "$PY" "$("$PY" -c 'import sys; print(sys.version.split()[0])')"

shim=$(mktemp -d)
ln -s "$PY" "$shim/python3"
export PATH="$shim:$PATH"
trap 'rm -rf "$shim"' EXIT

for t in test_*.sh; do
  [ "$t" = "run_all.sh" ] && continue
  printf '\n════════ %s ════════\n' "$t"
  ran=$((ran + 1))
  bash "$t" || { fail=$((fail + 1)); printf '*** %s FAILED ***\n' "$t"; }
done

if [ -f test_unit.py ]; then
  printf '\n════════ test_unit.py ════════\n'
  ran=$((ran + 1))
  python3 test_unit.py || { fail=$((fail + 1)); printf '*** test_unit.py FAILED ***\n'; }
fi

printf '\n────────────────────────────\n'
if [ "$fail" -eq 0 ]; then
  printf 'SUITE: ALL GREEN (%d test files)\n' "$ran"
else
  printf 'SUITE: %d of %d test file(s) FAILED\n' "$fail" "$ran"
fi
exit "$fail"
