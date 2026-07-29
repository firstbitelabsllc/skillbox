#!/usr/bin/env bash
# test_bugfixes.sh — regression guards for real bugs found by an adversarial
# code review (2026-06-15). Each assertion would FAIL if the fix regressed.
# Hermetic: operates only inside $SB_TMP. Never touches the real fleet.
#
#   1. name traversal: new/add/rm/promote reject path-escaping names (security)
#   2. malformed / [roots]-less manifest → clean error, never a traceback
#   3. doctor surfaces a winner blocked by real dirs in EVERY root (blind spot)
#   4. list attributes ownership by readlink target, not the plan winner
#   5. promoting a colliding name is refused (would orphan a copy)
set -uo pipefail
cd "$(dirname "$0")"
source lib/sandbox.sh
sb_setup
trap sb_teardown EXIT
echo "sandbox: $SB_TMP"

# ── 1. name traversal is refused everywhere a name becomes a path ─────────────
sb_fails "new rejects an absolute-path name"     sb_skillbox new "/tmp/sbx-escape" --repo private
sb_fails "new rejects a ../ traversal name"      sb_skillbox new "../escape" --repo private
sb_fails "new rejects an empty name"             sb_skillbox new "" --repo private
sb_fails "rm rejects a ../ traversal name"       sb_skillbox rm "../../escape"
sb_fails "add rejects a ../ traversal name"      sb_skillbox add "../escape"
sb_fails "promote rejects a ../ traversal name"  sb_skillbox promote "../escape" --to team
LONGNAME="$(python3 -c 'print("x"*5000)')"
sb_fails "new rejects an over-long name (no mkdir traceback)" sb_skillbox new "$LONGNAME" --repo private
case "$(sb_skillbox new "$LONGNAME" --repo private 2>&1)" in
  *Traceback*) _sb_fail "over-long name dumped a traceback" ;;
  *) _sb_pass "over-long name: clean refusal, no traceback" ;;
esac
sb_ok   "no escaped dir created at /tmp/sbx-escape"        test ! -e "/tmp/sbx-escape"
sb_ok   "no escape dir created beside the private source"  test ! -e "$SB_TMP/src/private/escape"

# ── 2. malformed / no-[roots] manifest → clean error, no traceback ────────────
bad="$SB_TMP/bad.toml"; printf 'this is = not valid toml [[[\n' > "$bad"
out="$(SKILLBOX_MANIFEST="$bad" python3 "$SKILLBOX_BIN" list 2>&1)"; rc=$?
sb_eq "malformed manifest exits nonzero" "$([ $rc -ne 0 ] && echo nz || echo z)" "nz"
case "$out" in *Traceback*) _sb_fail "malformed manifest dumped a traceback";;
  *) _sb_pass "malformed manifest: clean error, no traceback";; esac
sb_contains "malformed manifest error names the cause" "$out" "malformed manifest"

noroots="$SB_TMP/noroots.toml"
printf '[sources.x]\npath = "%s"\n' "$SB_TMP/src/team/skills" > "$noroots"
out2="$(SKILLBOX_MANIFEST="$noroots" python3 "$SKILLBOX_BIN" list 2>&1)"; rc2=$?
sb_eq "no-[roots] manifest exits nonzero" "$([ $rc2 -ne 0 ] && echo nz || echo z)" "nz"
case "$out2" in *Traceback*) _sb_fail "no-[roots] manifest dumped a traceback";;
  *) _sb_pass "no-[roots] manifest: clean error, no traceback";; esac
sb_contains "no-[roots] error mentions the missing table" "$out2" "[roots]"

# present-but-malformed [roots]/[sources] shapes also exit cleanly (no traceback)
badroots="$SB_TMP/badroots.toml"; printf '[roots]\nclaude = 42\n' > "$badroots"
out3="$(SKILLBOX_MANIFEST="$badroots" python3 "$SKILLBOX_BIN" list 2>&1)"; rc3=$?
sb_eq "non-string root value exits nonzero" "$([ $rc3 -ne 0 ] && echo nz || echo z)" "nz"
case "$out3" in *Traceback*) _sb_fail "non-string root value dumped a traceback";;
  *) _sb_pass "non-string root value: clean error, no traceback";; esac
badsrc="$SB_TMP/badsrc.toml"
printf '[roots]\nclaude = "%s"\n[sources.x]\npath = 7\n' "$SB_TMP/roots/claude" > "$badsrc"
out4="$(SKILLBOX_MANIFEST="$badsrc" python3 "$SKILLBOX_BIN" list 2>&1)"; rc4=$?
sb_eq "non-string source path exits nonzero" "$([ $rc4 -ne 0 ] && echo nz || echo z)" "nz"
case "$out4" in *Traceback*) _sb_fail "non-string source path dumped a traceback";;
  *) _sb_pass "non-string source path: clean error, no traceback";; esac

# ── 3. doctor must not go silent when a winner is blocked in EVERY root ───────
# 'beta' is a real team skill. Plant a real (non-symlink) dir in its slot in ALL
# four roots and symlink it nowhere. Before the fix, beta never entered `installed`
# (built from symlinks only) so doctor reported "clean (0 skills)" and stayed mute.
for r in claude agents cursor codex; do
  mkdir -p "$SB_TMP/roots/$r/beta"; printf 'real regular file\n' > "$SB_TMP/roots/$r/beta/x"
done
docj="$(sb_skillbox doctor --json 2>&1)"
occ="$(printf '%s' "$docj" | python3 -c 'import sys,json
d=json.load(sys.stdin); print(sum(1 for p in d["problems"] if p["kind"]=="OCCUPIED" and p["where"].endswith("/beta")))')"
sb_eq "doctor reports OCCUPIED for beta blocked in all 4 roots" "$occ" "4"
sb_eq "all-roots-occupied makes doctor blocking" \
  "$(printf '%s' "$docj" | python3 -c 'import sys,json;print(1 if json.load(sys.stdin)["blocking"]>=1 else 0)')" "1"
for r in claude agents cursor codex; do rm -rf "$SB_TMP/roots/$r/beta"; done  # cleanup

# ── 4. list attributes ownership by the symlink's real target, not the winner ─
# 'shared' collides (team prio1 wins, private prio2 shadowed). Force-install the
# SHADOWED private copy via --source; list must say 'private', not 'team'.
sb_ok "add shared --source private (force the shadowed copy)" sb_skillbox add shared --source private
owner="$(sb_skillbox list | awk '$1=="shared"{print $2}')"
sb_eq "list attributes shared to its actual readlink source (private)" "$owner" "private"

# ── 5. promoting a colliding name is refused (would orphan a copy) ────────────
sb_fails "promote a name that collides across sources is refused" sb_skillbox promote shared --to team
sb_contains "collision-promote refusal explains the risk" \
  "$(sb_skillbox promote shared --to team 2>&1)" "shadowed across"

sb_report
