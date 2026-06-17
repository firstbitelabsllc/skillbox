#!/usr/bin/env bash
# Run the full skillbox test suite (all hermetic — never touches the real fleet).
# Exits nonzero if any test file fails. Usage: bash tests/run_all.sh
cd "$(dirname "$0")"
fail=0 ran=0

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
