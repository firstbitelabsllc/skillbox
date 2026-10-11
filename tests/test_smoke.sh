#!/usr/bin/env bash
# Reference scenario test — proves the hermetic harness pattern end to end.
# Run: bash tests/test_smoke.sh   (exits nonzero on any failure)
set -uo pipefail
cd "$(dirname "$0")"
source lib/sandbox.sh

sb_setup
trap sb_teardown EXIT
echo "sandbox: $SB_TMP"

# doctor text is column-formatted; squeeze runs of spaces so assertions aren't brittle.
_doc() { sb_skillbox doctor | tr -s ' '; }

# 1. doctor on empty roots → clean
sb_contains "doctor clean on empty roots" "$(_doc)" "doctor: clean (0 skills"

# 2. add alpha → linked into all 4 roots, pointing at team
sb_ok "add alpha" sb_skillbox add alpha
for r in claude agents cursor codex; do
  sb_link "alpha mounted in $r -> team" "$SB_TMP/roots/$r/alpha" "$SB_TMP/src/team/skills/alpha"
done

# 3. precedence: 'shared' resolves to team (priority 1), NOT private
sb_ok "add shared" sb_skillbox add shared
sb_link "shared wins from team (first-wins)" "$SB_TMP/roots/claude/shared" "$SB_TMP/src/team/skills/shared"
sb_contains "doctor reports the shadow" "$(_doc)" "SHADOWED shared"

# 4. single-skill source: solo resolves to repo root
sb_ok "add solo (single-skill source)" sb_skillbox add solo
sb_link "solo -> repo root" "$SB_TMP/roots/agents/solo" "$SB_TMP/src/solo"

# 5. new born in a chosen repo + linked everywhere
sb_ok "new newbie --repo private" sb_skillbox new newbie --repo private
sb_link "newbie born in private, mounted" "$SB_TMP/roots/codex/newbie" "$SB_TMP/src/private/skills/newbie"

# 6. new refuses a name that already resolves (no accidental shadow)
sb_fails "new alpha refused (exists in team)" sb_skillbox new alpha --repo private

# 7. negative drift with a dirty source: preflight refuses before relinking;
#    once the source is committed, sync restores the link.
rm "$SB_TMP/roots/agents/alpha"
sb_contains "doctor flags MISSING after drift" "$(_doc)" "MISSING agents/alpha"
DRIFT_OUT="$(sb_skillbox sync --no-pull 2>&1)"; DRIFT_RC=$?
sb_eq "sync refuses dirty private source before repairing drift" "$DRIFT_RC" "1"
sb_contains "dirty-source refusal names SOURCE-DIRTY" "$DRIFT_OUT" "SOURCE-DIRTY"
sb_ok "dirty-source refusal leaves alpha link absent" test ! -L "$SB_TMP/roots/agents/alpha"
git -C "$SB_TMP/src/private" add -A
_sb_commit "$SB_TMP/src/private" -m fixture-newbie
git -C "$SB_TMP/src/private" push -q
sb_ok "sync repairs drift after source is clean" sb_skillbox sync --no-pull
sb_link "alpha restored in agents" "$SB_TMP/roots/agents/alpha" "$SB_TMP/src/team/skills/alpha"
sb_contains "doctor clean after repair" "$(_doc)" "doctor: clean"

# 8. sync is idempotent (no churn on a healthy fleet)
sb_contains "sync idempotent (relinked=0)" "$(sb_skillbox sync --no-pull)" "relinked=0"

# 9. prune: add beta, delete its source, sync prunes the now-dangling links
sb_ok "add beta" sb_skillbox add beta
rm -rf "$SB_TMP/src/team/skills/beta"
PRUNE_OUT="$(sb_skillbox sync --no-pull 2>&1)"; PRUNE_RC=$?
sb_eq "sync refuses dirty team source before pruning" "$PRUNE_RC" "1"
sb_contains "dirty-source refusal names SOURCE-DIRTY for prune" "$PRUNE_OUT" "SOURCE-DIRTY"
sb_ok "dirty-source refusal leaves beta link in claude" test -L "$SB_TMP/roots/claude/beta"
git -C "$SB_TMP/src/team" add -A
_sb_commit "$SB_TMP/src/team" -m fixture-remove-beta
git -C "$SB_TMP/src/team" push -q
sb_contains "sync prunes the removed skill after source is clean" "$(sb_skillbox sync --no-pull)" "pruned=4"
sb_eq "beta link gone from claude" "$([ -e "$SB_TMP/roots/claude/beta" ] && echo present || echo gone)" "gone"

# 10. rm unlinks from every root (source untouched)
sb_ok "rm shared" sb_skillbox rm shared
sb_eq "shared unlinked from claude" "$([ -e "$SB_TMP/roots/claude/shared" ] && echo present || echo gone)" "gone"
sb_eq "shared source still on disk" "$([ -f "$SB_TMP/src/team/skills/shared/SKILL.md" ] && echo present || echo gone)" "present"

