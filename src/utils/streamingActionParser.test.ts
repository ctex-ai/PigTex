import { describe, it, expect, beforeEach } from 'vitest'
import { StreamingActionParser, StreamingParserEvent } from './streamingActionParser'

describe('StreamingActionParser', () => {
    let parser: StreamingActionParser

    beforeEach(() => {
        parser = new StreamingActionParser()
    })

    const feedAll = (chunks: string[]): StreamingParserEvent[] => {
        const events: StreamingParserEvent[] = []
        for (const chunk of chunks) {
            events.push(...parser.feed(chunk))
        }
        events.push(...parser.flush())
        return events
    }

    it('parses a complete pigtex_write tag in one chunk', () => {
        const events = feedAll([
            '<pigtex_write path="hello.py">print("hello")</pigtex_write>'
        ])

        const actionStart = events.find(e => e.type === 'action_start')
        const actionEnd = events.find(e => e.type === 'action_end')

        expect(actionStart).toBeDefined()
        expect(actionStart!.type).toBe('action_start')
        expect((actionStart as any).action.path).toBe('hello.py')

        expect(actionEnd).toBeDefined()
        expect((actionEnd as any).action.content).toBe('print("hello")')
        expect((actionEnd as any).action.actionType).toBe('write_file')
    })

    it('streams content chunks across multiple feeds', () => {
        const events: StreamingParserEvent[] = []

        events.push(...parser.feed('<pigtex_write path="app.ts">'))
        events.push(...parser.feed('const x = 1\n'))
        events.push(...parser.feed('const y = 2\n'))
        events.push(...parser.feed('</pigtex_write>'))
        events.push(...parser.flush())

        const starts = events.filter(e => e.type === 'action_start')
        const chunks = events.filter(e => e.type === 'content_chunk')
        const ends = events.filter(e => e.type === 'action_end')

        expect(starts.length).toBe(1)
        expect(chunks.length).toBeGreaterThanOrEqual(1)
        expect(ends.length).toBe(1)

        const fullContent = chunks.map(c => (c as any).content).join('')
        expect(fullContent).toContain('const x = 1')
        expect(fullContent).toContain('const y = 2')
    })

    it('parses self-closing read tag', () => {
        const events = feedAll(['<pigtex_read path="src/main.ts" />'])

        const selfClose = events.find(e => e.type === 'self_closing_action')
        expect(selfClose).toBeDefined()
        expect((selfClose as any).action.actionType).toBe('read_file')
        expect((selfClose as any).action.path).toBe('src/main.ts')
    })

    it('parses self-closing delete tag', () => {
        const events = feedAll(['<pigtex_delete path="old.py" />'])

        const selfClose = events.find(e => e.type === 'self_closing_action')
        expect(selfClose).toBeDefined()
        expect((selfClose as any).action.actionType).toBe('delete_file')
        expect((selfClose as any).action.path).toBe('old.py')
    })

    it('parses self-closing mkdir tag', () => {
        const events = feedAll(['<pigtex_mkdir path="src/utils" />'])

        const selfClose = events.find(e => e.type === 'self_closing_action')
        expect(selfClose).toBeDefined()
        expect((selfClose as any).action.actionType).toBe('create_folder')
        expect((selfClose as any).action.path).toBe('src/utils')
    })

    it('parses rename tag with new_path', () => {
        const events = feedAll(['<pigtex_rename path="old.py" new_path="new.py" />'])

        const selfClose = events.find(e => e.type === 'self_closing_action')
        expect(selfClose).toBeDefined()
        expect((selfClose as any).action.actionType).toBe('rename_path')
        expect((selfClose as any).action.path).toBe('old.py')
        expect((selfClose as any).action.newPath).toBe('new.py')
    })

    it('parses single-quoted attributes', () => {
        const events = feedAll(["<pigtex_write path='src/a.ts'>export const a = 1\n</pigtex_write>"])
        const actionEnd = events.find(e => e.type === 'action_end')
        expect(actionEnd).toBeDefined()
        expect((actionEnd as any).action.path).toBe('src/a.ts')
        expect((actionEnd as any).action.content).toContain('export const a = 1')
    })

    it('parses non-self-closing read tag and consumes close tag', () => {
        const events = feedAll(['<pigtex_read path="src/main.ts"></pigtex_read>ok'])
        const readAction = events.find(e => e.type === 'self_closing_action')
        expect(readAction).toBeDefined()
        expect((readAction as any).action.actionType).toBe('read_file')

        const textEvents = events.filter(e => e.type === 'text')
        const allText = textEvents.map(e => (e as any).content).join('')
        expect(allText).not.toContain('</pigtex_read>')
        expect(allText).toContain('ok')
    })

    it('parses list directory tag', () => {
        const events = feedAll(['<pigtex_ls path="src/utils" />'])
        const action = events.find(e => e.type === 'self_closing_action')
        expect(action).toBeDefined()
        expect((action as any).action.actionType).toBe('list_directory')
        expect((action as any).action.path).toBe('src/utils')
    })

    it('parses dotted list directory tag alias', () => {
        const events = feedAll(['<pigtex.ls path=". " />'])
        const action = events.find(e => e.type === 'self_closing_action')
        expect(action).toBeDefined()
        expect((action as any).action.actionType).toBe('list_directory')
        expect((action as any).action.path).toBe('.')
    })

    it('parses patch tag with SEARCH/REPLACE content', () => {
        const events = feedAll([
            '<pigtex_patch path="src/app.ts"><<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE</pigtex_patch>'
        ])
        const actionEnd = events.find(e => e.type === 'action_end')
        expect(actionEnd).toBeDefined()
        expect((actionEnd as any).action.actionType).toBe('apply_diff')
        expect((actionEnd as any).action.path).toBe('src/app.ts')
        expect((actionEnd as any).action.content).toContain('<<<<<<< SEARCH')
    })

    it('preserves text outside of tags', () => {
        const events = feedAll([
            'Here is a file for you:\n<pigtex_write path="x.txt">hello</pigtex_write>\nDone!'
        ])

        const textEvents = events.filter(e => e.type === 'text')
        const allText = textEvents.map(t => (t as any).content).join('')
        expect(allText).toContain('Here is a file for you:')
        expect(allText).toContain('Done!')
    })

    it('handles tag split across chunks', () => {
        const events: StreamingParserEvent[] = []
        events.push(...parser.feed('<pigtex_wri'))
        events.push(...parser.feed('te path="test.js">'))
        events.push(...parser.feed('console.log(1)'))
        events.push(...parser.feed('</pigtex_write>'))
        events.push(...parser.flush())

        const starts = events.filter(e => e.type === 'action_start')
        const ends = events.filter(e => e.type === 'action_end')

        expect(starts.length).toBe(1)
        expect(ends.length).toBe(1)
        expect((ends[0] as any).action.content).toContain('console.log(1)')
    })

    it('handles content with special characters (no JSON escaping needed)', () => {
        const content = `def main():\n    print("hello, 'world'")\n    x = {"key": "value"}\n    # "special" chars: \\n \\t \\"`
        const events = feedAll([
            `<pigtex_write path="main.py">${content}</pigtex_write>`
        ])

        const actionEnd = events.find(e => e.type === 'action_end')
        expect(actionEnd).toBeDefined()
        expect((actionEnd as any).action.content).toBe(content)
    })

    it('handles multiple write tags in sequence', () => {
        const events = feedAll([
            '<pigtex_write path="a.txt">alpha</pigtex_write>',
            '<pigtex_write path="b.txt">beta</pigtex_write>'
        ])

        const ends = events.filter(e => e.type === 'action_end')
        expect(ends.length).toBe(2)
        expect((ends[0] as any).action.path).toBe('a.txt')
        expect((ends[0] as any).action.content).toBe('alpha')
        expect((ends[1] as any).action.path).toBe('b.txt')
        expect((ends[1] as any).action.content).toBe('beta')
    })

    it('handles stream ending mid-tag (graceful flush)', () => {
        const events: StreamingParserEvent[] = []
        events.push(...parser.feed('<pigtex_write path="partial.txt">some content'))
        // Stream ends without closing tag
        events.push(...parser.flush())

        const ends = events.filter(e => e.type === 'action_end')
        expect(ends.length).toBe(1)
        expect((ends[0] as any).action.content).toContain('some content')
    })

    it('emits isInsideContentTag correctly', () => {
        expect(parser.isInsideContentTag()).toBe(false)
        parser.feed('<pigtex_write path="x.txt">')
        expect(parser.isInsideContentTag()).toBe(true)
        parser.feed('stuff</pigtex_write>')
        expect(parser.isInsideContentTag()).toBe(false)
    })

    it('reset clears all state', () => {
        parser.feed('<pigtex_write path="x.txt">content')
        expect(parser.isInsideContentTag()).toBe(true)
        parser.reset()
        expect(parser.isInsideContentTag()).toBe(false)
        expect(parser.getCurrentAction()).toBeNull()
    })
})
