import { beforeEach, describe, expect, it } from 'vitest'

import {
    ACTIVE_CONVERSATION_SELECTION_STORAGE_KEY,
    ACTIVE_WORKSPACE_SELECTION_STORAGE_KEY,
    persistStoredConversationSelection,
    persistStoredWorkspaceSelection,
    readStoredConversationSelection,
    readStoredWorkspaceSelection,
} from './accountScopedSelection'

describe('accountScopedSelection', () => {
    beforeEach(() => {
        window.localStorage.clear()
    })

    it('keeps workspace selection shared across account switches on the same machine', () => {
        persistStoredWorkspaceSelection('user-a', 'workspace-a')

        expect(readStoredWorkspaceSelection('user-a')).toBe('workspace-a')
        expect(readStoredWorkspaceSelection('user-b')).toBe('workspace-a')
        expect(window.localStorage.getItem(ACTIVE_WORKSPACE_SELECTION_STORAGE_KEY)).toBe('workspace-a')
    })

    it('keeps conversation selection shared across account switches on the same machine', () => {
        persistStoredConversationSelection('user-a', 'conversation-a')

        expect(readStoredConversationSelection('user-a')).toBe('conversation-a')
        expect(readStoredConversationSelection('user-b')).toBe('conversation-a')
        expect(window.localStorage.getItem(ACTIVE_CONVERSATION_SELECTION_STORAGE_KEY)).toBe('conversation-a')
    })

    it('clears shared selections when removed', () => {
        persistStoredWorkspaceSelection('user-a', 'workspace-a')
        persistStoredConversationSelection('user-a', 'conversation-a')

        persistStoredWorkspaceSelection('user-b', null)
        persistStoredConversationSelection('user-b', null)

        expect(readStoredWorkspaceSelection('user-a')).toBeNull()
        expect(readStoredConversationSelection('user-a')).toBeNull()
        expect(window.localStorage.getItem(ACTIVE_WORKSPACE_SELECTION_STORAGE_KEY)).toBeNull()
        expect(window.localStorage.getItem(ACTIVE_CONVERSATION_SELECTION_STORAGE_KEY)).toBeNull()
    })
})
