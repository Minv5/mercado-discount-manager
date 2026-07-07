const state = {
  accounts: [],
  selectedAccountId: '',
  promotions: [],
  sites: [],
  items: [],
  selectedItems: new Set(),
  selectedPromotionId: '',
  selectedPromotionType: '',
  settings: null,
  lastPreview: null,
  lastPrecheck: null,
  lastInventoryFallbackJob: null
};

const $ = (id) => document.getElementById(id);

window.addEventListener('DOMContentLoaded', async () => {
  bindEvents();
  renderResultView({
    title: '等待结果',
    subtitle: 'dry-run、读取、兜底扫描和真实写入返回结果会在这里汇总。',
    emptyText: '尚未生成计划或执行结果。'
  });
  renderInventoryFallbackJob(null);
  await checkHealth();
  await loadSettings();
  await loadPromotionCreationStatus();
  await loadCandidateResolutionStatus();
  await loadSmartRealTestTarget();
  await refreshAccounts();
  await refreshResults();
  updateDangerButtons();
});

function bindEvents() {
  initViewNavigation();
  $('refreshAllBtn').addEventListener('click', refreshAllActivities);
  $('refreshCreationStatusBtn').addEventListener('click', loadPromotionCreationStatus);
  $('sellerCampaignCreatePrecheckBtn').addEventListener('click', createSellerCampaignPrecheck);
  $('manualCandidateImportBtn').addEventListener('click', saveManualCandidateImportDraft);
  $('smartRealTestBtn').addEventListener('click', generateSmartRealTestConfirmation);
  $('verifyAccountBtn').addEventListener('click', verifyAccount);
  $('refreshTokenBtn').addEventListener('click', refreshToken);
  $('createAuthBtn').addEventListener('click', createAuthLink);
  $('saveSettingsBtn').addEventListener('click', saveSettingsFromUi);
  $('exportWorkspaceBtn').addEventListener('click', exportWorkspace);
  $('todayDecisionBtn').addEventListener('click', todayDecision);
  $('todayPreviewBtn').addEventListener('click', todayPreview);
  $('todayPrecheckBtn').addEventListener('click', todayPrecheck);
  $('accountSelect').addEventListener('change', async () => {
    state.selectedAccountId = selectedAccount();
    await loadSitesAndPromotions();
  });
  $('uiModeSelect').addEventListener('change', applyLegacyMode);
  $('sellerActivityVisual').addEventListener('change', () => applyVisualActivityFilter('seller'));
  $('officialActivityVisual').addEventListener('change', () => applyVisualActivityFilter('official'));
  $('applyFiltersBtn').addEventListener('click', loadPromotions);
  $('siteFilter').addEventListener('change', loadPromotions);
  $('typeFilter').addEventListener('change', loadPromotions);
  $('statusFilter').addEventListener('change', loadPromotions);
  $('fetchItemsBtn').addEventListener('click', fetchSelectedItems);
  $('fetchBatchItemsBtn').addEventListener('click', fetchBatchItems);
  $('fetchFullCandidateBtn').addEventListener('click', startFullCandidateFetch);
  $('inventoryFallbackBtn').addEventListener('click', startInventoryFallbackScan);
  $('selectAllItems').addEventListener('change', toggleAllItems);
  $('actionSelect').addEventListener('change', syncStatusForAction);
  $('singlePlanBtn').addEventListener('click', createSinglePlan);
  $('batchPlanBtn').addEventListener('click', createBatchPlan);
  $('executeBtn').addEventListener('click', executeSingleReal);
  $('batchExecuteBtn').addEventListener('click', executeBatchRealPreview);
  $('cancelFilteredPreviewBtn').addEventListener('click', cancelFilteredPreview);
  $('cancelFilteredPrecheckBtn').addEventListener('click', cancelFilteredPrecheck);
  $('confirmText').addEventListener('input', updateDangerButtons);
  $('cycleDecisionBtn').addEventListener('click', cycleDecision);
  $('refreshResultsBtn').addEventListener('click', refreshResults);
  $('clearLogBtn').addEventListener('click', () => $('logBox').textContent = '');
}

function initViewNavigation() {
  document.querySelectorAll('[data-view]').forEach((button) => {
    button.addEventListener('click', () => setActiveView(button.dataset.view));
  });
}

function setActiveView(view) {
  const workspace = $('workspace');
  if (!workspace || !view) return;
  workspace.dataset.activeView = view;
  document.querySelectorAll('[data-view]').forEach((button) => {
    button.classList.toggle('active', button.dataset.view === view);
  });
}

async function loadSettings() {
  const data = await api('/api/settings');
  state.settings = data.settings;
  $('settingAuthDir').value = data.settings.authDir || '';
  $('settingOutputDir').value = data.settings.outputDir || '';
  $('settingSellerDiscount').value = data.settings.sellerDefaultDiscount ?? 5;
  $('settingOfficialDiscount').value = data.settings.officialDefaultDiscount ?? 6;
  $('settingCancelMaxRounds').value = data.settings.cancelMaxRounds ?? 5;
  $('settingMaxItems').value = data.settings.maxItemsPerPromotion ?? 50;
  $('settingReadConcurrency').value = data.settings.readConcurrency ?? 2;
  $('settingPreviewConcurrency').value = data.settings.previewConcurrency ?? 2;
  $('settingWriteConcurrency').value = data.settings.writeConcurrency ?? 2;
  syncOperationDefaults(data.settings);
  renderSettingsSummary(data.settings);
}

async function saveSettingsFromUi() {
  const payload = {
    authDir: $('settingAuthDir').value.trim(),
    outputDir: $('settingOutputDir').value.trim(),
    sellerDefaultDiscount: Number($('settingSellerDiscount').value || 5),
    officialDefaultDiscount: Number($('settingOfficialDiscount').value || 6),
    cancelMaxRounds: Number($('settingCancelMaxRounds').value || 5),
    maxItemsPerPromotion: Number($('settingMaxItems').value || 50),
    readConcurrency: Number($('settingReadConcurrency').value || 2),
    previewConcurrency: Number($('settingPreviewConcurrency').value || 2),
    writeConcurrency: Number($('settingWriteConcurrency').value || 2),
    defaultFilters: filters()
  };
  const data = await api('/api/settings', { method: 'POST', body: payload });
  state.settings = data.settings;
  $('settingReadConcurrency').value = data.settings.readConcurrency ?? 2;
  $('settingPreviewConcurrency').value = data.settings.previewConcurrency ?? 2;
  $('settingWriteConcurrency').value = data.settings.writeConcurrency ?? 2;
  syncOperationDefaults(data.settings);
  renderSettingsSummary(data.settings, '设置已保存');
  log('设置已保存；未保存任何 token、Client Secret 或密码。');
}

function syncOperationDefaults(settings) {
  $('sellerDiscount').value = settings.sellerDefaultDiscount ?? 5;
  $('officialDiscount').value = settings.officialDefaultDiscount ?? 6;
  $('maxItems').value = settings.maxItemsPerPromotion ?? 50;
}

function renderSettingsSummary(settings, prefix = '当前设置') {
  $('settingsSummary').textContent = `${prefix}：输出目录 ${settings.outputDir}；样本读取上限 ${settings.maxItemsPerPromotion}；只读并发 ${settings.readConcurrency}，预检并发 ${settings.previewConcurrency}，写入并发 ${settings.writeConcurrency}`;
}

async function loadPromotionCreationStatus() {
  const data = await api('/api/promotion-creation/status');
  const creation = data.creation;
  $('creationStatusBox').innerHTML = [
    `<strong>${esc(creation.uiLabel)}</strong>`,
    `<span>${esc(creation.summary)}</span>`,
    creation.createEndpoint ? `<span>接口：${esc(creation.createEndpoint.method)} ${esc(creation.createEndpoint.path)}；最大 ${esc(creation.createEndpoint.maxDurationDays)} 天。</span>` : '',
    `<span>${esc(creation.writeProtection)}</span>`,
    `<span>下一步：${esc((creation.nextSteps || []).join('；'))}</span>`
  ].filter(Boolean).join('');
}

async function createSellerCampaignPrecheck() {
  const payload = {
    accountId: selectedAccount(),
    siteId: $('createCampaignSiteId').value.trim(),
    childUserId: $('createCampaignChildUserId').value.trim(),
    name: $('createCampaignName').value.trim(),
    subType: $('createCampaignSubType').value,
    startDate: localDateInputToIso($('createCampaignStart').value),
    finishDate: localDateInputToIso($('createCampaignFinish').value)
  };
  const { response, data } = await apiRaw('/api/promotion-creation/precheck', { method: 'POST', body: payload });
  if (response.status === 409 && data.confirmation_package) {
    renderConfirmationPackage(data.confirmation_package);
    $('creationStatusBox').textContent = `已生成 Seller Campaign 创建预检：${data.confirmation_package.status}。请查看请求预览和阻断状态。`;
    log(`Seller Campaign 创建预检已生成：${data.confirmation_package.status}，请查看请求预览。`);
    return;
  }
  log(data.error || `创建活动预检失败：${response.status}`);
}

async function loadCandidateResolutionStatus() {
  const data = await api('/api/candidate-incomplete/status');
  renderCandidateResolution(data.resolution);
}

function renderCandidateResolution(resolution) {
  if (!resolution) return;
  $('candidateResolutionBox').innerHTML = [
    `<strong>状态：${esc(resolution.severity || 'blocking')}</strong>`,
    `<span>${esc(resolution.message || '')}</span>`,
    resolution.forbidden_fallbacks?.length ? `<span>禁止：${esc(resolution.forbidden_fallbacks.join('；'))}</span>` : '',
    `<span>安全选项：${esc((resolution.safe_options || []).map((item) => item.label).join('；'))}</span>`,
    `<span>要求：${esc((resolution.manual_import_requirements || []).join('；'))}</span>`
  ].filter(Boolean).join('');
}

