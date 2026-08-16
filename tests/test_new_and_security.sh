#!/usr/bin/env bash
# Scenario: new_and_security — the `new` authoring path + scaffold + name-security guard.
# Hermetic: operates only inside $SB_TMP via the sandbox manifest. Never touches the real fleet.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib/sandbox.sh
. "$DIR/lib/sandbox.sh"

trap sb_teardown EXIT
sb_setup

# ── 1. `new foo --repo private` scaffolds into the private source and links everywhere ──
out="$(sb_skillbox new foo --repo private 2>&1)"; rc=$?
sb_eq   "new foo --repo private exits 0" "$rc" "0"
sb_contains "new reports it created the SKILL.md" "$out" "created"

foo_skill="$SB_TMP/src/private/skills/foo/SKILL.md"
sb_ok   "scaffold file exists at private source path" test -f "$foo_skill"
# Negative control: it should NOT have leaked into the team source.
sb_ok   "scaffold did NOT land in team source" test ! -e "$SB_TMP/src/team/skills/foo/SKILL.md"

# Linked into ALL 4 runtime roots, each an absolute symlink to the exact source dir.
expected_target="$SB_TMP/src/private/skills/foo"
for r in claude agents cursor codex; do
  sb_link "foo linked into $r root -> private source" "$SB_TMP/roots/$r/foo" "$expected_target"
done

# ── 2. Scaffold content: frontmatter name + the generic skill-template sections ──
body="$(cat "$foo_skill")"
sb_contains "scaffold frontmatter carries name: foo"        "$body" "name: foo"
sb_contains "scaffold has a Purpose section"                "$body" "## Purpose"
sb_contains "scaffold has a When to use section"            "$body" "## When to use"
sb_contains "scaffold has a How it works section"           "$body" "## How it works"

# ── 3. `new` REFUSES bad inputs ──
# 3a. A name that already resolves (foo now exists in private).
sb_fails "new refuses a name that already resolves" sb_skillbox new foo --repo private
# Also refuse a name owned by another source (alpha lives in team).
sb_fails "new refuses an existing team-owned name"  sb_skillbox new alpha --repo private

# 3b. A single_skill source is not scaffoldable.
sb_fails "new --repo solo (single_skill) refused"   sb_skillbox new whatever --repo solo
sb_ok   "refused solo new left no folder behind"    test ! -e "$SB_TMP/src/solo/whatever"

# 3c. An unknown repo id.
solo_err="$(sb_skillbox new whatever --repo nonesuch 2>&1)"; src_rc=$?
sb_eq       "new --repo nonesuch exits nonzero"     "$([ $src_rc -ne 0 ] && echo nz || echo z)" "nz"
sb_contains "unknown repo error names the bad id"   "$solo_err" "nonesuch"

# ── 4. A `foo-mine` suffixed override is allowed ALONGSIDE the shared base skill ──
# 'shared' already resolves (team wins). A distinct name 'shared-mine' must be scaffoldable.
out_mine="$(sb_skillbox new shared-mine --repo private 2>&1)"; mine_rc=$?
sb_eq   "new shared-mine --repo private exits 0" "$mine_rc" "0"
sb_ok   "shared-mine scaffolded in private"      test -f "$SB_TMP/src/private/skills/shared-mine/SKILL.md"
sb_link "shared-mine linked into claude root"    "$SB_TMP/roots/claude/shared-mine" "$SB_TMP/src/private/skills/shared-mine"
# The shared base skill is untouched and still resolves to the team (winning) version.
sb_ok   "base 'shared' still present in team"   test -f "$SB_TMP/src/team/skills/shared/SKILL.md"

# ── 5. Default (no --repo) errors cleanly because the default source is absent here ──
# Proves the default is wired to the configured default source, not silently another.
unset SKILLBOX_DEFAULT_SOURCE   # exercise the built-in default ('personal')
def_err="$(sb_skillbox new orphan 2>&1)"; def_rc=$?
sb_eq       "new with no --repo exits nonzero (default source absent)" "$([ $def_rc -ne 0 ] && echo nz || echo z)" "nz"
sb_contains "default error names the missing 'personal' repo"    "$def_err" "personal"
sb_ok       "no orphan scaffolded into team"    test ! -e "$SB_TMP/src/team/skills/orphan"
sb_ok       "no orphan scaffolded into private" test ! -e "$SB_TMP/src/private/skills/orphan"

# ── 6. A retired source name cannot be re-created into its own exclusion list ──
perl -0pi -e 's/(\[sources\.private\]\npath = "[^"]+"\npriority = 2\n)/$1exclude = ["retired-alias"]\n/' \
  "$SKILLBOX_MANIFEST"
sb_fails "new refuses a name excluded by its target source" \
  sb_skillbox new retired-alias --repo private
sb_ok "excluded new leaves no unreachable folder behind" \
  test ! -e "$SB_TMP/src/private/skills/retired-alias"

sb_report
