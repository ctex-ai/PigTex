export type KnowledgeEditorTarget = {
    source: 'knowledge'
    id: string
}

export type LocalEditorTarget = {
    source: 'local'
    path: string
    rootPath: string
    name: string
}

export type EditorTarget = KnowledgeEditorTarget | LocalEditorTarget
