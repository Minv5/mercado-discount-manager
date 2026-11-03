App({
  globalData: {
    apiUrl: "https://minv5.trade",
    token: "",
    // 三大主力店铺
    stores: [
      { id: "3332096437", name: "广东店", alias: "广东店 (3332096437)", sites: ["MLM", "MLB", "MLC", "MCO", "MLA"] },
      { id: "2651442567", name: "湖北店", alias: "湖北店 (2651442567)", sites: ["MLC", "MLB", "MCO", "MLM", "MLA"] },
      { id: "3408885754", name: "湖南店", alias: "湖南店 (3408885754)", sites: ["MCO", "MLC", "MLM", "MLA", "MLB"] }
    ],
    currentStoreIndex: 0,
    unreadCounts: {
      questions: 3,
      messages: 5,
      claims: 1,
      orders: 8
    }
  },

  onLaunch() {
    // 恢复本地存储的配置与选中店铺
    const savedStoreIdx = wx.getStorageSync("currentStoreIndex");
    if (savedStoreIdx !== "" && savedStoreIdx !== null) {
      this.globalData.currentStoreIndex = Number(savedStoreIdx);
    }
    const savedApiUrl = wx.getStorageSync("apiUrl");
    if (savedApiUrl) {
      this.globalData.apiUrl = savedApiUrl;
    }
  },

  getCurrentStore() {
    return this.globalData.stores[this.globalData.currentStoreIndex] || this.globalData.stores[0];
  },

  setCurrentStore(index) {
    this.globalData.currentStoreIndex = index;
    wx.setStorageSync("currentStoreIndex", index);
  }
});
