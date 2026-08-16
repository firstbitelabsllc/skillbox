#!/usr/bin/env bash
# test_promote.sh — the sharing verb: move a skill between source repos, reversibly.
set -uo pipefail
cd "$(dirname "$0")"
source lib/sandbox.sh
sb_setup
trap sb_teardown EXIT
echo "sandbox: $SB_TMP"

# gamma lives only in the private source. Mount it, then promote private -> team.
sb_ok   "add gamma" sb_skillbox add gamma
sb_link "gamma starts in private" "$SB_TMP/roots/claude/gamma" "$SB_TMP/src/private/skills/gamma"

sb_ok   "promote gamma --to team" sb_skillbox promote gamma --to team
sb_eq   "gamma folder moved OUT of private" "$([ -e "$SB_TMP/src/private/skills/gamma" ] && echo here || echo gone)" "gone"
sb_eq   "gamma folder now IN team"          "$([ -f "$SB_TMP/src/team/skills/gamma/SKILL.md" ] && echo here || echo gone)" "here"
# every runtime relinked to the new source path (no dangling old links)
for r in claude agents cursor codex; do
  sb_link "gamma relinked -> team in $r" "$SB_TMP/roots/$r/gamma" "$SB_TMP/src/team/skills/gamma"
done
sb_contains "doctor clean after promote (no dangling)" "$(sb_skillbox doctor | tr -s ' ')" "doctor: clean"

# Reversible: promote back to private restores the original state.
sb_ok   "promote gamma --to private (reverse)" sb_skillbox promote gamma --to private
sb_link "gamma back in private (claude)" "$SB_TMP/roots/claude/gamma" "$SB_TMP/src/private/skills/gamma"
sb_eq   "gamma gone from team after reverse" "$([ -e "$SB_TMP/src/team/skills/gamma" ] && echo here || echo gone)" "gone"

# Refusals (negative modes).
sb_fails "promote to a single_skill source refused" sb_skillbox promote gamma --to solo
sb_fails "promote to the same source refused"       sb_skillbox promote gamma --to private
sb_fails "promote when target slot occupied refused" sb_skillbox promote shared --to private  # private already has 'shared'
sb_fails "promote with no --to refused"             sb_skillbox promote gamma
sb_fails "promote of unknown skill refused"         sb_skillbox promote nope --to team

# A target source may deliberately keep a legacy folder archival-only. Promote
# must refuse before moving anything into a folder that its own resolver hides.
perl -0pi -e 's/(\[sources\.team\]\npath = "[^"]+"\npriority = 1\n)/$1exclude = ["gamma"]\n/' \
  "$SKILLBOX_MANIFEST"
sb_fails "promote refuses a name excluded by its target source" \
  sb_skillbox promote gamma --to team
sb_ok "excluded promote leaves gamma in its original source" \
  test -f "$SB_TMP/src/private/skills/gamma/SKILL.md"
sb_ok "excluded promote creates no hidden target folder" \
  test ! -e "$SB_TMP/src/team/skills/gamma"

sb_report
