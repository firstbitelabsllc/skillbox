#!/usr/bin/env bash
# Scenario: gui_http — LIVE management GUI over HTTP (real server, not --render).
# Boots `skillbox ui --port N`, polls until 200, then drives the POST add/rm
# routes and asserts on real responses + real symlinks. Mutations are POST-only
# and CSRF-guarded (per-process token + same-origin), so the test also proves the
# negative cases: a plain GET never mutates, and a POST without the token (or from
# a cross-origin page) is rejected 403. Always kills the server on exit.
# Run: bash tests/test_gui_http.sh   (exits nonzero on any failure)
set -uo pipefail
cd "$(dirname "$0")"
source lib/sandbox.sh

sb_setup
SVPID=""   # set after we launch the server; trap must not reference unset
trap 'kill $SVPID 2>/dev/null; sb_teardown' EXIT
echo "sandbox: $SB_TMP"

# High-ish, run-stable port so parallel scenarios don't collide.
PORT=$(( 8700 + ($$ % 200) ))
BASE="http://localhost:$PORT"
echo "gui port: $PORT"

# Launch the live server in the background. It inherits SKILLBOX_MANIFEST from
# sb_setup so it operates entirely inside the sandbox.
sb_skillbox ui --port "$PORT" >"$SB_TMP/ui.log" 2>&1 &
SVPID=$!

# Bounded readiness poll: up to ~4s (20 tries * 0.2s). No long fixed sleep.
code=""
for _ in $(seq 1 20); do
  if ! kill -0 "$SVPID" 2>/dev/null; then break; fi
  code="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/" 2>/dev/null)"
  [ "$code" = "200" ] && break
  sleep 0.2
done
sb_eq "server came up (GET / == 200)" "$code" "200"

# ── GET / renders the live page and embeds a per-process CSRF token ───────────
root_body="$(curl -s "$BASE/")"
sb_contains "GET / has the skillbox title" "$root_body" "<h1>skillbox</h1>"
sb_contains "GET / lists a sandbox skill (alpha)" "$root_body" "alpha"
sb_contains "GET / actions are POST forms (no GET-mutation links)" "$root_body" 'method="post"'
TOKEN="$(printf '%s' "$root_body" | grep -o 'name="t" value="[^"]*"' | head -1 | sed 's/.*value="//; s/".*//')"
sb_eq "page embedded a non-empty CSRF token" "$([ -n "$TOKEN" ] && echo yes || echo no)" "yes"

# ── NEGATIVE: a plain GET /add must NOT mutate (CSRF via <img src> is inert) ───
get_add_code="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/add?s=beta&src=team")"
sb_eq "GET /add bounces home (303), does not mutate" "$get_add_code" "303"
sb_eq "GET /add created NO symlink (beta absent)" \
  "$([ -e "$SB_TMP/roots/claude/beta" ] && echo present || echo gone)" "gone"

# ── NEGATIVE: POST /add without the token → 403, no mutation ───────────────────
notoken_code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/add" --data "s=alpha&src=team")"
sb_eq "POST /add without token is forbidden (403)" "$notoken_code" "403"
sb_eq "forbidden POST created NO symlink (alpha absent)" \
  "$([ -e "$SB_TMP/roots/claude/alpha" ] && echo present || echo gone)" "gone"

# ── NEGATIVE: POST /add with a cross-origin Origin header → 403 ────────────────
xorigin_code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/add" \
  --data "s=alpha&src=team&t=$TOKEN" -H "Origin: http://evil.example")"
sb_eq "cross-origin POST /add is forbidden (403)" "$xorigin_code" "403"
sb_eq "cross-origin POST created NO symlink (alpha absent)" \
  "$([ -e "$SB_TMP/roots/claude/alpha" ] && echo present || echo gone)" "gone"

# ── NEGATIVE: a DNS-rebound Host (matching Origin, but not loopback) → 403 ─────
rebind_code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/add" \
  --data "s=alpha&src=team&t=$TOKEN" -H "Host: evil.example:$PORT" -H "Origin: http://evil.example:$PORT")"
