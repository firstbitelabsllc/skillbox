#!/usr/bin/env bash
# Hermetic focused coverage for `skillbox --version`.
# Proves the version path works with no home config, no manifest, and never
# touches the real agent fleet.
set -uo pipefail
cd "$(dirname "$0")"
# shellcheck source=lib/sandbox.sh
source lib/sandbox.sh

# Intentionally do NOT call sb_setup. Point HOME (and any default manifest
# location derived from it) at a nonexistent tree so skillbox has no config.
export HOME="${TMPDIR:-/tmp}/skillbox-version-nohome-$$"
unset SKILLBOX_MANIFEST
# Guard: if HOME somehow already exists as a real config dir, refuse rather
# than risk reading outside the hermetic intent of this test.
if [ -e "$HOME" ]; then
  echo "test_version: refusing to run — unexpected HOME already exists: $HOME" >&2
  exit 2
fi

out="$(python3 "$SKILLBOX_BIN" --version 2>&1)"
rc=$?

sb_eq "--version exits 0 with no home config" "$rc" "0"
sb_eq "--version prints exact identity line" "$out" "skillbox 1.0.0"

# Negative: still no fleet/home side effects after the call.
sb_eq "no HOME tree created by --version" \
  "$([ -e "$HOME" ] && echo present || echo absent)" "absent"

sb_report
