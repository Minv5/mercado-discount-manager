import fs from 'node:fs';
import path from 'node:path';
import { performance } from 'node:perf_hooks';

const dataDirArg = process.argv.find((value) => value.startsWith('--data-dir='));
const dataDir = path.resolve(dataDirArg ? dataDirArg.slice('--data-dir='.length) : process.env.MDM_DATA_DIR || '');
if (!dataDir || !fs.existsSync(path.join(dataDir, 'discount-manager.sqlite'))) {
  throw new Error('历史摘要迁移缺少有效数据目录。');
}
process.env.MDM_DATA_DIR = dataDir;

const repository = await import('../src/repository.js');
const database = await import('../src/db.js');

const baselineStarted = performance.now();
const baseline300 = repository.buildLegacyHistoryBaseline(300);
const baseline20 = baseline300.slice(0, 20);
const baselineMs = performance.now() - baselineStarted;

const migrationStarted = performance.now();
const migration = repository.backfillHistoryBatchSummaries({ force: true });
const migrationMs = performance.now() - migrationStarted;

const read20Started = performance.now();
const materialized20 = repository.listTaskSummaries(20, { includeDetails: false });
const read20Ms = performance.now() - read20Started;
const read300Started = performance.now();
const materialized300 = repository.listTaskSummaries(300, { includeDetails: false });
const read300Ms = performance.now() - read300Started;

if (JSON.stringify(baseline20) !== JSON.stringify(materialized20)
  || JSON.stringify(baseline300) !== JSON.stringify(materialized300)) {
  throw new Error('历史摘要迁移对账失败，正式数据未通过逐字段一致性校验。');
}
const integrity = database.get('PRAGMA integrity_check');
if (String(integrity?.integrity_check || '').toLowerCase() !== 'ok') {
  throw new Error('历史摘要迁移后的 SQLite 完整性校验失败。');
}
const state = database.get('SELECT * FROM history_summary_state WHERE id = 1');
console.log(JSON.stringify({
  ok: true,
  data_dir: dataDir,
  baseline_count: baseline300.length,
  baseline_ms: Number(baselineMs.toFixed(3)),
  migration,
  migration_ms: Number(migrationMs.toFixed(3)),
  limit20_ms: Number(read20Ms.toFixed(3)),
  limit300_ms: Number(read300Ms.toFixed(3)),
  equal20: true,
  equal300: true,
  integrity: 'ok',
  state
}));
