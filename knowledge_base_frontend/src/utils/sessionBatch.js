export async function deleteSessionBatch(ids, deleteSession) {
  const successfulIds = []
  const failedIds = []
  const uniqueIds = [...new Set(ids)]
  // Bound concurrent deletes and wait for failures as well as successes.
  for (let offset = 0; offset < uniqueIds.length; offset += 4) {
    const batch = uniqueIds.slice(offset, offset + 4)
    const results = await Promise.allSettled(
      batch.map((id) => Promise.resolve().then(() => deleteSession(id))),
    )
    results.forEach((result, index) => {
      if (result.status === 'fulfilled' || result.reason?.response?.status === 404) {
        successfulIds.push(batch[index])
      } else {
        failedIds.push(batch[index])
      }
    })
  }
  return { successfulIds, failedIds }
}