async function loadSmartRealTestTarget() {
  const data = await api('/api/smart-real-test/target');
  renderSmartRealTestPackage(data.confirmation_package);
}

async function generateSmartRealTestConfirmation() {
  const data = await apiRaw('/api/smart-real-test/confirmation', {
    method: 'POST',
    body: {
      accountId: '2651442567',
      siteId: 'MLB',
      childUserId: '2668031897',
      promotionId: 'P-MLB17755282',
      promotionType: 'SMART',
      action: 'enroll',
      status: 'candidate',
      itemId: 'MLB6729392606',
      offerId: 'CANDIDATE-MLB6729392606-76453189919',
      price: 19.62,
      originalPrice: 21.76,
      sellerPercentage: 8.9,
      meliPercentage: 1,
      writeConcurrency: 1,
      confirmText: 'REAL_SUBMIT'
    }
  });
  if (data.data?.confirmation_package) {
    renderSmartRealTestPackage(data.data.confirmation_package);
    const rawText = formatSmartRealTestPackage(data.data.confirmation_package);
    renderResultView({
      title: 'SMART 单商品验证包',
      subtitle: `状态：${data.data.confirmation_package.status || '-'}`,
      cards: [
        metric('目标商品', data.data.confirmation_package.target?.item_id || '-'),
        metric('活动', data.data.confirmation_package.target?.promotion_id || '-'),
        metric('写入并发', data.data.confirmation_package.write_concurrency || '-'),
        metric('release 状态', data.data.confirmation_package.release?.release_policy?.enabled ? '已启用' : '未启用', data.data.confirmation_package.release?.release_policy?.enabled ? 'ok' : 'warn')
      ],
      rawText,
      emptyText: '固定商品验证包无活动明细表。'
    });
    log('SMART 单商品验证包已生成；请查看固定商品状态和 release 状态。');
    return;
  }
  log(data.data?.error || `SMART 验证确认包生成失败：${data.response.status}`);
}

function renderSmartRealTestPackage(pkg) {
  if (!pkg) return;
  $('smartRealTestBox').innerHTML = [
    `<strong>${esc(pkg.status || 'blocked')}</strong>`,
    `<span>账号 ${esc(pkg.target?.account_id || '-')} | 站点 ${esc(pkg.target?.site_id || '-')} | child ${esc(pkg.target?.child_user_id || '-')}</span>`,
    `<span>活动 ${esc(pkg.target?.promotion_id || '-')} | 商品 ${esc(pkg.target?.item_id || '-')}</span>`,
    `<span>offer_id：${esc(pkg.target?.offer_id || '-')}</span>`,
    `<span>写入并发 ${esc(pkg.write_concurrency || '-')} | release code：${pkg.release?.release_policy?.enabled ? '已启用' : '未启用'}</span>`,
    `<span>状态：${esc(pkg.status || '-')}</span>`
  ].join('');
}

async function checkHealth() {
  try {
    const data = await api('/api/health');
    $('serviceStatus').textContent = data.ok ? '本地服务正常' : '服务异常';
  } catch {
    $('serviceStatus').textContent = '服务未连接';
  }
}

async function refreshAccounts() {
  const data = await api('/api/accounts');
  state.accounts = data.accounts || [];
  $('accountSelect').innerHTML = state.accounts.map((account) => (
    `<option value="${esc(account.account_id)}">${esc(account.display_name || account.account_id)}</option>`
  )).join('');
  state.selectedAccountId = selectedAccount();
  renderAccount();
  await verifyAccount(false);
  await loadSitesAndPromotions();
}

async function verifyAccount(writeLog = true) {
  const accountId = selectedAccount();
  if (!accountId) return;
  const data = await api(`/api/accounts/${encodeURIComponent(accountId)}/verify`, { method: 'POST', body: {} });
  const idx = state.accounts.findIndex((account) => String(account.account_id) === String(accountId));
  if (idx >= 0) state.accounts[idx] = { ...state.accounts[idx], ...data.account };
  renderAccount();
  if (writeLog) log(`账号验证成功：${data.account.display_name || data.account.account_id}，站点 ${data.account.site_id || '-'}`);
}

function renderAccount() {
  const account = currentAccount();
  if (!account) {
    $('accountTitle').textContent = '未找到授权账号';
    $('accountMeta').textContent = '请检查 standalone 授权目录。';
    return;
  }
  $('accountTitle').textContent = account.display_name || account.account_id;
  $('accountMeta').textContent = `账号 ${account.account_id} | 站点 ${account.site_id || '-'} | token 到期 ${formatTime(account.expires_at)} | 来源 ${account.auth_source || 'standalone'}`;
}

async function refreshToken() {
  const accountId = selectedAccount();
  if (!accountId) return;
  await api(`/api/accounts/${encodeURIComponent(accountId)}/refresh`, { method: 'POST', body: {} });
  log('token 已刷新，未输出任何敏感内容。');
  await refreshAccounts();
}

async function createAuthLink() {
  const payload = {
    clientId: $('clientId').value.trim(),
    clientSecret: $('clientSecret').value,
    redirectUri: $('redirectUri').value.trim(),
    authDomain: $('authDomain').value
  };
  const data = await api('/api/oauth/start', { method: 'POST', body: payload });
  $('authLink').href = data.authorizationUrl;
  $('authLink').classList.remove('disabled');
  log('备用授权链接已生成，Client Secret 不会写入日志。');
}

async function refreshAllActivities() {
  const accountId = selectedAccount();
  if (!accountId) return;
  const data = await api(`/api/accounts/${encodeURIComponent(accountId)}/promotions/fetch`, { method: 'POST', body: {} });
  const childText = (data.children || []).map((row) => `${row.site_id}:${row.total}`).join('，');
  log(`全部站点活动刷新完成：${data.total} 个。${childText}`);
  await loadSitesAndPromotions();
  setActiveView('activities');
}

async function todayDecision() {
  const data = await api('/api/today/decision', {
    method: 'POST',
    body: { accountId: selectedAccount(), filters: filters() }
  });
  renderTodayDecision(data.decision);
  log(`判断今日：${data.decision.today_action}，${data.decision.reason}`);
}

async function todayPreview() {
  const data = await api('/api/today/preview', {
    method: 'POST',
    body: {
      accountId: selectedAccount(),
      filters: filters(),
      priceMode: $('priceMode').value,
      sellerDiscountPercent: Number($('sellerDiscount').value || 5),
      officialDiscountPercent: Number($('officialDiscount').value || 6),
      directPrice: $('directPrice').value ? Number($('directPrice').value) : null
    }
  });
  state.lastPreview = data;
  renderTodayDecision(data.decision);
  renderBatchPlan(data.batch, data.taskIds);
  log(`今日预览：${data.decision.action}，活动 ${data.batch.totals.promotions}，可执行 ${data.batch.totals.planned}，阻断 ${data.batch.totals.blocked}。`);
  await refreshResults();
}

async function todayPrecheck() {
  const confirmText = requireRealSubmitConfirmation('提交执行预检');
  if (!confirmText) return;
  const payload = {
    accountId: selectedAccount(),
    filters: filters(),
    mode: 'real',
    confirmText,
    priceMode: $('priceMode').value,
    sellerDiscountPercent: Number($('sellerDiscount').value || 5),
    officialDiscountPercent: Number($('officialDiscount').value || 6),
    writeConcurrency: Number($('settingWriteConcurrency').value || state.settings?.writeConcurrency || 2),
    directPrice: $('directPrice').value ? Number($('directPrice').value) : null
  };
  const { response, data } = await apiRaw('/api/today/precheck', { method: 'POST', body: payload });
  if (data.confirmation_package) {
    state.lastPrecheck = data;
    renderConfirmationPackage(data.confirmation_package);
    renderTodayDecision(data.today_decision);
    log(`今日真实执行检查已生成：${data.confirmation_package.status}。`);
    return;
  }
  log(data.error || data.message || `今日预检未生成确认包：HTTP ${response.status}`);
}

async function loadSitesAndPromotions() {
  await loadSites();
  await loadPromotions();
}

async function loadSites() {
  const accountId = selectedAccount();
  if (!accountId) return;
  const data = await api(`/api/accounts/${encodeURIComponent(accountId)}/sites`);
  state.sites = data.sites || [];
  renderSites();
}

async function loadPromotions() {
  const accountId = selectedAccount();
  if (!accountId) return;
  const qs = new URLSearchParams(filters()).toString();
  const data = await api(`/api/accounts/${encodeURIComponent(accountId)}/promotions${qs ? `?${qs}` : ''}`);
  state.promotions = data.promotions || [];
  renderFilterOptions();
  renderPromotions();
}

function renderSites() {
  $('siteSummaryText').textContent = state.sites.length ? `${state.sites.length} 个子账号/站点` : '暂无站点活动数据';
  $('siteGrid').innerHTML = state.sites.map((site) => `
    <div class="site-card">
      <strong>${esc(site.site_id || '-')}</strong>
      <span>子账号：${esc(site.child_user_id || '-')}</span>
      <span>物流：${esc(site.logistic_type || '-')}</span>
      <span>活动：${site.total} 个</span>
      <span>类型：${esc(summaryObj(site.by_type))}</span>
      <span>状态：${esc(summaryObj(site.by_status))}</span>
    </div>
  `).join('');
}

function renderFilterOptions() {
  const current = filters();
  const allPromos = state.promotions;
  const sites = unique([...state.sites.map((site) => site.site_id), ...allPromos.map((promo) => promo.site_id)].filter(Boolean));
  const types = unique(allPromos.map((promo) => promo.promotion_type).filter(Boolean));
  const statuses = unique(allPromos.map((promo) => promo.status).filter(Boolean));
  fillSelect('siteFilter', '全部站点', sites, current.siteId);
  fillSelect('typeFilter', '全部类型', types, current.promotionType);
  fillSelect('statusFilter', '全部状态', statuses, current.status);
  fillVisualActivitySelects(allPromos);
}

