#!/usr/bin/env bash
# Hermetic test: scenario "precedence_overlay".
#
# First-wins overlay precedence DEPTH. We do NOT rely on the stock sb_setup
# manifest — we build a fully custom 3-source world where ONE name lives in all
# three sources, so we can prove:
#   1. the highest-priority (lowest number) source wins `add` (sb_link to its path)
#   2. a LOWER source still contributes a UNIQUE name no higher source defines
#   3. a "-mine" suffixed private skill does NOT collide with the shared one
#      (different name) yet BOTH mount
#   4. flipping priorities flips the winner (second manifest)
#   5. doctor --json SHADOWED owners are listed in precedence order (winner first)
#
# NOTE: we still call sb_setup to get $SB_TMP + helpers, then OVERWRITE
# $SKILLBOX_MANIFEST with our own three git sources so the test world is
# entirely under our control.

set -u
HERE="$(cd "$(dirname "$0")" && pwd -P)"
# shellcheck source=lib/sandbox.sh
. "$HERE/lib/sandbox.sh"
trap sb_teardown EXIT

sb_setup  # gives us $SB_TMP, helpers, fake roots under roots/{claude,agents,cursor,codex}

# ── build a custom 3-source overlay world ────────────────────────────────────
# Each source is a real git repo (so pin/sync paths would work too).
# topskill  → exists in all THREE sources (collision depth 3)
# onlylow   → exists ONLY in the lowest-priority source (unique contribution)
# shared    → exists in mid + low (collision)
# shared-mine → exists ONLY in mid (private override-by-suffix, distinct name)

mkdir -p "$SB_TMP/ov/high/skills" "$SB_TMP/ov/mid/skills" "$SB_TMP/ov/low/skills"

# high (priority 1): topskill (the winner of the 3-way collision)
_sb_mkskill "$SB_TMP/ov/high/skills" topskill "topskill — HIGH version (should win)"
_sb_initrepo "$SB_TMP/ov/high"

# mid (priority 2): topskill (shadowed), shared, shared-mine (distinct suffix)
_sb_mkskill "$SB_TMP/ov/mid/skills" topskill   "topskill — MID version (shadowed)"
_sb_mkskill "$SB_TMP/ov/mid/skills" shared     "shared — MID version"
_sb_mkskill "$SB_TMP/ov/mid/skills" shared-mine "shared-mine — private suffix override"
_sb_initrepo "$SB_TMP/ov/mid"

# low (priority 3): topskill (shadowed), shared (shadowed), onlylow (UNIQUE)
_sb_mkskill "$SB_TMP/ov/low/skills" topskill "topskill — LOW version (shadowed)"
_sb_mkskill "$SB_TMP/ov/low/skills" shared   "shared — LOW version (shadowed)"
_sb_mkskill "$SB_TMP/ov/low/skills" onlylow  "onlylow — only the lowest source has this"
_sb_initrepo "$SB_TMP/ov/low"

write_manifest() { # high_prio mid_prio low_prio
  cat > "$SKILLBOX_MANIFEST" <<EOF
[roots]
claude = "$SB_TMP/roots/claude"
agents = "$SB_TMP/roots/agents"
cursor = "$SB_TMP/roots/cursor"
codex  = "$SB_TMP/roots/codex"

[sources.high]
path = "$SB_TMP/ov/high/skills"
priority = $1

[sources.mid]
path = "$SB_TMP/ov/mid/skills"
priority = $2

[sources.low]
path = "$SB_TMP/ov/low/skills"
priority = $3
EOF
}

# ── MANIFEST A: high=1, mid=2, low=3 → high wins topskill ─────────────────────
write_manifest 1 2 3
echo "── manifest A (high=1 mid=2 low=3) ─────────────────────────"

# (1) highest-priority source wins the 3-way collision
sb_ok   "add topskill resolves" sb_skillbox add topskill
sb_link "topskill@claude -> HIGH source path" \
        "$SB_TMP/roots/claude/topskill" "$SB_TMP/ov/high/skills/topskill"
sb_link "topskill@agents -> HIGH source path (fanned to all roots)" \
        "$SB_TMP/roots/agents/topskill" "$SB_TMP/ov/high/skills/topskill"
# NEGATIVE: it must NOT point at the shadowed mid/low copies
got="$(readlink "$SB_TMP/roots/claude/topskill" 2>/dev/null)"
case "$got" in
  "$SB_TMP/ov/mid/skills/topskill"|"$SB_TMP/ov/low/skills/topskill")
    _sb_fail "topskill did NOT resolve to a shadowed copy"; printf '       got [%s]\n' "$got";;
  *) _sb_pass "topskill did NOT resolve to a shadowed copy";;
esac

# (2) lower source still contributes a UNIQUE name (onlylow lives only in low)
sb_ok   "add onlylow (unique to lowest source) resolves" sb_skillbox add onlylow
sb_link "onlylow@claude -> LOW source path" \
        "$SB_TMP/roots/claude/onlylow" "$SB_TMP/ov/low/skills/onlylow"
