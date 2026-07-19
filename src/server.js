import http from 'node:http';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { HOST, PORT, DEFAULT_AUTH_DOMAIN, DATA_DIR } from './config.js';
import { buildBatchConfirmationPackage, buildConfirmationPackage } from './confirmationPackage.js';
import { exportWorkspace } from './exporter.js';
import { queryFiltersFromSearchParams } from './filterQuery.js';
import { CANDIDATE_INCOMPLETE_STATUSES, buildBatchPlans, buildPlan, filterPromotions, normalizeItem, promotionKey, roundMoney, validateDealPrice } from './planner.js';
import { ordinaryPromotions, promotionBucketCounts } from './promotionDomain.js';
import { activityItemsDecision, isActivityExpired, nextNonPeakCalibrationAt, planActivityCatalogRoutes } from './activityChangeCache.js';
import { activityCallbackConfig, createActivityCallbackAdapter } from './activityCallbackAdapter.js';
import { createActivityWebhookConsumer } from './activityWebhookConsumer.js';
import { buildCandidateIncompleteResolution, buildManualCandidateDraftRows, parseManualCandidateItemIds } from './candidateResolution.js';
import { MAX_READ_CONCURRENCY, MAX_WRITE_CONCURRENCY, mapLimited, mapLimitedWithCap, normalizeConcurrency, normalizeConcurrencyWithCap, normalizeWriteConcurrency } from './concurrency.js';
import { BALANCED_READ_PROFILES, buildReadConcurrencyReport, createBalancedReadScheduler } from './balancedReadScheduler.js';
import { createAsyncLimiter, executePlannedRowsWithConcurrency } from './executor.js';
import { ADAPTIVE_WRITE_PROFILE, createAdaptiveWriteScheduler } from './adaptiveWriteScheduler.js';
import { createPendingWriteQueue, pendingRelationKey } from './pendingWriteQueue.js';
import {
  filterItemsByConfirmedScope,
  filterItemsByRequestedIds,
  filterPromotionsByConfirmedScope,
  hasConfirmedExecutionScope,
  requestedItemFilterErrorMessage,
} from './executionItemFilter.js';
import {
  INVENTORY_FALLBACK_ITEM_STATUS,
  INVENTORY_FALLBACK_READY_STATUS,
  INVENTORY_FALLBACK_SOURCE,
  buildSellerCampaignInventoryFallback,
  isSellerCampaign
} from './inventoryFallback.js';
import { decideCycleAction, getCycleState, markCycleAfterTask, nextDiscountFor } from './cycle.js';
import { ApiError, toChineseError } from './errors.js';
import { MercadoLibreClient, buildAuthorizationUrl, extractMarketplaceUsers, extractPromotions, mergePromotionsByIdentity } from './mlClient.js';
import { accountProfileRecord } from './accountProfiles.js';
import {
  HIDDEN_SELLER_CAMPAIGN_MESSAGE,
  HIDDEN_SELLER_CAMPAIGN_STATUS,
  applyHiddenSellerCampaignSkip,
  isDuplicateSellerCampaignNameError,
  recoverDuplicateSellerCampaign,
  resolveHiddenSellerCampaignTargets,
} from './sellerCampaignDuplicateRecovery.js';
import {
  accountRouteKey,
  assertAccountRouteAllowed,
  bindActivitiesToAccountRoute,
  bindActivityToAccountRoute,
  normalizeAccountRoute,
} from './accountRouteIdentity.js';
import {
  reconcileActivityCatalog,
  requireAuthoritativeActivityCatalogRead,
  selectMarketplaceUsersForCatalogRoutes,
  sellerCampaignWriteThroughPromotion,
  summarizeActivityCatalogRouteReads,
} from './activityCatalogReconcile.js';
import { resolveStoreIdentity } from './storeNameDomain.js';
import { prepareOAuthStartFromConfig } from './oauthConfig.js';
import { parseOAuthCallbackInput, selectCodeOnlyOAuthState } from './oauthCallback.js';
import { PROMOTION_CREATION_STATUS } from './promotionCreationStatus.js';
import { buildSellerCampaignBatchCreatePrecheck, buildSellerCampaignCreateConfirmation, summarizeSellerCampaignLiveSites } from './promotionCreation.js';
import { buildRealEnrollSmokeConfirmation, listRealEnrollSmokeTargets, REAL_ENROLL_SMOKE_POLICY } from './realEnrollSmokeTest.js';
import { realSubmitProtection } from './protection.js';
import { createPkcePair, createState } from './security.js';
import { SMART_REAL_TEST_RELEASE_POLICY, SMART_REAL_TEST_TARGET, buildSingleItemRealTestConfirmation } from './smartRealTest.js';
import {
  SMART_CANCEL_POLICY,
  buildSmartCancelQuery,
  buildSmartCancelRequestPreview,
  limitSmartCancelPlan,
  normalizeSmartCancelSampleLimit,
  smartCancelFieldEvidence
} from './smartCancel.js';
import { getStandaloneSecrets, hasStandaloneAuth, readStandaloneConfig, readStandaloneToken, refreshStandaloneToken, standaloneAccountSummary } from './standaloneAuth.js';
import {
  clearOAuthStates,
  consumeOAuthState,
  createTask,
  deleteTasks,
  finishTask,
  getAccount,
  getAccountSecrets,
  getAccountProfile,
  getCampaign,
  getItemFetchState,
  getActivityCacheState,
  hasActivityCallbackEvent,
  listAllMarketplaceSites,
  listStoredAccounts,
  listCampaigns,
  listCampaignsFiltered,
  listCycleStatesForPromotions,
  listItemCountsForPromotions,
  listItemFetchStatesForPromotions,
  listItems,
  listItemsForPromotions,
  listGlobalDiscountUpdateSummaries,
  listHiddenSellerCampaignsForAccount,
  listHiddenSellerCampaignsForRoute,
  listPendingOAuthStates,
  listPreparationReadStates,
  listResults,
  listTaskDetails,
  listTaskSummaries,
  listSiteSummaries,
  listSellerCampaignRecoveryCandidates,
  listVerifiedActivityCallbackPromotionMappings,
  invalidateMarketplaceSiteCatalog,
  markCampaignsCatalogRemoved,
  saveMarketplaceSites,
  deleteItemsBySource,
  saveCampaigns,
  saveExecutionResult,
  saveItemFetchState,
  recordActivityCatalogCalibration,
  recordActivityItemsCalibration,
  markActivityCacheDirty,
  saveActivityCacheState,
  saveActivityCallbackEvent,
  saveItems,
  saveOAuthState,
  savePlanResults,
  saveTokenAccount,
  saveAccountProfile,
  updateMarketplaceSitePromotionStatus,
  updateAccountToken,
  publishHistorySummaryForExecutionGroup
} from './repository.js';
import { getDb } from './db.js';
import { buildSubmitPayloadPreview, requireExecutableSubmitPayload, requireItemStatus } from './promotionPayload.js';
import { readSettings, saveSettings } from './settings.js';
import { filterByOperatingSites, hasOperatingSiteScope, mergeOperatingSiteEvidence, operatingSiteIds } from './operatingSites.js';
import { decideToday } from './today.js';
import { buildGlobalTodayDiscount } from './globalTodayDiscount.js';
import { createExecutionJobPersistence } from './executionJobPersistence.js';
import { readAuditText } from './executionAudit.js';
import {
  ACTIVE_EXECUTION_GROUP_STATUSES,
  TERMINAL_EXECUTION_GROUP_STATUSES,
  createExecutionGroupPersistence,
  executionGroupBusinessScope,
  summarizeExecutionGroup
} from './executionGroupPersistence.js';
import { PRODUCT_BUILD_INFO, PRODUCT_DISPLAY_NAME } from './productContract.js';
import {
  ACTIVE_SUBMISSION_STATES,
  cancelSubmissionCommit,
  claimSubmissionPreparation,
  commitSubmission,
  createSubmissionPersistence,
  loadAllEffectiveSubmissions,
  loadEffectiveSubmission,
  refreshSubmissionDeadline,
  resumePausedSubmission,
  runSubmissionPreparation,
  submissionRequestFingerprint,
  submissionScopeHash,
} from './submissionPersistence.js';
import {
  activityIdentityKey,
  activityReadKey,
  confirmedExecutionScopeFacts,
  createConfirmedExecutionScope,
  forceReadKeysForStatus,
  reconcileConfirmedExecutionScope,
} from './submissionScopeFreeze.js';
import { buildFinalRevalidationPlan } from './finalRevalidationPlan.js';
import {
  CANCEL_RESULT_STATUS,
  RESULT_CONTRACT_VERSION,
  buildCancelResultContract,
  stableContractCounts,
  summarizeLiveReadRows
} from './executionResultContract.js';
import { createSameDayConfirmationStore, sameDayCompletionGate } from './sameDayCompletionGate.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = path.join(__dirname, '..', 'public');
const batchFetchJobs = new Map();
const inventoryFallbackJobs = new Map();
const executionJobs = new Map();
const executionGroups = new Map();
const submissionPreparationTasks = new Map();
const submissionCommitTasks = new Map();
const submissionCommitControllers = new Map();
const submissionRecoveryAuditAt = new Map();
const PRE_GROUP_SUBMISSION_STATES = new Set(['committing', 'creating', 'created', 'starting']);
const sharedWriteLimiters = new Map();
const writeBenchmarkJobs = new Map();
let nextBatchFetchJobId = 1;
let nextInventoryFallbackJobId = 1;
let nextExecutionJobId = 1;
let nextWriteBenchmarkJobSeq = 1;
const CONCURRENCY_BENCHMARK_PATH = path.join(DATA_DIR, 'concurrency-benchmark-results.json');
const WRITE_BENCHMARK_JOB_DIR = path.join(DATA_DIR, 'write-benchmark-jobs');
const WRITE_BENCHMARK_JOB_INDEX_PATH = path.join(WRITE_BENCHMARK_JOB_DIR, 'index.json');
const EXECUTION_JOB_EVENT_DIR = path.join(DATA_DIR, 'execution-job-events');
const EXECUTION_JOB_STATE_DIR = path.join(DATA_DIR, 'execution-job-states');
const EXECUTION_GROUP_STATE_DIR = path.join(DATA_DIR, 'execution-group-states');
const SUBMISSION_STATE_DIR = path.join(DATA_DIR, 'execution-submissions');
const SAME_DAY_CONFIRMATION_STATE_DIR = path.join(DATA_DIR, 'execution-submission-confirmations');
const PENDING_WRITE_STATE_DIR = path.join(DATA_DIR, 'pending-write-queues');
const executionEventFileDescriptors = new Map();
const executionJobPersistence = createExecutionJobPersistence({ stateDir: EXECUTION_JOB_STATE_DIR, publicJob: publicExecutionJob });
const executionGroupPersistence = createExecutionGroupPersistence({ stateDir: EXECUTION_GROUP_STATE_DIR });
const submissionPersistence = createSubmissionPersistence({ stateDir: SUBMISSION_STATE_DIR });
const sameDayConfirmationStore = createSameDayConfirmationStore({ stateDir: SAME_DAY_CONFIRMATION_STATE_DIR });
const pendingWriteQueue = createPendingWriteQueue({ stateDir: PENDING_WRITE_STATE_DIR });
const SERVER_INSTANCE_ID = `node-${process.pid}-${Date.now()}-${crypto.randomUUID()}`;
const activityWebhookConsumer = createActivityWebhookConsumer({
  listMarketplaceSites: listAllMarketplaceSites,
  listAccounts: listAccountsForUi,
  createResourceClient: async (route) => {
    const account = await ensureUsableAccount(route.account_id);
    return new MercadoLibreClient({
      accessToken: account.accessToken,
      userId: route.child_user_id,
      callerId: route.child_user_id,
      marketplace: true,
      readAccountId: route.account_id,
    });
  },
  markDirty: markActivityCacheDirty,
  invalidateCatalog: invalidateMarketplaceSiteCatalog,
});
const activityCallbackAdapter = createActivityCallbackAdapter({
  config: activityCallbackConfig(),
  hasEvent: hasActivityCallbackEvent,
  saveEvent: saveActivityCallbackEvent,
  getCacheState: getActivityCacheState,
  markDirty: markActivityCacheDirty,
  consumeEvent: activityWebhookConsumer,
});
for (const group of executionGroupPersistence.loadAll()) {
  executionGroups.set(String(group.id), group);
  if (group.recovered_pending_after_restart && String(group.status || '') === 'queued') {
    setImmediate(() => runExecutionGroup(group.id).catch((error) => failExecutionGroup(group.id, error)));
  }
}
const READ_BENCHMARK_LEVELS = [1, 2, 3, 4, 5, 8, 10, 15, 20];
const WRITE_BENCHMARK_LEVELS = [1, 2, 3, 5, 8, 10, 15, 20];
const READ_BENCHMARK_MAX_CONCURRENCY = 20;
const WRITE_BENCHMARK_MAX_CONCURRENCY = 10000;
const LEGACY_SYNC_WRITE_ROUTES = new Set([
  '/api/execute',
  '/api/batch/execute',
  '/api/today/execute',
  '/api/today/precheck',
  '/api/cancel/filtered/precheck',
  '/api/real-enroll-smoke/execute',
  '/api/concurrency-benchmark/write/execute',
]);
const LEGACY_SYNC_WRITE_MESSAGE = '旧版同步执行入口已停用，请使用当前桌面版后台执行任务。';
const LATEST_WRITE_BENCHMARK_STATUS = {
  tool_status: '后台压测工具已启用逐商品落盘',
  source: '真实测试线程最新回传',
  action: 'update',
  target_discount_percent: 10,
  verified_stable_concurrency: 350,
  verified_note: '350 两次稳定，峰值 350，接口类失败 0',
  daily_recommendation_text: '保守 300-320；追求速度可手动设 350，并保留停止/回查',
  daily_recommended_min: 300,
  daily_recommended_max: 320,
  manual_fast_value: 350,
  formal_compact_result_pending: true,
  updated_at: '2026-07-05T00:00:00+08:00'
};

getDb();
for (const group of executionGroups.values()) {
  if (String(group.status || '') === 'interrupted') {
    publishHistorySummaryForExecutionGroup(group.id);
    const submission = submissionPersistence.findBySubmissionId(group.client_submission_id);
    if (submission) submissionPersistence.update(submission.id, { state: 'terminal', group_id: group.id, group: publicExecutionGroup(group), error: group.error });
  }
}

const server = http.createServer(async (req, res) => {
  const startedAt = Date.now();
  if (req.url.startsWith('/api/')) {
    res.on('finish', () => {
      const url = new URL(req.url, `http://${HOST}:${PORT}`);
      console.log(`${new Date().toISOString()} ${req.method || 'GET'} ${url.pathname} ${res.statusCode} ${Date.now() - startedAt}ms`);
    });
  }
  try {
    if (req.url.startsWith('/api/')) {
      await handleApi(req, res);
      return;
    }
    if (req.url.startsWith('/oauth/callback')) {
      await handleOAuthCallback(req, res);
      return;
    }
    serveStatic(req, res);
  } catch (error) {
    sendJson(res, error.status || 500, { ok: false, error: toChineseError(error), details: safeDetails(error) });
  }
});

server.listen(PORT, HOST, () => {
  console.log(`${PRODUCT_DISPLAY_NAME}已启动：http://${HOST}:${PORT}`);
  setImmediate(() => resumePersistedExecutionSubmissions());
  scheduleLowFrequencyActivityCalibration();
});

function scheduleLowFrequencyActivityCalibration() {
  const next = nextNonPeakCalibrationAt();
  const timer = setTimeout(async () => {
    try {
      await runLowFrequencyActivityCalibration();
    } catch (error) {
      console.error(`活动缓存低频校准未完成：${toChineseError(error)}`);
    } finally {
      scheduleLowFrequencyActivityCalibration();
    }
  }, Math.max(1_000, next.getTime() - Date.now()));
  timer.unref?.();
}

async function runLowFrequencyActivityCalibration() {
  const settings = readSettings();
  for (const summary of listAccountsForUi()) {
    const account = await ensureUsableAccount(summary.account_id);
    const catalog = await refreshActivityCatalogForPrepare({ account, settings, readConcurrency: Math.min(2, normalizeConcurrency(settings.readConcurrency)) });
    const promotions = listOperatingCampaignsFiltered(account.account_id, {}, settings)
      .filter((promotion) => !catalog.blocked_site_ids.has(String(promotion.site_id || '').toUpperCase()));
    await mapLimited(promotions, 2, async (campaign) => {
      for (const status of ['candidate', 'started']) {
        const cacheState = getActivityCacheState(account.account_id, campaign.site_id, campaign.promotion_id, campaign.promotion_type);
        const fetchState = getItemFetchState(account.account_id, campaign.promotion_id, campaign.promotion_type, status);
        if (!activityItemsDecision({ promotion: campaign, cacheState, fetchState }).refresh) continue;
        await fetchAndSavePromotionItemsForCampaign({ account, campaign, status, maxItems: 'all', fetchMode: 'full' });
      }
    });
  }
}

async function resumePersistedExecutionSubmissions() {
  for (const persisted of loadAllEffectiveSubmissions({
    store: submissionPersistence,
    onAbort: (prepare) => abortExecutionSubmissionCommit(prepare.id, 'commit_lease_expired'),
  })) {
    let prepare = persisted;
    if (!prepare) continue;
    if (prepare.state === 'preparing') {
      scheduleExecutionSubmissionPreparation(prepare.id);
      continue;
    }
    if (prepare.state === 'creating') {
      const recovered = await recoverCreatingSubmission(prepare);
      if (!recovered) continue;
      prepare = recovered;
    }
    if (!['committing', 'created', 'starting'].includes(prepare.state) || !prepare.commit_confirmed) continue;
    scheduleExecutionSubmissionCommit(prepare.id, {
        confirmText: 'REAL_SUBMIT',
        createConfirmText: prepare.create_confirmed ? 'CREATE_SELLER_CAMPAIGN' : '',
      }, { recover: true }).catch((error) => {
        console.error(`恢复已确认提交未完成：${submissionApiErrorMessage(error)}`);
      });
  }
}

async function recoverCreatingSubmission(prepare) {
  const expected = {
    version: prepare.version,
    state: 'creating',
    commit_lease_id: prepare.commit_lease_id || '',
  };
  try {
    await buildExecutionSubmissionSnapshot(prepare.request);
    const latest = submissionPersistence.load(prepare.id);
    if (!latest
        || latest.version !== expected.version
        || latest.state !== expected.state
        || String(latest.commit_lease_id || '') !== String(expected.commit_lease_id || '')) return null;
    const selected = prepare.seller_input?.selected_targets || [];
    const missing = selected.filter((target) => {
      const campaigns = listOperatingCampaignsFiltered(target.account_id, {
        siteIds: [target.site_id],
        promotionTypes: ['SELLER_CAMPAIGN'],
        sellerActivityNames: prepare.seller_input?.name ? [prepare.seller_input.name] : [],
      }, readSettings());
      return campaigns.length === 0;
    });
    if (missing.length) {
      submissionPersistence.compareAndSwap(prepare.id, expected, {
        state: 'failed',
        error: '程序重启后未能回查到全部已请求创建的自建活动，已阻断商品执行且不会重复创建。',
        failure_code: 'SELLER_CREATION_RECHECK_REQUIRED',
        commit_lease_id: null,
        commit_lease_owner: null,
        commit_lease_expires_at: null,
      });
      return null;
    }
    const recovered = submissionPersistence.compareAndSwap(prepare.id, expected, {
      state: 'created',
      creation_result: { ok: true, recovered_by_live_recheck: true, created_count: selected.length, failed_count: 0, recheck_missing_count: 0 },
      commit_lease_id: null,
      commit_lease_owner: null,
      commit_lease_expires_at: null,
    });
    return recovered.ok ? recovered.prepare : null;
  } catch (error) {
    submissionPersistence.compareAndSwap(prepare.id, expected, {
      state: 'failed',
      error: `程序重启后无法确认活动创建结果，已阻断商品执行且不会重复创建：${toChineseError(error)}`,
      failure_code: 'SELLER_CREATION_RECHECK_FAILED',
      commit_lease_id: null,
      commit_lease_owner: null,
      commit_lease_expires_at: null,
    });
    return null;
  }
}

async function handleApi(req, res) {
  const url = new URL(req.url, `http://${HOST}:${PORT}`);
  const method = req.method || 'GET';

  if (method === 'GET' && url.pathname === '/api/health') {
    return sendJson(res, 200, {
      ok: true,
      service: PRODUCT_DISPLAY_NAME,
      display_name: PRODUCT_DISPLAY_NAME,
      product: PRODUCT_BUILD_INFO.product,
      protocol_version: PRODUCT_BUILD_INFO.protocol_version,
      build_fingerprint: PRODUCT_BUILD_INFO.build_fingerprint,
      time: new Date().toISOString(),
    });
  }

  if (method === 'POST' && LEGACY_SYNC_WRITE_ROUTES.has(url.pathname)) {
    return sendJson(res, 410, { ok: false, error: LEGACY_SYNC_WRITE_MESSAGE, retired: true });
  }

  if (method === 'POST' && url.pathname === '/api/integrations/activity-callback') {
    const result = await activityCallbackAdapter.accept(await readJson(req), req.headers['x-mdm-signature']);
    if (result.status === 'disabled') return sendJson(res, 404, { ok: false, error: '活动变化回调接入未启用。', callback_enabled: false });
    return sendJson(res, result.status === 'duplicate' ? 200 : 202, { ok: true, ...result });
  }

  if (method === 'GET' && url.pathname === '/api/settings') {
    return sendJson(res, 200, { ok: true, settings: readSettings() });
  }

  if (method === 'GET' && url.pathname === '/api/concurrency-benchmark/results') {
    return sendJson(res, 200, { ok: true, results: readConcurrencyBenchmarkResults() });
  }

  if (method === 'POST' && url.pathname === '/api/concurrency-benchmark/read') {
    const body = await readJson(req);
    const account = await ensureUsableAccount(body.accountId || (await defaultAccountId()));
    const result = await runReadConcurrencyBenchmark({ account, input: body });
    saveConcurrencyBenchmarkResult('read', result);
    return sendJson(res, 200, { ok: true, result });
  }

  if (method === 'POST' && url.pathname === '/api/concurrency-benchmark/write/plan') {
    const body = await readJson(req);
    const account = await ensureUsableAccount(body.accountId || (await defaultAccountId()));
    const plan = buildWriteConcurrencyBenchmarkPlan({ account, input: body });
    saveConcurrencyBenchmarkResult('write_plan', plan);
    return sendJson(res, 409, { ok: false, disabled: true, confirmation_required: true, plan });
  }

  if (method === 'POST' && url.pathname === '/api/concurrency-benchmark/write/jobs/start') {
    const body = await readJson(req);
    const unfinished = findUnfinishedWriteBenchmarkJob();
    if (unfinished && !body.allowUnfinishedOverride) {
      return sendJson(res, 409, {
        ok: false,
        error: '存在未完成的真实写入压测任务，请先查询、停止或标记遗留未知后再启动新任务。',
        unfinished_job: publicWriteBenchmarkJob(unfinished)
      });
    }
    const account = await ensureUsableAccount(body.accountId || (await defaultAccountId()));
    const job = createWriteBenchmarkJob({ account, input: body });
    runWriteBenchmarkJob(job.id).catch((error) => {
      const current = writeBenchmarkJobs.get(job.id);
      if (!current) return;
      current.status = 'failed';
      current.finished_at = new Date().toISOString();
      current.error = toChineseError(error);
      appendWriteBenchmarkJobEvent(current, { type: 'job_failed', error: current.error });
      persistWriteBenchmarkJob(current);
    });
    return sendJson(res, 202, { ok: true, jobId: job.id, job: publicWriteBenchmarkJob(job) });
  }

  if (method === 'POST' && url.pathname === '/api/concurrency-benchmark/write/jobs/mark-legacy-unknown') {
    const body = await readJson(req);
    const job = createLegacyUnknownWriteBenchmarkJob(body);
    return sendJson(res, 200, { ok: true, job: publicWriteBenchmarkJob(job) });
  }

  const writeBenchmarkStopMatch = url.pathname.match(/^\/api\/concurrency-benchmark\/write\/jobs\/([^/]+)\/stop$/);
  if (method === 'POST' && writeBenchmarkStopMatch) {
    const job = loadWriteBenchmarkJob(writeBenchmarkStopMatch[1]);
    if (!job) return sendJson(res, 404, { ok: false, error: '未找到写入并发压测任务。' });
    job.cancel_requested = true;
    if (job.status === 'legacy_unknown' || (job.persisted_only && ['queued', 'running', 'stopping'].includes(job.status))) {
      job.status = 'cancelled';
      job.finished_at = new Date().toISOString();
    } else if (['queued', 'running'].includes(job.status)) {
      job.status = 'stopping';
    }
    appendWriteBenchmarkJobEvent(job, { type: 'stop_requested' });
    persistWriteBenchmarkJob(job);
    return sendJson(res, 200, { ok: true, job: publicWriteBenchmarkJob(job) });
  }

  const writeBenchmarkRecheckMatch = url.pathname.match(/^\/api\/concurrency-benchmark\/write\/jobs\/([^/]+)\/recheck$/);
  if (method === 'POST' && writeBenchmarkRecheckMatch) {
    const body = await readJson(req);
    const job = loadWriteBenchmarkJob(writeBenchmarkRecheckMatch[1]);
    if (!job) return sendJson(res, 404, { ok: false, error: '未找到写入并发压测任务。' });
    const result = await recheckPersistedWriteBenchmarkJob(job, body);
    return sendJson(res, 200, { ok: true, job: publicWriteBenchmarkJob(job), result });
  }

  const writeBenchmarkItemsMatch = url.pathname.match(/^\/api\/concurrency-benchmark\/write\/jobs\/([^/]+)\/items$/);
  if (method === 'GET' && writeBenchmarkItemsMatch) {
    const job = loadWriteBenchmarkJob(writeBenchmarkItemsMatch[1]);
    if (!job) return sendJson(res, 404, { ok: false, error: '未找到写入并发压测任务。' });
    const limit = Math.max(1, Math.min(5000, Math.floor(Number(url.searchParams.get('limit') || 500))));
    const events = readWriteBenchmarkJobEvents(job.id, limit);
    return sendJson(res, 200, { ok: true, job: publicWriteBenchmarkJob(job), events, items: writeBenchmarkItemsFromEvents(events) });
  }

  const writeBenchmarkJobMatch = url.pathname.match(/^\/api\/concurrency-benchmark\/write\/jobs\/([^/]+)$/);
  if (method === 'GET' && writeBenchmarkJobMatch) {
    const job = loadWriteBenchmarkJob(writeBenchmarkJobMatch[1]);
    if (!job) return sendJson(res, 404, { ok: false, error: '未找到写入并发压测任务。' });
    return sendJson(res, 200, { ok: true, job: publicWriteBenchmarkJob(job), recent_events: readWriteBenchmarkJobEvents(job.id, 100) });
  }

  if (method === 'GET' && url.pathname === '/api/promotion-creation/status') {
    return sendJson(res, 200, { ok: true, creation: PROMOTION_CREATION_STATUS });
  }

  if (method === 'GET' && url.pathname === '/api/real-enroll-smoke/target') {
    return sendJson(res, 200, {
      ok: true,
      enabled: false,
      release_policy: REAL_ENROLL_SMOKE_POLICY,
      targets: listRealEnrollSmokeTargets(),
      confirmation_package: buildRealEnrollSmokeConfirmation()
    });
  }

  if (method === 'POST' && url.pathname === '/api/real-enroll-smoke/precheck') {
    const body = await readJson(req);
    return sendJson(res, 409, {
      ok: false,
      confirmation_required: true,
      error: '固定 4 商品真实报名冒烟本轮只生成预检包，不执行 Mercado POST。',
      confirmation_package: buildRealEnrollSmokeConfirmation(body)
    });
  }

  if (method === 'POST' && url.pathname === '/api/promotion-creation/precheck') {
    const body = await readJson(req);
    return sendJson(res, 409, {
      ok: false,
      confirmation_required: true,
      error: 'Seller Campaign 创建活动本轮只生成请求预览和主管确认包，不执行 Mercado POST。',
      confirmation_package: buildSellerCampaignCreateConfirmation(body)
    });
  }

  if (method === 'POST' && url.pathname === '/api/promotion-creation/seller-campaign/batch-precheck') {
    const body = await readJson(req);
    const accountIds = normalizeAccountIdList(body.accountIds || body.account_ids || body.accountId || body.account_id);
    if (!accountIds.length) return sendJson(res, 400, { ok: false, error: '缺少店铺账号范围。' });
    const filters = body.filters || {};
    const settings = readSettings();
    const targets = await buildLiveSellerCampaignCreateTargets({ accountIds, filters, settings });
    const hasTargetSelections = hasSellerCampaignTargetSelections(body);
    const selectedTargets = filterSellerCampaignCreateTargets(targets, normalizeSellerCampaignTargetSelections(body), hasTargetSelections);
    const notSelectedTargets = sellerCampaignNotSelectedTargets(targets, selectedTargets, hasTargetSelections);
    const precheck = buildSellerCampaignBatchCreatePrecheck({
      targets: selectedTargets,
      name: body.name,
      startDate: body.startDate ?? body.start_date,
      finishDate: body.finishDate ?? body.finish_date
    });
    return sendJson(res, 200, { ...precheck, deprecated: true });
  }

  if (method === 'POST' && url.pathname === '/api/promotion-creation/seller-campaign/batch-create') {
    const body = await readJson(req);
    if (String(body.confirmText || body.confirm_text || '') !== 'CREATE_SELLER_CAMPAIGN') {
      return sendJson(res, 409, {
        ok: false,
        confirmation_required: true,
        error: '真实创建自建活动前必须确认目标清单。'
      });
    }
    const accountIds = normalizeAccountIdList(body.accountIds || body.account_ids || body.accountId || body.account_id);
    if (!accountIds.length) return sendJson(res, 400, { ok: false, error: '缺少店铺账号范围。' });
    const filters = body.filters || {};
    const settings = readSettings();
    const targets = await buildLiveSellerCampaignCreateTargets({ accountIds, filters, settings });
    const hasTargetSelections = hasSellerCampaignTargetSelections(body);
    const selectedTargets = filterSellerCampaignCreateTargets(targets, normalizeSellerCampaignTargetSelections(body), hasTargetSelections);
    const notSelectedTargets = sellerCampaignNotSelectedTargets(targets, selectedTargets, hasTargetSelections);
    const precheck = buildSellerCampaignBatchCreatePrecheck({
      targets: selectedTargets,
      name: body.name,
      startDate: body.startDate ?? body.start_date,
      finishDate: body.finishDate ?? body.finish_date
    });
    if ((precheck.validation_errors || []).length) {
      return sendJson(res, 400, {
        ok: false,
        promotion_type: 'SELLER_CAMPAIGN',
        error: '自建活动创建参数未通过预检。',
        validation_errors: precheck.validation_errors,
        existing: publicSellerCampaignTargets(precheck.existing),
        missing: publicSellerCampaignTargets(precheck.confirmed_absent || []),
        confirmed_absent: publicSellerCampaignTargets(precheck.confirmed_absent || []),
        needs_manual_review: publicSellerCampaignTargets(precheck.needs_manual_review || []),
        unreadable: precheck.unreadable || []
      });
    }
    const result = await createMissingSellerCampaigns({
      missingTargets: precheck.confirmed_absent || [],
      existingTargets: precheck.existing,
      notSelectedTargets,
      unreadableTargets: precheck.unreadable || [],
      name: body.name,
      startDate: body.startDate ?? body.start_date,
      finishDate: body.finishDate ?? body.finish_date
    });
    return sendJson(res, (result.failed_count > 0 || result.recheck_missing_count > 0 || result.unreadable_count > 0) ? 207 : 200, { ...result, deprecated: true });
  }

  if (method === 'GET' && url.pathname === '/api/candidate-incomplete/status') {
    return sendJson(res, 200, { ok: true, resolution: buildCandidateIncompleteResolution() });
  }

  if (method === 'GET' && url.pathname === '/api/smart-real-test/target') {
    return sendJson(res, 200, {
      ok: true,
      target: SMART_REAL_TEST_TARGET,
      release_policy: SMART_REAL_TEST_RELEASE_POLICY,
      confirmation_package: buildSingleItemRealTestConfirmation(SMART_REAL_TEST_TARGET)
    });
  }

  if (method === 'POST' && url.pathname === '/api/smart-real-test/confirmation') {
    const body = await readJson(req);
    return sendJson(res, 409, {
      ok: false,
      confirmation_required: true,
      error: 'SMART 单商品真实验证本轮只生成最终确认包，release code 未启用，不执行 Mercado 写接口。',
      confirmation_package: buildSingleItemRealTestConfirmation(body)
    });
  }

  if (method === 'POST' && url.pathname === '/api/settings') {
    const body = await readJson(req);
    return sendJson(res, 200, { ok: true, settings: saveSettings(body) });
  }

  if (method === 'POST' && url.pathname === '/api/oauth/start') {
    const body = await readJson(req);
    requireFields(body, ['clientId', 'clientSecret', 'redirectUri']);
    const { verifier, challenge } = createPkcePair();
    const state = createState();
    const authDomain = body.authDomain || DEFAULT_AUTH_DOMAIN;
    clearOAuthStates();
    saveOAuthState({
      state,
      clientId: body.clientId,
      clientSecret: body.clientSecret,
      redirectUri: body.redirectUri,
      authDomain,
      codeVerifier: verifier,
      codeChallenge: challenge
    });
    return sendJson(res, 200, {
      ok: true,
      authorizationUrl: buildAuthorizationUrl({
        authDomain,
        clientId: body.clientId,
        redirectUri: body.redirectUri,
        state,
        codeChallenge: challenge
      })
    });
  }

  if (method === 'POST' && url.pathname === '/api/oauth/start/from-config') {
    const { verifier, challenge } = createPkcePair();
    const state = createState();
    const standaloneConfig = readStandaloneConfig();
    const standaloneToken = readStandaloneToken();
    const prepared = prepareOAuthStartFromConfig(standaloneConfig, {
      pkce: { verifier, challenge },
      state,
      tokenRedirectUri: standaloneToken?.redirect_uri
    });
    clearOAuthStates();
    saveOAuthState(prepared.stateRecord);
    return sendJson(res, 200, prepared.response);
  }

  if (method === 'POST' && url.pathname === '/api/oauth/complete-callback') {
    const body = await readJson(req);
    const { code, state } = parseOAuthCallbackInput(
      body.callbackUrl || body.url || body.code || body.authorizationCode,
      body.state,
      resolveRecentOAuthStateForCodeOnly
    );
    const account = await completeOAuthAuthorization({ code, state });
    return sendJson(res, 200, { ok: true, account: publicAccount(account) });
  }

  if (method === 'GET' && url.pathname === '/api/accounts') {
    return sendJson(res, 200, { ok: true, accounts: listAccountsForUi() });
  }

  if (method === 'GET' && url.pathname === '/api/accounts/profiles/refresh') {
    const requested = normalizeAccountIdList(url.searchParams.get('accountIds') || url.searchParams.get('accountId'));
    const accountIds = requested.length ? requested : listAccountsForUi().map((account) => String(account.account_id));
    const results = await mapLimited(accountIds, Math.min(3, Math.max(1, accountIds.length)), async (accountId) => {
      try {
        const account = await refreshAccountProfile(accountId);
        return { account_id: accountId, ok: true, account };
      } catch (error) {
        return { account_id: accountId, ok: false, error: toChineseError(error) };
      }
    });
    return sendJson(res, 200, {
      ok: true,
      refreshed: results.filter((row) => row.ok).length,
      failed: results.filter((row) => !row.ok).length,
      results,
      accounts: listAccountsForUi()
    });
  }

  const verifyAccountMatch = url.pathname.match(/^\/api\/accounts\/([^/]+)\/verify$/);
  if (method === 'POST' && verifyAccountMatch) {
    const account = await ensureUsableAccount(verifyAccountMatch[1]);
    return sendJson(res, 200, {
      ok: true,
      account: publicAccount(account),
      refreshed: false
    });
  }

  const refreshMatch = url.pathname.match(/^\/api\/accounts\/([^/]+)\/refresh$/);
  if (method === 'POST' && refreshMatch) {
    const standalone = getStandaloneSecrets();
    if (standalone && String(standalone.account_id) === String(refreshMatch[1])) {
      const refresh = refreshStandaloneToken({ force: true });
      const account = await ensureStandaloneUsable(getStandaloneSecrets());
      return sendJson(res, 200, { ok: true, account: publicAccount(account), refresh });
    }
    const account = getAccountSecrets(refreshMatch[1]);
    if (!account?.refreshToken) return sendJson(res, 404, { ok: false, error: '未找到可刷新的授权账号' });
    const client = new MercadoLibreClient();
    const token = await client.refreshToken({
      clientId: account.client_id,
      clientSecret: account.clientSecret,
      refreshToken: account.refreshToken
    });
    updateAccountToken(account.account_id, token);
    return sendJson(res, 200, { ok: true, account: getAccount(account.account_id) });
  }

  const fetchPromosMatch = url.pathname.match(/^\/api\/accounts\/([^/]+)\/promotions\/fetch$/);
  if (method === 'POST' && fetchPromosMatch) {
    const account = await ensureUsableAccount(fetchPromosMatch[1]);
    const result = await fetchAndSavePromotions(account);
    return sendJson(res, 200, {
      ok: true,
      total: result.total,
      children: result.children,
      promotions: listCampaigns(account.account_id)
    });
  }

  const listPromosMatch = url.pathname.match(/^\/api\/accounts\/([^/]+)\/promotions$/);
  if (method === 'GET' && listPromosMatch) {
    return sendJson(res, 200, { ok: true, promotions: listOperatingCampaignsFiltered(listPromosMatch[1], queryFiltersFromSearchParams(url.searchParams)) });
  }

  const sitesMatch = url.pathname.match(/^\/api\/accounts\/([^/]+)\/sites$/);
  if (method === 'GET' && sitesMatch) {
    const account = await ensureUsableAccount(sitesMatch[1]);
    if (account.site_id === 'CBT') await discoverAndSaveMarketplaceSites(account);
    const settings = readSettings();
    const allSites = listSiteSummaries(sitesMatch[1]);
    const includeAll = url.searchParams.get('includeAll') === 'true' || url.searchParams.get('includeAll') === '1';
    if (includeAll && (url.searchParams.get('probeBusiness') === 'true' || url.searchParams.get('probeBusiness') === '1')) {
      const evidence = await inspectOperatingSiteEvidence({ account, sites: allSites, settings });
      return sendJson(res, 200, { ok: true, configured: hasOperatingSiteScope(settings, sitesMatch[1]), sites: evidence });
    }
    const sites = includeAll ? allSites : filterByOperatingSites(allSites, settings, sitesMatch[1]);
    const allowed = operatingSiteIds(settings, sitesMatch[1]);
    return sendJson(res, 200, {
      ok: true,
      configured: allowed !== null,
      sites: sites.map((site) => ({
        ...site,
        operating: allowed === null || allowed.has(String(site.site_id || '').toUpperCase())
      }))
    });
  }

  const fetchItemsMatch = url.pathname.match(/^\/api\/accounts\/([^/]+)\/promotions\/([^/]+)\/([^/]+)\/items\/fetch$/);
  if (method === 'POST' && fetchItemsMatch) {
    const body = await readJson(req);
    const account = await ensureUsableAccount(fetchItemsMatch[1]);
    const promotionId = decodeURIComponent(fetchItemsMatch[2]);
    const promotionType = decodeURIComponent(fetchItemsMatch[3]);
    const campaign = getCampaign(account.account_id, promotionId, promotionType);
    const status = requireItemStatus(body.status || url.searchParams.get('status') || 'candidate');
    const targetUserId = campaign?.child_user_id || account.account_id;
    const client = new MercadoLibreClient({
      accessToken: account.accessToken,
      userId: targetUserId,
      callerId: targetUserId,
      marketplace: isMarketplaceCampaign(account, campaign)
    });
    const fetchMode = body.fetchMode === 'full' ? 'full' : 'sample';
    const result = await client.fetchAllPromotionItems({ promotionId, promotionType, status, maxItems: fetchMode === 'full' ? 'all' : Number(body.maxItems || 5000) });
    saveItems(account.account_id, promotionId, promotionType, result.results, {
      childUserId: campaign?.child_user_id,
      siteId: campaign?.site_id,
      logisticType: campaign?.logistic_type,
      replaceStatus: status,
      itemStatus: status
    });
    saveItemFetchState({
      accountId: account.account_id,
      promotionId,
      promotionType,
      itemStatus: status,
      platformTotal: result.total,
      savedCount: result.saved,
      detailStatus: result.detailStatus,
      warning: result.warning,
      raw: result.rawSummary
    });
    return sendJson(res, 200, {
      ok: true,
      status,
      fetchMode,
      sample_only: result.sampleOnly,
      is_full_fetch: result.isFullFetch,
      total: result.total,
      saved: result.saved,
      detail_status: result.detailStatus,
      warning: result.warning,
      blocked: result.blocked,
      fetch_stats: fetchStatsFromRaw(result.rawSummary),
      items: listItems(account.account_id, promotionId, promotionType, status)
    });
  }

  const listItemsMatch = url.pathname.match(/^\/api\/accounts\/([^/]+)\/promotions\/([^/]+)\/([^/]+)\/items$/);
  if (method === 'GET' && listItemsMatch) {
    const status = url.searchParams.get('status') || null;
    if (status) requireItemStatus(status);
    return sendJson(res, 200, {
      ok: true,
      items: listItems(listItemsMatch[1], decodeURIComponent(listItemsMatch[2]), decodeURIComponent(listItemsMatch[3]), status)
    });
  }

  const manualImportMatch = url.pathname.match(/^\/api\/accounts\/([^/]+)\/promotions\/([^/]+)\/([^/]+)\/candidate\/manual-import$/);
  if (method === 'POST' && manualImportMatch) {
    const body = await readJson(req);
    const accountId = decodeURIComponent(manualImportMatch[1]);
    const promotionId = decodeURIComponent(manualImportMatch[2]);
    const promotionType = decodeURIComponent(manualImportMatch[3]);
    const itemIds = parseManualCandidateItemIds(body.itemIds || body.itemIdText || body.text || '');
    if (!itemIds.length) {
      const error = new Error('请提供 candidate item_id 列表');
      error.status = 400;
      throw error;
    }
    const campaign = getCampaign(accountId, promotionId, promotionType) || { promotion_id: promotionId, promotion_type: promotionType };
    const rows = buildManualCandidateDraftRows({ itemIds, status: 'candidate' });
    saveItems(accountId, promotionId, promotionType, rows, {
      childUserId: campaign.child_user_id,
      siteId: campaign.site_id,
      logisticType: campaign.logistic_type,
      replaceStatus: 'candidate',
      itemStatus: 'candidate'
    });
    saveItemFetchState({
      accountId,
      promotionId,
      promotionType,
      itemStatus: 'candidate',
      platformTotal: rows.length,
      savedCount: rows.length,
      detailStatus: 'manual_candidate_import_needs_detail',
      warning: '已保存人工导入 candidate item_id 草案；必须通过只读商品详情或已有 items 数据补齐价格、状态和边界后，才允许生成预检。',
      raw: {
        source: 'manual_candidate_import',
        requires_readonly_detail: true,
        item_count: rows.length
      }
    });
    return sendJson(res, 200, {
      ok: true,
      status: 'manual_candidate_import_needs_detail',
      source: 'manual_candidate_import',
      imported: rows.length,
      can_real_enroll: false,
      requirement: '仅保存本地草案；缺少价格明细时计划会跳过，真实报名仍需预检包和主管最终确认门。',
      resolution: buildCandidateIncompleteResolution({ promotionId, promotionType, platformTotal: rows.length }),
      items: listItems(accountId, promotionId, promotionType, 'candidate')
    });
  }

  if (method === 'POST' && url.pathname === '/api/plan') {
    const body = await readJson(req);
    const { accountId, promotionId, promotionType, action } = requireFields(body, ['accountId', 'promotionId', 'promotionType', 'action']);
    const campaign = getCampaign(accountId, promotionId, promotionType) || { promotion_id: promotionId, promotion_type: promotionType };
    const itemStatus = requireItemStatus(body.status || actionDefaultStatus(action));
    const fetchState = getItemFetchState(accountId, promotionId, promotionType, itemStatus);
    if (action === 'enroll' && CANDIDATE_INCOMPLETE_STATUSES.has(fetchState?.detail_status)) {
      return sendJson(res, 200, {
        ok: true,
        blocked: true,
        detail_status: fetchState.detail_status,
        warning: fetchState.warning || '平台返回候选总数但未返回候选明细，需要接口专项处理',
        plan: { promotion: campaign, action, total: 0, planned: 0, skipped: 0, rows: [] }
      });
    }
    const items = body.items?.length ? body.items : listItems(accountId, promotionId, promotionType, body.status);
    const plan = buildPlan({
      action,
      promotion: campaign,
      items,
      priceMode: body.priceMode || 'discount',
      discountPercent: body.discountPercent,
      directPrice: body.directPrice,
      skipSamePrice: body.skipSamePrice !== false
    });
    const taskId = createTask({
      accountId,
      promotionId,
      promotionType,
      action,
      mode: 'dry-run',
      discountPercent: plan.discountPercent,
      directPrice: plan.directPrice,
      plan
    });
    savePlanResults({ taskId, accountId, promotionId, promotionType, action, mode: 'dry-run', plan });
    return sendJson(res, 200, { ok: true, taskId, plan });
  }

  if (method === 'POST' && url.pathname === '/api/batch/items/fetch') {
    const body = await readJson(req);
    const { accountId } = requireFields(body, ['accountId']);
    const settings = readSettings();
    const account = await ensureUsableAccount(accountId);
    const promotions = listOperatingCampaignsFiltered(account.account_id, body.filters || {}, settings);
    const status = requireItemStatus(body.itemStatus || 'candidate');
    const fetchMode = body.fetchMode === 'full' ? 'full' : 'sample';
    const maxItems = fetchMode === 'full' ? 'all' : Number(body.maxItems || settings.maxItemsPerPromotion || 50);
    const readConcurrency = normalizeConcurrency(body.readConcurrency ?? settings.readConcurrency);
    const rows = await mapLimited(promotions, readConcurrency, async (campaign) => (
      fetchAndSavePromotionItemsForCampaign({ account, campaign, status, maxItems, fetchMode })
    ));
    return sendJson(res, 200, { ok: true, status, fetchMode, sample_only: fetchMode !== 'full', promotions: rows.length, readConcurrency, rows });
  }

  if (method === 'POST' && url.pathname === '/api/batch/items/fetch/start') {
    const body = await readJson(req);
    const { accountId } = requireFields(body, ['accountId']);
    const settings = readSettings();
    const status = requireItemStatus(body.itemStatus || 'candidate');
    const fetchMode = body.fetchMode === 'sample' ? 'sample' : 'full';
    const readConcurrency = normalizeConcurrency(body.readConcurrency ?? settings.readConcurrency);
    const job = createBatchFetchJob({ accountId, filters: body.filters || {}, itemStatus: status, fetchMode, readConcurrency });
    runBatchFetchJob(job.id, { accountId, filters: body.filters || {}, itemStatus: status, fetchMode, readConcurrency, maxItems: fetchMode === 'full' ? 'all' : Number(body.maxItems || settings.maxItemsPerPromotion || 50) });
    return sendJson(res, 202, { ok: true, job });
  }

  const batchFetchJobMatch = url.pathname.match(/^\/api\/batch\/items\/fetch\/jobs\/([^/]+)$/);
  if (method === 'GET' && batchFetchJobMatch) {
    const job = batchFetchJobs.get(batchFetchJobMatch[1]);
    if (!job) return sendJson(res, 404, { ok: false, error: '未找到全量读取任务' });
    return sendJson(res, 200, { ok: true, job });
  }

  if (method === 'POST' && url.pathname === '/api/inventory-fallback/seller-campaign/start') {
    const body = await readJson(req);
    const { accountId } = requireFields(body, ['accountId']);
    const settings = readSettings();
    const readConcurrency = normalizeConcurrency(body.readConcurrency ?? settings.readConcurrency);
    const detailConcurrency = normalizeConcurrency(body.detailConcurrency ?? settings.readConcurrency);
    const listingStatus = body.listingStatus || 'all';
    const maxScanItems = body.maxScanItems || 'all';
    const job = createInventoryFallbackJob({ accountId, filters: body.filters || {}, listingStatus, readConcurrency, detailConcurrency, maxScanItems });
    runInventoryFallbackJob(job.id, {
      accountId,
      filters: body.filters || {},
      listingStatus,
      readConcurrency,
      detailConcurrency,
      sellerDiscountPercent: Number(body.sellerDiscountPercent ?? settings.sellerDefaultDiscount ?? 5),
      maxScanItems
    });
    return sendJson(res, 202, { ok: true, job });
  }

  const inventoryFallbackJobMatch = url.pathname.match(/^\/api\/inventory-fallback\/seller-campaign\/jobs\/([^/]+)$/);
  if (method === 'GET' && inventoryFallbackJobMatch) {
    const job = inventoryFallbackJobs.get(inventoryFallbackJobMatch[1]);
    if (!job) return sendJson(res, 404, { ok: false, error: '未找到库存兜底扫描任务' });
    return sendJson(res, 200, { ok: true, job });
  }

  if (method === 'POST' && url.pathname === '/api/execution/submissions/prepare') {
    const body = await readJson(req);
    try {
      const result = prepareExecutionSubmission(body);
      const status = result.prepare.state === 'preparing' ? 202 : 200;
      return sendJson(res, status, { ok: true, reused: result.reused, prepare_id: result.prepare.id, prepare: publicExecutionSubmission(result.prepare) });
    } catch (error) {
      return sendJson(res, error.status || 500, { ok: false, code: error.code || null, error: submissionApiErrorMessage(error), details: error.details || null });
    }
  }

  if (method === 'POST' && url.pathname === '/api/execution/submissions/same-day-confirmations/cancel') {
    const body = await readJson(req);
    try {
      sameDayConfirmationStore.cancel(body.confirmation_token);
      return sendJson(res, 200, { ok: true, state: 'cancelled' });
    } catch (error) {
      return sendJson(res, error.status || 500, { ok: false, code: error.code || null, error: submissionApiErrorMessage(error) });
    }
  }

  const submissionUpdateMatch = url.pathname.match(/^\/api\/execution\/submissions\/([^/]+)\/seller-input$/);
  if (method === 'POST' && submissionUpdateMatch) {
    const body = await readJson(req);
    try {
      const prepare = updateExecutionSubmissionSellerInput(submissionUpdateMatch[1], body);
      return sendJson(res, 200, { ok: true, prepare: publicExecutionSubmission(prepare) });
    } catch (error) {
      return sendJson(res, error.status || 500, { ok: false, code: error.code || null, error: toChineseError(error), details: error.details || null });
    }
  }

  const submissionCommitMatch = url.pathname.match(/^\/api\/execution\/submissions\/([^/]+)\/commit$/);
  if (method === 'POST' && submissionCommitMatch) {
    const body = await readJson(req);
    try {
      const result = await commitExecutionSubmission(submissionCommitMatch[1], body);
      return sendJson(res, result.reused ? 200 : 202, {
        ok: true,
        accepted: result.accepted === true,
        reused: result.reused,
        prepare: publicExecutionSubmission(result.prepare),
        group: result.group ? publicExecutionGroup(result.group) : null,
      });
    } catch (error) {
      const current = submissionPersistence.load(submissionCommitMatch[1]);
      return sendJson(res, error.status || 500, {
        ok: false,
        code: error.code || null,
        error: submissionApiErrorMessage(error),
        details: error.details || null,
        prepare_id: current?.id || submissionCommitMatch[1],
        state: current?.state || null,
      });
    }
  }

  const submissionCancelMatch = url.pathname.match(/^\/api\/execution\/submissions\/([^/]+)\/cancel$/);
  if (method === 'POST' && submissionCancelMatch) {
    const prepare = loadEffectiveSubmission({ store: submissionPersistence, prepareId: submissionCancelMatch[1] });
    if (!prepare) return sendJson(res, 404, { ok: false, error: '未找到本次准备结果。' });
    if (['preparing', 'prepared', 'reconfirm_required'].includes(String(prepare.state || ''))) {
      const nextState = prepare.state === 'preparing' ? 'cancelled' : 'paused';
      const message = nextState === 'cancelled' ? '用户已停止本次准备。' : '本次准备已暂停，可在有效期内按相同范围恢复。';
      const cancelled = submissionPersistence.update(prepare.id, {
        state: nextState, error: null,
        progress: { ...(prepare.progress || {}), stage: nextState, message },
      });
      return sendJson(res, 200, { ok: true, prepare: publicExecutionSubmission(cancelled) });
    }
    if (['committing', 'creating', 'created', 'starting'].includes(String(prepare.state || '')) && !prepare.group_id) {
      const cancelled = cancelSubmissionCommit({
        store: submissionPersistence,
        prepareId: prepare.id,
        onAbort: () => abortExecutionSubmissionCommit(prepare.id, 'user_cancelled'),
      });
      if (cancelled.changed) return sendJson(res, 200, { ok: true, prepare: publicExecutionSubmission(cancelled.prepare) });
      return sendJson(res, 409, { ok: false, code: 'COMMIT_STATE_CHANGED', error: '提交状态刚刚发生变化，正在重新读取结果。' });
    }
    if (prepare.group_id) {
      const group = executionGroups.get(String(prepare.group_id)) || executionGroupPersistence.load(prepare.group_id);
      if (group) {
        requestExecutionGroupCancel(group);
        return sendJson(res, 200, { ok: true, prepare: publicExecutionSubmission(prepare), group: publicExecutionGroup(group) });
      }
    }
    return sendJson(res, 409, { ok: false, error: '本次提交已进入执行阶段，不能按准备取消。' });
  }

  if (method === 'GET' && url.pathname === '/api/execution/submissions/active') {
    const prepare = loadAllEffectiveSubmissions({
      store: submissionPersistence,
      onAbort: (row) => abortExecutionSubmissionCommit(row.id, 'commit_lease_expired'),
    })
      .filter((row) => ACTIVE_SUBMISSION_STATES.has(String(row.state || '')))
      .sort((left, right) => String(right.updated_at || '').localeCompare(String(left.updated_at || '')))[0] || null;
    return sendJson(res, 200, { ok: true, active: Boolean(prepare), prepare: prepare ? publicExecutionSubmission(prepare) : null });
  }

  const submissionMatch = url.pathname.match(/^\/api\/execution\/submissions\/([^/]+)$/);
  if (method === 'GET' && submissionMatch) {
    const prepare = loadEffectiveSubmission({
      store: submissionPersistence,
      prepareId: submissionMatch[1],
      onAbort: (row) => abortExecutionSubmissionCommit(row.id, 'commit_lease_expired'),
    });
    if (!prepare) return sendJson(res, 404, { ok: false, error: '未找到本次准备结果。' });
    recordSubmissionRecoveryPoll(prepare);
    return sendJson(res, 200, { ok: true, prepare: publicExecutionSubmission(prepare) });
  }

  if (method === 'POST' && url.pathname === '/api/execution/groups/start') {
    return sendJson(res, 410, { ok: false, retired: true, error: '执行组只能由已确认的提交准备启动。' });
  }

  if (method === 'GET' && url.pathname === '/api/execution/groups/active') {
    const group = executionGroupPersistence.active();
    if (group) executionGroups.set(String(group.id), group);
    return sendJson(res, 200, { ok: true, active: Boolean(group), group: group ? publicExecutionGroup(group) : null });
  }

  const executionGroupCancelMatch = url.pathname.match(/^\/api\/execution\/groups\/([^/]+)\/cancel$/);
  if (method === 'POST' && executionGroupCancelMatch) {
    const group = executionGroups.get(executionGroupCancelMatch[1]) || executionGroupPersistence.load(executionGroupCancelMatch[1]);
    if (!group) return sendJson(res, 404, { ok: false, error: '未找到执行组。' });
    requestExecutionGroupCancel(group);
    return sendJson(res, 200, { ok: true, group: publicExecutionGroup(group) });
  }

  const executionGroupMatch = url.pathname.match(/^\/api\/execution\/groups\/([^/]+)$/);
  if (method === 'GET' && executionGroupMatch) {
    const group = executionGroups.get(executionGroupMatch[1]) || executionGroupPersistence.load(executionGroupMatch[1]);
    if (!group) return sendJson(res, 404, { ok: false, error: '未找到执行组。' });
    executionGroups.set(String(group.id), group);
    return sendJson(res, 200, {
      ok: true,
      group: publicExecutionGroup(group, { compact: url.searchParams.get('compact') === '1' })
    });
  }

  if (method === 'POST' && url.pathname === '/api/execution/jobs/start') {
    return sendJson(res, 410, { ok: false, retired: true, error: '单账号执行入口已停用，请使用当前桌面版执行组。' });
  }

  if (method === 'GET' && url.pathname === '/api/execution/jobs/active') {
    const activeStatuses = new Set(['queued', 'running', 'stopping']);
    const execution = [...executionJobs.values()]
      .filter((job) => activeStatuses.has(String(job.status || '').toLowerCase()))
      .map(publicExecutionJob);
    const benchmarks = [...writeBenchmarkJobs.values()]
      .filter((job) => activeStatuses.has(String(job.status || '').toLowerCase()))
      .map(publicWriteBenchmarkJob);
    return sendJson(res, 200, {
      ok: true,
      active: execution.length + benchmarks.length > 0,
      execution_jobs: execution,
      benchmark_jobs: benchmarks,
      execution_groups: [...executionGroups.values()]
        .filter((group) => ACTIVE_EXECUTION_GROUP_STATUSES.has(String(group.status || '')))
        .map(publicExecutionGroup),
    });
  }

  const executionJobCancelMatch = url.pathname.match(/^\/api\/execution\/jobs\/([^/]+)\/cancel$/);
  if (method === 'POST' && executionJobCancelMatch) {
    const job = executionJobs.get(executionJobCancelMatch[1]);
    if (!job) return sendJson(res, 404, { ok: false, error: '未找到执行任务。' });
    if (job.request?.executionGroupId) {
      return sendJson(res, 410, { ok: false, retired: true, error: '组内任务只能通过执行组统一停止。' });
    }
    job.cancel_requested = true;
    appendExecutionJobLog(job, '已收到停止请求，未开始的商品会尽快跳过。');
    appendExecutionJobEvent(job, { type: 'stop_requested', reason: '用户请求停止执行 job。' });
    persistExecutionJob(job);
    return sendJson(res, 200, { ok: true, job: publicExecutionJob(job) });
  }

  const executionJobItemsMatch = url.pathname.match(/^\/api\/execution\/jobs\/([^/]+)\/items$/);
  if (method === 'GET' && executionJobItemsMatch) {
    const jobId = executionJobItemsMatch[1];
    const limit = Number(url.searchParams.get('limit') || 5000);
    const events = readExecutionJobEvents(jobId, limit);
    const items = executionItemsFromEvents(events);
    return sendJson(res, 200, {
      ok: true,
      jobId,
      events,
      items,
      unique_summary: summarizeExecutionUniqueItems(items)
    });
  }

  const executionJobMatch = url.pathname.match(/^\/api\/execution\/jobs\/([^/]+)$/);
  if (method === 'GET' && executionJobMatch) {
    const jobId = executionJobMatch[1];
    const job = executionJobs.get(jobId) || loadPersistedExecutionJob(jobId);
    if (!job) return sendJson(res, 404, { ok: false, error: '未找到执行任务。' });
    executionJobs.set(job.id, job);
    return sendJson(res, 200, { ok: true, job: publicExecutionJob(job) });
  }

  if (method === 'POST' && url.pathname === '/api/batch/plan') {
    const body = await readJson(req);
    const { accountId, action } = requireFields(body, ['accountId', 'action']);
    const settings = readSettings();
    const promotions = listOperatingCampaignsFiltered(accountId, body.filters || {}, settings);
    const itemStatus = requireItemStatus(body.itemStatus || actionDefaultStatus(action));
    const itemsByPromotion = listItemsForPromotions(accountId, promotions, itemStatus);
    const fetchStatesByPromotion = planningFetchStates(accountId, promotions, itemStatus, Boolean(body.allowInventoryFallback));
    const batch = buildBatchPlans({
      action,
      promotions,
      itemsByPromotion,
      fetchStatesByPromotion,
      priceMode: body.priceMode || 'discount',
      sellerDiscountPercent: Number(body.sellerDiscountPercent ?? 5),
      officialDiscountPercent: Number(body.officialDiscountPercent ?? 6),
      directPrice: body.directPrice === null || body.directPrice === undefined || body.directPrice === '' ? null : Number(body.directPrice),
      requireFullFetch: Boolean(body.requireFullFetch),
      sampleOnly: body.sampleOnly !== undefined ? Boolean(body.sampleOnly) : !body.requireFullFetch,
      allowInventoryFallback: Boolean(body.allowInventoryFallback)
    });
    const taskIds = [];
    for (const { promotion, plan } of batch.plans) {
      const taskId = createTask({
        accountId,
        promotionId: promotion.promotion_id,
        promotionType: promotion.promotion_type,
        action,
        mode: 'dry-run',
        discountPercent: plan.discountPercent,
        directPrice: plan.directPrice,
        plan
      });
      savePlanResults({ taskId, accountId, promotionId: promotion.promotion_id, promotionType: promotion.promotion_type, action, mode: 'dry-run', plan });
      taskIds.push(taskId);
    }
    return sendJson(res, 200, { ok: true, itemStatus, taskIds, batch });
  }

  if (method === 'POST' && url.pathname === '/api/cancel/filtered/preview') {
    const body = await readJson(req);
    const { accountId } = requireFields(body, ['accountId']);
    const settings = readSettings();
    const promotions = listOperatingCampaignsFiltered(accountId, body.filters || {}, settings);
    const itemStatus = requireItemStatus(body.itemStatus || 'started');
    const itemsByPromotion = listItemsForPromotions(accountId, promotions, itemStatus);
    const fetchStatesByPromotion = listItemFetchStatesForPromotions(accountId, promotions, itemStatus);
    const batch = buildBatchPlans({
      action: 'cancel',
      promotions,
      itemsByPromotion,
      fetchStatesByPromotion,
      priceMode: 'discount',
      sellerDiscountPercent: settings.sellerDefaultDiscount,
      officialDiscountPercent: settings.officialDefaultDiscount
    });
    const taskIds = saveBatchPlanTasks({ accountId, action: 'cancel', batch });
    return sendJson(res, 200, { ok: true, action: 'cancel', itemStatus, taskIds, batch });
  }

  if (method === 'POST' && url.pathname === '/api/today/decision') {
    const body = await readJson(req);
    const settings = readSettings();
    const accountIds = [...new Set((body.accountIds || [body.accountId]).map(String).filter(Boolean))];
    if (!accountIds.length) requireFields(body, ['accountId']);
    const promotions = accountIds.flatMap((accountId) => listOperatingCampaignsFiltered(accountId, body.filters || settings.defaultFilters, settings));
    return sendJson(res, 200, { ok: true, decision: buildTodayDecisionForScope(accountIds, promotions) });
  }

  if (method === 'POST' && url.pathname === '/api/today/preview') {
    const body = await readJson(req);
    const { accountId } = requireFields(body, ['accountId']);
    const settings = readSettings();
    const promotions = listOperatingCampaignsFiltered(accountId, body.filters || settings.defaultFilters, settings);
    const decision = buildTodayDecision(accountId, promotions);
    const action = decision.action;
    const itemStatus = requireItemStatus(body.itemStatus || actionDefaultStatus(action));
    const itemsByPromotion = listItemsForPromotions(accountId, promotions, itemStatus);
    const fetchStatesByPromotion = listItemFetchStatesForPromotions(accountId, promotions, itemStatus);
    const batch = buildBatchPlans({
      action,
      promotions,
      itemsByPromotion,
      fetchStatesByPromotion,
      priceMode: body.priceMode || 'discount',
      sellerDiscountPercent: Number(body.sellerDiscountPercent ?? settings.sellerDefaultDiscount),
      officialDiscountPercent: Number(body.officialDiscountPercent ?? settings.officialDefaultDiscount),
      directPrice: body.directPrice === null || body.directPrice === undefined || body.directPrice === '' ? null : Number(body.directPrice),
      requireFullFetch: Boolean(body.requireFullFetch),
      sampleOnly: body.sampleOnly !== undefined ? Boolean(body.sampleOnly) : !body.requireFullFetch
    });
    const taskIds = saveBatchPlanTasks({ accountId, action, batch });
    return sendJson(res, 200, { ok: true, decision, itemStatus, taskIds, batch });
  }

  if (method === 'POST' && url.pathname === '/api/cycle/decision') {
    const body = await readJson(req);
    const { accountId, promotionId, promotionType } = requireFields(body, ['accountId', 'promotionId', 'promotionType']);
    const existing = getCycleState(accountId, promotionId, promotionType);
    const lastDiscount = existing?.seller_discount_percent ?? existing?.official_discount_percent;
    const discount = nextDiscountFor({ promotionType, lastDiscount, lastStatus: existing?.status === 'completed' ? 'completed' : existing?.status });
    const startedCount = listItems(accountId, promotionId, promotionType, 'started').length;
    return sendJson(res, 200, {
      ok: true,
      state: existing || null,
      decision: decideCycleAction({ promotionType, currentDiscount: discount, hasStartedItems: startedCount > 0 }),
      startedCount
    });
  }

  if (method === 'GET' && url.pathname === '/api/results') {
    return sendJson(res, 200, { ok: true, results: listResults(Number(url.searchParams.get('limit') || 300)) });
  }

  if (method === 'GET' && url.pathname === '/api/tasks') {
    const includeDetails = ['1', 'true', 'yes'].includes(String(url.searchParams.get('includeDetails') || '').toLowerCase());
    return sendJson(res, 200, {
      ok: true,
      tasks: listTaskSummaries(Number(url.searchParams.get('limit') || 300), { includeDetails })
    });
  }

  if (method === 'GET' && url.pathname === '/api/today/global-discount') {
    const settings = readSettings();
    const tasks = listGlobalDiscountUpdateSummaries(300);
    return sendJson(res, 200, {
      ok: true,
      discount: buildGlobalTodayDiscount({ tasks, settings, today: new Date() })
    });
  }

  if (method === 'GET' && url.pathname === '/api/tasks/details') {
    const rawIds = String(url.searchParams.get('taskIds') || url.searchParams.get('ids') || '');
    const taskIds = rawIds.split(',').map((id) => Number(id.trim())).filter((id) => Number.isInteger(id) && id > 0);
    return sendJson(res, 200, { ok: true, details: listTaskDetails(taskIds) });
  }

  if ((method === 'GET' || method === 'POST') && url.pathname === '/api/smart-cancel/remaining') {
    const body = method === 'POST' ? await readJson(req) : {};
    const accountId = body.accountId || body.account_id || url.searchParams.get('accountId') || url.searchParams.get('account_id') || (await defaultAccountId());
    const source = body.source || url.searchParams.get('source') || null;
    const live = source === 'local' ? false : (body.live ?? url.searchParams.get('live')) !== 'false';
    const account = await ensureUsableAccount(accountId);
    const summary = await buildSmartCancelRemainingSummary({
      account,
      live,
      promotionId: body.promotionId || body.promotion_id || url.searchParams.get('promotionId') || url.searchParams.get('promotion_id'),
      itemId: body.itemId || body.item_id || url.searchParams.get('itemId') || url.searchParams.get('item_id')
    });
    return sendJson(res, 200, { ok: true, ...summary });
  }

  if (method === 'POST' && url.pathname === '/api/smart-cancel/preview') {
    const body = await readJson(req);
    const accountId = body.accountId || body.account_id || (await defaultAccountId());
    const promotionId = body.promotionId || body.promotion_id;
    const promotionType = body.promotionType || body.promotion_type || 'SMART';
    const itemId = body.itemId || body.item_id;
    const account = await ensureUsableAccount(accountId);
    const promotion = getCampaign(account.account_id, promotionId, promotionType);
    if (!promotion) return sendJson(res, 404, { ok: false, error: '未找到 SMART 活动缓存，请先刷新活动。' });
    const items = listItems(account.account_id, promotionId, promotionType, body.status || 'started');
    const item = itemId ? items.find((row) => String(row.item_id) === String(itemId)) : items[0];
    if (!item) return sendJson(res, 404, { ok: false, error: '未找到 SMART started 商品缓存，请先读取 started 商品。' });
    return sendJson(res, 200, {
      ok: true,
      preview: safeSmartCancelPreview({
        account,
        campaign: promotion,
        item,
        marketplace: isMarketplaceCampaign(account, promotion)
      })
    });
  }

  if (method === 'POST' && url.pathname === '/api/smart-cancel/detail') {
    const body = await readJson(req);
    const accountId = body.accountId || body.account_id || (await defaultAccountId());
    const promotionId = body.promotionId || body.promotion_id;
    const promotionType = body.promotionType || body.promotion_type || 'SMART';
    const itemId = body.itemId || body.item_id;
    const account = await ensureUsableAccount(accountId);
    const source = body.source || null;
    const detail = await buildSmartCancelDetail({
      account,
      promotionId,
      promotionType,
      itemId,
      live: source === 'local' ? false : body.live !== false
    });
    return sendJson(res, 200, { ok: true, detail });
  }

  if (method === 'POST' && url.pathname === '/api/tasks/delete') {
    const body = await readJson(req);
    const result = deleteTasks(Array.isArray(body.taskIds) ? body.taskIds : []);
    return sendJson(res, 200, { ok: true, ...result });
  }

  if (method === 'GET' && url.pathname === '/api/export/results.csv') {
    return sendCsv(res, listResults(5000));
  }

  if (method === 'POST' && url.pathname === '/api/export/workspace') {
    const body = await readJson(req);
    const accountId = body.accountId || listAccountsForUi()[0]?.account_id;
    const settings = readSettings();
    const accounts = listAccountsForUi();
    const sites = accountId ? listSiteSummaries(accountId) : [];
    const activities = accountId ? listCampaigns(accountId) : [];
    const results = listResults(5000);
    return sendJson(res, 200, {
      ok: true,
      export: exportWorkspace({
        outputDir: body.outputDir || settings.outputDir,
        accounts,
        sites,
        activities,
        results,
        preview: body.preview || null,
        precheck: body.precheck || null
      })
    });
  }

  sendJson(res, 404, { ok: false, error: '接口不存在' });
}

async function handleOAuthCallback(req, res) {
  const url = new URL(req.url, `http://${HOST}:${PORT}`);
  const code = url.searchParams.get('code');
  const state = url.searchParams.get('state');
  if (!code || !state) return sendHtml(res, 400, '<h1>授权失败</h1><p>缺少 code 或 state。</p>');
  try {
    const completedAccount = await completeOAuthAuthorization({ code, state });
    const storeName = storeIdentityForAccount(completedAccount.account_id, completedAccount).store_name;
    return sendHtml(res, 200, `<h1>授权成功</h1><p>${escapeHtml(storeName)}已保存。可以回到折扣管家继续操作。</p>`);
  } catch (error) {
    return sendHtml(res, error.status || 500, `<h1>授权失败</h1><p>${escapeHtml(error.message || '授权保存失败')}</p>`);
  }
}

async function buildSmartCancelRemainingSummary({ account, live = true, promotionId = null, itemId = null } = {}) {
  const accountId = account?.account_id;
  const campaigns = listCampaigns(accountId).filter((campaign) => {
    if (String(campaign.promotion_type || '').toUpperCase() !== 'SMART') return false;
    if (promotionId && String(campaign.promotion_id) !== String(promotionId)) return false;
    return true;
  });
  const groups = [];
  let totalStarted = 0;
  let totalLocalStarted = 0;
  let totalWithOfferId = 0;
  let totalStaleRemoved = 0;
  for (const campaign of campaigns) {
    const localItems = listItems(accountId, campaign.promotion_id, campaign.promotion_type, 'started');
    const liveSync = live
      ? await fetchAndSyncSmartStarted({ account, campaign, itemId })
      : {
          source: 'local',
          fetch_ok: false,
          items: localItems,
          started_count: localItems.length,
          local_started_count: localItems.length,
          local_stale_count: 0,
          local_stale_item_ids_sample: [],
          target_item_remaining: itemId ? localItems.some((row) => String(row.item_id) === String(itemId)) : null,
          cache_updated_from_live: false,
          errors: []
        };
    const items = liveSync.items;
    if (!items.length && !localItems.length && !promotionId) continue;
    const withOfferId = items.filter((item) => smartCancelFieldEvidence({ promotion: campaign, item }).offer_id).length;
    const withStartedOfferId = items.filter((item) => smartCancelFieldEvidence({ promotion: campaign, item }).offer_id_is_started_offer).length;
    totalStarted += items.length;
    totalLocalStarted += liveSync.local_started_count;
    totalWithOfferId += withOfferId;
    totalStaleRemoved += liveSync.local_stale_count;
    const sample = items[0] ? safeSmartCancelPreview({
      account: { account_id: accountId },
      campaign,
      item: items[0],
      marketplace: true
    }) : null;
    groups.push({
      account_id: String(accountId),
      site_id: campaign.site_id,
      child_user_id: campaign.child_user_id,
      promotion_id: campaign.promotion_id,
      promotion_type: campaign.promotion_type,
      promotion_name: campaign.name,
      source: liveSync.source,
      live_fetch_ok: liveSync.fetch_ok,
      local_started_count: liveSync.local_started_count,
      started_count: items.length,
      live_started_count: liveSync.fetch_ok ? items.length : null,
      local_stale_count: liveSync.local_stale_count,
      local_stale_item_ids_sample: liveSync.local_stale_item_ids_sample,
      cache_updated_from_live: liveSync.cache_updated_from_live,
      target_item_id: itemId || null,
      target_item_remaining: liveSync.target_item_remaining,
      with_offer_id: withOfferId,
      with_started_offer_id: withStartedOfferId,
      can_build_cancel_query: items.length > 0 && withStartedOfferId === items.length,
      sample_preview: sample,
      errors: liveSync.errors
    });
  }
  return {
    account_id: String(accountId),
    source: live ? 'live' : 'local',
    policy: SMART_CANCEL_POLICY,
    total_started: totalStarted,
    total_local_started: totalLocalStarted,
    total_stale_removed: totalStaleRemoved,
    total_with_offer_id: totalWithOfferId,
    all_started_have_offer_id: totalStarted > 0 && totalStarted === totalWithOfferId,
    groups
  };
}

function safeSmartCancelPreview({ account = {}, campaign = {}, item = {}, marketplace = true } = {}) {
  try {
    return buildSmartCancelRequestPreview({
      account,
      promotion: campaign,
      item,
      marketplace
    });
  } catch (error) {
    return {
      can_send_if_explicitly_enabled: false,
      error: error.message,
      missing_fields: error.missing_fields || [],
      field_evidence: error.field_evidence || smartCancelFieldEvidence({ promotion: campaign, item })
    };
  }
}

async function buildSmartCancelDetail({ account, promotionId, promotionType = 'SMART', itemId, live = false }) {
  const campaign = getCampaign(account.account_id, promotionId, promotionType);
  if (!campaign) {
    throw new ApiError('未找到 SMART 活动缓存，请先刷新活动。', 404);
  }
  const localStartedItems = listItems(account.account_id, promotionId, promotionType, 'started');
  const localItem = localStartedItems.find((row) => String(row.item_id) === String(itemId)) || null;
  const localPreview = localItem
    ? safeSmartCancelPreview({ account, campaign, item: localItem, marketplace: isMarketplaceCampaign(account, campaign) })
    : null;
  const detail = {
    account_id: String(account.account_id),
    promotion_id: promotionId,
    promotion_type: promotionType,
    promotion_name: campaign.name || null,
    site_id: campaign.site_id || null,
    child_user_id: campaign.child_user_id || null,
    item_id: itemId,
    local_started_count: localStartedItems.length,
    local_item_found: Boolean(localItem),
    local_field_evidence: localItem ? smartCancelFieldEvidence({ promotion: campaign, item: localItem }) : null,
    local_preview: localPreview,
    path_hypotheses: buildSmartCancelPathHypotheses({ account, campaign, itemId, offerId: localPreview?.offer_id || localPreview?.query?.offer_id }),
    local_cache_stale: false,
    live: null
  };

  if (!live) return detail;

  const liveResult = {
    started_fetch_ok: false,
    started_count: null,
    started_contains_item: null,
    started_item_field_evidence: null,
    source: 'live',
    cache_updated_from_live: false,
    local_started_count_before_sync: localStartedItems.length,
    local_stale_count: 0,
    local_stale_item_ids_sample: [],
    marketplace_item_ok: false,
    marketplace_item_summary: null,
    errors: []
  };
  try {
    const started = await fetchAndSyncSmartStarted({ account, campaign, itemId, promotionType });
    liveResult.started_fetch_ok = true;
    liveResult.started_count = started.items.length;
    liveResult.cache_updated_from_live = started.cache_updated_from_live;
    liveResult.local_stale_count = started.local_stale_count;
    liveResult.local_stale_item_ids_sample = started.local_stale_item_ids_sample;
    const liveItem = started.items.find((row) => String(row.id || row.item_id) === String(itemId)) || null;
    liveResult.started_contains_item = Boolean(liveItem);
    liveResult.started_item_field_evidence = liveItem ? smartCancelFieldEvidence({ promotion: campaign, item: liveItem }) : null;
    detail.local_cache_stale = Boolean(localItem && !liveItem);
  } catch (error) {
    liveResult.errors.push({ source: 'started_items', error: toChineseError(error), status: error?.status || null });
  }
  try {
    const client = makeWriteClient(account, campaign);
    const marketplaceItem = await client.getMarketplaceItem(itemId);
    liveResult.marketplace_item_ok = true;
    liveResult.marketplace_item_summary = safeSmartDetailSummary(marketplaceItem);
  } catch (error) {
    liveResult.errors.push({ source: 'marketplace_item', error: toChineseError(error), status: error?.status || null });
  }
  detail.live = liveResult;
  return detail;
}

async function fetchAndSyncSmartStarted({ account, campaign, itemId = null, promotionType = 'SMART' } = {}) {
  const localItems = listItems(account.account_id, campaign.promotion_id, campaign.promotion_type, 'started');
  const client = makeWriteClient(account, campaign);
  const started = await client.fetchAllPromotionItems({
    promotionId: campaign.promotion_id,
    promotionType: promotionType || campaign.promotion_type,
    status: 'started',
    maxItems: 5000
  });
  saveItems(account.account_id, campaign.promotion_id, campaign.promotion_type, started.results, {
    childUserId: campaign?.child_user_id,
    siteId: campaign?.site_id,
    logisticType: campaign?.logistic_type,
    replaceStatus: 'started',
    itemStatus: 'started'
  });
  const liveIds = new Set(started.results.map((row) => String(row.id || row.item_id)));
  const staleItemIds = localItems
    .map((row) => String(row.item_id || row.id || ''))
    .filter((id) => id && !liveIds.has(id));
  return {
    source: 'live',
    fetch_ok: true,
    items: started.results,
    started_count: started.results.length,
    local_started_count: localItems.length,
    local_stale_count: staleItemIds.length,
    local_stale_item_ids_sample: staleItemIds.slice(0, 20),
    target_item_remaining: itemId ? liveIds.has(String(itemId)) : null,
    cache_updated_from_live: true,
    errors: []
  };
}

function buildSmartCancelPathHypotheses({ account = {}, campaign = {}, itemId, offerId }) {
  const encodedItem = encodeURIComponent(itemId || '');
  const encodedPromotion = encodeURIComponent(campaign.promotion_id || '');
  const encodedOffer = encodeURIComponent(offerId || '');
  const childUserId = campaign.child_user_id || account.account_id || '';
  return [
    {
      name: 'current_marketplace_offer_delete',
      method: 'DELETE',
      path: `/marketplace/seller-promotions/items/${encodedItem}?user_id=${encodeURIComponent(childUserId)}&promotion_type=SMART&promotion_id=${encodedPromotion}&offer_id=${encodedOffer}`,
      headers: ['version:v2', 'X-Caller-Id: child_user_id'],
      status: '已验证会发送，但上次回查 started 未消失'
    },
    {
      name: 'non_marketplace_offer_delete_app_version',
      method: 'DELETE',
      path: `/seller-promotions/items/${encodedItem}?promotion_type=SMART&promotion_id=${encodedPromotion}&offer_id=${encodedOffer}&app_version=v2`,
      headers: ['Authorization only'],
      status: '未验证；按 PIX/BANK offer_id DELETE 官方形态推断'
    },
    {
      name: 'marketplace_without_user_id_app_version',
      method: 'DELETE',
      path: `/marketplace/seller-promotions/items/${encodedItem}?promotion_type=SMART&promotion_id=${encodedPromotion}&offer_id=${encodedOffer}&app_version=v2`,
      headers: ['version:v2', 'X-Caller-Id: child_user_id'],
      status: '未验证；用于排查 user_id/app_version 差异'
    }
  ];
}

function safeSmartDetailSummary(value) {
  if (!value || typeof value !== 'object') return null;
  return {
    id: value.id || value.item_id || null,
    status: value.status || null,
    site_id: value.site_id || null,
    price: value.price ?? null,
    currency_id: value.currency_id || null,
    raw_keys: Object.keys(value).slice(0, 40)
  };
}

function resolveRecentOAuthStateForCodeOnly() {
  const pendingStates = listPendingOAuthStates({ maxAgeMs: 15 * 60 * 1000 });
  return selectCodeOnlyOAuthState(pendingStates);
}

async function completeOAuthAuthorization({ code, state }) {
  const record = consumeOAuthState(state);
  if (!record) {
    const error = new Error('授权状态已过期或不匹配，请重新点击新增账号授权生成新链接');
    error.status = 400;
    throw error;
  }
  const client = new MercadoLibreClient();
  const token = await client.exchangeCode({
    clientId: record.clientId,
    clientSecret: record.clientSecret,
    code,
    redirectUri: record.redirectUri,
    codeVerifier: record.codeVerifier
  });
  const authed = new MercadoLibreClient({ accessToken: token.access_token, userId: token.user_id });
  const profile = await authed.getMe();
  const account = saveTokenAccount({
    token,
    profile,
    clientId: record.clientId,
    clientSecret: record.clientSecret,
    redirectUri: record.redirectUri,
    authDomain: record.authDomain
  });
  const cached = accountProfileRecord({ accountId: account.account_id, provider: account.provider, profile, source: 'users_me' });
  if (cached) saveAccountProfile(cached);
  return account;
}

async function executeAction(res, body) {
  const { accountId, promotionId, promotionType, action } = requireFields(body, ['accountId', 'promotionId', 'promotionType', 'action']);
  const settings = readSettings();
  const request = { ...body, writeConcurrency: normalizeWriteConcurrency(body.writeConcurrency, settings.writeConcurrency) };
  const itemStatus = requireItemStatus(body.status || actionDefaultStatus(action));
  const protection = realSubmitProtection(request);
  if (!protection.allowed) return sendJson(res, protection.status, { ok: false, ...protection });

  const account = await ensureUsableAccount(accountId);
  const campaign = getCampaign(account.account_id, promotionId, promotionType) || { promotion_id: promotionId, promotion_type: promotionType, account_id: account.account_id };
  const singlePrepare = await prepareItemsForExecution({
    account,
    promotions: [campaign],
    action,
    itemStatus,
    settings,
    request
  });
  const fetchState = getItemFetchState(account.account_id, promotionId, promotionType, itemStatus);
  const items = body.items?.length ? body.items : listItems(account.account_id, promotionId, promotionType, itemStatus);
  let plan = buildPlan({
    action,
    promotion: campaign,
    items,
    priceMode: body.priceMode || 'discount',
    discountPercent: body.discountPercent,
    directPrice: body.directPrice,
    skipSamePrice: body.skipSamePrice !== false
  });
  if (action === 'cancel' && String(promotionType || '').toUpperCase() === 'SMART') {
    const sampleItem = plan.rows.find((row) => row.status === 'planned')?.item || items[0] || null;
    const preview = sampleItem ? safeSmartCancelPreview({ account, campaign, item: sampleItem }) : null;
    if (!request.allowSmartCancel) {
      return sendJson(res, 409, {
        ok: false,
        blocked: true,
        error: 'SMART取消需要 started offer_id，并且默认只允许单商品/小样本验证；本次未发送接口。',
        request_preview: preview,
        required_flag: 'allowSmartCancel=true',
        default_sample_limit: SMART_CANCEL_POLICY.default_sample_limit
      });
    }
    plan = limitSmartCancelPlan(plan, request.smartCancelMaxItems ?? request.smartCancelLimit ?? SMART_CANCEL_POLICY.default_sample_limit);
  }
  const taskId = createTask({
    accountId: account.account_id,
    promotionId,
    promotionType,
    action,
    mode: 'real',
    discountPercent: plan.discountPercent,
    directPrice: plan.directPrice,
    plan
  });
  if (plan.total === 0) {
    const reason = `${itemStatus} 商品未读取到或当前筛选下无可处理商品。`;
    saveExecutionResult({
      taskId,
      accountId: account.account_id,
      promotionId,
      promotionType,
      itemId: '',
      action,
      mode: 'real',
      status: 'failed',
      errorCn: reason
    });
    const counts = { success: 0, failed: 1, skipped: 0 };
    finishTask(taskId, counts, 'empty_or_failed', false);
    return sendJson(res, 200, {
      ok: true,
      message: '单活动真实执行完成，但没有可处理商品。',
      taskId,
      action,
      itemStatus,
      prepare: { items: singlePrepare.summary },
      execution: {
        counts,
        writeConcurrency: normalizeWriteConcurrency(request.writeConcurrency, settings.writeConcurrency),
        maxActive: 0,
        results: [{ itemId: '', status: 'failed', errorCn: reason }],
        unreadable_candidates: 0,
        recheck: null
      }
    });
  }
  let client = makeWriteClient(account, campaign);
  const execution = await executePlannedRowsWithConcurrency({
    plan,
    action,
    promotionId,
    promotionType,
    accountId: account.account_id,
    taskId,
    writeConcurrency: request.writeConcurrency,
    executeOne: ({ row, itemId, dealPrice }) => executeOnePlannedWithTokenRefresh({
      client,
      setClient: (nextClient) => { client = nextClient; },
      accountId: account.account_id,
      action,
      campaign,
      row,
      input: {
        itemId,
        promotionId,
        promotionType,
        dealPrice
      }
    }),
    saveResult: saveExecutionResult,
    toErrorText: toChineseError
  });
  const unreadable = unreadableCandidateCount({ action, itemStatus, fetchInfo: fetchState ? null : undefined, fetchState, fallbackSavedCount: plan.total });
  if (unreadable > 0) {
    execution.counts.failed += unreadable;
    await saveExecutionResult({
      taskId,
      accountId: account.account_id,
      promotionId,
      promotionType,
      itemId: '',
      action,
      mode: 'real',
      status: 'failed',
      errorCn: unreadableItemDetailMessage({ action, itemStatus, count: unreadable })
    });
  }
  let recheck = null;
  if (action === 'cancel') {
    recheck = await recheckAndCancelRemainingStarted({
      client,
      accountId: account.account_id,
      campaign,
      promotionId,
      promotionType,
      action,
      taskId,
      writeConcurrency: request.writeConcurrency,
      maxRounds: readSettings().cancelMaxRounds,
      counts: execution.counts
    });
  }
  const completed = execution.counts.failed === 0 && plan.rows.length + unreadable === execution.counts.success + execution.counts.skipped;
  finishTask(taskId, execution.counts, completed ? 'completed' : 'partial_or_failed', completed);
  markCycleAfterTask({
    accountId: account.account_id,
    promotionId,
    promotionType,
    action,
    discountPercent: plan.discountPercent,
    completed
  });
  return sendJson(res, 200, {
    ok: true,
    message: '单活动真实执行已完成。',
    taskId,
    action,
    itemStatus,
    prepare: { items: singlePrepare.summary },
    execution: {
      ...execution,
      unreadable_candidates: unreadable,
      recheck
    }
  });
}

async function recheckAndCancelRemainingStarted({ client, accountId, campaign, promotionId, promotionType, action, taskId, writeConcurrency, maxRounds, counts, saveResult = saveExecutionResult, executionJobId, shouldCancel }) {
  const rounds = [];
  for (let round = 1; round <= maxRounds; round += 1) {
    if (shouldCancel?.()) return { remainingStarted: null, remainingItemIds: null, rounds, completed: false, cancelled: true };
    let after;
    try {
      after = await client.fetchAllPromotionItems({ promotionId, promotionType, status: 'started', maxItems: 5000 });
    } catch (error) {
      return {
        remainingStarted: null,
        remainingItemIds: null,
        rounds,
        completed: false,
        read_error: toChineseError(error),
      };
    }
    saveItems(accountId, promotionId, promotionType, after.results, {
      childUserId: campaign?.child_user_id,
      siteId: campaign?.site_id,
      logisticType: campaign?.logistic_type,
      replaceStatus: 'started',
      itemStatus: 'started'
    });
    rounds.push({ round, remainingStarted: after.results.length });
    if (after.results.length === 0) return { remainingStarted: 0, remainingItemIds: [], rounds, completed: true };
    if (String(promotionType || '').toUpperCase() === 'SMART') {
      for (const item of after.results) {
        appendExecutionItemAuditEvent(executionJobId, {
          type: 'item_remaining_started',
          account: { account_id: accountId },
          promotion: campaign,
          taskId,
          action,
          row: { item, deal_price: item.price },
          status: 'remaining_started',
          reason: 'SMART取消验证只做只读回查，不自动重试剩余商品'
        });
      }
      return { remainingStarted: after.results.length, remainingItemIds: after.results.map((item) => String(item.item_id || item.id || '')).filter(Boolean), rounds, completed: false, smart_cancel_no_auto_retry: true };
    }
    if (round === maxRounds) {
      for (const item of after.results) {
        appendExecutionItemAuditEvent(executionJobId, {
          type: 'item_remaining_started',
          account: { account_id: accountId },
          promotion: campaign,
          taskId,
          action,
          row: { item, deal_price: item.price },
          status: 'remaining_started',
          reason: '取消后仍处于 started'
        });
      }
      return { remainingStarted: after.results.length, remainingItemIds: after.results.map((item) => String(item.item_id || item.id || '')).filter(Boolean), rounds, completed: false };
    }

    const retryPlan = buildPlan({
      action: 'cancel',
      promotion: campaign,
      items: after.results,
      priceMode: 'discount'
    });
    const retry = await executePlannedRowsWithConcurrency({
      plan: retryPlan,
      action,
      promotionId,
      promotionType,
      accountId,
      taskId,
      writeConcurrency,
      shouldCancel,
      executeOne: ({ row, itemId }) => executeOnePlanned(client, action, campaign, row, { itemId, promotionId, promotionType }),
      saveResult,
      toErrorText: toChineseError,
      classifyError: classifyExecutionWriteError,
      onItemEvent: (event) => appendExecutionItemAuditEvent(executionJobId, {
        ...event,
        account: { account_id: accountId },
        promotion: campaign,
        taskId,
        action,
        retryRound: round
      })
    });
  }
  return { remainingStarted: null, remainingItemIds: null, rounds, completed: false };
}

function executeOne(client, action, input) {
  if (action === 'enroll') return client.enrollItem(input);
  if (action === 'update') return client.updateItem(input);
  if (action === 'cancel') return client.cancelItem(input);
  throw new Error('不支持的动作');
}

function executeOnePlanned(client, action, campaign, row, input) {
  if (action === 'cancel') {
    const itemId = input.itemId || row?.item?.item_id;
    const url = new URL(writeItemPath(client, itemId));
    url.searchParams.set('promotion_type', input.promotionType || campaign?.promotion_type);
    url.searchParams.set('promotion_id', input.promotionId || campaign?.promotion_id);
    if (String(input.promotionType || campaign?.promotion_type || '').toUpperCase() === 'SMART') {
      const query = buildSmartCancelQuery({ promotion: campaign, item: row?.item || {} });
      url.searchParams.set('offer_id', query.offer_id);
    }
    return client.request(url.toString(), {
      method: 'DELETE',
      headers: client.marketplace ? { version: 'v2' } : {},
      includeResponseMeta: String(input.promotionType || campaign?.promotion_type || '').toUpperCase() === 'SMART'
    });
  }
  const payloadPreview = buildSubmitPayloadPreview({ promotion: campaign, row, action });
  const payload = requireExecutableSubmitPayload(payloadPreview);
  const itemId = input.itemId || row?.item?.item_id;
  const method = action === 'update' ? 'PUT' : 'POST';
  return client.request(writeItemPath(client, itemId), {
    method,
    body: payload,
    headers: client.marketplace ? { version: 'v2' } : {}
  });
}

function makeWriteClient(account, campaign) {
  const targetUserId = campaign.child_user_id || account.account_id;
  return new MercadoLibreClient({
    accessToken: account.accessToken,
    userId: targetUserId,
    callerId: targetUserId,
    marketplace: isMarketplaceCampaign(account, campaign)
  });
}

async function executeOnePlannedWithTokenRefresh({ client, setClient, accountId, action, campaign, row, input }) {
  try {
    return await executeOnePlanned(client, action, campaign, row, input);
  } catch (error) {
    if (!isInvalidTokenError(error)) throw error;
    const refreshedAccount = await refreshAccountForWriteRetry(accountId);
    const refreshedClient = makeWriteClient(refreshedAccount, campaign);
    setClient?.(refreshedClient);
    return executeOnePlanned(refreshedClient, action, campaign, row, input);
  }
}

async function refreshAccountForWriteRetry(accountId) {
  const standalone = getStandaloneSecrets();
  if (standalone && String(standalone.account_id) === String(accountId)) {
    refreshStandaloneToken({ force: true });
    const refreshed = getStandaloneSecrets();
    if (!refreshed) throw new Error('standalone token 刷新后仍无法读取授权，请重新授权');
    return refreshed;
  }
  return ensureFreshAccount(accountId, { force: true });
}

function writeItemPath(client, itemId) {
  const prefix = client.marketplace ? '/marketplace/seller-promotions' : '/seller-promotions';
  const url = new URL(`${prefix}/items/${encodeURIComponent(itemId)}`, client.apiBaseUrl);
  if (client.marketplace) url.searchParams.set('user_id', String(client.userId));
  else url.searchParams.set('app_version', 'v2');
  return url.toString();
}

function listAccountsForUi() {
  const stored = listStoredAccounts().map((account) => ({ ...account, auth_source: 'local' }));
  const settings = readSettings();
  let accounts = stored;
  if (hasStandaloneAuth()) {
    const standalone = standaloneAccountSummary();
    if (standalone && !stored.some((account) => String(account.account_id) === String(standalone.account_id))) {
      accounts = [standalone, ...stored];
    }
  }
  return accounts.map((account) => {
    const profile = getAccountProfile(account.account_id);
    const identity = resolveStoreIdentity({
      accountId: account.account_id,
      account,
      profile,
      storeAliases: settings.storeAliases,
      standaloneNickname: account.profile?.nickname,
    });
    return {
      ...account,
      display_name: identity.raw_display_name,
      ...identity,
      site_id: profile?.site_id || account.site_id || null,
      profile_fetched_at: profile?.fetched_at || null,
      profile_source: profile?.source || null
    };
  });
}

async function refreshAccountProfile(accountId) {
  const account = await ensureUsableAccount(accountId);
  const profile = account.profile || await new MercadoLibreClient({
    accessToken: account.accessToken,
    userId: account.account_id
  }).getMe();
  const record = accountProfileRecord({
    accountId: account.account_id,
    provider: account.provider || 'mercadolibre',
    profile,
    source: 'users_me'
  });
  if (record) saveAccountProfile(record);
  return publicAccount({ ...account, display_name: profile.nickname || account.display_name, site_id: profile.site_id || account.site_id });
}

function normalizeAccountIdList(value) {
  const raw = Array.isArray(value) ? value : String(value || '').split(',');
  return [...new Set(raw.map((item) => String(item || '').trim()).filter(Boolean))];
}

function hasSellerCampaignTargetSelections(body = {}) {
  return Array.isArray(body.targetSelections)
    || Array.isArray(body.target_selections)
    || Array.isArray(body.selectedTargets)
    || Array.isArray(body.selected_targets);
}

function normalizeSellerCampaignTargetSelections(body = {}) {
  const raw = body.targetSelections || body.target_selections || body.selectedTargets || body.selected_targets || [];
  return (Array.isArray(raw) ? raw : [])
    .map((entry) => ({
      account_id: String(entry?.accountId ?? entry?.account_id ?? '').trim(),
      child_user_id: String(entry?.childUserId ?? entry?.child_user_id ?? '').trim(),
      site_id: String(entry?.siteId ?? entry?.site_id ?? '').trim().toUpperCase()
    }))
    .filter((entry) => entry.account_id && entry.child_user_id && entry.site_id);
}

function filterSellerCampaignCreateTargets(targets = [], selections = [], hasSelections = false) {
  if (!hasSelections) return targets;
  const allowed = new Set(selections.map(accountRouteKey));
  return targets.filter((target) => allowed.has(accountRouteKey(target)));
}

function sellerCampaignTargetKey(target = {}) {
  return accountRouteKey(target);
}

function sellerCampaignNotSelectedTargets(targets = [], selectedTargets = [], hasSelections = false) {
  if (!hasSelections) return [];
  const selected = new Set(selectedTargets.map(sellerCampaignTargetKey));
  return targets.filter((target) => !selected.has(sellerCampaignTargetKey(target)));
}

async function buildLiveSellerCampaignCreateTargets({ accountIds = [], filters = {}, settings = {}, catalogRefreshByAccount = null, signal = null, checkpoint = null, readScheduler = null } = {}) {
  const targetGroups = await Promise.all(accountIds.map(async (accountId) => {
    const targets = [];
    const safeAccountId = String(accountId || '');
    if (!safeAccountId) return targets;
    const liveStatusByRoute = new Map();
    try {
      checkpoint?.();
      const account = await ensureUsableAccount(safeAccountId);
      checkpoint?.();
      const preparedCatalog = catalogRefreshByAccount instanceof Map ? catalogRefreshByAccount.get(safeAccountId) : null;
      const fetched = preparedCatalog || await refreshActivityCatalogForPrepare({ account, filters, settings, signal, checkpoint, readScheduler });
      checkpoint?.();
      for (const [routeKey, summary] of activityCatalogSellerStatus(safeAccountId, fetched, filters, settings)) liveStatusByRoute.set(routeKey, summary);
    } catch (error) {
      if (signal?.aborted
          || String(error?.code || '').startsWith('COMMIT_')
          || String(error?.code || '').startsWith('SUBMISSION_')
          || String(error?.code || '').startsWith('ACTIVITY_ACCOUNT_ROUTE_')) throw error;
      const cachedTargets = sellerCampaignCreateTargetsForAccount({ accountId: safeAccountId, filters, settings });
      for (const target of cachedTargets) {
        targets.push({
          ...target,
          hasSellerCampaign: false,
          detection_status: 'unreadable',
          detection_message: `无法确认该店铺站点是否已有自建活动：${toChineseError(error)}`
        });
      }
      return targets;
    }
    targets.push(...sellerCampaignCreateTargetsForAccount({ accountId: safeAccountId, filters, settings, liveStatusByRoute }));
    return targets;
  }));
  return targetGroups.flat();
}

async function recoverHiddenSellerCampaignsForPreparedAccount({
  account,
  filters = {},
  settings = {},
  catalogRefresh,
  signal = null,
  checkpoint = null,
  readScheduler = null,
} = {}) {
  const accountId = String(account?.account_id || '');
  if (!accountId) return [];
  const liveStatusByRoute = activityCatalogSellerStatus(accountId, catalogRefresh, filters, settings);
  const targets = sellerCampaignCreateTargetsForAccount({ accountId, filters, settings, liveStatusByRoute });
  if (!targets.some((target) => target.detection_status === HIDDEN_SELLER_CAMPAIGN_STATUS)) return targets;
  const clients = new Map();
  return resolveHiddenSellerCampaignTargets({
    targets,
    recoverByName: async (target, name) => {
      checkpoint?.();
      const route = normalizeAccountRoute(target);
      const clientKey = accountRouteKey(route);
      let client = clients.get(clientKey);
      if (!client) {
        client = new MercadoLibreClient({
          accessToken: account.accessToken,
          userId: route.child_user_id,
          callerId: accountId,
          marketplace: true,
          readScheduler,
          readAccountId: accountId,
        });
        clients.set(clientKey, client);
      }
      const recovery = await recoverDuplicateSellerCampaign({
        target: route,
        name,
        forceCatalogRefresh: async () => listCampaignsFiltered(accountId, {
          siteIds: [route.site_id], promotionTypes: ['SELLER_CAMPAIGN'],
        }).filter((campaign) => accountRouteKey(campaign) === accountRouteKey(route)),
        listHistoricalCandidates: async () => listSellerCampaignRecoveryCandidates({
          accountId, childUserId: route.child_user_id, siteId: route.site_id, name,
        }),
        listWebhookCandidates: async () => listVerifiedActivityCallbackPromotionMappings({
          accountId, childUserId: route.child_user_id, siteId: route.site_id, promotionType: 'SELLER_CAMPAIGN',
        }),
        readPromotionDetail: async (candidate) => {
          checkpoint?.();
          const detail = await client.getPromotionDetail({
            promotionId: candidate.promotion_id,
            promotionType: 'SELLER_CAMPAIGN',
            userId: route.child_user_id,
            signal,
          });
          checkpoint?.();
          return bindActivityToAccountRoute(extractSellerCampaignDetail(detail, candidate.promotion_id), route);
        },
      });
      if (recovery.status !== 'existing' || !recovery.promotion_id || !recovery.promotion) return recovery;
      saveCampaigns(accountId, [recovery.promotion], {
        merchantId: accountId,
        childUserId: route.child_user_id,
        siteId: route.site_id,
        logisticType: target.logistic_type || null,
      });
      markActivityCacheDirty({
        accountId,
        siteId: route.site_id,
        promotionId: recovery.promotion_id,
        promotionType: 'SELLER_CAMPAIGN',
      });
      return recovery;
    },
  });
}

function sellerCampaignCreateTargetsForAccount({ accountId, filters = {}, settings = {}, liveStatusByRoute = null } = {}) {
  const safeAccountId = String(accountId || '');
  if (!safeAccountId) return [];
  const selectedSites = new Set(
    (Array.isArray(filters.siteIds) ? filters.siteIds : String(filters.siteId || '').split(','))
      .map((siteId) => String(siteId || '').trim())
      .filter(Boolean)
  );
  const sites = filterByOperatingSites(listSiteSummaries(safeAccountId), settings, safeAccountId)
    .filter((site) => !selectedSites.size || selectedSites.has(String(site.site_id || '')));
  const siteMap = new Map();
  for (const site of sites) {
    const siteId = String(site.site_id || '').trim();
    if (!siteId) continue;
    const current = siteMap.get(siteId);
    if (!current) {
      siteMap.set(siteId, site);
      continue;
    }
    siteMap.set(siteId, mergeSellerCampaignCreateSite(current, site));
  }
  const sellerCampaigns = listOperatingCampaignsFiltered(safeAccountId, {
    siteIds: [...siteMap.keys()],
    promotionTypes: ['SELLER_CAMPAIGN']
  });
  const sellerByRoute = new Map();
  for (const campaign of sellerCampaigns) {
    const routeKey = accountRouteKey({
      account_id: safeAccountId,
      child_user_id: campaign.child_user_id,
      site_id: campaign.site_id,
    });
    const current = sellerByRoute.get(routeKey) || [];
    current.push(campaign);
    sellerByRoute.set(routeKey, current);
  }
  const hiddenByRoute = new Map();
  for (const hidden of listHiddenSellerCampaignsForAccount(safeAccountId)) {
    const routeKey = accountRouteKey(hidden);
    const current = hiddenByRoute.get(routeKey) || [];
    current.push(hidden);
    hiddenByRoute.set(routeKey, current);
  }
  const account = getAccount(safeAccountId) || listAccountsForUi().find((row) => String(row.account_id) === safeAccountId) || { account_id: safeAccountId };
  const storeName = storeIdentityForAccount(safeAccountId, account, settings).store_name;
  return [...siteMap.values()].map((site) => {
    const siteId = String(site.site_id || '');
    const route = normalizeAccountRoute({ account_id: safeAccountId, child_user_id: site.child_user_id, site_id: siteId });
    const existing = sellerByRoute.get(accountRouteKey(route)) || [];
    const hidden = hiddenByRoute.get(accountRouteKey(route)) || [];
    const live = liveStatusByRoute instanceof Map ? liveStatusByRoute.get(accountRouteKey(route)) : null;
    const detection = sellerCampaignDetectionForSite({ live, existing, hidden });
    return {
      ...route,
      store_name: storeName,
      site_id: siteId,
      site_name: siteDisplayName(siteId),
      promotion_type: 'SELLER_CAMPAIGN',
      hasSellerCampaign: detection.status === 'existing',
      existing_seller_campaign_count: detection.count,
      detection_status: detection.status,
      detection_message: detection.message,
      hidden_activity_names: detection.hidden_activity_names || []
    };
  });
}

function sellerCampaignDetectionForSite({ live = null, existing = [], hidden = [] } = {}) {
  if (!live) {
    if (existing.length > 0) return { status: 'existing', count: existing.length, message: '本地活动列表已有自建活动。' };
    if (hidden.length > 0) {
      return {
        status: HIDDEN_SELLER_CAMPAIGN_STATUS,
        count: hidden.length,
        hidden_activity_names: [...new Set(hidden.map((row) => String(row.promotion_name || '')).filter(Boolean))],
        message: HIDDEN_SELLER_CAMPAIGN_MESSAGE,
      };
    }
    return { status: 'visibility_unknown', count: 0, message: '接口未读取到自建活动，不代表后台不存在；本次禁止自动创建。' };
  }
  const sellerCount = Number(live.seller_campaign_count || 0);
  if (sellerCount > 0) {
    return { status: 'existing', count: sellerCount, message: '实时接口已读取到自建活动。' };
  }
  if (hidden.length > 0) {
    return {
      status: HIDDEN_SELLER_CAMPAIGN_STATUS,
      count: hidden.length,
      hidden_activity_names: [...new Set(hidden.map((row) => String(row.promotion_name || '')).filter(Boolean))],
      message: HIDDEN_SELLER_CAMPAIGN_MESSAGE,
    };
  }
  const okCount = Number(live.ok_count || 0);
  if (okCount > 0) {
    return { status: 'visibility_unknown', count: 0, message: '接口未读取到自建活动，不代表后台不存在；本次禁止自动创建。' };
  }
  const message = Array.isArray(live.errors) && live.errors.length
    ? `无法确认该店铺站点是否已有自建活动：${live.errors[0]}`
    : '无法确认该店铺站点是否已有自建活动。';
  return { status: 'unreadable', count: 0, message };
}

function mergeSellerCampaignCreateSite(current = {}, next = {}) {
  const preferred = sellerCampaignCreateSiteScore(next) > sellerCampaignCreateSiteScore(current) ? next : current;
  return {
    ...current,
    child_user_id: preferred.child_user_id || current.child_user_id || next.child_user_id,
    logistic_type: preferred.logistic_type || current.logistic_type || next.logistic_type,
    last_promotion_status: preferred.last_promotion_status || current.last_promotion_status || next.last_promotion_status,
    last_error: preferred.last_error || current.last_error || next.last_error,
    updated_at: preferred.updated_at || current.updated_at || next.updated_at,
    total: Number(current.total || 0) + Number(next.total || 0),
    last_promotion_count: Math.max(Number(current.last_promotion_count || 0), Number(next.last_promotion_count || 0))
  };
}

function sellerCampaignCreateSiteScore(site = {}) {
  const logisticType = String(site.logistic_type || '').toLowerCase();
  let score = 0;
  score += Number(site.last_promotion_count || 0) * 1000;
  score += Number(site.total || 0) * 100;
  if (logisticType === 'remote') score += 50;
  if (logisticType === 'fulfillment') score -= 50;
  if (site.child_user_id) score += 10;
  if (String(site.last_promotion_status || '').toLowerCase() === 'ok') score += 5;
  return score;
}

async function createMissingSellerCampaigns({
  runId: requestedRunId = null,
  missingTargets = [],
  existingTargets = [],
  notSelectedTargets = [],
  unreadableTargets = [],
  name,
  startDate,
  finishDate,
  signal = null,
  checkpoint = null,
  onProgress = null,
  readScheduler = null,
} = {}) {
  assertSellerCampaignCreateTargetRoutes(missingTargets);
  const runId = requestedRunId || `seller-campaign-create-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const created = [];
  const failed = [];
  const recheckMissing = [];
  const apiSucceeded = [];
  const recoveredExisting = [];
  const hiddenWithoutVisibleId = [];
  const accountsToRefresh = new Map();
  const emitProgress = (phase) => onProgress?.({
    run_id: runId,
    phase,
    target_count: missingTargets.length,
    api_succeeded_count: apiSucceeded.length,
    failed_count: failed.length,
    recheck_missing_count: recheckMissing.length,
    created_count: created.length,
    recovered_existing_count: recoveredExisting.length,
    existing_without_visible_id_count: hiddenWithoutVisibleId.length,
  });
  for (const target of existingTargets) {
    saveSellerCampaignCreateResult({
      runId,
      target,
      name,
      startDate,
      finishDate,
      selected: true,
      requestStatus: 'already_exists',
      rechecked: true
    });
  }
  for (const target of notSelectedTargets) {
    saveSellerCampaignCreateResult({
      runId,
      target,
      name,
      startDate,
      finishDate,
      selected: false,
      requestStatus: 'not_selected'
    });
  }
  for (const target of unreadableTargets) {
    saveSellerCampaignCreateResult({
      runId,
      target,
      name,
      startDate,
      finishDate,
      selected: true,
      requestStatus: 'unreadable_blocked',
      errorCn: target.error || target.detection_message || '无法确认该店铺站点是否已有自建活动，已阻断创建。'
    });
  }
  for (const target of missingTargets) {
    checkpoint?.();
    const accountId = String(target.account_id || '');
    const childUserId = String(target.child_user_id || '');
    const siteId = String(target.site_id || '');
    const publicTarget = publicSellerCampaignTarget(target);
    if (!accountId || !childUserId || !siteId) {
      failed.push({ ...publicTarget, error: '店铺站点资料不完整，未发送创建请求。' });
      saveSellerCampaignCreateResult({
        runId,
        target,
        name,
        startDate,
        finishDate,
        selected: true,
        requestStatus: 'failed',
        errorCn: '店铺站点资料不完整，未发送创建请求。'
      });
      emitProgress('target_blocked');
      continue;
    }
    let requestSent = false;
    let responseRecorded = false;
    let account = null;
    let client = null;
    try {
      checkpoint?.();
      account = await ensureUsableAccount(accountId);
      checkpoint?.();
      accountsToRefresh.set(accountId, account);
      const callerUserId = String(account.account_id || accountId);
      client = new MercadoLibreClient({
        accessToken: account.accessToken,
        userId: childUserId,
        callerId: callerUserId,
        marketplace: true
      });
      saveSellerCampaignCreateResult({
        runId,
        target,
        name,
        startDate,
        finishDate,
        selected: true,
        requestStatus: 'request_sending'
      });
      emitProgress('request_sending');
      checkpoint?.();
      requestSent = true;
      const response = await client.createSellerCampaign({
        childUserId,
        callerId: callerUserId,
        clientUserId: callerUserId,
        name,
        startDate,
        finishDate,
        subType: 'FLEXIBLE_PERCENTAGE',
        signal,
      });
      const body = response?.body ?? response;
      const promotionId = extractCreatedSellerCampaignId(body);
      const entry = {
        target,
        publicTarget,
        promotionId,
        httpStatus: response?.http_status || null
      };
      apiSucceeded.push(entry);
      saveSellerCampaignCreateResult({
        runId,
        target,
        name,
        startDate,
        finishDate,
        selected: true,
        requestStatus: 'api_success_pending_recheck',
        promotionId,
        httpStatus: entry.httpStatus
      });
      responseRecorded = true;
      invalidateMarketplaceSiteCatalog({ accountId, childUserId, siteId });
      if (promotionId) {
        try {
          const writeThrough = sellerCampaignWriteThroughPromotion({
            route: { account_id: accountId, child_user_id: childUserId, site_id: siteId },
            promotionId,
            name,
            startDate,
            finishDate,
            response: body,
          });
          saveCampaigns(accountId, [writeThrough], {
            merchantId: accountId,
            childUserId,
            siteId,
            logisticType: target.logistic_type || null,
          });
          markActivityCacheDirty({
            accountId,
            siteId,
            promotionId: writeThrough.promotion_id,
            promotionType: writeThrough.promotion_type,
          });
          entry.writeThrough = true;
        } catch (writeThroughError) {
          entry.writeThroughError = toChineseError(writeThroughError);
        }
      }
      emitProgress('api_response_saved');
      checkpoint?.();
    } catch (error) {
      if (signal?.aborted || String(error?.code || '').startsWith('COMMIT_') || String(error?.code || '').startsWith('SUBMISSION_')) {
        if (requestSent && !responseRecorded) {
          saveSellerCampaignCreateResult({
            runId,
            target,
            name,
            startDate,
            finishDate,
            selected: true,
            requestStatus: 'request_outcome_unknown',
            errorCn: '创建请求已发出但响应未确认，后续必须先实时回查，程序不会自动重复创建。'
          });
        }
        throw error;
      }
      if (requestSent && isDuplicateSellerCampaignNameError(error) && account && client) {
        responseRecorded = true;
        const route = { account_id: accountId, child_user_id: childUserId, site_id: siteId };
        const recovery = await recoverDuplicateSellerCampaign({
          target: route,
          name,
          forceCatalogRefresh: async () => {
            checkpoint?.();
            await fetchAndSavePromotions(account, {
              routes: [route],
              signal,
              checkpoint,
              readScheduler,
            });
            checkpoint?.();
            return listCampaignsFiltered(accountId, { siteIds: [siteId], promotionTypes: ['SELLER_CAMPAIGN'] })
              .filter((campaign) => accountRouteKey(campaign) === accountRouteKey(route));
          },
          listHistoricalCandidates: async () => listSellerCampaignRecoveryCandidates({
            accountId, childUserId, siteId, name,
          }),
          listWebhookCandidates: async () => listVerifiedActivityCallbackPromotionMappings({
            accountId, childUserId, siteId, promotionType: 'SELLER_CAMPAIGN',
          }),
          readPromotionDetail: async (candidate) => {
            checkpoint?.();
            const detail = await client.getPromotionDetail({
              promotionId: candidate.promotion_id,
              promotionType: 'SELLER_CAMPAIGN',
              userId: childUserId,
              signal,
            });
            checkpoint?.();
            return bindActivityToAccountRoute(extractSellerCampaignDetail(detail, candidate.promotion_id), route);
          },
        });
        if (recovery.status === 'existing' && recovery.promotion_id) {
          if (recovery.promotion) {
            saveCampaigns(accountId, [recovery.promotion], {
              merchantId: accountId,
              childUserId,
              siteId,
              logisticType: target.logistic_type || null,
            });
          }
          recoveredExisting.push({
            ...publicTarget,
            status: 'existing_recovered',
            detection_status: 'existing',
            promotion_type: 'SELLER_CAMPAIGN',
            promotion_name: String(name || ''),
            promotion_id: recovery.promotion_id,
            recovery_source: recovery.recovery_source,
            rechecked: true,
          });
          saveSellerCampaignCreateResult({
            runId,
            target,
            name,
            startDate,
            finishDate,
            selected: true,
            requestStatus: 'duplicate_name_recovered',
            promotionId: recovery.promotion_id,
            httpStatus: error?.status || null,
            errorRaw: safeSellerCampaignCreateError(error),
            rechecked: true,
            detectionStatus: 'existing',
          });
          emitProgress('duplicate_name_recovered');
          continue;
        }
        const hidden = {
          ...publicTarget,
          status: HIDDEN_SELLER_CAMPAIGN_STATUS,
          detection_status: HIDDEN_SELLER_CAMPAIGN_STATUS,
          detection_reason: 'duplicate_name_hidden',
          detection_message: HIDDEN_SELLER_CAMPAIGN_MESSAGE,
          promotion_type: 'SELLER_CAMPAIGN',
          promotion_name: String(name || ''),
          promotion_id: '',
          rechecked: false,
        };
        hiddenWithoutVisibleId.push(hidden);
        saveSellerCampaignCreateResult({
          runId,
          target,
          name,
          startDate,
          finishDate,
          selected: true,
          requestStatus: 'duplicate_name_hidden',
          httpStatus: error?.status || null,
          errorCn: HIDDEN_SELLER_CAMPAIGN_MESSAGE,
          errorRaw: safeSellerCampaignCreateError(error),
          rechecked: false,
          detectionStatus: HIDDEN_SELLER_CAMPAIGN_STATUS,
        });
        emitProgress('duplicate_name_hidden');
        continue;
      }
      failed.push({ ...publicTarget, error: toChineseError(error), details: safeSellerCampaignCreateError(error) });
      saveSellerCampaignCreateResult({
        runId,
        target,
        name,
        startDate,
        finishDate,
        selected: true,
        requestStatus: 'failed',
        errorCn: toChineseError(error),
        errorRaw: safeSellerCampaignCreateError(error)
      });
      emitProgress('request_failed');
    }
  }
  const refreshedAccounts = new Set();
  const refreshErrors = new Map();
  for (const [accountId, account] of accountsToRefresh) {
    try {
      checkpoint?.();
      await fetchAndSavePromotions(account, { signal, checkpoint, readScheduler });
      checkpoint?.();
      refreshedAccounts.add(accountId);
    } catch (error) {
      if (signal?.aborted || String(error?.code || '').startsWith('COMMIT_') || String(error?.code || '').startsWith('SUBMISSION_')) throw error;
      refreshErrors.set(accountId, toChineseError(error));
    }
  }
  for (const entry of apiSucceeded) {
    checkpoint?.();
    const accountId = String(entry.target.account_id || '');
    const siteId = String(entry.target.site_id || '');
    const rechecked = refreshedAccounts.has(accountId)
      ? findSellerCampaignAfterCreate({
          accountId,
          childUserId: entry.target.child_user_id,
          siteId,
          name,
          promotionId: entry.promotionId,
        })
      : null;
    const promotionId = rechecked?.promotion_id || entry.promotionId || '';
    if (rechecked) {
      created.push({
        ...entry.publicTarget,
        status: 'created',
        promotion_type: 'SELLER_CAMPAIGN',
        promotion_name: String(name || ''),
        promotion_id: promotionId,
        rechecked: true,
        http_status: entry.httpStatus
      });
      saveSellerCampaignCreateResult({
        runId,
        target: entry.target,
        name,
        startDate,
        finishDate,
        selected: true,
        requestStatus: 'created',
        promotionId,
        httpStatus: entry.httpStatus,
        rechecked: true
      });
      emitProgress('target_rechecked');
      continue;
    }
    const error = refreshErrors.get(accountId)
      ? `接口已返回成功，但刷新该店铺活动失败：${refreshErrors.get(accountId)}。`
      : '接口已返回成功，但刷新活动后未在该店铺站点找到新自建活动。';
    recheckMissing.push({
      ...entry.publicTarget,
      status: 'api_success_recheck_missing',
      promotion_type: 'SELLER_CAMPAIGN',
      promotion_name: String(name || ''),
      promotion_id: promotionId,
      rechecked: false,
      http_status: entry.httpStatus,
      error
    });
    saveSellerCampaignCreateResult({
      runId,
      target: entry.target,
      name,
      startDate,
      finishDate,
      selected: true,
      requestStatus: 'api_success_recheck_missing',
      promotionId,
      httpStatus: entry.httpStatus,
      errorCn: error,
      rechecked: false
    });
    emitProgress('target_recheck_missing');
  }
  emitProgress('completed');
  return {
    ok: failed.length === 0 && recheckMissing.length === 0,
    run_id: runId,
    mode: 'real',
    action: 'create_seller_campaign',
    promotion_type: 'SELLER_CAMPAIGN',
    creates_official_activity: false,
    writes_external_state: apiSucceeded.length > 0,
    existing_count: existingTargets.length,
    to_create_count: missingTargets.length,
    not_selected_count: notSelectedTargets.length,
    unreadable_count: unreadableTargets.length,
    created_count: created.length,
    recovered_existing_count: recoveredExisting.length,
    existing_without_visible_id_count: hiddenWithoutVisibleId.length,
    failed_count: failed.length,
    recheck_missing_count: recheckMissing.length,
    existing: publicSellerCampaignTargets(existingTargets),
    to_create: publicSellerCampaignTargets(missingTargets),
    not_selected: publicSellerCampaignTargets(notSelectedTargets),
    unreadable: unreadableTargets,
    created,
    recovered_existing: recoveredExisting,
    existing_without_visible_id: hiddenWithoutVisibleId,
    duplicate_name_hidden: hiddenWithoutVisibleId,
    messages: hiddenWithoutVisibleId.length ? [
      `${HIDDEN_SELLER_CAMPAIGN_MESSAGE}（${hiddenWithoutVisibleId.length} 个店铺站点）；其余活动继续。`,
    ] : [],
    failed,
    recheck_missing: recheckMissing,
    refreshed_account_count: refreshedAccounts.size
  };
}

function assertSellerCampaignCreateTargetRoutes(targets = []) {
  const routesByAccount = new Map();
  for (const target of targets || []) {
    const accountId = String(target?.account_id || '');
    if (!routesByAccount.has(accountId)) {
      routesByAccount.set(accountId, listSiteSummaries(accountId).map((site) => ({
        account_id: accountId,
        child_user_id: site.child_user_id,
        site_id: site.site_id,
      })));
    }
    assertAccountRouteAllowed(target, routesByAccount.get(accountId));
  }
}

function saveSellerCampaignCreateResult({
  runId,
  target = {},
  name,
  startDate,
  finishDate,
  selected = true,
  requestStatus,
  promotionId = '',
  httpStatus = null,
  errorCn = '',
  errorRaw = null,
  rechecked = false,
  detectionStatus = null,
} = {}) {
  try {
    const now = new Date().toISOString();
    getDb().prepare(`
      INSERT INTO seller_campaign_create_results (
        run_id, account_id, child_user_id, store_name, site_id, site_name, promotion_type,
        promotion_name, start_date, finish_date, selected, request_status,
        promotion_id, http_status, error_cn, error_raw, rechecked, detection_status, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      String(runId || ''),
      String(target.account_id || ''),
      String(target.child_user_id || ''),
      String(target.store_name || ''),
      String(target.site_id || ''),
      String(target.site_name || siteDisplayName(target.site_id || '')),
      'SELLER_CAMPAIGN',
      String(name || ''),
      startDate ? String(startDate) : null,
      finishDate ? String(finishDate) : null,
      selected ? 1 : 0,
      String(requestStatus || 'unknown'),
      String(promotionId || ''),
      Number.isFinite(Number(httpStatus)) ? Number(httpStatus) : null,
      String(errorCn || ''),
      errorRaw ? JSON.stringify(errorRaw) : null,
      rechecked ? 1 : 0,
      detectionStatus ? String(detectionStatus) : null,
      now,
      now
    );
  } catch (error) {
    console.warn('[seller-campaign-create] 保存内部记录失败:', error?.message || error);
  }
}

function publicSellerCampaignTargets(targets = []) {
  return (Array.isArray(targets) ? targets : []).map(publicSellerCampaignTarget);
}

function publicSellerCampaignTarget(target = {}) {
  return {
    account_id: String(target.account_id || ''),
    child_user_id: String(target.child_user_id || ''),
    store_name: String(target.store_name || ''),
    site_id: String(target.site_id || ''),
    site_name: String(target.site_name || siteDisplayName(target.site_id || '')),
    promotion_type: 'SELLER_CAMPAIGN',
    existing_seller_campaign_count: Number(target.existing_seller_campaign_count || 0)
  };
}

function findSellerCampaignAfterCreate({ accountId, childUserId, siteId, name, promotionId }) {
  const campaigns = listCampaignsFiltered(accountId, { siteIds: [siteId], promotionTypes: ['SELLER_CAMPAIGN'] });
  const routed = campaigns.filter((campaign) => accountRouteKey(campaign) === accountRouteKey({
    account_id: accountId,
    child_user_id: childUserId,
    site_id: siteId,
  }));
  const byId = promotionId ? routed.find((campaign) => String(campaign.promotion_id || campaign.id || '') === String(promotionId)) : null;
  if (byId) return normalizeCreatedSellerCampaign(byId);
  const wantedName = String(name || '').trim();
  const byName = routed
    .filter((campaign) => String(campaign.name || campaign.promotion_name || '').trim() === wantedName)
    .sort((a, b) => String(b.updated_at || b.raw_json?.date_updated || b.raw_json?.last_updated || '').localeCompare(String(a.updated_at || a.raw_json?.date_updated || a.raw_json?.last_updated || '')))[0];
  return byName ? normalizeCreatedSellerCampaign(byName) : null;
}

function normalizeCreatedSellerCampaign(campaign = {}) {
  return {
    promotion_id: String(campaign.promotion_id || campaign.id || ''),
    name: String(campaign.name || campaign.promotion_name || ''),
    site_id: String(campaign.site_id || ''),
    promotion_type: String(campaign.promotion_type || '').toUpperCase()
  };
}

function extractCreatedSellerCampaignId(body) {
  if (!body || typeof body !== 'object') return '';
  const direct = body.promotion_id || body.id || body.promotionId;
  if (direct) return String(direct);
  const nested = body.promotion || body.result || body.data;
  if (nested && typeof nested === 'object') return extractCreatedSellerCampaignId(nested);
  return '';
}

function extractSellerCampaignDetail(input, fallbackPromotionId = '') {
  const body = input?.body && typeof input.body === 'object' ? input.body : input;
  const candidate = body?.promotion && typeof body.promotion === 'object'
    ? body.promotion
    : body?.result && typeof body.result === 'object'
      ? body.result
      : body?.data && typeof body.data === 'object'
        ? body.data
        : body;
  if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) {
    const error = new ApiError('平台未返回可验证的自建活动详情。', 422);
    error.code = 'SELLER_CAMPAIGN_DETAIL_INVALID';
    throw error;
  }
  return {
    ...candidate,
    promotion_id: String(candidate.promotion_id || candidate.id || fallbackPromotionId || ''),
    promotion_type: String(candidate.promotion_type || candidate.type || 'SELLER_CAMPAIGN').toUpperCase(),
  };
}

function safeSellerCampaignCreateError(error) {
  const body = error?.body && typeof error.body === 'object'
    ? sanitizeSellerCampaignCreateErrorBody(error.body)
    : error?.body || null;
  return {
    status: error?.status || null,
    message: error?.message || '',
    body
  };
}

function sanitizeSellerCampaignCreateErrorBody(body) {
  if (!body || typeof body !== 'object') return body;
  const result = {};
  for (const key of ['message', 'error', 'status', 'cause', 'error_description', 'department', 'code']) {
    if (Object.hasOwn(body, key)) result[key] = body[key];
  }
  return Object.keys(result).length ? result : body;
}

function storeIdentityForAccount(accountId, account = {}, settings = readSettings()) {
  return resolveStoreIdentity({
    accountId,
    account,
    profile: getAccountProfile(accountId) || account.profile || {},
    storeAliases: settings?.storeAliases || {},
  });
}

function publicAccount(account) {
  const identity = storeIdentityForAccount(account.account_id, account);
  return {
    account_id: String(account.account_id),
    display_name: identity.raw_display_name,
    ...identity,
    site_id: account.site_id,
    token_type: account.token_type,
    expires_at: account.expires_at,
    auth_source: account.authSource || account.auth_source || 'local'
  };
}

function actionDefaultStatus(action) {
  if (action === 'enroll') return 'candidate';
  if (action === 'update') return 'started';
  if (action === 'cancel') return 'started';
  return 'candidate';
}

function createExecutionJob(request, options = {}) {
  const id = options.id || createExecutionJobId();
  const runId = `${id}-${Math.random().toString(36).slice(2, 10)}`;
  const job = {
    id,
    run_id: runId,
    event_file_key: runId,
    status: options.status || 'queued',
    cancel_requested: false,
    request: { ...request },
    request_summary: {
      accountId: String(request.accountId || ''),
      action: request.action || 'auto',
      prepareOnly: Boolean(request.prepareOnly),
      itemStatus: request.itemStatus || '',
      readConcurrency: request.readConcurrency,
      requestedWriteConcurrency: request.requestedWriteConcurrency ?? request.writeConcurrency,
      writeConcurrency: request.writeConcurrency,
      requestedGlobalWriteConcurrency: request.globalWriteConcurrency,
      globalWriteConcurrency: request.globalWriteConcurrency,
      executionGroupId: request.executionGroupId || null,
      storeName: request.storeName || request.selectedStoreName || '',
      selectedSiteName: request.selectedSiteName || ''
    },
    started_at: new Date().toISOString(),
    finished_at: null,
    progress: {
      stage: 'queued',
      total_promotions: 0,
      completed_promotions: 0,
      failed_promotions: 0,
      platform_total: 0,
      saved_count: 0,
      execute_completed_promotions: 0,
      global_active_writes: 0,
      global_peak_in_flight: 0,
      execution_stop_reason: null
    },
    logs: [],
    userLogs: [],
    result: null,
    error: null
  };
  executionJobs.set(id, job);
  appendExecutionJobLog(job, '执行任务已创建。');
  appendExecutionJobEvent(job, { type: 'job_created', request_summary: job.request_summary });
  persistExecutionJob(job);
  return job;
}

function createExecutionJobId() {
  const stamp = new Date().toISOString().replace(/[-:.TZ]/g, '');
  return `exec-${stamp}-${process.pid}-${nextExecutionJobId++}`;
}

function createExecutionGroupId() {
  const stamp = new Date().toISOString().replace(/[-:.TZ]/g, '');
  return `exec-group-${stamp}-${process.pid}-${nextExecutionJobId++}`;
}

function executionGroupChildSnapshot(job) {
  return {
    job_id: job.id,
    account_id: String(job.request_summary?.accountId || ''),
    store_name: job.request_summary?.storeName || '',
    site_name: job.request_summary?.selectedSiteName || '',
    status: job.status,
    started_at: job.started_at,
    finished_at: job.finished_at,
    progress: job.progress,
    request_summary: job.request_summary,
    logs: job.logs,
    userLogs: job.userLogs,
    result: job.result,
    error: job.error
  };
}

function publicExecutionGroup(group, { compact = false } = {}) {
  const result = group.result || summarizeExecutionGroup(group);
  const compactResult = {};
  for (const field of [
    'action', 'store_count', 'total', 'success', 'failed', 'skipped',
    'relation_count', 'unique_item_count', 'activity_failure_count',
    'request_success_count', 'live_verified_removed_count', 'pending_verification_count'
  ]) compactResult[field] = result?.[field] ?? null;
  return {
    id: group.id,
    client_submission_id: group.client_submission_id,
    status: group.status,
    action: group.action,
    cancel_requested: Boolean(group.cancel_requested),
    created_at: group.created_at,
    updated_at: group.updated_at,
    finished_at: group.finished_at,
    global_peak_in_flight: Number(group.global_peak_in_flight || 0),
    scope: executionGroupBusinessScope(group),
    children: compact ? [] : (group.children || []).map((child) => ({ ...child })),
    result: compact ? compactResult : result,
    error: group.error || null
  };
}

function requestExecutionGroupCancel(group) {
  if (TERMINAL_EXECUTION_GROUP_STATUSES.has(String(group.status || ''))) return group;
  group.cancel_requested = true;
  group.status = 'stopping';
  for (const child of group.children || []) {
    const job = executionJobs.get(String(child.job_id)) || loadPersistedExecutionJob(child.job_id);
    if (!job || TERMINAL_EXECUTION_GROUP_STATUSES.has(String(job.status || ''))) continue;
    executionJobs.set(job.id, job);
    job.cancel_requested = true;
    if (job.status === 'queued') job.status = 'stopping';
    appendExecutionJobLog(job, '执行组已收到停止请求，未开始的商品会尽快跳过。');
    appendExecutionJobEvent(job, { type: 'group_stop_requested', group_id: group.id });
    persistExecutionJob(job);
  }
  executionGroupPersistence.persist(group);
  return group;
}

function createExecutionPrepareId() {
  return `prepare-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function publicExecutionSubmission(prepare = {}) {
  const publicTarget = (target = {}) => ({
    account_id: String(target.account_id || ''),
    child_user_id: String(target.child_user_id || ''),
    store_name: target.store_name || '',
    site_id: target.site_id || '',
    site_name: target.site_name || siteDisplayName(target.site_id),
    detection_status: target.detection_status || '',
    detection_message: target.detection_message || '',
    existing_seller_campaign_count: Number(target.existing_seller_campaign_count || 0),
    hidden_activity_names: Array.isArray(target.hidden_activity_names) ? target.hidden_activity_names.map(String) : [],
  });
  return {
    id: prepare.id,
    prepare_id: prepare.id,
    client_submission_id: prepare.client_submission_id,
    state: prepare.state,
    scope_hash: prepare.scope_hash,
    scope_version: prepare.scope_version,
    resolved_action: prepare.resolved_action,
    discounts: prepare.discounts || {},
    activity_buckets: prepare.activity_buckets || {},
    excluded_buckets: prepare.excluded_buckets || {},
    targets: prepare.targets || [],
    seller_detection: {
      existing: (prepare.seller_detection?.existing || []).map(publicTarget),
      existing_without_visible_id: (prepare.seller_detection?.existing_without_visible_id || []).map(publicTarget),
      duplicate_name_hidden: (prepare.seller_detection?.existing_without_visible_id || []).map(publicTarget),
      confirmed_absent: (prepare.seller_detection?.confirmed_absent || []).map(publicTarget),
      needs_manual_review: (prepare.seller_detection?.needs_manual_review || []).map(publicTarget),
      visibility_unknown: (prepare.seller_detection?.needs_manual_review || []).map(publicTarget),
      unreadable: (prepare.seller_detection?.unreadable || []).map(publicTarget),
    },
    live_read: prepare.live_read || { rows: [], readable_count: 0, blocked_count: 0, all_blocked: false },
    seller_input: prepare.seller_input ? {
      name: prepare.seller_input.name || '',
      start_date: prepare.seller_input.start_date || null,
      finish_date: prepare.seller_input.finish_date || null,
      selected_targets: (prepare.seller_input.selected_targets || []).map(publicTarget),
      validation_errors: prepare.seller_input.validation_errors || [],
    } : null,
    confirmation_summary: prepare.confirmation_summary || '',
    confirmation_token: prepare.execution_confirmation_token || null,
    creation_result: prepare.creation_result ? {
      created_count: Number(prepare.creation_result.created_count || 0),
      recovered_existing_count: Number(prepare.creation_result.recovered_existing_count || 0),
      existing_without_visible_id_count: Number(prepare.creation_result.existing_without_visible_id_count || 0),
      existing_without_visible_id: (prepare.creation_result.existing_without_visible_id || []).map(publicTarget),
      messages: Array.isArray(prepare.creation_result.messages) ? prepare.creation_result.messages.map(String) : [],
    } : null,
    reconfirm_changes: prepare.reconfirm_changes || [],
    scope_adjustments: prepare.scope_adjustments ? {
      messages: prepare.scope_adjustments.messages || [],
      auto_removed_item_count: Number(prepare.scope_adjustments.auto_removed_item_count || 0),
      excluded_new_item_count: Number(prepare.scope_adjustments.excluded_new_item_count || 0),
      removed_activity_count: Number(prepare.scope_adjustments.removed_activity_count || 0),
      blocked_activity_count: Number(prepare.scope_adjustments.blocked_activity_count || 0),
    } : null,
    expires_at: prepare.expires_at,
    group_id: prepare.group_id || null,
    group: prepare.group ? publicExecutionGroup(prepare.group) : null,
    error: prepare.error || null,
    progress: {
      stage: String(prepare.progress?.stage || prepare.state || ''),
      percent: Math.max(0, Math.min(100, Number(prepare.progress?.percent || 0))),
      completed: Math.max(0, Number(prepare.progress?.completed || 0)),
      total: Math.max(0, Number(prepare.progress?.total || 0)),
      message: String(prepare.progress?.message || ''),
      current_store: String(prepare.progress?.current_store || ''),
      current_site: String(prepare.progress?.current_site || ''),
      current_activity: String(prepare.progress?.current_activity || ''),
    },
  };
}

function selectedActivityRoutes(account, filters = {}, settings = {}) {
  const requested = [
    ...(Array.isArray(filters.siteIds) ? filters.siteIds : []),
    ...String(filters.siteId || '').split(','),
  ].map((value) => String(value || '').trim().toUpperCase()).filter(Boolean);
  const requestedSet = new Set(requested);
  const operating = filterByOperatingSites(listSiteSummaries(account.account_id), settings, account.account_id)
    .filter((site) => !requestedSet.size || requestedSet.has(String(site.site_id || '').trim().toUpperCase()))
    .map((site) => normalizeAccountRoute({
      account_id: account.account_id,
      child_user_id: site.child_user_id || (String(account.site_id || '').toUpperCase() === 'CBT' ? '' : account.account_id),
      site_id: site.site_id,
    }, { requireComplete: false }))
    .filter((route) => route.account_id && route.child_user_id && route.site_id);
  if (operating.length) return [...new Map(operating.map((route) => [accountRouteKey(route), route])).values()];
  if (String(account.site_id || '').toUpperCase() !== 'CBT') {
    const siteId = requested[0] || String(account.site_id || '').toUpperCase();
    return siteId ? [normalizeAccountRoute({ account_id: account.account_id, child_user_id: account.account_id, site_id: siteId })] : [];
  }
  return [];
}

async function refreshActivityCatalogForPrepare({ account, filters = {}, settings = {}, readConcurrency = null, signal = null, checkpoint = null, readScheduler = null } = {}) {
  const selectedRoutes = selectedActivityRoutes(account, filters, settings);
  const routePlan = planActivityCatalogRoutes(
    selectedRoutes,
    (route) => getActivityCacheState(route.account_id, route.site_id, '', ''),
  );
  const refreshRoutes = routePlan.refresh;
  const cachedRoutes = routePlan.cached;
  const liveCatalogSiteIds = [...new Set(refreshRoutes.map((route) => route.site_id).filter(Boolean))];
  let fetched = null;
  let fetchError = null;
  if (refreshRoutes.length) {
    try {
      checkpoint?.();
      fetched = await fetchAndSavePromotions(account, {
        readConcurrency: readConcurrency ?? settings.readConcurrency,
        siteIds: liveCatalogSiteIds,
        routes: refreshRoutes,
        signal,
        checkpoint,
        readScheduler,
      });
      checkpoint?.();
    } catch (error) {
      if (signal?.aborted
          || String(error?.code || '').startsWith('COMMIT_')
          || String(error?.code || '').startsWith('SUBMISSION_')
          || String(error?.code || '').startsWith('ACTIVITY_ACCOUNT_ROUTE_')) throw error;
      fetchError = toChineseError(error);
      for (const siteId of liveCatalogSiteIds) {
        recordActivityCatalogCalibration({ accountId: account.account_id, siteId, error: fetchError });
      }
    }
  }
  const fetchedRows = String(account.site_id || '').toUpperCase() === 'CBT'
    ? (fetched?.children || [])
    : fetched ? [{
        account_id: account.account_id,
        child_user_id: account.account_id,
        site_id: selectedRoutes[0]?.site_id || account.site_id,
        status: 'ok',
        total: fetched.total,
        catalog_revision: fetched.catalog_revision,
        catalog_changes: fetched.catalog_changes,
      }] : [];
  const cachedRows = cachedRoutes.map((route) => ({
    account_id: route.account_id,
    child_user_id: route.child_user_id,
    site_id: route.site_id,
    status: 'ok',
    cached: true,
    total: cachedActivityCatalogForRoute(route).length,
    catalog_changes: { added: 0, changed: 0, removed: 0, identities: [] },
  }));
  const resultRows = [...fetchedRows, ...cachedRows];
  const routeReads = summarizeActivityCatalogRouteReads({
    expectedRoutes: selectedRoutes,
    results: resultRows.map((row) => ({ account_id: account.account_id, ...row })),
    error: fetchError,
  });
  const refreshedRouteKeys = routeReads.refreshed_route_keys;
  const cachedRouteKeys = new Set(cachedRoutes.map(accountRouteKey));
  const blockedRouteKeys = routeReads.blocked_route_keys;
  const errorsByRoute = routeReads.errors_by_route;
  const catalogIdentityChanges = fetchedRows.flatMap((row) => (
    Array.isArray(row?.catalog_changes?.identities) ? row.catalog_changes.identities : []
  ));
  const blockedSiteIds = new Set([...blockedRouteKeys].map((key) => key.split('|')[2]).filter(Boolean));
  return {
    account_id: String(account.account_id),
    routes: selectedRoutes,
    site_ids: liveCatalogSiteIds,
    refreshed_site_ids: new Set(fetchedRows.map((row) => String(row.site_id || '').toUpperCase()).filter(Boolean)),
    cached_site_ids: new Set(cachedRoutes.map((route) => route.site_id)),
    refreshed_route_keys: refreshedRouteKeys,
    cached_route_keys: cachedRouteKeys,
    external_read_count: refreshRoutes.length,
    cache_reused_count: cachedRoutes.length,
    refresh_reasons: routePlan.reasons,
    blocked_route_keys: blockedRouteKeys,
    errors_by_route: errorsByRoute,
    catalog_identity_changes: catalogIdentityChanges,
    blocked_site_ids: blockedSiteIds,
    fetch_result: fetched,
  };
}

function activityCatalogSellerStatus(accountId, refresh = {}, filters = {}, settings = {}) {
  const rows = new Map();
  const targets = sellerCampaignCreateTargetsForAccount({ accountId, filters, settings });
  for (const target of targets) {
    const siteId = String(target.site_id || '').toUpperCase();
    const routeKey = accountRouteKey(target);
    if (refresh.blocked_route_keys?.has(routeKey)) {
      rows.set(routeKey, { ok_count: 0, error_count: 1, seller_campaign_count: 0, errors: [refresh.errors_by_route?.get(routeKey) || '活动目录读取失败，本次不使用旧缓存。'] });
      continue;
    }
    if (!refresh.refreshed_route_keys?.has(routeKey)) {
      rows.set(routeKey, { ok_count: 0, error_count: 1, seller_campaign_count: 0, errors: ['活动目录尚未完成校准。'] });
      continue;
    }
    const sellerCount = listOperatingCampaignsFiltered(accountId, { siteId, promotionTypes: ['SELLER_CAMPAIGN'] }, settings)
      .filter((campaign) => accountRouteKey(campaign) === routeKey).length;
    rows.set(routeKey, { ok_count: 1, error_count: 0, seller_campaign_count: sellerCount, errors: [] });
  }
  return rows;
}

async function buildExecutionSubmissionSnapshot(input = {}, reportProgress = () => {}, context = {}) {
  const accountIds = normalizeAccountIdList(input.accountIds || input.account_ids || input.accountId || input.account_id);
  if (!accountIds.length) throw new ApiError('请选择至少一个店铺账号。', 400);
  const finalRevalidation = context.finalRevalidation === true;
  reportProgress({
    stage: finalRevalidation ? 'final_catalog' : 'accounts',
    percent: 2,
    completed: 0,
    total: accountIds.length,
    message: finalRevalidation ? '正在执行提交前轻量活动目录复核' : '正在核对店铺和活动范围',
  });
  const settings = readSettings();
  const readScheduler = context.readScheduler || createBalancedReadScheduler(BALANCED_READ_PROFILES.prepare);
  const operationReadCache = context.operationReadCache || new Map();
  const filters = input.filters || {};
  const storeNames = {};
  const accountsById = new Map();
  const promotions = [];
  const catalogRefreshByAccount = new Map();
  let accountRefreshCompleted = 0;
  await Promise.all(accountIds.map(async (accountId) => {
    context.checkpoint?.();
    const account = await ensureUsableAccount(accountId);
    context.checkpoint?.();
    accountsById.set(String(accountId), account);
    storeNames[accountId] = storeIdentityForAccount(accountId, account, settings).store_name;
    reportProgress({
      stage: 'accounts', percent: 5 + Math.floor((accountRefreshCompleted / Math.max(1, accountIds.length)) * 25),
      completed: accountRefreshCompleted,
      total: accountIds.length,
      message: finalRevalidation ? '提交前正在检查活动目录版本与变更标记' : '正在核对本地活动目录状态',
      current_store: storeNames[accountId],
    });
    const catalogRefresh = await refreshActivityCatalogForPrepare({
      account,
      filters,
      settings,
      readConcurrency: normalizeConcurrency(input.readConcurrency ?? settings.readConcurrency),
      signal: context.signal,
      checkpoint: context.checkpoint,
      readScheduler,
    });
    context.checkpoint?.();
    catalogRefreshByAccount.set(String(accountId), catalogRefresh);
    reportProgress({
      stage: 'catalog',
      percent: 8 + Math.floor((accountRefreshCompleted / Math.max(1, accountIds.length)) * 20),
      completed: accountRefreshCompleted,
      total: accountIds.length,
      message: catalogRefresh.external_read_count
        ? `活动目录校准：外部读取 ${catalogRefresh.external_read_count} 个站点，缓存复用 ${catalogRefresh.cache_reused_count} 个站点`
        : `活动目录缓存有效：外部读取 0 个站点，缓存复用 ${catalogRefresh.cache_reused_count} 个站点`,
      current_store: storeNames[accountId],
    });
    await recoverHiddenSellerCampaignsForPreparedAccount({
      account,
      filters,
      settings,
      catalogRefresh,
      signal: context.signal,
      checkpoint: context.checkpoint,
      readScheduler,
    });
    context.checkpoint?.();
    const allowedRoutes = listSiteSummaries(accountId).map((site) => ({
      account_id: String(accountId),
      child_user_id: String(site.child_user_id || ''),
      site_id: String(site.site_id || ''),
    })).filter((route) => route.child_user_id && route.site_id);
    const accountPromotions = listOperatingCampaignsFiltered(accountId, {
      siteId: filters.siteId,
      siteIds: filters.siteIds,
    }, settings).filter((promotion) => !catalogRefresh.blocked_route_keys.has(accountRouteKey({
      account_id: accountId,
      child_user_id: promotion.child_user_id,
      site_id: promotion.site_id,
    })));
    promotions.push(...accountPromotions.map((row) => {
      const route = normalizeAccountRoute({
        account_id: String(accountId),
        child_user_id: row.child_user_id || (String(account.site_id || '').toUpperCase() === 'CBT' ? '' : account.account_id),
        site_id: row.site_id || account.site_id,
      });
      if (String(account.site_id || '').toUpperCase() === 'CBT') assertAccountRouteAllowed(route, allowedRoutes);
      return bindActivityToAccountRoute(row, route);
    }));
    accountRefreshCompleted += 1;
    reportProgress({
      stage: 'accounts', percent: 5 + Math.floor((accountRefreshCompleted / Math.max(1, accountIds.length)) * 25),
      completed: accountRefreshCompleted, total: accountIds.length, message: '店铺活动已刷新', current_store: storeNames[accountId],
    });
  }));
  const requested = String(input.requested_action || input.requestedAction || input.action || 'auto').toLowerCase();
  const selected = ordinaryPromotions(filterPromotions(promotions, filters));
  const readStateSnapshot = buildPreparationReadStateSnapshot(accountIds);
  const resolvedAction = requested === 'auto'
    ? String(buildTodayDecisionForScope(accountIds, selected).action || '')
    : ['enroll', 'update', 'cancel'].includes(requested) ? requested : (() => { throw new ApiError('不支持的执行动作。', 400); })();
  const rawRevalidateKeys = (context.revalidateTargetKeys || []).map(String).filter(Boolean);
  const revalidationPlan = finalRevalidation
    ? buildFinalRevalidationPlan({
        confirmedScope: context.previousSnapshot?.confirmed_execution_scope,
        currentPromotions: selected,
        catalogRefreshes: [...catalogRefreshByAccount.values()],
        action: resolvedAction,
        explicitTargetKeys: rawRevalidateKeys,
        getCacheState: (promotion) => preparationActivityCacheState(readStateSnapshot, promotion),
        getFetchState: (promotion, status) => preparationItemFetchState(readStateSnapshot, promotion, status),
        getFallbackState: (promotion) => preparationItemFetchState(readStateSnapshot, promotion, INVENTORY_FALLBACK_ITEM_STATUS),
      })
    : null;
  const revalidateIdentityKeys = revalidationPlan?.item_read_identity_keys || new Set(rawRevalidateKeys
    .filter((key) => !key.startsWith('__'))
    .map(activityIdentityKey)
    .filter((key) => key.split('|').length === 5 && key.split('|').every(Boolean)));
  const scopeReviewIdentityKeys = revalidationPlan?.scope_review_identity_keys || revalidateIdentityKeys;
  const targetedRevalidate = finalRevalidation || (rawRevalidateKeys.length > 0
    && revalidateIdentityKeys.size === rawRevalidateKeys.length);
  const promotionIsTargeted = (promotion) => {
    if (!targetedRevalidate) return true;
    return scopeReviewIdentityKeys.has(activityIdentityKey(promotion));
  };
  const promotionNeedsItemRead = (promotion) => {
    if (!targetedRevalidate) return true;
    return revalidateIdentityKeys.has(activityIdentityKey(promotion));
  };
  const priorLiveRows = targetedRevalidate ? (context.previousSnapshot?.live_read?.rows || []) : [];
  const unchangedPriorLiveRows = priorLiveRows.filter((row) => !promotionIsTargeted(row)).map((row) => ({ ...row }));
  const startedLiveReadRows = [];
  const selectedForLiveRead = targetedRevalidate ? selected.filter(promotionNeedsItemRead) : selected;
  if (finalRevalidation) {
    const reasonCount = Object.values(revalidationPlan.reasons_by_identity || {}).flat().length;
    reportProgress({
      stage: 'final_targeted',
      percent: 34,
      completed: 0,
      total: revalidationPlan.total_activity_count,
      message: revalidateIdentityKeys.size
        ? `提交前定向复核：活动 ${revalidateIdentityKeys.size}/${revalidationPlan.total_activity_count}，变化原因 ${reasonCount} 项`
        : `提交前轻量复核完成：活动目录无变化，商品活动复核 0/${revalidationPlan.total_activity_count}`,
    });
  }
  let startedActivitiesCompleted = 0;
  const preparationReadProgress = {
    external_reads_started: 0,
    cache_reused: 0,
    inventory_fallback_active: 0,
  };
  const reportActivityProgress = (stage, event, completedBase, percentBase, percentSpan) => {
    if (event.type === 'inventory_fallback_start') preparationReadProgress.inventory_fallback_active += 1;
    if (event.type === 'inventory_fallback_done') preparationReadProgress.inventory_fallback_active = Math.max(0, preparationReadProgress.inventory_fallback_active - 1);
    if (event.type === 'item_fetch_start') {
      if (event.external_read) preparationReadProgress.external_reads_started += 1;
      else if (event.cache_reused) preparationReadProgress.cache_reused += 1;
    }
    if (!['item_fetch_done', 'item_fetch_start', 'inventory_fallback_start', 'inventory_fallback_done'].includes(event.type)) return completedBase;
    const completed = event.type === 'item_fetch_done' ? completedBase + 1 : completedBase;
    reportProgress({
      stage,
      percent: percentBase + Math.floor((completed / Math.max(1, selectedForLiveRead.length)) * percentSpan),
      completed,
      total: selectedForLiveRead.length,
      message: finalRevalidation
        ? `提交前定向复核：${stage === 'started' ? '已报名商品' : '可报名商品'} ${completed}/${selectedForLiveRead.length}`
        : stage === 'started' ? '正在核对已报名商品' : '正在核对可报名商品',
      current_store: storeNames[event.promotion?.account_id || ''] || '',
      current_site: String(event.promotion?.site_id || ''),
      current_activity: String(event.promotion_id || ''),
      external_reads_started: preparationReadProgress.external_reads_started,
      cache_reused: preparationReadProgress.cache_reused,
      inventory_fallback_active: preparationReadProgress.inventory_fallback_active,
      remaining_activities: Math.max(0, selectedForLiveRead.length - completed),
      current_reason: String(event.refresh_reason || ''),
    });
    return completed;
  };
  let startedCompleted = 0;
  await Promise.all(accountIds.map(async (accountId) => {
    const accountPromotions = selectedForLiveRead.filter((promotion) => String(promotion.account_id || '') === String(accountId));
    if (!accountPromotions.length) return;
    reportProgress({
      stage: 'started', percent: 35 + Math.floor((startedCompleted / Math.max(1, accountIds.length)) * 25),
      completed: startedCompleted, total: accountIds.length, message: '正在核对已报名商品', current_store: storeNames[accountId],
    });
    const prep = await prepareItemsForExecution({
      account: accountsById.get(String(accountId)),
      promotions: accountPromotions,
      action: 'update',
      itemStatus: 'started',
      settings,
      request: { ...input, fetchMode: 'full', maxItems: 'all', allowInventoryFallback: false },
      signal: context.signal,
      checkpoint: context.checkpoint,
      readScheduler,
      operationReadCache,
      readStateSnapshot,
      forceReadKeys: forceReadKeysForStatus(revalidateIdentityKeys, 'started'),
      onProgress: (event) => {
        startedActivitiesCompleted = reportActivityProgress('started', event, startedActivitiesCompleted, 35, 25);
      },
    });
    context.checkpoint?.();
    startedLiveReadRows.push(...submissionLiveReadRows(accountId, prep.summary.rows));
    startedCompleted += 1;
  }));
  const sellerTargets = resolvedAction === 'enroll' && !filters.excludeSeller
    ? await buildLiveSellerCampaignCreateTargets({ accountIds, filters, settings, catalogRefreshByAccount, signal: context.signal, checkpoint: context.checkpoint, readScheduler })
    : [];
  context.checkpoint?.();
  reportProgress({ stage: 'seller', percent: 65, completed: 0, total: sellerTargets.length, message: '正在核对自建活动状态' });
  const sellerDetection = {
    existing: sellerTargets.filter((target) => target.detection_status === 'existing'),
    existing_without_visible_id: sellerTargets.filter((target) => target.detection_status === HIDDEN_SELLER_CAMPAIGN_STATUS),
    confirmed_absent: sellerTargets.filter((target) => target.detection_status === 'confirmed_absent'),
    needs_manual_review: sellerTargets.filter((target) => target.detection_status === 'visibility_unknown'),
    unreadable: sellerTargets.filter((target) => target.detection_status === 'unreadable'),
  };
  const discounts = {
    seller: Number(input.sellerDiscountPercent ?? settings.sellerDefaultDiscount ?? 5),
    official: Number(input.officialDiscountPercent ?? settings.officialDefaultDiscount ?? 6),
  };
  const liveReadRows = resolvedAction === 'enroll'
    ? [...unchangedPriorLiveRows]
    : [...unchangedPriorLiveRows, ...startedLiveReadRows];
  if (resolvedAction === 'enroll') {
    let itemAccountsCompleted = 0;
    let candidateActivitiesCompleted = 0;
    await Promise.all(accountIds.map(async (accountId) => {
      const accountPromotions = selectedForLiveRead.filter((promotion) => String(promotion.account_id || '') === String(accountId));
      if (!accountPromotions.length) return;
      reportProgress({
        stage: 'items', percent: 70 + Math.floor((itemAccountsCompleted / Math.max(1, accountIds.length)) * 22),
        completed: itemAccountsCompleted, total: accountIds.length, message: '正在核对可报名商品', current_store: storeNames[accountId],
      });
      const prep = await prepareItemsForExecution({
        account: accountsById.get(String(accountId)),
        promotions: accountPromotions,
        action: resolvedAction,
        itemStatus: actionDefaultStatus(resolvedAction),
        settings,
        request: { ...input, fetchMode: 'full', maxItems: 'all' },
        signal: context.signal,
        checkpoint: context.checkpoint,
        readScheduler,
        operationReadCache,
        readStateSnapshot,
        forceReadKeys: forceReadKeysForStatus(revalidateIdentityKeys, 'candidate'),
        onProgress: (event) => {
          candidateActivitiesCompleted = reportActivityProgress('items', event, candidateActivitiesCompleted, 70, 22);
        },
      });
      context.checkpoint?.();
      const startedByPromotion = new Map(startedLiveReadRows
        .filter((row) => row.account_id === String(accountId))
        .map((row) => [`${row.promotion_type}|${row.promotion_id}`, row]));
      liveReadRows.push(...submissionLiveReadRows(accountId, prep.summary.rows).map((row) => ({
        ...row,
        blocked: row.blocked || startedByPromotion.get(`${row.promotion_type}|${row.promotion_id}`)?.blocked === true,
      })));
      itemAccountsCompleted += 1;
    }));
  }
  const catalogBlockedRoutes = [...catalogRefreshByAccount.values()].flatMap((refresh) => (
    [...(refresh.blocked_route_keys || [])].map((routeKey) => {
      const [accountId, childUserId, siteId] = routeKey.split('|');
      return {
        account_id: accountId,
        child_user_id: childUserId,
        site_id: siteId,
        message: refresh.errors_by_route?.get(routeKey) || '活动目录读取失败，本次不使用旧缓存。',
      };
    })
  ));
  const itemLiveRead = summarizeLiveReadRows(liveReadRows, selected.length);
  const liveRead = {
    ...itemLiveRead,
    catalog_blocked_routes: catalogBlockedRoutes,
    blocked_count: Number(itemLiveRead.blocked_count || 0) + catalogBlockedRoutes.length,
    all_blocked: Number(itemLiveRead.readable_count || 0) === 0
      && (Number(itemLiveRead.blocked_count || 0) + catalogBlockedRoutes.length) > 0,
  };
  const observedExecutionScope = buildSnapshotExecutionScope({
    action: resolvedAction,
    promotions: selected,
    liveRead,
    sellerDetection,
    previousSnapshot: context.previousSnapshot,
    targetedRevalidate,
    promotionIsTargeted,
    blockedIdentityKeys: revalidationPlan?.blocked_identity_keys || new Set(),
  });
  const observedTargetIdentityKeys = finalRevalidation
    ? new Set(observedExecutionScope.activities.map(activityIdentityKey))
    : null;
  const targetSource = finalRevalidation
    ? selected.filter((promotion) => observedTargetIdentityKeys.has(activityIdentityKey(promotion)))
    : selected;
  const targets = targetSource.map((promotion) => ({
    account_id: String(promotion.account_id || ''),
    child_user_id: String(promotion.child_user_id || ''),
    promotion_id: String(promotion.promotion_id || ''),
    promotion_type: String(promotion.promotion_type || '').toUpperCase(),
    site_id: String(promotion.site_id || ''),
    status: String(promotion.status || ''),
    updated_at: promotion.updated_at || null,
  })).sort((a, b) => promotionKey(a).localeCompare(promotionKey(b)));
  const scopeFacts = {
    account_ids: accountIds,
    filters,
    resolved_action: resolvedAction,
    discounts,
    targets,
    seller_detection: Object.fromEntries(Object.entries(sellerDetection).map(([key, rows]) => [key, rows.map(sellerCampaignTargetKey).sort()])),
    live_read: liveRead.rows.map((row) => ({
      account_id: row.account_id,
      promotion_id: row.promotion_id,
      promotion_type: row.promotion_type,
      status: row.status,
      detail_status: row.detail_status,
      platform_total: row.platform_total,
      saved_count: row.saved_count,
    })).sort((left, right) => `${promotionKey(left)}|${left.status}`.localeCompare(`${promotionKey(right)}|${right.status}`)),
  };
  const selectedBucketCounts = promotionBucketCounts(targetSource);
  const scopeBucketCounts = promotionBucketCounts(promotions);
  const activityBuckets = { seller: selectedBucketCounts.seller, official: selectedBucketCounts.official };
  const excludedBuckets = { smart: scopeBucketCounts.smart, lightning: scopeBucketCounts.lightning, other: scopeBucketCounts.other };
  reportProgress({ stage: 'finalizing', percent: 96, completed: accountIds.length, total: accountIds.length, message: '正在生成最终确认范围' });
  return {
    accountIds,
    storeNames,
    filters,
    resolved_action: resolvedAction,
    discounts,
    targets,
    activity_buckets: activityBuckets,
    excluded_buckets: excludedBuckets,
    seller_detection: sellerDetection,
    live_read: liveRead,
    read_concurrency: buildReadConcurrencyReport({
      schedulerSnapshot: typeof readScheduler?.snapshot === 'function' ? readScheduler.snapshot() : {},
      localWorkConcurrency: Math.min(6, Math.max(1, accountIds.length)),
      localDbBatchQueries: readStateSnapshot.db_batch_queries,
    }),
    observed_execution_scope: observedExecutionScope,
    confirmed_execution_scope: observedExecutionScope,
    revalidation_plan: revalidationPlan ? {
      total_activity_count: revalidationPlan.total_activity_count,
      item_read_activity_count: revalidationPlan.item_read_identity_keys.size,
      scope_review_activity_count: revalidationPlan.scope_review_identity_keys.size,
      blocked_activity_count: revalidationPlan.blocked_identity_keys.size,
      removed_activity_count: revalidationPlan.removed_identity_keys.size,
      excluded_new_activity_count: revalidationPlan.excluded_new_activity_count,
      reasons_by_identity: revalidationPlan.reasons_by_identity,
    } : null,
    scope_facts: scopeFacts,
    scope_hash: executionSubmissionScopeHash(observedExecutionScope, discounts),
    confirmation_summary: `${actionDisplayName(resolvedAction)}：店铺 ${accountIds.length} 个，站点范围 ${input.selectedSiteName || '全部站点'}，自建活动 ${activityBuckets.seller} 个，官方活动 ${activityBuckets.official} 个${resolvedAction === 'cancel' ? '' : `，自建折扣 ${discounts.seller}%，官方折扣 ${discounts.official}%`}；SMART 排除 ${excludedBuckets.smart} 个，限时活动排除 ${excludedBuckets.lightning} 个，其它排除 ${excludedBuckets.other} 个。`,
    group_request: {
      ...input,
      accountIds,
      storeNames,
      action: resolvedAction,
      requested_action: resolvedAction,
      filters: { ...filters, promotionTypes: ['SELLER_CAMPAIGN', 'DEAL'] },
      sellerDiscountPercent: discounts.seller,
      officialDiscountPercent: discounts.official,
      confirmedExecutionScope: observedExecutionScope,
      confirmText: 'REAL_SUBMIT',
      prepareOnly: false,
    },
  };
}

function submissionLiveReadRows(accountId, rows = []) {
  return (rows || []).map((row) => ({
    account_id: String(accountId),
    child_user_id: String(row.child_user_id || ''),
    site_id: row.site_id || null,
    promotion_id: row.promotion_id || null,
    promotion_type: String(row.promotion_type || '').toUpperCase(),
    detail_status: row.detail_status || null,
    platform_total: row.platform_total ?? null,
    saved_count: row.saved_count ?? null,
    blocked: row.detail_status === 'error' || row.detail_status === 'unreadable',
  }));
}

function executionSubmissionScopeHash(scope = {}, discounts = {}) {
  return submissionScopeHash({
    execution_scope: confirmedExecutionScopeFacts(scope),
    discounts: {
      seller: Number(discounts?.seller ?? 0),
      official: Number(discounts?.official ?? 0),
    },
  });
}

function sellerDetectionStates(sellerDetection = {}) {
  const states = {};
  for (const [status, targets] of Object.entries(sellerDetection || {})) {
    for (const target of targets || []) states[sellerCampaignTargetKey(target)] = status;
  }
  return states;
}

function buildSnapshotExecutionScope({
  action = '',
  promotions = [],
  liveRead = {},
  sellerDetection = {},
  previousSnapshot = null,
  targetedRevalidate = false,
  promotionIsTargeted = () => true,
  blockedIdentityKeys = new Set(),
} = {}) {
  const itemStatus = actionDefaultStatus(action);
  const previousScope = previousSnapshot?.observed_execution_scope || previousSnapshot?.confirmed_execution_scope || null;
  const previousActivities = new Map((previousScope?.activities || []).map((activity) => [activityIdentityKey(activity), activity]));
  const liveByIdentity = new Map((liveRead.rows || []).map((row) => [activityIdentityKey(row), row]));
  const activities = [];
  const observedIdentityKeys = new Set();
  for (const promotion of promotions || []) {
    const identity = activityIdentityKey(promotion);
    if (targetedRevalidate && !promotionIsTargeted(promotion)) {
      const previous = previousActivities.get(identity);
      if (previous) {
        activities.push(previous);
        observedIdentityKeys.add(identity);
      }
      continue;
    }
    const live = liveByIdentity.get(identity) || null;
    const blocked = !live || live.status === 'blocked' || live.blocked === true;
    const itemIds = blocked ? [] : listItems(
      promotion.account_id,
      promotion.promotion_id,
      promotion.promotion_type,
      itemStatus,
    ).map((item) => String(item.item_id ?? item.itemId ?? item.id ?? '')).filter(Boolean);
    activities.push({
      ...promotion,
      item_status: itemStatus,
      item_ids: itemIds,
      platform_total: live?.platform_total ?? null,
      saved_count: live?.saved_count ?? null,
      detail_status: live?.detail_status ?? 'unreadable',
      blocked,
    });
    observedIdentityKeys.add(identity);
  }
  if (targetedRevalidate) {
    for (const key of blockedIdentityKeys || []) {
      if (observedIdentityKeys.has(key)) continue;
      const previous = previousActivities.get(key);
      if (!previous) continue;
      activities.push({
        ...previous,
        item_ids: [],
        platform_total: null,
        saved_count: null,
        detail_status: 'unreadable',
        blocked: true,
      });
      observedIdentityKeys.add(key);
    }
  }
  return createConfirmedExecutionScope({
    action,
    activities,
    sellerCreateTargetKeys: previousScope?.seller_create_target_keys || [],
    sellerTargetStates: sellerDetectionStates(sellerDetection),
  });
}

function resolveSubmissionGlobalAction(actions = []) {
  const unique = [...new Set(actions.map((value) => String(value || '')).filter(Boolean))];
  if (!unique.length) throw new ApiError('当前范围没有可执行动作。', 409);
  if (unique.length > 1) {
    const error = new ApiError('不同店铺站点需要不同动作，请分开选择后重新准备。', 409);
    error.code = 'ACTION_CONFLICT';
    error.details = unique;
    throw error;
  }
  return unique[0];
}

function executionSubmissionPreparedPatch(snapshot, prepare, options = {}) {
  const preparedAt = new Date();
  const latestSellerTargets = new Map(Object.values(snapshot.seller_detection || {}).flat().map((target) => [sellerCampaignTargetKey(target), target]));
  const selectedSellerTargets = (prepare.seller_input?.selected_targets || [])
    .map((target) => latestSellerTargets.get(sellerCampaignTargetKey(target)) || target)
    .filter((target) => String(target.detection_status || '') !== 'existing');
  const sourceScope = options.confirmedScope
    || snapshot.confirmed_execution_scope
    || snapshot.observed_execution_scope
    || createConfirmedExecutionScope({ action: snapshot.resolved_action, activities: [] });
  const confirmedScope = createConfirmedExecutionScope({
    action: sourceScope.action || snapshot.resolved_action,
    activities: sourceScope.activities,
    sellerCreateTargetKeys: selectedSellerTargets.map(sellerCampaignTargetKey),
    sellerTargetStates: sourceScope.seller_target_states || sellerDetectionStates(snapshot.seller_detection),
  });
  const scopeHash = options.scopeHash || executionSubmissionScopeHash(confirmedScope, snapshot.discounts);
  const scopeMessages = Array.isArray(options.scopeMessages) ? options.scopeMessages.filter(Boolean) : [];
  const sameDayWarning = prepare.same_day_warning || null;
  const sameDayMessage = sameDayWarning?.completed
    ? `提醒：今天当前范围已完成${actionDisplayName(sameDayWarning.completed.action)}，本次${sameDayWarning.same_action ? '仍准备相同动作，可能重复处理' : `将执行${actionDisplayName(snapshot.resolved_action)}，属于另一项真实操作`}。`
    : '';
  return {
    scope_hash: scopeHash,
    scope_version: preparedAt.toISOString(),
    resolved_action: snapshot.resolved_action,
    discounts: snapshot.discounts,
    targets: snapshot.targets,
    activity_buckets: snapshot.activity_buckets,
    excluded_buckets: snapshot.excluded_buckets,
    seller_detection: snapshot.seller_detection,
    live_read: snapshot.live_read,
    observed_execution_scope: snapshot.observed_execution_scope || sourceScope,
    confirmed_execution_scope: confirmedScope,
    scope_adjustments: options.scopeAdjustments || null,
    seller_input: prepare.seller_input ? {
      ...prepare.seller_input,
      selected_targets: selectedSellerTargets,
    } : { name: '', start_date: null, finish_date: null, selected_targets: [], validation_errors: [] },
    confirmation_summary: [sameDayMessage, snapshot.confirmation_summary, ...scopeMessages].filter(Boolean).join(' '),
    execution_confirmation_token: prepare.execution_confirmation_token || crypto.randomBytes(24).toString('base64url'),
    group_request: {
      ...snapshot.group_request,
      confirmedExecutionScope: confirmedScope,
    },
    expires_at: new Date(preparedAt.getTime() + 15 * 60 * 1000).toISOString(),
  };
}

function scheduleExecutionSubmissionPreparation(prepareId) {
  const key = String(prepareId || '');
  if (!key || submissionPreparationTasks.has(key)) return submissionPreparationTasks.get(key) || null;
  const task = new Promise((resolve) => setImmediate(resolve))
    .then(() => runSubmissionPreparation({
      store: submissionPersistence,
      prepareId: key,
      buildSnapshot: buildExecutionSubmissionSnapshot,
      preparedPatch: executionSubmissionPreparedPatch,
      formatError: toChineseError,
    }))
    .finally(() => submissionPreparationTasks.delete(key));
  submissionPreparationTasks.set(key, task);
  return task;
}

function refreshExecutionSubmissionExpiry(prepare) {
  if (!prepare) return prepare;
  return refreshSubmissionDeadline({
    store: submissionPersistence,
    prepareId: prepare.id,
    onAbort: () => abortExecutionSubmissionCommit(prepare.id, 'commit_lease_expired'),
  });
}

function prepareExecutionSubmission(body = {}) {
  requireFields(body, ['client_submission_id']);
  const rawPrior = submissionPersistence.findBySubmissionId(body.client_submission_id);
  const prior = rawPrior ? loadEffectiveSubmission({
    store: submissionPersistence,
    prepareId: rawPrior.id,
    onAbort: (row) => abortExecutionSubmissionCommit(row.id, 'commit_lease_expired'),
  }) : null;
  if (prior) {
    const incomingFingerprint = submissionRequestFingerprint(body);
    const priorFingerprint = prior.request_fingerprint || submissionRequestFingerprint(prior.request || {});
    if (incomingFingerprint !== priorFingerprint) {
      const error = new ApiError('同一次提交不能更改动作或执行范围，请重新发起。', 409);
      error.code = 'CLIENT_SUBMISSION_MISMATCH';
      error.details = { state: prior.state };
      throw error;
    }
    if (prior.state === 'preparing') scheduleExecutionSubmissionPreparation(prior.id);
    return { prepare: prior, reused: true };
  }
  const active = loadAllEffectiveSubmissions({
    store: submissionPersistence,
    onAbort: (row) => abortExecutionSubmissionCommit(row.id, 'commit_lease_expired'),
  })
    .find((row) => ACTIVE_SUBMISSION_STATES.has(String(row?.state || '')));
  if (active) {
    const error = new ApiError('已有一次提交正在准备或等待确认，请先完成当前提交。', 409);
    error.code = 'ACTIVE_SUBMISSION_EXISTS';
    throw error;
  }
  const sameDayDecision = sameDayCompletionGate({
    groups: executionGroupPersistence.loadAll(),
    request: body,
    confirmationStore: sameDayConfirmationStore,
    deferManualConfirmation: true,
  });
  const request = { ...body };
  delete request.same_day_confirmation_token;
  delete request.sameDayConfirmationToken;
  const resumed = resumePausedSubmission({
    store: submissionPersistence,
    request,
    clientSubmissionId: String(request.client_submission_id),
  });
  if (resumed) return resumed;
  const created = claimSubmissionPreparation({
    store: submissionPersistence,
    clientSubmissionId: String(request.client_submission_id),
    createRecord: () => ({
    id: createExecutionPrepareId(),
    client_submission_id: String(request.client_submission_id),
    state: 'preparing',
    scope_hash: null,
    scope_version: null,
    resolved_action: null,
    discounts: {},
    targets: [],
    activity_buckets: {},
    excluded_buckets: {},
    seller_detection: { existing: [], existing_without_visible_id: [], confirmed_absent: [], needs_manual_review: [], unreadable: [] },
    live_read: { rows: [], readable_count: 0, blocked_count: 0, all_blocked: false },
    seller_input: { name: '', start_date: null, finish_date: null, selected_targets: [], validation_errors: [] },
    confirmation_summary: '',
    same_day_warning: sameDayDecision.warning || null,
    request,
    request_fingerprint: submissionRequestFingerprint(request),
    group_request: null,
    expires_at: null,
    group_id: null,
    group: null,
    error: null,
    progress: { stage: 'queued', percent: 0, completed: 0, total: 0, message: '正在排队核对执行范围' },
    }),
  });
  scheduleExecutionSubmissionPreparation(created.prepare.id);
  return created;
}

function updateExecutionSubmissionSellerInput(prepareId, body = {}) {
  const prepare = loadEffectiveSubmission({ store: submissionPersistence, prepareId });
  if (!prepare) throw new ApiError('未找到本次准备结果，请重新准备。', 404);
  if (prepare.state !== 'prepared') throw new ApiError('本次准备已进入提交阶段，不能再修改创建目标。', 409);
  const selections = normalizeSellerCampaignTargetSelections(body);
  const allowed = new Map((prepare.seller_detection?.confirmed_absent || []).map((target) => [sellerCampaignTargetKey(target), target]));
  const selectedTargets = selections.map((entry) => allowed.get(sellerCampaignTargetKey(entry))).filter(Boolean);
  if (selectedTargets.length !== selections.length) throw new ApiError('所选目标已变化或无法确认，请重新准备。', 409);
  const precheck = buildSellerCampaignBatchCreatePrecheck({
    targets: selectedTargets,
    name: body.name,
    startDate: body.startDate ?? body.start_date,
    finishDate: body.finishDate ?? body.finish_date,
  });
  const confirmedScope = createConfirmedExecutionScope({
    action: prepare.confirmed_execution_scope?.action || prepare.resolved_action,
    activities: prepare.confirmed_execution_scope?.activities || [],
    sellerCreateTargetKeys: selectedTargets.map(sellerCampaignTargetKey),
    sellerTargetStates: prepare.confirmed_execution_scope?.seller_target_states || sellerDetectionStates(prepare.seller_detection),
  });
  return submissionPersistence.update(prepare.id, {
    scope_hash: executionSubmissionScopeHash(confirmedScope, prepare.discounts),
    scope_version: new Date().toISOString(),
    confirmed_execution_scope: confirmedScope,
    group_request: { ...(prepare.group_request || {}), confirmedExecutionScope: confirmedScope },
    seller_input: {
      name: body.name || '',
      start_date: body.startDate ?? body.start_date ?? null,
      finish_date: body.finishDate ?? body.finish_date ?? null,
      selected_targets: selectedTargets,
      validation_errors: precheck.validation_errors || [],
    },
  });
}

async function commitExecutionSubmission(prepareId, body = {}) {
  if (submissionCommitTasks.has(String(prepareId))) {
    const current = submissionPersistence.load(prepareId);
    submissionPersistence.appendAudit(prepareId, { type: 'recovery_polled', reason: 'duplicate_commit_post_blocked', state: current?.state || 'committing' });
    const error = new ApiError('后台正在处理同一次提交，请继续查询当前状态。', 409);
    error.code = 'COMMIT_IN_PROGRESS';
    error.details = { prepare_id: String(prepareId), state: current?.state || 'committing' };
    throw error;
  }
  let current = refreshExecutionSubmissionExpiry(submissionPersistence.load(prepareId));
  if (!current) throw new ApiError('未找到本次准备结果，请重新准备。', 404);
  if (current.group_id) return { prepare: current, group: current.group || { id: current.group_id }, reused: true, accepted: false };
  if (String(body.confirmText || body.confirm_text || '') !== 'REAL_SUBMIT') {
    const error = new ApiError('最终提交需要 REAL_SUBMIT 确认。', 409);
    error.code = 'REAL_SUBMIT_REQUIRED';
    throw error;
  }
  const expectedConfirmationToken = String(current.execution_confirmation_token || '');
  const suppliedConfirmationToken = String(body.confirmationToken || body.confirmation_token || '');
  if (expectedConfirmationToken && suppliedConfirmationToken !== expectedConfirmationToken) {
    const error = new ApiError('本次最终确认已失效，请重新核对执行范围。', 409);
    error.code = 'EXECUTION_CONFIRMATION_INVALID';
    throw error;
  }
  const selected = current.seller_input?.selected_targets || [];
  if (selected.length && !current.create_confirmed
      && String(body.createConfirmText || body.create_confirm_text || '') !== 'CREATE_SELLER_CAMPAIGN') {
    const error = new ApiError('创建自建活动需要 CREATE_SELLER_CAMPAIGN 确认。', 409);
    error.code = 'CREATE_CONFIRM_REQUIRED';
    throw error;
  }
  if (!['prepared', 'reconfirm_required'].includes(String(current.state || ''))) {
    const error = new ApiError('本次准备状态已变化，请查询当前状态。', 409);
    error.code = current.state === 'expired' ? 'PREPARE_EXPIRED' : 'INVALID_SUBMISSION_STATE';
    throw error;
  }
  const operation = scheduleExecutionSubmissionCommit(prepareId, body, { recover: false });
  operation.catch((error) => {
    console.error(`已确认提交后台处理未完成：${submissionApiErrorMessage(error)}`);
  });
  current = submissionPersistence.load(prepareId) || current;
  return { prepare: current, group: null, reused: false, accepted: true };
}

function abortExecutionSubmissionCommit(prepareId, reason = 'abort_requested') {
  const controller = submissionCommitControllers.get(String(prepareId));
  if (controller && !controller.signal.aborted) controller.abort(reason);
}

function recordSubmissionRecoveryPoll(prepare) {
  if (!PRE_GROUP_SUBMISSION_STATES.has(String(prepare?.state || ''))) return;
  const key = String(prepare.id || '');
  const last = Number(submissionRecoveryAuditAt.get(key) || 0);
  if (!key || Date.now() - last < 30_000) return;
  submissionRecoveryAuditAt.set(key, Date.now());
  submissionPersistence.appendAudit(key, { type: 'recovery_polled', state: prepare.state });
}

async function revalidateExecutionSubmissionScope(prepare, context = {}, { readScheduler, operationReadCache } = {}) {
  context.checkpoint?.();
  const current = await buildExecutionSubmissionSnapshot(prepare.request, context.reportProgress || (() => {}), {
    ...context,
    revalidateTargetKeys: context.targetKeys || [],
    previousSnapshot: prepare,
    finalRevalidation: true,
    readScheduler,
    operationReadCache,
  });
  context.checkpoint?.();
  if (current.live_read?.all_blocked) {
    const error = new ApiError('所选活动实时读取均失败，本次未启动商品执行。请稍后重新准备。', 409);
    error.code = 'LIVE_READ_BLOCKED';
    error.details = current.live_read;
    throw error;
  }
  if (!prepare.confirmed_execution_scope) {
    const error = new ApiError('当前准备结果缺少冻结范围，请重新准备并确认。', 409);
    error.code = 'PREPARE_SCOPE_VERSION_REQUIRED';
    throw error;
  }
  const reconciled = reconcileConfirmedExecutionScope({
    confirmedScope: prepare.confirmed_execution_scope,
    observedScope: current.observed_execution_scope,
  });
  if (reconciled.execution_relation_count === 0 && reconciled.blocked_activity_count > 0) {
    const error = new ApiError('已确认活动的实时读取失败，本次未启动商品执行。请稍后重新准备。', 409);
    error.code = 'LIVE_READ_BLOCKED';
    error.details = { blocked_activity_count: reconciled.blocked_activity_count };
    throw error;
  }
  const sellerCreationOnly = reconciled.requires_reconfirm
    && Array.isArray(context.targetKeys)
    && context.targetKeys.includes('__SELLER__')
    && reconciled.structural_target_keys.length > 0
    && reconciled.structural_target_keys.every((key) => key === '__SELLER__');
  const requiresReprepare = reconciled.requires_reconfirm && !sellerCreationOnly;
  const confirmedScope = reconciled.requires_reconfirm
    ? reconciled.reconfirmation_scope
    : reconciled.execution_scope;
  const scopeHash = executionSubmissionScopeHash(confirmedScope, current.discounts);
  const businessChanges = reconciled.messages.length
    ? reconciled.messages
    : requiresReprepare ? ['执行动作或活动结构发生实质变化，需要重新核对范围'] : [];
  const scopeAdjustments = {
    messages: businessChanges,
    auto_removed_item_count: reconciled.auto_removed_item_count,
    excluded_new_item_count: reconciled.excluded_new_item_count,
    removed_activity_count: reconciled.removed_activity_count,
    blocked_activity_count: reconciled.blocked_activity_count,
  };
  return {
    scope_hash: scopeHash,
    reconfirm_required: requiresReprepare,
    execution_relation_count: reconciled.execution_relation_count,
    changes: businessChanges,
    changed_target_keys: reconciled.structural_target_keys,
    prepared_patch: executionSubmissionPreparedPatch(current, prepare, {
      confirmedScope,
      scopeHash,
      scopeMessages: businessChanges,
      scopeAdjustments,
    }),
    revalidation_record: {
      before: reconciled.before_summaries,
      after: reconciled.after_summaries,
      activity_diffs: reconciled.activity_diffs,
      structural_changes: reconciled.structural_changes,
      auto_removed_item_count: reconciled.auto_removed_item_count,
      excluded_new_item_count: reconciled.excluded_new_item_count,
      removed_activity_count: reconciled.removed_activity_count,
      blocked_activity_count: reconciled.blocked_activity_count,
      execution_relation_count: reconciled.execution_relation_count,
      total_activity_count: Number(current.revalidation_plan?.total_activity_count || 0),
      item_read_activity_count: Number(current.revalidation_plan?.item_read_activity_count || 0),
      scope_review_activity_count: Number(current.revalidation_plan?.scope_review_activity_count || 0),
      revalidation_reasons: current.revalidation_plan?.reasons_by_identity || {},
    },
  };
}

function scheduleExecutionSubmissionCommit(prepareId, body = {}, { recover = false } = {}) {
  const key = String(prepareId || '');
  if (!key) return Promise.reject(new ApiError('未找到本次准备结果，请重新准备。', 404));
  const existing = submissionCommitTasks.get(key);
  if (existing) return existing;
  const controller = new AbortController();
  const readScheduler = createBalancedReadScheduler(BALANCED_READ_PROFILES.prepare);
  const operationReadCache = new Map();
  submissionCommitControllers.set(key, controller);
  let leaseTimer = null;
  const operation = commitSubmission({
    store: submissionPersistence,
    prepareId: key,
    confirmText: body.confirmText || body.confirm_text,
    createConfirmText: body.createConfirmText || body.create_confirm_text,
    confirmationToken: body.confirmationToken || body.confirmation_token,
    leaseOwner: SERVER_INSTANCE_ID,
    recover,
    signal: controller.signal,
    revalidate: (prepare, context = {}) => revalidateExecutionSubmissionScope(prepare, context, { readScheduler, operationReadCache }),
    revalidateAfterCreation: (prepare, context = {}) => revalidateExecutionSubmissionScope(prepare, context, { readScheduler, operationReadCache }),
    createSellerCampaigns: async (prepare, context = {}) => {
      if ((prepare.seller_input?.validation_errors || []).length) {
        return { ok: false, failed_count: prepare.seller_input.validation_errors.length, validation_errors: prepare.seller_input.validation_errors };
      }
      const result = await createMissingSellerCampaigns({
        runId: context.runId || prepare.creation_run_id,
        missingTargets: prepare.seller_input?.selected_targets || [],
        existingTargets: prepare.seller_detection?.existing || [],
        notSelectedTargets: sellerCampaignNotSelectedTargets(
          prepare.seller_detection?.confirmed_absent || [],
          prepare.seller_input?.selected_targets || [],
          true,
        ),
        unreadableTargets: [],
        name: prepare.seller_input?.name,
        startDate: prepare.seller_input?.start_date,
        finishDate: prepare.seller_input?.finish_date,
        signal: context.signal,
        checkpoint: context.checkpoint,
        onProgress: context.persistProgress,
        readScheduler,
      });
      const hiddenTargets = result.existing_without_visible_id || [];
      const hiddenPatch = hiddenTargets.length ? applyHiddenSellerCampaignSkip(prepare, hiddenTargets) : null;
      if (hiddenPatch) {
        hiddenPatch.scope_hash = executionSubmissionScopeHash(hiddenPatch.confirmed_execution_scope, prepare.discounts);
      }
      return {
        ...result,
        ...(hiddenPatch ? { prepared_patch: hiddenPatch } : {}),
        ok: Number(result.failed_count || 0) === 0
          && Number(result.recheck_missing_count || 0) === 0
          && Number(result.unreadable_count || 0) === 0,
      };
    },
    startGroup: async (prepare, context = {}) => {
      context.checkpoint?.();
      return startExecutionGroupInternal({
        ...prepare.group_request,
        client_submission_id: prepare.client_submission_id,
        submission_prepare_id: prepare.id,
        confirmText: 'REAL_SUBMIT',
      });
    },
  });
  const leased = submissionPersistence.load(key);
  if (leased?.commit_lease_expires_at) {
    const delay = Math.max(1_000, new Date(leased.commit_lease_expires_at).getTime() - Date.now() + 50);
    leaseTimer = setTimeout(() => refreshExecutionSubmissionExpiry(submissionPersistence.load(key)), delay);
    leaseTimer.unref?.();
  }
  const task = operation.finally(() => {
    if (leaseTimer) clearTimeout(leaseTimer);
    submissionCommitTasks.delete(key);
    submissionCommitControllers.delete(key);
  });
  submissionCommitTasks.set(key, task);
  return task;
}

async function startExecutionGroupInternal(body = {}) {
  requireFields(body, ['client_submission_id', 'action']);
  let accountIds = normalizeAccountIdList(body.accountIds);
  if (!accountIds.length) throw new ApiError('请选择至少一个店铺账号。', 400);
  const prior = executionGroupPersistence.findBySubmissionId(body.client_submission_id);
  if (prior) return publicExecutionGroup(prior);
  if (hasConfirmedExecutionScope(body)) {
    const scope = body.confirmedExecutionScope || body.confirmed_execution_scope;
    if (Number(scope.version || 1) >= 2) {
      for (const activity of scope.activities || []) {
        normalizeAccountRoute(activity);
        if (!accountIds.includes(String(activity.account_id ?? activity.accountId ?? ''))) {
          const error = new ApiError('最终确认活动归属不在本次店铺范围内，已阻止创建执行组。', 422);
          error.code = 'ACTIVITY_ACCOUNT_ROUTE_MISMATCH';
          throw error;
        }
      }
    }
    const executableAccounts = new Set((scope.activities || [])
      .filter((activity) => (activity.item_ids || activity.itemIds || []).length > 0)
      .map((activity) => String(activity.account_id ?? activity.accountId ?? ''))
      .filter(Boolean));
    accountIds = accountIds.filter((accountId) => executableAccounts.has(String(accountId)));
    if (!accountIds.length) {
      const error = new ApiError('最终核对后没有可执行商品，本次未创建执行组。', 409);
      error.code = 'NO_CONFIRMED_TARGETS';
      throw error;
    }
    const selectedExecutableAccounts = new Set(accountIds.map(String));
    const missingRelations = [];
    for (const activity of scope.activities || []) {
      if (!selectedExecutableAccounts.has(String(activity.account_id ?? activity.accountId ?? ''))) continue;
      const available = new Set(listItems(
        activity.account_id ?? activity.accountId,
        activity.promotion_id ?? activity.promotionId,
        activity.promotion_type ?? activity.promotionType,
        activity.item_status ?? activity.itemStatus,
      ).map((item) => String(item.item_id ?? item.itemId ?? item.id ?? '').toUpperCase()));
      for (const itemId of activity.item_ids ?? activity.itemIds ?? []) {
        if (!available.has(String(itemId || '').toUpperCase())) missingRelations.push(`${activityIdentityKey(activity)}|${itemId}`);
      }
    }
    if (missingRelations.length) {
      const error = new ApiError('最终确认商品在本地验证结果中已发生变化，本次未创建执行组。请重新准备。', 409);
      error.code = 'CONFIRMED_SCOPE_CHANGED';
      error.details = { missing_relation_count: missingRelations.length };
      throw error;
    }
  }
  const settings = readSettings();
  const normalizedWriteConcurrency = normalizeWriteConcurrency(body.writeConcurrency, settings.writeConcurrency);
  const commonRequest = {
    ...body,
    accountIds,
    requestedWriteConcurrency: body.writeConcurrency,
    requestedGlobalWriteConcurrency: body.globalWriteConcurrency,
    writeConcurrency: normalizedWriteConcurrency,
    globalWriteConcurrency: normalizeConcurrencyWithCap(body.globalWriteConcurrency ?? normalizedWriteConcurrency, normalizedWriteConcurrency, MAX_WRITE_CONCURRENCY),
  };
  const protection = realSubmitProtection({ ...commonRequest, mode: commonRequest.mode || 'real' }, { batch: true });
  if (!protection.allowed) throw new ApiError(protection.error || '真实执行确认未通过。', protection.status || 409);
  const groupId = createExecutionGroupId();
  const storeNames = body.storeNames && typeof body.storeNames === 'object' ? body.storeNames : {};
  const jobs = accountIds.map((accountId) => createExecutionJob({
    ...commonRequest,
    accountId,
    storeName: storeNames[accountId] || '',
    selectedStoreName: storeNames[accountId] || '',
    executionGroupId: groupId,
  }));
  const createdAt = new Date().toISOString();
  const group = {
    id: groupId,
    client_submission_id: String(body.client_submission_id),
    submission_prepare_id: body.submission_prepare_id || null,
    status: 'queued', action: String(body.action), request: commonRequest,
    children: jobs.map((job) => executionGroupChildSnapshot(job)),
    created_at: createdAt, updated_at: createdAt, finished_at: null,
    cancel_requested: false, global_peak_in_flight: 0, result: null, error: null,
  };
  try {
    executionGroupPersistence.create(group);
  } catch (error) {
    for (const job of jobs) executionJobs.delete(job.id);
    throw error;
  }
  executionGroups.set(group.id, group);
  setImmediate(() => runExecutionGroup(group.id).catch((error) => failExecutionGroup(group.id, error)));
  return publicExecutionGroup(group);
}

async function runExecutionGroup(groupId) {
  const group = executionGroups.get(String(groupId)) || executionGroupPersistence.load(groupId);
  if (!group || TERMINAL_EXECUTION_GROUP_STATUSES.has(String(group.status || ''))) return;
  group.status = 'running';
  executionGroupPersistence.persist(group);
  const terminalJobStatuses = new Set(['completed', 'failed', 'cancelled', 'interrupted']);
  const jobs = (group.children || [])
    .map((child) => executionJobs.get(String(child.job_id)) || loadPersistedExecutionJob(child.job_id))
    .filter((job) => job && !terminalJobStatuses.has(String(job.status || '')));
  await Promise.all(jobs.map(async (job) => {
    executionJobs.set(job.id, job);
    try {
      await runExecutionJob(job.id);
    } catch (error) {
      failExecutionJob(job, error);
    } finally {
      executionGroupPersistence.updateChild(group.id, job);
    }
  }));
  const current = executionGroups.get(String(group.id)) || executionGroupPersistence.load(group.id) || group;
  const childStatuses = (current.children || []).map((child) => String(child.status || ''));
  if (childStatuses.includes('paused')) {
    current.status = 'paused';
    current.finished_at = null;
    current.result = summarizeExecutionGroup(current);
    executionGroupPersistence.persist(current);
    executionGroups.set(String(current.id), current);
    sharedWriteLimiters.delete(String(current.id));
    const pendingSince = Date.parse(String(current.pending_since || '')) || Date.now();
    current.pending_since = current.pending_since || new Date(pendingSince).toISOString();
    current.pending_retry_round = Number(current.pending_retry_round || 0) + 1;
    current.next_retry_at = new Date(Date.now() + 60_000).toISOString();
    executionGroupPersistence.persist(current);
    if (Date.now() - pendingSince < 30 * 60_000) {
      const timer = setTimeout(() => runExecutionGroup(current.id).catch((error) => failExecutionGroup(current.id, error)), 60_000);
      timer.unref?.();
    }
    return;
  }
  if (current.cancel_requested || childStatuses.includes('cancelled')) current.status = 'cancelled';
  else if (childStatuses.includes('interrupted')) current.status = 'interrupted';
  else if (childStatuses.includes('failed')) current.status = 'failed';
  else current.status = 'completed';
  current.finished_at = new Date().toISOString();
  current.result = summarizeExecutionGroup(current);
  current.global_peak_in_flight = Math.max(
    Number(current.global_peak_in_flight || 0),
    Number(sharedWriteLimiters.get(String(current.id))?.maxActive || 0)
  );
  executionGroupPersistence.persist(current);
  executionGroups.set(String(current.id), current);
  sharedWriteLimiters.delete(String(current.id));
  publishHistorySummaryForExecutionGroup(current.id);
  const submission = submissionPersistence.findBySubmissionId(current.client_submission_id);
  if (submission) submissionPersistence.update(submission.id, { state: 'terminal', group_id: current.id, group: publicExecutionGroup(current) });
}

function failExecutionGroup(groupId, error) {
  const group = executionGroups.get(String(groupId)) || executionGroupPersistence.load(groupId);
  if (!group) return;
  group.status = group.cancel_requested ? 'cancelled' : 'failed';
  group.finished_at = new Date().toISOString();
  group.error = toChineseError(error);
  group.result = summarizeExecutionGroup(group);
  executionGroupPersistence.persist(group);
  executionGroups.set(String(group.id), group);
  sharedWriteLimiters.delete(String(group.id));
  publishHistorySummaryForExecutionGroup(group.id);
  const submission = submissionPersistence.findBySubmissionId(group.client_submission_id);
  if (submission) submissionPersistence.update(submission.id, { state: 'terminal', group_id: group.id, group: publicExecutionGroup(group), error: group.error });
}

function publicExecutionJob(job) {
  return {
    id: job.id,
    run_id: job.run_id || null,
    status: job.status,
    cancel_requested: Boolean(job.cancel_requested),
    request_summary: job.request_summary,
    started_at: job.started_at,
    finished_at: job.finished_at,
    progress: job.progress,
    logs: job.logs,
    userLogs: job.userLogs,
    result: job.result,
    error: job.error
  };
}

function persistExecutionJob(job) {
  executionJobPersistence.persist(job);
  const groupId = String(job.request?.executionGroupId || job.request_summary?.executionGroupId || '');
  if (groupId) {
    const group = executionGroupPersistence.updateChild(groupId, job);
    if (group) executionGroups.set(String(group.id), group);
  }
}

function loadPersistedExecutionJob(jobId) {
  return executionJobPersistence.load(jobId);
}

function appendExecutionJobLog(job, message, extra = {}) {
  job.logs.push({ at: new Date().toISOString(), message, ...extra });
  if (job.logs.length > 1000) job.logs.splice(0, job.logs.length - 1000);
}

function appendExecutionUserLog(job, message, extra = {}) {
  job.userLogs.push({ at: new Date().toISOString(), message, ...extra });
  if (job.userLogs.length > 1000) job.userLogs.splice(0, job.userLogs.length - 1000);
}

function ensureExecutionJobEventDir() {
  fs.mkdirSync(EXECUTION_JOB_EVENT_DIR, { recursive: true });
}

function safeExecutionJobId(jobId) {
  return String(jobId || '').replace(/[^A-Za-z0-9_.-]/g, '');
}

function executionJobEventPath(jobOrId) {
  const key = typeof jobOrId === 'object' && jobOrId
    ? (jobOrId.event_file_key || jobOrId.run_id || jobOrId.id)
    : jobOrId;
  return path.join(EXECUTION_JOB_EVENT_DIR, `${safeExecutionJobId(key)}.jsonl`);
}

function findExecutionJobEventPath(jobOrId) {
  const direct = executionJobEventPath(jobOrId);
  if (fs.existsSync(direct)) return direct;
  if (fs.existsSync(`${direct}.gz`)) return `${direct}.gz`;
  const jobId = typeof jobOrId === 'object' && jobOrId ? jobOrId.id : jobOrId;
  const safeJobId = safeExecutionJobId(jobId);
  if (!safeJobId) return direct;
  try {
    const candidates = fs.readdirSync(EXECUTION_JOB_EVENT_DIR)
      .filter((name) => name.endsWith('.jsonl') || name.endsWith('.jsonl.gz'))
      .filter((name) => name === `${safeJobId}.jsonl` || name === `${safeJobId}.jsonl.gz` || name.startsWith(`${safeJobId}-`))
      .map((name) => path.join(EXECUTION_JOB_EVENT_DIR, name))
      .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);
    for (const candidate of candidates) {
      const firstLine = readAuditText(candidate).split(/\r?\n/).find(Boolean);
      if (!firstLine) continue;
      try {
        const event = JSON.parse(firstLine);
        if (String(event.jobId || '') === String(jobId || '') || String(event.run_id || '') === String(jobId || '')) {
          return candidate;
        }
      } catch {
        // Ignore malformed legacy event files and keep scanning.
      }
    }
  } catch {
    return direct;
  }
  return direct;
}

function appendExecutionJobEvent(job, event) {
  if (!job?.id) return null;
  ensureExecutionJobEventDir();
  const row = {
    at: new Date().toISOString(),
    jobId: job.id,
    run_id: job.run_id || null,
    group_id: job.request?.executionGroupId || job.request_summary?.executionGroupId || null,
    ...event
  };
  const eventPath = executionJobEventPath(job);
  let descriptor = executionEventFileDescriptors.get(eventPath);
  if (descriptor === undefined) {
    descriptor = fs.openSync(eventPath, 'a');
    executionEventFileDescriptors.set(eventPath, descriptor);
  }
  fs.writeSync(descriptor, `${JSON.stringify(row)}\n`, null, 'utf8');
  job.last_event_at = row.at;
  return row;
}

process.on('exit', () => {
  for (const descriptor of executionEventFileDescriptors.values()) {
    try { fs.closeSync(descriptor); } catch { /* Process exit cleanup only. */ }
  }
  executionEventFileDescriptors.clear();
});

function readExecutionJobEvents(jobId, limit = 5000) {
  const job = executionJobs.get(String(jobId));
  const file = findExecutionJobEventPath(job || jobId);
  try {
    const lines = readAuditText(file).split(/\r?\n/).filter(Boolean);
    const safeLimit = Math.max(1, Math.min(50000, Math.floor(Number(limit) || 5000)));
    const events = lines.slice(-safeLimit).map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return { parse_error: true, raw: line.slice(0, 500) };
      }
    });
    return job?.run_id ? events.filter((event) => event.run_id === job.run_id) : events;
  } catch {
    return [];
  }
}

function executionItemsFromEvents(events = []) {
  return events
    .filter((event) => event.item && ['item_start', 'item_finish', 'item_skipped', 'item_cancelled_before_start', 'item_remaining_started'].includes(event.type))
    .map((event) => ({
      event_type: event.type,
      at: event.at,
      ...event.item
    }));
}

function summarizeExecutionUniqueItems(items = []) {
  const byItem = new Map();
  for (const item of items) {
    const itemId = String(item.itemId || item.item_id || '');
    if (!itemId) continue;
    const key = [
      item.accountId || item.account_id || '',
      item.promotionId || item.promotion_id || '',
      item.promotionType || item.promotion_type || '',
      itemId
    ].join('|');
    if (!byItem.has(key)) {
      byItem.set(key, {
        hasPlanEvent: false,
        hasWriteEvent: false,
        success: false,
        failed: false,
        skipped: false,
        remainingStarted: false
      });
    }
    const row = byItem.get(key);
    const eventType = String(item.event_type || '');
    const status = String(item.result_status || item.status || '').toLowerCase();
    if (['item_start', 'item_finish', 'item_skipped', 'item_cancelled_before_start'].includes(eventType)) {
      row.hasPlanEvent = true;
    }
    if (['item_start', 'item_finish'].includes(eventType)) {
      row.hasWriteEvent = true;
    }
    if (status === 'success') row.success = true;
    else if (status === 'failed') row.failed = true;
    else if (status === 'skipped' || status === 'cancelled') row.skipped = true;
    else if (status === 'remaining_started' || eventType === 'item_remaining_started') row.remainingStarted = true;
  }
  const summary = {
    planned_unique: 0,
    write_attempt_unique: 0,
    success_unique: 0,
    failed_unique: 0,
    skipped_unique: 0,
    remaining_started_unique: 0
  };
  for (const item of byItem.values()) {
    if (item.hasPlanEvent) {
      summary.planned_unique += 1;
    }
    if (item.hasWriteEvent) {
      summary.write_attempt_unique += 1;
    }
    if (item.success) summary.success_unique += 1;
    else if (item.failed) summary.failed_unique += 1;
    else if (item.skipped) summary.skipped_unique += 1;
    if (item.remainingStarted) summary.remaining_started_unique += 1;
  }
  return summary;
}

function appendExecutionItemAuditEvent(jobId, {
  type,
  account,
  promotion,
  taskId,
  row,
  action,
  status,
  reason,
  startedAt,
  finishedAt,
  durationMs,
  response,
  error,
  errorCn,
  sentToApi = true,
  isInterfaceFailure = false,
  retryRound = null,
  policyBlocked = false,
  attempt = null,
  retryCount = null,
  deferred = false,
  finalRetry = false
} = {}) {
  if (!jobId) return null;
  const item = row?.item || {};
  const errorInfo = error ? classifyExecutionWriteError(error) : {};
  const event = {
    type,
    item: {
      taskId: taskId ?? null,
      accountId: String(account?.account_id || ''),
      storeName: account?.storeName || storeIdentityForAccount(account?.account_id, account || {}).store_name,
      siteId: promotion?.site_id || item.site_id || '',
      siteName: siteDisplayName(promotion?.site_id || item.site_id || ''),
      promotionId: promotion?.promotion_id || '',
      promotionType: promotion?.promotion_type || '',
      promotionName: promotion?.name || '',
      itemId: item.item_id || item.id || '',
      offer_id: smartCancelFieldEvidence({ promotion, item }).offer_id || null,
      action,
      targetDealPrice: row?.deal_price ?? null,
      status,
      result_status: status,
      startedAt: startedAt || null,
      finishedAt: finishedAt || null,
      duration_ms: durationMs ?? null,
      http_status: errorInfo.status ?? error?.status ?? null,
      error_category: errorInfo.category || null,
      error_cn: errorCn || reason || null,
      raw_error_summary: executionRawErrorSummary(error),
      sent_to_api: Boolean(sentToApi) && !policyBlocked,
      is_interface_failure: Boolean(isInterfaceFailure || errorInfo.interfaceFailure),
      is_business_failure: Boolean(error && !(isInterfaceFailure || errorInfo.interfaceFailure)),
      attempt,
      retry_count: retryCount,
      retry_round: retryRound ?? retryCount,
      deferred: Boolean(deferred),
      final_retry: Boolean(finalRetry),
      request_summary: smartCancelAuditRequestSummary({ account, action, promotion, item }),
      response_summary: response ? responseSummary(response) : null
    }
  };
  return appendExecutionJobEvent(executionJobs.get(String(jobId)) || { id: jobId }, event);
}

function smartCancelAuditRequestSummary({ account = {}, action, promotion = {}, item = {} } = {}) {
  if (action !== 'cancel' || String(promotion.promotion_type || '').toUpperCase() !== 'SMART') return null;
  try {
    const preview = buildSmartCancelRequestPreview({ account, promotion, item, marketplace: isMarketplaceCampaign(account, promotion) });
    return {
      method: preview.method,
      endpoint_family: preview.endpoint_family,
      path_template: preview.path_template,
      query: preview.query
    };
  } catch (error) {
    return {
      method: 'DELETE',
      endpoint_family: 'seller-promotions offer cancel',
      build_error: toChineseError(error)
    };
  }
}

function executionRawErrorSummary(error) {
  if (!error) return null;
  const body = error.body || error.details || error.message || error;
  try {
    return JSON.stringify(body).slice(0, 500);
  } catch {
    return String(body).slice(0, 500);
  }
}

function classifyExecutionWriteError(error = {}) {
  const status = Number(error.status || error.body?.status || error.details?.status || 0);
  const raw = executionRawErrorSummary(error) || '';
  const lower = raw.toLowerCase();
  const errorCn = toChineseError(error);
  const rateLimited = status === 429 || /too many|rate.?limit|429|限流/.test(lower);
  const authFailure = status === 401 || /invalid_token|unauthorized|401/.test(lower);
  const businessFailure = !rateLimited && !authFailure && (
    (status >= 400 && status < 500)
    || /offer_id|offer id|invalid_parameter|bad_request|under_review|lockedentity|price/.test(lower)
    || /活动报价|报价|参数|审核|价格|缺少或无效/.test(errorCn)
  );
  const serverFailure = !businessFailure && (status >= 500 || /5\d\d|server|temporarily|service unavailable/.test(lower));
  const timeoutFailure = /timeout|timed out|504|fetch failed|socket|network|econnreset|etimedout|und_err|aborted/.test(lower);
  const transientFailure = !businessFailure && (serverFailure || timeoutFailure);
  return {
    status: status || null,
    category: rateLimited ? 'rate_limited' : authFailure ? 'auth_failure' : transientFailure ? 'transient_interface_failure' : businessFailure ? 'business_failure' : null,
    interfaceFailure: !businessFailure && (rateLimited || authFailure || transientFailure),
    rateLimited,
    authFailure,
    transientFailure,
    businessFailure,
    errorCn
  };
}

function describeFilters(filters = {}) {
  const siteIds = Array.isArray(filters.siteIds) ? filters.siteIds : String(filters.siteId || '').split(',').filter(Boolean);
  const types = Array.isArray(filters.promotionTypes) ? filters.promotionTypes : String(filters.promotionType || '').split(',').filter(Boolean);
  const keywords = Array.isArray(filters.keywords) ? filters.keywords : String(filters.name || '').split(',').filter(Boolean);
  return [
    `站点=${siteIds.length ? siteIds.join('/') : '全部站点'}`,
    `活动类型=${types.length ? types.join('/') : '全部类型'}`,
    `活动=${keywords.length ? keywords.join('/') : '全部活动'}`,
    filters.excludeSeller ? '不处理自建活动' : '',
    filters.excludeOfficial ? '不处理官方活动' : ''
  ].filter(Boolean).join('；');
}

function siteDisplayName(siteId = '') {
  const map = {
    MCO: '哥伦比亚站',
    MLA: '阿根廷站',
    MLB: '巴西站',
    MLC: '智利站',
    MLM: '墨西哥站',
    MLU: '乌拉圭站',
    MPE: '秘鲁站',
    MEC: '厄瓜多尔站',
    CBT: '跨境店'
  };
  return map[String(siteId || '').toUpperCase()] || (siteId ? `站点 ${siteId}` : '全部站点');
}

async function inspectOperatingSiteEvidence({ account, sites = [], settings = readSettings() } = {}) {
  const storeName = storeIdentityForAccount(account.account_id, account, settings).store_name;
  const rows = await mapLimited(sites, Math.min(10, normalizeConcurrency(settings.readConcurrency || 2)), async (site) => {
    const childUserId = String(site.child_user_id || '').trim();
    const base = {
      account_id: String(account.account_id),
      store_name: storeName,
      site_id: String(site.site_id || '').trim().toUpperCase(),
      site_name: siteDisplayName(site.site_id),
      activity_count: Number(site.total || site.last_promotion_count || 0),
      child_user_id: childUserId,
      logistic_type: String(site.logistic_type || '')
    };
    if (!childUserId) return { ...base, active_probe_ok: false, active_listing_count: 0 };
    try {
      const client = new MercadoLibreClient({
        accessToken: account.accessToken,
        userId: childUserId,
        callerId: childUserId,
        marketplace: true
      });
      const response = await client.searchMarketplaceUserItems({ userId: childUserId, status: 'active', limit: 1, searchType: 'scan' });
      return {
        ...base,
        active_probe_ok: true,
        active_listing_count: Math.max(0, Number(response?.paging?.total ?? (response?.results || []).length) || 0)
      };
    } catch {
      return { ...base, active_probe_ok: false, active_listing_count: 0 };
    }
  });
  return mergeOperatingSiteEvidence(rows, settings);
}

function listOperatingCampaignsFiltered(accountId, filters = {}, settings = readSettings()) {
  return filterByOperatingSites(listCampaignsFiltered(accountId, filters), settings, accountId)
    .filter((promotion) => !['catalog_removed', 'finished', 'ended', 'closed'].includes(String(promotion.status || '').toLowerCase()))
    .filter((promotion) => !isActivityExpired(promotion));
}

function actionDisplayName(action = '') {
  return {
    enroll: '批量报活动',
    update: '批量更新',
    cancel: '批量取消',
    auto: '自动判断',
    '': '自动判断'
  }[String(action || '')] || String(action || '自动判断');
}

function actionVerb(action = '') {
  return {
    enroll: '报名',
    update: '更新',
    cancel: '取消'
  }[String(action || '')] || '处理';
}

function itemStatusDisplayName(status = '') {
  return {
    candidate: '报名候选商品',
    started: '已报名商品',
    pending: '待生效商品'
  }[String(status || '')] || '活动商品';
}

function promotionDisplayName(promotion = {}) {
  return String(promotion.name || promotion.promotion_name || '').trim()
    || promotionTypeDisplayName(promotion.promotion_type);
}

function promotionTypeDisplayName(type = '') {
  return {
    SELLER_CAMPAIGN: '自建活动',
    DEAL: '官方活动',
    SMART: '智能折扣',
    LIGHTNING: '限时秒杀',
    BATCH: '多个活动'
  }[String(type || '').toUpperCase()] || '活动';
}

function businessScope({ storeName, promotion, siteId } = {}) {
  const site = siteDisplayName(promotion?.site_id || siteId || '');
  const activity = promotion ? promotionDisplayName(promotion) : '';
  return [storeName || '当前店铺', site, activity].filter(Boolean).join(' / ');
}

function readCompletenessText(event = {}) {
  const platformTotal = numberOrNull(event.platform_total);
  const savedCount = numberOrNull(event.saved_count) ?? 0;
  const base = `平台商品 ${platformTotal ?? '-'}，已读取 ${savedCount}`;
  if (event.error || event.blocked) return `${base}，读取异常`;
  if (event.is_full_fetch) return `${base}，已完整读取`;
  if (event.sample_only) return `${base}，样本读取`;
  const missing = platformTotal === null ? 0 : Math.max(0, platformTotal - savedCount);
  return missing > 0 ? `${base}，读取不完整，未返回明细 ${missing}` : `${base}，读取完成`;
}

function businessReasonText(reason = '') {
  const raw = String(reason || '');
  const text = (/[\u4e00-\u9fff]/.test(raw) ? raw : toChineseError(raw)).replace(/_/g, ' ');
  return text
    .replace(/\bapi incomplete marketplace candidate\b/gi, '商品明细不完整')
    .replace(/\bpartial api sparse marketplace candidate\b/gi, '平台商品明细读取不完整')
    .replace(/\bparameters unconfirmed\b/gi, '活动参数未确认')
    .replace(/\brunning\b/gi, '执行中')
    .replace(/\bpartial or failed\b/gi, '部分完成/有失败')
    .replace(/\bempty or failed\b/gi, '未执行/无可处理商品');
}

function formatDuration(ms) {
  const seconds = Math.max(0, Math.round(ms / 1000));
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return minutes > 0 ? `${minutes}分${rest}秒` : `${rest}秒`;
}

function summarizePromotionScope(promotions) {
  const bySite = new Map();
  const byType = new Map();
  for (const promotion of promotions) {
    bySite.set(promotion.site_id || '-', (bySite.get(promotion.site_id || '-') || 0) + 1);
    byType.set(promotion.promotion_type || 'UNKNOWN', (byType.get(promotion.promotion_type || 'UNKNOWN') || 0) + 1);
  }
  const siteText = [...bySite.entries()].map(([site, count]) => `${site} ${count}`).join('，') || '无站点';
  const typeText = [...byType.entries()].map(([type, count]) => `${type} ${count}`).join('，') || '无类型';
  return `${siteText}；${typeText}`;
}

function countBusinessSitesForPromotions(promotions = []) {
  const keys = new Set();
  for (const promotion of promotions) {
    const siteId = String(promotion.site_id || '');
    if (!siteId) continue;
    keys.add(`${promotion.account_id || ''}|${siteId}`);
  }
  return keys.size;
}

function formatFetchProgress(event) {
  const platformTotal = numberOrNull(event.platform_total);
  const savedCount = numberOrNull(event.saved_count) ?? 0;
  const missing = platformTotal === null ? null : Math.max(0, platformTotal - savedCount);
  const fullText = event.is_full_fetch ? 'full' : event.sample_only ? 'sample/partial' : 'partial';
  const parts = [
    `平台 ${platformTotal ?? '-'}`,
    `已读 ${savedCount}`,
    missing && missing > 0 ? `未返回明细 ${missing}` : '',
    `状态 ${event.detail_status || '-'}`,
    `读取 ${fullText}`,
    event.stop_reason ? `停止原因 ${event.stop_reason}` : '',
    event.note ? String(event.note).slice(0, 80) : ''
  ];
  return parts.filter(Boolean).join('，');
}

function formatPlanProgress(entry, index, total) {
  const promotion = entry.promotion || {};
  if (entry.blocked) {
    return `计划 ${index + 1}/${total}：${promotion.site_id || '-'} ${promotion.promotion_id} ${promotion.promotion_type} 阻断，原因：${entry.warning || entry.detail_status || '不满足执行条件'}`;
  }
  const plan = entry.plan || {};
  const fetch = entry.fetch_info || {};
  const platformTotal = fetch.platform_total ?? '-';
  const savedCount = fetch.saved_count ?? plan.total ?? 0;
  const readState = fetch.is_full_fetch ? 'full' : fetch.sample_only ? 'sample/partial' : (fetch.detail_status || 'unknown');
  return `计划 ${index + 1}/${total}：${promotion.site_id || '-'} ${promotion.promotion_id} ${promotion.promotion_type}，平台 ${platformTotal}，已读 ${savedCount}，读取 ${readState}，商品 ${plan.total || 0}，可执行 ${plan.planned || 0}，跳过 ${plan.skipped || 0}`;
}

function formatPlanUserProgress(entry, action, storeName) {
  const promotion = entry.promotion || {};
  if (entry.blocked) {
    return `${businessScope({ storeName, promotion })}：暂不能${actionVerb(action)}，${businessReasonText(entry.warning || entry.detail_status || '不满足执行条件')}。`;
  }
  const plan = entry.plan || {};
  return `${businessScope({ storeName, promotion })}：计划${actionVerb(action)} ${plan.planned || 0} 个，跳过 ${plan.skipped || 0} 个。`;
}

function appendSiteActivityUserLogs(job, account, filters, storeName) {
  const siteIds = new Set(Array.isArray(filters.siteIds) ? filters.siteIds.map(String) : String(filters.siteId || '').split(',').filter(Boolean));
  const sites = filterByOperatingSites(listSiteSummaries(account.account_id), readSettings(), account.account_id)
    .filter((site) => !siteIds.size || siteIds.has(String(site.site_id || '')));
  appendExecutionUserLog(job, '加载店铺站点列表...');
  if (!sites.length) {
    appendExecutionUserLog(job, '店铺站点列表完成');
    appendExecutionUserLog(job, '店铺站点数：0');
    appendExecutionUserLog(job, '已加载店铺站点：0 个');
    appendExecutionUserLog(job, '其中当前有活动：0 个，未开放/未读取到活动：0 个');
    appendExecutionUserLog(job, `${storeName} / ${siteIds.size ? [...siteIds].map(siteDisplayName).join('、') : '全部站点'}：未读取到站点活动信息。`);
    return;
  }
  const activityByBusinessSite = new Map();
  for (const site of sites) {
    const siteId = String(site.site_id || '');
    if (!siteId) continue;
    const current = activityByBusinessSite.get(siteId) || false;
    activityByBusinessSite.set(siteId, current || Number(site.total || site.last_promotion_count || 0) > 0);
  }
  const activeSiteCount = [...activityByBusinessSite.values()].filter(Boolean).length;
  const inactiveSiteCount = Math.max(0, activityByBusinessSite.size - activeSiteCount);
  appendExecutionUserLog(job, '店铺站点列表完成');
  appendExecutionUserLog(job, `店铺站点数：${activityByBusinessSite.size}`);
  appendExecutionUserLog(job, `已加载店铺站点：${activityByBusinessSite.size} 个`);
  appendExecutionUserLog(job, `其中当前有活动：${activeSiteCount} 个，未开放/未读取到活动：${inactiveSiteCount} 个`);
  const grouped = new Map();
  for (const site of sites) {
    const siteId = String(site.site_id || '');
    const key = siteId || siteDisplayName(siteId);
    const current = grouped.get(key) || { siteId, total: 0, errors: [] };
    current.total += Number(site.total || 0);
    if (site.error) current.errors.push(toChineseError(site.error));
    grouped.set(key, current);
  }
  for (const site of grouped.values()) {
    if (site.total > 0) {
      appendExecutionUserLog(job, `${storeName} / ${siteDisplayName(site.siteId)}：读取到活动 ${site.total} 个。`);
    } else if (site.errors.length) {
      appendExecutionUserLog(job, `${storeName} / ${siteDisplayName(site.siteId)}：活动读取失败，${site.errors[0]}。`);
    } else {
      appendExecutionUserLog(job, `${storeName} / ${siteDisplayName(site.siteId)}：未读取到匹配活动。`);
    }
  }
}

function topPlanReasons(entry, limit = 3) {
  const counts = new Map();
  if (entry.blocked) {
    const reason = entry.warning || entry.detail_status || '活动阻断';
    counts.set(reason, 1);
  }
  for (const row of entry.plan?.rows || []) {
    if (row.status !== 'skipped') continue;
    const reason = row.reason || '跳过';
    counts.set(reason, (counts.get(reason) || 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([reason, count]) => ({ reason, count }));
}

function summarizeExecutionFailureReasons(execution, limit = 5) {
  const counts = new Map();
  for (const promotion of execution.promotions || []) {
    if (promotion.reason) counts.set(promotion.reason, (counts.get(promotion.reason) || 0) + Number(promotion.failed || 1));
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([reason, count]) => ({ reason, count }));
}

function displayExecutionTotal(execution = {}) {
  return Math.max(
    Number(execution.total || 0),
    Number(execution.success || 0) + Number(execution.failed || 0) + Number(execution.skipped || 0) + Number(execution.pending || 0)
  );
}

function mergeExecutionRecoveryRound(previous = {}, current = {}) {
  const promotionKeyForResult = (row = {}) => [
    String(row.site_id || '').toUpperCase(),
    String(row.promotion_id || ''),
    String(row.promotion_type || '').toUpperCase(),
  ].join('|');
  const promotions = new Map((previous.promotions || []).map((row) => [promotionKeyForResult(row), { ...row }]));
  for (const row of current.promotions || []) {
    const key = promotionKeyForResult(row);
    const prior = promotions.get(key) || {};
    promotions.set(key, {
      ...prior,
      ...row,
      total: Math.max(Number(prior.total || 0), Number(row.total || 0)),
      success: Number(prior.success || 0) + Number(row.success || 0),
      failed: Number(prior.failed || 0) + Number(row.failed || 0),
      skipped: Number(prior.skipped || 0) + Number(row.skipped || 0),
      pending: Number(row.pending || 0),
    });
  }
  return {
    ...previous,
    ...current,
    promotions: [...promotions.values()],
    total: Math.max(Number(previous.total || 0), Number(current.total || 0)),
    relation_count: Math.max(Number(previous.relation_count || 0), Number(current.relation_count || 0)),
    unique_item_count: Math.max(Number(previous.unique_item_count || 0), Number(current.unique_item_count || 0)),
    success: Number(previous.success || 0) + Number(current.success || 0),
    failed: Number(previous.failed || 0) + Number(current.failed || 0),
    skipped: Number(previous.skipped || 0) + Number(current.skipped || 0),
    pending: Number(current.pending || 0),
    recovery_round: Number(previous.recovery_round || 0) + 1,
  };
}

function displayProgressTotal(event = {}) {
  return Math.max(
    Number(event.total_items || 0),
    Number(event.success || 0) + Number(event.failed || 0) + Number(event.skipped || 0) + Number(event.pending || 0)
  );
}

function executionTaskIdentity(request = {}) {
  return {
    executionGroupId: request.executionGroupId || null,
    executionJobId: request.executionJobId || null
  };
}

function saveBatchExecutionSummaryTask({ account, action, execution, completed, request = {}, existingTaskId = null }) {
  const plan = {
    total: execution.total || 0,
    planned: execution.success || 0,
    skipped: execution.skipped || 0,
    priceMode: 'batch',
    rows: []
  };
  const taskId = Number(existingTaskId || 0) || createTask({
      accountId: account.account_id,
      promotionId: '__BATCH__',
      promotionType: 'BATCH',
      action,
      mode: 'real',
      discountPercent: null,
      directPrice: null,
      plan,
      ...executionTaskIdentity(request)
    });
  finishTask(taskId, {
    success: execution.success || 0,
    failed: execution.failed || 0,
    skipped: execution.skipped || 0,
    pending: execution.pending || 0,
    blocked: execution.blocked || 0,
    planned: execution.success || 0,
    total: execution.total || 0,
    promotions_total: execution.promotions_total || 0,
    result_contract_version: execution.result_contract_version || RESULT_CONTRACT_VERSION,
    relation_count: execution.relation_count ?? execution.total ?? 0,
    unique_item_count: execution.unique_item_count ?? null,
    activity_failure_count: execution.activity_failure_count ?? 0,
    request_success_count: execution.request_success_count ?? 0,
    live_verified_removed_count: execution.live_verified_removed_count ?? 0,
    pending_verification_count: execution.pending_verification_count ?? 0,
    failure_reasons: summarizeExecutionFailureReasons(execution)
  }, completed && !execution.cancelled && (execution.failed || 0) === 0 && (execution.activity_failure_count || 0) === 0
    ? 'completed'
    : execution.cancelled ? 'cancelled' : 'partial_or_failed', completed, {
    publishHistory: !request.executionGroupId
  });
  return taskId;
}

function failExecutionJob(job, error) {
  if (job.request?.executionGroupId && !job.batch_task_id) {
    const plan = { total: 0, planned: 0, skipped: 0, priceMode: 'batch', rows: [] };
    const taskId = createTask({
      accountId: job.request.accountId,
      promotionId: '__BATCH__',
      promotionType: 'BATCH',
      action: job.request.action || 'auto',
      mode: 'real',
      discountPercent: null,
      directPrice: null,
      plan,
      ...executionTaskIdentity({ ...job.request, executionJobId: job.id })
    });
    finishTask(taskId, {
      success: 0,
      failed: 0,
      skipped: 0,
      planned: 0,
      total: 0,
      promotions_total: 0,
      failure_reasons: [{ reason: toChineseError(error), count: 1 }]
    }, 'failed', false, { publishHistory: false });
    job.batch_task_id = Number(taskId);
  }
  job.status = job.cancel_requested ? 'cancelled' : 'failed';
  job.error = toChineseError(error);
  job.finished_at = new Date().toISOString();
  appendExecutionUserLog(job, job.status === 'cancelled' ? '执行任务已停止。' : `执行失败：${job.error}`);
  appendExecutionJobLog(job, job.status === 'cancelled' ? '执行任务已停止。' : `执行任务失败：${job.error}`);
  persistExecutionJob(job);
}

async function runExecutionJob(jobId) {
  const job = executionJobs.get(jobId);
  if (!job) return;
  job.status = 'running';
  job.progress.stage = 'starting';
  appendExecutionJobLog(job, '开始准备执行。');
  persistExecutionJob(job);
  try {
    const request = job.request;
    const settings = readSettings();
    const account = await ensureUsableAccount(request.accountId);
    const filters = request.filters || settings.defaultFilters || {};
    const jobStartedMs = Date.now();
    const storeName = request.storeName || request.selectedStoreName || storeIdentityForAccount(account.account_id, account, settings).store_name;
    const selectedSiteName = request.selectedSiteName || (filters.siteId ? siteDisplayName(filters.siteId) : '全部站点');
    const jobReadConcurrency = normalizeConcurrency(request.readConcurrency, settings.readConcurrency);
    const jobSiteConcurrency = normalizeConcurrency(request.siteConcurrency ?? request.readConcurrency, settings.readConcurrency);
    const jobActivityConcurrency = normalizeConcurrency(request.activityConcurrency ?? request.previewConcurrency ?? settings.previewConcurrency, settings.previewConcurrency);
    const jobWriteConcurrency = normalizeWriteConcurrency(request.writeConcurrency, settings.writeConcurrency);
    const jobGlobalWriteConcurrency = normalizeConcurrencyWithCap(request.globalWriteConcurrency ?? jobWriteConcurrency, jobWriteConcurrency, MAX_WRITE_CONCURRENCY);
    Object.assign(job.progress, {
      requested_write_concurrency: request.requestedWriteConcurrency ?? request.writeConcurrency,
      write_concurrency: jobWriteConcurrency,
      requested_global_write_concurrency: request.requestedGlobalWriteConcurrency ?? request.globalWriteConcurrency,
      global_write_concurrency: jobGlobalWriteConcurrency
    });

    job.progress.stage = 'promotions';
    appendExecutionUserLog(job, `开始${actionDisplayName(request.action || '')}：店铺=${request.selectedStoreName || storeName}，站点=${selectedSiteName}，自建${request.sellerDiscountPercent ?? settings.sellerDefaultDiscount}%，官方${request.officialDiscountPercent ?? settings.officialDefaultDiscount}%，读取并发=${jobReadConcurrency}，站点并发=${jobSiteConcurrency}，活动并发=${jobActivityConcurrency}，商品写入并发=${jobWriteConcurrency}，全局写入上限=${jobGlobalWriteConcurrency}。`);
    if (String(request.requestedWriteConcurrency ?? jobWriteConcurrency) !== String(jobWriteConcurrency) || String(request.requestedGlobalWriteConcurrency ?? jobGlobalWriteConcurrency) !== String(jobGlobalWriteConcurrency)) {
      appendExecutionUserLog(job, `并发口径：请求商品写入并发=${request.requestedWriteConcurrency ?? '-'}，请求全局写入上限=${request.requestedGlobalWriteConcurrency ?? '-'}；本次实际商品写入并发=${jobWriteConcurrency}，实际全局写入上限=${jobGlobalWriteConcurrency}。`);
    }
    appendExecutionJobLog(job, `开始：店铺账号 ${account.account_id}，模式 ${request.action || '自动判断'}，自建折扣 ${request.sellerDiscountPercent ?? settings.sellerDefaultDiscount}%，官方折扣 ${request.officialDiscountPercent ?? settings.officialDefaultDiscount}%，读取并发 ${request.readConcurrency ?? settings.readConcurrency}，站点并发 ${jobSiteConcurrency}，活动并发 ${jobActivityConcurrency}，写入并发 ${jobWriteConcurrency}，全局写入上限 ${jobGlobalWriteConcurrency}。`);
    appendExecutionJobLog(job, `筛选：${describeFilters(filters)}；商品读取策略：${request.fetchMode === 'sample' ? `样本 ${request.maxItems || settings.maxItemsPerPromotion || 50}/活动` : '全量读取，平台 partial 时记录可见子集'}。`);
    appendExecutionUserLog(job, `并发读取站点活动：店铺站点并发=${jobSiteConcurrency}，读取并发=${jobReadConcurrency}。`);
    appendExecutionJobLog(job, `刷新并匹配活动，读取并发 ${jobReadConcurrency}。`);
    const frozenScope = hasConfirmedExecutionScope(request);
    const promotionPrep = frozenScope
      ? (() => {
          const localPromotions = ordinaryPromotions(listOperatingCampaignsFiltered(account.account_id, filters || {}, settings));
          const filtered = filterPromotionsByConfirmedScope({ accountId: account.account_id, promotions: localPromotions, request });
          return {
            promotions: filtered.promotions,
            confirmed_scope_missing_activity_keys: filtered.missingActivityKeys,
            summary: {
              stages: ['使用最终确认并已实时复核的活动范围，不再扩大或重新全量读取。'],
              fetched: false,
              matched_before_fetch: localPromotions.length,
              matched_after_fetch: filtered.promotions.length,
              total_after_fetch: localPromotions.length,
            },
          };
        })()
      : await preparePromotionsForExecution({ account, filters, settings, request });
    for (const line of promotionPrep.summary.stages || []) appendExecutionJobLog(job, line);
    appendSiteActivityUserLogs(job, account, filters, storeName);
    const promotions = promotionPrep.promotions;
    job.progress.total_promotions = promotions.length;
    appendExecutionUserLog(job, `匹配到活动 ${promotions.length} 个，并发读取活动商品：活动并发=${jobActivityConcurrency}，读取并发=${jobReadConcurrency}。`);
    appendExecutionJobLog(job, `匹配活动 ${promotions.length} 个：${summarizePromotionScope(promotions)}。`);
    promotions.slice(0, 30).forEach((promotion, index) => {
      appendExecutionJobLog(job, `活动 ${index + 1}/${promotions.length}：${promotion.site_id || '-'} ${promotion.promotion_id} ${promotion.promotion_type} ${promotion.name || ''}`.trim());
    });
    if (promotions.length > 30) appendExecutionJobLog(job, `活动列表较多，仅展示前 30 个，其余 ${promotions.length - 30} 个继续处理。`);
    if (!promotions.length) {
      throw new Error('未找到匹配活动。请检查店铺、站点、自建/官方活动筛选。');
    }
    if ((promotionPrep.confirmed_scope_missing_activity_keys || []).length) {
      throw new ApiError('最终确认活动已不在本地验证范围中，本次未执行商品。请重新准备。', 409);
    }

    let action = request.action || '';
    let decision = null;
    if (!action) {
      decision = buildTodayDecision(account.account_id, promotions);
      action = decision.action;
      job.result = { today_decision: decision };
      appendExecutionUserLog(job, `今日判断：${actionDisplayName(action)}。${decision.reason || ''}`);
      appendExecutionJobLog(job, `今日判断：${action || '无动作'}。${decision.reason || ''}`);
      if (decision.already_completed && !request.prepareOnly) {
        throw new Error('今天已完整执行，默认不重复提交。');
      }
    }

    const itemStatus = requireItemStatus(request.itemStatus || actionDefaultStatus(action));
    job.progress.stage = 'items';
    appendExecutionUserLog(job, `开始读取${itemStatusDisplayName(itemStatus)}：活动 ${promotions.length} 个，读取并发=${jobReadConcurrency}。`);
    appendExecutionJobLog(job, `读取 ${itemStatus} 商品，活动 ${promotions.length} 个。`);
    const itemPrep = frozenScope
      ? {
          summary: {
            action,
            itemStatus,
            fetchMode: 'confirmed_scope',
            readConcurrency: 0,
            promotions: promotions.length,
            platform_total: 0,
            saved_count: (request.confirmedExecutionScope?.activities || request.confirmed_execution_scope?.activities || [])
              .filter((activity) => String(activity.account_id ?? activity.accountId ?? '') === String(account.account_id))
              .reduce((sum, activity) => sum + (activity.item_ids ?? activity.itemIds ?? []).length, 0),
            failed_promotions: 0,
            fallback_promotions: 0,
            rows: [],
            stages: ['使用最终确认商品关系，不再进行同级全量读取。'],
          },
          rows: [],
        }
      : await prepareItemsForExecution({
          account,
          promotions,
          action,
          itemStatus,
          settings,
          request,
          shouldCancel: () => job.cancel_requested,
          onProgress: (event) => {
            if (event.total) job.progress.total_promotions = event.total;
            if (event.type === 'item_fetch_start') {
              job.progress.stage = 'items';
              appendExecutionUserLog(job, `${businessScope({ storeName, promotion: event.promotion })}：读取${itemStatusDisplayName(itemStatus)}。`);
              appendExecutionJobLog(job, `读取商品 ${event.index + 1}/${event.total}：${event.promotion_id} ${event.promotion_type}`);
            }
            if (event.type === 'item_fetch_done') {
              job.progress.completed_promotions += 1;
              job.progress.platform_total += Number(event.platform_total || 0);
              job.progress.saved_count += Number(event.saved_count || 0);
              if (event.error || event.blocked) job.progress.failed_promotions += 1;
              appendExecutionUserLog(job, `${businessScope({ storeName, promotion: event.promotion })}：${readCompletenessText(event)}。`);
              appendExecutionJobLog(job, `商品读取完成 ${event.index + 1}/${event.total}：${event.promotion_id}，${formatFetchProgress(event)}`);
            }
            if (event.type === 'inventory_fallback_start') {
              appendExecutionUserLog(job, `${businessScope({ storeName, promotion: event.promotion })}：候选明细不完整，尝试从库存商品补充可读候选。`);
              appendExecutionJobLog(job, `自建活动候选异常，启用库存兜底：${event.promotion_id}`);
            }
            if (event.type === 'inventory_fallback_done') {
              appendExecutionUserLog(job, `${businessScope({ storeName, promotion: event.promotion })}：库存补充完成，可读商品 ${event.saved_count ?? 0} 个。`);
              appendExecutionJobLog(job, `库存兜底完成：${event.promotion_id}，可读 ${event.saved_count ?? 0}。`);
            }
          }
        });
    for (const line of itemPrep.summary.stages || []) appendExecutionJobLog(job, line);

    if (job.cancel_requested) {
      job.status = 'cancelled';
      job.finished_at = new Date().toISOString();
      appendExecutionJobLog(job, '执行任务已在写入前停止。');
      persistExecutionJob(job);
      return;
    }

    const allowInventoryFallback = request.allowInventoryFallback !== false;
    let itemsByPromotion = listItemsForPromotions(account.account_id, promotions, itemStatus);
    const confirmedFilter = filterItemsByConfirmedScope({ accountId: account.account_id, promotions, itemsByPromotion, request });
    if (confirmedFilter.hasFilter) {
      if (confirmedFilter.missingRelations.length) {
        throw new ApiError('最终确认商品已不在本地验证范围中，本次未执行商品。请重新准备。', 409);
      }
      itemsByPromotion = confirmedFilter.itemsByPromotion;
      job.progress.confirmed_relation_count = confirmedFilter.matchedRelationCount;
      appendExecutionJobLog(job, `最终确认范围：商品关系 ${confirmedFilter.matchedRelationCount} 条；新增活动和新增候选未纳入。`);
    }
    const itemFilter = filterItemsByRequestedIds({ promotions, itemsByPromotion, request });
    if (itemFilter.hasFilter) {
      job.progress.target_item_ids = itemFilter.requestedItemIds;
      job.progress.itemIds_filtered_count = itemFilter.matchedItemIds.length;
      appendExecutionJobLog(job, `指定商品过滤：请求 ${itemFilter.requestedItemIds.length} 个，匹配 ${itemFilter.matchedItemIds.length} 个。`);
      appendExecutionUserLog(job, `指定商品过滤：本次只处理 ${itemFilter.matchedItemIds.length} 个指定商品。`);
      if (itemFilter.missingItemIds.length) {
        const message = requestedItemFilterErrorMessage(itemFilter, itemStatus);
        appendExecutionJobLog(job, message);
        throw new ApiError(message, 409);
      }
      validateRequestedSmartCancelItems({ promotions, itemsByPromotion: itemFilter.itemsByPromotion, action });
      itemsByPromotion = itemFilter.itemsByPromotion;
    }
    const fetchStatesByPromotion = planningFetchStates(account.account_id, promotions, itemStatus, allowInventoryFallback);
    const sampleOnly = action === 'enroll' ? Boolean(request.sampleOnly) : Boolean(request.sampleOnly);
    const batch = buildBatchPlans({
      action,
      promotions,
      itemsByPromotion,
      fetchStatesByPromotion,
      priceMode: request.priceMode || 'discount',
      sellerDiscountPercent: Number(request.sellerDiscountPercent ?? settings.sellerDefaultDiscount),
      officialDiscountPercent: Number(request.officialDiscountPercent ?? settings.officialDefaultDiscount),
      directPrice: request.directPrice === null || request.directPrice === undefined || request.directPrice === '' ? null : Number(request.directPrice),
      requireFullFetch: Boolean(request.requireFullFetch),
      sampleOnly,
      allowInventoryFallback
    });
    if (request.resumePendingOnly) {
      const pendingKeys = new Set(pendingWriteQueue.pending(job.id).map((record) => String(record.relation_key || '')));
      for (const entry of batch.plans) {
        const promotion = entry.promotion;
        entry.plan.rows = (entry.plan.rows || []).filter((row) => pendingKeys.has(pendingRelationKey({
          accountId: account.account_id,
          siteId: promotion.site_id,
          promotionId: promotion.promotion_id,
          promotionType: promotion.promotion_type,
          itemId: row.item?.item_id,
          action,
        })));
        entry.plan.total = entry.plan.rows.length;
        entry.plan.planned = entry.plan.rows.filter((row) => row.status === 'planned').length;
        entry.plan.skipped = entry.plan.rows.filter((row) => row.status !== 'planned').length;
      }
      batch.plans = batch.plans.filter((entry) => entry.plan.total > 0);
      batch.totals = {
        ...batch.totals,
        promotions: batch.plans.length,
        total: batch.plans.reduce((sum, entry) => sum + Number(entry.plan.total || 0), 0),
        planned: batch.plans.reduce((sum, entry) => sum + Number(entry.plan.planned || 0), 0),
        skipped: batch.plans.reduce((sum, entry) => sum + Number(entry.plan.skipped || 0), 0),
      };
      appendExecutionUserLog(job, `恢复末尾留置队列：仅处理 ${batch.totals.planned} 条仍待确认的活动商品关系。`);
    }
    appendExecutionJobLog(job, `计划阶段：活动 ${batch.totals.promotions} 个，商品 ${batch.totals.total}，可执行 ${batch.totals.planned}，跳过 ${batch.totals.skipped}，阻断活动 ${batch.totals.blocked}。`);
    for (const [index, entry] of batch.plans.entries()) {
      appendExecutionJobLog(job, formatPlanProgress(entry, index, batch.plans.length));
      appendExecutionUserLog(job, formatPlanUserProgress(entry, action, storeName));
      for (const reason of topPlanReasons(entry, 3)) appendExecutionJobLog(job, `  原因：${reason.reason} x${reason.count}`);
      const reasons = topPlanReasons(entry, 2);
      if (reasons.length) appendExecutionUserLog(job, `${businessScope({ storeName, promotion: entry.promotion })}：主要原因：${reasons.map((reason) => `${businessReasonText(reason.reason)} ${reason.count}`).join('，')}。`);
    }

    if (request.prepareOnly) {
      job.status = 'completed';
      job.progress.stage = 'completed';
      job.finished_at = new Date().toISOString();
      job.result = {
        ok: true,
        prepareOnly: true,
        action,
        itemStatus,
        prepare: { promotions: promotionPrep.summary, items: itemPrep.summary },
        batchSummary: {
          promotions_total: batch.plans.length,
          planned: batch.plans.reduce((sum, entry) => sum + Number(entry.plan?.planned || 0), 0),
          skipped: batch.plans.reduce((sum, entry) => sum + Number(entry.plan?.skipped || 0), 0),
          blocked: batch.plans.filter((entry) => entry.blocked).length,
          target_item_ids: itemFilter.hasFilter ? itemFilter.requestedItemIds : [],
          itemIds_filtered_count: itemFilter.hasFilter ? itemFilter.matchedItemIds.length : null
        },
        today_decision: decision
      };
      appendExecutionUserLog(job, `只读准备完成：活动 ${batch.plans.length} 个，商品 ${batch.totals.total}，可执行 ${batch.totals.planned}，跳过 ${batch.totals.skipped}。`);
      appendExecutionJobLog(job, '只读准备完成，未执行 Mercado 写接口。');
      persistExecutionJob(job);
      return;
    }

    job.progress.stage = 'execute';
    appendExecutionUserLog(job, `展开任务：店铺 1 个，店铺站点 ${countBusinessSitesForPromotions(promotions)} 个，活动任务 ${batch.plans.length} 个。`);
    appendExecutionUserLog(job, `并发处理活动任务：活动并发=${jobActivityConcurrency}，商品写入并发=${jobWriteConcurrency}，全局写入上限=${jobGlobalWriteConcurrency}。`);
    appendExecutionUserLog(job, `开始提交${actionVerb(action)}：活动 ${batch.plans.length} 个。`);
    appendExecutionJobLog(job, `开始真实执行：${action}，活动 ${batch.plans.length} 个。`);
    let execution = await executeBatchPlans({
      account,
      action,
      itemStatus,
      batch,
      request: { ...request, executionJobId: job.id, action, allowInventoryFallback, requireFullFetch: Boolean(request.requireFullFetch), sampleOnly, prepare: { promotions: promotionPrep.summary, items: itemPrep.summary } },
      writeConcurrency: jobWriteConcurrency,
      globalWriteConcurrency: jobGlobalWriteConcurrency,
      activityConcurrency: jobActivityConcurrency,
      shouldCancel: () => job.cancel_requested,
      onProgress: (event) => {
        if (event.type === 'write_peak') {
          job.progress.global_active_writes = event.active || 0;
          job.progress.global_peak_in_flight = Math.max(job.progress.global_peak_in_flight || 0, event.maxActive || 0);
          job.progress.write_queue_depth = Number(event.queued || 0);
          job.progress.write_adaptive_limit = Number(event.limit || 0);
          job.progress.write_cooldown_until = event.cooldown_until || null;
          job.progress.write_route_active = event.route_active || {};
        }
        if (event.type === 'execution_stop_requested') {
          job.cancel_requested = true;
          job.progress.execution_stop_reason = event.reason || '接口类失败触发停止。';
          appendExecutionUserLog(job, `已触发安全停止：${event.reason || '接口类失败'}，未开始的商品会尽快跳过。`);
          appendExecutionJobEvent(job, { type: 'execution_stop_requested', reason: event.reason || '', details: event.details || null });
        }
        if (event.type === 'execute_info' && event.message) {
          appendExecutionUserLog(job, event.message);
          appendExecutionJobLog(job, event.message);
        }
        if (event.type === 'execute_start') {
          appendExecutionUserLog(job, `${businessScope({ storeName, promotion: event.promotion })}：商品 ${event.total_items || 0} 个，按活动并发 ${event.activityConcurrency || jobActivityConcurrency}、商品写入并发 ${event.writeConcurrency || jobWriteConcurrency} 提交${actionVerb(action)}，全局写入上限=${event.globalWriteConcurrency || jobGlobalWriteConcurrency}。`);
          appendExecutionJobLog(job, `执行活动 ${event.index + 1}/${event.total}：${event.promotion_id} ${event.promotion_type}`);
        }
        if (event.type === 'execute_done') {
          job.progress.execute_completed_promotions += 1;
          const displayTotal = displayProgressTotal(event);
          appendExecutionUserLog(job, `${businessScope({ storeName, promotion: event.promotion })}：提交完成，处理 ${displayTotal} 个，成功 ${event.success}，失败 ${event.failed}，跳过 ${event.skipped}。`);
          appendExecutionJobLog(job, `活动执行完成 ${event.index + 1}/${event.total}：${event.promotion_id}，处理 ${displayTotal}，成功 ${event.success}，失败 ${event.failed}，跳过 ${event.skipped}`);
        }
      }
    });
    if (request.resumePendingOnly && job.result?.execution) {
      execution = mergeExecutionRecoveryRound(job.result.execution, execution);
    }
    if (itemFilter.hasFilter) {
      execution.target_item_ids = itemFilter.requestedItemIds;
      execution.itemIds_filtered_count = itemFilter.matchedItemIds.length;
    }
    for (const promotionResult of execution.promotions || []) {
      if (Number(promotionResult.success || 0) <= 0 && Number(promotionResult.request_success_count || 0) <= 0) continue;
      markActivityCacheDirty({
        accountId: account.account_id,
        siteId: promotionResult.site_id || '',
        promotionId: promotionResult.promotion_id,
        promotionType: promotionResult.promotion_type,
      });
    }
    const batchTaskId = saveBatchExecutionSummaryTask({
      account,
      action,
      execution,
      completed: !execution.cancelled
        && Number(execution.failed || 0) === 0
        && Number(execution.pending || 0) === 0
        && Number(execution.activity_failure_count || 0) === 0,
      request,
      existingTaskId: job.batch_task_id,
    });
    job.batch_task_id = Number(batchTaskId);
    job.status = execution.cancelled ? 'cancelled' : Number(execution.pending || 0) > 0 ? 'paused' : 'completed';
    job.request.resumePendingOnly = job.status === 'paused';
    job.progress.stage = job.status;
    job.progress.pending_relations = Number(execution.pending || 0);
    job.progress.write_queue = globalWriteLimiter.snapshot?.() || null;
    job.finished_at = new Date().toISOString();
    job.result = {
      ok: !execution.cancelled,
      message: execution.cancelled ? '执行任务已停止。' : '真实执行已完成。',
      today_decision: decision,
      action,
      itemStatus,
      batchTaskId,
      prepare: { promotions: promotionPrep.summary, items: itemPrep.summary },
      execution
    };
    const executionDisplayTotal = displayExecutionTotal(execution);
    appendExecutionJobLog(job, job.status === 'paused'
      ? `临时失败已进入持久留置队列：待恢复 ${execution.pending || 0} 条关系；已成功和业务失败项不会重复提交。`
      : execution.cancelled
      ? '执行任务已停止。'
      : `结束：总商品 ${executionDisplayTotal}，成功 ${execution.success}，失败 ${execution.failed}，跳过 ${execution.skipped}，阻断活动 ${execution.blocked}，用时 ${Math.round((Date.now() - jobStartedMs) / 1000)} 秒。`);
    appendExecutionUserLog(job, job.status === 'paused'
      ? `平台限流或临时网络失败，${execution.pending || 0} 条关系已安全留置并等待恢复；其它商品结果已保存。`
      : execution.cancelled
      ? `执行任务已按规则停止：活动 ${execution.promotions_total} 个，商品 ${executionDisplayTotal}，成功 ${execution.success}，失败 ${execution.failed}，跳过 ${execution.skipped}，已保存结果。`
      : `${actionDisplayName(action)}完成：活动 ${execution.promotions_total} 个，商品 ${executionDisplayTotal}，成功 ${execution.success}，失败 ${execution.failed}，跳过 ${execution.skipped}，用时 ${formatDuration(Date.now() - jobStartedMs)}。`);
    persistExecutionJob(job);
  } catch (error) {
    failExecutionJob(job, error);
  }
}

function buildTodayDecision(accountId, promotions) {
  return buildTodayDecisionForScope([accountId], promotions);
}

function buildTodayDecisionForScope(accountIds, promotions) {
  const cycleStatesByPromotion = new Map();
  const startedCountsByPromotion = new Map();
  for (const accountId of accountIds) {
    const scoped = promotions.filter((promotion) => String(promotion.account_id || '') === String(accountId));
    for (const [key, value] of listCycleStatesForPromotions(accountId, scoped)) cycleStatesByPromotion.set(key, value);
    for (const [key, value] of listItemCountsForPromotions(accountId, scoped, 'started')) startedCountsByPromotion.set(key, value);
  }
  const globalCycle = buildGlobalTodayDiscount({
    tasks: listGlobalDiscountUpdateSummaries(300),
    settings: readSettings(),
  });
  return decideToday({ promotions, cycleStatesByPromotion, startedCountsByPromotion, globalCycle });
}

async function executeBatchPlans({ account, action, itemStatus, batch, request, writeConcurrency, globalWriteConcurrency, activityConcurrency, onProgress, shouldCancel }) {
  const requestedNormalizedWriteConcurrency = normalizeWriteConcurrency(writeConcurrency, readSettings().writeConcurrency);
  const normalizedWriteConcurrency = Math.min(requestedNormalizedWriteConcurrency, ADAPTIVE_WRITE_PROFILE.perRoute);
  const normalizedActivityConcurrency = normalizeConcurrency(activityConcurrency ?? request?.activityConcurrency ?? readSettings().previewConcurrency, readSettings().previewConcurrency);
  const normalizedGlobalWriteConcurrency = ADAPTIVE_WRITE_PROFILE.maxGlobal;
  let stopReason = null;
  const globalWriteLimiter = request?.executionGroupId
    ? getSharedWriteLimiter(request.executionGroupId, normalizedGlobalWriteConcurrency, ({ active, maxActive, limit, queued, cooldown_until, route_active }) => {
      summary.globalMaxActive = Math.max(summary.globalMaxActive || 0, maxActive || 0);
      onProgress?.({ type: 'write_peak', active, maxActive, limit, queued, cooldown_until, route_active });
    })
    : createAdaptiveWriteScheduler({
      onStateChange: ({ active, maxActive, limit, queued, cooldown_until, route_active }) => {
        summary.globalMaxActive = Math.max(summary.globalMaxActive || 0, maxActive || 0);
        onProgress?.({ type: 'write_peak', active, maxActive, limit, queued, cooldown_until, route_active });
      }
    });
  const summary = {
    promotions_total: batch.plans.length,
    total: 0,
    success: 0,
    failed: 0,
    skipped: 0,
    pending: 0,
    blocked: 0,
    result_contract_version: RESULT_CONTRACT_VERSION,
    relation_count: 0,
    unique_item_count: 0,
    activity_failure_count: 0,
    request_success_count: 0,
    live_verified_removed_count: 0,
    pending_verification_count: 0,
    writeConcurrency: normalizedWriteConcurrency,
    activityConcurrency: normalizedActivityConcurrency,
    globalWriteConcurrency: normalizedGlobalWriteConcurrency,
    globalMaxActive: 0,
    requestedWriteConcurrency: request?.requestedWriteConcurrency ?? writeConcurrency,
    requestedGlobalWriteConcurrency: request?.requestedGlobalWriteConcurrency ?? globalWriteConcurrency ?? request?.globalWriteConcurrency,
    promotions: []
  };
  const shouldStop = () => Boolean(stopReason) || Boolean(shouldCancel?.());
  const summaryUniqueItems = new Set();
  const requestStop = (reason, details = null) => {
    if (stopReason) return;
    stopReason = reason || '接口类失败触发停止。';
    summary.cancelled = true;
    summary.stop_reason = stopReason;
    onProgress?.({ type: 'execution_stop_requested', reason: stopReason, details });
  };
  const indexedPlans = batch.plans.map((entry, index) => ({ entry, index }));
  await mapLimited(indexedPlans, normalizedActivityConcurrency, async ({ entry, index }) => {
    const { promotion, blocked, warning, detail_status: detailStatus } = entry;
    let plan = entry.plan;
    if (shouldStop()) {
      summary.cancelled = true;
      return;
    }
    onProgress?.({
      type: 'execute_start',
      index,
      total: batch.plans.length,
      promotion_id: promotion.promotion_id,
      promotion_type: promotion.promotion_type,
      promotion,
      total_items: plan.total || 0,
      activityConcurrency: normalizedActivityConcurrency,
      writeConcurrency: normalizedWriteConcurrency,
      globalWriteConcurrency: normalizedGlobalWriteConcurrency
    });
    if (blocked) {
      const reason = warning || detailStatus || '该活动不满足真实执行条件';
      const taskId = createTask({
        accountId: account.account_id,
        promotionId: promotion.promotion_id,
        promotionType: promotion.promotion_type,
        action,
        mode: 'real',
        discountPercent: plan.discountPercent,
        directPrice: plan.directPrice,
        plan,
        ...executionTaskIdentity(request)
      });
      saveExecutionResult({
        taskId,
        accountId: account.account_id,
        promotionId: promotion.promotion_id,
        promotionType: promotion.promotion_type,
        itemId: '',
        action,
        mode: 'real',
        status: 'activity_failed',
        errorCn: reason
      });
      finishTask(taskId, { success: 0, failed: 0, skipped: 0, blocked: 1, activity_failure_count: 1, result_contract_version: RESULT_CONTRACT_VERSION }, 'blocked', false, { publishHistory: false });
      summary.blocked += 1;
      summary.activity_failure_count += 1;
      summary.promotions.push({
        site_id: promotion.site_id,
        promotion_id: promotion.promotion_id,
        promotion_type: promotion.promotion_type,
        status: 'blocked',
        reason,
        taskId,
        total: 0,
        success: 0,
        failed: 0,
        skipped: 0
      });
      onProgress?.({
        type: 'execute_done',
        index,
        total: batch.plans.length,
        promotion_id: promotion.promotion_id,
        promotion_type: promotion.promotion_type,
        promotion,
        total_items: 0,
        success: 0,
        failed: 0,
        skipped: 0,
        activityConcurrency: normalizedActivityConcurrency,
        writeConcurrency: normalizedWriteConcurrency
      });
      return;
    }
    const resumedPendingRecords = request?.resumePendingOnly
      ? pendingWriteQueue.pending(request?.executionJobId, (record) => (
          String(record.account_id || '') === String(account.account_id)
          && String(record.site_id || '').toUpperCase() === String(promotion.site_id || '').toUpperCase()
          && String(record.promotion_id || '') === String(promotion.promotion_id || '')
          && String(record.promotion_type || '').toUpperCase() === String(promotion.promotion_type || '').toUpperCase()
          && String(record.action || '') === String(action || '')
        ))
      : [];
    const resumedTaskId = Number(resumedPendingRecords[0]?.task_id || 0);
    const taskId = resumedTaskId || createTask({
        accountId: account.account_id,
        promotionId: promotion.promotion_id,
        promotionType: promotion.promotion_type,
        action,
        mode: 'real',
        discountPercent: plan.discountPercent,
        directPrice: plan.directPrice,
        plan,
        ...executionTaskIdentity(request)
      });
    if (plan.total === 0) {
      const reason = `${itemStatus} 商品未读取到或当前筛选下无可处理商品。`;
      saveExecutionResult({
        taskId,
        accountId: account.account_id,
        promotionId: promotion.promotion_id,
        promotionType: promotion.promotion_type,
        itemId: '',
        action,
        mode: 'real',
        status: 'activity_failed',
        errorCn: reason
      });
      const counts = { success: 0, failed: 0, skipped: 0, activity_failure_count: 1, result_contract_version: RESULT_CONTRACT_VERSION };
      finishTask(taskId, counts, 'empty_or_failed', false, { publishHistory: false });
      summary.activity_failure_count += 1;
      summary.promotions.push({
        site_id: promotion.site_id,
        child_user_id: promotion.child_user_id,
        promotion_id: promotion.promotion_id,
        promotion_type: promotion.promotion_type,
        taskId,
        total: 0,
        success: 0,
        failed: 0,
        skipped: 0,
        completed: false,
        reason
      });
      onProgress?.({
        type: 'execute_done',
        index,
        total: batch.plans.length,
        promotion_id: promotion.promotion_id,
        promotion_type: promotion.promotion_type,
        promotion,
        total_items: 0,
        success: 0,
        failed: 0,
        skipped: 0,
        activityConcurrency: normalizedActivityConcurrency,
        writeConcurrency: normalizedWriteConcurrency
      });
      return;
    }
    if (action === 'cancel' && String(promotion.promotion_type || '').toUpperCase() === 'SMART' && !request?.allowSmartCancel) {
      const reason = 'SMART取消需要 started offer_id；当前未启用 SMART 取消单品/小样本验证开关，未发送接口';
      for (const row of plan.rows || []) {
        if (row.status !== 'planned') continue;
        appendExecutionItemAuditEvent(request?.executionJobId, {
          type: 'item_skipped',
          account,
          promotion,
          taskId,
          row,
          action,
          status: 'skipped',
          reason,
          sentToApi: false
        });
        saveExecutionResult({
          taskId,
          accountId: account.account_id,
          promotionId: promotion.promotion_id,
          promotionType: promotion.promotion_type,
          itemId: row.item?.item_id || '',
          action,
          mode: 'real',
          status: 'skipped',
          dealPrice: row.deal_price,
          errorCn: reason,
          errorRaw: JSON.stringify({ policyBlocked: true, sentToApi: false, reason })
        });
      }
      const skipped = (plan.rows || []).filter((row) => row.status === 'planned').length;
      const counts = { success: 0, failed: 0, skipped };
      finishTask(taskId, counts, 'blocked', false, { publishHistory: false });
      summary.total += plan.total;
      summary.skipped += skipped;
      summary.promotions.push({
        site_id: promotion.site_id,
        child_user_id: promotion.child_user_id,
        promotion_id: promotion.promotion_id,
        promotion_type: promotion.promotion_type,
        taskId,
        status: 'skipped',
        total: plan.total,
        success: 0,
        failed: 0,
        skipped,
        completed: false,
        reason,
        sent_to_api: false
      });
      onProgress?.({
        type: 'execute_done',
        index,
        total: batch.plans.length,
        promotion_id: promotion.promotion_id,
        promotion_type: promotion.promotion_type,
        promotion,
        total_items: plan.total,
        success: 0,
        failed: 0,
        skipped,
        activityConcurrency: normalizedActivityConcurrency,
        globalWriteConcurrency: normalizedGlobalWriteConcurrency,
        globalMaxActive: globalWriteLimiter.maxActive,
        writeConcurrency: normalizedWriteConcurrency
      });
      return;
    }
    if (action === 'cancel' && String(promotion.promotion_type || '').toUpperCase() === 'SMART') {
      const limit = normalizeSmartCancelSampleLimit(request.smartCancelMaxItems ?? request.smartCancelLimit ?? SMART_CANCEL_POLICY.default_sample_limit);
      plan = limitSmartCancelPlan(plan, limit);
      onProgress?.({
        type: 'execute_info',
        index,
        total: batch.plans.length,
        promotion_id: promotion.promotion_id,
        promotion_type: promotion.promotion_type,
        promotion,
        message: `SMART取消验证已启用：本活动最多发送 ${limit} 个商品，其余仅记录为未发送。`
      });
    }
    let client = makeWriteClient(account, promotion);
    const cancelOutcomes = [];
    const persistExecutionOutcome = (result) => {
      const persisted = action === 'cancel' && result?.status === 'success'
        ? { ...result, status: CANCEL_RESULT_STATUS.requestSuccess }
        : result;
      if (action === 'cancel' && String(result?.itemId || '').trim()) {
        cancelOutcomes.push({ item_id: String(result.itemId), status: persisted.status });
      }
      return saveExecutionResult(persisted);
    };
    const execution = await executePlannedRowsWithConcurrency({
      plan,
      action,
      promotionId: promotion.promotion_id,
      promotionType: promotion.promotion_type,
      accountId: account.account_id,
      taskId,
      writeConcurrency: normalizedWriteConcurrency,
      schedule: (runWrite) => globalWriteLimiter.run(runWrite, {
        accountId: account.account_id,
        siteId: promotion.site_id,
      }),
      shouldCancel: shouldStop,
      executeOne: ({ row, itemId, dealPrice }) => executeOnePlannedWithTokenRefresh({
        client,
        setClient: (nextClient) => { client = nextClient; },
        accountId: account.account_id,
        action,
        campaign: promotion,
        row,
        input: {
          itemId,
          promotionId: promotion.promotion_id,
          promotionType: promotion.promotion_type,
          dealPrice
        }
      }),
      saveResult: persistExecutionOutcome,
      toErrorText: toChineseError,
      classifyError: classifyExecutionWriteError,
      onStopRequested: ({ errorCn, classifiedError }) => {
        if (classifiedError?.authFailure) requestStop('账号授权失败，已停止该账号写入。', { errorCn });
      },
      onPending: ({ row, errorCn, classifiedError, attempt }) => {
        const relationKey = pendingRelationKey({
          accountId: account.account_id,
          siteId: promotion.site_id,
          promotionId: promotion.promotion_id,
          promotionType: promotion.promotion_type,
          itemId: row.item?.item_id,
          action,
        });
        pendingWriteQueue.enqueue(request?.executionJobId, {
          relation_key: relationKey,
          account_id: String(account.account_id),
          child_user_id: String(promotion.child_user_id || ''),
          site_id: String(promotion.site_id || '').toUpperCase(),
          promotion_id: String(promotion.promotion_id || ''),
          promotion_type: String(promotion.promotion_type || '').toUpperCase(),
          item_id: String(row.item?.item_id || ''),
          action,
          task_id: Number(taskId),
          row,
          attempt_count: attempt,
          retry_category: classifiedError?.rateLimited ? 'rate_limited' : 'transient_interface_failure',
          error_cn: errorCn,
        });
      },
      onItemEvent: (event) => {
        const progressJob = executionJobs.get(String(request?.executionJobId || ''));
        if (event.type === 'item_retry') {
          if (progressJob) appendExecutionJobLog(progressJob, `${event.reason || '临时接口失败，正在重试'}：${promotion.promotion_id} ${event.row?.item?.item_id || ''}`.trim());
        }
        if (event.type === 'item_deferred') {
          if (progressJob) appendExecutionJobLog(progressJob, `${event.reason || '临时接口失败，已留到本批末尾补跑'}：${promotion.promotion_id} ${event.row?.item?.item_id || ''}`.trim());
        }
        if (event.type === 'deferred_retry_start') {
          onProgress?.({
            type: 'execute_info',
            index,
            total: batch.plans.length,
            promotion_id: promotion.promotion_id,
            promotion_type: promotion.promotion_type,
            promotion,
            message: `${businessScope({ storeName: request?.storeName, promotion })}：临时失败 ${event.count || 0} 个，留到本活动末尾补跑。`
          });
        }
        if (event.type === 'deferred_retry_done') {
          onProgress?.({
            type: 'execute_info',
            index,
            total: batch.plans.length,
            promotion_id: promotion.promotion_id,
            promotion_type: promotion.promotion_type,
            promotion,
            message: `${businessScope({ storeName: request?.storeName, promotion })}：末尾补跑完成，成功 ${event.success || 0}，最终失败 ${event.failed || 0}，跳过 ${event.skipped || 0}，待后续恢复 ${event.pending || 0}。`
          });
        }
        if (['item_finish', 'item_skipped', 'item_cancelled_before_start'].includes(event.type)
            && ['success', 'failed', 'skipped'].includes(String(event.status || ''))) {
          const relationKey = pendingRelationKey({
            accountId: account.account_id,
            siteId: promotion.site_id,
            promotionId: promotion.promotion_id,
            promotionType: promotion.promotion_type,
            itemId: event.row?.item?.item_id,
            action,
          });
          pendingWriteQueue.resolve(request?.executionJobId, relationKey, event.status);
        }
        appendExecutionItemAuditEvent(request?.executionJobId, {
          ...event,
          account,
          promotion,
          taskId,
          action
        });
      },
      retryOptions: {
        maxImmediateRetries: 3,
        retryBackoffMs: [1000, 2000, 4000],
        deferredFinalRetry: true,
        deferredConcurrency: Math.min(normalizedWriteConcurrency, ADAPTIVE_WRITE_PROFILE.perRoute)
      }
    });
    const counts = execution.counts;
    const unreadable = unreadableCandidateCount({
      action,
      itemStatus,
      fetchInfo: entry.fetch_info,
      fetchState: entry.fetchState,
      fallbackSavedCount: plan.total
    });
    if (unreadable > 0) {
      counts.activity_failure_count = Number(counts.activity_failure_count || 0) + 1;
      saveExecutionResult({
        taskId,
        accountId: account.account_id,
        promotionId: promotion.promotion_id,
        promotionType: promotion.promotion_type,
        itemId: '',
        action,
        mode: 'real',
        status: 'activity_failed',
        errorCn: unreadableItemDetailMessage({ action, itemStatus, count: unreadable })
      });
    }
    let recheck = null;
    if (action === 'cancel') {
      recheck = await recheckAndCancelRemainingStarted({
        client,
        accountId: account.account_id,
        campaign: promotion,
        promotionId: promotion.promotion_id,
        promotionType: promotion.promotion_type,
        action,
        taskId,
        writeConcurrency: normalizedWriteConcurrency,
        maxRounds: readSettings().cancelMaxRounds,
        counts,
        saveResult: persistExecutionOutcome,
        executionJobId: request?.executionJobId,
        shouldCancel: shouldStop
      });
    }
    let resultContract = null;
    const activityFailureCount = Number(counts.activity_failure_count || 0);
    if (action === 'cancel') {
      resultContract = buildCancelResultContract({
        plannedItemIds: (plan.rows || []).map((row) => row.item?.item_id).filter(Boolean),
        outcomes: cancelOutcomes,
        recheck: {
          completed: recheck?.completed === true,
          remainingItemIds: recheck?.remainingItemIds,
        },
      });
      const rowByItemId = new Map((plan.rows || []).map((row) => [String(row.item?.item_id || ''), row]));
      for (const [itemId, status] of Object.entries(resultContract.final_status_by_item)) {
        const row = rowByItemId.get(itemId);
        saveExecutionResult({
          taskId,
          accountId: account.account_id,
          promotionId: promotion.promotion_id,
          promotionType: promotion.promotion_type,
          itemId,
          action,
          mode: 'real',
          status,
          dealPrice: row?.deal_price,
          errorCn: status === CANCEL_RESULT_STATUS.pendingVerification
            ? '取消请求已提交，尚未完成平台实时回查确认'
            : status === CANCEL_RESULT_STATUS.liveStillStarted
              ? '取消请求已提交，但实时回查仍为已报名状态'
              : null,
        });
        appendExecutionItemAuditEvent(request?.executionJobId, {
          type: 'item_cancel_final_state',
          account,
          promotion,
          taskId,
          action,
          row: row || { item: { item_id: itemId } },
          status,
          sentToApi: cancelOutcomes.some((outcome) => outcome.item_id === itemId && outcome.status === CANCEL_RESULT_STATUS.requestSuccess),
        });
      }
      Object.assign(counts, {
        success: resultContract.counts.success,
        failed: resultContract.counts.failed,
        skipped: resultContract.counts.skipped,
        ...stableContractCounts({
          relationCount: resultContract.counts.relation_count,
          uniqueItemCount: resultContract.counts.unique_item_count,
        }),
        ...resultContract.counts,
        activity_failure_count: activityFailureCount,
        result_contract_version: RESULT_CONTRACT_VERSION,
      });
    } else {
      Object.assign(counts, {
        relation_count: plan.total,
        unique_item_count: new Set((plan.rows || []).map((row) => row.item?.item_id).filter(Boolean)).size,
        activity_failure_count: activityFailureCount,
        request_success_count: 0,
        live_verified_removed_count: 0,
        pending_verification_count: 0,
        pending_count: Number(counts.pending || 0),
        result_contract_version: RESULT_CONTRACT_VERSION,
      });
    }
    const completed = counts.failed === 0
      && Number(counts.pending || 0) === 0
      && Number(counts.activity_failure_count || 0) === 0
      && plan.rows.length === counts.success + counts.skipped;
    finishTask(taskId, counts, completed ? 'completed' : 'partial_or_failed', completed, { publishHistory: false });
    markCycleAfterTask({
      accountId: account.account_id,
      promotionId: promotion.promotion_id,
      promotionType: promotion.promotion_type,
      action,
      discountPercent: plan.discountPercent,
      completed
    });
    summary.total += plan.total;
    summary.relation_count += Number(counts.relation_count || plan.total || 0);
    for (const row of plan.rows || []) {
      const itemId = String(row.item?.item_id || '').trim();
      if (itemId) summaryUniqueItems.add(`${account.account_id}|${itemId}`);
    }
    summary.unique_item_count = summaryUniqueItems.size;
    summary.request_success_count += Number(counts.request_success_count || 0);
    summary.live_verified_removed_count += Number(counts.live_verified_removed_count || 0);
    summary.pending_verification_count += Number(counts.pending_verification_count || 0);
    summary.activity_failure_count += Number(counts.activity_failure_count || 0);
    summary.success += counts.success;
    summary.failed += counts.failed;
    summary.skipped += counts.skipped;
    summary.pending += Number(counts.pending || 0);
    summary.promotions.push({
      site_id: promotion.site_id,
      child_user_id: promotion.child_user_id,
      promotion_id: promotion.promotion_id,
      promotion_type: promotion.promotion_type,
      taskId,
      total: plan.total,
      success: counts.success,
      failed: counts.failed,
      skipped: counts.skipped,
      pending: Number(counts.pending || 0),
      unreadable_candidates: unreadable,
      recheck,
      result_contract_version: RESULT_CONTRACT_VERSION,
      relation_count: Number(counts.relation_count || plan.total || 0),
      unique_item_count: Number(counts.unique_item_count || 0),
      activity_failure_count: Number(counts.activity_failure_count || 0),
      request_success_count: Number(counts.request_success_count || 0),
      live_verified_removed_count: Number(counts.live_verified_removed_count || 0),
      pending_verification_count: Number(counts.pending_verification_count || 0),
      completed,
      writeConcurrency: execution.writeConcurrency,
      maxActive: execution.maxActive,
      globalMaxActive: globalWriteLimiter.maxActive,
      activityConcurrency: normalizedActivityConcurrency
    });
    onProgress?.({
      type: 'execute_done',
      index,
      total: batch.plans.length,
      promotion_id: promotion.promotion_id,
      promotion_type: promotion.promotion_type,
      promotion,
      total_items: plan.total,
      success: counts.success,
      failed: counts.failed,
      skipped: counts.skipped,
      pending: Number(counts.pending || 0),
      activityConcurrency: normalizedActivityConcurrency,
      globalWriteConcurrency: normalizedGlobalWriteConcurrency,
      globalMaxActive: globalWriteLimiter.maxActive,
      writeConcurrency: execution.writeConcurrency
    });
  });
  summary.globalMaxActive = globalWriteLimiter.maxActive;
  if (stopReason) summary.stop_reason = stopReason;
  return summary;
}

function getSharedWriteLimiter(groupId, limit, onActiveChange) {
  const key = String(groupId || '');
  if (!key) return createAdaptiveWriteScheduler({ onStateChange: onActiveChange });
  const existing = sharedWriteLimiters.get(key);
  if (existing) {
    if (onActiveChange) existing.listeners.add(onActiveChange);
    return existing;
  }
  const listeners = new Set(onActiveChange ? [onActiveChange] : []);
  const limiter = createAdaptiveWriteScheduler({
    onStateChange: (state) => {
      for (const listener of listeners) listener(state);
      const group = executionGroups.get(key) || executionGroupPersistence.load(key);
      if (group) {
        const nextPeak = Math.max(Number(group.global_peak_in_flight || 0), Number(state.maxActive || 0));
        if (nextPeak > Number(group.global_peak_in_flight || 0)) {
          group.global_peak_in_flight = nextPeak;
          executionGroupPersistence.persist(group);
        }
        executionGroups.set(key, group);
      }
    },
  });
  limiter.listeners = listeners;
  sharedWriteLimiters.set(key, limiter);
  return limiter;
}

function unreadableCandidateCount({ action, itemStatus, fetchInfo, fetchState, fallbackSavedCount = 0 }) {
  if (action !== 'enroll' || itemStatus !== 'candidate') return 0;
  const platformTotal = numberOrNull(fetchInfo?.platform_total ?? fetchState?.platform_total);
  if (platformTotal === null) return 0;
  const savedCount = Math.max(0, Math.floor(numberOrNull(fetchInfo?.saved_count ?? fetchState?.saved_count) ?? fallbackSavedCount ?? 0));
  const missing = platformTotal - savedCount;
  if (missing <= 0) return 0;
  const isIncomplete = CANDIDATE_INCOMPLETE_STATUSES.has(fetchInfo?.detail_status || fetchState?.detail_status);
  const isPartial = Boolean(fetchInfo?.sample_only || fetchInfo?.partial_readable_subset || fetchState?.detail_status === 'partial_api_sparse_marketplace_candidate');
  return isIncomplete || isPartial ? missing : 0;
}

function unreadableItemDetailMessage({ action, itemStatus, count } = {}) {
  const normalizedAction = String(action || '').toLowerCase();
  const normalizedStatus = String(itemStatus || '').toLowerCase();
  const itemLabel = normalizedAction === 'cancel' || normalizedStatus === 'started' ? '已报名商品' : '候选';
  return `平台还有 ${count} 个${itemLabel}未返回明细，本次未执行。`;
}

function numberOrNull(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function saveBatchPlanTasks({ accountId, action, batch }) {
  const taskIds = [];
  for (const { promotion, plan } of batch.plans) {
    const taskId = createTask({
      accountId,
      promotionId: promotion.promotion_id,
      promotionType: promotion.promotion_type,
      action,
      mode: 'dry-run',
      discountPercent: plan.discountPercent,
      directPrice: plan.directPrice,
      plan
    });
    savePlanResults({ taskId, accountId, promotionId: promotion.promotion_id, promotionType: promotion.promotion_type, action, mode: 'dry-run', plan });
    taskIds.push(taskId);
  }
  return taskIds;
}

function planningFetchStates(accountId, promotions, itemStatus, allowInventoryFallback = false) {
  const states = listItemFetchStatesForPromotions(accountId, promotions, itemStatus);
  if (!allowInventoryFallback || itemStatus !== 'candidate') return states;
  for (const promotion of promotions) {
    if (!isSellerCampaign(promotion)) continue;
    const fallbackState = getItemFetchState(accountId, promotion.promotion_id, promotion.promotion_type, INVENTORY_FALLBACK_ITEM_STATUS);
    if (!fallbackState) continue;
    const key = promotionKey(promotion);
    states.set(key, {
      ...fallbackState,
      source_detail_status: fallbackState.detail_status
    });
  }
  return states;
}

function validateRequestedSmartCancelItems({ promotions = [], itemsByPromotion = new Map(), action } = {}) {
  if (action !== 'cancel') return;
  const invalid = [];
  for (const promotion of promotions) {
    if (String(promotion.promotion_type || '').toUpperCase() !== 'SMART') continue;
    const items = itemsByPromotion.get(promotionKey(promotion)) || [];
    for (const item of items) {
      const evidence = smartCancelFieldEvidence({ promotion, item });
      if (!evidence.offer_id || !evidence.offer_id_is_started_offer) {
        invalid.push(`${evidence.item_id || item.item_id || item.id || '-'} 缺少 started OFFER-* offer_id`);
      }
    }
  }
  if (invalid.length) {
    throw new ApiError(`指定 SMART 商品取消字段不足，已停止执行，未改为处理其它商品：${invalid.join('；')}`, 409);
  }
}

async function preparePromotionsForExecution({ account, filters = {}, settings = readSettings(), request = {} }) {
  const readConcurrency = normalizeConcurrency(request.readConcurrency ?? settings.readConcurrency);
  const summary = {
    stages: [],
    readConcurrency,
    fetched: false,
    matched_before_fetch: 0,
    matched_after_fetch: 0,
    total_after_fetch: 0
  };
  let promotions = listOperatingCampaignsFiltered(account.account_id, filters || {}, settings);
  if (request.executionGroupId || request.submissionPrepareId || request.submission_prepare_id) promotions = ordinaryPromotions(promotions);
  summary.matched_before_fetch = promotions.length;
  const localTotal = listCampaigns(account.account_id).length;
  if (localTotal === 0 || promotions.length === 0) {
    const fetched = await fetchAndSavePromotions(account, { readConcurrency });
    summary.fetched = true;
    summary.fetch_result = { total: fetched.total, children: fetched.children };
    summary.max_active = fetched.maxActive || 0;
    summary.stages.push(`并发读取站点活动完成：读取并发 ${readConcurrency}，活动 ${fetched.total} 个。`);
    promotions = listOperatingCampaignsFiltered(account.account_id, filters || {}, settings);
    if (request.executionGroupId || request.submissionPrepareId || request.submission_prepare_id) promotions = ordinaryPromotions(promotions);
  } else {
    summary.stages.push(`使用本地活动缓存：匹配 ${promotions.length} 个；如需重新读取站点活动，将使用读取并发 ${readConcurrency}。`);
  }
  summary.matched_after_fetch = promotions.length;
  summary.total_after_fetch = listCampaigns(account.account_id).length;
  if (promotions.length === 0) summary.stages.push('未找到匹配活动。');
  return { promotions, summary };
}

function operationActivityReadKey(accountId, campaign = {}, status = '') {
  return activityReadKey({ ...campaign, account_id: accountId }, status);
}

function buildPreparationReadStateSnapshot(accountIds = []) {
  const rows = listPreparationReadStates(accountIds);
  const activityCacheStates = new Map((rows.activity_cache_states || []).map((row) => [
    preparationActivityStateKey(row),
    row,
  ]));
  const itemFetchStates = new Map((rows.item_fetch_states || []).map((row) => [
    preparationItemStateKey(row, row.item_status),
    row,
  ]));
  return {
    activity_cache_states: activityCacheStates,
    item_fetch_states: itemFetchStates,
    db_batch_queries: Number(rows.db_batch_queries || 0),
  };
}

function preparationActivityStateKey(value = {}) {
  return [
    String(value.account_id || ''),
    String(value.site_id || '').toUpperCase(),
    String(value.promotion_id || ''),
    String(value.promotion_type || '').toUpperCase(),
  ].join('|');
}

function preparationItemStateKey(value = {}, status = '') {
  return [
    String(value.account_id || ''),
    String(value.promotion_id || ''),
    String(value.promotion_type || '').toUpperCase(),
    String(status || '').toLowerCase(),
  ].join('|');
}

function preparationActivityCacheState(snapshot, promotion) {
  return snapshot?.activity_cache_states?.get(preparationActivityStateKey(promotion)) || null;
}

function preparationItemFetchState(snapshot, promotion, status) {
  return snapshot?.item_fetch_states?.get(preparationItemStateKey(promotion, status)) || null;
}

async function prepareItemsForExecution({
  account,
  promotions,
  action,
  itemStatus,
  settings,
  request = {},
  onProgress,
  shouldCancel,
  signal = null,
  checkpoint = null,
  readScheduler = null,
  operationReadCache = null,
  readStateSnapshot = null,
  forceReadKeys = null,
}) {
  const fetchMode = request.fetchMode === 'sample' ? 'sample' : 'full';
  const maxItems = fetchMode === 'full' ? 'all' : Number(request.maxItems || settings.maxItemsPerPromotion || 50);
  const readConcurrency = normalizeConcurrency(request.readConcurrency ?? settings.readConcurrency);
  const summary = {
    action,
    itemStatus,
    fetchMode,
    readConcurrency,
    promotions: promotions.length,
    platform_total: 0,
    saved_count: 0,
    failed_promotions: 0,
    fallback_promotions: 0,
    rows: [],
    stages: []
  };
  if (!promotions.length) {
    summary.stages.push('未找到匹配活动，未读取商品。');
    return { summary, rows: [] };
  }
  summary.stages.push(`读取 ${itemStatus} 商品：活动 ${promotions.length} 个。`);
  const worker = async (campaign, index) => {
    checkpoint?.();
    if (shouldCancel?.()) throw new Error('执行任务已停止。');
    const cacheState = readStateSnapshot
      ? preparationActivityCacheState(readStateSnapshot, { ...campaign, account_id: account.account_id })
      : getActivityCacheState(account.account_id, campaign.site_id, campaign.promotion_id, campaign.promotion_type);
    const fetchState = readStateSnapshot
      ? preparationItemFetchState(readStateSnapshot, { ...campaign, account_id: account.account_id }, itemStatus)
      : getItemFetchState(account.account_id, campaign.promotion_id, campaign.promotion_type, itemStatus);
    const fallbackState = itemStatus === 'candidate' && isSellerCampaign(campaign)
      ? (readStateSnapshot
          ? preparationItemFetchState(readStateSnapshot, { ...campaign, account_id: account.account_id }, INVENTORY_FALLBACK_ITEM_STATUS)
          : getItemFetchState(account.account_id, campaign.promotion_id, campaign.promotion_type, INVENTORY_FALLBACK_ITEM_STATUS))
      : null;
    const operationKey = operationActivityReadKey(account.account_id, campaign, itemStatus);
    const operationCached = operationReadCache?.get(operationKey) || null;
    const forceRefresh = forceReadKeys instanceof Set && forceReadKeys.has(operationKey);
    const cacheDecision = forceRefresh
      ? { refresh: true, reason: 'reconfirm_changed_target' }
      : fetchMode === 'full'
      ? activityItemsDecision({ promotion: campaign, cacheState, fetchState, fallbackState })
      : { refresh: true, reason: 'sample_requested' };
    onProgress?.({
      type: 'item_fetch_start',
      index,
      total: promotions.length,
      promotion_id: campaign.promotion_id,
      promotion_type: campaign.promotion_type,
      promotion: campaign,
      external_read: cacheDecision.refresh,
      cache_reused: !cacheDecision.refresh,
      refresh_reason: cacheDecision.reason,
    });
    const effectiveState = cacheDecision.effective_state ? fallbackState : fetchState;
    const row = (!forceRefresh && operationCached) || (cacheDecision.refresh
      ? await fetchAndSavePromotionItemsForCampaign({ account, campaign, status: itemStatus, maxItems, fetchMode, signal, checkpoint, readScheduler })
      : batchFetchRow(campaign, itemStatus, {
          total: Number(effectiveState?.platform_total ?? effectiveState?.saved_count ?? 0),
          saved: Number(effectiveState?.saved_count || 0),
          platform_total: effectiveState?.platform_total ?? effectiveState?.saved_count ?? 0,
          saved_count: Number(effectiveState?.saved_count || 0),
          is_full_fetch: true,
          sample_only: false,
          fetch_mode: 'cache',
          detail_status: cacheDecision.effective_state || effectiveState?.detail_status || 'ok',
          blocked: false,
          cache_reused: true,
          note: cacheDecision.reason === 'verified_composite_cache'
            ? '使用三日内已验证的 candidate 与库存兜底合成缓存。'
            : '使用三日内已验证完整活动商品缓存。',
        }));
    operationReadCache?.set(operationKey, row);
    checkpoint?.();
    if (
      action === 'enroll'
      && itemStatus === 'candidate'
      && request.allowInventoryFallback !== false
      && isSellerCampaign(campaign)
      && (CANDIDATE_INCOMPLETE_STATUSES.has(row.detail_status) || row.detail_status === 'partial_api_sparse_marketplace_candidate' || row.blocked)
    ) {
      onProgress?.({
        type: 'inventory_fallback_start',
        index,
        total: promotions.length,
        promotion_id: campaign.promotion_id,
        promotion_type: campaign.promotion_type,
        promotion: campaign
      });
      const fallback = await scanAndSaveInventoryFallbackForCampaign({
        account,
        campaign,
        listingStatus: request.listingStatus || 'all',
        detailConcurrency: readConcurrency,
        sellerDiscountPercent: Number(request.sellerDiscountPercent ?? settings.sellerDefaultDiscount ?? 5),
        maxScanItems: request.maxScanItems || 'all',
        signal,
        checkpoint,
        readScheduler,
        operationReadCache,
      });
      const combined = { ...row, fallback };
      onProgress?.({
        type: 'inventory_fallback_done',
        index,
        total: promotions.length,
        promotion_id: campaign.promotion_id,
        promotion_type: campaign.promotion_type,
        promotion: campaign,
        platform_total: fallback?.platform_total ?? fallback?.total ?? null,
        saved_count: fallback?.saved_count ?? fallback?.saved ?? null,
        detail_status: fallback?.detail_status || null,
        sample_only: fallback?.sample_only ?? null,
        is_full_fetch: fallback?.is_full_fetch ?? null,
        stop_reason: fallback?.stop_reason || null,
        note: fallback?.note || null,
        error: fallback?.error || null,
        blocked: Boolean(fallback?.blocked)
      });
      onProgress?.({
        type: 'item_fetch_done',
        index,
        total: promotions.length,
        promotion_id: campaign.promotion_id,
        promotion_type: campaign.promotion_type,
        promotion: campaign,
        platform_total: fallback?.platform_total ?? fallback?.total ?? row?.platform_total ?? row?.total ?? null,
        saved_count: fallback?.saved_count ?? fallback?.saved ?? row?.saved_count ?? row?.saved ?? null,
        detail_status: fallback?.detail_status || row?.detail_status || null,
        sample_only: fallback?.sample_only ?? row?.sample_only ?? null,
        is_full_fetch: fallback?.is_full_fetch ?? row?.is_full_fetch ?? null,
        stop_reason: fallback?.stop_reason || row?.stop_reason || null,
        note: fallback?.note || row?.note || null,
        error: fallback?.error || row?.error || null,
        blocked: Boolean(fallback?.blocked)
      });
      operationReadCache?.set(operationKey, combined);
      return combined;
    }
    onProgress?.({
      type: 'item_fetch_done',
      index,
      total: promotions.length,
      promotion_id: campaign.promotion_id,
      promotion_type: campaign.promotion_type,
      promotion: campaign,
      platform_total: row?.platform_total ?? row?.total ?? null,
      saved_count: row?.saved_count ?? row?.saved ?? null,
      detail_status: row?.detail_status || null,
      sample_only: row?.sample_only ?? null,
      is_full_fetch: row?.is_full_fetch ?? null,
      stop_reason: row?.stop_reason || null,
      note: row?.note || null,
      error: row?.error || null,
      blocked: Boolean(row?.blocked)
    });
    return row;
  };
  const rows = readScheduler
    ? await Promise.all(promotions.map(worker))
    : await mapLimited(promotions, readConcurrency, worker);
  for (const row of rows) {
    const fallback = row?.fallback || null;
    const effective = fallback || row;
    summary.platform_total += Number(effective?.platform_total ?? effective?.total ?? 0) || 0;
    summary.saved_count += Number(effective?.saved_count ?? effective?.saved ?? 0) || 0;
    if (row?.error || row?.detail_status === 'error' || fallback?.error) summary.failed_promotions += 1;
    if (fallback) summary.fallback_promotions += 1;
    summary.rows.push({
      site_id: row?.site_id || null,
      child_user_id: row?.child_user_id || null,
      promotion_id: row?.promotion_id || null,
      promotion_type: row?.promotion_type || null,
      status: itemStatus,
      platform_total: effective?.platform_total ?? effective?.total ?? null,
      saved_count: effective?.saved_count ?? effective?.saved ?? null,
      detail_status: effective?.detail_status || null,
      note: effective?.note || row?.note || row?.error || null,
      fallback_used: Boolean(fallback),
      blocked: fallback ? Boolean(fallback.blocked) : Boolean(row?.blocked),
    });
  }
  if (summary.saved_count === 0) {
    summary.stages.push(`未读取到 ${itemStatus} 商品；请检查活动状态、平台返回或筛选条件。`);
  } else {
    summary.stages.push(`商品读取完成：平台 total ${summary.platform_total}，本地可处理 ${summary.saved_count}。`);
  }
  return { summary, rows };
}

async function readMarketplacePromotionsForChild({ account, childId, siteId, signal = null, checkpoint = null, readScheduler = null }) {
  const parentCallerId = String(account.account_id || '').trim();
  const targetChildId = String(childId || '').trim();
  const route = normalizeAccountRoute({ account_id: parentCallerId, child_user_id: targetChildId, site_id: siteId });
  const sources = [
    {
      source: 'read_promotions_parent_caller',
      authoritative: true,
      marketplace: true,
      callerId: parentCallerId || targetChildId,
      read: async () => {
        const client = new MercadoLibreClient({
          accessToken: account.accessToken,
          userId: targetChildId,
          callerId: parentCallerId || targetChildId,
          marketplace: true,
          readScheduler,
          readAccountId: account.account_id,
        });
        return client.getMarketplacePromotions(targetChildId, { callerId: parentCallerId || targetChildId, signal });
      }
    },
    {
      source: 'read_promotions_child_caller',
      authoritative: true,
      marketplace: true,
      callerId: targetChildId,
      read: async () => {
        const client = new MercadoLibreClient({
          accessToken: account.accessToken,
          userId: targetChildId,
          callerId: targetChildId,
          marketplace: true,
          readScheduler,
          readAccountId: account.account_id,
        });
        return client.getMarketplacePromotions(targetChildId, { callerId: targetChildId, signal });
      }
    },
    {
      source: 'read_promotions_regular_parent_caller',
      authoritative: false,
      marketplace: false,
      callerId: parentCallerId || targetChildId,
      read: async () => {
        const client = new MercadoLibreClient({
          accessToken: account.accessToken,
          userId: targetChildId,
          callerId: parentCallerId || targetChildId,
          marketplace: false,
          readScheduler,
          readAccountId: account.account_id,
        });
        return client.getPromotions({ userId: targetChildId, callerId: parentCallerId || targetChildId, includeVersionHeader: true, signal });
      }
    }
  ];
  const promotionGroups = [];
  const sourceSummaries = [];
  const errors = [];
  const sourceResults = await Promise.all(sources.map(async (source) => {
    try {
      checkpoint?.();
      const data = await source.read();
      checkpoint?.();
      const promotions = bindActivitiesToAccountRoute(extractPromotions(data), route);
      return { promotions, summary: {
        source: source.source,
        authoritative: source.authoritative,
        ok: true,
        total: promotions.length,
        seller_campaign_count: sellerCampaignPromotionCount(promotions)
      } };
    } catch (error) {
      if (signal?.aborted
          || String(error?.code || '').startsWith('COMMIT_')
          || String(error?.code || '').startsWith('SUBMISSION_')
          || String(error?.code || '').startsWith('ACTIVITY_ACCOUNT_ROUTE_')) throw error;
      return { error: {
        source: source.source,
        authoritative: source.authoritative,
        ok: false,
        error: toChineseError(error)
      } };
    }
  }));
  for (const result of sourceResults) {
    if (result.promotions) promotionGroups.push(result.promotions);
    if (result.summary) sourceSummaries.push(result.summary);
    if (result.error) errors.push(result.error);
  }
  requireAuthoritativeActivityCatalogRead([...sourceSummaries, ...errors]);
  if (!sourceSummaries.length) {
    const error = errors[0]?.error || '活动读取失败';
    throw new Error(error);
  }
  const promotions = mergePromotionsByIdentity(...promotionGroups);
  return {
    promotions,
    sources: [...sourceSummaries, ...errors],
    errors
  };
}

function cachedActivityCatalogForRoute(route) {
  const key = accountRouteKey(route);
  return listCampaigns(route.account_id)
    .filter((promotion) => String(promotion.status || '').toLowerCase() !== 'catalog_removed')
    .filter((promotion) => accountRouteKey({
      account_id: promotion.account_id,
      child_user_id: promotion.child_user_id,
      site_id: promotion.site_id,
    }) === key);
}

function persistLiveActivityCatalog({ route, promotions, logisticType = null }) {
  const normalizedRoute = normalizeAccountRoute(route);
  const reconciled = reconcileActivityCatalog({
    route: normalizedRoute,
    cachedPromotions: cachedActivityCatalogForRoute(normalizedRoute),
    livePromotions: promotions,
  });
  const changedRows = [...reconciled.added, ...reconciled.changed];
  if (changedRows.length) {
    saveCampaigns(normalizedRoute.account_id, changedRows, {
      merchantId: normalizedRoute.account_id,
      childUserId: normalizedRoute.child_user_id,
      siteId: normalizedRoute.site_id,
      logisticType,
    });
  }
  if (reconciled.removed.length) {
    markCampaignsCatalogRemoved({
      accountId: normalizedRoute.account_id,
      childUserId: normalizedRoute.child_user_id,
      siteId: normalizedRoute.site_id,
      promotions: reconciled.removed,
    });
  }
  for (const change of reconciled.dirty_identities) {
    const promotion = change.promotion;
    if (change.reason === 'removed') {
      saveActivityCacheState({
        accountId: normalizedRoute.account_id,
        siteId: normalizedRoute.site_id,
        promotionId: promotion.promotion_id,
        promotionType: promotion.promotion_type,
        dirty: true,
        expired: true,
        lastError: '实时活动目录已不再返回该活动。',
      });
    } else {
      markActivityCacheDirty({
        accountId: normalizedRoute.account_id,
        siteId: normalizedRoute.site_id,
        promotionId: promotion.promotion_id,
        promotionType: promotion.promotion_type,
      });
    }
  }
  return reconciled;
}

function sellerCampaignPromotionCount(promotions = []) {
  return promotions.filter((promotion) => String(promotion?.promotion_type || promotion?.type || '').toUpperCase() === 'SELLER_CAMPAIGN').length;
}

async function fetchAndSavePromotions(account, {
  readConcurrency = readSettings().readConcurrency,
  siteIds = null,
  routes = null,
  signal = null,
  checkpoint = null,
  readScheduler = null,
} = {}) {
  const normalizedReadConcurrency = normalizeConcurrency(readConcurrency);
  if (account.site_id === 'CBT') {
    checkpoint?.();
    const marketplaceUsers = await discoverAndSaveMarketplaceSites(account, { signal, readScheduler });
    checkpoint?.();
    const selectedUsers = selectMarketplaceUsersForCatalogRoutes({
      accountId: account.account_id,
      marketplaceUsers,
      routes,
      siteIds,
    });
    let total = 0;
    const childWorker = async (child) => {
      const childId = String(child.user_id);
      try {
        checkpoint?.();
        const readResult = await readMarketplacePromotionsForChild({ account, childId, siteId: child.site_id, signal, checkpoint, readScheduler });
        checkpoint?.();
        const reconciliation = persistLiveActivityCatalog({
          route: { account_id: account.account_id, child_user_id: childId, site_id: child.site_id },
          promotions: readResult.promotions,
          logisticType: child.logistic_type,
        });
        const promotions = reconciliation.live_promotions;
        const sellerCampaignCount = sellerCampaignPromotionCount(promotions);
        updateMarketplaceSitePromotionStatus({ accountId: account.account_id, childUserId: childId, count: promotions.length, status: 'ok' });
        recordActivityCatalogCalibration({ accountId: account.account_id, siteId: child.site_id });
        return {
          child_user_id: childId,
          site_id: child.site_id,
          logistic_type: child.logistic_type,
          total: promotions.length,
          seller_campaign_count: sellerCampaignCount,
          catalog_revision: reconciliation.revision,
          catalog_changes: {
            added: reconciliation.added.length,
            changed: reconciliation.changed.length,
            removed: reconciliation.removed.length,
            identities: reconciliation.dirty_identities.map(({ key, reason }) => ({ key, reason })),
          },
          read_sources: readResult.sources,
          status: 'ok'
        };
      } catch (error) {
        if (signal?.aborted
            || String(error?.code || '').startsWith('COMMIT_')
            || String(error?.code || '').startsWith('SUBMISSION_')
            || String(error?.code || '').startsWith('ACTIVITY_ACCOUNT_ROUTE_')) throw error;
        const errorCn = toChineseError(error);
        updateMarketplaceSitePromotionStatus({ accountId: account.account_id, childUserId: childId, count: 0, status: 'error', error: errorCn });
        recordActivityCatalogCalibration({ accountId: account.account_id, siteId: child.site_id, error: errorCn });
        return { child_user_id: childId, site_id: child.site_id, logistic_type: child.logistic_type, total: 0, status: 'error', error: errorCn };
      }
    };
    const children = readScheduler
      ? await Promise.all(selectedUsers.map(childWorker))
      : await mapLimited(selectedUsers, normalizedReadConcurrency, childWorker);
    for (const child of children) total += Number(child?.total || 0);
    return { total, children, readConcurrency: normalizedReadConcurrency, maxActive: children.maxActive || 0 };
  }
  const client = new MercadoLibreClient({
    accessToken: account.accessToken, userId: account.account_id, callerId: account.account_id,
    readScheduler, readAccountId: account.account_id,
  });
  checkpoint?.();
  const data = await client.getPromotions({ signal });
  checkpoint?.();
  const route = {
    account_id: account.account_id,
    child_user_id: account.account_id,
    site_id: account.site_id,
  };
  const promotions = bindActivitiesToAccountRoute(extractPromotions(data), route);
  const reconciliation = persistLiveActivityCatalog({ route, promotions });
  const sellerCampaignCount = sellerCampaignPromotionCount(promotions);
  recordActivityCatalogCalibration({ accountId: account.account_id, siteId: account.site_id });
  return {
    total: promotions.length,
    seller_campaign_count: sellerCampaignCount,
    catalog_revision: reconciliation.revision,
    catalog_changes: {
      added: reconciliation.added.length,
      changed: reconciliation.changed.length,
      removed: reconciliation.removed.length,
      identities: reconciliation.dirty_identities.map(({ key, reason }) => ({ key, reason })),
    },
    children: [], readConcurrency: normalizedReadConcurrency, maxActive: 1,
  };
}

async function runReadConcurrencyBenchmark({ account, input = {} }) {
  const settings = readSettings();
  const levels = normalizeBenchmarkLevels(input.levels || READ_BENCHMARK_LEVELS);
  const filters = input.filters || settings.defaultFilters || {};
  const statuses = Array.isArray(input.statuses) && input.statuses.length
    ? input.statuses.map(requireItemStatus)
    : ['candidate', 'started'];
  const maxPromotionsPerStatus = Math.max(1, Math.min(20, Math.floor(Number(input.maxPromotionsPerStatus || 4))));
  const maxItems = input.maxItems === 'all' ? 'all' : Math.max(1, Math.floor(Number(input.maxItems || settings.maxItemsPerPromotion || 50)));
  const benchmark = {
    benchmark_type: 'read_concurrency',
    account_id: String(account.account_id),
    started_at: new Date().toISOString(),
    levels,
    statuses,
    max_promotions_per_status: maxPromotionsPerStatus,
    max_items: maxItems,
    note: '只读压测只调用 Mercado GET，不修改活动或商品。',
    results: []
  };
  for (const level of levels) {
    const startedAt = Date.now();
    const activity = await benchmarkActivityRead({ account, concurrency: level });
    await preparePromotionsForExecution({ account, filters, settings, request: { readConcurrency: Math.min(level, READ_BENCHMARK_MAX_CONCURRENCY) } });
    const promotions = listCampaignsFiltered(account.account_id, filters).slice(0, maxPromotionsPerStatus);
    const itemResults = [];
    for (const status of statuses) {
      itemResults.push(await benchmarkItemRead({
        account,
        promotions,
        status,
        concurrency: level,
        maxItems
      }));
    }
    const combinedRequests = [
      ...activity.requests,
      ...itemResults.flatMap((result) => result.requests)
    ];
    benchmark.results.push({
      level,
      duration_ms: Date.now() - startedAt,
      activity,
      item_reads: itemResults,
      summary: summarizeBenchmarkRequests(combinedRequests),
      recommendation: benchmarkRecommendation(combinedRequests, level)
    });
  }
  benchmark.finished_at = new Date().toISOString();
  benchmark.suggested_read_concurrency = suggestReadConcurrency(benchmark.results);
  return benchmark;
}

async function benchmarkActivityRead({ account, concurrency }) {
  const requests = [];
  if (account.site_id !== 'CBT') {
    const client = new MercadoLibreClient({ accessToken: account.accessToken, userId: account.account_id, callerId: account.account_id });
    const result = await timedBenchmarkRequest({ kind: 'activity', site_id: account.site_id, child_user_id: account.account_id }, async () => {
      const data = await client.getPromotions();
      return { count: extractPromotions(data).length };
    });
    requests.push(result);
    return { concurrency, maxActive: 1, requests, summary: summarizeBenchmarkRequests(requests) };
  }
  const marketplaceUsers = await discoverAndSaveMarketplaceSites(account);
  const rows = await mapLimitedWithCap(marketplaceUsers, concurrency, READ_BENCHMARK_MAX_CONCURRENCY, async (child) => {
    const childId = String(child.user_id);
    const client = new MercadoLibreClient({
      accessToken: account.accessToken,
      userId: childId,
      callerId: childId,
      marketplace: true
    });
    return timedBenchmarkRequest({ kind: 'activity', site_id: child.site_id, child_user_id: childId, logistic_type: child.logistic_type }, async () => {
      const data = await client.getMarketplacePromotions(childId);
      return { count: extractPromotions(data).length };
    });
  });
  requests.push(...rows);
  return { concurrency, maxActive: rows.maxActive || 0, requests, summary: summarizeBenchmarkRequests(requests) };
}

async function benchmarkItemRead({ account, promotions, status, concurrency, maxItems }) {
  const rows = await mapLimitedWithCap(promotions, concurrency, READ_BENCHMARK_MAX_CONCURRENCY, async (campaign) => {
    const client = new MercadoLibreClient({
      accessToken: account.accessToken,
      userId: campaign.child_user_id || account.account_id,
      callerId: campaign.child_user_id || account.account_id,
      marketplace: isMarketplaceCampaign(account, campaign)
    });
    return timedBenchmarkRequest({
      kind: 'items',
      status,
      site_id: campaign.site_id,
      promotion_type: campaign.promotion_type,
      promotion_name: campaign.name || '',
      promotion_id: campaign.promotion_id
    }, async () => {
      const result = await client.fetchAllPromotionItems({
        promotionId: campaign.promotion_id,
        promotionType: campaign.promotion_type,
        status,
        maxItems
      });
      return { count: result.saved, total: result.total, detail_status: result.detailStatus };
    });
  });
  return {
    status,
    promotions_tested: promotions.length,
    concurrency,
    maxActive: rows.maxActive || 0,
    requests: rows,
    summary: summarizeBenchmarkRequests(rows)
  };
}

async function timedBenchmarkRequest(meta, worker) {
  const startedAt = Date.now();
  try {
    const output = await worker();
    return {
      ...meta,
      ok: true,
      duration_ms: Date.now() - startedAt,
      count: output?.count ?? null,
      total: output?.total ?? null,
      detail_status: output?.detail_status || null
    };
  } catch (error) {
    return {
      ...meta,
      ok: false,
      duration_ms: Date.now() - startedAt,
      status: error?.status || null,
      error_cn: toChineseError(error),
      timeout: /timeout|aborted|timed out/i.test(String(error?.message || error))
    };
  }
}

function summarizeBenchmarkRequests(requests) {
  const durations = requests.map((request) => Number(request.duration_ms || 0)).sort((a, b) => a - b);
  const failed = requests.filter((request) => !request.ok);
  return {
    total_requests: requests.length,
    success: requests.length - failed.length,
    failed: failed.length,
    http_429: failed.filter((request) => Number(request.status) === 429 || /限流|429/.test(request.error_cn || '')).length,
    http_5xx: failed.filter((request) => Number(request.status) >= 500).length,
    timeout: failed.filter((request) => request.timeout || /超时|timeout/i.test(request.error_cn || '')).length,
    average_ms: durations.length ? Math.round(durations.reduce((sum, value) => sum + value, 0) / durations.length) : 0,
    p50_ms: percentile(durations, 0.5),
    p95_ms: percentile(durations, 0.95),
    slowest_ms: durations.at(-1) || 0,
    failed_targets: failed.slice(0, 10).map((request) => ({
      kind: request.kind,
      site_id: request.site_id,
      promotion_name: request.promotion_name,
      promotion_id: request.promotion_id,
      status: request.status,
      error_cn: request.error_cn
    }))
  };
}

function percentile(sorted, p) {
  if (!sorted.length) return 0;
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil(sorted.length * p) - 1));
  return sorted[index];
}

function benchmarkRecommendation(requests, level) {
  const summary = summarizeBenchmarkRequests(requests);
  if (summary.http_429 > 0) return `并发 ${level} 出现限流，建议低于该档。`;
  if (summary.timeout > 0 || summary.http_5xx > 0) return `并发 ${level} 出现超时或平台异常，建议谨慎降低。`;
  if (summary.failed > 0) return `并发 ${level} 有失败请求，需查看失败站点/活动。`;
  return `并发 ${level} 只读通过。`;
}

function suggestReadConcurrency(results) {
  const passed = results.filter((result) => {
    const summary = result.summary || {};
    return Number(summary.failed || 0) === 0 && Number(summary.http_429 || 0) === 0 && Number(summary.timeout || 0) === 0;
  });
  if (!passed.length) return 1;
  return Math.max(...passed.map((result) => Number(result.level || 1)));
}

function normalizeBenchmarkLevels(value) {
  const levels = (Array.isArray(value) ? value : String(value || '').split(','))
    .map((item) => normalizeConcurrencyWithCap(item, 1, READ_BENCHMARK_MAX_CONCURRENCY))
    .filter(Boolean);
  return [...new Set(levels)].sort((a, b) => a - b);
}

function buildWriteConcurrencyBenchmarkPlan({ account, input = {} }) {
  const levels = normalizeWriteBenchmarkLevels(input.levels || WRITE_BENCHMARK_LEVELS);
  const action = input.action === 'update' ? 'update' : 'enroll';
  const itemStatus = action === 'update' ? 'started' : 'candidate';
  const filters = input.filters || readSettings().defaultFilters || {};
  const candidatePromotions = listCampaignsFiltered(account.account_id, {
    ...filters,
    status: filters.status || '',
    promotionTypes: filters.promotionTypes || []
  }).filter((promotion) => !['SMART', 'LIGHTNING'].includes(String(promotion.promotion_type || '').toUpperCase()));
  const promotion = candidatePromotions.find((item) => listItems(account.account_id, item.promotion_id, item.promotion_type, itemStatus).length > 0) || candidatePromotions[0] || null;
  const sampleSize = Math.max(1, Math.min(20, Math.floor(Number(input.sampleSize || 3))));
  const sampleItems = promotion
    ? listItems(account.account_id, promotion.promotion_id, promotion.promotion_type, itemStatus).slice(0, sampleSize).map((item) => ({
      item_id: item.item_id || item.id,
      status: item.status,
      original_price: item.original_price,
      price: item.price,
      min: item.min_discounted_price,
      max: item.max_discounted_price
    }))
    : [];
  return {
    benchmark_type: 'write_concurrency_plan',
    enabled: false,
    disabled_reason: '真实写入压测会改变 Mercado 活动商品状态，本接口只生成候选包，不执行 POST/PUT/DELETE。',
    account_id: String(account.account_id),
    site_id: promotion?.site_id || null,
    promotion_id: promotion?.promotion_id || null,
    promotion_type: promotion?.promotion_type || null,
    promotion_name: promotion?.name || null,
    action,
    item_status: itemStatus,
    levels,
    sample_size: sampleSize,
    sample_items: sampleItems,
    stop_conditions: [
      '任一档出现 429 限流即停止后续档位',
      '任一档超时或失败率超过 20% 即停止',
      '每档执行后回查 candidate/pending/started 状态',
      '成功报名后不自动取消；如需撤销必须另行确认'
    ],
    recheck_method: '每档后只读 GET 同活动 candidate/pending/started，核对商品状态变化并写入本地结果。',
    requires_final_confirmation: true,
    created_at: new Date().toISOString()
  };
}

function ensureWriteBenchmarkJobDir() {
  fs.mkdirSync(WRITE_BENCHMARK_JOB_DIR, { recursive: true });
}

function readWriteBenchmarkJobIndex() {
  try {
    return JSON.parse(fs.readFileSync(WRITE_BENCHMARK_JOB_INDEX_PATH, 'utf8'));
  } catch {
    return { jobs: [] };
  }
}

function writeWriteBenchmarkJobIndex(index) {
  ensureWriteBenchmarkJobDir();
  fs.writeFileSync(WRITE_BENCHMARK_JOB_INDEX_PATH, JSON.stringify({ jobs: index.jobs || [] }, null, 2), 'utf8');
}

function safeWriteBenchmarkJobId(jobId) {
  return String(jobId || '').replace(/[^A-Za-z0-9_.-]/g, '');
}

function writeBenchmarkJobEventPath(jobId) {
  return path.join(WRITE_BENCHMARK_JOB_DIR, `${safeWriteBenchmarkJobId(jobId)}.jsonl`);
}

function appendWriteBenchmarkJobEvent(job, event) {
  ensureWriteBenchmarkJobDir();
  const row = {
    at: new Date().toISOString(),
    jobId: job.id,
    ...event
  };
  fs.appendFileSync(writeBenchmarkJobEventPath(job.id), `${JSON.stringify(row)}\n`, 'utf8');
  job.last_event_at = row.at;
  return row;
}

function persistWriteBenchmarkJob(job) {
  const index = readWriteBenchmarkJobIndex();
  const publicJob = publicWriteBenchmarkJob(job);
  const nextJobs = (index.jobs || []).filter((item) => item.id !== job.id);
  nextJobs.unshift(publicJob);
  writeWriteBenchmarkJobIndex({ jobs: nextJobs.slice(0, 100) });
  writeBenchmarkJobs.set(job.id, job);
}

function loadWriteBenchmarkJob(jobId) {
  const id = safeWriteBenchmarkJobId(jobId);
  if (writeBenchmarkJobs.has(id)) return writeBenchmarkJobs.get(id);
  const persisted = (readWriteBenchmarkJobIndex().jobs || []).find((job) => job.id === id);
  if (!persisted) return null;
  const hydrated = {
    ...persisted,
    cancel_requested: Boolean(persisted.cancel_requested),
    persisted_only: true,
    input: persisted.input_summary || {},
    progress: persisted.progress || {}
  };
  writeBenchmarkJobs.set(id, hydrated);
  return hydrated;
}

function readWriteBenchmarkJobEvents(jobId, limit = 500) {
  const file = writeBenchmarkJobEventPath(jobId);
  try {
    const lines = fs.readFileSync(file, 'utf8').split(/\r?\n/).filter(Boolean);
    return lines.slice(-limit).map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return { parse_error: true, raw: line.slice(0, 500) };
      }
    });
  } catch {
    return [];
  }
}

function writeBenchmarkItemsFromEvents(events = []) {
  return events
    .filter((event) => event.item && ['item_start', 'item_finish', 'item_cancelled'].includes(event.type))
    .map((event) => ({
      event_type: event.type,
      at: event.at,
      ...event.item
    }));
}

function findUnfinishedWriteBenchmarkJob() {
  const unfinishedStatuses = new Set(['queued', 'running', 'stopping', 'legacy_unknown']);
  for (const job of writeBenchmarkJobs.values()) {
    if (unfinishedStatuses.has(job.status)) return job;
  }
  return (readWriteBenchmarkJobIndex().jobs || []).find((job) => unfinishedStatuses.has(job.status)) || null;
}

function publicWriteBenchmarkJob(job) {
  return {
    id: job.id,
    status: job.status,
    cancel_requested: Boolean(job.cancel_requested),
    dry_run: Boolean(job.dry_run),
    real_write_enabled: Boolean(job.real_write_enabled),
    action: job.action,
    item_status: job.item_status,
    levels: job.levels || [],
    current_level: job.current_level || null,
    sample_offset: Number(job.sample_offset || 0),
    round: Number(job.round || 1),
    attempt: Number(job.attempt || 1),
    started_at: job.started_at || null,
    finished_at: job.finished_at || null,
    last_event_at: job.last_event_at || null,
    input_summary: job.input_summary || {},
    progress: job.progress || {},
    level_results: job.level_results || [],
    error: job.error || null,
    event_log_path: writeBenchmarkJobEventPath(job.id)
  };
}

function createWriteBenchmarkJob({ account, input = {} }) {
  const dryRun = input.dryRun !== false;
  if (!dryRun && input.confirmText !== 'REAL_WRITE_BENCHMARK') {
    const error = new Error('真实写入压测 job 必须显式传入 REAL_WRITE_BENCHMARK；默认只允许 dry-run/fake 验证。');
    error.status = 409;
    throw error;
  }
  const levels = normalizeWriteBenchmarkLevels(input.levels || WRITE_BENCHMARK_LEVELS);
  const action = input.action === 'update' ? 'update' : 'enroll';
  const itemStatus = action === 'update' ? 'started' : 'candidate';
  const id = `wbj-${Date.now()}-${nextWriteBenchmarkJobSeq++}`;
  const job = {
    id,
    status: 'queued',
    cancel_requested: false,
    dry_run: dryRun,
    real_write_enabled: !dryRun,
    account_id: String(account.account_id),
    action,
    item_status: itemStatus,
    levels,
    current_level: null,
    sample_offset: Math.max(0, Math.floor(Number(input.sampleOffset || input.sample_offset || 0))),
    round: Math.max(1, Math.floor(Number(input.round || 1))),
    attempt: Math.max(1, Math.floor(Number(input.attempt || 1))),
    started_at: new Date().toISOString(),
    finished_at: null,
    input: { ...input, confirmText: undefined },
    input_summary: writeBenchmarkInputSummary(input, account),
    progress: {
      stage: 'queued',
      total_levels: levels.length,
      completed_levels: 0,
      current_level: null,
      current_level_items: 0,
      current_level_started: 0,
      current_level_finished: 0,
      peak_in_flight: 0,
      success: 0,
      failed: 0,
      interface_failed: 0,
      business_failed: 0,
      skipped: 0
    },
    level_results: [],
    error: null
  };
  writeBenchmarkJobs.set(id, job);
  appendWriteBenchmarkJobEvent(job, { type: 'job_created', input_summary: job.input_summary });
  persistWriteBenchmarkJob(job);
  return job;
}

function createLegacyUnknownWriteBenchmarkJob(input = {}) {
  const id = `legacy-unknown-${Date.now()}-${nextWriteBenchmarkJobSeq++}`;
  const job = {
    id,
    status: 'legacy_unknown',
    dry_run: false,
    real_write_enabled: false,
    action: input.action || 'update',
    item_status: input.itemStatus || 'started',
    levels: input.levels || [],
    sample_offset: Math.max(0, Math.floor(Number(input.sampleOffset || input.sample_offset || 0))),
    round: Math.max(1, Math.floor(Number(input.round || 1))),
    attempt: Math.max(1, Math.floor(Number(input.attempt || 1))),
    started_at: input.startedAt || new Date().toISOString(),
    finished_at: null,
    input_summary: {
      note: input.note || '遗留未知写入压测：可能已经产生部分 Mercado 写入，但旧同步接口没有逐商品结果落盘。',
      level: input.level || null,
      sample_offset: input.sampleOffset || input.sample_offset || null
    },
    progress: { stage: 'legacy_unknown' },
    level_results: [],
    error: input.error || null
  };
  writeBenchmarkJobs.set(id, job);
  appendWriteBenchmarkJobEvent(job, { type: 'legacy_unknown_marked', input_summary: job.input_summary });
  persistWriteBenchmarkJob(job);
  return job;
}

function writeBenchmarkInputSummary(input, account) {
  return {
    account_id: String(account.account_id),
    all_accounts: Boolean(input.allAccounts),
    action: input.action === 'update' ? 'update' : 'enroll',
    dry_run: input.dryRun !== false,
    levels: normalizeWriteBenchmarkLevels(input.levels || WRITE_BENCHMARK_LEVELS),
    sample_offset: input.sampleOffset || input.sample_offset || 0,
    round: input.round || 1,
    attempt: input.attempt || 1,
    discount_percent: Number(input.discountPercent) || null,
    fake_item_count: input.fakeItemCount || null,
    exclude_item_count: Array.isArray(input.excludeItemIds) ? input.excludeItemIds.length : 0
  };
}

async function runWriteBenchmarkJob(jobId) {
  const job = writeBenchmarkJobs.get(jobId);
  if (!job) return;
  job.status = 'running';
  job.progress.stage = 'collecting_samples';
  appendWriteBenchmarkJobEvent(job, { type: 'job_started' });
  persistWriteBenchmarkJob(job);
  try {
    const account = await ensureUsableAccount(job.account_id);
    const rows = await collectRowsForWriteBenchmarkJob({ job, account });
    job.progress.total_items = rows.length;
    appendWriteBenchmarkJobEvent(job, { type: 'sample_pool_ready', item_count: rows.length, dry_run: job.dry_run });
    let cursor = 0;
    let cumulativeTimeouts = 0;
    for (const level of job.levels) {
      if (job.cancel_requested) break;
      const sampleSize = Math.max(level, 3);
      const rowsForLevel = rows.slice(cursor, cursor + sampleSize);
      if (rowsForLevel.length < sampleSize) {
        const levelResult = {
          level,
          requested_items: sampleSize,
          item_count: rowsForLevel.length,
          skipped: true,
          valid: false,
          stable: false,
          stop_reason: `剩余样本 ${rowsForLevel.length} 个，不足以验证并发 ${level} 档。`
        };
        job.level_results.push(levelResult);
        job.status = 'completed';
        job.progress.stage = 'sample_exhausted';
        appendWriteBenchmarkJobEvent(job, { type: 'level_skipped', ...levelResult });
        break;
      }
      cursor += sampleSize;
      const levelResult = await runWriteBenchmarkJobLevel({ job, level, rows: rowsForLevel });
      job.level_results.push(levelResult);
      job.progress.completed_levels += 1;
      cumulativeTimeouts += Number(levelResult.summary?.timeout || 0);
      persistWriteBenchmarkJob(job);
      if (job.cancel_requested) break;
      const stopReason = writeBenchmarkStopReason({ level, summary: levelResult.summary }, cumulativeTimeouts);
      if (stopReason) {
        job.status = 'completed';
        job.progress.stage = 'stopped_by_rule';
        job.stop_reason = stopReason;
        appendWriteBenchmarkJobEvent(job, { type: 'job_stopped_by_rule', stop_reason: stopReason });
        break;
      }
    }
    if (job.cancel_requested) {
      job.status = 'cancelled';
      job.progress.stage = 'cancelled';
      appendWriteBenchmarkJobEvent(job, { type: 'job_cancelled' });
    } else if (!['completed', 'failed'].includes(job.status)) {
      job.status = 'completed';
      job.progress.stage = 'completed';
      appendWriteBenchmarkJobEvent(job, { type: 'job_completed' });
    }
  } catch (error) {
    job.status = 'failed';
    job.progress.stage = 'failed';
    job.error = toChineseError(error);
    appendWriteBenchmarkJobEvent(job, { type: 'job_failed', error: job.error });
  } finally {
    job.finished_at = new Date().toISOString();
    persistWriteBenchmarkJob(job);
  }
}

async function collectRowsForWriteBenchmarkJob({ job, account }) {
  const input = job.input || {};
  if (job.dry_run || input.fakeRows || input.fakeItemCount) {
    return buildFakeWriteBenchmarkRows({ job, account, input });
  }
  const settings = readSettings();
  const requiredItems = (job.levels || []).reduce((sum, level) => sum + Math.max(level, 3), 0);
  const accounts = await resolveWriteBenchmarkAccounts(account, input);
  const samplePool = await collectWriteBenchmarkRows({
    accounts,
    action: job.action,
    itemStatus: job.item_status,
    input,
    settings,
    requiredItems
  });
  const excluded = new Set((input.excludeItemIds || input.exclude_item_ids || []).map(String));
  return samplePool.rows.filter((row) => !excluded.has(String(row.item?.item_id || '')));
}

function buildFakeWriteBenchmarkRows({ job, account, input = {} }) {
  const fakeRows = Array.isArray(input.fakeRows) ? input.fakeRows : [];
  const requiredItems = Math.max(
    Number(input.fakeItemCount || 0),
    (job.levels || []).reduce((sum, level) => sum + Math.max(level, 3), 0)
  );
  const rows = fakeRows.length ? fakeRows : Array.from({ length: requiredItems }, (_, index) => ({
    itemId: `FAKE-${job.id}-${index + 1}`,
    accountId: account.account_id,
    storeName: storeIdentityForAccount(account.account_id, account).store_name,
    siteId: input.siteId || 'MLM',
    promotionId: input.promotionId || 'FAKE-PROMOTION',
    promotionType: input.promotionType || 'DEAL',
    promotionName: input.promotionName || '假执行压测活动',
    originalPrice: 100,
    price: 95,
    dealPrice: Number(input.targetPrice || 90)
  }));
  return rows.map((source, index) => {
    const campaign = {
      promotion_id: source.promotionId || source.promotion_id || input.promotionId || 'FAKE-PROMOTION',
      promotion_type: source.promotionType || source.promotion_type || input.promotionType || 'DEAL',
      name: source.promotionName || source.promotion_name || input.promotionName || '假执行压测活动',
      site_id: source.siteId || source.site_id || input.siteId || 'MLM',
      child_user_id: source.childUserId || source.child_user_id || account.account_id
    };
    return {
      item: {
        item_id: source.itemId || source.item_id || `FAKE-${job.id}-${index + 1}`,
        status: job.item_status,
        original_price: Number(source.originalPrice || source.original_price || 100),
        price: Number(source.price || 95)
      },
      action: job.action,
      status: 'planned',
      deal_price: Number(source.dealPrice || source.deal_price || source.targetPrice || input.targetPrice || 90),
      account,
      campaign,
      fake_index: index
    };
  });
}

async function runWriteBenchmarkJobLevel({ job, level, rows }) {
  const levelStartedAt = new Date();
  job.current_level = level;
  Object.assign(job.progress, {
    stage: 'running_level',
    current_level: level,
    current_level_items: rows.length,
    current_level_started: 0,
    current_level_finished: 0,
    peak_in_flight: 0,
    success: 0,
    failed: 0,
    interface_failed: 0,
    business_failed: 0,
    skipped: 0
  });
  appendWriteBenchmarkJobEvent(job, {
    type: 'level_start',
    level,
    target_concurrency: level,
    item_count: rows.length,
    sample_offset: job.sample_offset,
    round: job.round,
    attempt: job.attempt
  });
  persistWriteBenchmarkJob(job);
  let refreshCount = 0;
  let activeWrites = 0;
  let maxActiveWrites = 0;
  const items = await mapLimitedWithCap(rows, level, WRITE_BENCHMARK_MAX_CONCURRENCY, async (row, index) => {
    if (job.cancel_requested) {
      const skipped = writeBenchmarkItemBase(job, level, row, index, 'cancelled_before_start');
      skipped.result_status = 'cancelled';
      skipped.is_interface_failure = false;
      skipped.is_business_failure = false;
      job.progress.skipped += 1;
      appendWriteBenchmarkJobEvent(job, { type: 'item_cancelled', item: skipped });
      return {
        item_id: skipped.itemId,
        ok: false,
        status: 'cancelled',
        duration_ms: 0,
        cancelled: true
      };
    }
    const itemStart = writeBenchmarkItemBase(job, level, row, index, 'started');
    itemStart.startedAt = new Date().toISOString();
    job.progress.current_level_started += 1;
    activeWrites += 1;
    maxActiveWrites = Math.max(maxActiveWrites, activeWrites);
    job.progress.peak_in_flight = Math.max(job.progress.peak_in_flight, maxActiveWrites);
    itemStart.in_flight = activeWrites;
    itemStart.peak_in_flight = job.progress.peak_in_flight;
    appendWriteBenchmarkJobEvent(job, { type: 'item_start', item: itemStart });
    persistWriteBenchmarkJob(job);
    const startedMs = Date.now();
    try {
      const response = job.dry_run
        ? await fakeBenchmarkWrite(job, row, index)
        : await executeBenchmarkWriteWithRefresh({
          client: makeWriteClient(row.account, row.campaign),
          setClient: null,
          accountId: row.account.account_id,
          action: job.action,
          campaign: row.campaign,
          row,
          onRefresh: () => { refreshCount += 1; }
        });
      const itemFinish = {
        ...itemStart,
        result_status: 'success',
        finishedAt: new Date().toISOString(),
        duration_ms: Date.now() - startedMs,
        http_status: null,
        error_category: null,
        raw_error_summary: null,
        is_interface_failure: false,
        is_business_failure: false,
        response_summary: responseSummary(response)
      };
      job.progress.success += 1;
      job.progress.current_level_finished += 1;
      activeWrites = Math.max(0, activeWrites - 1);
      itemFinish.in_flight_after = activeWrites;
      itemFinish.peak_in_flight = job.progress.peak_in_flight;
      appendWriteBenchmarkJobEvent(job, { type: 'item_finish', item: itemFinish });
      persistWriteBenchmarkJob(job);
      return {
        item_id: row.item.item_id,
        account_id: String(row.account.account_id),
        promotion_id: row.campaign.promotion_id,
        promotion_type: row.campaign.promotion_type,
        deal_price: row.deal_price,
        ok: true,
        status: 'success',
        duration_ms: itemFinish.duration_ms,
        response_summary: itemFinish.response_summary
      };
    } catch (error) {
      const itemError = writeBenchmarkError(error);
      const isInterfaceFailure = Number(itemError.status) === 429 || Number(itemError.status) >= 500 || isWriteBenchmarkNetworkFailure(itemError);
      const itemFinish = {
        ...itemStart,
        result_status: 'failed',
        finishedAt: new Date().toISOString(),
        duration_ms: Date.now() - startedMs,
        http_status: itemError.status,
        error_category: itemError.code || itemError.message_cn || 'unknown',
        raw_error_summary: itemError.message,
        is_interface_failure: isInterfaceFailure,
        is_business_failure: !isInterfaceFailure,
        error_cn: itemError.message_cn
      };
      job.progress.failed += 1;
      if (isInterfaceFailure) job.progress.interface_failed += 1;
      else job.progress.business_failed += 1;
      job.progress.current_level_finished += 1;
      activeWrites = Math.max(0, activeWrites - 1);
      itemFinish.in_flight_after = activeWrites;
      itemFinish.peak_in_flight = job.progress.peak_in_flight;
      appendWriteBenchmarkJobEvent(job, { type: 'item_finish', item: itemFinish });
      persistWriteBenchmarkJob(job);
      return {
        item_id: row.item.item_id,
        account_id: String(row.account.account_id),
        promotion_id: row.campaign.promotion_id,
        promotion_type: row.campaign.promotion_type,
        deal_price: row.deal_price,
        ok: false,
        status: 'failed',
        duration_ms: itemFinish.duration_ms,
        error: itemError
      };
    }
  });
  job.progress.peak_in_flight = Math.max(job.progress.peak_in_flight, maxActiveWrites, items.maxActive || 0);
  const summary = summarizeWriteBenchmarkItems(items.filter((item) => !item.cancelled));
  const levelResult = {
    level,
    target_concurrency: level,
    item_count: rows.length,
    started_at: levelStartedAt.toISOString(),
    finished_at: new Date().toISOString(),
    duration_ms: Date.now() - levelStartedAt.getTime(),
    write_max_active: items.maxActive || 0,
    summary,
    average_ms: summary.average_ms,
    p95_ms: summary.p95_ms,
    slowest_ms: summary.slowest_ms,
    refresh_count: refreshCount,
    valid: rows.length >= level && (items.maxActive || 0) >= level,
    stable: rows.length >= level
      && (items.maxActive || 0) >= level
      && Number(summary.http_429 || 0) === 0
      && Number(summary.http_5xx || 0) === 0
      && Number(summary.timeout || 0) === 0
      && Number(summary.interface_failure_rate || 0) < 10,
    stopped: Boolean(job.cancel_requested),
    stop_reason: job.cancel_requested ? '用户请求停止压测 job。' : null,
    mercado_errors: summarizeWriteBenchmarkErrors(items)
  };
  appendWriteBenchmarkJobEvent(job, { type: 'level_finish', result: levelResult });
  persistWriteBenchmarkJob(job);
  return levelResult;
}

function writeBenchmarkItemBase(job, level, row, index, status) {
  const campaign = row.campaign || {};
  const account = row.account || {};
  return {
    jobId: job.id,
    level,
    attempt: job.attempt,
    round: job.round,
    sampleOffset: job.sample_offset,
    sampleIndex: index,
    accountId: String(account.account_id || job.account_id || ''),
    storeName: storeIdentityForAccount(account.account_id, account).store_name,
    siteId: campaign.site_id || '',
    siteName: siteDisplayName(campaign.site_id || ''),
    promotionId: campaign.promotion_id || '',
    promotionType: campaign.promotion_type || '',
    promotionName: campaign.name || '',
    itemId: row.item?.item_id || '',
    targetDiscount: Number(job.input?.discountPercent || 0) || null,
    targetPrice: row.deal_price,
    result_status: status
  };
}

async function fakeBenchmarkWrite(job, row, index) {
  const delay = Math.max(0, Math.min(5000, Math.floor(Number(job.input?.fakeDelayMs || 1))));
  if (delay) await sleep(delay);
  const failEvery = Math.max(0, Math.floor(Number(job.input?.fakeFailEvery || 0)));
  if (failEvery && (index + 1) % failEvery === 0) {
    const error = new Error('fake benchmark write failed');
    error.status = Number(job.input?.fakeFailureStatus || 500);
    error.body = { message: 'fake failure for benchmark job test', error: 'fake_error' };
    throw error;
  }
  return { status: 'dry_run_success', item_id: row.item.item_id };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function recheckPersistedWriteBenchmarkJob(job, input = {}) {
  const events = readWriteBenchmarkJobEvents(job.id, Number.MAX_SAFE_INTEGER);
  const finishedItems = events
    .filter((event) => event.type === 'item_finish' && event.item?.itemId)
    .map((event) => event.item);
  const result = {
    item_count: finishedItems.length,
    dry_run: Boolean(job.dry_run),
    counts: { targetMatch: 0, oldPrice: 0, changedOther: 0, notFound: 0, fetchFailed: 0 },
    note: job.dry_run
      ? 'dry-run/fake job 不调用 Mercado，只按落盘 item 返回可恢复回查结构。'
      : '真实 job 回查入口已具备 item list；实际回查只读逻辑由测试线程按账号/活动继续执行。'
  };
  if (job.dry_run || input.dryRun) {
    result.counts.targetMatch = finishedItems.filter((item) => item.result_status === 'success').length;
    result.counts.fetchFailed = finishedItems.filter((item) => item.result_status !== 'success').length;
  }
  appendWriteBenchmarkJobEvent(job, { type: 'recheck_recorded', result });
  persistWriteBenchmarkJob(job);
  return result;
}

async function runWriteConcurrencyBenchmark({ account, input = {} }) {
  const settings = readSettings();
  const levels = normalizeWriteBenchmarkLevels(input.levels || WRITE_BENCHMARK_LEVELS);
  const action = input.action === 'update' ? 'update' : 'enroll';
  const itemStatus = action === 'update' ? 'started' : 'candidate';
  const requiredItems = levels.reduce((sum, level) => sum + Math.max(level, 3), 0);
  const benchmarkAccounts = await resolveWriteBenchmarkAccounts(account, input);
  const samplePool = await collectWriteBenchmarkRows({
    accounts: benchmarkAccounts,
    action,
    itemStatus,
    input,
    settings,
    requiredItems
  });
  const benchmarkRows = samplePool.rows;
  if (!benchmarkRows.length) {
    throw new Error(`未找到价格边界清楚且可用于真实${action === 'update' ? '更新' : '报名'}压测的 ${itemStatus} 商品。`);
  }
  const primaryCampaign = benchmarkRows[0]?.campaign || samplePool.promotions[0] || null;

  const benchmark = {
    benchmark_type: action === 'update' ? 'write_concurrency_real_update' : 'write_concurrency_real_enroll',
    real_write_executed: true,
    note: action === 'update'
      ? '本报告来自小样本真实 Mercado PUT 更新压测；不包含全量更新、报名、取消，也不会自动回滚成功更新。'
      : '本报告来自小样本真实 Mercado POST 报名压测；不包含全量报名、更新、取消，也不会自动撤销成功报名。',
    account_id: benchmarkAccounts.length === 1 ? String(benchmarkAccounts[0].account_id) : 'multiple',
    account_count: benchmarkAccounts.length,
    display_name: benchmarkAccounts.length === 1 ? storeIdentityForAccount(benchmarkAccounts[0].account_id, benchmarkAccounts[0]).store_name : '多个店铺',
    site_id: primaryCampaign?.site_id || null,
    child_user_id: primaryCampaign?.child_user_id || benchmarkAccounts[0]?.account_id || null,
    promotion_id: primaryCampaign?.promotion_id || null,
    promotion_type: primaryCampaign?.promotion_type || null,
    promotion_name: primaryCampaign?.name || '',
    sample_scope: samplePool.scope,
    sample_offset: Math.max(0, Math.floor(Number(input.sampleOffset || input.sample_offset || 0))),
    action,
    item_status: itemStatus,
    levels,
    requested_item_count: requiredItems,
    fetched_item_total: samplePool.fetched_total,
    fetched_item_saved: samplePool.fetched_saved,
    usable_item_count: benchmarkRows.length,
    target_discount_percent: Number(input.discountPercent) || null,
    price_strategy: '按本次压测目标折扣计算活动价；如超出平台 min/max，则跳过该商品，不拿业务错误冒充并发失败。',
    started_at: new Date().toISOString(),
    results: [],
    stopped: false,
    stop_reason: null,
    report_path: null
  };

  let cursor = 0;
  let cumulativeTimeouts = 0;
  let refreshCountTotal = 0;
  for (const level of levels) {
    const sampleSize = Math.max(level, 3);
    const rows = benchmarkRows.slice(cursor, cursor + sampleSize);
    if (rows.length < sampleSize) {
      benchmark.results.push({
        level,
        requested_items: sampleSize,
        item_count: rows.length,
        skipped: true,
        reason: `剩余样本 ${rows.length} 个，不足以验证并发 ${level} 档。`
      });
      benchmark.stopped = true;
      benchmark.stop_reason = `样本商品不足，无法继续验证并发 ${level} 档。`;
      break;
    }
    cursor += sampleSize;
    const levelStartedAt = new Date();
    const executed = await runWriteBenchmarkLevel({
      level,
      rows,
      action,
      account: rows[0]?.account || benchmarkAccounts[0],
      campaign: rows[0]?.campaign || primaryCampaign
    });
    refreshCountTotal += executed.refresh_count || 0;
    cumulativeTimeouts += executed.summary.timeout;
    let recheck;
    try {
      recheck = await recheckWriteBenchmarkRows(rows);
    } catch (error) {
      recheck = {
        counts: { candidate: 0, pending: 0, started: 0, not_found: rows.length },
        error: toChineseError(error)
      };
    }
    const levelResult = {
      level,
      item_count: rows.length,
      started_at: levelStartedAt.toISOString(),
      finished_at: new Date().toISOString(),
      duration_ms: Date.now() - levelStartedAt.getTime(),
      write_max_active: executed.maxActive || 0,
      summary: executed.summary,
      average_ms: executed.summary.average_ms,
      p95_ms: executed.summary.p95_ms,
      slowest_ms: executed.summary.slowest_ms,
      refresh_count: executed.refresh_count || 0,
      mercado_errors: summarizeWriteBenchmarkErrors(executed.items),
      recheck
    };
    benchmark.results.push(levelResult);
    const stopReason = writeBenchmarkStopReason(levelResult, cumulativeTimeouts);
    if (stopReason) {
      benchmark.stopped = true;
      benchmark.stop_reason = stopReason;
      break;
    }
  }

  benchmark.finished_at = new Date().toISOString();
  benchmark.total_refresh_count = refreshCountTotal;
  benchmark.suggested_write_concurrency = suggestWriteConcurrency(benchmark.results) || null;
  benchmark.daily_recommended_write_concurrency = benchmark.suggested_write_concurrency
    ? dailyWriteConcurrencyFromStable(benchmark.suggested_write_concurrency)
    : null;
  benchmark.recommendation = benchmark.suggested_write_concurrency
    ? `本轮最高已验证稳定档位 ${benchmark.suggested_write_concurrency}；日常建议 ${benchmark.daily_recommended_write_concurrency}，保留限流/超时降速保护。`
    : '本次未得到稳定写入并发档位，建议先处理失败原因后再测。';
  // 用户不希望再生成单独的并发压测导出报告；只把压测汇总保存在内部状态文件供界面读取。
  benchmark.report_path = null;
  return benchmark;
}

async function resolveWriteBenchmarkAccounts(defaultAccount, input = {}) {
  if (!input.allAccounts) return [defaultAccount];
  const ids = listAccountsForUi().map((row) => row.account_id).filter(Boolean);
  const uniqueIds = [...new Set(ids.map(String))];
  const accounts = [];
  for (const id of uniqueIds) {
    accounts.push(await ensureUsableAccount(id));
  }
  return accounts.length ? accounts : [defaultAccount];
}

async function collectWriteBenchmarkRows({ accounts, action, itemStatus, input = {}, settings, requiredItems }) {
  const rows = [];
  const rowBuckets = [];
  const promotions = [];
  const scope = [];
  const useRoundRobinSamplePool = action === 'update' && input.roundRobinSamplePool !== false;
  let fetchedTotal = 0;
  let fetchedSaved = 0;
  const perPromotionMax = input.maxItems === 'all' || input.maxCandidateItems === 'all'
    ? 'all'
    : Math.max(50, Math.floor(Number(input.maxItems || input.maxCandidateItems || requiredItems)));
  for (const account of accounts) {
    const campaigns = await resolveWriteBenchmarkPromotions({ account, input, settings });
    for (const campaign of campaigns) {
      if (!useRoundRobinSamplePool && rows.length >= requiredItems) break;
      const promotionType = String(campaign.promotion_type || '').toUpperCase();
      if (['SMART', 'LIGHTNING'].includes(promotionType)) continue;
      if (!['DEAL', 'SELLER_CAMPAIGN'].includes(promotionType)) continue;
      const remaining = requiredItems - rows.length;
      if (action === 'update' && input.useCachedSamplePool !== false) {
        const cachedItems = listItems(account.account_id, campaign.promotion_id, campaign.promotion_type, itemStatus);
        const selectedFromCache = selectBenchmarkRows({
          items: cachedItems,
          action,
          input,
          promotion: campaign,
          settings,
          limit: useRoundRobinSamplePool ? cachedItems.length : remaining
        }).map((row) => ({ ...row, account, campaign }));
        if (selectedFromCache.length > 0) {
          if (useRoundRobinSamplePool) {
            rowBuckets.push(selectedFromCache);
          } else {
            rows.push(...selectedFromCache);
          }
          promotions.push(campaign);
          fetchedTotal += cachedItems.length;
          fetchedSaved += cachedItems.length;
          scope.push({
            account_id: String(account.account_id),
            site_id: campaign.site_id || null,
            child_user_id: campaign.child_user_id || account.account_id,
            promotion_id: campaign.promotion_id,
            promotion_type: promotionType,
            promotion_name: campaign.name || '',
            sample_source: 'cached_started_items',
            fetched_total: cachedItems.length,
            fetched_saved: cachedItems.length,
            selected_rows: selectedFromCache.length
          });
          continue;
        }
      }
      let rowAccount = account;
      let client = makeWriteClient(rowAccount, campaign);
      let fetched;
      try {
        fetched = await client.fetchAllPromotionItems({
          promotionId: campaign.promotion_id,
          promotionType,
          status: itemStatus,
          maxItems: perPromotionMax === 'all' ? 'all' : Math.max(remaining, perPromotionMax)
        });
      } catch (error) {
        if (!isInvalidTokenError(error)) {
          scope.push({
            account_id: String(account.account_id),
            site_id: campaign.site_id || null,
            promotion_id: campaign.promotion_id,
            promotion_type: promotionType,
            promotion_name: campaign.name || '',
            error: toChineseError(error)
          });
          continue;
        }
        try {
          rowAccount = await refreshAccountForWriteRetry(account.account_id);
          client = makeWriteClient(rowAccount, campaign);
          fetched = await client.fetchAllPromotionItems({
            promotionId: campaign.promotion_id,
            promotionType,
            status: itemStatus,
            maxItems: perPromotionMax === 'all' ? 'all' : Math.max(remaining, perPromotionMax)
          });
        } catch (retryError) {
          scope.push({
            account_id: String(account.account_id),
            site_id: campaign.site_id || null,
            promotion_id: campaign.promotion_id,
            promotion_type: promotionType,
            promotion_name: campaign.name || '',
            error: toChineseError(retryError)
          });
          continue;
        }
      }
      fetchedTotal += Number(fetched.total || 0);
      fetchedSaved += Number(fetched.saved || fetched.results?.length || 0);
      const selected = selectBenchmarkRows({
        items: fetched.results,
        action,
        input,
        promotion: campaign,
        settings,
        limit: useRoundRobinSamplePool ? (fetched.results?.length || remaining) : remaining
      }).map((row) => ({ ...row, account: rowAccount, campaign }));
      if (useRoundRobinSamplePool) {
        if (selected.length > 0) rowBuckets.push(selected);
      } else {
        rows.push(...selected);
      }
      promotions.push(campaign);
      scope.push({
        account_id: String(account.account_id),
        site_id: campaign.site_id || null,
        child_user_id: campaign.child_user_id || account.account_id,
        promotion_id: campaign.promotion_id,
        promotion_type: promotionType,
        promotion_name: campaign.name || '',
        fetched_total: fetched.total,
        fetched_saved: fetched.saved,
        selected_rows: selected.length
      });
    }
  }
  if (useRoundRobinSamplePool) {
    const sampleOffset = Math.max(0, Math.floor(Number(input.sampleOffset || input.sample_offset || 0)));
    rows.push(...interleaveBenchmarkRowBuckets(rowBuckets, requiredItems, sampleOffset));
  }
  return {
    rows,
    promotions,
    fetched_total: fetchedTotal,
    fetched_saved: fetchedSaved,
    scope
  };
}

function interleaveBenchmarkRowBuckets(buckets, limit, offset = 0) {
  const output = [];
  const indexes = buckets.map(() => 0);
  const wanted = limit + Math.max(0, offset);
  while (output.length < wanted) {
    let added = 0;
    for (let i = 0; i < buckets.length && output.length < wanted; i += 1) {
      const bucket = buckets[i] || [];
      const index = indexes[i];
      if (index >= bucket.length) continue;
      output.push(bucket[index]);
      indexes[i] += 1;
      added += 1;
    }
    if (!added) break;
  }
  return output.slice(Math.max(0, offset), Math.max(0, offset) + limit);
}

async function resolveWriteBenchmarkPromotions({ account, input = {}, settings }) {
  if (input.promotionId) return [await resolveWriteBenchmarkPromotion({ account, input, settings })];
  if (input.refreshPromotionsForBenchmark === true || input.useCachedSamplePool === false) {
    await preparePromotionsForExecution({
      account,
      filters: input.filters || settings.defaultFilters || {},
      settings,
      request: { readConcurrency: settings.readConcurrency }
    });
  }
  return listCampaignsFiltered(account.account_id, input.filters || settings.defaultFilters || {})
    .filter((campaign) => ['DEAL', 'SELLER_CAMPAIGN'].includes(String(campaign.promotion_type || '').toUpperCase()));
}

async function resolveWriteBenchmarkPromotion({ account, input = {}, settings }) {
  if (input.promotionId) {
    const promotionType = String(input.promotionType || 'DEAL').toUpperCase();
    let campaign = getCampaign(account.account_id, input.promotionId, promotionType);
    if (!campaign) {
      await preparePromotionsForExecution({ account, filters: { siteIds: input.siteId ? [input.siteId] : [] }, settings, request: { readConcurrency: settings.readConcurrency } });
      campaign = getCampaign(account.account_id, input.promotionId, promotionType);
    }
    if (!campaign) {
      throw new Error(`未找到压测活动 ${input.promotionId} / ${promotionType}。`);
    }
    return campaign;
  }
  await preparePromotionsForExecution({ account, filters: input.filters || settings.defaultFilters || {}, settings, request: { readConcurrency: settings.readConcurrency } });
  const campaigns = listCampaignsFiltered(account.account_id, input.filters || settings.defaultFilters || {})
    .filter((campaign) => ['DEAL', 'SELLER_CAMPAIGN'].includes(String(campaign.promotion_type || '').toUpperCase()));
  const withCandidates = campaigns.find((campaign) => listItems(account.account_id, campaign.promotion_id, campaign.promotion_type, 'candidate').length > 0);
  const selected = withCandidates || campaigns[0];
  if (!selected) throw new Error('未找到可用于写入并发压测的 DEAL / SELLER_CAMPAIGN 活动。');
  return selected;
}

function selectBenchmarkRows({ items, action = 'enroll', input = {}, promotion, settings, limit }) {
  const rows = [];
  const seen = new Set();
  for (const source of items || []) {
    const item = normalizeItem(source);
    const expectedStatus = action === 'update' ? 'started' : 'candidate';
    if (!item.item_id || seen.has(item.item_id) || item.status !== expectedStatus) continue;
    const dealPrice = benchmarkDealPrice(item, promotion, settings, input);
    if (!Number.isFinite(dealPrice)) continue;
    if (action === 'update' && item.price !== null && roundMoney(item.price) === roundMoney(dealPrice)) continue;
    rows.push({
      item,
      action,
      status: 'planned',
      deal_price: dealPrice,
      reason: `真实写入并发${action === 'update' ? '更新' : '报名'}压测样本`
    });
    seen.add(item.item_id);
    if (rows.length >= limit) break;
  }
  return rows;
}

function targetBenchmarkDiscountPercent(promotion, settings, input = {}) {
  const promotionType = String(promotion?.promotion_type || '').toUpperCase();
  const explicit = Number(input.discountPercent);
  if (Number.isFinite(explicit) && explicit > 0 && explicit < 100) return explicit;
  return promotionType === 'SELLER_CAMPAIGN'
    ? Number(settings.sellerDefaultDiscount || 5)
    : Number(settings.officialDefaultDiscount || 6);
}

function benchmarkDealPrice(item, promotion, settings, input = {}) {
  const discountPercent = targetBenchmarkDiscountPercent(promotion, settings, input);
  const base = item.original_price ?? item.price;
  if (!Number.isFinite(base) || base <= 0) return null;
  let dealPrice = roundMoney(base * (100 - discountPercent) / 100);
  if (validateDealPrice(item, dealPrice)) {
    if (item.max_discounted_price !== null) dealPrice = item.max_discounted_price;
    if (item.min_discounted_price !== null && dealPrice < item.min_discounted_price) dealPrice = item.min_discounted_price;
    dealPrice = roundMoney(dealPrice);
  }
  return validateDealPrice(item, dealPrice) ? null : dealPrice;
}

async function runWriteBenchmarkLevel({ level, rows, action, account, campaign, getClient, setClient }) {
  let refreshCount = 0;
  const results = await mapLimitedWithCap(rows, level, WRITE_BENCHMARK_MAX_CONCURRENCY, async (row) => {
    const startedAt = Date.now();
    const rowAccount = row.account || account;
    const rowCampaign = row.campaign || campaign;
    const client = getClient ? getClient(row) : makeWriteClient(rowAccount, rowCampaign);
    try {
      const response = await executeBenchmarkWriteWithRefresh({
        client,
        setClient: setClient ? (nextClient) => setClient(nextClient, row) : null,
        accountId: rowAccount.account_id,
        action,
        campaign: rowCampaign,
        row,
        onRefresh: () => { refreshCount += 1; }
      });
      return {
        item_id: row.item.item_id,
        account_id: String(rowAccount.account_id),
        promotion_id: rowCampaign.promotion_id,
        promotion_type: rowCampaign.promotion_type,
        deal_price: row.deal_price,
        ok: true,
        status: 'success',
        duration_ms: Date.now() - startedAt,
        response_summary: responseSummary(response)
      };
    } catch (error) {
      return {
        item_id: row.item.item_id,
        account_id: String(rowAccount.account_id),
        promotion_id: rowCampaign.promotion_id,
        promotion_type: rowCampaign.promotion_type,
        deal_price: row.deal_price,
        ok: false,
        status: 'failed',
        duration_ms: Date.now() - startedAt,
        error: writeBenchmarkError(error)
      };
    }
  });
  const summary = summarizeWriteBenchmarkItems(results);
  return {
    maxActive: results.maxActive || 0,
    refresh_count: refreshCount,
    summary,
    items: results
  };
}

async function recheckWriteBenchmarkRows(rows) {
  const groups = new Map();
  for (const row of rows || []) {
    const account = row.account;
    const campaign = row.campaign;
    if (!account || !campaign) continue;
    const key = promotionKey({ ...campaign, account_id: account.account_id });
    if (!groups.has(key)) groups.set(key, { account, campaign, itemIds: [] });
    groups.get(key).itemIds.push(row.item.item_id);
  }
  const counts = { candidate: 0, pending: 0, started: 0, not_found: 0 };
  const details = [];
  for (const group of groups.values()) {
    const client = makeWriteClient(group.account, group.campaign);
    const recheck = await recheckWriteBenchmarkItems({ client, campaign: group.campaign, itemIds: group.itemIds });
    for (const [name, value] of Object.entries(recheck.counts || {})) {
      counts[name] = (counts[name] || 0) + Number(value || 0);
    }
    details.push({
      account_id: String(group.account.account_id),
      site_id: group.campaign.site_id || null,
      promotion_id: group.campaign.promotion_id,
      promotion_type: group.campaign.promotion_type,
      promotion_name: group.campaign.name || '',
      counts: recheck.counts
    });
  }
  return { counts, groups: details };
}

async function executeBenchmarkWriteWithRefresh({ client, setClient, accountId, action, campaign, row, onRefresh }) {
  try {
    return await executeOnePlanned(client, action, campaign, row, {
      itemId: row.item.item_id,
      promotionId: campaign.promotion_id,
      promotionType: campaign.promotion_type,
      dealPrice: row.deal_price
    });
  } catch (error) {
    if (!isInvalidTokenError(error)) throw error;
    onRefresh?.();
    const refreshedAccount = await refreshAccountForWriteRetry(accountId);
    const refreshedClient = makeWriteClient(refreshedAccount, campaign);
    setClient?.(refreshedClient);
    return executeOnePlanned(refreshedClient, action, campaign, row, {
      itemId: row.item.item_id,
      promotionId: campaign.promotion_id,
      promotionType: campaign.promotion_type,
      dealPrice: row.deal_price
    });
  }
}

function summarizeWriteBenchmarkItems(items) {
  const durations = items.map((item) => Number(item.duration_ms || 0)).sort((a, b) => a - b);
  const failed = items.filter((item) => !item.ok);
  return {
    total_requests: items.length,
    success: items.length - failed.length,
    failed: failed.length,
    skipped: 0,
    http_429: failed.filter((item) => Number(item.error?.status) === 429 || /429|限流/i.test(item.error?.message_cn || '')).length,
    http_5xx: failed.filter((item) => Number(item.error?.status) >= 500).length,
    timeout: failed.filter((item) => isWriteBenchmarkNetworkFailure(item.error)).length,
    http_401_refresh: 0,
    failure_rate: items.length ? Math.round((failed.length / items.length) * 10000) / 100 : 0,
    interface_failure_rate: items.length ? Math.round(((failed.filter((item) => Number(item.error?.status) === 429 || Number(item.error?.status) >= 500 || isWriteBenchmarkNetworkFailure(item.error)).length) / items.length) * 10000) / 100 : 0,
    average_ms: durations.length ? Math.round(durations.reduce((sum, value) => sum + value, 0) / durations.length) : 0,
    p95_ms: percentile(durations, 0.95),
    slowest_ms: durations.at(-1) || 0
  };
}

function isWriteBenchmarkNetworkFailure(error) {
  if (!error?.status) return true;
  const text = `${error?.message_cn || ''} ${error?.message || ''} ${error?.code || ''}`;
  return Number(error?.status) === 504 || /超时|timeout|fetch failed|network|socket|ECONNRESET|UND_ERR/i.test(text);
}

function writeBenchmarkError(error) {
  const body = error?.body || error?.details || null;
  return {
    status: error?.status || null,
    code: body?.error || body?.code || body?.message_code || null,
    message: sanitizeMercadoMessage(body?.message || error?.message || String(error)),
    message_cn: toChineseError(error)
  };
}

function sanitizeMercadoMessage(message) {
  return String(message || '')
    .replace(/Bearer\s+[A-Za-z0-9._-]+/g, 'Bearer [hidden]')
    .replace(/access_token[=:]\s*[^,\s"}]+/gi, 'access_token=[hidden]')
    .replace(/refresh_token[=:]\s*[^,\s"}]+/gi, 'refresh_token=[hidden]')
    .slice(0, 300);
}

function responseSummary(response) {
  if (!response || typeof response !== 'object') return null;
  if (Object.hasOwn(response, 'http_status') || Object.hasOwn(response, 'body')) {
    const body = response.body && typeof response.body === 'object' ? response.body : null;
    return {
      http_status: response.http_status || null,
      headers: response.headers || null,
      body: sanitizeResponseBody(body || response.body || null),
      raw_text_length: response.raw_text_length ?? null,
      status: body?.status || body?.message || null,
      id: body?.id || body?.item_id || null
    };
  }
  return {
    status: response.status || response.item_status || response.message || null,
    id: response.id || response.item_id || null
  };
}

function sanitizeResponseBody(body) {
  if (!body) return null;
  try {
    const text = JSON.stringify(body);
    return JSON.parse(sanitizeMercadoMessage(text));
  } catch {
    return sanitizeMercadoMessage(String(body));
  }
}

function summarizeWriteBenchmarkErrors(items) {
  const map = new Map();
  for (const item of items || []) {
    if (item.ok) continue;
    const key = `${item.error?.status || '-'}|${item.error?.code || '-'}|${item.error?.message_cn || item.error?.message || '未知失败'}`;
    if (!map.has(key)) {
      map.set(key, {
        status: item.error?.status || null,
        code: item.error?.code || null,
        message: item.error?.message || null,
        message_cn: item.error?.message_cn || '未知失败',
        count: 0
      });
    }
    map.get(key).count += 1;
  }
  return [...map.values()].sort((a, b) => b.count - a.count);
}

function writeBenchmarkStopReason(levelResult, cumulativeTimeouts) {
  const summary = levelResult.summary || {};
  if (summary.http_429 > 0) return `并发 ${levelResult.level} 出现 429 限流，停止后续档位。`;
  if (cumulativeTimeouts >= 2) return `累计 504/超时达到 ${cumulativeTimeouts} 次，停止后续档位。`;
  if (summary.interface_failure_rate >= 10) return `并发 ${levelResult.level} 接口类失败率 ${summary.interface_failure_rate}% >= 10%，停止后续档位。`;
  return null;
}

function suggestWriteConcurrency(results) {
  let suggested = 0;
  for (const result of results || []) {
    if (result.skipped) break;
    const summary = result.summary || {};
    const level = Number(result.level || 0);
    const sampleEnough = Number(result.item_count || 0) >= level;
    const peakReached = Number(result.write_max_active || 0) >= level;
    const stable = Number(summary.success || 0) > 0
      && sampleEnough
      && peakReached
      && Number(summary.http_429 || 0) === 0
      && Number(summary.http_5xx || 0) === 0
      && Number(summary.timeout || 0) === 0
      && Number(summary.interface_failure_rate || 0) < 10;
    if (!stable) break;
    suggested = level || suggested;
  }
  return suggested;
}

function dailyWriteConcurrencyFromStable(stable) {
  const n = Math.max(1, Math.min(Number(stable) || 1, WRITE_BENCHMARK_MAX_CONCURRENCY));
  if (n <= 2) return n;
  return Math.max(1, Math.min(WRITE_BENCHMARK_MAX_CONCURRENCY, Math.floor(n * 0.8)));
}

async function recheckWriteBenchmarkItems({ client, campaign, itemIds }) {
  const wanted = new Set(itemIds.map(String));
  const perItem = new Map([...wanted].map((itemId) => [itemId, { item_id: itemId, status: 'not_found' }]));
  const statuses = ['candidate', 'pending', 'started'];
  const counts = { candidate: 0, pending: 0, started: 0, not_found: wanted.size };
  for (const status of statuses) {
    const result = await client.fetchAllPromotionItems({
      promotionId: campaign.promotion_id,
      promotionType: campaign.promotion_type,
      status,
      maxItems: 'all'
    });
    for (const item of result.results || []) {
      const itemId = String(item.item_id || item.id || '');
      if (!wanted.has(itemId)) continue;
      perItem.set(itemId, { item_id: itemId, status });
    }
  }
  counts.candidate = 0;
  counts.pending = 0;
  counts.started = 0;
  counts.not_found = 0;
  for (const item of perItem.values()) {
    counts[item.status] = (counts[item.status] || 0) + 1;
  }
  return {
    counts,
    items: [...perItem.values()]
  };
}

function normalizeWriteBenchmarkLevels(value) {
  const levels = (Array.isArray(value) ? value : String(value || '').split(','))
    .map((item) => normalizeConcurrencyWithCap(item, 1, WRITE_BENCHMARK_MAX_CONCURRENCY))
    .filter(Boolean);
  return [...new Set(levels)].sort((a, b) => a - b);
}

function readConcurrencyBenchmarkResults() {
  const withLatest = (value) => ({ ...value, write_latest_status: LATEST_WRITE_BENCHMARK_STATUS });
  try {
    return withLatest(JSON.parse(fs.readFileSync(CONCURRENCY_BENCHMARK_PATH, 'utf8')));
  } catch {
    return withLatest({ read: null, write_plan: null, history: [] });
  }
}

function saveConcurrencyBenchmarkResult(kind, result) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  const current = readConcurrencyBenchmarkResults();
  current[kind] = compactStoredBenchmarkResult(kind, result);
  current.history = Array.isArray(current.history) ? current.history : [];
  current.history.unshift({ kind, at: new Date().toISOString(), summary: summarizeStoredBenchmark(kind, result) });
  current.history = current.history.slice(0, 20);
  fs.writeFileSync(CONCURRENCY_BENCHMARK_PATH, JSON.stringify(current, null, 2), 'utf8');
}

function compactStoredBenchmarkResult(kind, result) {
  if (kind !== 'write' || !result) return result;
  return {
    ...result,
    report_path: null,
    results: (result.results || []).map((level) => ({
      level: level.level,
      requested_items: level.requested_items,
      item_count: level.item_count,
      skipped: level.skipped,
      reason: level.reason,
      started_at: level.started_at,
      finished_at: level.finished_at,
      duration_ms: level.duration_ms,
      write_max_active: level.write_max_active,
      summary: level.summary,
      average_ms: level.average_ms,
      p95_ms: level.p95_ms,
      slowest_ms: level.slowest_ms,
      refresh_count: level.refresh_count,
      mercado_errors: level.mercado_errors,
      recheck: level.recheck?.counts ? { counts: level.recheck.counts } : level.recheck
    }))
  };
}

function summarizeStoredBenchmark(kind, result) {
  if (kind === 'read') {
    return {
      suggested_read_concurrency: result.suggested_read_concurrency,
      levels: result.levels,
      finished_at: result.finished_at
    };
  }
  if (kind === 'write') {
    return {
      suggested_write_concurrency: result.suggested_write_concurrency,
      daily_recommended_write_concurrency: result.daily_recommended_write_concurrency,
      tested_levels: (result.results || []).filter((level) => !level.skipped).map((level) => level.level),
      promotion_name: result.promotion_name,
      finished_at: result.finished_at
    };
  }
  return {
    enabled: false,
    levels: result.levels,
    promotion_name: result.promotion_name,
    sample_items: result.sample_items?.length || 0
  };
}

async function discoverAndSaveMarketplaceSites(account, { signal = null, readScheduler = null } = {}) {
  const client = new MercadoLibreClient({
    accessToken: account.accessToken, userId: account.account_id, callerId: account.account_id,
    readScheduler, readAccountId: account.account_id,
  });
  const marketplaceUsers = extractMarketplaceUsers(await client.getMarketplaceUsers(account.account_id, { signal }));
  saveMarketplaceSites(account.account_id, marketplaceUsers);
  return marketplaceUsers;
}

function isMarketplaceCampaign(account, campaign) {
  return account.site_id === 'CBT' || Boolean(campaign?.child_user_id && String(campaign.child_user_id) !== String(account.account_id));
}

function createBatchFetchJob({ accountId, filters, itemStatus, fetchMode, readConcurrency }) {
  const id = String(nextBatchFetchJobId++);
  const job = {
    id,
    status: 'queued',
    accountId: String(accountId),
    filters,
    itemStatus,
    fetchMode,
    sample_only: fetchMode !== 'full',
    readConcurrency,
    started_at: new Date().toISOString(),
    finished_at: null,
    progress: { total_promotions: 0, completed_promotions: 0, failed_promotions: 0 },
    rows: [],
    error: null
  };
  batchFetchJobs.set(id, job);
  return job;
}

function createInventoryFallbackJob({ accountId, filters, listingStatus, readConcurrency, detailConcurrency, maxScanItems = 'all' }) {
  const id = String(nextInventoryFallbackJobId++);
  const job = {
    id,
    status: 'queued',
    accountId: String(accountId),
    filters,
    listingStatus,
    readConcurrency,
    detailConcurrency,
    maxScanItems,
    started_at: new Date().toISOString(),
    finished_at: null,
    progress: { total_promotions: 0, completed_promotions: 0, failed_promotions: 0 },
    rows: [],
    error: null
  };
  inventoryFallbackJobs.set(id, job);
  return job;
}

async function runInventoryFallbackJob(jobId, { accountId, filters, listingStatus, readConcurrency, detailConcurrency, sellerDiscountPercent, maxScanItems = 'all' }) {
  const job = inventoryFallbackJobs.get(jobId);
  if (!job) return;
  job.status = 'running';
  try {
    const account = await ensureUsableAccount(accountId);
    const promotions = listOperatingCampaignsFiltered(account.account_id, filters || {}, readSettings()).filter(isSellerCampaign);
    job.progress.total_promotions = promotions.length;
    const rows = await mapLimited(promotions, readConcurrency, async (campaign) => {
      const row = await scanAndSaveInventoryFallbackForCampaign({
        account,
        campaign,
        listingStatus,
        detailConcurrency,
        sellerDiscountPercent,
        maxScanItems
      });
      job.rows.push(row);
      job.progress.completed_promotions += 1;
      if (row.error || row.blocked) job.progress.failed_promotions += 1;
      return row;
    });
    job.rows = rows;
    job.status = 'completed';
    job.finished_at = new Date().toISOString();
  } catch (error) {
    job.status = 'failed';
    job.error = toChineseError(error);
    job.finished_at = new Date().toISOString();
  }
}

async function scanAndSaveInventoryFallbackForCampaign({
  account,
  campaign,
  listingStatus,
  detailConcurrency,
  sellerDiscountPercent,
  maxScanItems = 'all',
  signal = null,
  checkpoint = null,
  readScheduler = null,
  operationReadCache = null,
}) {
  try {
    checkpoint?.();
    const targetUserId = campaign.child_user_id || account.account_id;
    const client = new MercadoLibreClient({
      accessToken: account.accessToken,
      userId: targetUserId,
      callerId: targetUserId,
      marketplace: isMarketplaceCampaign(account, campaign),
      readScheduler,
      readAccountId: account.account_id,
    });
    const readStatus = async (status) => {
      const key = operationActivityReadKey(account.account_id, campaign, status);
      if (operationReadCache?.has(key)) {
        const rows = listItems(account.account_id, campaign.promotion_id, campaign.promotion_type, status);
        return { results: rows, total: rows.length, saved: rows.length, detailStatus: 'operation_cache', isFullFetch: true };
      }
      const result = await client.fetchAllPromotionItems({
        promotionId: campaign.promotion_id,
        promotionType: campaign.promotion_type,
        status,
        maxItems: 'all',
        signal,
      });
      operationReadCache?.set(key, batchFetchRow(campaign, status, {
        total: result.total, saved: result.saved, platform_total: result.total, saved_count: result.saved,
        detail_status: result.detailStatus, is_full_fetch: result.isFullFetch, blocked: result.blocked,
      }));
      return result;
    };
    const [started, pending] = await Promise.all([
      readStatus('started'),
      readStatus('pending'),
    ]);
    checkpoint?.();
    saveItems(account.account_id, campaign.promotion_id, campaign.promotion_type, started.results, {
      childUserId: campaign.child_user_id,
      siteId: campaign.site_id,
      logisticType: campaign.logistic_type,
      replaceStatus: 'started',
      itemStatus: 'started'
    });
    saveItems(account.account_id, campaign.promotion_id, campaign.promotion_type, pending.results, {
      childUserId: campaign.child_user_id,
      siteId: campaign.site_id,
      logisticType: campaign.logistic_type,
      replaceStatus: 'pending',
      itemStatus: 'pending'
    });
    const existingCandidateItems = listItems(account.account_id, campaign.promotion_id, campaign.promotion_type, 'candidate');
    const fallback = await buildSellerCampaignInventoryFallback({
      client,
      promotion: campaign,
      startedItems: started.results,
      pendingItems: pending.results,
      existingCandidateItems,
      listingStatus,
      detailConcurrency,
      discountPercent: sellerDiscountPercent,
      maxScanItems,
      signal,
      readScheduler,
      accountId: account.account_id,
    });
    checkpoint?.();
    deleteItemsBySource(account.account_id, campaign.promotion_id, campaign.promotion_type, 'candidate', INVENTORY_FALLBACK_SOURCE);
    saveItems(account.account_id, campaign.promotion_id, campaign.promotion_type, fallback.fallback_rows, {
      childUserId: campaign.child_user_id,
      siteId: campaign.site_id,
      logisticType: campaign.logistic_type,
      itemStatus: 'candidate',
      source: INVENTORY_FALLBACK_SOURCE
    });
    const candidateAfter = listItems(account.account_id, campaign.promotion_id, campaign.promotion_type, 'candidate');
    const originalCandidateState = getItemFetchState(account.account_id, campaign.promotion_id, campaign.promotion_type, 'candidate');
    const raw = {
      ...fallback.raw,
      scan_total: fallback.scan_total,
      scan_saved: fallback.scan_saved,
      scan_is_full_fetch: fallback.scan_is_full_fetch,
      listing_status: listingStatus || 'all',
      excluded_started_pending: fallback.excluded_started_pending,
      existing_candidate_count: fallback.existing_candidate_count,
      detail_targets: fallback.detail_targets,
      detail_success: fallback.detail_success,
      detail_failed: fallback.detail_failed,
      added_count: fallback.added_count,
      combined_candidate_count: candidateAfter.length,
      original_candidate_detail_status: originalCandidateState?.detail_status || null,
      original_candidate_platform_total: originalCandidateState?.platform_total ?? null,
      original_candidate_saved_count: originalCandidateState?.saved_count ?? null
    };
    saveItemFetchState({
      accountId: account.account_id,
      promotionId: campaign.promotion_id,
      promotionType: campaign.promotion_type,
      itemStatus: INVENTORY_FALLBACK_ITEM_STATUS,
      platformTotal: candidateAfter.length,
      savedCount: candidateAfter.length,
      detailStatus: fallback.scan_is_full_fetch ? INVENTORY_FALLBACK_READY_STATUS : 'inventory_scan_fallback_partial',
      warning: '自建活动 candidate 接口不完整时启用 marketplace 库存扫描兜底；资格以 Mercado 报名接口逐商品返回为准。',
      raw
    });
    if (fallback.scan_is_full_fetch) {
      recordActivityItemsCalibration({
        accountId: account.account_id,
        siteId: campaign.site_id,
        promotionId: campaign.promotion_id,
        promotionType: campaign.promotion_type,
      });
    }
    return batchFetchRow(campaign, 'candidate', {
      source: INVENTORY_FALLBACK_SOURCE,
      listing_status: listingStatus || 'all',
      total: candidateAfter.length,
      saved: candidateAfter.length,
      platform_total: candidateAfter.length,
      saved_count: candidateAfter.length,
      scan_total: fallback.scan_total,
      scan_saved: fallback.scan_saved,
      added_count: fallback.added_count,
      detail_success: fallback.detail_success,
      detail_failed: fallback.detail_failed,
      excluded_started_pending: fallback.excluded_started_pending,
      existing_candidate_count: fallback.existing_candidate_count,
      detail_status: fallback.scan_is_full_fetch ? INVENTORY_FALLBACK_READY_STATUS : 'inventory_scan_fallback_partial',
      is_full_fetch: true,
      sample_only: false,
      blocked: false,
      note: fallback.note
    });
  } catch (error) {
    const errorCn = toChineseError(error);
    saveItemFetchState({
      accountId: account.account_id,
      promotionId: campaign.promotion_id,
      promotionType: campaign.promotion_type,
      itemStatus: INVENTORY_FALLBACK_ITEM_STATUS,
      platformTotal: 0,
      savedCount: 0,
      detailStatus: 'inventory_scan_fallback_error',
      warning: errorCn,
      raw: { source: INVENTORY_FALLBACK_SOURCE, message: error?.message, status: error?.status }
    });
    return batchFetchRow(campaign, 'candidate', {
      source: INVENTORY_FALLBACK_SOURCE,
      total: 0,
      saved: 0,
      platform_total: 0,
      saved_count: 0,
      is_full_fetch: false,
      sample_only: true,
      detail_status: 'inventory_scan_fallback_error',
      blocked: true,
      note: errorCn,
      error: errorCn
    });
  }
}

async function runBatchFetchJob(jobId, { accountId, filters, itemStatus, fetchMode, readConcurrency, maxItems }) {
  const job = batchFetchJobs.get(jobId);
  if (!job) return;
  job.status = 'running';
  try {
    const account = await ensureUsableAccount(accountId);
    const promotions = listOperatingCampaignsFiltered(account.account_id, filters || {}, readSettings());
    job.progress.total_promotions = promotions.length;
    const rows = await mapLimited(promotions, readConcurrency, async (campaign) => {
      const row = await fetchAndSavePromotionItemsForCampaign({ account, campaign, status: itemStatus, maxItems, fetchMode });
      job.rows.push(row);
      job.progress.completed_promotions += 1;
      if (row.error || row.detail_status === 'error') job.progress.failed_promotions += 1;
      return row;
    });
    job.rows = rows;
    job.status = 'completed';
    job.finished_at = new Date().toISOString();
  } catch (error) {
    job.status = 'failed';
    job.error = toChineseError(error);
    job.finished_at = new Date().toISOString();
  }
}

async function fetchAndSavePromotionItemsForCampaign({ account, campaign, status, maxItems, fetchMode = 'sample', signal = null, checkpoint = null, readScheduler = null }) {
  try {
    checkpoint?.();
    const targetUserId = campaign.child_user_id || account.account_id;
    const client = new MercadoLibreClient({
      accessToken: account.accessToken,
      userId: targetUserId,
      callerId: targetUserId,
      marketplace: isMarketplaceCampaign(account, campaign),
      readScheduler,
      readAccountId: account.account_id,
    });
    const result = await client.fetchAllPromotionItems({
      promotionId: campaign.promotion_id,
      promotionType: campaign.promotion_type,
      status,
      maxItems,
      signal,
    });
    checkpoint?.();
    const existingState = getItemFetchState(account.account_id, campaign.promotion_id, campaign.promotion_type, status);
    const preserveExisting = shouldPreserveExistingFetchState({ fetchMode, existingState, nextSaved: result.saved, nextTotal: result.total });
    if (!preserveExisting) {
      saveItems(account.account_id, campaign.promotion_id, campaign.promotion_type, result.results, {
        childUserId: campaign.child_user_id,
        siteId: campaign.site_id,
        logisticType: campaign.logistic_type,
        replaceStatus: status,
        itemStatus: status
      });
      saveItemFetchState({
        accountId: account.account_id,
        promotionId: campaign.promotion_id,
        promotionType: campaign.promotion_type,
        itemStatus: status,
        platformTotal: result.total,
        savedCount: result.saved,
        detailStatus: result.detailStatus,
        warning: result.warning,
        raw: result.rawSummary
      });
      if (fetchMode === 'full' && result.isFullFetch && !result.blocked) {
        recordActivityItemsCalibration({
          accountId: account.account_id,
          siteId: campaign.site_id,
          promotionId: campaign.promotion_id,
          promotionType: campaign.promotion_type,
        });
      }
    }
    const savedCount = preserveExisting ? Number(existingState.saved_count || 0) : result.saved;
    const platformTotal = preserveExisting ? Number(existingState.platform_total ?? result.total ?? savedCount) : result.total;
    const detailStatus = preserveExisting ? existingState.detail_status : result.detailStatus;
    const note = preserveExisting
      ? `样本读取返回 ${result.saved}/${result.total}，已保留本地较完整缓存 ${savedCount}/${platformTotal}。`
      : result.warning || (result.detailStatus === 'empty' ? '无商品' : '已保存');
    return batchFetchRow(campaign, status, {
      total: platformTotal,
      saved: savedCount,
      platform_total: platformTotal,
      saved_count: savedCount,
      is_full_fetch: preserveExisting ? isStateFull(existingState) : result.isFullFetch,
      sample_only: preserveExisting ? false : result.sampleOnly || fetchMode !== 'full',
      fetch_mode: fetchMode,
      detail_status: detailStatus,
      blocked: preserveExisting ? false : result.blocked,
      ...fetchStatsFromRaw(result.rawSummary),
      note
    });
  } catch (error) {
    if (signal?.aborted || String(error?.code || '').startsWith('COMMIT_') || String(error?.code || '').startsWith('SUBMISSION_')) throw error;
    const errorCn = toChineseError(error);
    recordActivityItemsCalibration({
      accountId: account.account_id,
      siteId: campaign.site_id,
      promotionId: campaign.promotion_id,
      promotionType: campaign.promotion_type,
      error: errorCn,
    });
    saveItemFetchState({
      accountId: account.account_id,
      promotionId: campaign.promotion_id,
      promotionType: campaign.promotion_type,
      itemStatus: status,
      platformTotal: 0,
      savedCount: 0,
      detailStatus: 'error',
      warning: errorCn,
      raw: { message: error?.message, status: error?.status }
    });
    return batchFetchRow(campaign, status, {
      total: 0,
      saved: 0,
      platform_total: 0,
      saved_count: 0,
      is_full_fetch: false,
      sample_only: fetchMode !== 'full',
      fetch_mode: fetchMode,
      detail_status: 'error',
      blocked: true,
      note: errorCn,
      error: errorCn
    });
  }
}

function shouldPreserveExistingFetchState({ fetchMode, existingState, nextSaved, nextTotal }) {
  if (fetchMode !== 'sample' || !existingState) return false;
  const existingSaved = Number(existingState.saved_count || 0);
  const existingTotal = Number(existingState.platform_total ?? 0);
  if (existingSaved <= Number(nextSaved || 0)) return false;
  return isStateFull(existingState) || existingSaved >= existingTotal || existingState.detail_status === 'ok';
}

function isStateFull(state) {
  if (!state) return false;
  const saved = Number(state.saved_count || 0);
  const total = Number(state.platform_total ?? saved);
  return total === 0 || saved >= total || state.detail_status === 'ok';
}

function batchFetchRow(campaign, status, details) {
  return {
    site_id: campaign.site_id,
    child_user_id: campaign.child_user_id,
    promotion_id: campaign.promotion_id,
    promotion_type: campaign.promotion_type,
    name: campaign.name,
    status,
    ...details
  };
}

function fetchStatsFromRaw(raw = {}) {
  return {
    pages_read: raw.pages_read ?? null,
    empty_page_count: raw.empty_page_count ?? null,
    consecutive_empty_pages: raw.consecutive_empty_pages ?? null,
    unique_count: raw.unique_count ?? null,
    duplicate_count: raw.duplicate_count ?? null,
    last_search_after: raw.last_search_after ?? null,
    stop_reason: raw.stop_reason ?? null
  };
}

async function ensureUsableAccount(accountId) {
  const standalone = getStandaloneSecrets();
  if (standalone && String(standalone.account_id) === String(accountId)) {
    return ensureStandaloneUsable(standalone);
  }
  return ensureFreshAccount(accountId);
}

async function defaultAccountId() {
  const standalone = getStandaloneSecrets();
  if (standalone?.account_id) return String(standalone.account_id);
  const account = listStoredAccounts()[0];
  if (account?.account_id) return String(account.account_id);
  throw new Error('未找到可用于压测的授权账号');
}

async function ensureStandaloneUsable(account) {
  let current = account;
  try {
    const profile = await new MercadoLibreClient({ accessToken: current.accessToken, userId: current.account_id }).getMe();
    const record = accountProfileRecord({ accountId: current.account_id, provider: current.provider, profile, source: 'users_me' });
    if (record) saveAccountProfile(record);
    return { ...current, display_name: profile.nickname || current.display_name, site_id: profile.site_id || current.site_id, profile };
  } catch (error) {
    if (!isInvalidTokenError(error)) throw error;
    refreshStandaloneToken({ force: true });
    current = getStandaloneSecrets();
    if (!current) throw new Error('standalone token refresh 后仍无法读取 token');
    const profile = await new MercadoLibreClient({ accessToken: current.accessToken, userId: current.account_id }).getMe();
    const record = accountProfileRecord({ accountId: current.account_id, provider: current.provider, profile, source: 'users_me' });
    if (record) saveAccountProfile(record);
    return { ...current, display_name: profile.nickname || current.display_name, site_id: profile.site_id || current.site_id, profile };
  }
}

function isInvalidTokenError(error) {
  const raw = JSON.stringify(error?.body || error?.message || '');
  return error instanceof ApiError && error.status === 401 || /invalid.*access.*token|invalid_token|expired/i.test(raw);
}

async function ensureFreshAccount(accountId, { force = false } = {}) {
  const account = getAccountSecrets(accountId);
  if (!account) throw new Error('未找到授权账号');
  if (!force && (!account.expires_at || new Date(account.expires_at).getTime() > Date.now() + 5 * 60 * 1000)) {
    return account;
  }
  if (!account.refreshToken) throw new Error('token 已过期且没有 refresh token，请重新授权');
  const client = new MercadoLibreClient();
  const token = await client.refreshToken({
    clientId: account.client_id,
    clientSecret: account.clientSecret,
    refreshToken: account.refreshToken
  });
  updateAccountToken(account.account_id, token);
  return getAccountSecrets(account.account_id);
}

function serveStatic(req, res) {
  const url = new URL(req.url, `http://${HOST}:${PORT}`);
  const rel = url.pathname === '/' ? '/index.html' : url.pathname;
  const filePath = path.normalize(path.join(PUBLIC_DIR, rel));
  if (!filePath.startsWith(PUBLIC_DIR)) return sendHtml(res, 403, 'Forbidden');
  if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) return sendHtml(res, 404, 'Not found');
  const ext = path.extname(filePath).toLowerCase();
  const type = {
    '.html': 'text/html; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8'
  }[ext] || 'application/octet-stream';
  res.writeHead(200, { 'Content-Type': type });
  fs.createReadStream(filePath).pipe(res);
}

async function readJson(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  if (!chunks.length) return {};
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

function requireFields(body, fields) {
  for (const field of fields) {
    if (body[field] === undefined || body[field] === null || body[field] === '') {
      throw new Error(`缺少必填字段：${field}`);
    }
  }
  return body;
}

function sendJson(res, status, payload) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(payload));
}

function sendHtml(res, status, html) {
  res.writeHead(status, { 'Content-Type': 'text/html; charset=utf-8' });
  res.end(html);
}

function sendCsv(res, rows) {
  const columns = ['created_at', 'account_id', 'promotion_id', 'promotion_type', 'item_id', 'action', 'mode', 'status', 'deal_price', 'error_cn'];
  const lines = [columns.join(',')];
  for (const row of rows) {
    lines.push(columns.map((column) => csvCell(row[column])).join(','));
  }
  res.writeHead(200, {
    'Content-Type': 'text/csv; charset=utf-8',
    'Content-Disposition': 'attachment; filename="promo-results.csv"'
  });
  res.end(`\uFEFF${lines.join('\n')}`);
}

function csvCell(value) {
  const text = value === null || value === undefined ? '' : String(value);
  return `"${text.replace(/"/g, '""')}"`;
}

function safeDetails(error) {
  return {
    status: error?.status,
    message: error?.message,
    body: error?.body
  };
}

function submissionApiErrorMessage(error) {
  return error?.code ? String(error.message || '本次操作未继续。') : toChineseError(error);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  }[char]));
}
