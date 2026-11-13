/**
 * 小程序网络请求与统一 API 交互模块
 * 已打通移动端公网安全网关，直连美客多真实店铺数据与买家回复通道
 */

const DEFAULT_GATEWAY_URL = "https://minv5.trade";

function getBaseUrl() {
  const custom = wx.getStorageSync("custom_gateway_url");
  return (custom && custom.trim()) ? custom.trim().replace(/\/+$/, "") : DEFAULT_GATEWAY_URL;
}

function getGatewayToken() {
  return String(wx.getStorageSync("gateway_access_token") || "").trim();
}

function request(path, options = {}) {
  const baseUrl = getBaseUrl();
  const token = getGatewayToken();
  const url = `${baseUrl}${path}`;
  const method = options.method || "GET";
  const data = options.data || {};

  return new Promise((resolve, reject) => {
    wx.request({
      url,
      method,
      data,
      timeout: 10000,
      header: {
        "Content-Type": "application/json; charset=utf-8",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(options.header || {})
      },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
        } else if (res.statusCode === 401 || res.statusCode === 503) {
          if (!wx._hasShownTokenTip) {
            wx._hasShownTokenTip = true;
            wx.showModal({
              title: "需要访问口令",
              content: (res.data && res.data.error) || "请到「设置」页填写与电脑端一致的访问口令。",
              showCancel: false
            });
          }
          reject(new Error(`HTTP ${res.statusCode}: 访问口令未通过`));
        } else {
          reject(new Error(`HTTP ${res.statusCode}: ${JSON.stringify(res.data)}`));
        }
      },
      fail(err) {
        console.warn(`[API] 请求失败 ${url}:`, err);
        const errMsg = (err && (err.errMsg || err.message)) || "";
        if (!wx._hasShownDomainTip && (errMsg.includes("domain") || errMsg.includes("fail") || errMsg.includes("abort"))) {
          wx._hasShownDomainTip = true;
          wx.showModal({
            title: "需要开启手机调试",
            content: "微信默认拦截了跨域公网连接。请点击右上角【···】选择【开发调试】/【打开调试】，页面边缘出现 vConsole 绿标后退出重新点进即可彻底通畅！",
            showCancel: false
          });
        }
        reject(err);
      }
    });
  });
}

// 格式化提问数据为小程序展示结构 (严格对齐桌面端买家提问与官方商家回复)
function formatQuestion(s) {
  const msgs = s.messages || [];
  const firstMsg = msgs[0] || {};
  const isAnswered = s.is_answered !== undefined ? s.is_answered : (String(s.status || "").toLowerCase() === "answered" || (s.raw_data && s.raw_data.status === "ANSWERED"));

  let sellerMsg = msgs.find(m => m.is_seller || m.sender_role === "seller");
  const rawAns = (s.raw_data && s.raw_data.answer) || {};
  let answerTextEs = "";
  let answerTextCn = "";
  let answerTime = "";

  if (sellerMsg) {
    answerTextEs = sellerMsg.text || "";
    answerTextCn = sellerMsg.text_translated || "";
    answerTime = sellerMsg.created_at || "";
  } else if (rawAns.text) {
    answerTextEs = rawAns.text || "";
    answerTextCn = rawAns.text_translated || "";
    answerTime = rawAns.date_created || "";
  }

  return {
    id: s.id,
    store_id: s.account_id || "",
    store_name: s.account_name || "美客多店铺",
    site_id: s.site_id || "MLM",
    item_id: s.item_id || "",
    item_title: s.item_title || s.item_id || "美客多热销商品",
    item_thumbnail: s.item_thumbnail || s.item_picture_url || s.sku_picture_url || "https://http2.mlstatic.com/D_NQ_NP_2X_841285-MLM43849301298_102020-F.webp",
    item_price: s.item_price || "",
    item_permalink: s.item_permalink || "",
    buyer_id: s.buyer_id || "",
    buyer_name: s.buyer_nickname || "买家咨询",
    text_es: firstMsg.text || s.last_message || "¿Tiene stock disponible?",
    text_cn: firstMsg.text_translated || s.last_message_translated || "请问还有现货吗？",
    status: isAnswered ? "ANSWERED" : "UNANSWERED",
    is_answered: isAnswered,
    has_answer: isAnswered || !!answerTextEs,
    answer_es: answerTextEs,
    answer_cn: answerTextCn,
    answer_time: answerTime ? (answerTime.slice(5, 16).replace("T", " ")) : "",
    status_text: isAnswered ? "● 已回复" : "● 待回复",
    status_class: isAnswered ? "status-gray" : "status-gold",
    created_at: s.created_at || s.updated_at || new Date().toISOString()
  };
}

