#!/usr/bin/env python3
"""Hermetic stdlib unittest for the PURE functions of skillbox.py.

No shell, no real fleet. We build a tiny temp world in setUp (tempfile.mkdtemp +
fake source dirs + a TOML manifest), point $SKILLBOX_MANIFEST at it BEFORE the
module is imported, then importlib-load the single-file tool and exercise its
pure functions directly:

  resolve_plan      first-wins winner + collisions in precedence order
  link_one          idempotent, trailing-slash-normalizing, refuses real files
  doctor_problems   induces BROKEN / MISSING / DRIFTED / PARITY
  skill_md_hash     differs on differing SKILL.md, matches on identical
  render_page       returns bytes containing b"skillbox" and a skill name

Every assertion is written so it would FAIL if the function regressed (we test
real source targets, exact tuple kinds, exact return values — no tautologies).

Run: python3 test_unit.py   (exits 0 on success via unittest.main).
"""
import os
import sys
import json
import shutil
import tempfile
import importlib.util
import unittest
from pathlib import Path

SKILLBOX_PY = str(Path(__file__).resolve().parent.parent / "bin" / "skillbox.py")


def _write_skill(skills_dir: Path, name: str, desc: str = None, body: str = "test body"):
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    desc = desc if desc is not None else f"test skill {name}"
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n{body}\n")
    return d


# ── one-time module load ────────────────────────────────────────────────────
# The module reads $SKILLBOX_MANIFEST at import time to compute CONFIG_DIR etc.
# We must point it somewhere harmless BEFORE import so it never touches the real
# fleet. Each test then overrides sb.MANIFEST/CONFIG_DIR/etc. to its own world.
_BOOT_DIR = tempfile.mkdtemp(prefix="skillbox-boot.")
os.environ["SKILLBOX_MANIFEST"] = str(Path(_BOOT_DIR) / "boot.toml")
(Path(_BOOT_DIR) / "boot.toml").write_text("[roots]\n[sources]\n")

_spec = importlib.util.spec_from_file_location("skillbox_under_test", SKILLBOX_PY)
sb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sb)


