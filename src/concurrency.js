export const DEFAULT_READ_CONCURRENCY = 2;
export const MAX_READ_CONCURRENCY = 20;
export const DEFAULT_WRITE_CONCURRENCY = 2;
export const MAX_WRITE_CONCURRENCY = 700;

export function normalizeConcurrency(value, fallback = DEFAULT_READ_CONCURRENCY) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(MAX_READ_CONCURRENCY, Math.max(1, Math.floor(n)));
}

export function normalizeWriteConcurrency(value, fallback = DEFAULT_WRITE_CONCURRENCY) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(MAX_WRITE_CONCURRENCY, Math.max(1, Math.floor(n)));
}

export function normalizeConcurrencyWithCap(value, fallback = DEFAULT_READ_CONCURRENCY, max = MAX_READ_CONCURRENCY) {
  const n = Number(value);
  if (!Number.isFinite(n)) return Math.min(max, Math.max(1, Math.floor(Number(fallback) || DEFAULT_READ_CONCURRENCY)));
  return Math.min(max, Math.max(1, Math.floor(n)));
}

export async function mapLimitedWithCap(items, limit, max, worker) {
  const concurrency = normalizeConcurrencyWithCap(limit, DEFAULT_READ_CONCURRENCY, max);
  const results = new Array(items.length);
  let nextIndex = 0;
  let active = 0;
  let maxActive = 0;

  async function runNext() {
    const index = nextIndex;
    nextIndex += 1;
    if (index >= items.length) return;
    active += 1;
    maxActive = Math.max(maxActive, active);
    try {
      results[index] = await worker(items[index], index);
    } catch (error) {
      results[index] = { ok: false, error };
    } finally {
      active -= 1;
      await runNext();
    }
  }

  const workers = Array.from({ length: Math.min(concurrency, items.length) }, () => runNext());
  await Promise.all(workers);
  Object.defineProperty(results, 'maxActive', { value: maxActive, enumerable: false });
  return results;
}

export async function mapLimited(items, limit, worker) {
  return mapLimitedWithCap(items, limit, MAX_READ_CONCURRENCY, worker);
}
