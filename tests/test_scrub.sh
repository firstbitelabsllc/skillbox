#!/usr/bin/env bash
# test_scrub.sh — hermetic audit/pre-block for KEEP-PRIVATE / *-leo promote leaks.
set -uo pipefail
cd "$(dirname "$0")"
source lib/sandbox.sh
sb_setup
trap sb_teardown EXIT
echo "sandbox: $SB_TMP"

# Private-boundary fixtures in the private source.
_sb_mkskill "$SB_TMP/src/private/skills" secret-leo "secret leo overlay"
_sb_mkskill "$SB_TMP/src/private/skills" vault "vault — KEEP-PRIVATE"
printf '\nKEEP-PRIVATE — never promote to shared sources.\n' >> "$SB_TMP/src/private/skills/vault/SKILL.md"

# (1) Full scrub audit lists both private-boundary skills.
out="$(sb_skillbox scrub 2>&1)"; rc=$?
sb_eq   "scrub exits 1 when private boundaries exist" "$rc" "1"
sb_contains "scrub lists secret-leo overlay" "$out" "WOULD-LEAK secret-leo"
sb_contains "scrub lists vault KEEP-PRIVATE" "$out" "WOULD-LEAK vault"
sb_contains "scrub shows skill paths" "$out" "$SB_TMP/src/private/skills/secret-leo/SKILL.md"

# (2) Dry-run is informational only (exit 0) but still lists leaks.
dry="$(sb_skillbox scrub --dry-run 2>&1)"; dry_rc=$?
sb_eq   "scrub --dry-run exits 0" "$dry_rc" "0"
sb_contains "dry-run lists secret-leo" "$dry" "WOULD-LEAK secret-leo"

# (3) Targeted scrub for a clean skill is clean.
clean="$(sb_skillbox scrub gamma --to team 2>&1)"; clean_rc=$?
sb_eq   "scrub gamma --to team exits 0 (no boundary)" "$clean_rc" "0"
sb_contains "scrub clean for promotable gamma" "$clean" "scrub: clean"

# (4) Promote guard: private-boundary skills refused; gamma still promotable.
sb_ok   "add gamma" sb_skillbox add gamma
sb_fails "promote secret-leo --to team blocked" sb_skillbox promote secret-leo --to team
sb_fails "promote vault --to team blocked"       sb_skillbox promote vault --to team
sb_ok   "promote gamma --to team still allowed"  sb_skillbox promote gamma --to team

# (5) doctor scrub alias matches scrub.
doc_out="$(sb_skillbox doctor scrub --dry-run 2>&1)"; doc_rc=$?
sb_eq   "doctor scrub --dry-run exits 0" "$doc_rc" "0"
sb_contains "doctor scrub lists vault" "$doc_out" "WOULD-LEAK vault"

# (6) org off-ramp also blocked for private boundaries (restore gamma to private first).
sb_ok "promote gamma back to private" sb_skillbox promote gamma --to private
export SKILLBOX_ORG_REPO="example-org/skill-marketplace"
sb_fails "promote secret-leo --to org blocked" sb_skillbox promote secret-leo --to org
unset SKILLBOX_ORG_REPO

sb_report
