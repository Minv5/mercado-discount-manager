export const ITEM_STATUS_WHITELIST = new Set(['candidate', 'pending', 'started']);
export const DIRECT_PAYLOAD_PROMOTION_TYPES = new Set(['SELLER_CAMPAIGN', 'DEAL']);
export const SPECIAL_PROMOTION_TYPES = new Set(['SMART', 'LIGHTNING']);
export const ADAPTER_STATES = Object.freeze({
  unsupported: 'unsupported',
  parameters_unconfirmed: 'parameters_unconfirmed',
  ready_for_preview: 'ready_for_preview',
  ready_for_limited_real_test: 'ready_for_limited_real_test'
});

const SPECIAL_ADAPTERS = {
  SMART: {
    label: 'SMART 智能/共同出资活动',
    requiredFields: ['offer_id'],
    observedFields: ['offer_id', 'seller_percentage', 'meli_percentage', 'start_date', 'end_date', 'price', 'original_price'],
    previewFields: ['promotion_id', 'promotion_type', 'offer_id'],
    defaultAdapterState: ADAPTER_STATES.ready_for_preview,
    evidence: '官方 PIX/BANK offer_id 模式线索 + live SMART candidate 字段',
    unconfirmedReason: '实验预览已就绪，等待单商品真实验证；不能批量真实放行'
  },
  LIGHTNING: {
    label: 'LIGHTNING 限时闪购活动',
    requiredFields: ['stock', 'min_discounted_price', 'price', 'original_price'],
    observedFields: ['stock', 'min_discounted_price', 'price', 'original_price'],
    previewFields: ['deal_id', 'promotion_type', 'deal_price', 'original_price', 'stock'],
    defaultAdapterState: ADAPTER_STATES.ready_for_limited_real_test,
    evidence: '官方 Lightning Deal Global Selling 文档 body: deal_id / deal_price / original_price / promotion_type=LIGHTNING / stock + live LIGHTNING candidate 字段',
    unconfirmedReason: '官方 body 已确认，可生成实验 preview；真实写入仍需小样本验证和主管最终确认，不能批量放行'
  }
};

export function validateItemStatus(status) {
  return ITEM_STATUS_WHITELIST.has(String(status || ''));
}

export function requireItemStatus(status) {
  if (!validateItemStatus(status)) {
    const error = new Error('商品状态参数非法，只允许 candidate / pending / started');
    error.status = 400;
    throw error;
  }
  return status;
}

export function isDirectPayloadPromotionType(promotionType) {
  return DIRECT_PAYLOAD_PROMOTION_TYPES.has(String(promotionType || '').toUpperCase());
}

export function buildSubmitPayloadPreview({ promotion, row, action }) {
  const promotionType = String(promotion?.promotion_type || promotion?.type || '').toUpperCase();
  const promotionId = promotion?.promotion_id || promotion?.id;
  const fieldSummary = summarizeSpecialPromotionFields(row?.item || row?.raw || {});
  const adapterState = getPromotionAdapterState({ promotionType, promotionId, row, action, fieldSummary });

  if (row?.status !== 'planned') {
    return {
      can_submit: false,
      preview_only: false,
      requires_supervisor_final_confirmation: true,
      status: 'skipped',
      adapter_state: adapterState.adapter_state,
      reason: row?.reason || '该商品未进入可执行计划',
      field_summary: fieldSummary
    };
  }

  if (SPECIAL_PROMOTION_TYPES.has(promotionType)) {
    return {
      can_submit: adapterState.can_submit,
      preview_only: [ADAPTER_STATES.ready_for_preview, ADAPTER_STATES.ready_for_limited_real_test].includes(adapterState.adapter_state),
      requires_supervisor_final_confirmation: true,
      requires_limited_real_test: adapterState.requires_limited_real_test || false,
      status: adapterState.status || adapterState.adapter_state,
      adapter_state: adapterState.adapter_state,
      reason: adapterState.reason,
      field_summary: fieldSummary,
      adapter: adapterState,
      payload: adapterState.preview_payload
    };
  }

  if (!isDirectPayloadPromotionType(promotionType)) {
    return {
      can_submit: false,
      preview_only: false,
      requires_supervisor_final_confirmation: true,
      status: 'unsupported_type',
      adapter_state: ADAPTER_STATES.unsupported,
      reason: `${promotionType || 'UNKNOWN'} 活动类型未确认提交参数，真实执行前阻断`,
      field_summary: fieldSummary
    };
  }

  if (action === 'cancel') {
    return {
      can_submit: true,
      preview_only: true,
      requires_supervisor_final_confirmation: true,
      status: 'preview_ready',
      adapter_state: ADAPTER_STATES.ready_for_preview,
      field_summary: fieldSummary,
      payload: {
        promotion_id: promotionId,
        promotion_type: promotionType
      }
    };
  }

  const payload = {
    promotion_id: promotionId,
    promotion_type: promotionType,
    deal_price: row.deal_price
  };
  const topDealPrice = topDealPriceFromRow(row);
  if (topDealPrice !== null) payload.top_deal_price = topDealPrice;
  return {
    can_submit: true,
    preview_only: true,
    requires_supervisor_final_confirmation: true,
    status: 'preview_ready',
    adapter_state: ADAPTER_STATES.ready_for_preview,
    field_summary: fieldSummary,
    payload
  };
}

