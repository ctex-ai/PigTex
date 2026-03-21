import { afterEach, describe, expect, it, vi } from 'vitest'
import {
    buildAiFileRuntimeInstruction,
    createAiFileExecutionContext,
    executeAiFileActionsFromParsed,
    ParsedAiFileAction
} from './aiFileActions'

describe('aiFileActions', () => {
    afterEach(() => {
        const windowWithElectron = window as unknown as { electronAPI?: unknown }
        windowWithElectron.electronAPI = undefined
    })

    it('treats repeated create_file with identical content as idempotent success', async () => {
        const createFile = vi.fn().mockRejectedValue(new Error('File already exists'))
        const readFile = vi.fn().mockResolvedValue({
            content: 'hello',
            size: 5,
            mtimeMs: 123
        })
        const writeFile = vi.fn()

        const windowWithElectron = window as unknown as {
            electronAPI?: {
                createFile: (payload: { parentPath: string; fileName: string; content: string }) => Promise<void>
                readFile: (path: string) => Promise<{ content: string; size: number; mtimeMs: number }>
                writeFile: (payload: { filePath: string; content: string }) => Promise<void>
                createFolder: (payload: { parentPath: string; folderName: string }) => Promise<void>
                deletePath: (payload: { targetPath: string }) => Promise<void>
            }
        }
        windowWithElectron.electronAPI = {
            createFile,
            readFile,
            writeFile,
            createFolder: vi.fn().mockResolvedValue(undefined),
            deletePath: vi.fn().mockResolvedValue(undefined)
        }

        const actions: ParsedAiFileAction[] = [
            {
                type: 'create_file',
                path: 'cay_thong_noel.py',
                content: 'hello'
            }
        ]

        const result = await executeAiFileActionsFromParsed(actions, 'D:\\Root')
        expect(result).not.toBeNull()
        expect(result?.errors).toHaveLength(0)
        expect(result?.applied).toBe(1)
        expect(result?.logs.some(log => log.includes('Skipped create (already exists, same content)'))).toBe(true)
        expect(writeFile).not.toHaveBeenCalled()
    })

    it('emits single-step instruction for chat-like mode', () => {
        const instruction = buildAiFileRuntimeInstruction('D:\\Root', {
            executionMode: 'single_step'
        })
        expect(instruction).toContain('Single-step mode')
    })

    it('applies apply_diff SEARCH/REPLACE patch on existing file', async () => {
        const readFile = vi.fn().mockResolvedValue({
            content: 'line 1\nold value\nline 3\n',
            size: 23,
            mtimeMs: 123
        })
        const writeFile = vi.fn().mockResolvedValue({
            ok: true,
            size: 23,
            mtimeMs: 124
        })

        const windowWithElectron = window as unknown as {
            electronAPI?: {
                readFile: (path: string) => Promise<{ content: string; size: number; mtimeMs: number }>
                writeFile: (payload: { filePath: string; content: string }) => Promise<{ ok: boolean; size: number; mtimeMs: number }>
                createFolder: (payload: { parentPath: string; folderName: string }) => Promise<void>
                createFile: (payload: { parentPath: string; fileName: string; content: string }) => Promise<void>
                deletePath: (payload: { targetPath: string }) => Promise<void>
            }
        }
        windowWithElectron.electronAPI = {
            readFile,
            writeFile,
            createFolder: vi.fn().mockResolvedValue(undefined),
            createFile: vi.fn().mockResolvedValue(undefined),
            deletePath: vi.fn().mockResolvedValue(undefined),
        }

        const patch = [
            '<<<<<<< SEARCH',
            'old value',
            '=======',
            'new value',
            '>>>>>>> REPLACE'
        ].join('\n')

        const actions: ParsedAiFileAction[] = [
            {
                type: 'apply_diff',
                path: 'src/app.ts',
                content: patch
            }
        ]

        const result = await executeAiFileActionsFromParsed(actions, 'D:\\Root')
        expect(result).not.toBeNull()
        expect(result?.errors).toHaveLength(0)
        expect(result?.applied).toBe(1)
        expect(writeFile).toHaveBeenCalledTimes(1)
        expect(writeFile.mock.calls[0][0].content).toContain('new value')
        expect(writeFile.mock.calls[0][0].content).not.toContain('old value')
    })

    it('emits multi-step instruction for codex-like mode', () => {
        const instruction = buildAiFileRuntimeInstruction('D:\\Root', {
            executionMode: 'multi_step'
        })
        expect(instruction).toContain('Multi-step mode')
        expect(instruction).toContain('Never guess conventional names such as index.html')
        expect(instruction).toContain('This rule also applies to follow-up folder listings inside that directory')
    })

    it('normalizes list_directory root path and invokes listDirectory with absolute dirPath', async () => {
        const listDirectory = vi.fn().mockResolvedValue([])

        const windowWithElectron = window as unknown as {
            electronAPI?: {
                listDirectory: (payload: { rootPath: string; dirPath: string }) => Promise<Array<{
                    name: string
                    path: string
                    type: 'file' | 'directory'
                    size: number
                    mtimeMs: number
                }>>
            }
        }
        windowWithElectron.electronAPI = {
            listDirectory
        }

        const actions: ParsedAiFileAction[] = [
            {
                type: 'list_directory',
                path: '.'
            }
        ]

        const result = await executeAiFileActionsFromParsed(actions, 'D:\\Root')
        expect(result).not.toBeNull()
        expect(result?.errors).toHaveLength(0)
        expect(result?.applied).toBe(1)
        expect(listDirectory).toHaveBeenCalledTimes(1)
        expect(listDirectory).toHaveBeenCalledWith({
            rootPath: 'D:\\Root',
            dirPath: 'D:\\Root'
        })
    })

    it('blocks guessed read_file paths that are absent from the latest listing', async () => {
        const listDirectory = vi.fn().mockResolvedValue([
            {
                name: 'landing.html',
                path: 'D:\\Root\\landing.html',
                type: 'file',
                size: 120,
                mtimeMs: 100
            },
            {
                name: 'assets',
                path: 'D:\\Root\\assets',
                type: 'directory',
                size: 0,
                mtimeMs: 101
            }
        ])
        const readFile = vi.fn()
        const executionContext = createAiFileExecutionContext()

        const windowWithElectron = window as unknown as {
            electronAPI?: {
                listDirectory: (payload: { rootPath: string; dirPath: string }) => Promise<Array<{
                    name: string
                    path: string
                    type: 'file' | 'directory'
                    size: number
                    mtimeMs: number
                }>>
                readFile: (path: string) => Promise<{ content: string; size: number; mtimeMs: number }>
            }
        }
        windowWithElectron.electronAPI = {
            listDirectory,
            readFile
        }

        await executeAiFileActionsFromParsed(
            [{ type: 'list_directory', path: '.' }],
            'D:\\Root',
            undefined,
            executionContext
        )

        const result = await executeAiFileActionsFromParsed(
            [{ type: 'read_file', path: 'index.html' }],
            'D:\\Root',
            undefined,
            executionContext
        )

        expect(readFile).not.toHaveBeenCalled()
        expect(result?.applied).toBe(0)
        expect(result?.errors).toHaveLength(1)
        expect(result?.errors[0]).toContain('Path is not present in the latest directory listing')
        expect(result?.errors[0]).toContain('landing.html')
        expect(result?.errors[0]).toContain('assets/')
    })

    it('blocks guessed subfolder listings that were not returned by the latest parent listing', async () => {
        const listDirectory = vi.fn().mockResolvedValue([
            {
                name: 'bauer-cuo',
                path: 'D:\\Root\\bauer-cuo',
                type: 'directory',
                size: 0,
                mtimeMs: 101
            }
        ])
        const executionContext = createAiFileExecutionContext()

        const windowWithElectron = window as unknown as {
            electronAPI?: {
                listDirectory: (payload: { rootPath: string; dirPath: string }) => Promise<Array<{
                    name: string
                    path: string
                    type: 'file' | 'directory'
                    size: number
                    mtimeMs: number
                }>>
            }
        }
        windowWithElectron.electronAPI = {
            listDirectory
        }

        await executeAiFileActionsFromParsed(
            [{ type: 'list_directory', path: '.' }],
            'D:\\Root',
            undefined,
            executionContext
        )

        const result = await executeAiFileActionsFromParsed(
            [{ type: 'list_directory', path: 'bau_cu' }],
            'D:\\Root',
            undefined,
            executionContext
        )

        expect(listDirectory).toHaveBeenCalledTimes(1)
        expect(result?.applied).toBe(0)
        expect(result?.errors).toHaveLength(1)
        expect(result?.errors[0]).toContain('Path is not present in the latest directory listing')
        expect(result?.errors[0]).toContain('bauer-cuo/')
    })

    it('normalizes missing list_directory errors with a parent-listing hint', async () => {
        const listDirectory = vi.fn().mockRejectedValue(new Error('ENOENT: no such file or directory'))

        const windowWithElectron = window as unknown as {
            electronAPI?: {
                listDirectory: (payload: { rootPath: string; dirPath: string }) => Promise<Array<{
                    name: string
                    path: string
                    type: 'file' | 'directory'
                    size: number
                    mtimeMs: number
                }>>
            }
        }
        windowWithElectron.electronAPI = {
            listDirectory
        }

        const result = await executeAiFileActionsFromParsed(
            [{ type: 'list_directory', path: 'bau_cu' }],
            'D:\\Root',
            undefined,
            createAiFileExecutionContext()
        )

        expect(result?.applied).toBe(0)
        expect(result?.errors).toHaveLength(1)
        expect(result?.errors[0]).toContain('Folder not found: bau_cu (D:\\Root\\bau_cu)')
        expect(result?.errors[0]).toContain('list its parent folder first')
    })

    it('invalidates stale directory snapshots after file mutations', async () => {
        const listDirectory = vi.fn().mockResolvedValue([])
        const writeFile = vi.fn().mockResolvedValue({
            ok: true,
            size: 42,
            mtimeMs: 124
        })
        const readFile = vi.fn().mockResolvedValue({
            content: '<html></html>',
            size: 13,
            mtimeMs: 125
        })
        const executionContext = createAiFileExecutionContext()

        const windowWithElectron = window as unknown as {
            electronAPI?: {
                listDirectory: (payload: { rootPath: string; dirPath: string }) => Promise<Array<{
                    name: string
                    path: string
                    type: 'file' | 'directory'
                    size: number
                    mtimeMs: number
                }>>
                writeFile: (payload: { filePath: string; content: string }) => Promise<{ ok: boolean; size: number; mtimeMs: number }>
                readFile: (path: string) => Promise<{ content: string; size: number; mtimeMs: number }>
                createFolder: (payload: { parentPath: string; folderName: string }) => Promise<void>
                createFile: (payload: { parentPath: string; fileName: string; content: string }) => Promise<void>
                deletePath: (payload: { targetPath: string }) => Promise<void>
            }
        }
        windowWithElectron.electronAPI = {
            listDirectory,
            writeFile,
            readFile,
            createFolder: vi.fn().mockResolvedValue(undefined),
            createFile: vi.fn().mockResolvedValue(undefined),
            deletePath: vi.fn().mockResolvedValue(undefined),
        }

        await executeAiFileActionsFromParsed(
            [{ type: 'list_directory', path: '.' }],
            'D:\\Root',
            undefined,
            executionContext
        )

        await executeAiFileActionsFromParsed(
            [{ type: 'write_file', path: 'index.html', content: '<html></html>' }],
            'D:\\Root',
            undefined,
            executionContext
        )

        const result = await executeAiFileActionsFromParsed(
            [{ type: 'read_file', path: 'index.html' }],
            'D:\\Root',
            undefined,
            executionContext
        )

        expect(readFile).toHaveBeenCalledWith('D:\\Root\\index.html')
        expect(result?.errors).toHaveLength(0)
        expect(result?.applied).toBe(1)
    })
})