// 格式化私信数据：严格对齐桌面端状态与商品图片
function formatMessage(s) {
  const msgs = (s.messages || []).map(m => ({
    sender: m.is_seller ? "seller" : "buyer",
    text: m.text || "",
    text_cn: m.text_translated || "",
    time: (m.created_at || "").slice(5, 16).replace("T", " ")
  }));

  const isBlocked = s.status === "blocked" || s.can_reply === false;
  const isUnread = s.status === "unread";
  const isPending = s.status === "new_message" || s.status === "unanswered" || (s.is_unread && s.status !== "unread");

  let statusText = "● 已读";
  let statusClass = "status-green";
  if (isBlocked) {
    statusText = "● 不可回复";
    statusClass = "status-gray";
  } else if (isUnread) {
    statusText = "● 未读";
    statusClass = "status-gold";
  } else if (isPending) {
    statusText = "● 待回复";
    statusClass = "status-gold";
  }

  return {
    id: s.id,
    store_id: s.account_id || "",
    store_name: s.account_name || "美客多店铺",
    site_id: s.site_id || "MLM",
    order_id: s.order_id || s.pack_id || s.id.replace("m_", ""),
    buyer_name: s.buyer_nickname || "买家私信",
    item_title: s.item_title || (s.primary_item && s.primary_item.title) || "",
    item_thumbnail: s.item_thumbnail || (s.primary_item && s.primary_item.thumbnail) || s.item_picture_url || "",
    status: s.status,
    is_unread: isUnread || isPending,
    can_reply: !isBlocked,
    status_text: statusText,
    status_class: statusClass,
    last_message: s.last_message || (msgs.length > 0 ? msgs[msgs.length - 1].text : "您好，关于订单有问题咨询"),
    last_message_cn: s.last_message_translated || (msgs.length > 0 ? msgs[msgs.length - 1].text_cn : ""),
    created_at: s.created_at || "",
    updated_at: s.updated_at || s.created_at || new Date().toISOString(),
    history: msgs
  };
}