export function requireExecutableSubmitPayload(preview) {
  if (!preview?.payload) {
    const error = new Error(preview?.reason || '缺少可提交 payload');
    error.code = 'submit_payload_blocked';
    error.policyBlocked = true;
    throw error;
  }
  if (preview.can_submit !== true) {
    const error = new Error(preview.reason || preview.adapter?.reason || '该活动类型尚未允许批量真实提交');
    error.code = 'submit_payload_blocked';
    error.policyBlocked = true;
    error.adapter_state = preview.adapter_state || preview.adapter?.adapter_state || null;
    error.promotion_type = preview.adapter?.promotion_type || null;
    throw error;
  }
  return preview.payload;
}

export function getPromotionAdapterState({ promotionType, promotionId, row, action, fieldSummary: providedSummary } = {}) {
  const type = String(promotionType || '').toUpperCase();
  const fieldSummary = providedSummary || summarizeSpecialPromotionFields(row?.item || row?.raw || {});
  if (isDirectPayloadPromotionType(type)) {
    return {
      promotion_type: type,
      adapter_state: ADAPTER_STATES.ready_for_preview,
      can_submit: true,
      preview_only: true,
      requires_supervisor_final_confirmation: true,
      reason: 'DEAL / SELLER_CAMPAIGN 已有普通 deal_price preview payload；真实写入仍需主管最终确认。'
    };
  }

  if (!SPECIAL_PROMOTION_TYPES.has(type)) {
    return {
      promotion_type: type || 'UNKNOWN',
      adapter_state: ADAPTER_STATES.unsupported,
      can_submit: false,
      preview_only: false,
      requires_supervisor_final_confirmation: true,
      reason: `${type || 'UNKNOWN'} 活动类型未确认提交参数，真实执行前阻断`,
      missing_fields: ['official_submit_parameters']
    };
  }

  const adapter = SPECIAL_ADAPTERS[type];
  const missingFields = missingAdapterFields(adapter.requiredFields, fieldSummary);
  const lightningStockMin = stockMin(fieldSummary.stock);
  const dealPrice = type === 'LIGHTNING' ? fieldSummary.price : numberOrNull(row?.deal_price ?? fieldSummary.price);
  const missingLightningFields = type === 'LIGHTNING'
    ? [
      ...missingFields,
      lightningStockMin === null ? 'stock.min' : null,
      dealPrice === null ? 'deal_price' : null
    ].filter(Boolean)
    : missingFields;
  const missingRequirements = type === 'SMART' ? missingFields : missingLightningFields;
  if (type === 'SMART' && missingFields.length) {
    return {
      promotion_type: type,
      label: adapter.label,
      adapter_state: ADAPTER_STATES.parameters_unconfirmed,
      status: 'adapter_fields_incomplete',
      can_submit: false,
      preview_only: false,
      requires_supervisor_final_confirmation: true,
      requires_limited_real_test: true,
      official_parameters_confirmed: false,
      observed_fields: adapter.observedFields,
      required_fields: adapter.requiredFields,
      preview_fields: adapter.previewFields,
      missing_fields: missingRequirements,
      item_missing_fields: missingFields,
      payload_evidence: adapter.evidence,
      next_step: '补齐 SMART candidate offer_id 后才能生成实验 preview；即使生成 preview，也只允许主管安排单商品真实验证。',
      reason: `SMART 字段不足，缺少 ${missingFields.join(', ')}；不能生成 offer_id 模式实验 preview。`,
      preview_payload: null,
      action: action || null
    };
  }
  if (type === 'LIGHTNING' && missingLightningFields.length) {
    return {
      promotion_type: type,
      label: adapter.label,
      adapter_state: ADAPTER_STATES.parameters_unconfirmed,
      status: 'adapter_fields_incomplete',
      can_submit: false,
      preview_only: false,
      requires_supervisor_final_confirmation: true,
      requires_limited_real_test: true,
      official_parameters_confirmed: true,
      observed_fields: adapter.observedFields,
      required_fields: adapter.requiredFields,
      preview_fields: adapter.previewFields,
      missing_fields: missingRequirements,
      item_missing_fields: missingLightningFields,
      payload_evidence: adapter.evidence,
      next_step: '补齐 LIGHTNING candidate stock.min、deal_price、original_price 后才能生成官方 body preview；即使生成 preview，也不能批量真实放行。',
      reason: `LIGHTNING 字段不足，缺少 ${missingLightningFields.join(', ')}；不能生成官方 body preview。`,
      preview_payload: null,
      action: action || null
    };
  }
  const adapterState = adapter.defaultAdapterState;
  const reason = missingFields.length
    ? `${type} 字段不足，缺少 ${missingFields.join(', ')}；待测试线程确认参数后可启用 preview，且不能沿用普通 deal_price payload`
    : `${type} ${adapter.unconfirmedReason}`;
  const previewPayload = type === 'SMART'
    ? {
      promotion_id: promotionId || row?.promotion_id || null,
      promotion_type: 'SMART',
      offer_id: fieldSummary.offer_id
    }
    : {
      deal_id: promotionId || row?.promotion_id || null,
      deal_price: dealPrice,
      original_price: fieldSummary.original_price,
      promotion_type: 'LIGHTNING',
      stock: lightningStockMin
    };
  return {
    promotion_type: type,
    label: adapter.label,
    adapter_state: adapterState,
    can_submit: false,
    preview_only: [ADAPTER_STATES.ready_for_preview, ADAPTER_STATES.ready_for_limited_real_test].includes(adapterState),
    requires_supervisor_final_confirmation: true,
    requires_limited_real_test: true,
    official_parameters_confirmed: type === 'LIGHTNING',
    observed_fields: adapter.observedFields,
    required_fields: adapter.requiredFields,
    preview_fields: adapter.previewFields,
    missing_fields: missingRequirements,
    item_missing_fields: missingFields,
    payload_evidence: adapter.evidence || null,
    next_step: type === 'SMART'
      ? 'SMART 实验 preview 已就绪；需主管明确安排单商品真实验证，不能批量放行。'
      : 'LIGHTNING 官方 body preview 已就绪；需主管明确安排单商品小样本验证，不能批量放行。',
    reason,
    preview_payload: previewPayload,
    action: action || null
  };
}