function fillVisualActivitySelects(promotions) {
  const seller = promotions.filter((promo) => String(promo.promotion_type || '').toUpperCase() === 'SELLER_CAMPAIGN');
  const official = promotions.filter((promo) => String(promo.promotion_type || '').toUpperCase() !== 'SELLER_CAMPAIGN');
  fillActivityVisualSelect('sellerActivityVisual', '全部自建活动', seller);
  fillActivityVisualSelect('officialActivityVisual', '全部官方活动', official);
}

function fillActivityVisualSelect(id, label, promotions) {
  const select = $(id);
  const current = select.value;
  select.innerHTML = `<option value="">${label}</option>` + promotions.map((promo) => (
    `<option value="${esc(promo.promotion_id)}" data-type="${esc(promo.promotion_type || '')}">${esc(promo.name || promo.promotion_id)}</option>`
  )).join('');
  select.value = [...select.options].some((option) => option.value === current) ? current : '';
}

function applyLegacyMode() {
  const mode = $('uiModeSelect').value;
  if (mode === 'auto') return;
  $('actionSelect').value = mode;
  syncStatusForAction();
}

async function applyVisualActivityFilter(kind) {
  const select = $(kind === 'seller' ? 'sellerActivityVisual' : 'officialActivityVisual');
  const peer = $(kind === 'seller' ? 'officialActivityVisual' : 'sellerActivityVisual');
  if (peer) peer.value = '';
  const option = select.selectedOptions?.[0];
  const value = select.value;
  $('nameFilter').value = value || '';
  $('typeFilter').value = value
    ? option?.dataset?.type || ''
    : kind === 'seller' ? 'SELLER_CAMPAIGN' : '';
  if (kind === 'official' && !value) $('typeFilter').value = '';
  await loadPromotions();
}

function fillSelect(id, label, values, selected) {
  const select = $(id);
  select.innerHTML = `<option value="">${label}</option>` + values.map((value) => `<option value="${esc(value)}">${esc(value)}</option>`).join('');
  select.value = selected || '';
}

function renderPromotions() {
  $('activitiesBody').innerHTML = state.promotions.map((promo) => `
    <tr class="clickable ${isSelectedPromotion(promo) ? 'selected-row' : ''}" data-promotion-id="${esc(promo.promotion_id)}" data-promotion-type="${esc(promo.promotion_type)}">
      <td>${esc(promo.site_id || '')}</td>
      <td>${esc(promo.child_user_id || '')}</td>
      <td><strong>${esc(promo.name || promo.promotion_id)}</strong><br><span class="muted">${esc(promo.promotion_id)}</span></td>
      <td>${typeCell(promo.promotion_type)}</td>
      <td>${esc(promo.status || '')}</td>
      <td>${esc(shortDate(promo.start_date))} - ${esc(shortDate(promo.finish_date))}</td>
    </tr>
  `).join('');
  document.querySelectorAll('[data-promotion-id]').forEach((row) => {
    row.addEventListener('click', () => selectPromotion(row.dataset.promotionId, row.dataset.promotionType));
  });
  updateSelectedActivityText();
}

function selectPromotion(promotionId, promotionType) {
  state.selectedPromotionId = promotionId;
  state.selectedPromotionType = promotionType;
  state.items = [];
  state.selectedItems = new Set();
  renderPromotions();
  renderItems();
  setActiveView('items');
}

async function fetchSelectedItems() {
  const promo = selectedPromotion();
  if (!promo) return log('请先选择一个活动。');
  const status = $('itemStatus').value;
  const data = await api(`/api/accounts/${encodeURIComponent(selectedAccount())}/promotions/${encodeURIComponent(promo.promotion_id)}/${encodeURIComponent(promo.promotion_type)}/items/fetch`, {
    method: 'POST',
    body: { status, maxItems: Number($('maxItems').value || 50), fetchMode: 'sample' }
  });
  state.items = data.items || [];
  state.selectedItems = new Set(state.items.map((item) => item.item_id));
  renderItems(data.total, data.saved);
  setActiveView('items');
  if (data.blocked) {
    log(`${promo.site_id || '-'} ${promo.name || promo.promotion_id} ${status}：${data.warning}`);
    $('itemSummary').textContent = `平台 ${data.total}，已拉取 ${data.saved}，状态 blocking：${data.warning}`;
    if (data.detail_status === 'api_incomplete') await loadCandidateResolutionStatus();
  } else {
    log(`${promo.site_id || '-'} ${promo.name || promo.promotion_id} ${status}：平台 ${data.total}，已拉取 ${data.saved}，${data.is_full_fetch ? '已全量' : '未全量'}${data.detail_status === 'partial_api_sparse_marketplace_candidate' ? `，可读候选子集，空页 ${data.fetch_stats?.empty_page_count ?? data.empty_page_count ?? 0}` : ''}${data.detail_status === 'empty' ? '，无商品' : ''}`);
  }
}

async function saveManualCandidateImportDraft() {
  const promo = selectedPromotion();
  if (!promo) return log('请先选择需要导入 candidate 草案的活动。');
  const text = $('manualCandidateIds').value.trim();
  if (!text) return log('请先填写 candidate item_id。');
  const data = await api(`/api/accounts/${encodeURIComponent(selectedAccount())}/promotions/${encodeURIComponent(promo.promotion_id)}/${encodeURIComponent(promo.promotion_type)}/candidate/manual-import`, {
    method: 'POST',
    body: { itemIdText: text }
  });
  state.items = data.items || [];
  state.selectedItems = new Set();
  renderItems(data.imported, data.imported);
  setActiveView('items');
  renderCandidateResolution(data.resolution);
  $('itemSummary').textContent = `已保存 ${data.imported} 个人工导入草案；需只读补齐价格明细后才可预检报名。`;
  log(`candidate 人工导入草案已保存：${data.imported} 个。本地草案不会触发真实报名。`);
}

async function fetchBatchItems() {
  const data = await api('/api/batch/items/fetch', {
    method: 'POST',
    body: {
      accountId: selectedAccount(),
      filters: filters(),
      itemStatus: $('itemStatus').value,
      fetchMode: 'sample',
      maxItems: Number($('maxItems').value || 50),
      readConcurrency: Number($('settingReadConcurrency').value || state.settings?.readConcurrency || 2)
    }
  });
  const emptyCount = data.rows.filter((row) => row.detail_status === 'empty').length;
  const blockedCount = data.rows.filter((row) => row.blocked).length;
  log(`样本读取完成：活动 ${data.promotions} 个，只读并发 ${data.readConcurrency}，空 ${emptyCount} 个，blocking ${blockedCount} 个。样本不代表平台全量。`);
  renderFetchRowsResult(data.rows, {
    title: '样本读取结果',
    subtitle: `活动 ${data.promotions} 个，只读并发 ${data.readConcurrency}；样本不代表平台全量候选。`,
    rawText: renderFetchRowsText(data.rows, '样本读取结果：样本不代表平台全量候选。')
  });
  setActiveView('results');
}

async function startFullCandidateFetch() {
  const data = await api('/api/batch/items/fetch/start', {
    method: 'POST',
    body: {
      accountId: selectedAccount(),
      filters: filters(),
      itemStatus: 'candidate',
      fetchMode: 'full',
      readConcurrency: Number($('settingReadConcurrency').value || state.settings?.readConcurrency || 2)
    }
  });
  log(`全量读取候选已启动：任务 ${data.job.id}，活动 ${data.job.progress.total_promotions || '统计中'}。`);
  renderFetchRowsResult(data.job.rows || [], {
    title: `全量读取候选任务 ${data.job.id}`,
    subtitle: `状态：${data.job.status}`,
    rawText: `全量读取候选任务 ${data.job.id} 已启动...\n状态：${data.job.status}`
  });
  setActiveView('results');
  await pollBatchFetchJob(data.job.id);
}

