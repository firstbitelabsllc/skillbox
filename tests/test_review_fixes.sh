#!/usr/bin/env bash
# test_review_fixes.sh — regression guards for bugs found by a code review of
# bin/skillbox.py (2026-09-06). Each section reproduces the wrong behavior first.
# Hermetic: operates only inside $SB_TMP. Never touches the real fleet.
#
#   1. sync pruned a single-skill source's mounts while its repo was absent,
#      then doctor reported a false-clean fleet; relative dangling links into
#      a present source were never pruned
#   2. source add stored a relative path, so the source vanished from any
#      other working directory
#   3. retire dumped a traceback (and lost the recovery receipt for slots it
#      had already parked) when a runtime root refused the recovery journal
#   4. list crashed with StopIteration on an empty [roots] table
#   5. source add --priority <non-integer> crashed with ValueError
set -uo pipefail
cd "$(dirname "$0")"
source lib/sandbox.sh
sb_setup
trap 'chmod -R u+w "$SB_TMP" 2>/dev/null; sb_teardown' EXIT
echo "sandbox: $SB_TMP"

# ── 1. absent single-skill source is transient, not dead ─────────────────────
sb_ok "add solo (single-skill source)" sb_skillbox add solo
mv "$SB_TMP/src/solo" "$SB_TMP/src/solo.away"
out="$(sb_skillbox sync --no-pull 2>&1)"
sb_contains "sync refuses the absent Git source" "$out" "SOURCE-MISSING"
sb_fails "sync exits nonzero while the Git source is absent" sb_skillbox sync --no-pull
sb_ok "solo link survives refused sync while its repo is absent" test -L "$SB_TMP/roots/claude/solo"
sb_fails "doctor is not clean while the solo mount is broken" sb_skillbox doctor
mv "$SB_TMP/src/solo.away" "$SB_TMP/src/solo"
sb_ok "doctor is clean once the source is back" sb_skillbox doctor
# a relative dangling link into a PRESENT source is genuinely dead
ln -s "../../src/team/skills/zeta" "$SB_TMP/roots/claude/zeta"
sb_ok "sync exits 0 with a relative dangling link" sb_skillbox sync --no-pull
sb_ok "relative dangling link into a present source is pruned" test ! -L "$SB_TMP/roots/claude/zeta"

# A clean-but-ahead canonical source is also unsafe for a local merge.  Plant
# both a wrong configured-slot link and a dangling unmanaged link, then prove
# the preflight refuses before either relink or prune can mutate the roots.
FOREIGN_ALPHA="$SB_TMP/foreign/alpha"
mkdir -p "$FOREIGN_ALPHA"
ln -sfn "$FOREIGN_ALPHA" "$SB_TMP/roots/claude/alpha"
ln -s "../../src/team/skills/missing-ahead" "$SB_TMP/roots/claude/missing-ahead"
printf 'ahead-only\n' >> "$SB_TMP/src/team/skills/alpha/SKILL.md"
git -C "$SB_TMP/src/team" add skills/alpha/SKILL.md
_sb_commit "$SB_TMP/src/team" -m fixture-ahead
AHEAD_OUT="$(sb_skillbox sync --no-pull 2>&1)"; AHEAD_RC=$?
sb_eq "clean-ahead source refuses sync" "$AHEAD_RC" "1"
sb_contains "clean-ahead refusal names SOURCE-AHEAD" "$AHEAD_OUT" "SOURCE-AHEAD"
sb_link "clean-ahead refusal leaves wrong alpha slot unchanged" \
  "$SB_TMP/roots/claude/alpha" "$FOREIGN_ALPHA"
sb_ok "clean-ahead refusal leaves dangling link for zero prune" \
  test -L "$SB_TMP/roots/claude/missing-ahead"

# ── 2. source add stores an absolute path ────────────────────────────────────
mkdir -p "$SB_TMP/relsrc/skills"
_sb_mkskill "$SB_TMP/relsrc/skills" kappa
( cd "$SB_TMP" && sb_skillbox source add rel relsrc >/dev/null 2>&1 )
sb_ok "manifest stores the source as an absolute path" grep -q 'path = "/' <(sed -n '/\[sources\.rel\]/,/priority/p' "$SKILLBOX_MANIFEST")
sb_ok "added source resolves from another working directory" bash -c "cd / && python3 '$SKILLBOX_BIN' add kappa"
sb_link "kappa mounted from the absolute path" "$SB_TMP/roots/claude/kappa" "$SB_TMP/relsrc/skills/kappa"

# ── 3. retire: unwritable runtime root → clean refusal with recovery receipt ──
if [ "$(id -u)" = 0 ]; then
  _sb_pass "retire read-only root: skipped as root (mode bits are not enforced)"
else
  sb_ok "add gamma from private" sb_skillbox add gamma --source private
  perl -0pi -e 's/(\[sources\.private\]\npath = "[^"]+"\npriority = 2\n)/$1exclude = ["gamma"]\n/' \
    "$SKILLBOX_MANIFEST"
  chmod 555 "$SB_TMP/roots/cursor"
  out="$(sb_skillbox retire gamma --source private 2>&1)"; rc=$?
  chmod 755 "$SB_TMP/roots/cursor"
  sb_eq "retire exits nonzero when a root refuses the journal" "$([ $rc -ne 0 ] && echo nz || echo z)" "nz"
  case "$out" in *Traceback*) _sb_fail "retire dumped a traceback";;
    *) _sb_pass "retire: clean refusal, no traceback";; esac
  sb_contains "refusal names the unwritable slot" "$out" "cursor/gamma"
  sb_contains "refusal reports the already-parked recovery path" "$out" "verified recovery retained"
  sb_ok "unwritable slot still holds its link" test -L "$SB_TMP/roots/cursor/gamma"
fi

# ── 4. list with an empty [roots] table ──────────────────────────────────────
printf '[roots]\n' > "$SB_TMP/noroots.toml"
out="$(SKILLBOX_MANIFEST="$SB_TMP/noroots.toml" python3 "$SKILLBOX_BIN" list 2>&1)"; rc=$?
sb_eq "list with empty [roots] exits nonzero" "$([ $rc -ne 0 ] && echo nz || echo z)" "nz"
case "$out" in *Traceback*) _sb_fail "empty [roots] dumped a traceback";;
  *) _sb_pass "empty [roots]: clean error, no traceback";; esac
sb_contains "empty [roots] error names the table" "$out" "[roots]"

# ── 5. source add --priority must be an integer ──────────────────────────────
out="$(sb_skillbox source add prio "$SB_TMP/relsrc" --priority abc 2>&1)"; rc=$?
sb_eq "non-integer --priority exits nonzero" "$([ $rc -ne 0 ] && echo nz || echo z)" "nz"
case "$out" in *Traceback*) _sb_fail "non-integer --priority dumped a traceback";;
  *) _sb_pass "non-integer --priority: clean error, no traceback";; esac
sb_ok "manifest was not edited" bash -c "! grep -q 'sources.prio' '$SKILLBOX_MANIFEST'"

sb_report
