import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

import { BalancedReadScheduler } from '../src/balancedReadScheduler.js';
import { mapLimited } from '../src/concurrency.js';

const args = Object.fromEntries(process.argv.slice(2).map((entry) => {
  const [key, ...rest] = entry.replace(/^--/, '').split('=');
  return [key, rest.join('=')];
}));
const sourceDb = path.resolve(args['source-db'] || 'data/discount-manager.sqlite');
const workDir = path.resolve(args['work-dir'] || 'data/validation-evidence/prepare-balanced-benchmark');
const outputPath = path.resolve(args.output || path.join(workDir, 'benchmark.json'));
fs.mkdirSync(workDir, { recursive: true });
const copyPath = path.join(workDir, 'formal-db-copy.sqlite');
fs.rmSync(copyPath, { force: true });

const source = new DatabaseSync(sourceDb, { readOnly: true });
source.exec(`VACUUM INTO '${copyPath.replaceAll("'", "''")}'`);
source.close();
const copy = new DatabaseSync(copyPath, { readOnly: true });
const integrity = String(copy.prepare('PRAGMA integrity_check').get()?.integrity_check || 'unknown');
const counts = copy.prepare(`
  SELECT account_id, COUNT(*) AS count
  FROM promo_campaigns
  WHERE UPPER(promotion_type) IN ('SELLER_CAMPAIGN', 'DEAL')
  GROUP BY account_id
  ORDER BY account_id
`).all().map((row) => ({ account_id: String(row.account_id), count: Number(row.count || 0) }));
copy.close();

const delayProfileMs = [4, 5, 6, 8, 12, 16];
const tasks = counts.flatMap((row) => Array.from({ length: row.count }, (_, index) => ({
  accountId: row.account_id,
  key: `${row.account_id}|activity-${index}`,
  delays: [delayProfileMs[index % delayProfileMs.length], delayProfileMs[(index + 2) % delayProfileMs.length]],
})));
if (!tasks.length) throw new Error('No ordinary promotion rows were available in the database copy.');

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function runLegacy(input) {
  let active = 0;
  let peak = 0;
  const startedAt = performance.now();
  for (const accountId of [...new Set(input.map((task) => task.accountId))]) {
    const scoped = input.filter((task) => task.accountId === accountId);
    await mapLimited(scoped, 20, async (task) => {
      for (const baseDelay of task.delays) {
        active += 1;
        peak = Math.max(peak, active);
        const overloadPenalty = active > 10 ? 50 : 0;
        await wait(baseDelay + overloadPenalty);
        active -= 1;
      }
    });
  }
  return { duration_ms: Math.round(performance.now() - startedAt), peak };
}

async function runBalanced(input) {
  const scheduler = new BalancedReadScheduler({ initialLimit: 6, maxLimit: 10, successesPerIncrease: 8 });
  const startedAt = performance.now();
  await Promise.all(input.map(async (task) => {
    for (const [index, delay] of task.delays.entries()) {
      await scheduler.schedule({ accountId: task.accountId, key: `${task.key}|${index}` }, () => wait(delay));
    }
  }));
  return { duration_ms: Math.round(performance.now() - startedAt), ...scheduler.snapshot() };
}

const legacyPrepare = await runLegacy(tasks);
const balancedPrepare = await runBalanced(tasks);
const changedTasks = tasks.filter((_task, index) => index % 3 !== 2);
const legacyRevalidate = await runLegacy(tasks);
const balancedRevalidate = await runBalanced(changedTasks);
const reduction = (before, after) => Number(((before - after) / Math.max(1, before) * 100).toFixed(1));
const result = {
  generated_at: new Date().toISOString(),
  source: { database_copy: copyPath, integrity, account_activity_counts: counts, relation_inputs: tasks.length },
  fake_profile: {
    request_delays_ms: delayProfileMs,
    requests_per_activity: 2,
    legacy_account_outer_serial: true,
    legacy_concurrency: 20,
    simulated_overload_threshold: 10,
    simulated_overload_penalty_ms: 50,
    balanced_limits: { initial_global: 6, max_global: 10, per_account: 4 },
    final_revalidate_changed_activities: changedTasks.length,
  },
  prepare: { legacy: legacyPrepare, balanced: balancedPrepare, long_tail_reduction_percent: reduction(legacyPrepare.duration_ms, balancedPrepare.duration_ms) },
  final_revalidate: { legacy: legacyRevalidate, balanced: balancedRevalidate, long_tail_reduction_percent: reduction(legacyRevalidate.duration_ms, balancedRevalidate.duration_ms) },
  external_requests: 0,
};
fs.writeFileSync(outputPath, JSON.stringify(result, null, 2), 'utf8');
console.log(JSON.stringify(result));
