"""
Memory API Routes - Endpoints for workspaces, knowledge items, and conversations.
Now uses LOCAL SQLite storage for privacy-first design (like Cursor/Antigravity).
"""

import logging
import sqlite3
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..memory.knowledge_service import get_knowledge_service
from .auth_utils import get_current_user

router = APIRouter(prefix="/memory", tags=["Memory"])
logger = logging.getLogger(__name__)


def _raise_conversation_endpoint_gone(replacement: str) -> None:
    raise HTTPException(
        status_code=410,
        detail={
            "code": "endpoint_removed",
            "message": "Conversation endpoints moved to /api/v1/conversations.",
            "replacement": replacement,
        },
    )


# =============================================================================
# Schemas
# =============================================================================

class WorkspaceCreate(BaseModel):
    name: str
    icon: str = "📁"
    color: str = "#6366f1"
    parent_id: Optional[str] = None


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None


class WorkspaceResponse(BaseModel):
    id: str
    name: str
    icon: str
    color: str
    parent_id: Optional[str]
    item_count: int = 0

    class Config:
        from_attributes = True


class KnowledgeItemCreate(BaseModel):
    workspace_id: str
    title: str
    content: str = ""
    content_type: str = "note"


class KnowledgeItemUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    is_favorite: Optional[bool] = None
    is_pinned: Optional[bool] = None


class KnowledgeItemResponse(BaseModel):
    id: str
    workspace_id: Optional[str]
    title: str
    content: Optional[str]
    content_type: str
    is_favorite: bool
    is_pinned: bool

    class Config:
        from_attributes = True


class ConversationCreate(BaseModel):
    title: Optional[str] = None
    workspace_id: Optional[str] = None


class ConversationResponse(BaseModel):
    id: str
    title: Optional[str]
    summary: Optional[str]
    total_messages: int
    workspace_id: Optional[str]

    class Config:
        from_attributes = True


class MessageCreate(BaseModel):
    role: str
    content: str
    model: Optional[str] = None


class MessageUpdate(BaseModel):
    content: str
    model: Optional[str] = None


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    model: Optional[str]
    token_count: int

    class Config:
        from_attributes = True


# =============================================================================
# Workspace Endpoints (LOCAL STORAGE)
# =============================================================================

@router.post("/workspaces", response_model=WorkspaceResponse)
async def create_workspace(
    data: WorkspaceCreate,
    current_user: User = Depends(get_current_user)
):
    """Create a new workspace (stored locally)"""
    service = get_knowledge_service(current_user.id)
    workspace = service.create_workspace(
        name=data.name,
        icon=data.icon,
        color=data.color,
        parent_id=data.parent_id
    )
    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        icon=workspace.icon,
        color=workspace.color,
        parent_id=workspace.parent_id,
        item_count=workspace.item_count
    )


