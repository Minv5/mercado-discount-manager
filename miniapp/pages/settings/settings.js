const app = getApp();

Page({
  data: {
    appid: "wxad7319a4b69eec78",
    stores: [],
    pushEnabled: true
  },

  onShow() {
    this.setData({
      stores: app.globalData.stores,
      pushEnabled: wx.getStorageSync("pushNotificationEnabled") !== false
    });
  },

  togglePush(e) {
    const val = e.detail.value;
    wx.setStorageSync("pushNotificationEnabled", val);
    this.setData({ pushEnabled: val });
    wx.showToast({
      title: val ? "已开启服务通知联动" : "已关闭服务通知联动",
      icon: "none"
    });
  },

  clearStorage() {
    wx.showModal({
      title: "清除缓存",
      content: "确定清除本地所有缓存配置并重置吗？",
      success(res) {
        if (res.confirm) {
          wx.clearStorageSync();
          wx.showToast({ title: "已清空缓存", icon: "success" });
        }
      }
    });
  }
});
