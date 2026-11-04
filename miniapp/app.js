App({
  globalData: {
    apiUrl: "https://awareness-reid-benchmark-army.trycloudflare.com",
    token: "",
    // 店铺列表：支持全部店铺大盘汇总与单店过滤
    stores: [
      { id: "", name: "全部店铺", alias: "全部店铺汇总 (3店已同步)", sites: ["MLM", "MLB", "MLC", "MCO", "MLA"] },
      { id: "广东店", name: "广东店", alias: "广东店 (3332096437)", sites: ["MLM", "MLB", "MLC", "MCO", "MLA"] },
      { id: "湖北店", name: "湖北店", alias: "湖北店 (2651442567)", sites: ["MLC", "MLB", "MCO", "MLM", "MLA"] },
      { id: "湖南店", name: "湖南店", alias: "湖南店 (3408885754)", sites: ["MCO", "MLC", "MLM", "MLA", "MLB"] }
    ],
    currentStoreIndex: 0,
    unreadCounts: {
      questions: 22,
      messages: 3,
      claims: 71,
      orders: 18
    }
  },

  onLaunch() {
    const savedStoreIdx = wx.getStorageSync("currentStoreIndex");
    if (savedStoreIdx !== "" && savedStoreIdx !== null) {
      this.globalData.currentStoreIndex = Number(savedStoreIdx);
    }
    const savedApiUrl = wx.getStorageSync("custom_gateway_url");
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
