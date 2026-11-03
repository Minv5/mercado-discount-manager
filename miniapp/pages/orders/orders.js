const app = getApp();
const { getOrders } = require("../../utils/api");
const { formatCurrency, formatTime } = require("../../utils/format");

Page({
  data: {
    orders: [],
    filteredOrders: [],
    filterStatus: "all",
    searchQuery: ""
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
      (o.item_title && o.item_title.toLowerCase().includes(q))
    );
    this.setData({ filteredOrders: filtered });
  },

  openOrderDetail(e) {
    const order = e.currentTarget.dataset.order;
    wx.showModal({
      title: `订单 #${order.order_id}`,
      content: `店铺: ${order.store_name} (${order.site_id})\n买家: ${order.buyer_name}\n收件城市: ${order.shipping_city}\n总金额: ${order.amount_formatted}\n状态: ${order.status_text}`,
      showCancel: false,
      confirmText: "知道了"
    });
  }
});