export function summarizeSpecialPromotionFields(item) {
  const source = item?.raw || item || {};
  const raw = source?.raw_json ? safeJson(source.raw_json) : source;
  return {
    offer_id: raw.offer_id ?? source?.offer_id ?? item?.offer_id ?? null,
    seller_percentage: numberOrNull(raw.seller_percentage ?? source?.seller_percentage ?? item?.seller_percentage),
    meli_percentage: numberOrNull(raw.meli_percentage ?? source?.meli_percentage ?? item?.meli_percentage),
    stock: raw.stock ?? source?.stock ?? item?.stock ?? null,
    min_discounted_price: numberOrNull(raw.min_discounted_price ?? source?.min_discounted_price ?? item?.min_discounted_price),
    max_discounted_price: numberOrNull(raw.max_discounted_price ?? source?.max_discounted_price ?? item?.max_discounted_price),
    price: numberOrNull(raw.price ?? source?.price ?? item?.price),
    original_price: numberOrNull(raw.original_price ?? source?.original_price ?? item?.original_price),
    top_deal_price: numberOrNull(raw.top_deal_price ?? source?.top_deal_price ?? item?.top_deal_price),
    start_date: raw.start_date ?? source?.start_date ?? item?.start_date ?? null,
    end_date: raw.end_date ?? source?.end_date ?? item?.end_date ?? null
  };
}

function missingAdapterFields(requiredFields, fieldSummary) {
  return requiredFields.filter((field) => {
    const value = fieldSummary[field];
    return value === null || value === undefined || value === '';
  });
}

function safeJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    return {};
  }
}

function numberOrNull(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function stockMin(stock) {
  if (stock === null || stock === undefined || stock === '') return null;
  if (typeof stock === 'number' || typeof stock === 'string') return numberOrNull(stock);
  return numberOrNull(stock.min ?? stock.minimum);
}

function topDealPriceFromRow(row) {
  const rawJsonTopDealPrice = row?.item?.raw_json
    ? safeJson(row.item.raw_json).top_deal_price
    : row?.item?.raw?.raw_json
      ? safeJson(row.item.raw.raw_json).top_deal_price
      : null;
  return numberOrNull(
    row?.top_deal_price
    ?? row?.item?.top_deal_price
    ?? row?.item?.raw_json?.top_deal_price
    ?? row?.item?.raw?.top_deal_price
    ?? rawJsonTopDealPrice
  );
}
