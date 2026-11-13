const app = getApp();
const { getOrders, formatDeadline } = require("../../utils/api");
const { formatCurrency, formatTime } = require("../../utils/format");

Page({
  data: {
    allOrders: [],
    filteredOrders: [],
    filterStatus: "all",
    searchQuery: "",
    isLoading: true,
    showOrderDrawer: false,
    currentOrder: {}
  },

  onLoad() {
    // 1. 优先读取本地秒开快照，0ms 瞬间上屏
    const cached = wx.getStorageSync("cached_orders_list");
    if (Array.isArray(cached) && cached.length > 0) {
      this.setData({
        allOrders: cached,
        isLoading: false
      }, () => {
        this.applyFilter();
      });
    }
  },

  onShow() {
    this.fetchOrders();
    this.startCountdownTimer();
  },

  onHide() {
    this.clearCountdownTimer();
  },

  onUnload() {
    this.clearCountdownTimer();
  },

  startCountdownTimer() {
    this.clearCountdownTimer();
    this.countdownTimer = setInterval(() => {
      const { allOrders, showOrderDrawer, currentOrder } = this.data;
      if (!allOrders || allOrders.length === 0) return;
      let hasUpdate = false;
      const updated = allOrders.map(o => {
        if (o.deadline_ts) {
          hasUpdate = true;
          return { ...o, shipping_deadline: formatDeadline(o.deadline_ts) };
        }
        return o;
      });
      if (hasUpdate) {
        const updateData = { allOrders: updated };
        if (showOrderDrawer && currentOrder && currentOrder.deadline_ts) {
          updateData.currentOrder = {
            ...currentOrder,
            shipping_deadline: formatDeadline(currentOrder.deadline_ts)
          };
        }
        this.setData(updateData, () => {
          this.applyFilter();
        });
      }
    }, 30000);
  },

  clearCountdownTimer() {
    if (this.countdownTimer) {
      clearInterval(this.countdownTimer);
      this.countdownTimer = null;
    }
  },

  onPullDownRefresh() {
    this.fetchOrders(() => {
      wx.stopPullDownRefresh();
    });
  },

  // 向网关拉取全量订单并持久化快照
  fetchOrders(callback) {
    const store = app.getCurrentStore();
    getOrders(store.id, "all").then((list) => {
      const formatted = list.map(o => ({
        ...o,
        amount_formatted: formatCurrency(o.total_amount),
        created_at: formatTime(o.created_at)
      }));

      // 更新内存与本地持久缓存
      wx.setStorageSync("cached_orders_list", formatted);

      this.setData({
        allOrders: formatted,
        isLoading: false
      }, () => {
        this.applyFilter();
      });
      if (typeof callback === "function") callback();
    }).catch(() => {
      this.setData({ isLoading: false });
      if (typeof callback === "function") callback();
    });
  },

  // 纯本地内存 0ms 瞬间切换，无任何网络延迟与卡顿
  onFilterChange(e) {
    const status = e.currentTarget.dataset.status;
    if (this.data.filterStatus === status) return;

    this.setData({ filterStatus: status }, () => {
      this.applyFilter();
    });
  },

  onSearchInput(e) {
    this.setData({ searchQuery: e.detail.value }, () => {
      this.applyFilter();
    });
  },

  // 本地极速过滤
  applyFilter() {
    const { allOrders, filterStatus, searchQuery } = this.data;
    let list = allOrders;

    // 1. 状态分类过滤
    if (filterStatus && filterStatus !== "all") {
      list = list.filter(o => o.filter_cat === filterStatus);
    }

    // 2. 关键词模糊搜索
    const q = (searchQuery || "").trim().toLowerCase();
    if (q) {
      list = list.filter(o =>
        o.order_id.toLowerCase().includes(q) ||
        (o.buyer_name && o.buyer_name.toLowerCase().includes(q)) ||
        (o.item_title && o.item_title.toLowerCase().includes(q)) ||
        (o.tracking_number && o.tracking_number.toLowerCase().includes(q))
      );
    }

    this.setData({ filteredOrders: list });
  },

  openOrderDetail(e) {
    const order = e.currentTarget.dataset.order;
    this.setData({
      currentOrder: order,
      showOrderDrawer: true
    });
  },

  closeOrderDrawer() {
    this.setData({ showOrderDrawer: false });
  },

  preventProp() {},

  contactBuyer() {
    const order = this.data.currentOrder;
    if (!order) return;
    this.closeOrderDrawer();

    const orderId = String(order.order_id || "");
    const packId = String(order.pack_id || orderId);
    const buyerName = order.buyer_name || order.buyer_nickname || "买家";
    const sessionId = `m_${packId || orderId}`;

    // 优先在私信缓存中查找已有往来历史
    const cachedList = wx.getStorageSync("cached_messages_list") || [];
    const existing = cachedList.find(m => 
      String(m.order_id) === orderId || 
      String(m.pack_id) === packId || 
      String(m.id) === sessionId ||
      String(m.id) === `m_${orderId}`
    );

    const chatItem = existing ? { ...existing } : {
      id: sessionId,
      session_type: "message",
      type: "message",
      store_id: order.store_id || order.account_id || "",
      store_name: order.store_name || "美客多店铺",
      site_id: order.site_id || "MLA",
      order_id: orderId,
      pack_id: packId,
      buyer_name: buyerName,
      buyer_nickname: buyerName,
      item_title: order.item_title || "美客多订单商品",
      item_thumbnail: order.item_thumbnail || "",
      item_id: order.item_sku || order.item_id || "",
      status: "read",
      can_reply: true,
      history: []
    };

    wx.setStorageSync("activeChatItem", chatItem);
    wx.navigateTo({
      url: `/pages/chat/chat?type=message&id=${encodeURIComponent(chatItem.id)}`
    });
  }
});