# 11. UNMANAGED: a skill symlinked into roots from outside any source repo is
# surfaced (non-blocking), not mistaken for an OCCUPIED/blocking problem.
mkdir -p "$SB_TMP/external/orphanx"
printf -- '---\nname: orphanx\ndescription: not from any source\n---\n' > "$SB_TMP/external/orphanx/SKILL.md"
for r in claude agents cursor codex; do ln -s "$SB_TMP/external/orphanx" "$SB_TMP/roots/$r/orphanx"; done
um="$(sb_skillbox doctor --json)"
sb_eq "orphanx flagged UNMANAGED" \
  "$(printf '%s' "$um" | python3 -c 'import sys,json;print(sum(1 for p in json.load(sys.stdin)["problems"] if p["kind"]=="UNMANAGED" and p["where"]=="orphanx"))')" "1"
sb_eq "UNMANAGED is non-blocking (doctor exits 0)" "$(sb_skillbox doctor >/dev/null 2>&1; echo $?)" "0"

# 11b. A link declared external is owned by another installer: not UNMANAGED.
sed -i.bak '1i\
external = ["orphanx"]
' "$SKILLBOX_MANIFEST" && rm -f "$SKILLBOX_MANIFEST.bak"
ext="$(sb_skillbox doctor --json --strict)"
sb_eq "external orphanx is not flagged UNMANAGED" \
  "$(printf '%s' "$ext" | python3 -c 'import sys,json;print(sum(1 for p in json.load(sys.stdin)["problems"] if p["kind"]=="UNMANAGED" and p["where"]=="orphanx"))')" "0"
sb_eq "external orphanx link left in place" "$([ -L "$SB_TMP/roots/claude/orphanx" ] && echo present || echo gone)" "present"
sed -i.bak '1d' "$SKILLBOX_MANIFEST" && rm -f "$SKILLBOX_MANIFEST.bak"

# 11c. An external name that a source also ships stays with the other installer.
rm "$SB_TMP/roots/claude/alpha" && ln -s "$SB_TMP/external/orphanx" "$SB_TMP/roots/claude/alpha"
sed -i.bak '1i\
external = ["alpha"]
' "$SKILLBOX_MANIFEST" && rm -f "$SKILLBOX_MANIFEST.bak"
sb_ok "sync succeeds with an external name a source also ships" sb_skillbox sync --no-pull
sb_link "sync leaves the external alpha link alone" "$SB_TMP/roots/claude/alpha" "$SB_TMP/external/orphanx"
ext2="$(sb_skillbox doctor --json --strict)"
sb_eq "external alpha is not reported" \
  "$(printf '%s' "$ext2" | python3 -c 'import sys,json;print(sum(1 for p in json.load(sys.stdin)["problems"] if p["where"].endswith("alpha")))')" "0"
rm "$SB_TMP/roots/claude/alpha" && ln -s "$SB_TMP/src/team/skills/ghost" "$SB_TMP/roots/claude/alpha"
sb_ok "sync succeeds with a dangling external link" sb_skillbox sync --no-pull
sb_eq "sync does not prune a dangling external link" "$([ -L "$SB_TMP/roots/claude/alpha" ] && echo present || echo gone)" "present"
sed -i.bak '1s/.*/external = "alpha"/' "$SKILLBOX_MANIFEST" && rm -f "$SKILLBOX_MANIFEST.bak"
sb_eq "malformed external refuses sync" "$(sb_skillbox sync --no-pull >/dev/null 2>&1; echo $?)" "1"
sed -i.bak '1d' "$SKILLBOX_MANIFEST" && rm -f "$SKILLBOX_MANIFEST.bak"
rm "$SB_TMP/roots/claude/alpha" && sb_skillbox sync --no-pull >/dev/null
sb_link "alpha returns to the source once external is removed" "$SB_TMP/roots/claude/alpha" "$SB_TMP/src/team/skills/alpha"

# 12. Dot-prefixed runtime helper symlinks are infrastructure, not skills.
before_dot_count="$(sb_skillbox doctor --json | python3 -c 'import sys,json;print(json.load(sys.stdin)["skills_installed"])')"
mkdir -p "$SB_TMP/system-helper"
printf -- '---\nname: helper\ndescription: helper\n---\n' > "$SB_TMP/system-helper/SKILL.md"
for r in claude agents cursor codex; do ln -s "$SB_TMP/system-helper" "$SB_TMP/roots/$r/.codex-system"; done
dot_doc="$(sb_skillbox doctor --json)"
sb_eq "dot helper is not counted as installed" \
  "$(printf '%s' "$dot_doc" | python3 -c 'import sys,json;print(json.load(sys.stdin)["skills_installed"])')" "$before_dot_count"
sb_eq "dot helper is not flagged unmanaged" \
  "$(printf '%s' "$dot_doc" | python3 -c 'import sys,json;print(sum(1 for p in json.load(sys.stdin)["problems"] if p["where"]==".codex-system"))')" "0"

sb_report