async function pollBatchFetchJob(jobId) {
  for (let i = 0; i < 240; i += 1) {
    const data = await api(`/api/batch/items/fetch/jobs/${encodeURIComponent(jobId)}`);
    const job = data.job;
    const rawText = [
      `全量读取候选任务 ${job.id} | ${job.status}`,
      `进度：${job.progress.completed_promotions}/${job.progress.total_promotions}，失败 ${job.progress.failed_promotions}`,
      '',
      renderFetchRowsText(job.rows || [], '已完成活动：')
    ].join('\n');
    renderFetchRowsResult(job.rows || [], {
      title: `全量读取候选任务 ${job.id}`,
      subtitle: `状态：${job.status}；进度 ${job.progress.completed_promotions}/${job.progress.total_promotions}，失败 ${job.progress.failed_promotions}`,
      rawText
    });
    if (job.status === 'completed' || job.status === 'failed') {
      log(job.status === 'completed'
        ? `全量读取候选完成：活动 ${job.progress.completed_promotions}/${job.progress.total_promotions}。`
        : `全量读取候选失败：${job.error || '未知错误'}`);
      return job;
    }
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
  log('全量读取候选仍在运行，可稍后查看任务状态。');
  return null;
}

async function startInventoryFallbackScan() {
  const data = await api('/api/inventory-fallback/seller-campaign/start', {
    method: 'POST',
    body: {
      accountId: selectedAccount(),
      filters: { ...filters(), promotionTypes: ['SELLER_CAMPAIGN'] },
      listingStatus: 'all',
      readConcurrency: Number($('settingReadConcurrency').value || state.settings?.readConcurrency || 2),
      detailConcurrency: Number($('settingReadConcurrency').value || state.settings?.readConcurrency || 2),
      sellerDiscountPercent: Number($('sellerDiscount').value || 5)
    }
  });
  state.lastInventoryFallbackJob = data.job;
  renderInventoryFallbackJob(data.job);
  setActiveView('items');
  log(`自建库存兜底扫描已启动：任务 ${data.job.id}，会扫描站点商品并排除已参加/待参加商品。`);
  await pollInventoryFallbackJob(data.job.id);
}

async function pollInventoryFallbackJob(jobId) {
  for (let i = 0; i < 360; i += 1) {
    const data = await api(`/api/inventory-fallback/seller-campaign/jobs/${encodeURIComponent(jobId)}`);
    const job = data.job;
    state.lastInventoryFallbackJob = job;
    renderInventoryFallbackJob(job);
    if (job.status === 'completed' || job.status === 'failed') {
      log(job.status === 'completed'
        ? `自建库存兜底扫描完成：活动 ${job.progress.completed_promotions}/${job.progress.total_promotions}。`
        : `自建库存兜底扫描失败：${job.error || '未知错误'}`);
      return job;
    }
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
  log('自建库存兜底扫描仍在运行，可稍后查看任务状态。');
  return null;
}

function renderInventoryFallbackJob(job) {
  const rows = job?.rows || [];
  const progress = job?.progress || { total_promotions: 0, completed_promotions: 0, failed_promotions: 0 };
  const detailFailed = sumFirstNumber(rows, ['detail_failed']);
  const cards = [
    metric('活动进度', job ? `${progress.completed_promotions}/${progress.total_promotions}` : '-'),
    metric('平台 candidate total', sumFirstNumber(rows, ['platform_total', 'total'])),
    metric('已读 candidate', sumFirstNumber(rows, ['saved_count', 'saved'])),
    metric('自建库存扫描数', `${sumFirstNumber(rows, ['scan_saved'])}/${sumFirstNumber(rows, ['scan_total'])}`),
    metric('排除已参加/待参加', sumFirstNumber(rows, ['excluded_started_pending']), 'warn'),
    metric('兜底新增', sumFirstNumber(rows, ['added_count']), 'ok'),
    metric('详情失败', detailFailed, detailFailed ? 'danger' : 'ok')
  ];
  const summary = $('inventoryFallbackSummary');
  if (summary) {
    summary.innerHTML = job
      ? cards.map(renderMetricCard).join('')
      : '<div class="empty-state">尚未运行自建库存兜底扫描。</div>';
  }
  const details = $('inventoryFallbackDetails');
  if (details) {
    if (rows.length) {
      details.className = '';
      details.innerHTML = renderDataTable(inventoryFallbackColumns(), rows, 'fallback-details-wrap');
    } else {
      details.className = 'detail-placeholder';
      details.textContent = job
        ? '任务已启动，等待扫描结果返回。'
        : '扫描后会显示每个活动的平台 candidate total、已读 candidate、自建库存扫描数、排除已参加/待参加、兜底新增和详情失败。';
    }
  }
  const rawText = job ? [
    `自建库存兜底扫描任务 ${job.id} | ${job.status}`,
    `进度：${progress.completed_promotions}/${progress.total_promotions}，失败 ${progress.failed_promotions}`,
    '',
    renderInventoryFallbackRows(rows)
  ].join('\n') : '尚未运行自建库存兜底扫描。';
  renderResultView({
    title: job ? `自建库存兜底扫描任务 ${job.id}` : '等待结果',
    subtitle: job ? `状态：${job.status}；只影响本地候选草案和后续计划口径。` : 'dry-run、读取、兜底扫描和真实写入返回结果会在这里汇总。',
    cards: job ? cards : [],
    columns: rows.length ? inventoryFallbackColumns() : [],
    rows,
    rawText,
    emptyText: job ? '任务已启动，等待扫描结果返回。' : '尚未生成计划或执行结果。'
  });
}

function inventoryFallbackColumns() {
  return [
    { label: '站点', value: (row) => row.site_id || '-' },
    { label: '活动', value: (row) => row.name || row.promotion_id || '-' },
    { label: '平台 candidate total', value: (row) => firstValue(row, ['platform_total', 'total']) },
    { label: '已读 candidate', value: (row) => firstValue(row, ['saved_count', 'saved']) },
    { label: '库存扫描数', value: (row) => `${firstValue(row, ['scan_saved'])}/${firstValue(row, ['scan_total'])}` },
    { label: '排除已参加/待参加', value: (row) => firstValue(row, ['excluded_started_pending']) },
    { label: '兜底新增', value: (row) => firstValue(row, ['added_count']) },
    { label: '详情失败', value: (row) => firstValue(row, ['detail_failed']) },
    { label: '状态', value: (row) => row.detail_status || '-' },
    { label: '备注', value: (row) => row.note || row.error || '-' }
  ];
}

function renderFetchRowsResult(rows, { title, subtitle, rawText }) {
  const blocked = rows.filter((row) => row.blocked).length;
  const empty = rows.filter((row) => row.detail_status === 'empty').length;
  const failed = rows.filter((row) => row.error || row.detail_status === 'error').length;
  renderResultView({
    title,
    subtitle,
    cards: [
      metric('活动', rows.length),
      metric('平台 total', sumFirstNumber(rows, ['platform_total', 'total'])),
      metric('已读取', sumFirstNumber(rows, ['saved_count', 'saved'])),
      metric('空活动', empty, empty ? 'warn' : 'ok'),
      metric('阻断', blocked, blocked ? 'danger' : 'ok'),
      metric('失败', failed, failed ? 'danger' : 'ok')
    ],
    columns: rows.length ? [
      { label: '站点', value: (row) => row.site_id || '-' },
      { label: '活动', value: (row) => row.name || row.promotion_id || '-' },
      { label: '商品状态', value: (row) => row.status || '-' },
      { label: '平台 total', value: (row) => firstValue(row, ['platform_total', 'total']) },
      { label: '已读取', value: (row) => firstValue(row, ['saved_count', 'saved']) },
      { label: '全量', value: (row) => row.is_full_fetch ? '是' : '否' },
      { label: '明细状态', value: (row) => row.detail_status || '-' },
      { label: '备注', value: (row) => row.note || row.warning || row.error || '-' }
    ] : [],
    rows,
    rawText,
    emptyText: '尚未返回活动明细。'
  });
}

function renderInventoryFallbackRows(rows) {
  const lines = ['库存兜底结果：'];
  lines.push(...(rows || []).map((row) => (
    `${row.site_id || '-'} ${row.name || row.promotion_id} | 扫描 ${row.scan_saved ?? '-'} / ${row.scan_total ?? '-'} | 已排除 ${row.excluded_started_pending ?? 0} | 原候选 ${row.existing_candidate_count ?? 0} | 新增兜底 ${row.added_count ?? 0} | 详情失败 ${row.detail_failed ?? 0} | ${row.detail_status} | ${row.note || ''}`
  )));
  return lines.join('\n');
}

function renderFetchRowsText(rows, header) {
  const lines = [header];
  lines.push(...(rows || []).map((row) => {
    const sparse = row.detail_status === 'partial_api_sparse_marketplace_candidate'
      ? ` | 可读子集 ${row.saved_count ?? row.saved ?? 0}，空页 ${row.empty_page_count ?? 0}，重复 ${row.duplicate_count ?? 0}，停止:${row.stop_reason || '-'}`
      : '';
    return `${row.site_id || '-'} ${row.name || row.promotion_id} | ${row.status} | 平台 ${row.platform_total ?? row.total ?? '-'} | 已读取 ${row.saved_count ?? row.saved ?? 0} | ${row.is_full_fetch ? '已全量' : '未全量'} | ${row.detail_status}${sparse} | ${row.note || ''}`;
  }));
  return lines.join('\n');
}

function renderItems(platformTotal = null, saved = null) {
  const suffix = platformTotal === null ? '' : `，平台 ${platformTotal}，已拉取 ${saved}`;
  $('itemSummary').textContent = `当前表格 ${state.items.length} 个，已选择 ${state.selectedItems.size} 个${suffix}`;
  $('itemsBody').innerHTML = state.items.map((item) => `
    <tr>
      <td><input type="checkbox" data-item="${esc(item.item_id)}" ${state.selectedItems.has(item.item_id) ? 'checked' : ''}></td>
      <td>${esc(item.item_id)}</td>
      <td>${esc(item.status || '')}</td>
      <td>${esc(item.currency_id || '')}</td>
      <td>${money(item.original_price)}</td>
      <td>${money(item.price)}</td>
      <td>${money(item.suggested_discounted_price)}</td>
      <td>${money(item.min_discounted_price)}</td>
      <td>${money(item.max_discounted_price)}</td>
    </tr>
  `).join('');
  document.querySelectorAll('[data-item]').forEach((input) => {
    input.addEventListener('change', () => {
      if (input.checked) state.selectedItems.add(input.dataset.item);
      else state.selectedItems.delete(input.dataset.item);
      $('itemSummary').textContent = `当前表格 ${state.items.length} 个，已选择 ${state.selectedItems.size} 个`;
    });
  });
}

function toggleAllItems(event) {
  state.selectedItems = event.target.checked ? new Set(state.items.map((item) => item.item_id)) : new Set();
  renderItems();
}

async function createSinglePlan() {
  const promo = selectedPromotion();
  if (!promo) return log('请先选择活动。');
  const payload = operationPayload(promo);
  const data = await api('/api/plan', { method: 'POST', body: payload });
  if (data.blocked) {
    renderResultView({
      title: '选中活动 dry-run 被阻断',
      subtitle: data.warning || data.detail_status || '',
      cards: [
        metric('可执行', 0, 'danger'),
        metric('阻断', 1, 'danger')
      ],
      rawText: `blocked | ${data.detail_status}\n${data.warning}`,
      emptyText: '当前活动无法生成可执行计划。'
    });
    log(`选中活动 dry-run 被阻断：${data.warning}`);
    return;
  }
  renderSinglePlan(data.plan, data.taskId);
  await refreshResults();
}

async function createBatchPlan() {
  const payload = batchPayload();
  payload.sampleOnly = true;
  payload.requireFullFetch = false;
  const data = await api('/api/batch/plan', { method: 'POST', body: payload });
  renderBatchPlan(data.batch, data.taskIds);
  await refreshResults();
}

async function executeSingleReal() {
  const promo = selectedPromotion();
  if (!promo) return log('请先选择活动。');
  const confirmText = requireRealSubmitConfirmation('提交选中活动预检');
  if (!confirmText) return;
  const payload = operationPayload(promo);
  payload.mode = 'real';
  payload.confirmText = confirmText;
  payload.writeConcurrency = Number($('settingWriteConcurrency').value || state.settings?.writeConcurrency || 2);
  const { response, data } = await apiRaw('/api/execute', { method: 'POST', body: payload });
  if (data.confirmation_package) {
    renderConfirmationPackage(data.confirmation_package);
    log(`真实执行检查已生成：可执行 ${data.confirmation_package.planned}，跳过 ${data.confirmation_package.skipped}，阻断 ${data.confirmation_package.blocked}。`);
    return;
  }
  if (response.ok && data.ok) {
    const rawText = [
      `单活动真实执行完成 | 任务 ${data.taskId}`,
      `成功 ${data.counts?.success ?? 0}，失败 ${data.counts?.failed ?? 0}，跳过 ${data.counts?.skipped ?? 0}`,
      `写入并发 ${data.writeConcurrency ?? '-'}，完成状态：${data.completed ? '完成' : '部分失败'}`
    ].join('\n');
    renderResultView({
      title: '单活动真实执行完成',
      subtitle: `任务 ${data.taskId}；写入并发 ${data.writeConcurrency ?? '-'}`,
      cards: [
        metric('成功', data.counts?.success ?? 0, 'ok'),
        metric('失败', data.counts?.failed ?? 0, data.counts?.failed ? 'danger' : 'ok'),
        metric('跳过', data.counts?.skipped ?? 0, data.counts?.skipped ? 'warn' : ''),
        metric('完成状态', data.completed ? '完成' : '部分失败', data.completed ? 'ok' : 'warn')
      ],
      columns: [
        { label: '任务', value: () => data.taskId || '-' },
        { label: '成功', value: () => data.counts?.success ?? 0 },
        { label: '失败', value: () => data.counts?.failed ?? 0 },
        { label: '跳过', value: () => data.counts?.skipped ?? 0 },
        { label: '状态', value: () => data.completed ? '完成' : '部分失败' }
      ],
      rows: [data],
      rawText,
      emptyText: '没有执行明细。'
    });
    log(`单活动真实执行完成：成功 ${data.counts?.success ?? 0}，失败 ${data.counts?.failed ?? 0}，跳过 ${data.counts?.skipped ?? 0}。`);
    await refreshResults();
    return;
  }
  log(data.error || data.message || `真实提交未生成确认包：HTTP ${response.status}`);
}

async function executeBatchRealPreview() {
  const confirmText = requireRealSubmitConfirmation('提交批量执行预检');
  if (!confirmText) return;
  const payload = batchPayload();
  payload.mode = 'real';
  payload.confirmText = confirmText;
  payload.writeConcurrency = Number($('settingWriteConcurrency').value || state.settings?.writeConcurrency || 2);
  payload.requireFullFetch = payload.action === 'enroll';
  payload.sampleOnly = false;
  const { response, data } = await apiRaw('/api/batch/execute', { method: 'POST', body: payload });
  if (data.confirmation_package) {
    renderConfirmationPackage(data.confirmation_package);
    log(`批量真实执行检查已生成：可执行 ${data.confirmation_package.planned}，跳过 ${data.confirmation_package.skipped}，阻断 ${data.confirmation_package.blocked}。`);
    return;
  }
  if (response.ok && data.ok) {
    renderBatchExecution(data.batch);
    log(`批量真实执行完成：成功 ${data.batch.success}，失败 ${data.batch.failed}，跳过 ${data.batch.skipped}，阻断活动 ${data.batch.blocked}。`);
    await refreshResults();
    return;
  }
  log(data.error || data.message || `批量真实执行未返回确认包或执行结果：HTTP ${response.status}`);
}

function renderBatchExecution(batch) {
  const rawText = [
    '批量真实执行结果',
    `活动 ${batch.promotions_total}，商品 ${batch.total}`,
    `成功 ${batch.success}，失败 ${batch.failed}，跳过 ${batch.skipped}，阻断活动 ${batch.blocked}`,
    `写入并发 ${batch.writeConcurrency}`,
    '',
    ...batch.promotions.map((row) => `${row.site_id || '-'} ${row.promotion_id} ${row.promotion_type} | 任务 ${row.taskId || '-'} | 总 ${row.total} | 成功 ${row.success} | 失败 ${row.failed} | 跳过 ${row.skipped} | ${row.completed === false ? '部分失败' : row.status || '完成'}`)
  ].join('\n');
  renderResultView({
    title: '批量真实执行结果',
    subtitle: `写入并发 ${batch.writeConcurrency}`,
    cards: [
      metric('活动', batch.promotions_total),
      metric('商品', batch.total),
      metric('成功', batch.success, 'ok'),
      metric('失败', batch.failed, batch.failed ? 'danger' : 'ok'),
      metric('跳过', batch.skipped, batch.skipped ? 'warn' : ''),
      metric('阻断活动', batch.blocked, batch.blocked ? 'danger' : 'ok')
    ],
    columns: [
      { label: '站点', value: (row) => row.site_id || '-' },
      { label: '活动', value: (row) => row.promotion_id || '-' },
      { label: '类型', value: (row) => row.promotion_type || '-' },
      { label: '任务', value: (row) => row.taskId || '-' },
      { label: '总数', value: (row) => row.total ?? 0 },
      { label: '成功', value: (row) => row.success ?? 0 },
      { label: '失败', value: (row) => row.failed ?? 0 },
      { label: '跳过', value: (row) => row.skipped ?? 0 },
      { label: '状态', value: (row) => row.completed === false ? '部分失败' : row.status || '完成' }
    ],
    rows: batch.promotions || [],
    rawText,
    emptyText: '没有活动执行明细。'
  });
}

async function cancelFilteredPreview() {
  const data = await api('/api/cancel/filtered/preview', {
    method: 'POST',
    body: { accountId: selectedAccount(), filters: filters(), itemStatus: 'started' }
  });
  state.lastPreview = data;
  renderBatchPlan(data.batch, data.taskIds);
  log(`预览筛选取消：活动 ${data.batch.totals.promotions}，可取消 ${data.batch.totals.planned}，阻断 ${data.batch.totals.blocked}。`);
  await refreshResults();
}

async function cancelFilteredPrecheck() {
  const confirmText = requireRealSubmitConfirmation('提交筛选取消预检');
  if (!confirmText) return;
  const payload = {
    accountId: selectedAccount(),
    filters: filters(),
    itemStatus: 'started',
    mode: 'real',
    confirmText,
    writeConcurrency: Number($('settingWriteConcurrency').value || state.settings?.writeConcurrency || 2)
  };
  const { response, data } = await apiRaw('/api/cancel/filtered/precheck', { method: 'POST', body: payload });
  if (data.confirmation_package) {
    state.lastPrecheck = data;
    renderConfirmationPackage(data.confirmation_package);
    log(`筛选取消真实执行检查已生成：可执行 ${data.confirmation_package.planned}，阻断 ${data.confirmation_package.blocked}。`);
    return;
  }
  log(data.error || data.message || `筛选取消预检未生成确认包：HTTP ${response.status}`);
}

async function exportWorkspace() {
  const data = await api('/api/export/workspace', {
    method: 'POST',
    body: {
      accountId: selectedAccount(),
      preview: state.lastPreview,
      precheck: state.lastPrecheck
    }
  });
  $('settingsSummary').textContent = `已导出 ${data.export.files.length} 个文件到：${data.export.outputDir}`;
  log(`结果目录已生成：${data.export.outputDir}`);
}

function operationPayload(promo) {
  const action = $('actionSelect').value;
  const selected = state.items.filter((item) => state.selectedItems.has(item.item_id));
  return {
    accountId: selectedAccount(),
    promotionId: promo.promotion_id,
    promotionType: promo.promotion_type,
    action,
    status: $('itemStatus').value,
    items: selected,
    priceMode: $('priceMode').value,
    discountPercent: promo.promotion_type === 'SELLER_CAMPAIGN' ? Number($('sellerDiscount').value || 5) : Number($('officialDiscount').value || 6),
    writeConcurrency: Number($('settingWriteConcurrency').value || state.settings?.writeConcurrency || 2),
    directPrice: $('directPrice').value ? Number($('directPrice').value) : null
  };
}

function batchPayload() {
  return {
    accountId: selectedAccount(),
    action: $('actionSelect').value,
    filters: filters(),
    itemStatus: $('itemStatus').value,
    priceMode: $('priceMode').value,
    sellerDiscountPercent: Number($('sellerDiscount').value || 5),
    officialDiscountPercent: Number($('officialDiscount').value || 6),
    allowInventoryFallback: true,
    writeConcurrency: Number($('settingWriteConcurrency').value || state.settings?.writeConcurrency || 2),
    directPrice: $('directPrice').value ? Number($('directPrice').value) : null
  };
}

function renderSinglePlan(plan, taskId) {
  const rawText = [
    `任务 ${taskId}`,
    `活动：${plan.promotion.name || plan.promotion.promotion_id}`,
    `动作：${plan.action}`,
    `价格规则：${plan.priceMode}，折扣 ${plan.discountPercent}`,
    `总数 ${plan.total}，可执行 ${plan.planned}，跳过 ${plan.skipped}`,
    '',
    ...plan.rows.slice(0, 160).map((row) => `${row.item.item_id || '-'} | ${row.status} | 目标价 ${row.deal_price ?? '-'} | ${row.reason}`)
  ].join('\n');
  renderResultView({
    title: '选中活动 dry-run',
    subtitle: `任务 ${taskId}；活动 ${plan.promotion.name || plan.promotion.promotion_id}`,
    cards: [
      metric('商品', plan.total),
      metric('可执行', plan.planned, 'ok'),
      metric('跳过', plan.skipped, plan.skipped ? 'warn' : ''),
      metric('动作', plan.action),
      metric('价格规则', plan.priceMode),
      metric('折扣', plan.discountPercent ?? '-')
    ],
    columns: [
      { label: '商品', value: (row) => row.item?.item_id || '-' },
      { label: '状态', value: (row) => row.status || '-' },
      { label: '目标价', value: (row) => money(row.deal_price) || '-' },
      { label: '原因', value: (row) => row.reason || '-' }
    ],
    rows: plan.rows.slice(0, 160),
    rawText,
    emptyText: '没有商品计划明细。'
  });
  log(`选中活动 dry-run：可执行 ${plan.planned}，跳过 ${plan.skipped}。`);
}

function renderBatchPlan(batch, taskIds) {
  const lines = [
    `批量 dry-run 任务：${taskIds.length} 个${batch.sample_only ? ' | 样本预览，不代表平台全量' : ''}`,
    `活动 ${batch.totals.promotions}，商品 ${batch.totals.total}，可执行 ${batch.totals.planned}，跳过 ${batch.totals.skipped}，空活动 ${batch.totals.empty}，blocking ${batch.totals.blocked}`,
    '',
    ...batch.plans.map(({ promotion, plan, blocked, warning, fetch_info }) => `${promotion.site_id || '-'} ${promotion.name || promotion.promotion_id} | ${promotion.promotion_type} | 平台 ${fetch_info?.platform_total ?? '-'} | 已读取 ${fetch_info?.saved_count ?? plan.total}${fetch_info?.partial_readable_subset ? '（可读子集，非全量）' : ''} | ${fetch_info?.is_full_fetch ? '已全量' : '未全量'}${fetch_info?.empty_page_count ? ` | 空页 ${fetch_info.empty_page_count}` : ''} | ${blocked ? 'blocked' : `总 ${plan.total} | 可执行 ${plan.planned} | 跳过 ${plan.skipped}`} ${warning || ''}`)
  ];
  renderResultView({
    title: '批量 dry-run',
    subtitle: `${taskIds.length} 个任务${batch.sample_only ? '；样本预览，不代表平台全量' : ''}`,
    cards: [
      metric('活动', batch.totals.promotions),
      metric('商品', batch.totals.total),
      metric('可执行', batch.totals.planned, 'ok'),
      metric('跳过', batch.totals.skipped, batch.totals.skipped ? 'warn' : ''),
      metric('空活动', batch.totals.empty, batch.totals.empty ? 'warn' : ''),
      metric('阻断', batch.totals.blocked, batch.totals.blocked ? 'danger' : 'ok')
    ],
    columns: [
      { label: '站点', value: (row) => row.promotion.site_id || '-' },
      { label: '活动', value: (row) => row.promotion.name || row.promotion.promotion_id || '-' },
      { label: '类型', value: (row) => row.promotion.promotion_type || '-' },
      { label: '平台 total', value: (row) => row.fetch_info?.platform_total ?? '-' },
      { label: '已读取', value: (row) => row.fetch_info?.saved_count ?? row.plan.total ?? 0 },
      { label: '全量', value: (row) => row.fetch_info?.is_full_fetch ? '是' : '否' },
      { label: '可执行', value: (row) => row.blocked ? 0 : row.plan.planned },
      { label: '跳过', value: (row) => row.plan.skipped },
      { label: '阻断/原因', value: (row) => row.blocked ? row.warning || 'blocked' : row.warning || '-' }
    ],
    rows: batch.plans,
    rawText: lines.join('\n'),
    emptyText: '没有活动计划明细。'
  });
  log(`批量 dry-run：活动 ${batch.totals.promotions}，可执行 ${batch.totals.planned}，跳过 ${batch.totals.skipped}。`);
}

function renderTodayDecision(decision) {
  if (!decision) return;
  const actionLabel = legacyActionText(decision.action || decision.today_action);
  const title = decision.already_completed
    ? '今日判断：今日已完成'
    : `今日判断：进入${actionLabel}`;
  const todayTitle = $('todayTitle');
  if (todayTitle) todayTitle.textContent = title;
  $('todaySummary').textContent = [
    `以本次完整折扣为基准递增：自建活动 ${$('sellerDiscount')?.value || 5}%，官方活动 ${$('officialDiscount')?.value || 6}%。`,
    `活动：${decision.selected_promotions}/${decision.promotions_total}`,
    decision.already_completed ? '今天已完整执行' : '',
    decision.needs_resume ? '存在未完成任务，建议补跑' : '',
    decision.reason
  ].filter(Boolean).join(' | ');
}

function renderConfirmationPackage(pkg) {
  if (pkg.package_type === 'seller_campaign_create_precheck') {
    renderCreationConfirmationPackage(pkg);
    return;
  }
  const lines = [
    `真实执行检查结果 | ${pkg.status === 'blocked' ? '已阻断' : '可继续核对'}`,
    `账号：${pkg.account_id || '-'} | merchant：${pkg.merchant_id || '-'}`,
    pkg.package_type === 'batch_real_write_precheck'
      ? `批量：活动 ${pkg.promotions_total || 0}，站点 ${((pkg.sites || []).join(', ') || '-')}`
      : `活动：${pkg.site_id || '-'} | child ${pkg.child_user_id || '-'} | ${pkg.promotion_name || pkg.promotion_id || '-'} | ${pkg.promotion_type || '-'}`,
    `动作：${pkg.action} | 商品状态：${pkg.item_status || '-'} | 模式：${pkg.mode}`,
    `写入并发：${pkg.write_concurrency || '-'}（REAL_SUBMIT 执行时使用）`,
    `读取口径：${pkg.full_fetch_required ? '要求全量' : '未要求全量'} | ${pkg.inventory_fallback_ready ? '库存兜底已启用' : (pkg.sample_only ? '样本/子集数据' : '非样本')}${pkg.platform_total !== undefined ? ` | 平台 ${pkg.platform_total ?? '-'} | 已读取 ${pkg.saved_count ?? 0}${pkg.partial_readable_subset ? '（可读候选子集，非全量）' : ''}${pkg.inventory_fallback_ready ? ` | 扫描 ${pkg.inventory_scan_saved ?? '-'} / ${pkg.inventory_scan_total ?? '-'} | 新增兜底 ${pkg.inventory_added_count ?? 0} | 排除已参加/待参加 ${pkg.inventory_excluded_started_pending ?? 0}` : ''} | ${pkg.is_full_fetch ? '已全量' : '未全量'}${pkg.empty_page_count ? ` | 空页 ${pkg.empty_page_count}` : ''}${pkg.missing_count && !pkg.inventory_fallback_ready ? ` | 未返回明细 ${pkg.missing_count}` : ''}` : ''}`,
    `价格规则：${formatPriceRule(pkg.price_rule)} | 折扣 ${pkg.discount_percent ?? '-'} | 直接价 ${pkg.direct_price ?? '-'}`,
    `汇总：平台/计划商品 ${pkg.items_total || 0}，可执行 ${pkg.planned || 0}，跳过 ${pkg.skipped || 0}，阻断 ${pkg.blocked || 0}`,
    `预计影响：${pkg.expected_impact_summary || '-'}`,
    `回查方式：${pkg.recheck_method || '-'}`,
    ''
  ];
  if (pkg.blocking_reasons?.length) {
    lines.push('阻断原因：', ...pkg.blocking_reasons.map((reason) => `- ${reason}`), '');
  }
  if (pkg.promotions?.length) {
    lines.push('活动明细：');
    lines.push(...pkg.promotions.slice(0, 30).map((row) => `${row.site_id || '-'} ${row.promotion_id} ${row.promotion_type} | 平台 ${row.platform_total ?? '-'} | 已读取 ${row.saved_count ?? 0}${row.partial_readable_subset ? '（可读子集，非全量）' : ''}${row.inventory_fallback_ready ? ` | 库存兜底新增 ${row.inventory_added_count ?? 0}，扫描 ${row.inventory_scan_saved ?? '-'} / ${row.inventory_scan_total ?? '-'}` : ''} | ${row.is_full_fetch ? '已全量' : '未全量'}${row.empty_page_count ? ` | 空页 ${row.empty_page_count}` : ''}${row.missing_count && !row.inventory_fallback_ready ? ` | 未返回 ${row.missing_count}` : ''} | 可执行 ${row.planned} | 跳过 ${row.skipped} | 阻断 ${row.blocked} | ${row.status}`));
    lines.push('');
  }
  if (pkg.sample_items?.length) {
    lines.push('样本商品：');
    lines.push(...pkg.sample_items.map((item) => `${item.site_id ? `${item.site_id} ` : ''}${item.item_id || '-'} | ${item.status || '-'} | 当前 ${item.current_price ?? '-'} | 目标 ${item.target_deal_price ?? '-'} | 最低/最高 ${item.min ?? '-'} / ${item.max ?? '-'} | adapter:${item.adapter_state || '-'}${item.requires_limited_real_test ? ' | 需小样本验证' : ''} | ${specialFieldsText(item.special_fields)} | ${item.payload_evidence ? `依据:${item.payload_evidence}` : ''} | ${item.preview_payload ? `preview:${JSON.stringify(item.preview_payload)}` : ''} | ${item.adapter_missing_fields ? `缺:${item.adapter_missing_fields.join(',')}` : ''} | ${item.skip_or_error_reason || item.payload_preview_status || '-'}${item.adapter_next_step ? ` | ${item.adapter_next_step}` : ''}`));
    lines.push('');
  }
  if (pkg.candidate_resolution) {
    lines.push('candidate 明细异常处理：');
    lines.push(`- ${pkg.candidate_resolution.message}`);
    if (pkg.candidate_resolution.forbidden_fallbacks?.length) {
      lines.push(...pkg.candidate_resolution.forbidden_fallbacks.map((item) => `- 禁止：${item}`));
    }
    lines.push(...(pkg.candidate_resolution.safe_options || []).map((item) => `- ${item.label}：${item.description}`));
    lines.push('');
  }
  if (pkg.risk_prompts?.length) {
    lines.push('风险提示：', ...pkg.risk_prompts.map((risk) => `- ${risk}`));
  }
  const rows = confirmationPackageRows(pkg);
  renderResultView({
    title: '真实执行检查结果',
    subtitle: `${pkg.status === 'blocked' ? '已阻断' : '可继续核对'}；真实写入入口需要二次确认，是否可继续以后端确认包或返回结果为准。`,
    cards: [
      metric('活动', pkg.promotions_total ?? rows.length),
      metric('商品', pkg.items_total ?? 0),
      metric('可执行', pkg.planned ?? 0, 'ok'),
      metric('跳过', pkg.skipped ?? 0, pkg.skipped ? 'warn' : ''),
      metric('阻断', pkg.blocked ?? 0, pkg.blocked ? 'danger' : 'ok'),
      metric('状态', pkg.status || '-')
    ],
    columns: confirmationPackageColumns(),
    rows,
    rawText: lines.join('\n'),
    emptyText: '没有活动检查明细。'
  });
}

function confirmationPackageRows(pkg) {
  if (pkg.promotions?.length) return pkg.promotions;
  return [{
    site_id: pkg.site_id,
    promotion_id: pkg.promotion_id,
    promotion_type: pkg.promotion_type,
    platform_total: pkg.platform_total,
    saved_count: pkg.saved_count,
    is_full_fetch: pkg.is_full_fetch,
    inventory_fallback_ready: pkg.inventory_fallback_ready,
    inventory_added_count: pkg.inventory_added_count,
    inventory_scan_saved: pkg.inventory_scan_saved,
    inventory_scan_total: pkg.inventory_scan_total,
    planned: pkg.planned,
    skipped: pkg.skipped,
    blocked: pkg.blocked,
    status: pkg.status
  }];
}

function confirmationPackageColumns() {
  return [
    { label: '站点', value: (row) => row.site_id || '-' },
    { label: '活动', value: (row) => row.promotion_id || '-' },
    { label: '类型', value: (row) => row.promotion_type || '-' },
    { label: '平台 total', value: (row) => row.platform_total ?? '-' },
    { label: '已读取', value: (row) => row.saved_count ?? 0 },
    { label: '全量', value: (row) => row.is_full_fetch ? '是' : '否' },
    { label: '兜底', value: (row) => row.inventory_fallback_ready ? `新增 ${row.inventory_added_count ?? 0}，扫描 ${row.inventory_scan_saved ?? '-'}/${row.inventory_scan_total ?? '-'}` : '-' },
    { label: '可执行', value: (row) => row.planned ?? 0 },
    { label: '跳过', value: (row) => row.skipped ?? 0 },
    { label: '阻断', value: (row) => row.blocked ?? 0 },
    { label: '状态', value: (row) => row.status || '-' }
  ];
}

function renderCreationConfirmationPackage(pkg) {
  const lines = [
    `Seller Campaign 创建预检包 | ${pkg.status === 'blocked' ? '已阻断' : '请求预览已生成'}`,
    `账号：${pkg.account_id || '-'} | 站点：${pkg.site_id || '-'} | child：${pkg.child_user_id || '-'}`,
    `活动：${pkg.promotion_name || '-'} | 类型：${pkg.promotion_type} / ${pkg.sub_type}`,
    `时间：${pkg.start_date || '-'} -> ${pkg.finish_date || '-'} | ${pkg.duration_days === null ? '天数未知' : `${Number(pkg.duration_days).toFixed(2)} 天`}`,
    `预计影响：${pkg.expected_impact_summary || '-'}`,
    `回查方式：${pkg.recheck_method || '-'}`,
    '',
    '请求预览：',
    JSON.stringify(pkg.request_preview, null, 2),
    ''
  ];
  if (pkg.validation_errors?.length) {
    lines.push('阻断原因：', ...pkg.validation_errors.map((item) => `- ${item}`), '');
  }
  if (pkg.risk_prompts?.length) {
    lines.push('风险提示：', ...pkg.risk_prompts.map((item) => `- ${item}`));
  }
  renderResultView({
    title: 'Seller Campaign 创建预检',
    subtitle: pkg.status === 'blocked' ? '已阻断' : '请求预览已生成',
    cards: [
      metric('站点', pkg.site_id || '-'),
      metric('child_user_id', pkg.child_user_id || '-'),
      metric('活动类型', pkg.promotion_type || '-'),
      metric('阻断原因', pkg.validation_errors?.length || 0, pkg.validation_errors?.length ? 'danger' : 'ok')
    ],
    rawText: lines.join('\n'),
    emptyText: '创建预检没有活动明细表。'
  });
}

function formatSmartRealTestPackage(pkg) {
  const requestPreview = pkg.request_preview || {};
  const body = requestPreview.body || {};
  return [
    `SMART 单商品真实验证确认包 | ${pkg.status}`,
    `可立即执行：${pkg.can_execute_now ? '是' : '否'} | release code：${pkg.release?.release_policy?.enabled ? '已启用' : '未启用'}`,
    `账号：${pkg.target.account_id} | ${pkg.target.nickname}`,
    `站点：${pkg.target.site_id} | child ${pkg.target.child_user_id}`,
    `活动：${pkg.target.promotion_id} | ${pkg.target.promotion_type}`,
    `商品：${pkg.target.item_id} | candidate | offer ${pkg.target.offer_id}`,
    `价格：${pkg.target.price} ${pkg.target.currency_id} | 原价 ${pkg.target.original_price} | seller% ${pkg.target.seller_percentage} | meli% ${pkg.target.meli_percentage}`,
    `payload preview：${JSON.stringify(body)}`,
    `release：enabled=${pkg.release.release_policy.enabled} | code_present=${pkg.release.release_code_present} | target_match=${pkg.release.target_match}`,
    '',
    '回查方式：',
    ...(pkg.recheck_method || []).map((line) => `- ${line}`),
    '',
    '失败处理：',
    ...(pkg.failure_handling || []).map((line) => `- ${line}`),
    '',
    '硬阻断：',
    ...(pkg.hard_blocks || []).map((line) => `- ${line}`)
  ].join('\n');
}

async function cycleDecision() {
  const promo = selectedPromotion();
  if (!promo) return log('请先选择活动。');
  const data = await api('/api/cycle/decision', {
    method: 'POST',
    body: { accountId: selectedAccount(), promotionId: promo.promotion_id, promotionType: promo.promotion_type }
  });
  log(`周期建议：${data.decision.action}，折扣 ${data.decision.discount}%，started ${data.startedCount}，${data.decision.reason}`);
}

async function refreshResults() {
  const data = await api('/api/tasks?limit=300');
  $('resultsBody').innerHTML = (data.tasks || []).map((row) => `
    <tr>
      <td>${esc(shortDate(row.created_at || row.updated_at))}</td>
      <td>${esc(legacyActionText(row.action))}</td>
      <td>${esc(row.seller_activity_text || (isSellerPromotionId(row.promotion_id, row.promotion_type) ? activityName(row) : ''))}</td>
      <td>${esc(row.official_activity_text || (!isSellerPromotionId(row.promotion_id, row.promotion_type) ? activityName(row) : ''))}</td>
      <td>${esc(row.mode === 'dry-run' ? '预览' : '提交')}</td>
      <td>${esc(row.action === 'enroll' ? '已报名商品数' : '实际处理数')}</td>
      <td>${Number(row.total_count || 0)}</td>
      <td>${Number(row.mode === 'dry-run' ? row.planned_count || 0 : row.success_count || 0)}</td>
      <td>${Number(row.mode === 'dry-run' ? row.skipped_count || 0 : row.failed_count || 0)}</td>
      <td>${esc(taskReasonText(row))}</td>
    </tr>
  `).join('');
}

function taskReasonText(row) {
  if (row.short_failure_reason) return row.short_failure_reason;
  const parts = [];
  const reasons = taskFailureReasons(row).slice(0, 3);
  if (reasons.length) parts.push(reasons.map((reason) => `${reason.reason} ${reason.count}`).join('，'));
  if (Number(row.blocked_count || 0)) parts.push(`阻断:${row.blocked_count}`);
  if (Number(row.skipped_count || 0)) parts.push(`跳过:${row.skipped_count}`);
  if (!reasons.length && (row.completed === 0 || row.completed === false)) parts.push('未完成');
  return parts.join('；');
}

function taskFailureReasons(row) {
  try {
    const summary = JSON.parse(row.summary_json || '{}');
    return Array.isArray(summary.failure_reasons) ? summary.failure_reasons : [];
  } catch {
    return [];
  }
}

function activityName(row) {
  if (row.promotion_name) return row.promotion_name;
  if (row.promotion_id === '__BATCH__' || row.promotion_type === 'BATCH') return '';
  return row.promotion_id || '';
}

function legacyActionText(action) {
  if (action === 'enroll') return '批量报活动';
  if (action === 'update') return '批量更新';
  if (action === 'cancel') return '批量取消';
  if (action === 'completed') return '已完成';
  if (!action) return '-';
  return action;
}

function isSellerPromotionId(promotionId, promotionType) {
  const type = String(promotionType || '').toUpperCase();
  if (type) return type === 'SELLER_CAMPAIGN';
  return String(promotionId || '').startsWith('C-');
}

function syncStatusForAction() {
  const action = $('actionSelect').value;
  $('itemStatus').value = action === 'enroll' ? 'candidate' : 'started';
}

function updateDangerButtons() {
  $('executeBtn').disabled = false;
  $('batchExecuteBtn').disabled = false;
  $('todayPrecheckBtn').disabled = false;
  $('cancelFilteredPrecheckBtn').disabled = false;
}

function requireRealSubmitConfirmation(label) {
  const confirmed = window.confirm(`${label}将进入本地真实写入预检/确认流程。继续前请确认店铺、站点、活动、商品数和价格规则已经核对；后续以后端确认包或返回结果为准。`);
  if (!confirmed) {
    log(`${label}已取消。`);
    return null;
  }
  const typed = window.prompt(`请输入 REAL_SUBMIT 继续${label}；后端仍会按确认包和保护门返回结果。`, '');
  if (typed !== 'REAL_SUBMIT') {
    log(`${label}未提交：口令不匹配。`);
    return null;
  }
  return typed;
}

function selectedAccount() {
  return $('accountSelect').value || state.accounts[0]?.account_id || '';
}

function currentAccount() {
  return state.accounts.find((account) => String(account.account_id) === String(selectedAccount())) || null;
}

function selectedPromotion() {
  return state.promotions.find((promo) => isSelectedPromotion(promo)) || null;
}

function isSelectedPromotion(promo) {
  return promo.promotion_id === state.selectedPromotionId && promo.promotion_type === state.selectedPromotionType;
}

function updateSelectedActivityText() {
  const promo = selectedPromotion();
  if (!promo) {
    $('selectedActivityText').textContent = '未选择活动';
    return;
  }
  $('selectedActivityText').textContent = `选中：${promo.site_id || '-'} | ${promo.name || promo.promotion_id} | ${promo.promotion_type} | child ${promo.child_user_id || '-'}`;
  $('sellerDiscount').value = state.settings?.sellerDefaultDiscount ?? 5;
  $('officialDiscount').value = state.settings?.officialDefaultDiscount ?? 6;
}

function filters() {
  return {
    siteId: $('siteFilter').value,
    siteIds: splitInput($('siteMultiFilter').value || $('siteFilter').value),
    promotionType: $('typeFilter').value,
    promotionTypes: splitInput($('typeMultiFilter').value || $('typeFilter').value),
    status: $('statusFilter').value,
    name: $('nameFilter').value.trim(),
    keywords: splitInput($('keywordFilter').value || $('nameFilter').value),
    excludeSeller: $('excludeSellerFilter').checked,
    excludeOfficial: $('excludeOfficialFilter').checked
  };
}

function splitInput(value) {
  return String(value || '').split(',').map((item) => item.trim()).filter(Boolean);
}

async function api(url, options = {}) {
  const { response, data } = await apiRaw(url, options);
  if (!response.ok || data.ok === false) {
    const message = data.error || `请求失败 ${response.status}`;
    log(message);
    const error = new Error(message);
    error.data = data;
    error.status = response.status;
    throw error;
  }
  return data;
}

async function apiRaw(url, options = {}) {
  const init = { method: options.method || 'GET', headers: { 'Content-Type': 'application/json' } };
  if (options.body) init.body = JSON.stringify(options.body);
  const response = await fetch(url, init);
  const data = await response.json();
  return { response, data };
}

function renderResultView({ title, subtitle = '', cards = [], columns = [], rows = [], rawText = '', emptyText = '暂无明细' }) {
  if (title && title !== '等待结果') setActiveView('results');
  const cardsBox = $('resultSummaryCards');
  if (cardsBox) {
    cardsBox.innerHTML = cards.length
      ? cards.map(renderMetricCard).join('')
      : `<div class="empty-state">${esc(emptyText)}</div>`;
  }
  const meta = $('resultMeta');
  if (meta) {
    meta.innerHTML = `<strong>${esc(title || '结果')}</strong>${subtitle ? ` <span>${esc(subtitle)}</span>` : ''}`;
  }
  const details = $('resultDetailTable');
  if (details) {
    if (columns.length && rows.length) {
      details.className = '';
      details.innerHTML = renderDataTable(columns, rows, 'result-details-wrap');
    } else {
      details.className = 'detail-placeholder';
      details.textContent = emptyText;
    }
  }
  const raw = $('planBox');
  if (raw) raw.textContent = rawText || title || '尚未生成计划';
}

function metric(label, value, tone = '') {
  return { label, value, tone };
}

function renderMetricCard(card) {
  return `<div class="metric-card ${esc(card.tone || '')}"><span>${esc(card.label)}</span><strong>${esc(card.value ?? '-')}</strong></div>`;
}

function renderDataTable(columns, rows, wrapClass = '') {
  return `<div class="table-wrap ${esc(wrapClass)}"><table><thead><tr>${columns.map((column) => `<th>${esc(column.label)}</th>`).join('')}</tr></thead><tbody>${rows.map((row) => `<tr>${columns.map((column) => `<td>${renderCell(column.value(row), column)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function renderCell(value) {
  if (value && typeof value === 'object' && value.html) return value.html;
  return esc(value ?? '-');
}

function firstValue(row, keys, fallback = '-') {
  for (const key of keys) {
    const value = row?.[key];
    if (value !== null && value !== undefined && value !== '') return value;
  }
  return fallback;
}

function firstNumber(row, keys) {
  const value = firstValue(row, keys, 0);
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function sumFirstNumber(rows, keys) {
  return (rows || []).reduce((sum, row) => sum + firstNumber(row, keys), 0);
}

function log(message) {
  const box = $('logBox');
  const atBottom = box.scrollHeight - box.scrollTop <= box.clientHeight + 20;
  box.textContent += `[${new Date().toLocaleTimeString()}] ${message}\n`;
  if (atBottom) box.scrollTop = box.scrollHeight;
}

function typeCell(type) {
  const cls = type === 'SELLER_CAMPAIGN' ? 'type-seller' : type === 'DEAL' ? 'type-official' : 'type-other';
  const label = type === 'SELLER_CAMPAIGN' ? '自建 SELLER_CAMPAIGN' : type === 'DEAL' ? '官方 DEAL' : `${type || ''}（需专项适配）`;
  return `<span class="${cls}">${esc(label)}</span>`;
}

function formatPriceRule(rule) {
  if (!rule) return '-';
  return Object.entries(rule).map(([key, value]) => `${key}:${value ?? '-'}`).join(' / ');
}

function specialFieldsText(fields) {
  if (!fields) return '专项字段:-';
  const parts = [];
  if (fields.offer_id) parts.push(`offer:${fields.offer_id}`);
  if (fields.seller_percentage !== null && fields.seller_percentage !== undefined) parts.push(`seller%:${fields.seller_percentage}`);
  if (fields.meli_percentage !== null && fields.meli_percentage !== undefined) parts.push(`meli%:${fields.meli_percentage}`);
  if (fields.stock) parts.push(`stock:${JSON.stringify(fields.stock)}`);
  if (fields.min_discounted_price !== null && fields.min_discounted_price !== undefined) parts.push(`min:${fields.min_discounted_price}`);
  if (fields.price !== null && fields.price !== undefined) parts.push(`price:${fields.price}`);
  if (fields.original_price !== null && fields.original_price !== undefined) parts.push(`orig:${fields.original_price}`);
  if (fields.top_deal_price !== null && fields.top_deal_price !== undefined) parts.push(`top:${fields.top_deal_price}`);
  return parts.length ? `专项字段 ${parts.join(' / ')}` : '专项字段:-';
}

function localDateInputToIso(value) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toISOString();
}

function statusCell(status) {
  const cls = status === 'success' || status === 'planned' ? 'badge-ok' : status === 'failed' ? 'badge-danger' : 'badge-warn';
  return `<span class="${cls}">${esc(status || '')}</span>`;
}

function summaryObj(obj) {
  return Object.entries(obj || {}).map(([key, value]) => `${key}:${value}`).join(' / ');
}

function unique(values) {
  return [...new Set(values)].sort();
}

function formatTime(value) {
  return value ? new Date(value).toLocaleString() : '-';
}

function shortDate(value) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleDateString();
}

function money(value) {
  return value === null || value === undefined || value === '' ? '' : Number(value).toFixed(2);
}

function normalizeUiCopy(value) {
  const legacySupervisorPackage = ['主管', '确认包'].join('');
  const legacyFinalConfirm = ['最终确认', '后才生效'].join('');
  const legacyNoDirectWrite = ['本轮仍不', '会直接执行 Mercado', ' 写接口。'].join('');
  return String(value ?? '')
    .replace(new RegExp(['当前程序只生成请求预览和', legacySupervisorPackage, '，不执行真实创建。'].join(''), 'g'), '当前程序展示请求预览和创建风险状态。')
    .replace(new RegExp(['真实报名仍必须经过预检包和主管', '最终确认门。'].join(''), 'g'), '真实报名提交前必须核对账号、站点、活动、商品和价格。')
    .replace(new RegExp(['真实创建前仍需主管按账号、站点、child、名称、时间范围再次确认。'].join(''), 'g'), '真实创建前必须按账号、站点、child、名称、时间范围再次核对。')
    .replace(new RegExp(legacySupervisorPackage, 'g'), '创建预检包')
    .replace(/不执行真实创建/g, '用于创建预检')
    .replace(new RegExp(['主管', '最终确认'].join(''), 'g'), '后端确认包或返回结果确认')
    .replace(new RegExp(legacyFinalConfirm, 'g'), '以后端返回结果为准')
    .replace(new RegExp(legacyNoDirectWrite, 'g'), '后端会按确认包和保护门返回结果。');
}

function esc(value) {
  return normalizeUiCopy(value).replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  }[char]));
}
