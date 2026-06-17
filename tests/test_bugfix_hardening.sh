#!/usr/bin/env bash
# Regression guards for the 2026 hardening pass. Each asserts a fixed bug stays
# fixed. Hermetic: operates only inside $SB_TMP via the sandbox manifest.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib/sandbox.sh
. "$DIR/lib/sandbox.sh"
trap sb_teardown EXIT
sb_setup

# ── 0. Fresh fleet (nothing installed yet) — GUI render defaults + filter case ──
# Run first, while no skill is mounted, so `installed` is genuinely empty.
page="$(sb_skillbox ui --render)"
sb_contains "empty-fleet GUI defaults to available" "$page" 'class="opt active" data-show="available"'
sb_contains "filter lowercases the haystack"        "$page" 'toLowerCase().indexOf(flt)'

# ── 1. opt(): a trailing value-less flag is a clean usage error, NOT a traceback ──
out="$(sb_skillbox new foo --repo 2>&1)"; rc=$?
sb_eq       "trailing flag exits nonzero"          "$([ $rc -ne 0 ] && echo nz || echo z)" "nz"
sb_contains "trailing flag says it needs a value"  "$out" "needs a value"
case "$out" in *Traceback*) _sb_fail "no python traceback on trailing flag";; *) _sb_pass "no python traceback on trailing flag";; esac

# ── 2. Unknown / incomplete verb exits nonzero (not a silent exit-0 help dump) ──
out="$(sb_skillbox bogusverb 2>&1)"; rc=$?
sb_eq       "unknown verb exits nonzero"   "$([ $rc -ne 0 ] && echo nz || echo z)" "nz"
sb_contains "unknown verb names the input" "$out" "unknown or incomplete"

# ── 3. promote from a single-skill source is REFUSED (never relocates the repo root) ──
sb_fails "promote single-skill source refused" sb_skillbox promote solo --to team
sb_ok    "single-skill repo root left intact"  test -f "$SB_TMP/src/solo/SKILL.md"
sb_ok    "not moved into the target source"    test ! -e "$SB_TMP/src/team/skills/solo"

# ── 4. prune: a TRANSIENTLY-absent source is kept; a genuinely-dead leaf is pruned ──
sb_ok "add alpha (from team)" sb_skillbox add alpha
mv "$SB_TMP/src/team" "$SB_TMP/src/team.away"          # whole source blinks out
sb_skillbox sync --no-pull >/dev/null 2>&1
sb_eq "alpha link SURVIVES a transient source outage" \
  "$([ -L "$SB_TMP/roots/claude/alpha" ] && echo linked || echo gone)" "linked"
mv "$SB_TMP/src/team.away" "$SB_TMP/src/team"           # source comes back
rm -rf "$SB_TMP/src/team/skills/alpha"                  # now the leaf is genuinely deleted
sb_skillbox sync --no-pull >/dev/null 2>&1
sb_eq "alpha link pruned once the leaf is truly gone" \
  "$([ -L "$SB_TMP/roots/claude/alpha" ] && echo linked || echo gone)" "gone"

# ── 5. source rm preserves a comment that heads the NEXT table ──
cat >> "$SKILLBOX_MANIFEST" <<EOF

[sources.zz]
path = "$SB_TMP/src/private/skills"
priority = 20

# IMPORTANT: keep this annotation for yy
[sources.yy]
path = "$SB_TMP/src/private/skills"
priority = 21
EOF
sb_ok       "source rm zz" sb_skillbox source rm zz
man="$(cat "$SKILLBOX_MANIFEST")"
sb_contains "rm preserved the next table's comment" "$man" "IMPORTANT: keep this annotation"
sb_contains "rm preserved the [sources.yy] table"   "$man" "[sources.yy]"
case "$man" in *'[sources.zz]'*) _sb_fail "zz block removed";; *) _sb_pass "zz block removed";; esac

sb_report
