import fs from 'node:fs';
import path from 'node:path';

export const ITEM_SNAPSHOT_BASELINE_SCOPE = 'activity_relations_v1';

export function readItemSnapshotBaselineCheckpoint(checkpointPath) {
  try {
    const value = JSON.parse(fs.readFileSync(checkpointPath, 'utf8'));
    if (value?.scope !== ITEM_SNAPSHOT_BASELINE_SCOPE || value?.status !== 'complete') return null;
    return {
      scope: ITEM_SNAPSHOT_BASELINE_SCOPE,
      status: 'complete',
      completed_at: String(value.completed_at || ''),
      relation_count: Math.max(0, Number(value.relation_count || 0)),
      refreshed_count: Math.max(0, Number(value.refreshed_count || 0)),
    };
  } catch {
    return null;
  }
}

export function writeItemSnapshotBaselineCheckpoint(checkpointPath, value = {}) {
  const directory = path.dirname(checkpointPath);
  fs.mkdirSync(directory, { recursive: true });
  const temporary = `${checkpointPath}.${process.pid}.tmp`;
  const payload = {
    scope: ITEM_SNAPSHOT_BASELINE_SCOPE,
    status: 'complete',
    completed_at: String(value.completed_at || new Date().toISOString()),
    relation_count: Math.max(0, Number(value.relation_count || 0)),
    refreshed_count: Math.max(0, Number(value.refreshed_count || 0)),
  };
  try {
    fs.writeFileSync(temporary, JSON.stringify(payload), 'utf8');
    fs.renameSync(temporary, checkpointPath);
  } finally {
    try { if (fs.existsSync(temporary)) fs.unlinkSync(temporary); } catch {}
  }
  return payload;
}
