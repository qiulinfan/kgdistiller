from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import kgdistiller.agent as agent  # noqa: E402
from kgdistiller.agent import AgentIndexError, INDEX_SCHEMA  # noqa: E402
from kgdistiller.ingest import _backup_target, _filesystem_path  # noqa: E402


class AgentIndexGenerationContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="kgdistiller-generation-test-"))
        self.directory_aliases: list[Path] = []

    def tearDown(self) -> None:
        for alias in reversed(self.directory_aliases):
            try:
                if os.name == "nt":
                    os.rmdir(_filesystem_path(alias))
                else:
                    alias.unlink()
            except OSError:
                pass
        shutil.rmtree(_filesystem_path(self.root), ignore_errors=True)

    @staticmethod
    def _write_index(path: Path, sentinel: str) -> None:
        filesystem_path = _filesystem_path(path)
        filesystem_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(filesystem_path))
        try:
            connection.execute(
                "CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO index_meta VALUES (?, ?)",
                [
                    ("schema", json.dumps(INDEX_SCHEMA)),
                    ("sentinel", json.dumps(sentinel)),
                ],
            )
            connection.commit()
        finally:
            connection.close()

    def _logical_path(self, name: str) -> Path:
        return self.root / name / "knowledge.sqlite"

    def _directory_alias(self, target: Path, name: str) -> Path:
        alias = self.root / name
        if os.name == "nt":
            completed = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(target)],
                check=False,
                capture_output=True,
                timeout=10,
            )
            self.assertEqual(
                0,
                completed.returncode,
                completed.stderr.decode(errors="replace"),
            )
        else:
            alias.symlink_to(target, target_is_directory=True)
        self.directory_aliases.append(alias)
        return alias

    @unittest.skipUnless(os.name == "nt", "Windows sharing semantics are required")
    def test_published_marker_makes_cleanup_best_effort(self) -> None:
        logical = self._logical_path("publish-cleanup")
        self._write_index(logical, "old")
        source = self.root / "publish-source.sqlite"
        self._write_index(source, "new")
        reader = sqlite3.connect(logical)
        try:
            with patch.object(
                agent,
                "_cleanup_index_generations",
                side_effect=PermissionError("injected generation cleanup denial"),
            ):
                agent.publish_agent_index_file(source, logical)
        finally:
            reader.close()

        self.assertEqual("new", agent.index_status(logical)["sentinel"])

    @unittest.skipUnless(os.name == "nt", "Windows sharing semantics are required")
    def test_missing_marker_makes_cleanup_best_effort(self) -> None:
        logical = self._logical_path("remove-cleanup")
        self._write_index(logical, "old")
        source = self.root / "remove-source.sqlite"
        self._write_index(source, "new")
        reader = sqlite3.connect(logical)
        try:
            agent.publish_agent_index_file(source, logical)
            with patch.object(
                agent,
                "_cleanup_index_generations",
                side_effect=PermissionError("injected generation cleanup denial"),
            ):
                agent.remove_agent_index(logical)
        finally:
            reader.close()

        self.assertFalse(agent.agent_index_exists(logical))

    def test_marker_close_failure_cannot_revoke_published_generation(self) -> None:
        logical = self._logical_path("marker-close")
        self._write_index(logical, "old")
        source = self.root / "marker-close-source.sqlite"
        self._write_index(source, "new")
        real_close = agent.os.close
        injected = False

        def close_then_fail(descriptor: int) -> None:
            nonlocal injected
            real_close(descriptor)
            if not injected:
                injected = True
                raise PermissionError("injected marker close failure")

        with patch.object(agent.os, "close", side_effect=close_then_fail):
            agent.publish_agent_index_file(source, logical)

        self.assertTrue(injected)
        self.assertEqual("new", agent.index_status(logical)["sentinel"])

    def test_republishing_current_generation_never_moves_referenced_source(self) -> None:
        for marker_failure in (False, True):
            with self.subTest(marker_failure=marker_failure):
                logical = self._logical_path(
                    "source-alias-failure" if marker_failure else "source-alias-success"
                )
                seed = self.root / (
                    "source-alias-failure.sqlite"
                    if marker_failure
                    else "source-alias-success.sqlite"
                )
                self._write_index(seed, "last-good")
                agent.publish_agent_index_file(seed, logical)
                referenced = agent.resolve_agent_index_path(logical)
                before = _filesystem_path(referenced).read_bytes()

                if marker_failure:
                    with patch.object(
                        agent,
                        "_write_index_marker",
                        side_effect=PermissionError("injected marker failure"),
                    ):
                        with self.assertRaises((AgentIndexError, PermissionError)):
                            agent.publish_agent_index_file(referenced, logical)
                else:
                    try:
                        agent.publish_agent_index_file(referenced, logical)
                    except AgentIndexError:
                        # Rejecting a marker-referenced source is also safe.
                        pass

                self.assertTrue(_filesystem_path(referenced).is_file())
                self.assertEqual(before, _filesystem_path(referenced).read_bytes())
                self.assertEqual("last-good", agent.index_status(logical)["sentinel"])

    def test_directory_identity_alias_rollback_preserves_every_last_good(self) -> None:
        for state in ("legacy", "published"):
            with self.subTest(state=state):
                real_parent = self.root / f"identity-{state}-real"
                real_parent.mkdir(parents=True)
                logical = real_parent / "knowledge.sqlite"
                if state == "legacy":
                    self._write_index(logical, "last-good")
                    referenced = logical
                else:
                    seed = self.root / "identity-published-seed.sqlite"
                    self._write_index(seed, "last-good")
                    agent.publish_agent_index_file(seed, logical)
                    referenced = agent.resolve_agent_index_path(logical)

                alias_parent = self._directory_alias(
                    real_parent, f"identity-{state}-alias"
                )
                alias_source = alias_parent / referenced.relative_to(real_parent)
                self.assertNotEqual(agent._path_key(referenced), agent._path_key(alias_source))
                self.assertTrue(
                    os.path.samefile(
                        _filesystem_path(referenced), _filesystem_path(alias_source)
                    )
                )
                before = _filesystem_path(referenced).read_bytes()

                with patch.object(
                    agent,
                    "_write_index_marker",
                    side_effect=PermissionError("injected marker failure"),
                ):
                    with self.assertRaises((AgentIndexError, PermissionError)):
                        agent.publish_agent_index_file(alias_source, logical)

                self.assertTrue(_filesystem_path(referenced).is_file())
                self.assertEqual(before, _filesystem_path(referenced).read_bytes())
                self.assertEqual("last-good", agent.index_status(logical)["sentinel"])

    def test_hardlink_alias_cannot_share_history_with_a_new_generation(self) -> None:
        logical = self._logical_path("hardlink-alias")
        seed = self.root / "hardlink-seed.sqlite"
        self._write_index(seed, "last-good")
        old_generation = agent.publish_agent_index_file(seed, logical)
        old_bytes = _filesystem_path(old_generation).read_bytes()
        alias = self.root / "hardlink-alias.sqlite"
        os.link(_filesystem_path(old_generation), _filesystem_path(alias))
        self.assertTrue(
            os.path.samefile(_filesystem_path(old_generation), _filesystem_path(alias))
        )

        try:
            try:
                new_generation = agent.publish_agent_index_file(alias, logical)
            except AgentIndexError:
                self.assertEqual(old_bytes, _filesystem_path(old_generation).read_bytes())
                self.assertEqual("last-good", agent.index_status(logical)["sentinel"])
                return

            self.assertFalse(
                os.path.samefile(
                    _filesystem_path(old_generation),
                    _filesystem_path(new_generation),
                ),
                "a published generation must not hardlink its referenced history",
            )
            writer = agent._connect(logical)
            try:
                writer.execute(
                    "UPDATE index_meta SET value = ? WHERE key = 'sentinel'",
                    (json.dumps("maintained"),),
                )
                writer.commit()
            finally:
                writer.close()
            self.assertEqual(old_bytes, _filesystem_path(old_generation).read_bytes())
            self.assertEqual("maintained", agent.index_status(logical)["sentinel"])
        finally:
            try:
                _filesystem_path(alias).unlink(missing_ok=True)
            except OSError:
                pass

    @unittest.skipUnless(os.name == "nt", "Windows case-insensitive paths are required")
    def test_case_alias_collision_never_deletes_caller_owned_generation(self) -> None:
        logical = self._logical_path("case-alias-collision")
        root = agent._index_generation_root(logical)
        caller_owned = root / "Generation-00000000000000000001.sqlite"
        self._write_index(caller_owned, "caller-owned")
        before = _filesystem_path(caller_owned).read_bytes()
        generated_name = agent._index_generation_path(logical, 1)
        self.assertTrue(
            os.path.samefile(
                _filesystem_path(caller_owned), _filesystem_path(generated_name)
            )
        )

        try:
            agent.publish_agent_index_file(caller_owned, logical)
        except (AgentIndexError, OSError):
            # Rejecting a name collision is safe as long as the caller-owned
            # source is not treated as this invocation's cleanup target.
            pass

        self.assertTrue(
            _filesystem_path(caller_owned).is_file(),
            "pre-existing case-alias source was deleted during failed publication",
        )
        self.assertEqual(before, _filesystem_path(caller_owned).read_bytes())

    def test_scan_to_open_collision_never_deletes_unowned_destination(self) -> None:
        logical = self._logical_path("scan-open-collision")
        source = self.root / "scan-open-source.sqlite"
        self._write_index(source, "source")
        source_before = _filesystem_path(source).read_bytes()
        collision = agent._index_generation_path(logical, 1)
        collision_bytes = b"caller-owned collision"
        real_copy = agent._copy_index_file
        injected = False

        def collide_then_open(copy_source: Path, destination: Path) -> None:
            nonlocal injected
            if not injected:
                injected = True
                _filesystem_path(destination).write_bytes(collision_bytes)
            real_copy(copy_source, destination)

        with patch.object(agent, "_copy_index_file", side_effect=collide_then_open):
            try:
                agent.publish_agent_index_file(source, logical)
            except (AgentIndexError, OSError):
                # A stable conflict is acceptable; deleting the colliding file
                # that this call did not create is not.
                pass

        self.assertTrue(injected)
        self.assertEqual(source_before, _filesystem_path(source).read_bytes())
        self.assertTrue(
            _filesystem_path(collision).is_file(),
            "scan-to-open collision was unlinked as if publication owned it",
        )
        self.assertEqual(collision_bytes, _filesystem_path(collision).read_bytes())

    def test_same_counter_marker_collision_keeps_selected_generation(self) -> None:
        logical = self._logical_path("same-counter-marker-collision")
        source = self.root / "same-counter-marker-source.sqlite"
        self._write_index(source, "candidate")
        destination = agent._index_generation_path(logical, 1)
        marker = (
            agent._index_generation_root(logical)
            / "current-00000000000000000001-generation"
        )
        real_write_marker = agent._write_index_marker
        collided = False

        def linearize_then_report_collision(
            marker_path: Path, counter: int, kind: str
        ) -> Path:
            nonlocal collided
            real_write_marker(marker_path, counter, kind)
            collided = True
            return real_write_marker(marker_path, counter, kind)

        with patch.object(
            agent,
            "_write_index_marker",
            side_effect=linearize_then_report_collision,
        ):
            try:
                agent.publish_agent_index_file(source, logical)
            except (AgentIndexError, OSError):
                pass

        self.assertTrue(collided)
        self.assertTrue(_filesystem_path(marker).is_file())
        with self.subTest("linearized marker target remains readable"):
            self.assertTrue(
                _filesystem_path(destination).is_file(),
                "marker collision cleanup deleted the selected generation",
            )
        with self.subTest("logical index does not become dangling"):
            self.assertEqual("candidate", agent.index_status(logical)["sentinel"])

    def test_foreign_destination_survives_outer_control_flow_interrupt(self) -> None:
        for interrupt_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(interrupt=interrupt_type.__name__):
                suffix = interrupt_type.__name__.lower()
                logical = self._logical_path(f"foreign-interrupt-{suffix}")
                self._write_index(logical, "old")
                source = self.root / f"foreign-interrupt-{suffix}-source.sqlite"
                self._write_index(source, "candidate")
                source_before = _filesystem_path(source).read_bytes()
                destination = agent._index_generation_path(logical, 1)
                foreign_bytes = f"foreign-{suffix}".encode("ascii")

                def create_foreign_then_interrupt(
                    copy_source: Path, copy_destination: Path
                ) -> tuple[int, int]:
                    self.assertEqual(source, copy_source)
                    self.assertEqual(destination, copy_destination)
                    _filesystem_path(copy_destination).write_bytes(foreign_bytes)
                    raise interrupt_type

                with patch.object(
                    agent,
                    "_copy_index_file",
                    side_effect=create_foreign_then_interrupt,
                ):
                    with self.assertRaises(interrupt_type):
                        agent.publish_agent_index_file(source, logical)

                self.assertEqual(source_before, _filesystem_path(source).read_bytes())
                self.assertTrue(
                    _filesystem_path(destination).is_file(),
                    "outer interrupt cleanup deleted a foreign destination",
                )
                self.assertEqual(
                    foreign_bytes, _filesystem_path(destination).read_bytes()
                )
                self.assertEqual("old", agent.index_status(logical)["sentinel"])

    def test_compatibility_path_replacement_preserves_caller_hardlink(self) -> None:
        logical = self._logical_path("compatibility-path-replacement")
        source = self.root / "compatibility-path-replacement-source.sqlite"
        caller = self.root / "compatibility-caller-owned.sqlite"
        self._write_index(source, "candidate")
        self._write_index(caller, "caller-owned")
        caller_before = _filesystem_path(caller).read_bytes()
        caller_identity = agent._path_identity(caller)
        compatibility = agent._index_generation_root(logical) / ".canonical-1.tmp"
        real_copyfile = agent.shutil.copyfile
        replaced = False

        def replace_before_pathname_reopen(
            copy_source: object, copy_destination: object, *args: object, **kwargs: object
        ) -> object:
            nonlocal replaced
            _filesystem_path(compatibility).unlink()
            os.link(_filesystem_path(caller), _filesystem_path(compatibility))
            replaced = True
            return real_copyfile(copy_source, copy_destination, *args, **kwargs)

        with patch.object(
            agent.shutil,
            "copyfile",
            side_effect=replace_before_pathname_reopen,
        ):
            agent.publish_agent_index_file(source, logical)

        # A safe implementation may stop using shutil.copyfile here entirely.
        # If the vulnerable pathname reopen still exists, the injected hardlink
        # must not let the compatibility copy modify the caller-owned inode.
        self.assertTrue(_filesystem_path(caller).is_file())
        self.assertEqual(caller_identity, agent._path_identity(caller))
        self.assertEqual(
            caller_before,
            _filesystem_path(caller).read_bytes(),
            f"compatibility pathname replacement corrupted caller bytes "
            f"(injection reached={replaced})",
        )
        self.assertEqual("candidate", agent.index_status(logical)["sentinel"])

    def test_authoritative_destination_replacement_never_publishes_foreign(self) -> None:
        logical = self._logical_path("authoritative-destination-replacement")
        self._write_index(logical, "old")
        source = self.root / "authoritative-replacement-source.sqlite"
        foreign = self.root / "authoritative-replacement-foreign.sqlite"
        self._write_index(source, "candidate")
        self._write_index(foreign, "foreign")
        foreign_before = _filesystem_path(foreign).read_bytes()
        foreign_identity = agent._path_identity(foreign)
        destination = agent._index_generation_path(logical, 1)
        replaced = False

        if os.name == "nt":
            real_write_marker = agent._write_index_marker

            def replace_after_copy_then_mark(
                marker_path: Path, counter: int, kind: str
            ) -> Path:
                nonlocal replaced
                _filesystem_path(destination).unlink()
                os.link(_filesystem_path(foreign), _filesystem_path(destination))
                replaced = True
                return real_write_marker(marker_path, counter, kind)

            replacement_patch = patch.object(
                agent,
                "_write_index_marker",
                side_effect=replace_after_copy_then_mark,
            )
        else:
            real_copyfileobj = agent.shutil.copyfileobj

            def replace_while_destination_is_open(
                input_: object, output: object, length: int = 0
            ) -> None:
                nonlocal replaced
                real_copyfileobj(input_, output, length)
                self.assertFalse(
                    output.closed,  # type: ignore[attr-defined]
                    "WSL replacement must occur while the destination fd is open",
                )
                output.flush()  # type: ignore[attr-defined]
                _filesystem_path(destination).unlink()
                os.link(_filesystem_path(foreign), _filesystem_path(destination))
                replaced = True

            replacement_patch = patch.object(
                agent.shutil,
                "copyfileobj",
                side_effect=replace_while_destination_is_open,
            )

        with replacement_patch:
            try:
                agent.publish_agent_index_file(source, logical)
            except (AgentIndexError, OSError):
                pass

        self.assertTrue(replaced)
        self.assertTrue(_filesystem_path(foreign).is_file())
        self.assertEqual(foreign_identity, agent._path_identity(foreign))
        self.assertEqual(foreign_before, _filesystem_path(foreign).read_bytes())
        try:
            sentinel = agent.index_status(logical)["sentinel"]
        except AgentIndexError:
            # Refusing to resolve a detected replacement is fail-closed.
            return
        self.assertIn(
            sentinel,
            {"old", "candidate"},
            "foreign replacement became the authoritative logical index",
        )

    def test_cleanup_identity_check_cannot_unlink_foreign_replacement(self) -> None:
        owned = self.root / "cleanup-owned-partial.sqlite"
        foreign = self.root / "cleanup-foreign-owner.sqlite"
        _filesystem_path(owned).write_bytes(b"owned partial")
        _filesystem_path(foreign).write_bytes(b"foreign caller bytes")
        owned_identity = agent._path_identity(owned)
        foreign_identity = agent._path_identity(foreign)
        foreign_before = _filesystem_path(foreign).read_bytes()
        real_path_identity = agent._path_identity
        replaced = False

        def replace_after_identity_check(path: Path) -> tuple[int, int] | None:
            nonlocal replaced
            observed = real_path_identity(path)
            if not replaced and agent._path_key(path) == agent._path_key(owned):
                _filesystem_path(owned).unlink()
                os.link(_filesystem_path(foreign), _filesystem_path(owned))
                replaced = True
            return observed

        self.assertIsNotNone(owned_identity)
        with patch.object(
            agent,
            "_path_identity",
            side_effect=replace_after_identity_check,
        ):
            agent._unlink_owned_file(owned, owned_identity)  # type: ignore[arg-type]

        self.assertTrue(_filesystem_path(foreign).is_file())
        self.assertEqual(foreign_identity, agent._path_identity(foreign))
        self.assertEqual(foreign_before, _filesystem_path(foreign).read_bytes())
        if replaced:
            self.assertTrue(
                _filesystem_path(owned).is_file(),
                "cleanup unlinked the foreign replacement after a stale stat",
            )
            self.assertEqual(foreign_before, _filesystem_path(owned).read_bytes())

    def test_partial_generation_copy_interrupt_removes_only_owned_orphan(self) -> None:
        logical = self._logical_path("partial-generation-interrupt")
        self._write_index(logical, "last-good")
        source = self.root / "partial-generation-source.sqlite"
        self._write_index(source, "candidate")
        old_before = _filesystem_path(logical).read_bytes()
        source_before = _filesystem_path(source).read_bytes()
        destination = agent._index_generation_path(logical, 1)

        def interrupt_after_partial_copy(
            copy_source: Path, copy_destination: Path
        ) -> None:
            self.assertEqual(source, copy_source)
            self.assertEqual(destination, copy_destination)
            with _filesystem_path(copy_destination).open("xb") as output:
                output.write(b"partial generation")
                output.flush()
            raise KeyboardInterrupt

        with patch.object(
            agent, "_copy_index_file", side_effect=interrupt_after_partial_copy
        ):
            with self.assertRaises(KeyboardInterrupt):
                agent.publish_agent_index_file(source, logical)

        self.assertEqual(old_before, _filesystem_path(logical).read_bytes())
        self.assertEqual(source_before, _filesystem_path(source).read_bytes())
        self.assertFalse(
            _filesystem_path(destination).exists(),
            "interrupted private generation copy left an unreferenced orphan",
        )
        self.assertEqual([], agent._index_marker_records(logical))

    def test_legacy_maintenance_writer_is_separate_from_public_readers(self) -> None:
        logical = self._logical_path("compatibility-writer")
        self._write_index(logical, "old")
        source = self.root / "compatibility-writer-source.sqlite"
        self._write_index(source, "published")
        physical = agent.publish_agent_index_file(source, logical)

        writer = agent._connect(logical)
        try:
            writer.execute(
                "UPDATE index_meta SET value = ? WHERE key = 'sentinel'",
                (json.dumps("maintained"),),
            )
            writer.commit()
        finally:
            writer.close()

        self.assertEqual(physical, agent.resolve_agent_index_path(logical))
        reader = agent._connect(logical, read_only=True)
        try:
            self.assertEqual(
                json.dumps("maintained"),
                reader.execute(
                    "SELECT value FROM index_meta WHERE key = 'sentinel'"
                ).fetchone()[0],
            )
            with self.assertRaises(sqlite3.OperationalError):
                reader.execute(
                    "UPDATE index_meta SET value = 'forbidden' "
                    "WHERE key = 'sentinel'"
                )
        finally:
            reader.close()

    def test_missing_marker_is_state_not_a_filesystem_sentinel(self) -> None:
        logical = self._logical_path("missing-state")
        self._write_index(logical, "old")
        agent.remove_agent_index(logical)
        collision = agent._index_generation_root(logical) / "missing"
        self._write_index(collision, "resurrected")

        self.assertFalse(agent.agent_index_exists(logical))
        with self.assertRaises(AgentIndexError):
            agent.index_status(logical)

    def test_unpublished_absent_path_cannot_be_resolved_into_an_empty_database(self) -> None:
        logical = self._logical_path("unpublished-absent")
        logical.parent.mkdir(parents=True, exist_ok=True)
        connection: sqlite3.Connection | None = None
        try:
            self.assertFalse(agent.agent_index_exists(logical))
            with self.assertRaises(AgentIndexError):
                connection = sqlite3.connect(agent.resolve_agent_index_path(logical))
        finally:
            if connection is not None:
                connection.close()
            self.assertFalse(_filesystem_path(logical).exists())

        legacy = self._logical_path("legacy-canonical")
        self._write_index(legacy, "legacy")
        self.assertEqual(legacy, agent.resolve_agent_index_path(legacy))
        self.assertEqual("legacy", agent.index_status(legacy)["sentinel"])

    def test_regular_file_generation_root_fails_closed(self) -> None:
        logical = self._logical_path("sidecar-file")
        self._write_index(logical, "stale-canonical")
        sidecar = agent._index_generation_root(logical)
        sidecar.write_text("not a generation directory", encoding="utf-8")

        with self.assertRaises(AgentIndexError):
            agent.resolve_agent_index_path(logical)
        with self.assertRaises(AgentIndexError):
            agent.agent_index_exists(logical)

    def test_malformed_marker_like_entries_fail_closed(self) -> None:
        malformed = (
            "current-not-a-counter-generation",
            "current-00000000000000000001-unknown",
            "current-000000000000000000001-generation",
        )
        for index, name in enumerate(malformed):
            with self.subTest(name=name):
                logical = self._logical_path(f"malformed-marker-{index}")
                self._write_index(logical, "stale-canonical")
                sidecar = agent._index_generation_root(logical)
                _filesystem_path(sidecar).mkdir(parents=True, exist_ok=True)
                _filesystem_path(sidecar / name).touch()

                with self.assertRaisesRegex(AgentIndexError, "malformed marker"):
                    agent.resolve_agent_index_path(logical)
                with self.assertRaisesRegex(AgentIndexError, "malformed marker"):
                    agent.agent_index_exists(logical)

    def test_remove_refuses_to_hide_dangling_or_ambiguous_state(self) -> None:
        dangling = self._logical_path("remove-dangling")
        self._write_index(dangling, "stale-canonical")
        agent._write_index_marker(dangling, 1, "generation")
        with self.assertRaises(AgentIndexError):
            agent.remove_agent_index(dangling)
        self.assertEqual(
            [(1, "generation")],
            [
                (counter, kind)
                for counter, kind, _ in agent._index_marker_records(dangling)
            ],
        )

        ambiguous = self._logical_path("remove-ambiguous")
        self._write_index(ambiguous, "canonical")
        self._write_index(agent._index_generation_path(ambiguous, 1), "generation")
        agent._write_index_marker(ambiguous, 1, "canonical")
        agent._write_index_marker(ambiguous, 1, "generation")
        with self.assertRaises(AgentIndexError):
            agent.remove_agent_index(ambiguous)
        self.assertEqual(2, len(agent._index_marker_records(ambiguous)))

    def test_generation_cleanup_unpublishes_before_deleting_target(self) -> None:
        def seeded_logical(name: str) -> Path:
            logical = self._logical_path(name)
            for counter in range(1, 18):
                self._write_index(
                    agent._index_generation_path(logical, counter),
                    f"generation-{counter}",
                )
                agent._write_index_marker(logical, counter, "generation")
            return logical

        real_unlink = os.unlink
        marker_blocked = seeded_logical("cleanup-marker-blocked")
        marker = agent._index_generation_root(marker_blocked) / (
            "current-00000000000000000001-generation"
        )
        target = agent._index_generation_path(marker_blocked, 1)

        def deny_marker(candidate: object, *args: object, **kwargs: object) -> None:
            if Path(os.fspath(candidate)).name == marker.name:
                raise PermissionError("injected marker unlink denial")
            real_unlink(candidate, *args, **kwargs)

        with patch.object(agent.os, "unlink", side_effect=deny_marker):
            agent._cleanup_index_generations(marker_blocked)
        self.assertTrue(_filesystem_path(marker).is_file())
        self.assertTrue(_filesystem_path(target).is_file())

        target_blocked = seeded_logical("cleanup-target-blocked")
        marker = agent._index_generation_root(target_blocked) / (
            "current-00000000000000000001-generation"
        )
        target = agent._index_generation_path(target_blocked, 1)

        def deny_target(candidate: object, *args: object, **kwargs: object) -> None:
            if Path(os.fspath(candidate)).name == target.name:
                raise PermissionError("injected target unlink denial")
            real_unlink(candidate, *args, **kwargs)

        with patch.object(agent.os, "unlink", side_effect=deny_target):
            agent._cleanup_index_generations(target_blocked)
        self.assertFalse(_filesystem_path(marker).exists())
        self.assertTrue(_filesystem_path(target).is_file())

        agent._cleanup_index_generations(marker_blocked)
        agent._cleanup_index_generations(target_blocked)
        self.assertFalse(
            _filesystem_path(
                agent._index_generation_path(marker_blocked, 1)
            ).exists()
        )
        self.assertFalse(
            _filesystem_path(
                agent._index_generation_path(target_blocked, 1)
            ).exists()
        )

    def test_publish_cleanup_does_not_swallow_keyboard_interrupt(self) -> None:
        logical = self._logical_path("publish-interrupt")
        self._write_index(logical, "old")
        source = self.root / "publish-interrupt-source.sqlite"
        self._write_index(source, "new")

        try:
            with patch.object(
                agent,
                "_cleanup_index_generations",
                side_effect=KeyboardInterrupt,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    agent.publish_agent_index_file(source, logical)
        finally:
            self.assertEqual("new", agent.index_status(logical)["sentinel"])

    def test_remove_cleanup_does_not_swallow_system_exit(self) -> None:
        logical = self._logical_path("remove-interrupt")
        self._write_index(logical, "old")

        try:
            with patch.object(
                agent,
                "_cleanup_index_generations",
                side_effect=SystemExit,
            ):
                with self.assertRaises(SystemExit):
                    agent.remove_agent_index(logical)
        finally:
            self.assertFalse(agent.agent_index_exists(logical))

    def test_post_marker_compatibility_copy_does_not_swallow_interrupt(self) -> None:
        logical = self._logical_path("compatibility-interrupt")
        source = self.root / "compatibility-interrupt-source.sqlite"
        self._write_index(source, "new")
        compatibility = (
            agent._index_generation_root(logical)
            / ".canonical-1.tmp"
        )
        interrupted = False

        def interrupt_after_partial_copy(
            copy_source: object, copy_destination: object
        ) -> None:
            nonlocal interrupted
            interrupted = True
            Path(os.fspath(copy_destination)).write_bytes(b"partial canonical copy")
            raise KeyboardInterrupt

        try:
            with patch.object(
                agent.shutil,
                "copyfile",
                side_effect=interrupt_after_partial_copy,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    agent.publish_agent_index_file(source, logical)
        finally:
            self.assertTrue(interrupted)
            self.assertEqual("new", agent.index_status(logical)["sentinel"])
            self.assertFalse(
                _filesystem_path(compatibility).exists(),
                "interrupted compatibility copy leaked a private temp file",
            )

    def test_missing_newest_generation_fails_closed(self) -> None:
        logical = self._logical_path("missing-generation")
        old_generation = agent._index_generation_path(logical, 1)
        self._write_index(old_generation, "old")
        agent._write_index_marker(logical, 1, "generation")
        agent._write_index_marker(logical, 2, "generation")

        with self.assertRaises(AgentIndexError):
            agent.index_status(logical)

    def test_missing_newest_canonical_target_fails_closed(self) -> None:
        logical = self._logical_path("missing-canonical")
        old_generation = agent._index_generation_path(logical, 1)
        self._write_index(old_generation, "old")
        agent._write_index_marker(logical, 1, "generation")
        agent._write_index_marker(logical, 2, "canonical")

        with self.assertRaises(AgentIndexError):
            agent.index_status(logical)

    def test_corrupt_newest_generation_does_not_fall_back(self) -> None:
        logical = self._logical_path("corrupt-generation")
        old_generation = agent._index_generation_path(logical, 1)
        newest_generation = agent._index_generation_path(logical, 2)
        self._write_index(old_generation, "old")
        _filesystem_path(newest_generation).parent.mkdir(parents=True, exist_ok=True)
        _filesystem_path(newest_generation).write_bytes(b"not a sqlite database")
        agent._write_index_marker(logical, 1, "generation")
        agent._write_index_marker(logical, 2, "generation")

        with self.assertRaises(AgentIndexError):
            agent.index_status(logical)

    def test_duplicate_marker_kinds_at_newest_counter_fail_closed(self) -> None:
        logical = self._logical_path("duplicate-kinds")
        self._write_index(logical, "canonical")
        generation = agent._index_generation_path(logical, 1)
        self._write_index(generation, "generation")
        agent._write_index_marker(logical, 1, "canonical")
        agent._write_index_marker(logical, 1, "generation")

        with self.assertRaises(AgentIndexError):
            agent.index_status(logical)

    def test_resolve_open_race_retries_read_only_without_creating_empty_file(self) -> None:
        logical = self._logical_path("resolve-open-race")
        first_generation = agent._index_generation_path(logical, 1)
        second_generation = agent._index_generation_path(logical, 2)
        self._write_index(first_generation, "old")
        self._write_index(second_generation, "new")
        agent._write_index_marker(logical, 1, "generation")
        real_connect = sqlite3.connect
        raced = False

        def racing_connect(database: object, *args: object, **kwargs: object):
            nonlocal raced
            if not raced:
                raced = True
                _filesystem_path(first_generation).unlink()
                agent._write_index_marker(logical, 2, "generation")
            return real_connect(database, *args, **kwargs)

        with patch.object(agent.sqlite3, "connect", side_effect=racing_connect) as mocked:
            status = agent.index_status(logical)

        self.assertEqual("new", status["sentinel"])
        self.assertFalse(_filesystem_path(first_generation).exists())
        self.assertLessEqual(mocked.call_count, 3)

    def test_open_retries_complete_selection_when_old_target_is_collected(self) -> None:
        logical = self._logical_path("selection-open-race")
        first_generation = agent._index_generation_path(logical, 1)
        second_generation = agent._index_generation_path(logical, 2)
        self._write_index(first_generation, "old")
        self._write_index(second_generation, "new")
        agent._write_index_marker(logical, 1, "generation")
        real_is_file = agent._path_is_file
        raced = False

        def collect_during_selection(candidate: Path) -> bool:
            nonlocal raced
            if candidate == first_generation and not raced:
                raced = True
                _filesystem_path(first_generation).unlink()
                agent._write_index_marker(logical, 2, "generation")
            return real_is_file(candidate)

        with patch.object(
            agent, "_path_is_file", side_effect=collect_during_selection
        ):
            status = agent.index_status(logical)

        self.assertTrue(raced)
        self.assertEqual("new", status["sentinel"])

    @unittest.skipUnless(os.name == "nt", "Windows sharing semantics are required")
    def test_concurrent_publishers_are_serialized_across_processes(self) -> None:
        logical = self._logical_path("concurrent-publishers")
        self._write_index(logical, "old")
        ready = self.root / "publisher-ready"
        ready.mkdir()
        release = self.root / "publisher-release"
        workers = 6
        payloads = {f"writer-{index}".encode("ascii") for index in range(workers)}
        sources: list[Path] = []
        for index, payload in enumerate(sorted(payloads)):
            source = self.root / f"publisher-{index}.sqlite"
            source.write_bytes(payload)
            sources.append(source)
        child_code = (
            "import os, sys, time\n"
            "from pathlib import Path\n"
            "import kgdistiller.agent as agent\n"
            "logical, source, ready, release = map(Path, sys.argv[1:5])\n"
            "original = agent._next_index_generation\n"
            "def gated(path):\n"
            "    counter = original(path)\n"
            "    (ready / str(os.getpid())).touch()\n"
            "    while not release.exists():\n"
            "        time.sleep(0.005)\n"
            "    return counter\n"
            "agent._next_index_generation = gated\n"
            "print(agent.publish_agent_index_file(source, logical), flush=True)\n"
        )
        environment = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
        reader = sqlite3.connect(logical)
        processes: list[subprocess.Popen[str]] = []
        try:
            for source in sources:
                processes.append(
                    subprocess.Popen(
                        [
                            sys.executable,
                            "-c",
                            child_code,
                            str(logical),
                            str(source),
                            str(ready),
                            str(release),
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        env=environment,
                    )
                )
            deadline = time.monotonic() + 1.0
            while len(list(ready.iterdir())) < workers and time.monotonic() < deadline:
                time.sleep(0.01)
            release.touch()
            results = [process.communicate(timeout=15) for process in processes]
        finally:
            release.touch(exist_ok=True)
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
            reader.close()

        failures = [
            f"pid={process.pid} exit={process.returncode} stderr={stderr.strip()}"
            for process, (_, stderr) in zip(processes, results)
            if process.returncode != 0
        ]
        self.assertEqual([], failures)
        records = agent._index_marker_records(logical)
        self.assertEqual(workers, len(records))
        self.assertEqual(workers, len({counter for counter, _, _ in records}))
        published_payloads = {
            _filesystem_path(agent._index_generation_path(logical, counter)).read_bytes()
            for counter, kind, _ in records
            if kind == "generation"
        }
        self.assertEqual(payloads, published_payloads)

    def test_publisher_waits_for_a_slow_cross_process_lock_holder(self) -> None:
        logical = self._logical_path("slow-lock-holder")
        self._write_index(logical, "old")
        source = self.root / "slow-lock-source.sqlite"
        self._write_index(source, "new")
        ready = self.root / "slow-lock-ready"
        child_code = (
            "import sys, time\n"
            "from pathlib import Path\n"
            "import kgdistiller.agent as agent\n"
            "logical, ready = map(Path, sys.argv[1:3])\n"
            "with agent._index_publication_lock(logical):\n"
            "    ready.touch()\n"
            "    time.sleep(12.0)\n"
        )
        environment = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
        holder = subprocess.Popen(
            [sys.executable, "-c", child_code, str(logical), str(ready)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        try:
            deadline = time.monotonic() + 5.0
            while not ready.exists() and time.monotonic() < deadline:
                if holder.poll() is not None:
                    break
                time.sleep(0.01)
            if not ready.exists():
                stdout, stderr = holder.communicate(timeout=1)
                self.fail(
                    "lock holder did not become ready: "
                    f"exit={holder.returncode} stdout={stdout} stderr={stderr}"
                )
            agent.publish_agent_index_file(source, logical)
            stdout, stderr = holder.communicate(timeout=5)
            self.assertEqual(0, holder.returncode, f"stdout={stdout} stderr={stderr}")
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.communicate(timeout=5)

        self.assertEqual("new", agent.index_status(logical)["sentinel"])

    def test_lock_timeout_and_interrupt_release_waiter_handles(self) -> None:
        logical = self._logical_path("lock-release")
        self._write_index(logical, "old")
        source = self.root / "lock-release-source.sqlite"
        self._write_index(source, "new")
        ready = self.root / "lock-release-ready"
        child_code = (
            "import sys, time\n"
            "from pathlib import Path\n"
            "import kgdistiller.agent as agent\n"
            "logical, ready = map(Path, sys.argv[1:3])\n"
            "with agent._index_publication_lock(logical):\n"
            "    ready.touch()\n"
            "    time.sleep(1.0)\n"
        )
        environment = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
        holder = subprocess.Popen(
            [sys.executable, "-c", child_code, str(logical), str(ready)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        try:
            deadline = time.monotonic() + 5.0
            while not ready.exists() and time.monotonic() < deadline:
                if holder.poll() is not None:
                    break
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "lock holder did not become ready")

            with patch.object(
                agent, "_INDEX_PUBLICATION_LOCK_TIMEOUT_SECONDS", 0.1
            ):
                with self.assertRaisesRegex(AgentIndexError, "timed out"):
                    agent.publish_agent_index_file(source, logical)
            with patch.object(agent.time, "sleep", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    agent.publish_agent_index_file(source, logical)

            stdout, stderr = holder.communicate(timeout=3)
            self.assertEqual(0, holder.returncode, f"stdout={stdout} stderr={stderr}")
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.communicate(timeout=5)

        lock_path = agent._index_generation_root(logical) / "publication.lock"
        probe = lock_path.with_name("publication-lock-probe")
        os.replace(_filesystem_path(lock_path), _filesystem_path(probe))
        os.replace(_filesystem_path(probe), _filesystem_path(lock_path))
        agent.publish_agent_index_file(source, logical)
        self.assertEqual("new", agent.index_status(logical)["sentinel"])

    @unittest.skipUnless(os.name == "nt", "Windows extended-length I/O is required")
    def test_generation_sidecar_supports_extended_length_paths(self) -> None:
        logical = (
            self.root
            / ("long-sidecar-" + "x" * 180)
            / ("nested-" + "y" * 70)
            / "knowledge.sqlite"
        )
        self.assertGreater(len(str(agent._index_generation_path(logical, 1))), 260)
        self._write_index(logical, "old")
        source = self.root / "long-sidecar-source.sqlite"
        self._write_index(source, "new")
        reader = sqlite3.connect(str(_filesystem_path(logical)))
        try:
            agent.publish_agent_index_file(source, logical)
        finally:
            reader.close()

        physical = agent.resolve_agent_index_path(logical)
        self.assertEqual("new", agent.index_status(logical)["sentinel"])
        self.assertTrue(_filesystem_path(physical).is_file())
        self.assertFalse(str(physical).startswith("\\\\?\\"))
        self.assertEqual(str(logical), agent.index_status(logical)["path"])

    def test_agent_index_backup_includes_committed_wal_state(self) -> None:
        repository = self.root / "repository"
        logical = repository / "knowledge/build/knowledge.sqlite"
        self._write_index(logical, "current")
        connection = sqlite3.connect(logical)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA wal_autocheckpoint=0")
            connection.execute("CREATE TABLE committed_rows (value TEXT NOT NULL)")
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("INSERT INTO committed_rows VALUES ('committed')")
            connection.commit()

            backup_root = repository / "knowledge/build/backups/snapshot"
            record = _backup_target(
                repository,
                logical,
                backup_root,
                source=agent.resolve_agent_index_path(logical),
                kind="agent-index",
            )
            backup = backup_root / record["path"]
            backup_reader = sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)
            try:
                values = backup_reader.execute(
                    "SELECT value FROM committed_rows ORDER BY value"
                ).fetchall()
            finally:
                backup_reader.close()
        finally:
            connection.close()

        self.assertEqual([("committed",)], values)

    def test_logical_database_interface_boundary_is_documented(self) -> None:
        documentation = " ".join(
            (REPO_ROOT / "docs/deployment.md")
            .read_text(encoding="utf-8")
            .lower()
            .split()
        )
        self.assertRegex(documentation, r"--database.{0,160}logical")
        self.assertIn("sqlite3.connect", documentation)
        self.assertRegex(documentation, r"cli.{0,120}mcp.{0,120}(api|python)")
        self.assertRegex(
            documentation,
            r"physical generation.{0,160}(portable artifact|portable store)",
        )
        self.assertIn("one writer environment", documentation)
        self.assertIn("not claimed to mutually exclude", documentation)


if __name__ == "__main__":
    unittest.main()
