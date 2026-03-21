"""
Vector Service - Embedding generation and semantic search.
Uses the local embedding service so semantic search stays offline and does not
depend on any provider API keys.
"""

import json
import numpy as np
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import text

from ..local_storage import get_embedding_service
from ..models import KnowledgeItem, Message


class VectorService:
    """Service for vector embeddings and semantic search"""
    
    EMBEDDING_DIM = 384  # Matches the default local embedding service
    
    def __init__(self, db: Session, embedding_service=None):
        self.db = db
        self.embedding_service = embedding_service
    
    async def generate_embedding(self, text: str) -> List[float]:
        """Generate embeddings via the local offline embedding service."""
        if not text or not text.strip():
            return [0.0] * self.EMBEDDING_DIM

        service = self.embedding_service or get_embedding_service()
        embedding = service.embed(text)
        return list(embedding or [0.0] * self.EMBEDDING_DIM)
    
    def cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors"""
        a = np.array(vec1)
        b = np.array(vec2)
        
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        
        if norm_a == 0 or norm_b == 0:
            return 0.0
        
        return float(np.dot(a, b) / (norm_a * norm_b))
    
    async def embed_knowledge_item(self, item: KnowledgeItem) -> bool:
        """Generate and store embedding for a knowledge item"""
        # Combine title and content for embedding
        text_to_embed = f"{item.title}\n\n{item.content or ''}"
        
        embedding = await self.generate_embedding(text_to_embed)
        
        # Store as JSON (for portability; can optimize with pgvector later)
        item.embedding_json = json.dumps(embedding)
        self.db.commit()
        
        return True
    
    async def embed_message(self, message: Message) -> bool:
        """Generate and store embedding for a message"""
        embedding = await self.generate_embedding(message.content)
        message.embedding_json = json.dumps(embedding)
        self.db.commit()
        return True
    
    async def search_similar_items(
        self,
        query: str,
        user_id: str,
        top_k: int = 5,
        min_similarity: float = 0.5
    ) -> List[Tuple[KnowledgeItem, float]]:
        """Search for similar knowledge items using semantic search"""
        
        # Generate query embedding
        query_embedding = await self.generate_embedding(query)
        
        # Get all items with embeddings for this user
        items = self.db.query(KnowledgeItem).filter(
            KnowledgeItem.user_id == user_id,
            KnowledgeItem.embedding_json.isnot(None)
        ).all()
        
        # Calculate similarities
        results = []
        for item in items:
            try:
                item_embedding = json.loads(item.embedding_json)
                similarity = self.cosine_similarity(query_embedding, item_embedding)
                
                if similarity >= min_similarity:
                    results.append((item, similarity))
            except (json.JSONDecodeError, TypeError):
                continue
        
        # Sort by similarity (descending) and return top_k
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]
    
    async def search_similar_messages(
        self,
        query: str,
        user_id: str,
        top_k: int = 10,
        min_similarity: float = 0.5
    ) -> List[Tuple[Message, float]]:
        """Search for similar messages in conversation history"""
        from ..models import Conversation
        
        query_embedding = await self.generate_embedding(query)
        
        # Get messages from user's conversations that have embeddings
        messages = self.db.query(Message).join(Conversation).filter(
            Conversation.user_id == user_id,
            Message.embedding_json.isnot(None)
        ).all()
        
        results = []
        for msg in messages:
            try:
                msg_embedding = json.loads(msg.embedding_json)
                similarity = self.cosine_similarity(query_embedding, msg_embedding)
                
                if similarity >= min_similarity:
                    results.append((msg, similarity))
            except (json.JSONDecodeError, TypeError):
                continue
        
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]
    
    async def reindex_all_items(self, user_id: str) -> int:
        """Reindex all knowledge items for a user"""
        items = self.db.query(KnowledgeItem).filter(
            KnowledgeItem.user_id == user_id
        ).all()
        
        count = 0
        for item in items:
            if await self.embed_knowledge_item(item):
                count += 1
        
        return count


def get_vector_service(db: Session) -> VectorService:
    """Factory function to create vector service"""
    return VectorService(db)
