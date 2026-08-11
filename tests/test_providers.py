from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kgdistiller.providers import (  # noqa: E402
    DeterministicFixtureEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
)


def provider_config(*, adapter: str) -> dict[str, Any]:
    return {
        "adapter": adapter,
        "model": "fixture-query-v1",
        "dimensions": 3,
        "base_url": "https://provider.invalid/v1",
        "credential_env": "KGDISTILLER_TEST_KEY",
    }


class ProviderQueryEmbeddingTest(unittest.TestCase):
    def test_deterministic_query_batch_does_not_route_through_document_methods(
        self,
    ) -> None:
        class QueryOnlyFixture(DeterministicFixtureEmbeddingProvider):
            def embed(self, texts: list[str]) -> list[list[float]]:
                raise AssertionError("generic embedding route was used")

            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                raise AssertionError("document embedding route was used")

        provider = QueryOnlyFixture(
            "fixture",
            provider_config(adapter="deterministic-fixture"),
        )

        vectors = provider.embed_queries(["first query", "second query"])

        self.assertEqual(2, len(vectors))
        self.assertEqual([3, 3], [len(vector) for vector in vectors])

    def test_openai_query_batch_does_not_route_through_document_methods(self) -> None:
        class QueryOnlyOpenAI(OpenAICompatibleEmbeddingProvider):
            def __init__(self) -> None:
                super().__init__(
                    "fixture",
                    provider_config(adapter="openai-compatible"),
                    "fixture-secret",
                )
                self.requests: list[list[str]] = []

            def embed(self, texts: list[str]) -> list[list[float]]:
                raise AssertionError("generic embedding route was used")

            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                raise AssertionError("document embedding route was used")

            def _request(self, texts: list[str]) -> Any:
                self.requests.append(list(texts))
                return {
                    "data": [
                        {"index": index, "embedding": [1.0, 2.0, 3.0]}
                        for index, _ in enumerate(texts)
                    ]
                }

        provider = QueryOnlyOpenAI()

        vectors = provider.embed_queries(["first query", "second query"])

        self.assertEqual([["first query", "second query"]], provider.requests)
        self.assertEqual([[1.0, 2.0, 3.0], [1.0, 2.0, 3.0]], vectors)

    def test_single_query_uses_the_query_batch_entrypoint(self) -> None:
        class TrackingFixture(DeterministicFixtureEmbeddingProvider):
            def __init__(self) -> None:
                super().__init__(
                    "fixture",
                    provider_config(adapter="deterministic-fixture"),
                )
                self.query_batches: list[list[str]] = []

            def embed_queries(self, texts: list[str]) -> list[list[float]]:
                self.query_batches.append(list(texts))
                return self._embed_texts(texts)

        provider = TrackingFixture()

        vector = provider.embed_query("one query")

        self.assertEqual([["one query"]], provider.query_batches)
        self.assertEqual(3, len(vector))


if __name__ == "__main__":
    unittest.main()
