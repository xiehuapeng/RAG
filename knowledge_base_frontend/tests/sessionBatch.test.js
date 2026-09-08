import assert from 'node:assert/strict'
import test from 'node:test'
import { deleteSessionBatch } from '../src/utils/sessionBatch.js'

test('waits for every request and keeps failed IDs for retry', async () => {
  const finished = []
  const result = await deleteSessionBatch([1, 2, 3], async (id) => {
    if (id === 2) throw new Error('failed')
    await new Promise((resolve) => setTimeout(resolve, 5))
    finished.push(id)
  })
  assert.deepEqual(result, { successfulIds: [1, 3], failedIds: [2] })
  assert.deepEqual(finished, [1, 3])
})

test('deduplicates IDs and treats already deleted sessions as success', async () => {
  const requested = []
  const result = await deleteSessionBatch([1, 1, 2], (id) => {
    requested.push(id)
    if (id === 2) throw { response: { status: 404 } }
  })
  assert.deepEqual(requested, [1, 2])
  assert.deepEqual(result, { successfulIds: [1, 2], failedIds: [] })
})

test('bounds concurrency and handles all failures without rejecting the batch', async () => {
  let active = 0
  let peak = 0
  const ids = Array.from({ length: 11 }, (_, index) => index)
  const result = await deleteSessionBatch(ids, async () => {
    active += 1
    peak = Math.max(peak, active)
    await new Promise((resolve) => setTimeout(resolve, 2))
    active -= 1
    throw new Error('unavailable')
  })
  assert.equal(peak, 4)
  assert.equal(active, 0)
  assert.deepEqual(result, { successfulIds: [], failedIds: ids })
})

test('empty selection does not send requests', async () => {
  assert.deepEqual(await deleteSessionBatch([], () => assert.fail()), { successfulIds: [], failedIds: [] })
})
