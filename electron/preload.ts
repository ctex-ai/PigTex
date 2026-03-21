import { contextBridge, ipcRenderer } from 'electron'

type DesktopUpdateRequest = {
    manifestUrl?: string | null
}

contextBridge.exposeInMainWorld('electronAPI', {
    minimize: () => ipcRenderer.send('window-minimize'),
    maximize: () => ipcRenderer.send('window-maximize'),
    close: () => ipcRenderer.send('window-close'),
    openExternal: (url: string) => ipcRenderer.invoke('shell:open-external', { url }),
    getSystemInfo: () => ipcRenderer.invoke('app:get-system-info'),
    checkDesktopUpdate: (payload?: DesktopUpdateRequest) => ipcRenderer.invoke('app:check-desktop-update', payload),
    downloadAndInstallDesktopUpdate: (payload?: DesktopUpdateRequest) => ipcRenderer.invoke('app:download-and-install-desktop-update', payload),
    isSecureStorageAvailable: () => ipcRenderer.sendSync('settings:is-secure-storage-available'),
    getSecureApiKeys: () => ipcRenderer.sendSync('settings:get-secure-api-keys'),
    setSecureApiKeys: (payload: Record<string, string>) => ipcRenderer.sendSync('settings:set-secure-api-keys', payload),
    getSecureAuthToken: () => ipcRenderer.sendSync('settings:get-secure-auth-token'),
    setSecureAuthToken: (token: string) => ipcRenderer.sendSync('settings:set-secure-auth-token', token),
    clearSecureAuthToken: () => {
        ipcRenderer.sendSync('settings:set-secure-auth-token', null)
    },
    pickFolder: () => ipcRenderer.invoke('fs:pick-folder'),
    openFolder: (payload: { path: string }) => ipcRenderer.invoke('fs:open-folder', payload),
    getLocalSessionState: () => ipcRenderer.invoke('session:get-local-state'),
    updateLocalSessionState: (payload: { rootPath?: string | null; filePath?: string | null; fileName?: string | null }) =>
        ipcRenderer.invoke('session:update-local-state', payload),
    listDirectory: (payload: { rootPath: string; dirPath: string }) =>
        ipcRenderer.invoke('fs:list-directory', payload),
    readFile: (filePath: string) => ipcRenderer.invoke('fs:read-file', filePath),
    writeFile: (payload: { filePath: string; content: string }) =>
        ipcRenderer.invoke('fs:write-file', payload),
    createFile: (payload: { parentPath: string; fileName: string; content?: string }) =>
        ipcRenderer.invoke('fs:create-file', payload),
    createFolder: (payload: { parentPath: string; folderName: string }) =>
        ipcRenderer.invoke('fs:create-folder', payload),
    renamePath: (payload: { targetPath: string; newName: string }) =>
        ipcRenderer.invoke('fs:rename-path', payload),
    deletePath: (payload: { targetPath: string }) =>
        ipcRenderer.invoke('fs:delete-path', payload),
    getUndoState: () => ipcRenderer.invoke('fs:get-undo-state'),
    undoLastChange: () => ipcRenderer.invoke('fs:undo-last-change')
})
