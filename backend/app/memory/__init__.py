# Memory Module - Super Context Memory System
"""
PigTex Memory System (v2 - 2-Stream Architecture)

Components:
- knowledge_service: CRUD for knowledge items (server/local)
- conversation_memory: Conversation history management
- vector_service: Embedding and semantic search
- context_builder: RAG pipeline for context assembly
- prompt_injector: "Bơm Ngầm" - hidden prompt/skill injection
- memory_coordinator: Main orchestrator between Server and Local
- fact_extractor: Auto-extract facts from conversations (legacy)

v2 Architecture:
- memory_gate: GateKeeper - classifies messages into extraction streams
- user_profile_store: Stream 1 - strict identity-only storage
- context_memory_store: Stream 2 - scoped context storage
"""

from .knowledge_service import KnowledgeService, get_knowledge_service
from .conversation_memory import ConversationMemory, get_conversation_memory
from .vector_service import VectorService, get_vector_service
from .context_builder import ContextBuilder, get_context_builder
from .prompt_injector import PromptInjector, get_prompt_injector
from .memory_coordinator import MemoryCoordinator, WorkingMemory, get_memory_coordinator
from .fact_extractor import FactExtractor, get_fact_extractor
from .memory_gate import MemoryGate, MemoryStream
from .user_profile_store import UserProfileStore
from .context_memory_store import ContextMemoryStore

__all__ = [
    # Knowledge
    "KnowledgeService",
    "get_knowledge_service",
    
    # Conversation
    "ConversationMemory", 
    "get_conversation_memory",
    
    # Vector
    "VectorService",
    "get_vector_service",
    
    # Context
    "ContextBuilder",
    "get_context_builder",
    
    # Prompt Injection (Bơm Ngầm)
    "PromptInjector",
    "get_prompt_injector",
    
    # Memory Coordinator
    "MemoryCoordinator",
    "WorkingMemory",
    "get_memory_coordinator",
    
    # Fact Extraction (legacy)
    "FactExtractor",
    "get_fact_extractor",

    # v2: 2-Stream Memory
    "MemoryGate",
    "MemoryStream",
    "UserProfileStore",
    "ContextMemoryStore",
]