# NEGATIVE: a name that exists in NO source must fail to add
sb_fails "add nonexistent fails" sb_skillbox add does_not_exist_anywhere

# (3) "-mine" suffix does NOT collide with "shared"; both mount independently
sb_ok   "add shared resolves"     sb_skillbox add shared
sb_ok   "add shared-mine resolves" sb_skillbox add shared-mine
# shared (collision mid+low) wins at the HIGHER of those two → mid
sb_link "shared@claude -> MID source path (mid beats low)" \
        "$SB_TMP/roots/claude/shared" "$SB_TMP/ov/mid/skills/shared"
# shared-mine is a DISTINCT name, only in mid → mounts on its own path
sb_link "shared-mine@claude -> MID source path (distinct name, no collision)" \
        "$SB_TMP/roots/claude/shared-mine" "$SB_TMP/ov/mid/skills/shared-mine"
# both physically present and distinct targets
a="$(readlink "$SB_TMP/roots/claude/shared" 2>/dev/null)"
b="$(readlink "$SB_TMP/roots/claude/shared-mine" 2>/dev/null)"
if [ "$a" != "$b" ] && [ -n "$a" ] && [ -n "$b" ]; then
  _sb_pass "shared and shared-mine are independent mounts (distinct targets)"
else
  _sb_fail "shared and shared-mine are independent mounts (distinct targets)"
  printf '       shared=[%s] shared-mine=[%s]\n' "$a" "$b"
fi

# (5) doctor --json SHADOWED owners in precedence order (winner FIRST)
DJ="$(sb_skillbox doctor --json)"
# shared-mine must NOT appear as a SHADOWED collision (it is unique)
sb_contains "doctor reports topskill SHADOWED" "$DJ" '"where": "topskill"'
sb_contains "doctor topskill shadow detail: high wins, mid+low shadowed (order)" \
            "$(printf '%s' "$DJ" | tr -s ' ')" 'high wins; shadowed: ['"'"'mid'"'"', '"'"'low'"'"']'
sb_contains "doctor reports shared SHADOWED" "$DJ" '"where": "shared"'
sb_contains "doctor shared shadow detail: mid wins, low shadowed" \
            "$(printf '%s' "$DJ" | tr -s ' ')" 'mid wins; shadowed: ['"'"'low'"'"']'
# NEGATIVE: shared-mine (unique name) is NOT a SHADOWED entry
case "$DJ" in
  *'"where": "shared-mine"'*) _sb_fail "shared-mine is NOT flagged SHADOWED (unique name)";;
  *) _sb_pass "shared-mine is NOT flagged SHADOWED (unique name)";;
esac
# SHADOWED is non-blocking: a clean overlay fleet has blocking==0
sb_contains "shadows are non-blocking (blocking:0)" "$DJ" '"blocking": 0'

# ── MANIFEST B: flip priorities → low=1, mid=2, high=3 → topskill winner flips ─
echo "── manifest B (flipped: low=1 mid=2 high=3) ────────────────"
write_manifest 3 2 1   # high=3, mid=2, low=1  → LOW now wins

# sync relinks ALL winners into ALL roots under the new precedence
sb_ok   "sync under flipped manifest" sb_skillbox sync --no-pull
sb_link "topskill@claude FLIPS to LOW source path" \
        "$SB_TMP/roots/claude/topskill" "$SB_TMP/ov/low/skills/topskill"
# NEGATIVE: winner is no longer the HIGH copy
got="$(readlink "$SB_TMP/roots/claude/topskill" 2>/dev/null)"
case "$got" in
  "$SB_TMP/ov/high/skills/topskill")
    _sb_fail "topskill no longer points at HIGH after flip"; printf '       got [%s]\n' "$got";;
  *) _sb_pass "topskill no longer points at HIGH after flip";;
esac
# shared collision: low now outranks mid → low wins
sb_link "shared@claude FLIPS to LOW source path" \
        "$SB_TMP/roots/claude/shared" "$SB_TMP/ov/low/skills/shared"

# doctor SHADOWED order also flips (low first now)
DJ2="$(sb_skillbox doctor --json | tr -s ' ')"
sb_contains "doctor topskill shadow order flips: low wins; high+mid shadowed" \
            "$DJ2" 'low wins; shadowed: ['"'"'mid'"'"', '"'"'high'"'"']'
sb_contains "doctor shared shadow order flips: low wins; mid shadowed" \
            "$DJ2" 'low wins; shadowed: ['"'"'mid'"'"']'
# onlylow still unique and still mounted (independent of priority)
sb_link "onlylow@claude still -> LOW source path after flip" \
        "$SB_TMP/roots/claude/onlylow" "$SB_TMP/ov/low/skills/onlylow"

sb_report