@router.get("/workspaces", response_model=List[WorkspaceResponse])
async def list_workspaces(
    parent_id: Optional[str] = None,
    current_user: User = Depends(get_current_user)
):
    """List all workspaces (from local storage)"""
    service = get_knowledge_service(current_user.id)
    workspaces = service.get_workspaces(parent_id)
    return [
        WorkspaceResponse(
            id=w.id,
            name=w.name,
            icon=w.icon,
            color=w.color,
            parent_id=w.parent_id,
            item_count=w.item_count
        )
        for w in workspaces
    ]


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get a single workspace (from local storage)"""
    service = get_knowledge_service(current_user.id)
    workspace = service.get_workspace(workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        icon=workspace.icon,
        color=workspace.color,
        parent_id=workspace.parent_id,
        item_count=workspace.item_count
    )


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: str,
    data: WorkspaceUpdate,
    current_user: User = Depends(get_current_user)
):
    """Update a workspace (local storage)"""
    service = get_knowledge_service(current_user.id)
    workspace = service.update_workspace(
        workspace_id,
        name=data.name,
        icon=data.icon,
        color=data.color
    )
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        icon=workspace.icon,
        color=workspace.color,
        parent_id=workspace.parent_id,
        item_count=workspace.item_count
    )


@router.delete("/workspaces/{workspace_id}")
async def delete_workspace(
    workspace_id: str,
    current_user: User = Depends(get_current_user)
):
    """Delete a workspace (local storage)"""
    try:
        service = get_knowledge_service(current_user.id)
        if not service.delete_workspace(workspace_id):
            raise HTTPException(status_code=404, detail="Workspace not found")
        return {"ok": True}
    except sqlite3.OperationalError as exc:
        if "database or disk is full" in str(exc).lower():
            raise HTTPException(
                status_code=507,
                detail="Local storage is full. Free disk space on system drive and retry."
            ) from exc
        raise


# =============================================================================
# Knowledge Item Endpoints (LOCAL STORAGE)
# =============================================================================

@router.post("/knowledge", response_model=KnowledgeItemResponse)
async def create_knowledge_item(
    data: KnowledgeItemCreate,
    current_user: User = Depends(get_current_user)
):
    """Create a new knowledge item (stored locally)"""
    service = get_knowledge_service(current_user.id)
    item = service.create_knowledge_item(
        workspace_id=data.workspace_id,
        title=data.title,
        content=data.content,
        content_type=data.content_type
    )
    return KnowledgeItemResponse(
        id=item.id,
        workspace_id=item.workspace_id,
        title=item.title,
        content=item.content,
        content_type=item.content_type,
        is_favorite=item.is_favorite,
        is_pinned=item.is_pinned
    )


@router.get("/knowledge", response_model=List[KnowledgeItemResponse])
async def list_knowledge_items(
    workspace_id: Optional[str] = None,
    content_type: Optional[str] = None,
    favorites_only: bool = False,
    current_user: User = Depends(get_current_user)
):
    """List knowledge items with filters (from local storage)"""
    service = get_knowledge_service(current_user.id)
    items = service.get_knowledge_items(
        workspace_id=workspace_id,
        content_type=content_type,
        favorites_only=favorites_only
    )
    return [
        KnowledgeItemResponse(
            id=i.id,
            workspace_id=i.workspace_id,
            title=i.title,
            content=i.content,
            content_type=i.content_type,
            is_favorite=i.is_favorite,
            is_pinned=i.is_pinned
        )
        for i in items
    ]


@router.get("/knowledge/recent", response_model=List[KnowledgeItemResponse])
async def get_recent_items(
    limit: int = 10,
    current_user: User = Depends(get_current_user)
):
    """Get recently updated knowledge items (from local storage)"""
    service = get_knowledge_service(current_user.id)
    items = service.get_recent_items(limit)
    return [
        KnowledgeItemResponse(
            id=i.id,
            workspace_id=i.workspace_id,
            title=i.title,
            content=i.content,
            content_type=i.content_type,
            is_favorite=i.is_favorite,
            is_pinned=i.is_pinned
        )
        for i in items
    ]


@router.get("/knowledge/search", response_model=List[KnowledgeItemResponse])
async def search_knowledge(
    q: str,
    limit: int = 20,
    current_user: User = Depends(get_current_user)
):
    """Search knowledge items by text (full-text search in local storage)"""
    service = get_knowledge_service(current_user.id)
    items = service.search_knowledge(q, limit)
    return [
        KnowledgeItemResponse(
            id=i.id,
            workspace_id=i.workspace_id,
            title=i.title,
            content=i.content,
            content_type=i.content_type,
            is_favorite=i.is_favorite,
            is_pinned=i.is_pinned
        )
        for i in items
    ]


@router.get("/knowledge/{item_id}", response_model=KnowledgeItemResponse)
async def get_knowledge_item(
    item_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get a single knowledge item (from local storage)"""
    service = get_knowledge_service(current_user.id)
    item = service.get_knowledge_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return KnowledgeItemResponse(
        id=item.id,
        workspace_id=item.workspace_id,
        title=item.title,
        content=item.content,
        content_type=item.content_type,
        is_favorite=item.is_favorite,
        is_pinned=item.is_pinned
    )


@router.patch("/knowledge/{item_id}", response_model=KnowledgeItemResponse)
async def update_knowledge_item(
    item_id: str,
    data: KnowledgeItemUpdate,
    current_user: User = Depends(get_current_user)
):
    """Update a knowledge item (local storage)"""
    service = get_knowledge_service(current_user.id)
    item = service.update_knowledge_item(
        item_id,
        title=data.title,
        content=data.content,
        is_favorite=data.is_favorite,
        is_pinned=data.is_pinned
    )
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return KnowledgeItemResponse(
        id=item.id,
        workspace_id=item.workspace_id,
        title=item.title,
        content=item.content,
        content_type=item.content_type,
        is_favorite=item.is_favorite,
        is_pinned=item.is_pinned
    )


