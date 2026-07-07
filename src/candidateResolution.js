export const CANDIDATE_INCOMPLETE_CODE = 'api_incomplete';
export const MARKETPLACE_CANDIDATE_INCOMPLETE_CODE = 'api_incomplete_marketplace_candidate';
export const MANUAL_CANDIDATE_IMPORT_SOURCE = 'manual_candidate_import';

export function buildCandidateIncompleteResolution({
  promotionId = null,
  promotionType = null,
  platformTotal = null,
  warning = null,
  state = MARKETPLACE_CANDIDATE_INCOMPLETE_CODE
} = {}) {
  const marketplaceCandidate = state === MARKETPLACE_CANDIDATE_INCOMPLETE_CODE;
  return {
    state,
    severity: 'blocking',
    promotion_id: promotionId,
    promotion_type: promotionType,
    platform_total: platformTotal,
    message: warning || candidateIncompleteMessage({ marketplaceCandidate }),
    can_real_enroll: false,
    forbidden_fallbacks: marketplaceCandidate
      ? ['不要把 status=candidates / eligible 等近似参数返回的 started 商品当作 candidate fallback。']
      : [],
    safe_options: [
      {
        code: 'wait_alternative_api',
        label: marketplaceCandidate ? '等待官方/平台修复或替代 API' : '等待替代 API 解决',
        description: marketplaceCandidate
          ? '平台返回 candidate total，但 marketplace child 未返回明细；等待 Mercado 修复或测试线程确认稳定只读接口后再读取。'
          : '等待测试线程确认能稳定返回 candidate 明细的官方只读接口后，再重新读取商品并生成预检。'
      },
      {
        code: 'contact_mercado_support',
        label: '联系 Mercado 支持',
        description: '提供 promotion_id、promotion_type、child_user_id、site_id、paging.total 与 results=null 证据，请平台确认接口异常。'
      },
      {
        code: 'manual_candidate_import',
        label: '人工导入 candidate item_id 草案',
        description: '仅保存本地 item_id 列表；必须通过只读商品详情或已有 items 数据补齐价格、状态和边界后，才允许生成预检。'
      }
    ],
    manual_import_requirements: [
      '只保存 item_id，不视为平台明细已拉取成功。',
      '导入行 source=manual_candidate_import，detail_status=needs_readonly_detail。',
      '缺少价格、最低价、最高价或状态时，计划必须跳过或阻断。',
      '真实报名仍必须经过预检包和主管最终确认门。'
    ]
  };
}

export function candidateIncompleteMessage({ marketplaceCandidate = true } = {}) {
  if (marketplaceCandidate) {
    return '平台返回 candidate total，但 marketplace child 未返回 candidate 明细；近似 status 会返回 started，已禁止作为 fallback。';
  }
  return '平台返回候选总数但未返回候选明细，需要接口专项处理。';
}

export function buildManualCandidateDraftRows({ itemIds, status = 'candidate' } = {}) {
  const uniqueIds = [...new Set(arrayOfText(itemIds))];
  return uniqueIds.map((itemId) => ({
    item_id: itemId,
    id: itemId,
    status,
    raw: {
      item_id: itemId,
      id: itemId,
      status,
      source: MANUAL_CANDIDATE_IMPORT_SOURCE,
      detail_status: 'needs_readonly_detail',
      requires_readonly_detail: true
    }
  }));
}

export function parseManualCandidateItemIds(input) {
  return arrayOfText(input);
}

function arrayOfText(value) {
  if (Array.isArray(value)) return value.map((item) => String(item).trim()).filter(Boolean);
  return String(value || '').split(/[\s,;，；]+/).map((item) => item.trim()).filter(Boolean);
}