sb_eq "rebound-Host POST /add is forbidden (403)" "$rebind_code" "403"
sb_eq "rebound-Host POST created NO symlink (alpha absent)" \
  "$([ -e "$SB_TMP/roots/claude/alpha" ] && echo present || echo gone)" "gone"

# ── POSITIVE: same-origin POST /add with the token → 303 + real symlinks ──────
add_code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/add" \
  --data "s=alpha&src=team&t=$TOKEN" -H "Origin: $BASE")"
sb_eq "authorized POST /add returns 303 redirect" "$add_code" "303"
for r in claude agents cursor codex; do
  sb_link "alpha mounted in $r -> team after POST /add" \
    "$SB_TMP/roots/$r/alpha" "$SB_TMP/src/team/skills/alpha"
done
# And the live page now offers a remove form for installed alpha.
after_add="$(curl -s "$BASE/")"
sb_contains "page offers a remove form for installed alpha" "$after_add" 'action="/rm"'
sb_contains "remove form carries alpha as its skill value" "$after_add" 'value="alpha"'
# Flash is one-shot: the action result shows on the first GET after the POST, then clears.
sb_contains "action result flashes once after the POST" "$after_add" 'class="note"'
refresh="$(curl -s "$BASE/")"
case "$refresh" in
  *'class="note"'*) _sb_fail "flash leaked onto a plain refresh (not one-shot)" ;;
  *) _sb_pass "flash is one-shot (cleared after first render)" ;;
esac

# ── POSITIVE: authorized POST /rm → 303 + link gone, source survives ──────────
rm_code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/rm" \
  --data "s=alpha&t=$TOKEN" -H "Origin: $BASE")"
sb_eq "authorized POST /rm returns 303 redirect" "$rm_code" "303"
sb_eq "alpha symlink gone from claude after /rm" \
  "$([ -e "$SB_TMP/roots/claude/alpha" ] && echo present || echo gone)" "gone"
sb_eq "alpha symlink gone from codex after /rm" \
  "$([ -e "$SB_TMP/roots/codex/alpha" ] && echo present || echo gone)" "gone"
sb_eq "alpha source still on disk after /rm" \
  "$([ -f "$SB_TMP/src/team/skills/alpha/SKILL.md" ] && echo present || echo gone)" "present"

# ── POST /source-add: add a teammate source live → it appears in the rail ─────
_sb_mkskill "$SB_TMP/mate/skills" mate-skill
sa_code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/source-add" \
  --data "id=mate" --data-urlencode "path=$SB_TMP/mate/skills" --data "t=$TOKEN" -H "Origin: $BASE")"
sb_eq "authorized POST /source-add returns 303" "$sa_code" "303"
sb_contains "manifest gained [sources.mate]" "$(cat "$SKILLBOX_MANIFEST")" "[sources.mate]"
after_sa="$(curl -s "$BASE/")"
sb_contains "rail lists the new source live (no restart)" "$after_sa" 'data-src="mate"'
sb_contains "the new source's skill now renders" "$after_sa" "mate-skill"
# CSRF: source-add without the token is rejected and writes nothing
sa_bad="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/source-add" \
  --data "id=evil" --data-urlencode "path=$SB_TMP/mate/skills")"
sb_eq "POST /source-add without token forbidden (403)" "$sa_bad" "403"
sb_eq "rejected source-add wrote nothing (no [sources.evil])" \
  "$(grep -c '\[sources.evil\]' "$SKILLBOX_MANIFEST")" "0"

# ── /gitinfo (read-only per-skill git view): git-backed vs non-git ────────────
gi="$(curl -s "$BASE/gitinfo?s=alpha")"
sb_contains "/gitinfo: git-backed skill returns git:true" "$gi" '"git": true'
sb_contains "/gitinfo: payload carries version history (log)" "$gi" '"log"'
# 'mate' (added via /source-add above) is a plain dir, not a git repo
gin="$(curl -s "$BASE/gitinfo?s=mate-skill")"
sb_contains "/gitinfo: non-git skill returns git:false" "$gin" '"git": false'

# ── server stays alive after the whole round-trip ─────────────────────────────
sb_eq "server still serving" \
  "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/")" "200"

sb_report