// 格式化索赔数据：严格对齐桌面端官方状态、信誉影响与商品缩略图
function formatClaim(s) {
  const msgs = (s.messages || []).map(m => ({
    sender: m.is_seller ? "seller" : (m.sender_role === "mediator" ? "mediator" : "buyer"),
    sender_name: m.sender_name || (m.is_seller ? "我 (商家)" : (m.sender_role === "mediator" ? "平台调解员" : (s.buyer_nickname || "买家"))),
    text: m.text || "",
    text_cn: m.text_translated || "",
    time: (m.created_at || "").slice(5, 16).replace("T", " ")
  }));

  const isClosed = s.status === "closed" || s.stage === "closed" || s.is_closed === true;

  // 1. 信誉影响（严格对齐桌面端）
  const affectsRep = s.affects_reputation === "affected";
  const repBadgeText = affectsRep ? "影响信誉: 是" : "影响信誉: 否";
  const repBadgeClass = affectsRep ? "badge-rep-red" : "badge-rep-green";

  // 2. 官方状态徽章（严格对齐桌面端）
  let statusText = s.return_status_label || "";
  if (!statusText) {
    if (isClosed) {
      const res = (s.raw_data && s.raw_data.resolution) || {};
      if (s.stage === "dispute" || res.closed_by === "mediator") {
        statusText = "调解完成";
      } else {
        statusText = "退款结案";
      }
    } else if (s.stage === "dispute") {
      statusText = "纠纷仲裁";
    } else {
      statusText = "常规索赔";
    }
  }

  let statusClass = "badge-status-gold";
  if (["退款结案", "调解完成", "已结案"].includes(statusText)) {
    statusClass = "badge-status-gray";
  } else if (["包裹已到", "退货在途", "买家已寄"].includes(statusText)) {
    statusClass = "badge-status-blue";
  } else if (["待我同意", "纠纷仲裁"].includes(statusText)) {
    statusClass = "badge-status-red";
  }

  // 3. 投诉理由与阶段后缀（严格对齐桌面端）
  let stageCn = s.stage || "";
  if (stageCn === "dispute") stageCn = "平台调解";
  else if (stageCn === "claim") stageCn = "买家投诉";
  else if (stageCn === "recontact") stageCn = "调解完成";
  if (isClosed) stageCn = "调解完成";

  const reasonTxt = s.claim_reason || s.last_message || "买家投诉";
  const reasonDisplay = stageCn ? `${reasonTxt} · ${stageCn}` : reasonTxt;

  // 统一金额美元展示
  let cleanAmount = s.item_price || s.claim_amount || "$ 0.00";
  if (cleanAmount && !cleanAmount.startsWith("$")) {
    const num = cleanAmount.replace(/[^0-9.]/g, "");
    cleanAmount = num ? `$ ${num}` : cleanAmount;
  }

  return {
    id: s.id,
    store_id: s.account_id || "",
    store_name: s.account_name || "美客多店铺",
    site_id: s.site_id || "MLM",
    order_id: s.order_id || s.id.replace("c_", ""),
    claim_id: s.id.replace("c_", ""),
    buyer_name: s.buyer_nickname || "买家",
    item_title: s.item_title || "",
    item_thumbnail: s.item_thumbnail || s.item_picture_url || "",
    item_id: s.item_id || "",
    stage: s.stage,
    status: s.status,
    is_closed: isClosed,
    rep_badge_text: repBadgeText,
    rep_badge_class: repBadgeClass,
    status_text: statusText,
    status_class: statusClass,
    reason: s.claim_reason || s.reason || (reasonTxt !== "买家投诉" ? reasonTxt : ""),
    reason_display: reasonDisplay,
    amount: cleanAmount,
    created_at: s.created_at || s.updated_at || new Date().toISOString(),
    return_status_label: statusText,
    return_shipment_id: s.return_shipment_id || "",
    return_destination: s.return_destination || "",
    reputation_text: repBadgeText,
    reputation_class: repBadgeClass,
    history: msgs
  };
}

// 解析发货截止时间戳（秒）：优先用网关算好的，否则按 CBT 工作日与 SLA 规则本地推算
function resolveDeadlineTs(o, filterCat) {
  const served = o.shipping_deadline || {};
  if (Number(served.deadline_ts) > 0) return Number(served.deadline_ts);
  if (filterCat !== "paid") return 0;

  const sh = o.shipping || {};
  const shipSt = String(sh.status || "").toLowerCase();
  const shipSub = String(sh.substatus || "").toLowerCase();
  if (["shipped", "in_transit", "delivered", "completed", "cancelled"].includes(shipSt) ||
      ["picked_up", "dropped_off", "in_hub", "forwarded_to_carrier", "on_carrier", "customs_cleared"].includes(shipSub)) {
    return 0;
  }

  let deadlineTs = 0;
  if (sh.estimated_handling_limit) {
    const d = new Date(sh.estimated_handling_limit);
    if (!isNaN(d.getTime())) deadlineTs = d.getTime() / 1000;
  }

  if (!deadlineTs) {
    const rawTime = o.created_timestamp || o.date_created;
    let baseTime = rawTime ? new Date(typeof rawTime === "number" ? rawTime * 1000 : rawTime) : new Date();
    if (isNaN(baseTime.getTime())) baseTime = new Date();

    const site = String(o.site_id || "").toUpperCase();
    const hHours = Number(sh.handling_hours || 0);
    let bDays = 2;
    if (hHours > 0) {
      const hDays = Math.floor(hHours / 24);
      if (site === "MLM") bDays = Math.min(2, Math.max(1, hDays));
      else if (site === "MLB") bDays = Math.min(3, Math.max(2, hDays));
      else if (site === "MLC" || site === "MCO") bDays = Math.min(3, Math.max(2, hDays));
      else if (site === "MLA") bDays = Math.min(4, Math.max(3, hDays));
      else bDays = Math.max(2, Math.min(4, hDays));
    } else {
      bDays = (site === "MLC" || site === "MCO" || site === "MLA") ? 3 : 2;
    }

    // Convert to Beijing time (UTC+8)
    const curr = new Date(baseTime.getTime() + 8 * 3600 * 1000);
    while (curr.getUTCDay() === 0 || curr.getUTCDay() === 6) {
      curr.setUTCDate(curr.getUTCDate() + 1);
    }
    let daysCounted = 0;
    while (daysCounted < bDays) {
      curr.setUTCDate(curr.getUTCDate() + 1);
      if (curr.getUTCDay() !== 0 && curr.getUTCDay() !== 6) {
        daysCounted++;
      }
    }
    curr.setUTCHours(23, 59, 59, 0);
    deadlineTs = (curr.getTime() - 8 * 3600 * 1000) / 1000;
  }

  return deadlineTs || 0;
}

