const DEVICE_SCOPE_ID_STORAGE_KEY = 'pigtex:device-scope-id'
const KNOWN_ACCOUNT_IDS_STORAGE_KEY = 'pigtex:known-account-ids'

const normalizeValue = (value?: string | null) => {
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

const createDeviceScopeId = () => {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return `device-${crypto.randomUUID()}`
    }

    const randomPart = Math.random().toString(36).slice(2, 12)
    const timePart = Date.now().toString(36)
    return `device-${timePart}${randomPart}`
}

export const getOrCreateDeviceScopeId = () => {
    const storage = getStorage()
    if (!storage) return null

    const existing = normalizeValue(storage.getItem(DEVICE_SCOPE_ID_STORAGE_KEY))
    if (existing) {
        return existing
    }

    const created = createDeviceScopeId()
    storage.setItem(DEVICE_SCOPE_ID_STORAGE_KEY, created)
    return created
}

export const getKnownAccountIds = () => {
    const storage = getStorage()
    if (!storage) return []

    try {
        const raw = JSON.parse(storage.getItem(KNOWN_ACCOUNT_IDS_STORAGE_KEY) || '[]')
        if (!Array.isArray(raw)) return []

        const seen = new Set<string>()
        const normalized: string[] = []
        for (const item of raw) {
            const candidate = normalizeValue(typeof item === 'string' ? item : null)
            if (!candidate || seen.has(candidate)) continue
            seen.add(candidate)
            normalized.push(candidate)
        }
        return normalized
    } catch {
        return []
    }
}

export const rememberKnownAccountId = (userId?: string | null) => {
    const storage = getStorage()
    const normalizedUserId = normalizeValue(userId)
    if (!storage || !normalizedUserId) return

    const nextIds = Array.from(new Set([...getKnownAccountIds(), normalizedUserId]))
    storage.setItem(KNOWN_ACCOUNT_IDS_STORAGE_KEY, JSON.stringify(nextIds))
}

export const applyDeviceScopeHeaders = (headers: Record<string, string>) => {
    const deviceScopeId = getOrCreateDeviceScopeId()
    if (deviceScopeId) {
        headers['X-PigTex-Device-Scope'] = deviceScopeId
    }

    const knownAccountIds = getKnownAccountIds()
    if (knownAccountIds.length > 0) {
        headers['X-PigTex-Legacy-Accounts'] = knownAccountIds.join(',')
    }
}

export {
    DEVICE_SCOPE_ID_STORAGE_KEY,
    KNOWN_ACCOUNT_IDS_STORAGE_KEY,
}
