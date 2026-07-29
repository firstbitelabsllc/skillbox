#!/usr/bin/env bash
# Hermetic test: install_uninstall_cycles
#
# Covers:
#  - add -> rm -> add churn (3x) for a skill: symlink correctly present/absent each
#    pass, and the SOURCE folder is byte-for-byte untouched throughout.
#  - add idempotency: a second add on a fully-mounted skill reports "already mounted
#    everywhere" and exits 0 (no error, no extra links).
#  - sync FULL merge: every resolved source skill becomes a real symlink in EVERY one
#    of the 4 roots; per-root link count == number of resolved skills.
#  - rm unlinks symlinks in configured slots regardless of provenance: a real
#    (non-symlink) file in a root is left untouched by both add and rm.
#
# Every assertion can fail if skillbox regresses (real targets, real exit codes,
# negative paths). NEVER touches the real fleet — sandbox-only via SKILLBOX_MANIFEST.

set -u
HERE="$(cd "$(dirname "$0")" && pwd -P)"
# shellcheck source=lib/sandbox.sh
source "$HERE/lib/sandbox.sh"
trap sb_teardown EXIT

sb_setup

TEAM_SKILLS="$SB_TMP/src/team/skills"
ROOTS=(claude agents cursor codex)

# ── 1. add -> rm -> add churn 3x; source folder never touched ──────────────────
# Snapshot the source skill so we can prove the cycle never mutates the repo folder.
SRC_BETA="$TEAM_SKILLS/beta"
BETA_SNAPSHOT_BEFORE="$(cat "$SRC_BETA/SKILL.md")"
BETA_INODE_BEFORE="$(ls -di "$SRC_BETA" | awk '{print $1}')"

for i in 1 2 3; do
  # Fleet starts clean for beta on every pass.
  sb_ok "cycle$i: add beta exits 0" sb_skillbox add beta
  # Symlink present in every root, pointing at the real team source path.
  for r in "${ROOTS[@]}"; do
    sb_link "cycle$i: $r/beta -> team source" \
      "$SB_TMP/roots/$r/beta" "$TEAM_SKILLS/beta"
  done

  sb_ok "cycle$i: rm beta exits 0" sb_skillbox rm beta
  # Symlink absent in every root after rm.
  for r in "${ROOTS[@]}"; do
    if [ ! -e "$SB_TMP/roots/$r/beta" ] && [ ! -L "$SB_TMP/roots/$r/beta" ]; then
      _sb_pass "cycle$i: $r/beta absent after rm"
    else
      _sb_fail "cycle$i: $r/beta absent after rm"
      printf '       still exists: %s\n' "$SB_TMP/roots/$r/beta"
    fi
  done
done

# Source folder identity + content untouched by the whole add/rm churn.
sb_eq "source beta/SKILL.md content unchanged" \
  "$(cat "$SRC_BETA/SKILL.md")" "$BETA_SNAPSHOT_BEFORE"
sb_eq "source beta/ folder inode unchanged (not recreated)" \
  "$(ls -di "$SRC_BETA" | awk '{print $1}')" "$BETA_INODE_BEFORE"
# rm should NEVER reach into the source dir to delete the skill.
sb_ok "source beta/SKILL.md still present" test -f "$SRC_BETA/SKILL.md"

# rm when nothing is mounted: graceful message, exit 0.
RM_ABSENT="$(sb_skillbox rm beta 2>&1)"
sb_ok "rm beta (already absent) exits 0" sb_skillbox rm beta
sb_contains "rm beta absent → no symlinks in configured slots" \
  "$RM_ABSENT" "no symlinks found in configured runtime slots"

# ── 2. add idempotency ─────────────────────────────────────────────────────────
sb_ok "add alpha (first) exits 0" sb_skillbox add alpha
ADD_AGAIN="$(sb_skillbox add alpha 2>&1)"
sb_ok "add alpha (second) exits 0" sb_skillbox add alpha
sb_contains "second add alpha → 'already mounted everywhere'" \
  "$ADD_AGAIN" "already mounted everywhere"
# Idempotency must not have produced a duplicate / changed the link target.
for r in "${ROOTS[@]}"; do
  sb_link "idempotent: $r/alpha still -> team source" \
    "$SB_TMP/roots/$r/alpha" "$TEAM_SKILLS/alpha"
done

# ── 3. sync = FULL merge into every root ───────────────────────────────────────
# Fresh sandbox so prior single-skill adds don't muddy the per-root counts.
sb_teardown
sb_setup
TEAM_SKILLS="$SB_TMP/src/team/skills"

