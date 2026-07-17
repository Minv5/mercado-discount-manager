import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

import { compressExecutionAudits } from './executionAudit.js';
import { calibrateLegacyOrphanTasks, dropLegacyHistoryCache, previewLegacyOrphanTasks } from './maintenance.js';
import { applyCleanupPlan, buildCleanupPreview } from './productCleanup.js';

function option(name, fallback = '') {
  const prefix = `--${name}=`;
  const value = process.argv.find((entry) => entry.startsWith(prefix));
  return value ? value.slice(prefix.length) : fallback;
}

function flag(name) {
  return process.argv.includes(`--${name}`);
}

function required(name) {
  const value = option(name);
  if (!value) throw new Error(`missing --${name}`);
  return path.resolve(value);
}

function output(value) {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

const command = process.argv[2] || '';
if (command === 'cleanup') {
  const plan = buildCleanupPreview({
    projectRoot: required('project-root'),
    localAppData: required('local-app-data'),
    currentDist: required('current-dist'),
    currentInstall: required('current-install'),
    includeLegacyWinForms: flag('include-legacy-winforms'),
  });
  output(flag('confirm') ? applyCleanupPlan(plan, { confirm: true }) : plan);
} else if (command === 'compress-audits') {
  output(compressExecutionAudits({
    eventDir: required('event-dir'),
    jobStateDir: required('job-state-dir'),
    groupStateDir: required('group-state-dir'),
    olderThanMs: Number(option('older-than-ms', String(30 * 24 * 60 * 60 * 1000))),
    confirm: flag('confirm'),
  }));
} else if (command === 'drop-legacy-history-cache' || command === 'calibrate-orphans') {
  const dbPath = required('db');
  const database = new DatabaseSync(dbPath, { readOnly: !flag('confirm') });
  try {
    if (command === 'drop-legacy-history-cache') {
      output(dropLegacyHistoryCache(database, { confirm: flag('confirm') }));
    } else {
      const options = {
        safeAgeMs: Number(option('safe-age-ms', String(24 * 60 * 60 * 1000))),
        groupStateDir: required('group-state-dir'),
        jobStateDir: required('job-state-dir'),
      };
      if (!flag('confirm')) {
        output(previewLegacyOrphanTasks(database, options));
      } else {
        const result = calibrateLegacyOrphanTasks(database, { ...options, confirm: true });
        database.close();
        if (result.updated) {
          process.env.MDM_DB_PATH = dbPath;
          process.env.MDM_DATA_DIR = path.dirname(dbPath);
          const repository = await import('./repository.js');
          const databaseModule = await import('./db.js');
          result.history_materialization = result.task_ids.map((taskId) => repository.publishHistorySummaryForTask(taskId));
          databaseModule.closeDb();
        }
        output(result);
      }
    }
  } finally {
    try { database.close(); } catch {}
  }
} else {
  throw new Error('command must be cleanup, compress-audits, drop-legacy-history-cache, or calibrate-orphans');
}
