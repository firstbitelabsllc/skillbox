#!/usr/bin/env bash
# test_git_view.sh — `skillbox diff/log`: read-only per-skill git view. Covers
# dirty / clean-after-commit / non-git / not-found / never-writes behavior.
set -uo pipefail
cd "$(dirname "$0")"
source lib/sandbox.sh
sb_setup
trap sb_teardown EXIT
echo "sandbox: $SB_TMP"

# alpha lives in team/skills/alpha, under team's .git (sb_setup → _sb_initrepo).
sb_contains "log shows history for a git-backed skill" "$(sb_skillbox log alpha)" "init"
sb_contains "diff clean before any edit" "$(sb_skillbox diff alpha)" "no uncommitted changes"

# dirty edit → uncommitted diff shows it, scoped to the folder
printf '\nT25 dirty line\n' >> "$SB_TMP/src/team/skills/alpha/SKILL.md"
diff_out="$(sb_skillbox diff alpha)"
sb_contains "diff shows the skill file path"  "$diff_out" "skills/alpha/SKILL.md"
sb_contains "diff shows the uncommitted line" "$diff_out" "T25 dirty line"
sb_contains "diff is a real unified diff"     "$diff_out" "diff --git"

# never writes: HEAD unchanged after diff + log
head0="$(git -C "$SB_TMP/src/team" rev-parse HEAD)"
sb_skillbox diff alpha >/dev/null; sb_skillbox log alpha >/dev/null
sb_eq "diff/log are read-only (HEAD unchanged)" "$(git -C "$SB_TMP/src/team" rev-parse HEAD)" "$head0"

# commit → history includes it; working tree clean again
git -C "$SB_TMP/src/team" add skills/alpha/SKILL.md
_sb_commit "$SB_TMP/src/team" -m "alpha tweak"
sb_contains "history includes the new commit" "$(sb_skillbox log alpha)" "alpha tweak"
sb_contains "diff clean after commit"         "$(sb_skillbox diff alpha)" "no uncommitted changes"

# non-git source → graceful message, no traceback
NOGIT="$SB_TMP/nogit/skills"; _sb_mkskill "$NOGIT" loose
sb_skillbox source add nogit "$NOGIT" >/dev/null 2>&1
sb_contains "diff: non-git source reports cleanly" "$(sb_skillbox diff loose)" "not under a git repo"
sb_contains "log: non-git source reports cleanly"  "$(sb_skillbox log loose)"  "not under a git repo"

# unknown skill → refused (nonzero)
sb_fails "diff of an unknown skill refused" sb_skillbox diff nope
sb_fails "log of an unknown skill refused"  sb_skillbox log nope

sb_report
