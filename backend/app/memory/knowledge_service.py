"""
Knowledge Service - CRUD operations for knowledge items and workspaces.
Now uses LOCAL SQLite storage for privacy-first design (like Cursor/Antigravity).
"""

from typing import List, Optional
from datetime import datetime
import uuid

from ..local_storage import LocalDatabase, LocalWorkspace, LocalKnowledgeItem


class KnowledgeService:
    """
    Service for managing knowledge items and workspaces.
    Uses LOCAL SQLite storage for privacy (not server MySQL).
    """
    
    def __init__(self, user_id: str):
        self.local = LocalDatabase(user_id)
        self.user_id = self.local.user_id
    
    # =========================================================================
    # Workspace Operations (LOCAL)
    # =========================================================================
    
    def create_workspace(
        self,
        name: str,
        icon: str = "📁",
        color: str = "#6366f1",
        parent_id: Optional[str] = None
    ) -> LocalWorkspace:
        """Create a new workspace (stored locally)"""
        workspace = LocalWorkspace(
            id=str(uuid.uuid4()),
            user_id=self.user_id,
            name=name,
            icon=icon,
            color=color,
            parent_id=parent_id,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        self.local.save_workspace(workspace)
        return workspace
    
    def get_workspaces(self, parent_id: Optional[str] = None) -> List[LocalWorkspace]:
        """Get all workspaces for user, optionally filtered by parent"""
        return self.local.get_workspaces(parent_id)
    
    def get_workspace(self, workspace_id: str) -> Optional[LocalWorkspace]:
        """Get a single workspace by ID"""
        return self.local.get_workspace(workspace_id)
    
    def update_workspace(
        self,
        workspace_id: str,
        name: Optional[str] = None,
        icon: Optional[str] = None,
        color: Optional[str] = None
    ) -> Optional[LocalWorkspace]:
        """Update a workspace"""
        return self.local.update_workspace(workspace_id, name, icon, color)
    
    def delete_workspace(self, workspace_id: str) -> bool:
        """Delete a workspace and orphan its contents"""
        return self.local.delete_workspace(workspace_id)
    
    # =========================================================================
    # Knowledge Item Operations (LOCAL)
    # =========================================================================
    
    def create_knowledge_item(
        self,
        workspace_id: str,
        title: str,
        content: str = "",
        content_type: str = "note",
        metadata: Optional[dict] = None
    ) -> LocalKnowledgeItem:
        """Create a new knowledge item (stored locally)"""
        import json
        
        item = LocalKnowledgeItem(
            id=str(uuid.uuid4()),
            user_id=self.user_id,
            workspace_id=workspace_id,
            title=title,
            content=content,
            content_type=content_type,
            metadata_json=json.dumps(metadata) if metadata else None,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        self.local.save_knowledge_item(item)
        
        # Update workspace item count
        if workspace_id:
            self.local.update_workspace_item_count(workspace_id)
        
        return item
    
    def get_knowledge_items(
        self,
        workspace_id: Optional[str] = None,
        content_type: Optional[str] = None,
        favorites_only: bool = False,
        limit: int = 100
    ) -> List[LocalKnowledgeItem]:
        """Get knowledge items with filters"""
        # Use local database search
        with self.local._get_connection() as conn:
            query = "SELECT * FROM knowledge_items WHERE user_id = ?"
            params = [self.user_id]
            
            if workspace_id:
                query += " AND workspace_id = ?"
                params.append(workspace_id)
            if content_type:
                query += " AND content_type = ?"
                params.append(content_type)
            if favorites_only:
                query += " AND is_favorite = 1"
            
            query += " ORDER BY updated_at DESC LIMIT ?"
            params.append(limit)
            
            cursor = conn.execute(query, params)
            return [self.local._row_to_knowledge_item(row) for row in cursor]
    
    def get_knowledge_item(self, item_id: str) -> Optional[LocalKnowledgeItem]:
        """Get a single knowledge item by ID"""
        with self.local._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM knowledge_items WHERE id = ? AND user_id = ?",
                (item_id, self.user_id)
            )
            row = cursor.fetchone()
            if row:
                return self.local._row_to_knowledge_item(row)
        return None
    
    def update_knowledge_item(
        self,
        item_id: str,
        title: Optional[str] = None,
        content: Optional[str] = None,
        is_favorite: Optional[bool] = None,
        is_pinned: Optional[bool] = None
    ) -> Optional[LocalKnowledgeItem]:
        """Update a knowledge item"""
        item = self.get_knowledge_item(item_id)
        if not item:
            return None
        
        if title is not None:
            item.title = title
        if content is not None:
            item.content = content
        if is_favorite is not None:
            item.is_favorite = is_favorite
        if is_pinned is not None:
            item.is_pinned = is_pinned
        
        item.updated_at = datetime.now()
        self.local.save_knowledge_item(item)
        return item
    
    def delete_knowledge_item(self, item_id: str) -> bool:
        """Delete a knowledge item"""
        item = self.get_knowledge_item(item_id)
        if not item:
            return False
        
        workspace_id = item.workspace_id
        
        with self.local._get_connection() as conn:
            conn.execute("DELETE FROM knowledge_items WHERE id = ?", (item_id,))
        
        # Update workspace item count
        if workspace_id:
            self.local.update_workspace_item_count(workspace_id)
        
        return True
    
    def search_knowledge(self, query: str, limit: int = 20) -> List[LocalKnowledgeItem]:
        """Search knowledge items using full-text search"""
        return self.local.search_knowledge_fts(query, limit)
    
    def get_recent_items(self, limit: int = 10) -> List[LocalKnowledgeItem]:
        """Get recently updated knowledge items"""
        with self.local._get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM knowledge_items 
                WHERE user_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
            """, (self.user_id, limit))
            return [self.local._row_to_knowledge_item(row) for row in cursor]


def get_knowledge_service(user_id: str) -> KnowledgeService:
    """Factory function to create knowledge service (now uses local storage)"""
    return KnowledgeService(user_id)