// 把截止时间戳渲染成当前时刻的倒计时文案；定时器每次调用都会得到最新剩余时间
function formatDeadline(deadlineTs) {
  if (!deadlineTs) return null;

  const nowTs = Date.now() / 1000;
  const diff = deadlineTs - nowTs;

  if (diff > 0) {
    const totalMinutes = Math.floor(diff / 60);
    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;
    if (hours >= 24) {
      const days = Math.floor(hours / 24);
      const remH = hours % 24;
      return {
        text: remH > 0 ? `⏳ 剩 ${days}天${remH}时` : `⏳ 剩 ${days}天`,
        class: "sla-green",
        color: "#34D399"
      };
    } else if (hours >= 6) {
      return {
        text: `⏳ 剩 ${hours}时${minutes}分`,
        class: "sla-yellow",
        color: "#FBBF24"
      };
    } else {
      return {
        text: minutes > 0 ? `🚨 仅剩 ${hours}时${minutes}分` : `🚨 仅剩 ${hours}时`,
        class: "sla-red",
        color: "#EF4444"
      };
    }
  } else {
    const overdueMinutes = Math.floor(Math.abs(diff) / 60);
    const overdueH = Math.floor(overdueMinutes / 60);
    const overdueM = overdueMinutes % 60;
    if (overdueH >= 24) {
      const overdueD = Math.floor(overdueH / 24);
      const overdueRemH = overdueH % 24;
      return {
        text: overdueRemH > 0 ? `⚠️ 已超时 ${overdueD}天${overdueRemH}时` : `⚠️ 已超时 ${overdueD}天`,
        class: "sla-overdue",
        color: "#F87171"
      };
    } else if (overdueH > 0) {
      return {
        text: `⚠️ 已超时 ${overdueH}时${overdueM}分`,
        class: "sla-overdue",
        color: "#F87171"
      };
    } else {
      return {
        text: `⚠️ 已超时 ${overdueM}分`,
        class: "sla-overdue",
        color: "#F87171"
      };
    }
  }
}

