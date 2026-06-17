#!/usr/bin/env bash
# Scenario: drift_taxonomy
# Exhaustive doctor drift taxonomy via `doctor --json`. We induce EACH problem
# kind independently and assert it shows up (and the others that should NOT) by
# filtering the JSON on (kind, where). We also pin down the exit-code contract:
# nonzero whenever a blocking problem exists, 0 when only SHADOWED remains.
#
# Hermetic: everything runs inside $SB_TMP via the sandbox manifest. We NEVER
# touch the real fleet. trap sb_teardown EXIT guarantees cleanup.
set -u
HERE="$(cd "$(dirname "$0")" && pwd -P)"
# shellcheck source=lib/sandbox.sh
. "$HERE/lib/sandbox.sh"
trap sb_teardown EXIT

# ── JSON helpers (python3 stdlib; no jq dependency) ──────────────────────────
# Print the doctor JSON for the current fleet.
doctor_json() { sb_skillbox doctor --json 2>/dev/null; }

# Count problems in JSON ($1) matching kind=$2 and (optional) where-substring=$3.
# Prints an integer; usable as an actual value for sb_eq. JSON arrives on stdin
# (here-string) so the program body and the data never share a channel.
jq_count() { # json kind [where_substr]
  printf '%s' "$1" | python3 -c '
import sys, json
kind = sys.argv[1]; where_sub = sys.argv[2]
data = json.load(sys.stdin)
n = sum(1 for p in data.get("problems", [])
        if p["kind"] == kind and (not where_sub or where_sub in p["where"]))
print(n)
' "$2" "${3:-}"
}

# Read a scalar field (blocking | skills_installed) out of the JSON.
jq_field() { # json field
  printf '%s' "$1" | python3 -c '
import sys, json
print(json.load(sys.stdin)[sys.argv[1]])
' "$2"
}

# parity.<name>.consistent  → "true"/"false"
jq_parity_consistent() { # json name
  printf '%s' "$1" | python3 -c '
import sys, json
d = json.load(sys.stdin)
print(str(d["parity"][sys.argv[1]]["consistent"]).lower())
' "$2"
}

echo "=== drift_taxonomy ==="

# =============================================================================
# CASE 1 — BROKEN: a root link is a symlink whose target no longer exists.
# Induce: install alpha everywhere, then repoint ONE root's alpha link at a
# throwaway dir and delete that dir so the link dangles.
# =============================================================================
sb_setup
sb_ok    "case1 add alpha everywhere"        sb_skillbox add alpha
# sanity: alpha link in claude points at the team source dir (real target).
sb_link  "case1 alpha->team/skills/alpha"    "$SB_TMP/roots/claude/alpha" "$SB_TMP/src/team/skills/alpha"

# Build a real skill dir, point cursor/alpha at it, then delete it → dangling.
mkdir -p "$SB_TMP/decoy/alpha"
printf -- '---\nname: alpha\ndescription: decoy\n---\n# alpha\n' > "$SB_TMP/decoy/alpha/SKILL.md"
rm "$SB_TMP/roots/cursor/alpha"
ln -s "$SB_TMP/decoy/alpha" "$SB_TMP/roots/cursor/alpha"
rm -rf "$SB_TMP/decoy"          # target gone → cursor/alpha is now BROKEN

J="$(doctor_json)"
sb_eq    "case1 BROKEN cursor/alpha == 1"    "$(jq_count "$J" BROKEN cursor/alpha)" 1
# A dangling symlink is NOT counted as MISSING (it is still a symlink).
sb_eq    "case1 no MISSING for cursor/alpha" "$(jq_count "$J" MISSING cursor/alpha)" 0
# A non-existent target short-circuits before the DRIFTED check.
sb_eq    "case1 no DRIFTED for cursor/alpha" "$(jq_count "$J" DRIFTED cursor/alpha)" 0
# Blocking present → doctor exits nonzero.
sb_fails "case1 doctor nonzero (BROKEN blocks)" sb_skillbox doctor --json
sb_teardown

# =============================================================================
# CASE 2 — MISSING: an installed skill is absent from one root only.
# Induce: install beta everywhere, then delete agents/beta entirely.
# =============================================================================
sb_setup
sb_ok    "case2 add beta everywhere"         sb_skillbox add beta
sb_link  "case2 beta->team/skills/beta"      "$SB_TMP/roots/agents/beta" "$SB_TMP/src/team/skills/beta"
rm "$SB_TMP/roots/agents/beta"               # agents has no beta now → MISSING

J="$(doctor_json)"
sb_eq    "case2 MISSING agents/beta == 1"    "$(jq_count "$J" MISSING agents/beta)" 1
# Only one root is missing it; the other three still have a healthy symlink.
sb_eq    "case2 no MISSING claude/beta"      "$(jq_count "$J" MISSING claude/beta)" 0
sb_eq    "case2 no BROKEN for beta"          "$(jq_count "$J" BROKEN beta)" 0
sb_fails "case2 doctor nonzero (MISSING blocks)" sb_skillbox doctor --json
sb_teardown

# =============================================================================
# CASE 3 — DRIFTED (without PARITY): a root link resolves to a NON-winner dir
# whose SKILL.md bytes are IDENTICAL to the winner, so it drifts in path but
# parity hashes still match across roots.
# Induce: install alpha everywhere; make a byte-identical copy of the alpha
# source dir at a different path; repoint codex/alpha at the copy.
# =============================================================================
sb_setup
sb_ok    "case3 add alpha everywhere"        sb_skillbox add alpha
mkdir -p "$SB_TMP/twin/alpha"
cp "$SB_TMP/src/team/skills/alpha/SKILL.md" "$SB_TMP/twin/alpha/SKILL.md"  # identical bytes
rm "$SB_TMP/roots/codex/alpha"
ln -s "$SB_TMP/twin/alpha" "$SB_TMP/roots/codex/alpha"   # exists, != winner path

