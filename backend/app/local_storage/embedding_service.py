"""
Local Embedding Service - Generates embeddings locally without API calls.
Uses sentence-transformers for fast, offline embedding generation.
"""

import numpy as np
from typing import List, Optional, Union
from functools import lru_cache
import logging
import os
import threading

logger = logging.getLogger(__name__)


class LocalEmbeddingService:
    """
    Local embedding generation using sentence-transformers.
    
    Features:
    - Runs completely offline
    - No API costs
    - Fast inference
    - Supports multiple models
    
    Default model: all-MiniLM-L6-v2 (384 dimensions, ~90MB)
    - Good balance of speed and quality
    - Works well for semantic search
    """
    
    # Model options (from smallest to largest)
    MODELS = {
        "mini": "all-MiniLM-L6-v2",          # 384 dims, 90MB, fastest
        "small": "all-MiniLM-L12-v2",         # 384 dims, 120MB
        "base": "all-mpnet-base-v2",          # 768 dims, 420MB, best quality
        "multi": "paraphrase-multilingual-MiniLM-L12-v2"  # 384 dims, supports 50+ languages
    }
    
    _instance = None
    _model = None
    _instance_lock = threading.Lock()
    _model_load_lock = threading.Lock()
    
    def __new__(cls, model_name: str = "mini"):
        """Singleton pattern - only load model once"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, model_name: str = "mini"):
        if self._initialized:
            return
        
        self.model_name = self.MODELS.get(model_name, model_name)
        self.model = None
        self.dimension = None
        self._initialized = True
        
        # Lazy load - don't load model until first use
        logger.info(f"LocalEmbeddingService initialized (model: {self.model_name})")
    
    def _load_model(self):
        """Load the model on first use"""
        if self.model is not None:
            return

        # Prevent concurrent model materialization when multiple async tasks
        # request embeddings at the same time on first run.
        with self._model_load_lock:
            if self.model is not None:
                return

            try:
                from sentence_transformers import SentenceTransformer

                logger.info(f"Loading embedding model: {self.model_name}...")
                hf_token = (
                    os.getenv("HF_TOKEN")
                    or os.getenv("HUGGINGFACEHUB_API_TOKEN")
                    or os.getenv("HUGGINGFACE_TOKEN")
                )
                kwargs = {}
                if hf_token:
                    # sentence-transformers 2.x uses use_auth_token
                    kwargs["use_auth_token"] = hf_token
                try:
                    self.model = SentenceTransformer(self.model_name, **kwargs)
                except TypeError:
                    # Backward/forward compatibility across sentence-transformers versions
                    self.model = SentenceTransformer(self.model_name)

                # Get embedding dimension
                test_embedding = self.model.encode("test", convert_to_numpy=True, show_progress_bar=False)
                self.dimension = len(test_embedding)

                logger.info(f"Model loaded! Dimension: {self.dimension}")

            except ImportError:
                logger.error("sentence-transformers not installed. Run: pip install sentence-transformers")
                raise
            except Exception as e:
                logger.error(f"Failed to load model: {e}")
                raise
    
    def embed(self, text: str) -> List[float]:
        """
        Generate embedding for a single text.
        
        Args:
            text: Input text to embed
            
        Returns:
            List of floats (embedding vector)
        """
        self._load_model()
        
        if not text or not text.strip():
            return [0.0] * self.dimension
        
        # Truncate long text (model max is usually 512 tokens)
        if len(text) > 8000:
            text = text[:8000]
        
        try:
            embedding = self.model.encode(text, convert_to_numpy=True)
            return embedding.tolist()
        except Exception as e:
            logger.error(f"Embedding error: {e}")
            return [0.0] * self.dimension
    
    def embed_batch(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        """
        Generate embeddings for multiple texts efficiently.
        
        Args:
            texts: List of texts to embed
            batch_size: Batch size for processing
            
        Returns:
            List of embedding vectors
        """
        self._load_model()
        
        if not texts:
            return []
        
        # Clean and truncate texts
        cleaned = []
        for text in texts:
            if not text or not text.strip():
                cleaned.append("")
            elif len(text) > 8000:
                cleaned.append(text[:8000])
            else:
                cleaned.append(text)
        
        try:
            embeddings = self.model.encode(
                cleaned,
                convert_to_numpy=True,
                batch_size=batch_size,
                show_progress_bar=False
            )
            return embeddings.tolist()
        except Exception as e:
            logger.error(f"Batch embedding error: {e}")
            return [[0.0] * self.dimension for _ in texts]
    
    def embed_to_bytes(self, text: str) -> bytes:
        """
        Generate embedding and serialize to bytes for SQLite storage.
        
        Args:
            text: Input text
            
        Returns:
            Bytes representation of embedding
        """
        import struct
        embedding = self.embed(text)
        return struct.pack(f'{len(embedding)}f', *embedding)
    
    def similarity(self, embedding1: List[float], embedding2: List[float]) -> float:
        """
        Calculate cosine similarity between two embeddings.
        
        Args:
            embedding1: First embedding vector
            embedding2: Second embedding vector
            
        Returns:
            Cosine similarity score (0-1)
        """
        if len(embedding1) != len(embedding2):
            return 0.0
        
        a = np.array(embedding1)
        b = np.array(embedding2)
        
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        
        if norm_a == 0 or norm_b == 0:
            return 0.0
        
        return float(np.dot(a, b) / (norm_a * norm_b))
    
    def search_similar(
        self,
        query: str,
        items: List[dict],
        embedding_key: str = "embedding",
        content_key: str = "content",
        top_k: int = 5,
        min_similarity: float = 0.3
    ) -> List[tuple]:
        """
        Search for similar items given a query.
        
        Args:
            query: Search query text
            items: List of dicts with embeddings
            embedding_key: Key for embedding in dict
            content_key: Key for content (used if embedding missing)
            top_k: Number of results to return
            min_similarity: Minimum similarity threshold
            
        Returns:
            List of (item, similarity_score) tuples
        """
        query_embedding = self.embed(query)
        
        results = []
        for item in items:
            item_embedding = item.get(embedding_key)
            
            # Generate embedding if missing
            if not item_embedding and content_key in item:
                item_embedding = self.embed(item[content_key])
            
            if not item_embedding:
                continue
            
            sim = self.similarity(query_embedding, item_embedding)
            if sim >= min_similarity:
                results.append((item, sim))
        
        # Sort by similarity descending
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]
    
    def get_dimension(self) -> int:
        """Get embedding dimension"""
        self._load_model()
        return self.dimension
    
    @property
    def is_loaded(self) -> bool:
        """Check if model is loaded"""
        return self.model is not None


# Singleton instance getter
_embedding_service: Optional[LocalEmbeddingService] = None

def get_embedding_service(model_name: str = "mini") -> LocalEmbeddingService:
    """
    Get or create the embedding service singleton.
    
    Args:
        model_name: Model to use ('mini', 'small', 'base', 'multi')
        
    Returns:
        LocalEmbeddingService instance
    """
    global _embedding_service
    if _embedding_service is None:
        with LocalEmbeddingService._instance_lock:
            if _embedding_service is None:
                _embedding_service = LocalEmbeddingService(model_name)
    return _embedding_service


# Convenience functions
def embed_text(text: str) -> List[float]:
    """Quick helper to embed text"""
    return get_embedding_service().embed(text)

def embed_texts(texts: List[str]) -> List[List[float]]:
    """Quick helper to embed multiple texts"""
    return get_embedding_service().embed_batch(texts)

def text_similarity(text1: str, text2: str) -> float:
    """Quick helper to get similarity between two texts"""
    service = get_embedding_service()
    emb1 = service.embed(text1)
    emb2 = service.embed(text2)
    return service.similarity(emb1, emb2)