SYNC_OUT="$(sb_skillbox sync --no-pull 2>&1)"
sb_ok "sync exits 0" sb_skillbox sync --no-pull
# Resolved winners: alpha, beta, shared(team wins), gamma, solo = 5.
sb_contains "sync reports 5 resolved skills" "$SYNC_OUT" "5 skills resolved"
EXPECTED_SKILLS=(alpha beta gamma shared solo)

# Every root holds a real symlink for every resolved skill; count == 5 per root.
for r in "${ROOTS[@]}"; do
  count=0
  for s in "${EXPECTED_SKILLS[@]}"; do
    if [ -L "$SB_TMP/roots/$r/$s" ] && [ -e "$SB_TMP/roots/$r/$s" ]; then
      count=$((count + 1))
    else
      _sb_fail "merge: $r/$s missing valid symlink after sync"
    fi
  done
  # Total symlinks in the root must equal exactly the resolved count (no extras).
  total_links=0
  for f in "$SB_TMP/roots/$r"/*; do
    [ -L "$f" ] && total_links=$((total_links + 1))
  done
  sb_eq "merge: $r has exactly 5 skillbox symlinks" "$total_links" "5"
  sb_eq "merge: $r resolved-skill symlinks valid" "$count" "5"
done

# shared resolves to the TEAM copy (priority 1 wins over private).
for r in "${ROOTS[@]}"; do
  sb_link "merge: $r/shared -> team (winner), not private" \
    "$SB_TMP/roots/$r/shared" "$TEAM_SKILLS/shared"
done
# solo (single_skill source: repo root IS the skill) lands too.
sb_link "merge: claude/solo -> single-skill source root" \
  "$SB_TMP/roots/claude/solo" "$SB_TMP/src/solo"

# sync is idempotent: a second sync relinks nothing.
SYNC2="$(sb_skillbox sync --no-pull 2>&1)"
sb_contains "second sync relinked=0 (idempotent)" "$SYNC2" "relinked=0"
sb_contains "second sync linked=0 (idempotent)" "$SYNC2" "linked=0"

# ── 4. rm leaves a REAL file untouched; add & rm refuse to clobber it ──────────
# Place a genuine (non-symlink) file at a root path that ALSO resolves as a source
# skill name. This is the meaningful clobber test: add must skip that root but still
# link the others; rm must remove only the symlinks and leave the real file alone.
sb_teardown
sb_setup
TEAM_SKILLS="$SB_TMP/src/team/skills"

REALFILE="$SB_TMP/roots/claude/alpha"   # 'alpha' is a real source skill name
printf 'i am a real local override, not a skillbox symlink\n' > "$REALFILE"
REAL_CONTENT="$(cat "$REALFILE")"

ADD_OUT="$(sb_skillbox add alpha 2>&1)"
sb_ok "add alpha (real file in claude) exits 0" sb_skillbox add alpha
sb_contains "add refuses to clobber → 'real file/dir present'" \
  "$ADD_OUT" "real file/dir present"
# claude/alpha remains the real file (NOT a symlink), content intact.
if [ -f "$REALFILE" ] && [ ! -L "$REALFILE" ]; then
  _sb_pass "add: claude/alpha still a real file (not symlinked)"
else
  _sb_fail "add: claude/alpha still a real file (not symlinked)"
fi
sb_eq "add: real file content preserved" "$(cat "$REALFILE")" "$REAL_CONTENT"
# The other 3 roots DID get the symlink.
for r in agents cursor codex; do
  sb_link "add: $r/alpha symlinked despite claude conflict" \
    "$SB_TMP/roots/$r/alpha" "$TEAM_SKILLS/alpha"
done

# rm must remove the 3 skillbox symlinks but NOT touch the real file in claude.
RM_OUT="$(sb_skillbox rm alpha 2>&1)"
if [ -f "$REALFILE" ] && [ ! -L "$REALFILE" ]; then
  _sb_pass "rm: claude/alpha real file survives rm"
else
  _sb_fail "rm: claude/alpha real file survives rm"
fi
sb_eq "rm: real file content still intact after rm" "$(cat "$REALFILE")" "$REAL_CONTENT"
for r in agents cursor codex; do
  if [ ! -e "$SB_TMP/roots/$r/alpha" ] && [ ! -L "$SB_TMP/roots/$r/alpha" ]; then
    _sb_pass "rm: $r/alpha symlink removed"
  else
    _sb_fail "rm: $r/alpha symlink removed"
  fi
done
# rm output should not claim it removed the claude one (only the symlink roots).
sb_contains "rm unlinked agents/alpha (symlink slot only)" "$RM_OUT" "unlinked agents/alpha"
case "$RM_OUT" in
  *"unlinked claude/alpha"*) _sb_fail "rm must NOT report unlinking the real claude/alpha";;
  *) _sb_pass "rm did not report unlinking real claude/alpha";;
esac

sb_report