J="$(doctor_json)"
sb_eq    "case3 DRIFTED codex/alpha == 1"    "$(jq_count "$J" DRIFTED codex/alpha)" 1
# Identical bytes → no PARITY problem and parity stays consistent.
sb_eq    "case3 no PARITY for alpha"         "$(jq_count "$J" PARITY alpha)" 0
sb_eq    "case3 parity.alpha consistent"     "$(jq_parity_consistent "$J" alpha)" true
sb_eq    "case3 no BROKEN for alpha"         "$(jq_count "$J" BROKEN alpha)" 0
sb_fails "case3 doctor nonzero (DRIFTED blocks)" sb_skillbox doctor --json
sb_teardown

# =============================================================================
# CASE 4 — PARITY: one root's link resolves to a copy whose SKILL.md bytes
# DIFFER, so the cross-root hash set has >1 distinct value.
# Induce: install gamma everywhere; copy gamma dir but MUTATE its SKILL.md;
# repoint claude/gamma at the mutated copy.
# (PARITY co-occurs with DRIFTED here by construction — different bytes implies
# a different path. We assert PARITY on THIS skill and keep our DRIFTED-only
# assertion on a separate skill in case 3.)
# =============================================================================
sb_setup
sb_ok    "case4 add gamma everywhere"        sb_skillbox add gamma
mkdir -p "$SB_TMP/fork/gamma"
printf -- '---\nname: gamma\ndescription: MUTATED bytes\n---\n# gamma\nDIFFERENT\n' \
  > "$SB_TMP/fork/gamma/SKILL.md"
rm "$SB_TMP/roots/claude/gamma"
ln -s "$SB_TMP/fork/gamma" "$SB_TMP/roots/claude/gamma"

J="$(doctor_json)"
sb_eq    "case4 PARITY gamma == 1"           "$(jq_count "$J" PARITY gamma)" 1
sb_eq    "case4 parity.gamma inconsistent"   "$(jq_parity_consistent "$J" gamma)" false
# Negative control: an untouched skill (we add beta too) stays parity-consistent.
sb_ok    "case4 add beta everywhere"         sb_skillbox add beta
J="$(doctor_json)"
sb_eq    "case4 parity.beta consistent"      "$(jq_parity_consistent "$J" beta)" true
sb_eq    "case4 no PARITY for beta"          "$(jq_count "$J" PARITY beta)" 0
sb_fails "case4 doctor nonzero (PARITY blocks)" sb_skillbox doctor --json
sb_teardown

# =============================================================================
# CASE 5 — MISSING-ROOT: one runtime root directory does not exist.
# Induce: install alpha everywhere, then rmdir the cursor root.
# =============================================================================
sb_setup
sb_ok    "case5 add alpha everywhere"        sb_skillbox add alpha
rm -rf "$SB_TMP/roots/cursor"                # whole root gone → MISSING-ROOT

J="$(doctor_json)"
sb_eq    "case5 MISSING-ROOT cursor == 1"    "$(jq_count "$J" MISSING-ROOT cursor)" 1
# The other three roots are healthy → exactly one MISSING-ROOT in total.
sb_eq    "case5 MISSING-ROOT total == 1"     "$(jq_count "$J" MISSING-ROOT "")" 1
# A removed root does NOT manufacture a MISSING for alpha (loop skips dead roots).
sb_eq    "case5 no MISSING for alpha"        "$(jq_count "$J" MISSING alpha)" 0
sb_fails "case5 doctor nonzero (MISSING-ROOT blocks)" sb_skillbox doctor --json
sb_teardown

# =============================================================================
# CASE 6 — SHADOWED is NON-blocking: a clean fleet that installs the colliding
# `shared` skill reports SHADOWED but exits 0 (blocking == 0).
# Contrast with a blocking fleet to prove the exit code actually moves.
# =============================================================================
sb_setup
sb_ok    "case6 add shared everywhere"       sb_skillbox add shared
# Winner is team/shared; private/shared is shadowed (collision in the manifest).
sb_link  "case6 shared->team (winner)"       "$SB_TMP/roots/claude/shared" "$SB_TMP/src/team/skills/shared"

J="$(doctor_json)"
sb_eq    "case6 SHADOWED shared == 1"        "$(jq_count "$J" SHADOWED shared)" 1
sb_eq    "case6 blocking == 0"               "$(jq_field "$J" blocking)" 0
# No blocking kinds present.
sb_eq    "case6 no BROKEN"                    "$(jq_count "$J" BROKEN "")" 0
sb_eq    "case6 no MISSING"                   "$(jq_count "$J" MISSING "")" 0
sb_eq    "case6 no DRIFTED"                   "$(jq_count "$J" DRIFTED "")" 0
sb_eq    "case6 no PARITY"                    "$(jq_count "$J" PARITY "")" 0
sb_eq    "case6 no MISSING-ROOT"              "$(jq_count "$J" MISSING-ROOT "")" 0
# THE contract: SHADOWED-only fleet exits 0.
sb_ok    "case6 doctor exit 0 (only SHADOWED)" sb_skillbox doctor --json

# Now break it: delete one root's shared link → MISSING appears → exit nonzero.
rm "$SB_TMP/roots/agents/shared"
J="$(doctor_json)"
sb_eq    "case6 after-break blocking >0"     "$([ "$(jq_field "$J" blocking)" -gt 0 ] && echo yes || echo no)" yes
sb_fails "case6 doctor nonzero once blocking" sb_skillbox doctor --json
sb_teardown

sb_report
