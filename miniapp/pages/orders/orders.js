const app = getApp();
const { getOrders } = require("../../utils/api");
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
