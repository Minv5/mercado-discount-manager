export const PHYSICAL_GET_BUDGET_EXHAUSTED = 'PHYSICAL_GET_BUDGET_EXHAUSTED';

function safeLimit(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return 0;
  return Math.max(0, Math.floor(parsed));
}

export function createPhysicalGetBudget(limit = 0) {
  const safeBudget = safeLimit(limit);
  let nextPermitId = 1;
  let used = 0;
  let reserved = 0;
  let issued = 0;
  let completed = 0;
  let released = 0;
  let activeReserved = 0;
  let activeIssued = 0;
  const permits = new Map();

  const stats = () => ({
    physical_budget: safeBudget,
    physical_get_used: completed,
    reserved,
    issued,
    completed,
    released,
    active_reserved: activeReserved,
    active_issued: activeIssued,
    available: Math.max(0, safeBudget - used),
  });

  const reserve = (kind = 'marketplace_item') => {
    if (used >= safeBudget) return null;
    const permit = { id: nextPermitId++, kind: String(kind || 'marketplace_item'), state: 'reserved' };
    permits.set(permit.id, permit);
    used += 1;
    reserved += 1;
    activeReserved += 1;
    return permit;
  };

  const issue = (permit) => {
    if (!permit || permits.get(permit.id) !== permit || permit.state !== 'reserved') return false;
    permit.state = 'issued';
    activeReserved = Math.max(0, activeReserved - 1);
    activeIssued += 1;
    issued += 1;
    return true;
  };

  const complete = (permit) => {
    if (!permit || permits.get(permit.id) !== permit || permit.state !== 'issued') return false;
    permit.state = 'completed';
    activeIssued = Math.max(0, activeIssued - 1);
    completed += 1;
    permits.delete(permit.id);
    return true;
  };

  const release = (permit) => {
    if (!permit || permits.get(permit.id) !== permit || permit.state !== 'reserved') return false;
    permit.state = 'released';
    activeReserved = Math.max(0, activeReserved - 1);
    used = Math.max(0, used - 1);
    released += 1;
    permits.delete(permit.id);
    return true;
  };

  const exhaustedError = (kind = 'marketplace_item') => {
    const error = new Error('本次物理读取预算已用尽，未发出新的平台读取。');
    error.code = PHYSICAL_GET_BUDGET_EXHAUSTED;
    error.cause_code = PHYSICAL_GET_BUDGET_EXHAUSTED;
    error.error_kind = 'budget';
    error.budget_remaining = true;
    error.physical_get_count = 0;
    error.operation = String(kind || 'marketplace_item');
    error.budget = stats();
    return error;
  };

  return Object.freeze({
    limit: safeBudget,
    reserve,
    issue,
    complete,
    release,
    stats,
    exhaustedError,
  });
}

export async function readMarketplaceItemWithBudget({
  client,
  itemId,
  signal = null,
  physicalGetBudget = null,
  kind = 'marketplace_item',
} = {}) {
  if (!client || typeof client.getMarketplaceItem !== 'function') {
    throw new TypeError('marketplace item client is required');
  }
  if (!physicalGetBudget || typeof physicalGetBudget.reserve !== 'function') {
    return client.getMarketplaceItem(itemId, { signal });
  }
  const permit = physicalGetBudget.reserve(kind);
  if (!permit) throw physicalGetBudget.exhaustedError(kind);
  physicalGetBudget.issue(permit);
  try {
    const result = await client.getMarketplaceItem(itemId, { signal, physicalGetPermit: permit });
    physicalGetBudget.complete(permit);
    return result;
  } catch (error) {
    // Once issued, even a failed/aborted request consumed one physical GET.
    physicalGetBudget.complete(permit);
    throw error;
  }
}
