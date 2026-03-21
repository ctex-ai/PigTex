"""
Conversation Memory Service - Episodic memory management.
"""

from typing import List, Optional
from sqlalchemy.orm import Session
import json

from ..models import Conversation, Message


class ConversationMemory:
    """Service for managing conversation history (episodic memory)"""
    
    def __init__(self, db: Session, user_id: str):
        self.db = db
        self.user_id = user_id
    
    # =========================================================================
    # Conversation Operations
    # =========================================================================
    
    def create_conversation(
        self,
        title: Optional[str] = None,
        workspace_id: Optional[str] = None
    ) -> Conversation:
        """Create a new conversation"""
        conv = Conversation(
            user_id=self.user_id,
            title=title or "New Conversation",
            workspace_id=workspace_id
        )
        self.db.add(conv)
        self.db.commit()
        self.db.refresh(conv)
        return conv
    
    def get_conversations(
        self,
        workspace_id: Optional[str] = None,
        limit: int = 50
    ) -> List[Conversation]:
        """Get conversations for user"""
        query = self.db.query(Conversation).filter(
            Conversation.user_id == self.user_id
        )
        
        if workspace_id:
            query = query.filter(Conversation.workspace_id == workspace_id)
        
        return query.order_by(Conversation.updated_at.desc()).limit(limit).all()
    
    def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """Get a single conversation by ID"""
        return self.db.query(Conversation).filter(
            Conversation.id == conversation_id,
            Conversation.user_id == self.user_id
        ).first()
    
    def update_conversation(
        self,
        conversation_id: str,
        title: Optional[str] = None,
        summary: Optional[str] = None
    ) -> Optional[Conversation]:
        """Update conversation metadata"""
        conv = self.get_conversation(conversation_id)
        if not conv:
            return None
        
        if title is not None:
            conv.title = title
        if summary is not None:
            conv.summary = summary
        
        self.db.commit()
        self.db.refresh(conv)
        return conv
    
    def delete_conversation(self, conversation_id: str) -> bool:
        """Delete a conversation and all its messages"""
        conv = self.get_conversation(conversation_id)
        if not conv:
            return False
        
        self.db.delete(conv)
        self.db.commit()
        return True
    
    # =========================================================================
    # Message Operations
    # =========================================================================
    
    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        model: Optional[str] = None,
        token_count: int = 0,
        sources: Optional[List[str]] = None
    ) -> Message:
        """Add a message to a conversation"""
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            model=model,
            token_count=token_count,
            sources_json=json.dumps(sources) if sources else None
        )
        self.db.add(message)
        
        # Update conversation stats
        conv = self.get_conversation(conversation_id)
        if conv:
            conv.total_messages = (conv.total_messages or 0) + 1
            conv.total_tokens = (conv.total_tokens or 0) + token_count
        
        self.db.commit()
        self.db.refresh(message)
        return message
    
    def get_messages(
        self,
        conversation_id: str,
        limit: Optional[int] = None
    ) -> List[Message]:
        """Get messages for a conversation"""
        query = self.db.query(Message).filter(
            Message.conversation_id == conversation_id
        ).order_by(Message.created_at)
        
        if limit:
            query = query.limit(limit)
        
        return query.all()
    
    def get_recent_messages(
        self,
        conversation_id: str,
        max_tokens: int = 8000
    ) -> List[Message]:
        """Get recent messages up to a token limit (for context window)"""
        messages = self.db.query(Message).filter(
            Message.conversation_id == conversation_id
        ).order_by(Message.created_at.desc()).all()
        
        result = []
        total_tokens = 0
        
        for msg in messages:
            msg_tokens = msg.token_count or len(msg.content.split()) * 1.3
            if total_tokens + msg_tokens > max_tokens:
                break
            result.append(msg)
            total_tokens += msg_tokens
        
        # Return in chronological order
        return list(reversed(result))
    
    def generate_title_from_content(self, conversation_id: str) -> str:
        """Generate a title from the first message"""
        messages = self.get_messages(conversation_id, limit=1)
        if messages:
            content = messages[0].content[:100]
            return content.split('\n')[0][:80] + "..."
        return "New Conversation"


def get_conversation_memory(db: Session, user_id: str) -> ConversationMemory:
    """Factory function to create conversation memory service"""
    return ConversationMemory(db, user_id)
