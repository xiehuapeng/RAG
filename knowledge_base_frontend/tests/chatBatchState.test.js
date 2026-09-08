import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import { compileScript, parse } from 'vue/compiler-sfc'
import { deleteSessionBatch } from '../src/utils/sessionBatch.js'

// Execute the real setup handlers with IO mocked, without requiring a browser DOM.
const source = readFileSync(new URL('../src/pages/ChatPage.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
const compiled = compileScript(descriptor, { id: 'batch-state-test' })
let setup = descriptor.scriptSetup.content
for (const node of [...compiled.scriptSetupAst].reverse()) {
  if (node.type === 'ImportDeclaration') setup = setup.slice(0, node.start) + setup.slice(node.end)
}

function harness({ failIds = [], refreshFails = false, cancel = false } = {}) {
  const calls = { deleted: [], refreshed: 0, confirmed: 0, warnings: [] }
  const state = vm.createContext({
    ref: (value) => ({ value }),
    computed: (fn) => ({ get value() { return fn() } }),
    onMounted: () => {}, nextTick: async () => {},
    deleteSessionBatch,
    ElMessageBox: { confirm: async () => {
      calls.confirmed++
      if (cancel) throw new Error('cancel')
    } },
    ElMessage: { warning: (message) => calls.warnings.push(message), success: () => {} },
    chatApi: {
      deleteSession: async (id) => {
        if (failIds.includes(id)) throw new Error('injected')
        calls.deleted.push(id)
      },
      sessions: async () => {
        calls.refreshed++
        if (refreshFails) throw new Error('refresh failed')
        return [1, 2].filter((id) => !calls.deleted.includes(id)).map((id) => ({ id }))
      },
    },
  })
  vm.runInContext(`${setup}\n globalThis.subject = {
    sessions, selectedSessionIds, sessionId, messages, references, batchManageMode,
    batchDeleting, sending, deleteSelectedSessions, toggleSessionSelection,
  }`, state)
  const subject = state.subject
  subject.sessions.value = [{ id: 1 }, { id: 2 }]
  subject.selectedSessionIds.value = [1, 2]
  subject.sessionId.value = 1
  subject.messages.value = [{ content: 'existing answer' }]
  subject.batchManageMode.value = true
  return { subject, calls }
}

test('partial deletion clears deleted active chat, refreshes, and retains failed selection', async () => {
  const { subject, calls } = harness({ failIds: [2] })
  await subject.deleteSelectedSessions()
  assert.equal(subject.sessionId.value, null)
  assert.equal(subject.messages.value.length, 0)
  assert.deepEqual(Array.from(subject.selectedSessionIds.value), [2])
  assert.equal(subject.batchManageMode.value, true)
  assert.equal(subject.batchDeleting.value, false)
  assert.equal(calls.refreshed, 1)
})

test('failed current chat remains visible', async () => {
  const { subject } = harness({ failIds: [1] })
  await subject.deleteSelectedSessions()
  assert.equal(subject.sessionId.value, 1)
  assert.equal(subject.messages.value.length, 1)
  assert.deepEqual(Array.from(subject.selectedSessionIds.value), [1])
})

test('refresh failure still removes confirmed deletions locally', async () => {
  const { subject, calls } = harness({ failIds: [2], refreshFails: true })
  await subject.deleteSelectedSessions()
  assert.deepEqual(Array.from(subject.sessions.value, (item) => item.id), [2])
  assert.equal(subject.sessionId.value, null)
  assert.equal(calls.warnings.length, 2)
  assert.equal(subject.batchDeleting.value, false)
})

test('cancellation sends no requests and releases busy state', async () => {
  const { subject, calls } = harness({ cancel: true })
  await subject.deleteSelectedSessions()
  assert.equal(calls.deleted.length, 0)
  assert.equal(calls.refreshed, 0)
  assert.equal(subject.batchDeleting.value, false)
  assert.equal(subject.sessionId.value, 1)
})

test('busy state prevents duplicate confirmation and selection changes', async () => {
  const { subject, calls } = harness()
  const first = subject.deleteSelectedSessions()
  subject.toggleSessionSelection(1)
  await subject.deleteSelectedSessions()
  assert.deepEqual(Array.from(subject.selectedSessionIds.value), [1, 2])
  await first
  assert.equal(calls.confirmed, 1)
  assert.deepEqual(calls.deleted, [1, 2])
  assert.equal(subject.batchManageMode.value, false)
})
