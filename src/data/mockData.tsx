import {
    Rocket,
    Lightbulb,
    Star,
    FileText,
    MessageSquare,
    Folder,
    BookOpen,
    Brain,
    Sparkles,
    Clock,
    Hash
} from 'lucide-react'

// ===== Workspaces =====
export interface Workspace {
    id: string
    name: string
    icon: string
    color: string
    documentsCount: number
    lastAccessed: string
}

export const workspaces: Workspace[] = [
    {
        id: 'w1',
        name: 'Project Phoenix',
        icon: 'rocket',
        color: '#6366F1',
        documentsCount: 12,
        lastAccessed: '2 hours ago'
    },
    {
        id: 'w2',
        name: 'Research Hub',
        icon: 'lightbulb',
        color: '#F59E0B',
        documentsCount: 8,
        lastAccessed: '1 day ago'
    },
    {
        id: 'w3',
        name: 'Design System',
        icon: 'sparkles',
        color: '#EC4899',
        documentsCount: 5,
        lastAccessed: '3 days ago'
    },
    {
        id: 'w4',
        name: 'Knowledge Base',
        icon: 'book',
        color: '#10B981',
        documentsCount: 24,
        lastAccessed: '1 week ago'
    }
]

// ===== Documents =====
export interface Document {
    id: string
    title: string
    excerpt: string
    type: 'note' | 'chat' | 'document' | 'idea'
    lastModified: string
    workspaceId: string
    isFavorite: boolean
}

export const documents: Document[] = [
    {
        id: 'd1',
        title: 'Architecture Overview',
        excerpt: 'High-level system design for the AI workstation including the three-layer architecture...',
        type: 'document',
        lastModified: '10 min ago',
        workspaceId: 'w1',
        isFavorite: true
    },
    {
        id: 'd2',
        title: 'API Design Notes',
        excerpt: 'RESTful endpoints for the Gateway service, authentication flow, and rate limiting...',
        type: 'note',
        lastModified: '1 hour ago',
        workspaceId: 'w1',
        isFavorite: false
    },
    {
        id: 'd3',
        title: 'Brainstorm: User Onboarding',
        excerpt: 'Ideas for creating a seamless first-time experience for new users...',
        type: 'idea',
        lastModified: '3 hours ago',
        workspaceId: 'w2',
        isFavorite: true
    },
    {
        id: 'd4',
        title: 'Chat: Model Comparison',
        excerpt: 'Discussion about GPT-4 vs Gemini Pro performance characteristics...',
        type: 'chat',
        lastModified: 'Yesterday',
        workspaceId: 'w2',
        isFavorite: false
    },
    {
        id: 'd5',
        title: 'Color Palette Research',
        excerpt: 'Exploring modern dark theme palettes with purple and blue accents...',
        type: 'note',
        lastModified: '2 days ago',
        workspaceId: 'w3',
        isFavorite: false
    }
]

// ===== Current Document Content =====
export const currentDocumentContent = `# Project Phoenix: Architecture Overview

## Vision

Build a **"General Assistant IDE"** powered by a proprietary **"AI Power Plant"**. The system abstracts the complexity of managing multiple AI accounts and provides users with a unified, verified, and stateful work environment.

## Core Principles

### 🚀 Infinite Intelligence
Users should never worry about rate limits or quotas. The system handles rotation transparently across multiple AI providers.

### ✅ Accountability
Every AI generation allows for verification, citation, and fact-checking. No more black-box responses.

### 🧠 Contextual Mastery
The IDE remembers facts, decisions, and sources better than any standard chat interface.

---

## System Layers

### Layer 1: Resource Factory
*The "Power Plant" - manages raw AI account access*

- **ChatGPT Pool**: Dockerized infrastructure for ChatGPT Plus accounts
- **Gemini Farm**: Automated provisioning and rotation of API keys

### Layer 2: The Gateway
*The "Router" - intelligent distribution and load balancing*

- Unified API endpoint compatible with OpenAI SDKs
- Smart routing based on query intent
- Health checks and circuit breakers

### Layer 3: The Application
*The "Assistant IDE" - user-facing client*

- Local-first data storage
- Rich document editing
- AI-powered workflows

---

## Next Steps

- [ ] Finalize Gateway API schema
- [ ] Set up Docker infrastructure
- [ ] Design the main dashboard UI
- [ ] Implement chat streaming
`

// ===== AI Conversations =====
export interface Message {
    id: string
    role: 'user' | 'assistant'
    content: string
    timestamp: string
}

export const conversations: Message[] = [
    {
        id: 'm1',
        role: 'user',
        content: 'Can you summarize the key differences between our proposed architecture and a traditional monolithic approach?',
        timestamp: '2 min ago'
    },
    {
        id: 'm2',
        role: 'assistant',
        content: `Great question! Here are the **key differences**:

**1. Multi-Provider Abstraction**
Unlike monolithic systems tied to a single API, our architecture aggregates multiple AI providers (ChatGPT, Gemini) behind a unified Gateway.

**2. Local-First Storage**
Traditional apps rely on cloud storage. We prioritize local SQLite for privacy and offline access, with optional sync.

**3. Intelligent Routing**
Our Gateway analyzes query intent and routes to the optimal model - reasoning-heavy tasks go to GPT-4o, while high-context analysis uses Gemini.

**4. Built-in Verification**
Every response can be verified against stored sources. This accountability layer is absent in standard chat interfaces.

Would you like me to elaborate on any of these points?`,
        timestamp: '1 min ago'
    }
]

// ===== Quick Prompts =====
export const quickPrompts = [
    { id: 'q1', label: 'Summarize this', icon: 'sparkles' },
    { id: 'q2', label: 'Explain further', icon: 'lightbulb' },
    { id: 'q3', label: 'Find sources', icon: 'search' },
    { id: 'q4', label: 'Create outline', icon: 'list' },
]

// ===== Icon Mapping =====
export const iconMap: Record<string, typeof Rocket> = {
    rocket: Rocket,
    lightbulb: Lightbulb,
    star: Star,
    file: FileText,
    message: MessageSquare,
    folder: Folder,
    book: BookOpen,
    brain: Brain,
    sparkles: Sparkles,
    clock: Clock,
    hash: Hash
}
