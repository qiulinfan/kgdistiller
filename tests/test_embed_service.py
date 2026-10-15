"""The user's one embedding process, run with fake encoders in a child process; no model runtime is imported."""

from __future__ import annotations

import multiprocessing
import os
import shutil
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from kgdistiller.adapters import embed_service
from kgdistiller.adapters.embed_service import Client, address, serve
from kgdistiller.adapters.embedding import EmbeddingError, RetrievalExtraMissing
from tests.knowledge_fixture import FakeEncoder


def fake_load(model: str) -> FakeEncoder:
    """Record each load beside the socket, then answer with the fake encoder or fail as the model id says."""
    with open(os.environ["KGD_TEST_LOADS"], "a", encoding="utf-8") as loads:
        loads.write(model + "\n")
    if model == "missing/extra":
        raise RetrievalExtraMissing("install kgdistiller[retrieval]")
    if model == "broken/model":
        raise EmbeddingError("cannot load embedding model broken/model: not in the cache")
    return FakeEncoder(model, max_chars=5)


def run_server(directory: str, idle: float, loads: str) -> None:
    os.environ["KGD_TEST_LOADS"] = loads
    serve(Path(directory), idle=idle, load=fake_load)


class ServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="kgd-embed-"))
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.loads = self.directory / "loads"
        self.loads.touch()

    def start(self, idle: float = 30.0) -> multiprocessing.Process:
        process = multiprocessing.get_context("spawn").Process(
            target=run_server, args=(str(self.directory), idle, str(self.loads)), daemon=True
        )
        process.start()
        self.addCleanup(process.join, 5)
        self.addCleanup(process.kill)
        deadline = time.monotonic() + 30
        while not self.answers():
            self.assertLess(time.monotonic(), deadline, "the embedding process did not start")
            time.sleep(0.05)
        return process

    def answers(self) -> bool:
        from multiprocessing.connection import Client as connect

        try:
            connect(address(self.directory), authkey=embed_service.authkey(self.directory)).close()
            return True
        except OSError:
            return False

    def client(self, model: str = "fake/model") -> Client:
        return Client(model, self.directory)

    def test_encodes_for_every_client_with_one_load_per_model(self) -> None:
        self.start()
        first, second = self.client(), self.client()
        self.assertEqual([FakeEncoder.vector("a measure")], first.encode_documents(["a measure"]))
        self.assertEqual(FakeEncoder.vector("measure"), second.encode_query("measure"))
        self.assertEqual([1], second.over_limit(["short", "longer text"]))
        self.assertEqual(["fake/model"], self.loads.read_text(encoding="utf-8").split())
        self.client("other/model").encode_query("x")
        first.encode_query("y")
        self.assertEqual(["fake/model", "other/model", "fake/model"], self.loads.read_text(encoding="utf-8").split())

    def test_concurrent_clients_queue_without_starting_another_process(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        self.start()
        with patch.object(embed_service, "_start") as start, ThreadPoolExecutor(16) as pool:
            vectors = list(pool.map(lambda n: self.client().encode_documents([f"text {n}"] * 50), range(32)))
        start.assert_not_called()
        self.assertEqual([[FakeEncoder.vector(f"text {n}")] * 50 for n in range(32)], vectors)
        self.assertEqual(["fake/model"], self.loads.read_text(encoding="utf-8").split())

    def test_load_failures_reach_the_client_and_the_process_keeps_serving(self) -> None:
        self.start()
        with self.assertRaises(RetrievalExtraMissing):
            self.client("missing/extra").encode_query("x")
        with self.assertRaisesRegex(EmbeddingError, "not in the cache"):
            self.client("broken/model").encode_documents(["x"])
        self.assertEqual(FakeEncoder.vector("x"), self.client().encode_query("x"))

    def test_a_second_process_leaves_the_running_one_alone(self) -> None:
        first = self.start()
        serve(self.directory, idle=30, load=fake_load)  # returns at once: the address answers
        self.assertTrue(first.is_alive())
        self.assertEqual(FakeEncoder.vector("x"), self.client().encode_query("x"))

    def test_exits_after_the_idle_time_and_removes_its_socket(self) -> None:
        process = self.start(idle=0.5)
        self.client().encode_query("x")
        process.join(15)
        self.assertEqual(0, process.exitcode)
        if os.name != "nt":
            self.assertFalse(Path(address(self.directory)).exists())

    @unittest.skipIf(os.name == "nt", "a stale socket file exists only on POSIX")
    def test_a_stale_socket_is_replaced(self) -> None:
        stale = socket.socket(socket.AF_UNIX)
        stale.bind(address(self.directory))
        stale.close()  # the file stays, with nothing listening
        self.start()
        self.assertEqual(FakeEncoder.vector("x"), self.client().encode_query("x"))

    def test_a_client_starts_the_process_when_none_answers(self) -> None:
        started: list[Path] = []

        def start(directory: Path) -> None:
            started.append(directory)
            process = multiprocessing.get_context("spawn").Process(
                target=run_server, args=(str(directory), 30.0, str(self.loads)), daemon=True
            )
            process.start()
            self.addCleanup(process.join, 5)
            self.addCleanup(process.kill)

        with patch.object(embed_service, "_start", start):
            self.assertEqual(FakeEncoder.vector("x"), self.client().encode_query("x"))
            self.assertEqual(FakeEncoder.vector("y"), self.client().encode_query("y"))
        self.assertEqual([self.directory], started)

    def test_the_key_is_private_and_stable(self) -> None:
        key = embed_service.authkey(self.directory)
        self.assertEqual(32, len(key))
        self.assertEqual(key, embed_service.authkey(self.directory))
        if os.name != "nt":
            self.assertEqual(0o600, (self.directory / embed_service.KEY_FILENAME).stat().st_mode & 0o777)

    def test_the_runtime_directory_is_private(self) -> None:
        directory = embed_service.runtime_directory()
        if os.name != "nt":
            self.assertEqual(0o700, directory.stat().st_mode & 0o777)
            self.assertEqual(os.getuid(), directory.stat().st_uid)


if __name__ == "__main__":
    unittest.main()