// 格式化订单数据（与桌面端 get_order_status_badge_info 规则对齐）
function formatOrder(o) {
  const items = o.items || [];
  const firstItem = items[0] || {};
  const sh = o.shipping || {};
  const recv = (sh.receiver_address) || {};
  const city = sh.city_name || recv.city || "目的地城市";
  const state = sh.state_name || recv.state || "";
  const street = [sh.street_name || recv.street_name, sh.street_number || recv.street_number].filter(Boolean).join(" ");
  const fullAddress = [sh.country_id, state, city, street, sh.zip_code].filter(Boolean).join(", ");

  const shipSt = String(sh.status || "").toLowerCase();
  const shipSub = String(sh.substatus || "").toLowerCase();
  const orderSt = String(o.status || "").toLowerCase();

  let statusText = o.status_text || "";
  let filterCat = o.filter_cat || "";

  if (!statusText || !filterCat) {
    if (["delivered", "completed"].includes(shipSt) || shipSub === "delivered") {
      statusText = "已送达";
      filterCat = "delivered";
    } else if (shipSt === "cancelled" || shipSub.includes("cancelled") || orderSt === "cancelled") {
      statusText = "已取消";
      filterCat = "cancelled";
    } else if (["not_delivered", "failed"].includes(shipSt) || shipSub.includes("not_delivered")) {
      statusText = "未送达";
      filterCat = "not_delivered";
    } else if (["shipped", "in_transit"].includes(shipSt) || ["picked_up", "dropped_off", "in_hub", "forwarded_to_carrier"].includes(shipSub)) {
      statusText = "运送中";
      filterCat = "shipped";
    } else if (["ready_to_ship", "handling", "to_ship", "to_be_shipped"].includes(shipSt) || ["paid", "confirmed"].includes(orderSt)) {
      statusText = "待发货";
      filterCat = "paid";
    } else {
      statusText = "处理中";
      filterCat = orderSt;
    }
  }

  const deadlineTs = resolveDeadlineTs(o, filterCat);
  // 网关只给了文案、没给时间戳时沿用其文案（无法本地刷新）
  const deadline = formatDeadline(deadlineTs) || (o.shipping_deadline && o.shipping_deadline.text ? o.shipping_deadline : null);

  const paidVal = Number(o.paid_amount || o.total_amount || 0);
  const saleFeeVal = Number(o.sale_fee || 0);
  const shipCostVal = Number(sh.shipping_cost || o.shipping_cost || 0);
  const netProfitVal = paidVal - saleFeeVal - shipCostVal;
  const netProfitCny = (netProfitVal * 7.2).toFixed(2);

  const payments = o.payments || [];
  const primaryPayment = payments[0] || {};
  const paymentMethod = primaryPayment.payment_method || "Mercado Pago";
  const paymentId = primaryPayment.payment_id || "无";
  const isPrinted = String(sh.substatus || "").toLowerCase().includes("print") && !String(sh.substatus || "").toLowerCase().includes("ready");

  return {
    order_id: String(o.order_id || o.id || ""),
    pack_id: String(o.pack_id || ""),
    has_pack_id: Boolean(o.pack_id && String(o.pack_id).startsWith("20000") && String(o.pack_id) !== String(o.order_id)),
    store_id: o.account_id || "",
    store_name: o.store_name || "美客多店铺",
    site_id: o.site_id || "MLM",
    status: o.status || "paid",
    filter_cat: filterCat,
    status_text: statusText,
    shipping_deadline: deadline,
    deadline_ts: deadlineTs,
    item_title: firstItem.title || "美客多跨境电商热卖单品",
    item_thumbnail: firstItem.thumbnail || firstItem.picture_url || "https://http2.mlstatic.com/D_NQ_NP_2X_841285-MLM43849301298_102020-F.webp",
    item_sku: firstItem.sku || firstItem.item_id || "",
    item_id: firstItem.item_id || "",
    item_permalink: firstItem.permalink || "",
    quantity: firstItem.quantity || 1,
    unit_price: firstItem.unit_price || 0,
    paid_amount: paidVal.toFixed(2),
    total_amount: paidVal,
    sale_fee_formatted: saleFeeVal > 0 ? `-USD ${saleFeeVal.toFixed(2)}` : "免佣金",
    shipping_cost_formatted: shipCostVal > 0 ? `-USD ${shipCostVal.toFixed(2)}` : "免运费",
    net_profit_formatted: `USD ${netProfitVal.toFixed(2)} (约 ¥${netProfitCny})`,
    payment_method: paymentMethod,
    payment_id: paymentId,
    taxes_formatted: o.taxes_amount > 0 ? `USD ${Number(o.taxes_amount).toFixed(2)}` : "",
    is_printed: isPrinted,
    carrier_status: sh.substatus || sh.status || "正常履约",
    currency_id: o.currency_id || "USD",
    buyer_name: o.buyer_nickname || o.buyer_full_name || sh.receiver_name || "海外买家",
    shipping_city: city,
    tracking_number: sh.tracking_number || "",
    tracking_method: sh.tracking_method || sh.logistic_type || "官方物流",
    receiver_name: sh.receiver_name || o.buyer_full_name || "",
    receiver_phone: sh.receiver_phone || "无",
    full_address: fullAddress,
    package_dimensions: sh.dimensions || firstItem.dimensions || "未记录",
    estimated_delivery: (sh.estimated_delivery_date || "").slice(0, 10),
    items: items,
    created_at: o.date_created || new Date().toISOString()
  };
}