@router.delete("/knowledge/{item_id}")
async def delete_knowledge_item(
    item_id: str,
    current_user: User = Depends(get_current_user)
):
    """Delete a knowledge item (local storage)"""
    service = get_knowledge_service(current_user.id)
    if not service.delete_knowledge_item(item_id):
        raise HTTPException(status_code=404, detail="Item not found")
    return {"ok": True}


# =============================================================================
# Conversation Endpoints (Using Local Storage)
# =============================================================================

@router.post("/conversations", response_model=ConversationResponse)
async def create_conversation(
    data: ConversationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Deprecated: use /api/v1/conversations instead."""
    _raise_conversation_endpoint_gone("/api/v1/conversations")


@router.get("/conversations", response_model=List[ConversationResponse])
async def list_conversations(
    workspace_id: Optional[str] = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user)
):
    """Deprecated: use /api/v1/conversations instead."""
    _raise_conversation_endpoint_gone("/api/v1/conversations")


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user)
):
    """Deprecated: use /api/v1/conversations/{conversation_id} instead."""
    _raise_conversation_endpoint_gone(f"/api/v1/conversations/{conversation_id}")


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user)
):
    """Deprecated: use /api/v1/conversations/{conversation_id} instead."""
    _raise_conversation_endpoint_gone(f"/api/v1/conversations/{conversation_id}")



# =============================================================================
# Message Endpoints (LOCAL STORAGE)
# =============================================================================

@router.post("/conversations/{conversation_id}/messages", response_model=MessageResponse)
async def add_message(
    conversation_id: str,
    data: MessageCreate,
    current_user: User = Depends(get_current_user)
):
    """Deprecated: use /api/v1/conversations/{conversation_id}/messages instead."""
    _raise_conversation_endpoint_gone(f"/api/v1/conversations/{conversation_id}/messages")


@router.get("/conversations/{conversation_id}/messages", response_model=List[MessageResponse])
async def get_messages(
    conversation_id: str,
    limit: Optional[int] = None,
    current_user: User = Depends(get_current_user)
):
    """Deprecated: use /api/v1/conversations/{conversation_id}/messages instead."""
    _raise_conversation_endpoint_gone(f"/api/v1/conversations/{conversation_id}/messages")


@router.patch("/conversations/{conversation_id}/messages/{message_id}", response_model=MessageResponse)
async def update_message(
    conversation_id: str,
    message_id: str,
    data: MessageUpdate,
    current_user: User = Depends(get_current_user)
):
    """Deprecated: use /api/v1/conversations/{conversation_id}/messages/{message_id} instead."""
    _raise_conversation_endpoint_gone(f"/api/v1/conversations/{conversation_id}/messages/{message_id}")


# =============================================================================
# Facts Endpoints (Virtual Knowledge)
# =============================================================================

class FactResponse(BaseModel):
    id: str
    content: str
    subject: str
    predicate: str
    object: str
    category: str
    confidence: float
    source: str
    source_conversation_id: Optional[str]
    workspace_id: Optional[str]
    scope: str
    access_count: int
    created_at: Optional[str]
    updated_at: Optional[str]
    confirmed_at: Optional[str]
    type: Optional[str] = None
    key: Optional[str] = None
    value: Optional[str] = None
    status: Optional[str] = None
    expires_at: Optional[str] = None

    class Config:
        from_attributes = True


def _map_assertion_scope_to_legacy(scope: str) -> str:
    return "workspace" if (scope or "").strip().lower() == "workspace" else "system"


def _assertion_source(category: str) -> str:
    return "user_input" if (category or "").strip().lower() == "explicit_memory" else "pattern_extraction"

@router.get("/facts", response_model=List[FactResponse])
async def get_facts(
    workspace_id: Optional[str] = None,
    current_user: User = Depends(get_current_user)
):
    """Get automatically extracted facts (Invisible Memory)"""
    from ..local_storage import LocalDatabase
    local_db = LocalDatabase(current_user.id)

    if workspace_id:
        assertions = local_db.get_memory_assertions(
            type=None,
            scope="workspace",
            workspace_id=workspace_id,
            include_expired=False,
            status="active",
            limit=300,
        )
    else:
        assertions = local_db.get_memory_assertions(
            type=None,
            scope=None,
            workspace_id=...,
            include_expired=False,
            status="active",
            limit=600,
        )

    result: List[FactResponse] = []
    for assertion in assertions:
        if (assertion.type or "").strip().lower() not in {"identity", "fact", "temporary"}:
            continue
        if (assertion.category or "").strip().lower() == "explicit_memory":
            continue
        predicate = (assertion.key or "").strip()
        obj = (assertion.value or "").strip()
        result.append(FactResponse(
            id=assertion.id,
            content=f"User {predicate.replace('_', ' ')} {obj}".strip(),
            subject="User",
            predicate=predicate,
            object=obj,
            category=assertion.category or "general",
            confidence=float(assertion.confidence or 0.0),
            source=_assertion_source(assertion.category),
            source_conversation_id=assertion.conversation_id,
            workspace_id=assertion.workspace_id,
            scope=_map_assertion_scope_to_legacy(assertion.scope),
            access_count=int(assertion.access_count or 0),
            created_at=assertion.created_at.isoformat() if assertion.created_at else None,
            updated_at=assertion.updated_at.isoformat() if assertion.updated_at else None,
            confirmed_at=assertion.confirmed_at.isoformat() if assertion.confirmed_at else None,
            type=assertion.type,
            key=assertion.key,
            value=assertion.value,
            status=assertion.status,
            expires_at=assertion.expires_at.isoformat() if assertion.expires_at else None,
        ))
    if result:
        return result

    # Backward-compatible fallback for users who only have legacy facts.
    filter_ws = workspace_id if workspace_id else ...
    legacy_facts = local_db.get_facts(workspace_id=filter_ws, limit=200)
    for fact in legacy_facts:
        result.append(FactResponse(
            id=fact.id,
            content=fact.to_sentence(),
            subject=fact.subject,
            predicate=fact.predicate,
            object=fact.object,
            category=fact.category,
            confidence=float(fact.confidence or 0.0),
            source=fact.source_type,
            source_conversation_id=fact.source_id,
            workspace_id=fact.workspace_id,
            scope=fact.scope,
            access_count=int(fact.access_count or 0),
            created_at=fact.created_at.isoformat() if fact.created_at else None,
            updated_at=fact.updated_at.isoformat() if fact.updated_at else None,
            confirmed_at=fact.confirmed_at.isoformat() if fact.confirmed_at else None,
            type=None,
            key=fact.predicate,
            value=fact.object,
            status=None,
            expires_at=None,
        ))
    return result

@router.delete("/facts/{fact_id}")
async def delete_fact(
    fact_id: str,
    current_user: User = Depends(get_current_user)
):
    """Delete a specific extracted fact"""
    from ..local_storage import LocalDatabase
    local_db = LocalDatabase(current_user.id)

    if not local_db.delete_memory_assertion(fact_id):
        local_db.delete_fact(fact_id)
    return {"ok": True}

# =============================================================================
# Preferences Endpoints (Virtual Knowledge)
# =============================================================================

class PreferenceResponse(BaseModel):
    id: str
    category: str
    key: str
    value: str
    confidence: float
    source_conversation_id: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]

    class Config:
        from_attributes = True

@router.get("/preferences", response_model=List[PreferenceResponse])
async def get_preferences(
    category: Optional[str] = None,
    current_user: User = Depends(get_current_user)
):
    """Get automatically extracted user preferences"""
    from ..local_storage import LocalDatabase
    local_db = LocalDatabase(current_user.id)

    assertions = local_db.get_memory_assertions(
        type="preference",
        scope=None,
        workspace_id=...,
        include_expired=False,
        status="active",
        limit=400,
    )
    if category:
        normalized_category = category.strip().lower()
        assertions = [
            item for item in assertions
            if (item.category or "").strip().lower() == normalized_category
        ]

    if assertions:
        return [
            PreferenceResponse(
                id=p.id,
                category=p.category or "preference",
                key=p.key,
                value=p.value,
                confidence=float(p.confidence or 0.0),
                source_conversation_id=p.conversation_id,
                created_at=p.created_at.isoformat() if p.created_at else None,
                updated_at=p.updated_at.isoformat() if p.updated_at else None,
            ) for p in assertions
            if (p.category or "").strip().lower() != "explicit_memory"
        ]

    # Backward-compatible fallback for users who only have legacy preferences.
    legacy_prefs = local_db.get_preferences(category=category)
    return [
        PreferenceResponse(
            id=p.id,
            category=p.category,
            key=p.key,
            value=p.value,
            confidence=float(p.confidence or 0.0),
            source_conversation_id=p.source_conversation_id,
            created_at=p.created_at.isoformat() if p.created_at else None,
            updated_at=p.updated_at.isoformat() if p.updated_at else None,
        ) for p in legacy_prefs
    ]

@router.delete("/preferences/{pref_id}")
async def delete_preference(
    pref_id: str,
    current_user: User = Depends(get_current_user)
):
    """Delete a specific user preference"""
    from ..local_storage import LocalDatabase
    local_db = LocalDatabase(current_user.id)

    if not local_db.delete_memory_assertion(pref_id):
        local_db.delete_preference(pref_id)
    return {"ok": True}

# =============================================================================
# Semantic Search Endpoints (Phase 2)
# =============================================================================

class SemanticSearchResult(BaseModel):
    id: str
    title: str
    content: Optional[str]
    content_type: str
    similarity: float

    class Config:
        from_attributes = True


@router.get("/search/semantic", response_model=List[SemanticSearchResult])
async def semantic_search(
    q: str,
    workspace_id: Optional[str] = None,
    limit: int = 5,
    min_similarity: float = 0.4,
    current_user: User = Depends(get_current_user)
):
    """Semantic search using local embeddings (falls back to FTS)."""
    from ..local_storage import LocalDatabase, get_embedding_service

    local_db = LocalDatabase(current_user.id)

    # Vector search first (if embeddings runtime is available)
    try:
        embedding_service = get_embedding_service()
        query_embedding = embedding_service.embed(q)
        results = local_db.search_knowledge_vector(
            query_embedding=query_embedding,
            limit=limit,
            min_similarity=min_similarity,
            workspace_id=workspace_id
        )

        if results:
            return [
                SemanticSearchResult(
                    id=item.id,
                    title=item.title,
                    content=item.content[:200] + "..." if item.content and len(item.content) > 200 else item.content,
                    content_type=item.content_type,
                    similarity=round(similarity, 3)
                )
                for item, similarity in results
            ]
    except Exception as e:
        # Non-fatal; fallback to full-text search
        logger.warning("Semantic search fallback to FTS due to embedding failure: %s", e)

    # Fallback: full-text search
    items = local_db.search_knowledge_fts(q, limit=limit, workspace_id=workspace_id)
    return [
        SemanticSearchResult(
            id=item.id,
            title=item.title,
            content=item.content[:200] + "..." if item.content and len(item.content) > 200 else item.content,
            content_type=item.content_type,
            similarity=0.0
        )
        for item in items
    ]


@router.post("/knowledge/{item_id}/embed")
async def embed_knowledge_item(
    item_id: str,
    current_user: User = Depends(get_current_user)
):
    """Generate embedding for a single local knowledge item."""
    from datetime import datetime
    from ..local_storage import get_embedding_service

    service = get_knowledge_service(current_user.id)
    item = service.get_knowledge_item(item_id)
    
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    try:
        embedding_service = get_embedding_service()
        text = f"{item.title}\n\n{item.content or ''}"
        item.embedding = embedding_service.embed_to_bytes(text)
        item.updated_at = datetime.now()
        service.local.save_knowledge_item(item)
        return {"ok": True, "item_id": item_id}
    except Exception as e:
        logger.warning("Knowledge embedding unavailable for item_id=%s: %s", item_id, e)
        raise HTTPException(status_code=503, detail="Embedding service unavailable")


@router.post("/reindex")
async def reindex_all(
    current_user: User = Depends(get_current_user)
):
    """Reindex all local knowledge items (generate embeddings)."""
    from datetime import datetime
    from ..local_storage import get_embedding_service

    service = get_knowledge_service(current_user.id)

    try:
        embedding_service = get_embedding_service()
        items = service.get_knowledge_items(limit=100000)
        count = 0

        for item in items:
            text = f"{item.title}\n\n{item.content or ''}"
            item.embedding = embedding_service.embed_to_bytes(text)
            item.updated_at = datetime.now()
            service.local.save_knowledge_item(item)
            count += 1

        return {"ok": True, "items_indexed": count}
    except Exception as e:
        logger.warning("Bulk reindex embedding unavailable user_id=%s: %s", current_user.id, e)
        raise HTTPException(status_code=503, detail="Embedding service unavailable")


# =============================================================================
# Backup & Export Endpoints
# =============================================================================

class ExportResponse(BaseModel):
    ok: bool
    filepath: str
    stats: dict


class ImportRequest(BaseModel):
    filepath: str
    merge: bool = False


class ImportResponse(BaseModel):
    ok: bool
    stats: dict


class StorageStatsResponse(BaseModel):
    db_size_bytes: int
    db_size_human: str
    brain_size_bytes: int
    brain_size_human: str
    total_size_human: str
    workspace_count: int
    knowledge_item_count: int
    conversation_count: int
    archived_conversation_count: int
    message_count: int
    fact_count: int
    preference_count: int
    memory_assertion_count: Optional[int] = None
    memory_evidence_count: Optional[int] = None
    memory_pending_change_count: Optional[int] = None


class CleanupRequest(BaseModel):
    days_threshold: int = 90
    keep_favorites: bool = True
    keep_important: bool = True


class CleanupResponse(BaseModel):
    ok: bool
    stats: dict


@router.post("/export", response_model=ExportResponse)
async def export_data(
    current_user: User = Depends(get_current_user)
):
    """Export all user data to a JSON backup file"""
    from ..local_storage import LocalDatabase
    
    local_db = LocalDatabase(current_user.id)
    filepath = local_db.export_to_file()
    data = local_db.export_all_data()
    
    stats = {
        "workspaces": len(data["workspaces"]),
        "knowledge_items": len(data["knowledge_items"]),
        "conversations": len(data["conversations"]),
        "messages": len(data["messages"]),
        "facts": len(data["facts"]),
        "preferences": len(data["preferences"]),
        "memory_assertions": len(data.get("memory_assertions", [])),
        "memory_evidence": len(data.get("memory_evidence", [])),
        "memory_pending_changes": len(data.get("memory_pending_changes", [])),
    }
    
    return ExportResponse(
        ok=True,
        filepath=str(filepath),
        stats=stats
    )


@router.post("/import", response_model=ImportResponse)
async def import_data(
    data: ImportRequest,
    current_user: User = Depends(get_current_user)
):
    """Import data from a JSON backup file"""
    from ..local_storage import LocalDatabase
    from pathlib import Path
    
    filepath = Path(data.filepath)
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Backup file not found")
    
    local_db = LocalDatabase(current_user.id)
    stats = local_db.import_from_file(filepath, merge=data.merge)
    
    return ImportResponse(ok=True, stats=stats)


@router.get("/stats", response_model=StorageStatsResponse)
async def get_storage_stats(
    current_user: User = Depends(get_current_user)
):
    """Get storage statistics for the user"""
    from ..local_storage import LocalDatabase
    
    local_db = LocalDatabase(current_user.id)
    stats = local_db.get_storage_stats()
    
    return StorageStatsResponse(**stats)


@router.post("/cleanup", response_model=CleanupResponse)
async def cleanup_old_data(
    data: CleanupRequest,
    current_user: User = Depends(get_current_user)
):
    """Clean up old, unused data to save space"""
    from ..local_storage import LocalDatabase
    
    local_db = LocalDatabase(current_user.id)
    stats = local_db.cleanup_old_data(
        days_threshold=data.days_threshold,
        keep_favorites=data.keep_favorites,
        keep_important=data.keep_important
    )
    
    # Vacuum to reclaim space
    local_db.vacuum_database()
    
    return CleanupResponse(ok=True, stats=stats)


@router.post("/vacuum")
async def vacuum_database(
    current_user: User = Depends(get_current_user)
):
    """Vacuum the database to reclaim space"""
    from ..local_storage import LocalDatabase
    
    local_db = LocalDatabase(current_user.id)
    local_db.vacuum_database()
    
    return {"ok": True}


# =============================================================================
# Conversation Logs & Artifacts
# =============================================================================

@router.post("/conversations/{conversation_id}/logs")
async def write_conversation_log(
    conversation_id: str,
    task_name: str,
    content: str,
    current_user: User = Depends(get_current_user)
):
    """Deprecated: use /api/v1/conversations/{conversation_id}/logs instead."""
    _raise_conversation_endpoint_gone(f"/api/v1/conversations/{conversation_id}/logs")


@router.get("/conversations/{conversation_id}/logs")
async def list_conversation_logs(
    conversation_id: str,
    current_user: User = Depends(get_current_user)
):
    """Deprecated: use /api/v1/conversations/{conversation_id}/logs instead."""
    _raise_conversation_endpoint_gone(f"/api/v1/conversations/{conversation_id}/logs")


@router.post("/conversations/{conversation_id}/artifacts")
async def save_artifact(
    conversation_id: str,
    filename: str,
    content: str,
    current_user: User = Depends(get_current_user)
):
    """Deprecated: use /api/v1/conversations/{conversation_id}/artifacts instead."""
    _raise_conversation_endpoint_gone(f"/api/v1/conversations/{conversation_id}/artifacts")


@router.get("/conversations/{conversation_id}/artifacts")
async def list_artifacts(
    conversation_id: str,
    current_user: User = Depends(get_current_user)
):
    """Deprecated: use /api/v1/conversations/{conversation_id}/artifacts instead."""
    _raise_conversation_endpoint_gone(f"/api/v1/conversations/{conversation_id}/artifacts")


@router.get("/conversations/{conversation_id}/export")
async def export_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user)
):
    """Deprecated: use /api/v1/conversations/{conversation_id}/export instead."""
    _raise_conversation_endpoint_gone(f"/api/v1/conversations/{conversation_id}/export")


# =============================================================================
# Encryption Endpoints
# =============================================================================

class EncryptionRequest(BaseModel):
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class EncryptionStatusResponse(BaseModel):
    encrypted: bool
    sqlcipher_available: bool
    locked: bool
    unlocked: bool
    message: str = ""


def _raise_encryption_route_error(message: str, invalid_password_status: int = 400) -> None:
    status_code = invalid_password_status if message == "Invalid password" else 400
    if "SQLCipher" in message:
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    raise HTTPException(status_code=status_code, detail=message)


@router.get("/encryption/status", response_model=EncryptionStatusResponse)
async def get_encryption_status(
    current_user: User = Depends(get_current_user)
):
    """Get current encryption status for user's database"""
    from ..local_storage import LocalDatabase, check_encryption_available
    
    # Check SQLCipher availability
    cipher_status = check_encryption_available()
    
    local_db = LocalDatabase(current_user.id)
    status = local_db.get_encryption_status()
    
    return EncryptionStatusResponse(
        **status,
        message=cipher_status.get("message", "")
    )


@router.post("/encryption/enable")
async def enable_encryption(
    data: EncryptionRequest,
    current_user: User = Depends(get_current_user)
):
    """Enable encryption for user's database"""
    from ..local_storage import LocalDatabase
    
    local_db = LocalDatabase(current_user.id)
    result = local_db.enable_encryption(data.password)
    
    if not result["ok"]:
        _raise_encryption_route_error(result["message"])
    
    return result


@router.post("/encryption/unlock")
async def unlock_database(
    data: EncryptionRequest,
    current_user: User = Depends(get_current_user)
):
    """Unlock encrypted database"""
    from ..local_storage import LocalDatabase
    
    local_db = LocalDatabase(current_user.id, encryption_password=data.password)
    
    if local_db.is_unlocked:
        return {"ok": True, "message": "Database unlocked"}

    result = local_db.unlock(data.password)
    if not result["ok"]:
        _raise_encryption_route_error(result["message"], invalid_password_status=401)
    return result


@router.post("/encryption/change-password")
async def change_encryption_password(
    data: ChangePasswordRequest,
    current_user: User = Depends(get_current_user)
):
    """Change encryption password"""
    from ..local_storage import LocalDatabase
    
    local_db = LocalDatabase(current_user.id, encryption_password=data.old_password)
    result = local_db.change_password(data.old_password, data.new_password)
    
    if not result["ok"]:
        _raise_encryption_route_error(result["message"])
    
    return result


@router.post("/encryption/disable")
async def disable_encryption(
    data: EncryptionRequest,
    current_user: User = Depends(get_current_user)
):
    """Disable encryption (decrypt database)
    
    WARNING: This removes encryption protection from your data.
    """
    from ..local_storage import LocalDatabase
    
    local_db = LocalDatabase(current_user.id, encryption_password=data.password)
    result = local_db.disable_encryption(data.password)
    
    if not result["ok"]:
        _raise_encryption_route_error(result["message"])
    
    return result


@router.get("/encryption/check")
async def check_encryption_support():
    """Check if SQLCipher encryption is available on this system"""
    from ..local_storage import check_encryption_available
    
    status = check_encryption_available()
    
    return {
        "available": status["sqlcipher_available"],
        "library": status.get("library"),
        "message": status["message"],
        "install_command": "pip install sqlcipher3-binary" if not status["sqlcipher_available"] else None
    }
