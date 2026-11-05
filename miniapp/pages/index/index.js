const app = getApp();
const { getOverview, getQuestions, getMessages, getClaims, getOrders } = require("../../utils/api");

Page({
  data: {
    stores: [],
    storeIndex: 0,
    currentStore: {},
    refreshTime: "刚刚",
    todayStats: {
      todayOrderCount: 0,
      todaySalesAmount: "0.00",
      pendingShipCount: 0,
      totalOrderCount: 0,
      totalSalesAmount: "0.00"
    },
    todo: {
      questions: 0,
      messages: 0,
      claims: 0
    }
  },

  onLoad() {
    this.initStoreData();
  },

  onShow() {
    this.refreshDashboard();
  },

  onPullDownRefresh() {
    this.refreshDashboard(() => {
      wx.stopPullDownRefresh();
    });
  },

  initStoreData() {
    const stores = app.globalData.stores;
    const storeIndex = app.globalData.currentStoreIndex;
    this.setData({
      stores,
      storeIndex,
      currentStore: stores[storeIndex] || stores[0]
    });

    // 静默预热订单缓存，确保切换到订单中心 0ms 秒开
    const store = stores[storeIndex] || stores[0];
    if (store) {
      getOrders(store.id, "all").then((list) => {
        const { formatCurrency, formatTime } = require("../../utils/format");
        const formatted = list.map(o => ({
          ...o,
          amount_formatted: formatCurrency(o.total_amount),
          created_at: formatTime(o.created_at)
        }));
        wx.setStorageSync("cached_orders_list", formatted);
      }).catch(() => {});
    }
  },

  onStoreChange(e) {
    const idx = Number(e.detail.value);
    app.setCurrentStore(idx);
    this.setData({
      storeIndex: idx,
      currentStore: this.data.stores[idx]
    });
    wx.showToast({
      title: `已切换至 ${this.data.stores[idx].name}`,
      icon: "none"
    });
    this.refreshDashboard();
  },

  refreshDashboard(callback) {
    const store = this.data.currentStore;
    const now = new Date();
    const timeStr = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;

    // 优先调用权威 /api/overview 接口，与电脑桌面端大盘完全一致
    getOverview(store.id).then(res => {
      const ov = (res && res.overview) || {};
      this.setData({
        refreshTime: timeStr,
        "todo.questions": ov.pending_questions !== undefined ? ov.pending_questions : 0,
        "todo.messages": ov.pending_messages !== undefined ? ov.pending_messages : 0,
        "todo.claims": ov.pending_claims !== undefined ? ov.pending_claims : 0,
        "todayStats.todayOrderCount": ov.today_orders !== undefined ? ov.today_orders : 0,
        "todayStats.todaySalesAmount": Number(ov.today_sales || 0).toFixed(2),
        "todayStats.pendingShipCount": ov.pending_ship !== undefined ? ov.pending_ship : 0,
        "todayStats.totalOrderCount": ov.total_orders !== undefined ? ov.total_orders : 0,
        "todayStats.totalSalesAmount": Number(ov.total_sales || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
      });
      if (typeof callback === "function") callback();
    }).catch(() => {
      // 网络降级保护：严格按照桌面端规则过滤待办与在途
      Promise.all([
        getQuestions(store.id),
        getMessages(store.id),
        getClaims(store.id),
        getOrders(store.id)
      ]).then(([qList, mList, cList, oList]) => {
        const pendingQuestions = qList.filter(q => !q.is_answered && q.status !== "ANSWERED").length;
        const pendingClaims = cList.filter(c => !c.is_closed && c.status !== "closed" && c.stage !== "closed").length;
        const todayStr = new Date().toISOString().slice(0, 10);
        const todayOrders = oList.filter(o => (o.created_at || "").slice(0, 10) === todayStr);
        const todaySales = todayOrders.reduce((acc, o) => acc + (Number(o.total_amount) || 0), 0);
        const totalSales = oList.reduce((acc, o) => acc + (Number(o.total_amount) || 0), 0);
        const pendingShip = oList.filter(o => o.filter_cat === "paid" || o.status_text === "待发货").length;

        this.setData({
          refreshTime: timeStr,
          "todo.questions": pendingQuestions,
          "todo.messages": mList.reduce((acc, m) => acc + (m.unread_count || 0), 0),
          "todo.claims": pendingClaims,
          "todayStats.todayOrderCount": todayOrders.length,
          "todayStats.todaySalesAmount": todaySales.toFixed(2),
          "todayStats.pendingShipCount": pendingShip,
          "todayStats.totalOrderCount": oList.length,
          "todayStats.totalSalesAmount": totalSales.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
        });
        if (typeof callback === "function") callback();
      }).catch(() => {
        if (typeof callback === "function") callback();
      });
    });
  },

  goToQuestions() {
    app.globalData.activeMessageTab = 0;
    wx.switchTab({
      url: "/pages/messages/messages"
    });
  },

  goToClaims() {
    app.globalData.activeMessageTab = 1;
    wx.switchTab({
      url: "/pages/messages/messages"
    });
  },

  goToMessages() {
    app.globalData.activeMessageTab = 2;
    wx.switchTab({
      url: "/pages/messages/messages"
    });
  },

  goToOrders() {
    wx.switchTab({
      url: "/pages/orders/orders"
    });
  }
});
