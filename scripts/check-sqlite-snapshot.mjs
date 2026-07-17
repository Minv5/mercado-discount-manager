import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

const dataDirArg = process.argv.find((value) => value.startsWith('--data-dir='));
const dataDir = path.resolve(dataDirArg ? dataDirArg.slice('--data-dir='.length) : '');
const dbPath = path.join(dataDir, 'discount-manager.sqlite');
const db = new DatabaseSync(dbPath, { readOnly: true });

try {
  const integrityRow = db.prepare('PRAGMA integrity_check').get();
  const integrity = String(integrityRow?.integrity_check || '').toLowerCase();
  const taskCount = Number(db.prepare('SELECT COUNT(*) AS count FROM promo_tasks').get()?.count || 0);
  const resultCount = Number(db.prepare('SELECT COUNT(*) AS count FROM promo_action_results').get()?.count || 0);
  console.log(JSON.stringify({ integrity, task_count: taskCount, result_count: resultCount }));
} finally {
  db.close();
}
