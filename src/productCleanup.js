import fs from 'node:fs';
import path from 'node:path';

function treeBytes(target) {
  if (!fs.existsSync(target)) return 0;
  const stat = fs.statSync(target);
  if (stat.isFile()) return stat.size;
  return fs.readdirSync(target, { withFileTypes: true }).reduce((sum, entry) => sum + treeBytes(path.join(target, entry.name)), 0);
}

function children(directory, filter = () => true) {
  if (!fs.existsSync(directory)) return [];
  return fs.readdirSync(directory, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && filter(entry.name))
    .map((entry) => path.join(directory, entry.name))
    .sort((left, right) => fs.statSync(right).mtimeMs - fs.statSync(left).mtimeMs);
}

function item(category, target, decision, allowedRoot, reason) {
  return { category, path: path.resolve(target), decision, allowed_root: path.resolve(allowedRoot), bytes: treeBytes(target), reason };
}

function addExisting(items, value) {
  if (fs.existsSync(value.path)) items.push(value);
}

function validationOutcome(directory) {
  try {
    const text = fs.readFileSync(path.join(directory, 'summary.json'), 'utf8').replace(/^\uFEFF/, '');
    const summary = JSON.parse(text);
    return String(summary?.overall || '').toUpperCase();
  } catch {
    return '';
  }
}

export function buildCleanupPreview({ projectRoot, localAppData, currentDist, currentInstall, includeLegacyWinForms = false } = {}) {
  const project = path.resolve(projectRoot);
  const local = path.resolve(localAppData);
  const items = [];
  addExisting(items, item('current_dist', currentDist, 'retain', project, '当前候选包'));
  addExisting(items, item('current_install', currentInstall, 'blocked', local, '当前正式安装禁止清理'));

  const projectBackups = children(path.join(project, 'release-backups'));
  const databaseBackups = projectBackups.filter((target) => /history-summary-migration|database|sqlite/i.test(path.basename(target)));
  projectBackups.forEach((target) => {
    const databaseIndex = databaseBackups.indexOf(target);
    const isDatabaseBackup = databaseIndex >= 0;
    items.push(item(
      isDatabaseBackup ? 'db_migration_backup' : 'project_release_backup',
      target,
      isDatabaseBackup && databaseIndex === 0 ? 'retain' : 'delete',
      project,
      isDatabaseBackup && databaseIndex === 0 ? '保留最近一个数据库迁移回退' : '旧项目发布回退',
    ));
  });
  const programRoot = path.join(local, 'Programs');
  const appBackups = children(programRoot, (name) => name.startsWith('MercadoDiscountManagerPySide.backup-'));
  appBackups.forEach((target, index) => items.push(item('app_program_backup', target, index === 0 ? 'retain' : 'delete', programRoot, index === 0 ? '保留最近一个程序回退' : '旧程序回退')));

  for (const [category, target] of [
    ['dist_full', path.join(project, 'dist-full')],
    ['pyside_build', path.join(project, 'desktop-pyside', 'build-release')],
    ['pyside_runtime_staging', path.join(project, 'desktop-pyside', 'runtime-staging')],
    ['pyside_install_validation', path.join(project, 'desktop-pyside', 'install-validation')],
    ['temporary_decompile', path.join(project, 'tmp-decompile-program')],
  ]) addExisting(items, item(category, target, 'delete', project, '可再生成的中间产物'));

  const validationRuns = children(path.join(project, 'data', 'validation-evidence'));
  validationRuns.forEach((target, index) => {
    const outcome = validationOutcome(target);
    const retain = index === 0 || outcome !== 'PASS';
    items.push(item(
      'validation_evidence_run',
      target,
      retain ? 'retain' : 'delete',
      project,
      index === 0 ? '保留最新验证证据' : (outcome === 'PASS' ? '旧的已通过验证证据' : '失败或无法确认的验证证据必须保留'),
    ));
  });

  for (const directory of [project, path.join(project, 'desktop-pyside')]) {
    if (!fs.existsSync(directory)) continue;
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (!entry.isFile() || !/^(?:tmp-.+\.png|.+-(?:visual|final|smoke).+\.png)$/i.test(entry.name)) continue;
      items.push(item('temporary_visual', path.join(directory, entry.name), 'delete', project, '临时截图或视觉验证产物'));
    }
  }

  const legacy = path.join(programRoot, 'MercadoDiscountManager');
  addExisting(items, item('legacy_winforms', legacy, includeLegacyWinForms ? 'delete' : 'blocked', programRoot, includeLegacyWinForms ? '已单独允许清理旧WinForms' : '旧WinForms需要独立开关'));
  return {
    mode: 'preview',
    items,
    totals: items.reduce((summary, row) => {
      summary[row.decision] = (summary[row.decision] || 0) + 1;
      summary[`${row.decision}_bytes`] = (summary[`${row.decision}_bytes`] || 0) + row.bytes;
      return summary;
    }, {}),
  };
}

function assertWithin(target, allowedRoot) {
  const relative = path.relative(path.resolve(allowedRoot), path.resolve(target));
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error(`cleanup target is outside allowed root: ${target}`);
  }
}

export function applyCleanupPlan(plan, { confirm = false } = {}) {
  if (!confirm) return { mode: 'preview', deleted: [], retained: plan.items || [] };
  const deleted = [];
  for (const row of plan.items || []) {
    if (row.decision !== 'delete') continue;
    assertWithin(row.path, row.allowed_root);
    if (fs.existsSync(row.path)) fs.rmSync(row.path, { recursive: true, force: true });
    deleted.push({ category: row.category, path: row.path, bytes: row.bytes });
  }
  return { mode: 'confirm', deleted, retained: (plan.items || []).filter((row) => row.decision !== 'delete') };
}
