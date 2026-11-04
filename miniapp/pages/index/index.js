const app = getApp();
const { getQuestions, getMessages, getClaims, getOrders } = require("../../utils/api");

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
      questions: 3,
      messages: 5,
      claims: 1
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

    Promise.all([
      getQuestions(store.id),
      getMessages(store.id),
      getClaims(store.id),
      getOrders(store.id)
    ]).then(([qList, mList, cList, oList]) => {
      const pendingQuestions = qList.filter(q => q.status === "UNANSWERED" || q.status === "unanswered").length;
      const pendingClaims = cList.filter(c => c.stage !== "closed" && c.status !== "closed").length;
      // 严格统计今日（当天）实时新单与销售额
      const todayStr = new Date().toISOString().slice(0, 10);
      const todayOrders = oList.filter(o => (o.created_at || "").slice(0, 10) === todayStr);
      const todaySales = todayOrders.reduce((acc, o) => acc + (Number(o.total_amount) || 0), 0);

      const totalSales = oList.reduce((acc, o) => acc + (Number(o.total_amount) || 0), 0);
      const pendingShip = oList.filter(o => o.status === "paid" || o.status === "confirmed").length;

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
  },

  goToQuestions() {
    wx.navigateTo({
      url: "/pages/messages/messages?tab=0"
    });
  },

  goToMessages() {
    wx.navigateTo({
      url: "/pages/messages/messages?tab=1"
    });
  },

  goToClaims() {
    wx.navigateTo({
      url: "/pages/messages/messages?tab=2"
    });
  },

  goToOrders() {
    wx.switchTab({
      url: "/pages/orders/orders"
    });
  },

  openQuickTemplates() {
    wx.showActionSheet({
      itemList: ["售前常用话术", "物流查件话术", "售后退换话术"],
      success(res) {
        wx.navigateTo({
          url: "/pages/messages/messages?tab=0"
        });
      }
    });
  },

  goToSettings() {
    wx.switchTab({
      url: "/pages/settings/settings"
    });
  }
});
