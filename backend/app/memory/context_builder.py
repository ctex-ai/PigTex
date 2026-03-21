"""
Context Builder - Local-first RAG pipeline.
Retrieves relevant knowledge and conversation history from local storage.
"""

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import logging
from sqlalchemy.orm import Session

from ..local_storage import LocalDatabase

try:
    from ..local_storage import get_embedding_service
    EMBEDDINGS_AVAILABLE = True
except ImportError:
    get_embedding_service = None
    EMBEDDINGS_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class ContextChunk:
    """A chunk of context to include in the prompt."""
    source_type: str  # 'knowledge', 'conversation', 'system'
    source_id: str
    content: str
    title: Optional[str] = None
    similarity: float = 1.0
    token_estimate: int = 0


class ContextBuilder:
    """Builds context for RAG-augmented prompts from local storage."""

    # Token budget for context (conservative estimate)
    MAX_CONTEXT_TOKENS = 6000

    def __init__(self, db: Session, user_id: str):
        self.db = db
        self.local = LocalDatabase(user_id)
        self.user_id = self.local.user_id
        self._embedding_service = None

    def _get_embedding_service(self):
        if self._embedding_service is None and EMBEDDINGS_AVAILABLE:
            self._embedding_service = get_embedding_service()
        return self._embedding_service

    def estimate_tokens(self, text: str) -> int:
        """Rough token estimation (~1.3 tokens per word)."""
        return int(len(text.split()) * 1.3)

    def _search_knowledge(
        self,
        query: str,
        top_k: int,
        min_similarity: float
    ) -> List[Tuple[object, float]]:
        """Search knowledge with vector similarity, then FTS fallback."""
        if EMBEDDINGS_AVAILABLE:
            try:
                service = self._get_embedding_service()
                query_embedding = service.embed(query)
                vector_results = self.local.search_knowledge_vector(
                    query_embedding=query_embedding,
                    limit=top_k,
                    min_similarity=min_similarity
                )
                if vector_results:
                    return vector_results
            except Exception as e:
                logger.warning("ContextBuilder vector search fallback to FTS: %s", e)

        fts_items = self.local.search_knowledge_fts(query, limit=top_k)
        return [(item, 0.0) for item in fts_items]

    async def build_context(
        self,
        query: str,
        conversation_id: Optional[str] = None,
        include_knowledge: bool = True,
        include_history: bool = True,
        max_knowledge_items: int = 3,
        max_history_messages: int = 10
    ) -> List[ContextChunk]:
        """Build context for a query using local RAG."""
        chunks: List[ContextChunk] = []
        total_tokens = 0

        # 1. Get relevant knowledge items
        if include_knowledge:
            similar_items = self._search_knowledge(
                query=query,
                top_k=max_knowledge_items,
                min_similarity=0.4
            )

            for item, similarity in similar_items:
                content = f"[{item.content_type.upper()}] {item.title}\n{item.content or ''}"
                token_est = self.estimate_tokens(content)

                if total_tokens + token_est <= self.MAX_CONTEXT_TOKENS:
                    chunks.append(ContextChunk(
                        source_type="knowledge",
                        source_id=item.id,
                        content=content,
                        title=item.title,
                        similarity=similarity,
                        token_estimate=token_est
                    ))
                    total_tokens += token_est

        # 2. Get conversation history
        if include_history and conversation_id:
            messages = self.local.get_recent_messages(
                conversation_id=conversation_id,
                max_tokens=min(2000, self.MAX_CONTEXT_TOKENS - total_tokens)
            )

            if max_history_messages > 0 and len(messages) > max_history_messages:
                messages = messages[-max_history_messages:]

            for msg in messages:
                content = f"{msg.role.upper()}: {msg.content}"
                token_est = msg.token_count or self.estimate_tokens(content)

                chunks.append(ContextChunk(
                    source_type="conversation",
                    source_id=msg.id,
                    content=content,
                    similarity=1.0,
                    token_estimate=token_est
                ))
                total_tokens += token_est

        return chunks

    def format_context_for_prompt(
        self,
        chunks: List[ContextChunk],
        include_citations: bool = True
    ) -> str:
        """Format context chunks into a string for the prompt."""
        if not chunks:
            return ""

        sections = []

        # Group by source type
        knowledge_chunks = [c for c in chunks if c.source_type == "knowledge"]
        conversation_chunks = [c for c in chunks if c.source_type == "conversation"]

        if knowledge_chunks:
            section = "## Relevant Knowledge\n"
            for i, chunk in enumerate(knowledge_chunks, 1):
                if include_citations:
                    section += f"\n### [{i}] {chunk.title or 'Knowledge'}\n"
                section += chunk.content + "\n"
            sections.append(section)

        if conversation_chunks:
            section = "## Conversation History\n"
            for chunk in conversation_chunks:
                section += chunk.content + "\n"
            sections.append(section)

        return "\n---\n".join(sections)

    def get_source_citations(self, chunks: List[ContextChunk]) -> List[Dict]:
        """Get source citations for response attribution."""
        citations = []

        for i, chunk in enumerate(chunks, 1):
            if chunk.source_type == "knowledge":
                citations.append({
                    "index": i,
                    "type": chunk.source_type,
                    "id": chunk.source_id,
                    "title": chunk.title,
                    "similarity": round(chunk.similarity, 3)
                })

        return citations

    async def augment_messages(
        self,
        messages: List[Dict[str, str]],
        conversation_id: Optional[str] = None
    ) -> tuple[List[Dict[str, str]], List[Dict]]:
        """
        Augment messages with retrieved context for RAG.
        Returns (augmented_messages, citations).
        """
        # Get the last user message as query
        user_messages = [m for m in messages if m.get("role") == "user"]
        if not user_messages:
            return messages, []

        query = user_messages[-1].get("content", "")

        # Build context
        chunks = await self.build_context(
            query=query,
            conversation_id=conversation_id,
            include_knowledge=True,
            include_history=False  # History is already in caller messages.
        )

        if not chunks:
            return messages, []

        # Format context
        context_text = self.format_context_for_prompt(chunks)
        citations = self.get_source_citations(chunks)

        # Prepend context to system message or create one
        augmented = list(messages)

        system_prompt = f"""You are a helpful AI assistant. Use the following context to answer the user's question. If the context is relevant, incorporate it into your response. Always be accurate and cite your sources when appropriate.

{context_text}

---
Now respond to the user's message:"""

        if augmented and augmented[0].get("role") == "system":
            augmented[0]["content"] = system_prompt + "\n\n" + augmented[0]["content"]
        else:
            augmented.insert(0, {"role": "system", "content": system_prompt})

        return augmented, citations


def get_context_builder(db: Session, user_id: str) -> ContextBuilder:
    """Factory function to create context builder."""
    return ContextBuilder(db, user_id)
