"""``kgd embedding``: show, set and turn off the home's embedding model."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

from kgdistiller import cli
from kgdistiller.adapters.embedding import EmbeddingError
from kgdistiller.index import index
from tests.knowledge_fixture import fake_encoder, make_record_home, use_temporary_home


class Described:
    def __init__(self, model: str) -> None:
        self.model = model

    def report(self) -> dict[str, Any]:
        return {"model": self.model, "pooling": "cls", "weights": "model.safetensors", "precision": "int8"}


def run(*arguments: str) -> tuple[int, Any]:
    """The exit status and the JSON kgd printed: the report on stdout, or the error on stderr."""
    out, err = io.StringIO(), io.StringIO()
    with patch("sys.argv", ["kgd", *arguments]), redirect_stdout(out), redirect_stderr(err):
        status = cli.main()
    return status, json.loads(out.getvalue() or err.getvalue())


class EmbeddingCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        empty = patch("kgdistiller.adapters.embedding.cached_models", list)
        empty.start()
        self.addCleanup(empty.stop)
        self.kb = make_record_home(self)
        self.kb.write_source("notes/a.txt", "Title\nA measure space is a triple.\n")
        self.kb.write_record("measure-space", "label: Measure space\nkind: definition\nsource: notes/a.txt\nlines: 2",
                             "A triple.", ["A measure space is a triple."])

    def config(self) -> dict[str, Any]:
        return json.loads((Path(self.kb.home) / "config.json").read_text(encoding="utf-8"))

    def test_shows_the_model_and_the_stored_vectors(self) -> None:
        status, shown = run("embedding")
        self.assertEqual(0, status)
        self.assertEqual({"embedding": None, "precision": None, "vectors": None, "unembedded": 0,
                          "embedding_changed": False, "cached": []}, {key: shown[key] for key in shown if key != "home"})
        self.kb.embedding = "fake/model"
        self.kb.write_config()
        with fake_encoder():
            index()
        _, shown = run("embedding")
        self.assertEqual(("fake/model", "int8", "fake/model int8", 0, False),
                         (shown["embedding"], shown["precision"], shown["vectors"], shown["unembedded"],
                          shown["embedding_changed"]))

    def test_set_checks_the_model_then_writes_it_and_reembeds_nothing(self) -> None:
        with patch("kgdistiller.adapters.embedding.describe", Described):
            status, shown = run("embedding", "set", " fake/model ")
        self.assertEqual(0, status)
        self.assertEqual("fake/model", self.config()["embedding"])
        self.assertEqual({"model": "fake/model", "pooling": "cls", "weights": "model.safetensors", "precision": "int8"},
                         shown["checked"])
        self.assertEqual((None, True), (shown["vectors"], shown["embedding_changed"]))
        self.assertEqual(sorted(self.config()), ["bases", "embedding"])

    def test_a_refused_model_changes_nothing(self) -> None:
        def refuse(model: str) -> Described:
            raise EmbeddingError(f"{model} is a bert model; kgdistiller embeds with XLM-RoBERTa sentence encoders")

        before = self.config()
        with patch("kgdistiller.adapters.embedding.describe", refuse):
            status, shown = run("embedding", "set", "google-bert/bert-base-uncased")
        self.assertEqual(1, status)
        self.assertIn("is a bert model", shown["message"])
        self.assertEqual(before, self.config())

    def test_off_turns_the_dense_lane_off(self) -> None:
        self.kb.embedding = "fake/model"
        self.kb.write_config()
        status, shown = run("embedding", "off")
        self.assertEqual(0, status)
        self.assertIsNone(self.config()["embedding"])
        self.assertEqual((None, None), (shown["embedding"], shown["precision"]))


if __name__ == "__main__":
    unittest.main()


class CachedModelsTest(unittest.TestCase):
    """The runnable models of the Hugging Face cache, read from its files: XLM-RoBERTa with a tokenizer, pooling and weights."""

    def setUp(self) -> None:
        import tempfile

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cache = Path(directory.name)
        environment = patch.dict("os.environ", {"HF_HUB_CACHE": str(self.cache)})
        environment.start()
        self.addCleanup(environment.stop)
        use_temporary_home(self)

    def model(self, name: str, files: dict[str, str], revisions: int = 1) -> None:
        names = list(files)
        for revision in range(revisions):
            snapshot = self.cache / f"models--{name.replace('/', '--')}" / "snapshots" / f"r{revision}"
            for file in names[revision::revisions]:
                (snapshot / file).parent.mkdir(parents=True, exist_ok=True)
                (snapshot / file).write_text(files[file], encoding="utf-8")

    def test_only_runnable_xlm_roberta_encoders_are_listed(self) -> None:
        from kgdistiller.adapters.embedding import cached_models

        encoder = {"config.json": json.dumps({"model_type": "xlm-roberta"}), "sentencepiece.bpe.model": "",
                   "1_Pooling/config.json": "{}", "model.safetensors": ""}
        self.model("BAAI/bge-m3", encoder, revisions=2)
        self.model("BAAI/bge-reranker", {name: text for name, text in encoder.items() if name != "1_Pooling/config.json"})
        self.model("Qwen/embedding", {**encoder, "config.json": json.dumps({"model_type": "qwen3"})})
        self.model("x/no-weights", {name: text for name, text in encoder.items() if name != "model.safetensors"})
        self.assertEqual(["BAAI/bge-m3"], cached_models())
        status, report = run("embedding")
        self.assertEqual((1, "kgdistiller-error"), (status, report["kind"]), "no home: the command reports it")

