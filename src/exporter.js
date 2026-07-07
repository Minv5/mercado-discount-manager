import fs from 'node:fs';
import path from 'node:path';

export function exportWorkspace({ outputDir, accounts = [], sites = [], activities = [], results = [], preview = null, precheck = null }) {
  fs.mkdirSync(outputDir, { recursive: true });
  const stamp = timestampForFile();
  const files = [];
  files.push(writeJson(outputDir, `shops-sites-${stamp}.json`, { accounts, sites }));
  files.push(writeCsv(outputDir, `shops-sites-${stamp}.csv`, flattenSites(accounts, sites)));
  files.push(writeJson(outputDir, `activities-${stamp}.json`, activities));
  files.push(writeCsv(outputDir, `activities-${stamp}.csv`, activities.map(activityRow)));
  files.push(writeJson(outputDir, `history-${stamp}.json`, results));
  files.push(writeCsv(outputDir, `history-${stamp}.csv`, results.map(historyRow)));
  if (preview) {
    files.push(writeJson(outputDir, `preview-tasks-${stamp}.json`, preview));
    files.push(writeCsv(outputDir, `preview-tasks-${stamp}.csv`, previewRows(preview)));
  }
  if (precheck) {
    files.push(writeJson(outputDir, `precheck-results-${stamp}.json`, precheck));
    files.push(writeCsv(outputDir, `precheck-results-${stamp}.csv`, precheckRows(precheck)));
  }
  return { outputDir, files };
}

function writeJson(dir, fileName, data) {
  const fullPath = path.join(dir, fileName);
  fs.writeFileSync(fullPath, JSON.stringify(data, null, 2), 'utf8');
  return fullPath;
}

function writeCsv(dir, fileName, rows) {
  const fullPath = path.join(dir, fileName);
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))];
  const lines = [columns.join(',')];
  for (const row of rows) lines.push(columns.map((column) => csvCell(row[column])).join(','));
  fs.writeFileSync(fullPath, `\uFEFF${lines.join('\n')}`, 'utf8');
  return fullPath;
}

function flattenSites(accounts, sites) {
  if (!sites.length) {
    return accounts.map((account) => ({
      account_id: account.account_id,
      display_name: account.display_name,
      site_id: account.site_id,
      child_user_id: '',
      logistic_type: '',
      activities: 0
    }));
  }
  return sites.map((site) => ({
    account_id: accounts[0]?.account_id || '',
    display_name: accounts[0]?.display_name || '',
    site_id: site.site_id || '',
    child_user_id: site.child_user_id || '',
    logistic_type: site.logistic_type || '',
    activities: site.total || 0,
    by_type: JSON.stringify(site.by_type || {}),
    by_status: JSON.stringify(site.by_status || {})
  }));
}

function activityRow(row) {
  return {
    account_id: row.account_id,
    merchant_id: row.merchant_id,
    child_user_id: row.child_user_id,
    site_id: row.site_id,
    logistic_type: row.logistic_type,
    promotion_id: row.promotion_id,
    promotion_type: row.promotion_type,
    name: row.name,
    status: row.status,
    start_date: row.start_date,
    finish_date: row.finish_date,
    updated_at: row.updated_at
  };
}

function historyRow(row) {
  return {
    created_at: row.created_at,
    account_id: row.account_id,
    promotion_id: row.promotion_id,
    promotion_type: row.promotion_type,
    item_id: row.item_id,
    action: row.action,
    mode: row.mode,
    status: row.status,
    deal_price: row.deal_price,
    error_cn: row.error_cn
  };
}

function previewRows(preview) {
  const plans = preview.batch?.plans || preview.plans || [];
  return plans.flatMap(({ promotion, plan, blocked, warning }) => {
    if (!plan?.rows?.length) {
      return [{
        site_id: promotion?.site_id,
        promotion_id: promotion?.promotion_id,
        promotion_type: promotion?.promotion_type,
        status: blocked ? 'blocked' : 'empty',
        reason: warning || ''
      }];
    }
    return plan.rows.map((row) => ({
      site_id: promotion?.site_id,
      promotion_id: promotion?.promotion_id,
      promotion_type: promotion?.promotion_type,
      item_id: row.item?.item_id,
      status: row.status,
      deal_price: row.deal_price,
      reason: row.reason
    }));
  });
}

function precheckRows(precheck) {
  const pkg = precheck.confirmation_package || precheck;
  if (pkg.promotions?.length) {
    return pkg.promotions.map((row) => ({
      site_id: row.site_id,
      promotion_id: row.promotion_id,
      promotion_type: row.promotion_type,
      promotion_name: row.promotion_name,
      planned: row.planned,
      skipped: row.skipped,
      blocked: row.blocked,
      status: row.status,
      blocking_reasons: (row.blocking_reasons || []).join(' | ')
    }));
  }
  return (pkg.sample_items || []).map((row) => ({
    promotion_id: pkg.promotion_id,
    promotion_type: pkg.promotion_type,
    item_id: row.item_id,
    status: row.plan_status,
    target_deal_price: row.target_deal_price,
    reason: row.skip_or_error_reason
  }));
}

function csvCell(value) {
  const text = value === null || value === undefined ? '' : String(value);
  return `"${text.replace(/"/g, '""')}"`;
}

function timestampForFile() {
  return new Date().toISOString().replace(/[:.]/g, '-');
}