function getQuestions(storeId) {
  const storeParam = storeId ? `&store_name=${encodeURIComponent(storeId)}` : "";
  return request(`/api/sessions?type=question${storeParam}`)
    .then(res => {
      const list = res.sessions || [];
      return list.map(formatQuestion);
    })
    .catch(err => {
      console.warn("读取提问失败，降级本地展示:", err);
      return [];
    });
}

function getMessages(storeId) {
  const storeParam = storeId ? `&store_name=${encodeURIComponent(storeId)}` : "";
  return request(`/api/sessions?type=message${storeParam}`)
    .then(res => {
      const list = res.sessions || [];
      return list.map(formatMessage);
    })
    .catch(err => {
      console.warn("读取私信失败，降级本地展示:", err);
      return [];
    });
}

function getClaims(storeId) {
  const storeParam = storeId ? `&store_name=${encodeURIComponent(storeId)}` : "";
  return request(`/api/sessions?type=claim${storeParam}`)
    .then(res => {
      const list = res.sessions || [];
      return list.map(formatClaim);
    })
    .catch(err => {
      console.warn("读取索赔失败，降级本地展示:", err);
      return [];
    });
}

function getOrders(storeId, status) {
  const storeParam = storeId ? `&store_name=${encodeURIComponent(storeId)}` : "";
  const statusParam = (status && status !== "all") ? `&status=${encodeURIComponent(status)}` : "";
  return request(`/api/orders?1=1${storeParam}${statusParam}`)
    .then(res => {
      const list = res.orders || [];
      return list.map(formatOrder);
    })
    .catch(err => {
      console.warn("读取订单失败，降级本地展示:", err);
      return [];
    });
}

function sendReply(targetId, type, contentText) {
  return request("/api/reply", {
    method: "POST",
    data: {
      session_id: targetId,
      reply_text: contentText,
      chat_type: type
    }
  }).then(res => {
    return { ok: res.success !== false, msg: res.message || "回复已成功发送至美客多买家！" };
  });
}

function getGatewayStatus() {
  return request("/api/status")
    .then(res => ({ ok: true, data: res }))
    .catch(err => ({ ok: false, error: err.message }));
}

// 校验当前访问口令是否被网关接受
function checkGatewayAuth() {
  return request("/api/auth_check")
    .then(() => ({ ok: true }))
    .catch(err => ({ ok: false, error: err.message }));
}

function getOverview(storeId) {
  const storeParam = storeId ? `?store_name=${encodeURIComponent(storeId)}` : "";
  return request(`/api/overview${storeParam}`);
}

// confirmed 必须由调用方在用户明确点击确认后传 true，否则网关拒绝真实索赔操作
function handleClaimAction(sessionId, action, extraParams = {}, confirmed = false) {
  return request("/api/claim_action", {
    method: "POST",
    data: {
      session_id: sessionId,
      action: action,
      extra_params: extraParams,
      ...(confirmed === true ? { confirm: "REAL_SUBMIT" } : {})
    }
  }).then(res => {
    return { ok: res.success !== false, msg: res.message || "索赔处理操作已成功提交！" };
  });
}

function getSessionDetail(sessionId, type = "question") {
  return request(`/api/session_detail?session_id=${encodeURIComponent(sessionId)}`)
    .then(res => {
      if (res && res.session) {
        if (type === "claim") return formatClaim(res.session);
        if (type === "message") return formatMessage(res.session);
        return formatQuestion(res.session);
      }
      return null;
    })
    .catch(() => null);
}

module.exports = {
  DEFAULT_GATEWAY_URL,
  getBaseUrl,
  getOverview,
  getQuestions,
  getMessages,
  getClaims,
  getOrders,
  formatDeadline,
  sendReply,
  handleClaimAction,
  getSessionDetail,
  getGatewayStatus,
  checkGatewayAuth
};
