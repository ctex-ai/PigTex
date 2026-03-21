import asyncio
import unittest

from app.memory.vector_service import VectorService


class _StubEmbeddingService:
    def embed(self, text: str):
        if not text.strip():
            return [0.0, 0.0, 0.0]
        return [0.25, 0.5, 0.75]


class VectorServiceTests(unittest.TestCase):
    def test_generate_embedding_uses_local_embedding_service(self) -> None:
        service = VectorService(db=None, embedding_service=_StubEmbeddingService())

        vector = asyncio.run(service.generate_embedding("hello world"))

        self.assertEqual(vector, [0.25, 0.5, 0.75])


if __name__ == "__main__":
    unittest.main()
