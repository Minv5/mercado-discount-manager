const app = getApp();
const { getOrders } = require("../../utils/api");
const { formatCurrency, formatTime } = require("../../utils/format");

Page({
  data: {
    orders: [],
    filteredOrders: [],
    filterStatus: "all",
    searchQuery: "",
    showOrderDrawer: false,
    currentOrder: {}
  },

  onShow() {
    this.loadOrders();
  },

  onPullDownRefresh() {
    this.loadOrders(() => {
      wx.stopPullDownRefresh();
    });
  },

  loadOrders(callback) {
    const store = app.getCurrentStore();
    getOrders(store.id, this.data.filterStatus).then((list) => {
      const formatted = list.map(o => ({
        ...o,
        amount_formatted: formatCurrency(o.total_amount, o.site_id),
        created_at: formatTime(o.created_at)
      }));
      this.setData({
        orders: formatted
      });
      this.applySearchFilter();
      if (typeof callback === "function") callback();
    }).catch(() => {
      if (typeof callback === "function") callback();
    });
  },

  onFilterChange(e) {
    const status = e.currentTarget.dataset.status;
    this.setData({ filterStatus: status }, () => {
      this.loadOrders();
    });
  },

  onSearchInput(e) {
    this.setData({ searchQuery: e.detail.value }, () => {
      this.applySearchFilter();
    });
  },

  applySearchFilter() {
    const q = (this.data.searchQuery || "").trim().toLowerCase();
    if (!q) {
      this.setData({ filteredOrders: this.data.orders });
      return;
    }
    const filtered = this.data.orders.filter(o => 
      o.order_id.toLowerCase().includes(q) ||
      (o.buyer_name && o.buyer_name.toLowerCase().includes(q)) ||
      (o.item_title && o.item_title.toLowerCase().includes(q)) ||
      (o.tracking_number && o.tracking_number.toLowerCase().includes(q))
    );
    this.setData({ filteredOrders: filtered });
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

  copyText(e) {
    const text = e.currentTarget.dataset.text;
    if (!text) return;
    wx.setClipboardData({
      data: String(text),
      success() {
        wx.showToast({
          title: "已复制到剪贴板",
          icon: "success"
        });
      }
    });
  }
});
