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
import io
import json
import shutil
import tempfile
import importlib.util
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

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

    def test_source_exclude_reveals_lower_priority_copy(self):
        # Exclusion belongs to one source only. Hiding the high-priority
        # team/shared must let the private/shared copy become the real winner.
        team = dict(self.sources[0], exclude=frozenset({"shared"}))
        plan, collisions = sb.resolve_plan([team, *self.sources[1:]])
        src, path = plan["shared"]
        self.assertEqual(src["id"], "private")
        self.assertEqual(Path(path), self.priv_dir / "shared")
        self.assertNotIn("shared", collisions)

    def test_source_exclude_hides_a_single_skill_source(self):
        # A single-skill source is how the global Shadow leaf is mounted. It
        # must support the same reversible retirement boundary as a directory.
        solo = dict(self.sources[2], exclude=frozenset({"solo"}))
        plan, _ = sb.resolve_plan([*self.sources[:2], solo])
        self.assertNotIn("solo", plan)
        self.assertEqual(sb.source_skills(solo), {})

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
        real.write_text("hand-written regular file")
        path = self.team_dir / "alpha"

        linked, relinked = sb.link_one(self.roots_loaded, "alpha", path, quiet=True)
        # claude is skipped (real file); the other 3 roots get linked
        self.assertEqual((linked, relinked), (3, 0))
        # the real file is untouched: still a regular file, content intact
        self.assertFalse(real.is_symlink())
        self.assertTrue(real.is_file())
        self.assertEqual(real.read_text(), "hand-written regular file")

    def test_link_one_replaces_any_symlink_in_configured_slot(self):
        path = self.team_dir / "alpha"
        foreign_target = self.tmp / "foreign-skill"
        foreign_target.mkdir()
        slot = self.roots_loaded["claude"] / "alpha"
        slot.symlink_to(foreign_target)

        linked, relinked = sb.link_one(self.roots_loaded, "alpha", path, quiet=True)

        self.assertEqual((linked, relinked), (3, 1))
        self.assertEqual(slot.resolve(), path.resolve())
        self.assertTrue(foreign_target.is_dir())

    def test_cmd_rm_unlinks_any_symlink_in_configured_slot(self):
        foreign_target = self.tmp / "foreign-skill"
        foreign_target.mkdir()
        slot = self.roots_loaded["claude"] / "alpha"
        slot.symlink_to(foreign_target)

        output = io.StringIO()
        with redirect_stdout(output):
            sb.cmd_rm(self.roots_loaded, "alpha")

        self.assertFalse(slot.is_symlink())
        self.assertFalse(slot.exists())
        self.assertTrue(foreign_target.is_dir())
        self.assertIn("unlinked claude/alpha", output.getvalue())

    def test_cmd_retire_preserves_a_slot_replaced_during_mutation(self):
        # The preflight check and mutation are separate filesystem operations.
        # If another writer replaces a slot in between, retirement must preserve
        # that replacement rather than deleting it by the stale pathname.
        team = dict(self.sources[0], exclude=frozenset({"beta"}))
        sources = [team, *self.sources[1:]]
        target = self.team_dir / "beta"
        slots = [root / "beta" for root in self.roots_loaded.values()]
        for slot in slots:
            slot.symlink_to(target)

        original_rename_noreplace = sb._rename_noreplace_at
        foreign_contents = "FOREIGN\n"
        replaced = {"done": False}

        def replace_source_then_park(src_fd, src_name, dst_fd, dst_name):
            if not replaced["done"]:
                os.unlink(src_name, dir_fd=src_fd)
                fd = os.open(
                    src_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=src_fd,
                )
                try:
                    os.write(fd, foreign_contents.encode())
                finally:
                    os.close(fd)
                replaced["done"] = True
            return original_rename_noreplace(src_fd, src_name, dst_fd, dst_name)

        with patch.object(sb, "_rename_noreplace_at", new=replace_source_then_park):
            with self.assertRaises(SystemExit) as raised:
                sb.cmd_retire(self.roots_loaded, sources, "beta", "team")

        self.assertTrue(replaced["done"])
        self.assertIn("changed", str(raised.exception))
        self.assertFalse(slots[0].exists())
        self.assertFalse(slots[0].is_symlink())
        for slot in slots[1:]:
            self.assertTrue(slot.is_symlink())
            self.assertEqual(slot.resolve(), target.resolve())
        journals = list(slots[0].parent.glob(".skillbox-retire-beta-*"))
        self.assertEqual(len(journals), 1)
        preserved = journals[0] / "mount"
        self.assertTrue(preserved.is_file())
        self.assertFalse(preserved.is_symlink())
        self.assertEqual(preserved.read_text(), foreign_contents)

    def test_cmd_retire_never_overwrites_a_raced_recovery_entry(self):
        # `rename()` replaces an existing destination on POSIX.  A recovery
        # journal is only safe if the final move refuses an entry another
        # writer planted in it; the live source link must remain untouched.
        team = dict(self.sources[0], exclude=frozenset({"beta"}))
        sources = [team, *self.sources[1:]]
        target = self.team_dir / "beta"
        slots = [root / "beta" for root in self.roots_loaded.values()]
        for slot in slots:
            slot.symlink_to(target)

        original_rename_noreplace = getattr(sb, "_rename_noreplace_at", None)
        planted = {"done": False}

        def plant_archive_then_move(src_fd, src_name, dst_fd, dst_name):
            if not planted["done"]:
                fd = os.open(
                    dst_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=dst_fd,
                )
                try:
                    os.write(fd, b"FOREIGN RECOVERY ENTRY\n")
                finally:
                    os.close(fd)
                planted["done"] = True
            return original_rename_noreplace(src_fd, src_name, dst_fd, dst_name)

        with patch.object(
            sb,
            "_rename_noreplace_at",
            new=plant_archive_then_move,
            create=True,
        ):
            with self.assertRaises(SystemExit) as raised:
                sb.cmd_retire(self.roots_loaded, sources, "beta", "team")

        self.assertTrue(planted["done"])
        self.assertIn("recovery retained", str(raised.exception))
        for slot in slots:
            self.assertTrue(slot.is_symlink())
            self.assertEqual(slot.resolve(), target.resolve())
        journals = list(self.tmp.rglob(".skillbox-retire-beta-*"))
        self.assertEqual(len(journals), 1)
        preserved = journals[0] / "mount"
        self.assertTrue(preserved.is_file())
        self.assertFalse(preserved.is_symlink())
        self.assertEqual(preserved.read_text(), "FOREIGN RECOVERY ENTRY\n")

    def test_cmd_retire_refuses_to_claim_a_journal_path_replaced_mid_park(self):
        # The recovery journal is held by an FD, so a pathname rename cannot
        # divert the no-replace move.  But its old pathname would no longer be
        # a truthful recovery receipt.  Detect that and fail without printing
        # the stale path; the captured link remains in the moved journal.
        team = dict(self.sources[0], exclude=frozenset({"beta"}))
        sources = [team, *self.sources[1:]]
        target = self.team_dir / "beta"
        slots = [root / "beta" for root in self.roots_loaded.values()]
        for slot in slots:
            slot.symlink_to(target)

        original_park = sb._park_slot_noreplace
        raced = {}

        def move_journal_then_park(root_fd, skill, journal_fd):
            if not raced:
                root = slots[0].parent
                journals = list(root.glob(".skillbox-retire-beta-*"))
                self.assertEqual(len(journals), 1)
                original = journals[0]
                moved = root / f"{original.name}-moved"
                os.rename(original, moved)
                original.mkdir(mode=0o700)
                raced.update(original=original, moved=moved)
            return original_park(root_fd, skill, journal_fd)

        with patch.object(sb, "_park_slot_noreplace", new=move_journal_then_park):
            with self.assertRaises(SystemExit) as raised:
                sb.cmd_retire(self.roots_loaded, sources, "beta", "team")

        message = str(raised.exception)
        self.assertIn("nominal recovery path is untrusted", message)
        self.assertNotIn(str(raced["original"] / "mount"), message)
        self.assertFalse(slots[0].exists())
        self.assertFalse(slots[0].is_symlink())
        self.assertTrue((raced["moved"] / "mount").is_symlink())
        self.assertEqual((raced["moved"] / "mount").resolve(), target.resolve())
        self.assertFalse((raced["original"] / "mount").exists())
        for slot in slots[1:]:
            self.assertTrue(slot.is_symlink())
            self.assertEqual(slot.resolve(), target.resolve())

    def test_cmd_retire_refuses_to_claim_a_runtime_root_replaced_mid_park(self):
        # Root FDs anchor the move even if a same-user writer renames the
        # configured root.  The configured pathname is then a false receipt,
        # so retirement must fail rather than print it as recovery evidence.
        team = dict(self.sources[0], exclude=frozenset({"beta"}))
        sources = [team, *self.sources[1:]]
        target = self.team_dir / "beta"
        slots = [root / "beta" for root in self.roots_loaded.values()]
        for slot in slots:
            slot.symlink_to(target)

        original_park = sb._park_slot_noreplace
        raced = {}

        def move_root_then_park(root_fd, skill, journal_fd):
            if not raced:
                root = slots[0].parent
                moved = root.parent / f"{root.name}-moved"
                os.rename(root, moved)
                root.mkdir(mode=0o700)
                raced.update(original=root, moved=moved)
            return original_park(root_fd, skill, journal_fd)

        with patch.object(sb, "_park_slot_noreplace", new=move_root_then_park):
            with self.assertRaises(SystemExit) as raised:
                sb.cmd_retire(self.roots_loaded, sources, "beta", "team")

        message = str(raised.exception)
        self.assertIn("nominal recovery path is untrusted", message)
        journals = list(raced["moved"].glob(".skillbox-retire-beta-*"))
        self.assertEqual(len(journals), 1)
        actual_archive = journals[0] / "mount"
        self.assertNotIn(str(raced["original"] / journals[0].name / "mount"), message)
        self.assertTrue(actual_archive.is_symlink())
        self.assertEqual(actual_archive.resolve(), target.resolve())
        self.assertFalse((raced["original"] / "beta").exists())
        self.assertFalse((raced["original"] / "beta").is_symlink())
        for slot in slots[1:]:
            self.assertTrue(slot.is_symlink())
            self.assertEqual(slot.resolve(), target.resolve())

    def test_mutation_lock_refuses_a_second_skillbox_writer(self):
        # All public mount mutations share one cooperative lock so `rm`/sync
        # cannot race a retirement after its preflight passes.
        with sb.mutation_lock():
            with self.assertRaises(SystemExit) as raised:
                with sb.mutation_lock():
                    pass
        self.assertIn("another skillbox mutation", str(raised.exception).lower())

    def test_main_takes_the_mutation_lock_before_loading_a_sync_plan(self):
        # A stale sync that loaded its plan before waiting could re-mount a
        # freshly retired alias.  The CLI must refuse before it even reads the
        # manifest while another Skillbox writer holds the shared lock.
        with sb.mutation_lock(), \
             patch.object(sb, "load", wraps=sb.load) as load_manifest, \
             patch.object(sys, "argv", ["skillbox", "sync", "--no-pull"]):
            with self.assertRaises(SystemExit) as raised:
                sb.main()
        self.assertIn("another skillbox mutation", str(raised.exception).lower())
        load_manifest.assert_not_called()
        for root in self.roots_loaded.values():
            self.assertFalse((root / "alpha").exists())
            self.assertFalse((root / "alpha").is_symlink())

    def test_cmd_retire_preserves_earlier_link_when_later_park_fails(self):
        # A multi-root retirement must never delete an earlier link if a later
        # root refuses the move. The first link remains in its hidden recovery
        # folder instead of being renamed back over a concurrently new slot.
        team = dict(self.sources[0], exclude=frozenset({"beta"}))
        sources = [team, *self.sources[1:]]
        target = self.team_dir / "beta"
        slots = [root / "beta" for root in self.roots_loaded.values()]
        for slot in slots:
            slot.symlink_to(target)

        original_rename_noreplace = sb._rename_noreplace_at
        calls = []

        def park_then_fail(src_fd, src_name, dst_fd, dst_name):
            calls.append(src_name)
            if len(calls) == 2:
                raise PermissionError("simulated later-root refusal")
            return original_rename_noreplace(src_fd, src_name, dst_fd, dst_name)

        with patch.object(sb, "_rename_noreplace_at", new=park_then_fail):
            with self.assertRaises(SystemExit) as raised:
                sb.cmd_retire(self.roots_loaded, sources, "beta", "team")

        self.assertIn("recovery retained", str(raised.exception))
        self.assertFalse(slots[0].exists())
        self.assertFalse(slots[0].is_symlink())
        for slot in slots[1:]:
            self.assertTrue(slot.is_symlink())
            self.assertEqual(slot.resolve(), target.resolve())
        journals = list(slots[0].parent.glob(".skillbox-retire-beta-*"))
        self.assertEqual(len(journals), 1)
        self.assertTrue((journals[0] / "mount").is_symlink())
        self.assertEqual((journals[0] / "mount").resolve(), target.resolve())

    def test_cmd_retire_parks_relative_symlinks(self):
        # A valid source link may be relative to its runtime root. Moving it
        # into a hidden journal must validate that original interpretation,
        # not treat the archived relative path as a foreign dangling link.
        team = dict(self.sources[0], exclude=frozenset({"beta"}))
        sources = [team, *self.sources[1:]]
        target = self.team_dir / "beta"
        slots = [root / "beta" for root in self.roots_loaded.values()]
        raw_targets = set()
        for slot in slots:
            raw_target = os.path.relpath(target, slot.parent)
            raw_targets.add(raw_target)
            slot.symlink_to(raw_target)

        with redirect_stdout(io.StringIO()):
            sb.cmd_retire(self.roots_loaded, sources, "beta", "team")

        for slot in slots:
            self.assertFalse(slot.exists())
            self.assertFalse(slot.is_symlink())
        # Do not use Path.rglob here: Python's recursive glob treatment of
        # dot-prefixed journal folders differs across supported versions. Each
        # runtime root has exactly one known hidden journal after this success.
        parked = []
        for root in self.roots_loaded.values():
            journals = list(root.glob(".skillbox-retire-beta-*"))
            self.assertEqual(len(journals), 1)
            parked.append(journals[0] / "mount")
        self.assertEqual(len(parked), len(slots))
        self.assertTrue(all(path.is_symlink() for path in parked))
        self.assertEqual({os.readlink(path) for path in parked}, raw_targets)

    def test_prune_preserves_dangling_link_outside_configured_sources(self):
        foreign_parent = self.tmp / "foreign-source"
        foreign_parent.mkdir()
        slot = self.roots_loaded["claude"] / "foreign"
        slot.symlink_to(foreign_parent / "missing-skill")

        cleaned = sb.prune_dangling(
            self.roots_loaded, self.sources, quiet=True
        )

        self.assertEqual(cleaned, 0)
        self.assertTrue(slot.is_symlink())

    def test_link_one_normalizes_trailing_slash(self):
        path = self.team_dir / "alpha"
        # Pre-create a configured-slot link in claude WITH a trailing-slash target,
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

    # ── scrub / private-boundary guards ─────────────────────────────────────
    def test_skill_private_boundary_leo_suffix(self):
        d = _write_skill(self.priv_dir, "pilot-leo", "overlay")
        self.assertEqual(sb.skill_private_boundary(d, "pilot-leo"), ["leo-overlay suffix"])

    def test_skill_private_boundary_keep_private_marker(self):
        d = _write_skill(self.priv_dir, "vault", "vault", "KEEP-PRIVATE\n")
        self.assertEqual(sb.skill_private_boundary(d, "vault"), ["KEEP-PRIVATE marker"])

    def test_skill_private_boundary_clean(self):
        d = _write_skill(self.priv_dir, "gamma", "gamma")
        self.assertEqual(sb.skill_private_boundary(d, "gamma"), [])

    def test_scrub_would_leak_blocks_shared_promote_only(self):
        d = _write_skill(self.priv_dir, "pilot-leo", "overlay")
        self.assertTrue(sb.scrub_would_leak("pilot-leo", d, "team", "private"))
        self.assertFalse(sb.scrub_would_leak("pilot-leo", d, "private", "private"))
        g = _write_skill(self.priv_dir, "gamma", "gamma")
        self.assertFalse(sb.scrub_would_leak("gamma", g, "team", "private"))

    def test_other_skillbox_on_path_ignores_self_and_finds_peer(self):
        fake = self.tmp / "fakebin"
        fake.mkdir()
        peer = fake / "skillbox"
        peer.write_text("#!/bin/sh\necho fake\n")
        peer.chmod(0o755)
        me = Path(sb.__file__).resolve()
        found = sb.other_skillbox_on_path(path_env=str(fake), me=me)
        self.assertEqual(found, [str(peer)])
        # symlink to this script is not a peer
        real = self.tmp / "realbin"
        real.mkdir()
        link = real / "skillbox"
        link.symlink_to(me)
        self.assertEqual(sb.other_skillbox_on_path(path_env=str(real), me=me), [])


if __name__ == "__main__":
    # clean up the boot dir on the way out (best-effort)
    import atexit
    atexit.register(lambda: shutil.rmtree(_BOOT_DIR, ignore_errors=True))
    unittest.main(verbosity=2)
