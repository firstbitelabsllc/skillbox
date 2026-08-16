#!/usr/bin/env bash
# Hermetic regression: a source-level exclusion retires a mounted compatibility
# alias without deleting its source folder, and a later sync must not resurrect it.
set -u
HERE="$(cd "$(dirname "$0")" && pwd -P)"
source "$HERE/lib/sandbox.sh"
trap sb_teardown EXIT

sb_setup

TEAM_SKILLS="$SB_TMP/src/team/skills"
ROOTS=(claude agents cursor codex)

# Establish the historical state first: beta is an ordinary mounted leaf.
sb_ok "baseline sync mounts beta" sb_skillbox sync --no-pull
for r in "${ROOTS[@]}"; do
  sb_link "baseline: $r/beta -> team source" \
    "$SB_TMP/roots/$r/beta" "$TEAM_SKILLS/beta"
done

# An exclusion belongs to the source table. It changes future resolution only;
# `retire` is the source-aware, reversible removal of the old runtime links.
perl -0pi -e 's/(\[sources\.team\]\npath = "[^"]+"\npriority = 1\n)/$1exclude = ["beta"]\n/' \
  "$SKILLBOX_MANIFEST"

# A generic `rm` can intentionally remove any runtime symlink. Retirement is
# narrower: it must refuse an unrelated symlink before touching any root.
FOREIGN="$SB_TMP/foreign/beta"
mkdir -p "$FOREIGN"
unlink "$SB_TMP/roots/claude/beta"
ln -s "$FOREIGN" "$SB_TMP/roots/claude/beta"
FOREIGN_ERR="$(sb_skillbox retire beta --source team 2>&1)"; FOREIGN_RC=$?
sb_eq "retire refuses an unrelated symlink" \
  "$([ "$FOREIGN_RC" -ne 0 ] && echo nz || echo z)" "nz"
sb_contains "foreign-link refusal names the safety boundary" "$FOREIGN_ERR" "refusing to park"
sb_link "foreign claude/beta remains intact after refusal" \
  "$SB_TMP/roots/claude/beta" "$FOREIGN"
sb_link "retire refusal leaves agents/beta untouched" \
  "$SB_TMP/roots/agents/beta" "$TEAM_SKILLS/beta"
unlink "$SB_TMP/roots/claude/beta"
ln -s "$TEAM_SKILLS/beta" "$SB_TMP/roots/claude/beta"

sb_ok "retire beta after source exclusion" sb_skillbox retire beta --source team
for r in "${ROOTS[@]}"; do
  sb_eq "retirement: $r/beta inactive after parking" \
    "$( [ -e "$SB_TMP/roots/$r/beta" ] || [ -L "$SB_TMP/roots/$r/beta" ]; echo $? )" "1"
done

# This is the regression boundary: the next full local sync must not re-link
# the explicitly excluded source leaf.
SYNC_OUT="$(sb_skillbox sync --no-pull 2>&1)"
sb_contains "sync resolves one fewer skill" "$SYNC_OUT" "4 skills resolved"
for r in "${ROOTS[@]}"; do
  sb_eq "sync does not resurrect excluded $r/beta" \
    "$( [ -e "$SB_TMP/roots/$r/beta" ] || [ -L "$SB_TMP/roots/$r/beta" ]; echo $? )" "1"
done

# Exclusion is authoritative for direct add and list as well, not only sync.
sb_fails "add excludes beta" sb_skillbox add beta
sb_fails "add excludes beta even from the named source" sb_skillbox add beta --source team
LIST_OUT="$(sb_skillbox list)"
case "$LIST_OUT" in
  *beta*) _sb_fail "list hides excluded beta";;
  *) _sb_pass "list hides excluded beta";;
esac
sb_ok "source beta/SKILL.md remains archival material" test -f "$TEAM_SKILLS/beta/SKILL.md"

# Exclusion is source-local. A lower-priority active copy must block retirement
# instead of silently becoming the new winner on the next sync.
sb_teardown
sb_setup
TEAM_SKILLS="$SB_TMP/src/team/skills"
PRIVATE_SKILLS="$SB_TMP/src/private/skills"
_sb_mkskill "$PRIVATE_SKILLS" beta "private beta must block retirement"
sb_ok "second baseline sync mounts team beta" sb_skillbox sync --no-pull
perl -0pi -e 's/(\[sources\.team\]\npath = "[^"]+"\npriority = 1\n)/$1exclude = ["beta"]\n/' \
  "$SKILLBOX_MANIFEST"
LOWER_ERR="$(sb_skillbox retire beta --source team 2>&1)"; LOWER_RC=$?
sb_eq "retire refuses a lower-priority active beta" \
  "$([ "$LOWER_RC" -ne 0 ] && echo nz || echo z)" "nz"
sb_contains "lower-owner refusal names private source" "$LOWER_ERR" "private"
for r in "${ROOTS[@]}"; do
  sb_link "lower-owner refusal keeps $r/beta on team" \
    "$SB_TMP/roots/$r/beta" "$TEAM_SKILLS/beta"
done

sb_report