class SkillboxWorld(unittest.TestCase):
    """Builds a fresh hermetic world per test; resolve+link against it directly."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="skillbox-unit."))
        # canonicalize so resolve()/readlink comparisons line up on macOS /var->/private/var
        self.tmp = Path(os.path.realpath(self.tmp))

        # 4 fake runtime roots
        self.roots = {}
        for r in ("claude", "agents", "cursor", "codex"):
            (self.tmp / "roots" / r).mkdir(parents=True)
            self.roots[r] = self.tmp / "roots" / r

        # team source (priority 1): alpha, beta, shared
        self.team_dir = self.tmp / "src" / "team" / "skills"
        self.team_dir.mkdir(parents=True)
        _write_skill(self.team_dir, "alpha")
        _write_skill(self.team_dir, "beta")
        _write_skill(self.team_dir, "shared", "shared — team version (wins)")

        # private source (priority 2): gamma, shared (collides → shadowed)
        self.priv_dir = self.tmp / "src" / "private" / "skills"
        self.priv_dir.mkdir(parents=True)
        _write_skill(self.priv_dir, "gamma")
        _write_skill(self.priv_dir, "shared", "shared — private version (shadowed)")

        # solo single-skill source (priority 3): repo root IS the skill
        self.solo_dir = self.tmp / "src" / "solo"
        self.solo_dir.mkdir(parents=True)
        (self.solo_dir / "SKILL.md").write_text(
            "---\nname: solo\ndescription: single-skill source\n---\n# solo\n")

        manifest = self.tmp / "skills.toml"
        manifest.write_text(
            "[roots]\n"
            f'claude = "{self.roots["claude"]}"\n'
            f'agents = "{self.roots["agents"]}"\n'
            f'cursor = "{self.roots["cursor"]}"\n'
            f'codex  = "{self.roots["codex"]}"\n'
            "\n[sources.team]\n"
            f'path = "{self.team_dir}"\n'
            "priority = 1\n"
            "\n[sources.private]\n"
            f'path = "{self.priv_dir}"\n'
            "priority = 2\n"
            "\n[sources.solo]\n"
            f'path = "{self.solo_dir}"\n'
            "priority = 3\n"
            'single_skill = "solo"\n')

        # Point the module's module-level config at THIS world for the test.
        self.manifest = manifest
        sb.MANIFEST = manifest
        sb.CONFIG_DIR = manifest.parent

        self.roots_loaded, self.sources = sb.load()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── resolve_plan ─────────────────────────────────────────────────────────
    def test_resolve_plan_first_wins_winner(self):
        plan, collisions = sb.resolve_plan(self.sources)

        # every defined name resolves
        self.assertEqual(
            set(plan.keys()), {"alpha", "beta", "shared", "gamma", "solo"})

        # winner for a non-collided name points at its only source path
        win_src, win_path = plan["alpha"]
        self.assertEqual(win_src["id"], "team")
        self.assertEqual(Path(win_path), self.team_dir / "alpha")

        # gamma exists only in private — lower-priority source still contributes
        g_src, g_path = plan["gamma"]
        self.assertEqual(g_src["id"], "private")
        self.assertEqual(Path(g_path), self.priv_dir / "gamma")

        # single_skill: the solo repo root IS the skill folder
        s_src, s_path = plan["solo"]
        self.assertEqual(s_src["id"], "solo")
        self.assertEqual(Path(s_path), self.solo_dir)

        # FIRST-WINS: 'shared' is defined in both team(prio1) and private(prio2);
        # team must win and the winner path must be the TEAM copy, not private.
        sh_src, sh_path = plan["shared"]
        self.assertEqual(sh_src["id"], "team")
        self.assertEqual(Path(sh_path), self.team_dir / "shared")
        self.assertNotEqual(Path(sh_path), self.priv_dir / "shared")

    def test_resolve_plan_collisions_in_precedence_order(self):
        _, collisions = sb.resolve_plan(self.sources)
        # only 'shared' collides; non-collided names absent from the dict
        self.assertIn("shared", collisions)
        self.assertNotIn("alpha", collisions)
        self.assertNotIn("gamma", collisions)
        # owners listed winner-first in precedence order
        self.assertEqual(collisions["shared"], ["team", "private"])

    # ── link_one ───────────────────────────────────────────────────────────
    def test_link_one_links_then_idempotent(self):
        path = self.team_dir / "alpha"
        linked, relinked = sb.link_one(self.roots_loaded, "alpha", path, quiet=True)
        # links into all 4 roots on first call
        self.assertEqual((linked, relinked), (4, 0))
        for root in self.roots_loaded.values():
            dst = root / "alpha"
            self.assertTrue(dst.is_symlink())
            # ABSOLUTE per-skill symlink pointing at the exact source path
            self.assertEqual(os.readlink(dst).rstrip("/"), str(path))

        # second call is a no-op: nothing linked, nothing relinked
        linked2, relinked2 = sb.link_one(self.roots_loaded, "alpha", path, quiet=True)
        self.assertEqual((linked2, relinked2), (0, 0))

    def test_link_one_refuses_real_file_without_clobber(self):
        # plant a REAL file (not a symlink) where the link would go
        real = self.roots_loaded["claude"] / "alpha"
        real.write_text("hand-written, not skillbox-owned")
        path = self.team_dir / "alpha"

        linked, relinked = sb.link_one(self.roots_loaded, "alpha", path, quiet=True)
        # claude is skipped (real file); the other 3 roots get linked
        self.assertEqual((linked, relinked), (3, 0))
        # the real file is untouched: still a regular file, content intact
        self.assertFalse(real.is_symlink())
        self.assertTrue(real.is_file())
        self.assertEqual(real.read_text(), "hand-written, not skillbox-owned")

    def test_link_one_normalizes_trailing_slash(self):
        path = self.team_dir / "alpha"
        # Pre-create a skillbox-owned link in claude WITH a trailing-slash target,
        # mimicking the old captain sync.sh writer. A raw string compare would
        # consider this drifted and relink; the normalizer must treat it as equal.
        claude = self.roots_loaded["claude"]
        (claude / "alpha").symlink_to(str(path) + "/")
        self.assertEqual(os.readlink(claude / "alpha"), str(path) + "/")

        linked, relinked = sb.link_one(self.roots_loaded, "alpha", path, quiet=True)
        # claude must NOT relink (trailing slash normalized to equal);
        # the other 3 roots are fresh links.
        self.assertEqual((linked, relinked), (3, 0))
        # claude's link was left exactly as-is (trailing slash preserved, no rewrite)
        self.assertEqual(os.readlink(claude / "alpha"), str(path) + "/")

    # ── skill_md_hash ────────────────────────────────────────────────────────
    def test_skill_md_hash_differs_and_matches(self):
        # team/shared and private/shared have DIFFERENT SKILL.md descriptions
        h_team = sb.skill_md_hash(self.team_dir / "shared")
        h_priv = sb.skill_md_hash(self.priv_dir / "shared")
        self.assertIsNotNone(h_team)
        self.assertIsNotNone(h_priv)
        self.assertNotEqual(h_team, h_priv)

        # identical SKILL.md content → identical hash
        twin = self.tmp / "twin"
        twin.mkdir()
        shutil.copyfile(self.team_dir / "shared" / "SKILL.md", twin / "SKILL.md")
        self.assertEqual(sb.skill_md_hash(twin), h_team)

        # missing SKILL.md → None (OSError path)
        empty = self.tmp / "no_skill_here"
        empty.mkdir()
        self.assertIsNone(sb.skill_md_hash(empty))

    # ── doctor_problems ────────────────────────────────────────────────────
    def _kinds(self, problems):
        return [(k, w) for (k, w, _d) in problems]

    def test_doctor_clean_has_no_blocking(self):
        # fully sync everything, then doctor should be clean except SHADOWED(shared)
        plan, _ = sb.resolve_plan(self.sources)
        for name, (src, path) in plan.items():
            sb.link_one(self.roots_loaded, name, path, quiet=True)
        problems, installed, collisions, parity = sb.doctor_problems(
            self.roots_loaded, self.sources)
        kinds = {k for (k, _w, _d) in problems}
        # the only problem on a healthy fully-synced fleet is the SHADOWED note
        self.assertEqual(kinds, {"SHADOWED"})
        # SHADOWED is non-blocking
        blocking = [p for p in problems if p[0] != "SHADOWED"]
        self.assertEqual(blocking, [])
        # 'shared' parity holds (same winner linked into every root)
        self.assertTrue(parity["shared"]["consistent"])

    def test_doctor_detects_broken(self):
        # link alpha everywhere, then make the source disappear → dangling links
        path = self.team_dir / "alpha"
        sb.link_one(self.roots_loaded, "alpha", path, quiet=True)
        shutil.rmtree(path)  # target gone; links now dangle
        problems, installed, _c, _p = sb.doctor_problems(self.roots_loaded, self.sources)
        broken = [(k, w) for (k, w, _d) in problems if k == "BROKEN"]
        # one BROKEN per root that has the dangling link
        self.assertEqual(len(broken), 4)
        self.assertIn(("BROKEN", "claude/alpha"), broken)
        # alpha is no longer in any source → not a DRIFTED, purely BROKEN
        self.assertNotIn("DRIFTED", {k for (k, _w, _d) in problems})

    def test_doctor_detects_missing(self):
        # link beta everywhere, then remove ONLY the claude link.
        # beta is still present in other runtimes → MISSING for claude.
        path = self.team_dir / "beta"
        sb.link_one(self.roots_loaded, "beta", path, quiet=True)
        (self.roots_loaded["claude"] / "beta").unlink()
        problems, _i, _c, _p = sb.doctor_problems(self.roots_loaded, self.sources)
        missing = [(k, w) for (k, w, _d) in problems if k == "MISSING"]
        self.assertEqual(missing, [("MISSING", "claude/beta")])

    def test_doctor_detects_drifted(self):
        # link 'shared' correctly to the team winner in 3 roots, but point claude
        # at the PRIVATE (shadowed) copy → DRIFTED relative to the winner.
        win_path = self.team_dir / "shared"
        for r in ("agents", "cursor", "codex"):
            (self.roots_loaded[r] / "shared").symlink_to(win_path)
        (self.roots_loaded["claude"] / "shared").symlink_to(self.priv_dir / "shared")

        problems, _i, _c, _p = sb.doctor_problems(self.roots_loaded, self.sources)
        drifted = [(k, w) for (k, w, _d) in problems if k == "DRIFTED"]
        self.assertEqual(drifted, [("DRIFTED", "claude/shared")])

    def test_doctor_detects_parity(self):
        # Make claude's 'alpha' link point at a DIFFERENT SKILL.md content than the
        # other roots, so doctor flags PARITY (SKILL.md differs across runtimes).
        # Build a divergent alpha twin with different content.
        twin_parent = self.tmp / "src" / "twin"
        twin_dir = _write_skill(twin_parent, "alpha", "alpha — DIVERGENT content here")
        win_path = self.team_dir / "alpha"
        for r in ("agents", "cursor", "codex"):
            (self.roots_loaded[r] / "alpha").symlink_to(win_path)
        (self.roots_loaded["claude"] / "alpha").symlink_to(twin_dir)

        problems, _i, _c, parity = sb.doctor_problems(self.roots_loaded, self.sources)
        parity_probs = [(k, w) for (k, w, _d) in problems if k == "PARITY"]
        self.assertEqual(parity_probs, [("PARITY", "alpha")])
        # parity dict agrees alpha is inconsistent
        self.assertFalse(parity["alpha"]["consistent"])

    # ── render_page ──────────────────────────────────────────────────────────
    def test_render_page_bytes_and_skill_name(self):
        # install gamma so it appears in the Installed pane too
        sb.link_one(self.roots_loaded, "gamma", self.priv_dir / "gamma", quiet=True)
        state = {"flash": "", "error": ""}
        out = sb.render_page(self.roots_loaded, self.sources, state)
        self.assertIsInstance(out, bytes)
        # branding present
        self.assertIn(b"skillbox", out)
        # a real skill name from our world is rendered
        self.assertIn(b"gamma", out)
        self.assertIn(b"alpha", out)
        # it is a full HTML document
        self.assertIn(b"<!doctype html>", out)
        # source rail + per-row source attribution drive the by-source filter
        self.assertIn(b'class="srcbar"', out)
        self.assertIn(b'data-source=', out)
        self.assertIn(b'data-show="installed"', out)

    # ── require_name (security: a name must be one safe path segment) ─────────
    def test_require_name_accepts_valid(self):
        for ok in ("alpha", "foo-bar", "foo.bar", "a_b", "shared-mine", "x1"):
            self.assertEqual(sb.require_name(ok), ok)

    def test_require_name_rejects_traversal_and_junk(self):
        for bad in ("", ".", "..", "../x", "a/b", "/abs", "a..b", ".hidden", "a b", "a\nb", "x" * 65):
            with self.assertRaises(SystemExit, msg=f"{bad!r} must be rejected"):
                sb.require_name(bad)

    # ── no TOML support (no tomllib AND no tomli) → clean exit, not a traceback ──
    def test_load_without_toml_support_exits_clean(self):
        old_tomllib = sb.tomllib
        try:
            sb.tomllib = None
            with self.assertRaises(SystemExit) as cm:
                sb.load()
            self.assertIn("tomli", str(cm.exception))
        finally:
            sb.tomllib = old_tomllib
            sb.MANIFEST = self.manifest

    # ── owner_of (list attributes by readlink target, not the plan winner) ────
    def test_owner_of_uses_readlink_not_winner(self):
        plan, _ = sb.resolve_plan(self.sources)
        # 'shared' collides: team wins. A link at the PRIVATE copy attributes to private.
        self.assertEqual(
            sb.owner_of((self.priv_dir / "shared").resolve(), self.sources, plan, "shared"),
            "private")
        # a link at the winner copy attributes to the winner
        self.assertEqual(
            sb.owner_of((self.team_dir / "shared").resolve(), self.sources, plan, "shared"),
            "team")
        # an unmatched target falls back to the plan winner
        self.assertEqual(
            sb.owner_of(self.tmp / "nowhere", self.sources, plan, "shared"), "team")
        # an unknown name with no match → unmanaged
        self.assertEqual(
            sb.owner_of(self.tmp / "nowhere", self.sources, plan, "ghost"), "?(unmanaged)")


if __name__ == "__main__":
    # clean up the boot dir on the way out (best-effort)
    import atexit
    atexit.register(lambda: shutil.rmtree(_BOOT_DIR, ignore_errors=True))
    unittest.main(verbosity=2)
