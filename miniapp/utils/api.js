/**
 * 小程序网络请求与统一 API 交互模块
 * 已打通移动端公网安全网关，直连美客多真实店铺数据与买家回复通道
 */

const DEFAULT_GATEWAY_URL = "https://traditional-signature-rooms-blocks.trycloudflare.com";

function getBaseUrl() {
  const custom = wx.getStorageSync("custom_gateway_url");
  return (custom && custom.trim()) ? custom.trim().replace(/\/+$/, "") : DEFAULT_GATEWAY_URL;
}

function request(path, options = {}) {
  const baseUrl = getBaseUrl();
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
        ...(options.header || {})
      },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
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

// 格式化提问数据为小程序展示结构
function formatQuestion(s) {
  const msgs = s.messages || [];
  const firstMsg = msgs[0] || {};
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
    status: s.status || (s.is_read ? "ANSWERED" : "UNANSWERED"),
    created_at: s.created_at || s.updated_at || new Date().toISOString()
  };
}

// 格式化私信数据
function formatMessage(s) {
  const msgs = (s.messages || []).map(m => ({
    sender: m.is_seller ? "seller" : "buyer",
    text: m.text || "",
    text_cn: m.text_translated || "",
    time: (m.created_at || "").slice(5, 16).replace("T", " ")
  }));
  return {
    id: s.id,
    store_id: s.account_id || "",
    store_name: s.account_name || "美客多店铺",
    site_id: s.site_id || "MLM",
    order_id: s.order_id || s.pack_id || s.id.replace("m_", ""),
    buyer_name: s.buyer_nickname || "买家私信",
    last_message: s.last_message || (msgs.length > 0 ? msgs[msgs.length - 1].text : "您好，关于订单有问题咨询"),
    last_message_cn: s.last_message_translated || (msgs.length > 0 ? msgs[msgs.length - 1].text_cn : "您好，关于订单有问题咨询"),
    unread_count: s.is_read ? 0 : 1,
    updated_at: s.updated_at || s.created_at || new Date().toISOString(),
    history: msgs
  };
}

// 格式化索赔数据
function formatClaim(s) {
  const msgs = (s.messages || []).map(m => ({
    sender: m.is_seller ? "seller" : (m.sender_role === "mediator" ? "mediator" : "buyer"),
    text: m.text || "",
    text_cn: m.text_translated || "",
    time: (m.created_at || "").slice(5, 16).replace("T", " ")
  }));
  return {
    id: s.id,
    store_id: s.account_id || "",
    store_name: s.account_name || "美客多店铺",
    site_id: s.site_id || "MLM",
    order_id: s.order_id || s.id.replace("c_", ""),
    claim_id: s.id.replace("c_", ""),
    stage: s.stage || "dispute",
    reason: s.claim_reason || s.last_message || "买家申请售后退货/纠纷",
    deadline_hours: 8,
    amount: s.item_price || s.claim_amount || "协商处理中",
    item_title: s.item_title || "",
    item_thumbnail: s.item_thumbnail || s.item_picture_url || "",
    item_id: s.item_id || "",
    created_at: s.created_at || s.updated_at || new Date().toISOString(),
    history: msgs
  };
}

// 格式化订单数据
function formatOrder(o) {
  const items = o.items || [];
  const firstItem = items[0] || {};
  const statusMap = {
    paid: "待打单发货",
    confirmed: "已确认",
    payment_required: "待付款",
    payment_in_process: "支付处理中",
    partially_paid: "部分支付",
    shipped: "干线运输中",
    delivered: "已妥投送达",
    cancelled: "已取消",
    invalid: "无效订单"
  };
  const sh = o.shipping || {};
  const recv = (sh.receiver_address) || {};
  const city = sh.city_name || recv.city || "目的地城市";
  const state = sh.state_name || recv.state || "";
  const street = [sh.street_name || recv.street_name, sh.street_number || recv.street_number].filter(Boolean).join(" ");
  const fullAddress = [sh.country_id, state, city, street, sh.zip_code].filter(Boolean).join(", ");

  return {
    order_id: String(o.order_id || o.id || ""),
    store_id: o.account_id || "",
    store_name: o.store_name || "美客多店铺",
    site_id: o.site_id || "MLM",
    status: o.status || "paid",
    status_text: statusMap[o.status] || o.status || "处理中",
    item_title: firstItem.title || "美客多跨境电商热卖单品",
    item_thumbnail: firstItem.thumbnail || firstItem.picture_url || "https://http2.mlstatic.com/D_NQ_NP_2X_841285-MLM43849301298_102020-F.webp",
    item_sku: firstItem.sku || firstItem.item_id || "",
    item_permalink: firstItem.permalink || "",
    quantity: firstItem.quantity || 1,
    unit_price: firstItem.unit_price || 0,
    total_amount: Number(o.paid_amount || o.total_amount || 0),
    currency_id: o.currency_id || "USD",
    buyer_name: o.buyer_nickname || o.buyer_full_name || sh.receiver_name || "海外买家",
    shipping_city: city,
    tracking_number: sh.tracking_number || "",
    tracking_method: sh.tracking_method || sh.logistic_type || "官方物流",
    receiver_name: sh.receiver_name || o.buyer_full_name || "",
    receiver_phone: sh.receiver_phone || "",
    full_address: fullAddress,
    package_dimensions: sh.dimensions || firstItem.dimensions || "",
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

function handleClaimAction(sessionId, action, extraParams = {}) {
  return request("/api/claim_action", {
    method: "POST",
    data: {
      session_id: sessionId,
      action: action,
      extra_params: extraParams
    }
  }).then(res => {
    return { ok: res.success !== false, msg: res.message || "索赔处理操作已成功提交！" };
  });
}

module.exports = {
  DEFAULT_GATEWAY_URL,
  getBaseUrl,
  getQuestions,
  getMessages,
  getClaims,
  getOrders,
  sendReply,
  handleClaimAction,
  getGatewayStatus
};
