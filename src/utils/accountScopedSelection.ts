const ACTIVE_WORKSPACE_SELECTION_STORAGE_KEY = 'active_workspace_id'
const ACTIVE_CONVERSATION_SELECTION_STORAGE_KEY = 'pigtex:active-conversation'

const normalizeStorageValue = (value?: string | null) => {
    if (typeof value !== 'string') return null
    const normalized = value.trim()
    return normalized || null
}

const getStorage = () => {
    if (typeof window === 'undefined') return null

    try {
        return window.localStorage
    } catch {
        return null
    }
}

export const readStoredWorkspaceSelection = (_userId?: string | null) => {
    const storage = getStorage()
    if (!storage) return null
    return normalizeStorageValue(storage.getItem(ACTIVE_WORKSPACE_SELECTION_STORAGE_KEY))
}

export const persistStoredWorkspaceSelection = (_userId: string | null | undefined, workspaceId?: string | null) => {
    const storage = getStorage()
    if (!storage) return

    const normalizedWorkspaceId = normalizeStorageValue(workspaceId)
    if (normalizedWorkspaceId) {
        storage.setItem(ACTIVE_WORKSPACE_SELECTION_STORAGE_KEY, normalizedWorkspaceId)
        return
    }

    storage.removeItem(ACTIVE_WORKSPACE_SELECTION_STORAGE_KEY)
}

export const readStoredConversationSelection = (_userId?: string | null) => {
    const storage = getStorage()
    if (!storage) return null
    return normalizeStorageValue(storage.getItem(ACTIVE_CONVERSATION_SELECTION_STORAGE_KEY))
}

export const persistStoredConversationSelection = (_userId: string | null | undefined, conversationId?: string | null) => {
    const storage = getStorage()
    if (!storage) return

    const normalizedConversationId = normalizeStorageValue(conversationId)
    if (normalizedConversationId) {
        storage.setItem(ACTIVE_CONVERSATION_SELECTION_STORAGE_KEY, normalizedConversationId)
        return
    }

    storage.removeItem(ACTIVE_CONVERSATION_SELECTION_STORAGE_KEY)
}

export {
    ACTIVE_CONVERSATION_SELECTION_STORAGE_KEY,
    ACTIVE_WORKSPACE_SELECTION_STORAGE_KEY,
}
