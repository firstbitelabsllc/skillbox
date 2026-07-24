#!/usr/bin/env bash
# test_path_shadow.sh — doctor PATH-SHADOW for npm/Homebrew name collision.
set -uo pipefail
cd "$(dirname "$0")"
source lib/sandbox.sh
sb_setup
trap sb_teardown EXIT
echo "sandbox: $SB_TMP"

mkdir -p "$SB_TMP/fakebin"
printf '#!/bin/sh\necho fake-npm-skillbox\n' > "$SB_TMP/fakebin/skillbox"
chmod +x "$SB_TMP/fakebin/skillbox"

# Keep the real python on PATH; inject a peer skillbox ahead of it.
export PATH="$SB_TMP/fakebin:$PATH"
out="$(sb_skillbox doctor 2>&1)"; rc=$?
sb_eq "PATH-SHADOW does not block doctor" "$rc" "0"
sb_contains "PATH-SHADOW kind printed" "$out" "PATH-SHADOW"
sb_contains "PATH-SHADOW names the peer" "$out" "$SB_TMP/fakebin/skillbox"
sb_contains "summary counts PATH-SHADOW" "$out" "PATH-SHADOW(s)"

jout="$(sb_skillbox doctor --json 2>&1)"; jrc=$?
sb_eq "PATH-SHADOW json doctor exit 0" "$jrc" "0"
sb_contains "json includes PATH-SHADOW kind" "$jout" '"kind": "PATH-SHADOW"'

# Self-ignore is covered in test_unit.py (other_skillbox_on_path).

sb_report
