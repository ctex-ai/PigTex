from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime


# ============ Auth Schemas ============

class UserCreate(BaseModel):
    email: EmailStr
    username: str
    password: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    user_id: Optional[str] = None


class UserResponse(BaseModel):
    id: str
    email: str
    username: str
    plan: str
    role: str = "user"
    is_admin: bool = False
    permissions: List[str] = []
    is_active: bool
    created_at: datetime
    last_login: Optional[datetime] = None
    has_password: bool = False
    oauth_provider: Optional[str] = None
    avatar_url: Optional[str] = None
    
    class Config:
        from_attributes = True


# ============ Chat Schemas ============

class ChatMessage(BaseModel):
    role: str  # system, user, assistant
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "gpt-4o"
    messages: List[ChatMessage]
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False


class ChatCompletionChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str


class ChatCompletionUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionChoice]
    usage: ChatCompletionUsage


# ============ Usage Schemas ============

class UsageResponse(BaseModel):
    total_requests: int
    total_tokens: int
    total_cost: float
    period: str  # daily, weekly, monthly
    
    
class UsageSummary(BaseModel):
    today: UsageResponse
    this_month: UsageResponse


# ============ Smart Chat Schemas (with Memory) ============

class SmartChatRequest(BaseModel):
    """Request for smart chat with full memory system"""
    model: str = "gpt-4o"
    message: str  # Single message (not array)
    conversation_id: Optional[str] = None  # Continue existing conversation
    workspace_id: Optional[str] = None  # Project context
    runtime_instruction: Optional[str] = None  # Runtime-only system instruction (not saved as user msg)
    
    # Options
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False
    
    # Memory options
    use_knowledge: Optional[bool] = True  # Include knowledge items
    use_facts: Optional[bool] = True  # Include learned facts
    use_history: Optional[bool] = True  # Include conversation history


class SmartChatSource(BaseModel):
    """Source citation for RAG"""
    index: int
    id: str
    title: str
    type: str  # knowledge, fact, conversation


class SmartChatResponse(BaseModel):
    """Response from smart chat"""
    id: str
    conversation_id: str
    message: ChatMessage
    sources: Optional[List[SmartChatSource]] = None
    usage: Optional[ChatCompletionUsage] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


class ConversationCreate(BaseModel):
    """Create a new conversation"""
    title: str = "New Conversation"
    workspace_id: Optional[str] = None


class ConversationResponse(BaseModel):
    """Conversation response"""
    id: str
    title: str
    workspace_id: Optional[str] = None
    total_messages: int
    total_tokens: int
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

