const app = getApp();
const { getBaseUrl, getGatewayStatus, checkGatewayAuth, getPairedVersion } = require("../../utils/api");

Page({
  data: {
    appid: "wxad7319a4b69eec78",
    stores: [],
    pushEnabled: true,
    gatewayUrl: "",
    gatewayOnline: false,
    tokenAccepted: false,
    miniappVersion: ""
  },

  onShow() {
    // 体验版/开发版向微信查不到版本号，退回上传时内置的版本号
    const pairedVersion = getPairedVersion();
    let version = pairedVersion ? `v${pairedVersion}` : "开发版";
    try {
      const accountInfo = wx.getAccountInfoSync();
      if (accountInfo && accountInfo.miniProgram && accountInfo.miniProgram.version) {
        version = `v${accountInfo.miniProgram.version}`;
      }
    } catch (_) {}

    this.setData({
      stores: app.globalData.stores,
      pushEnabled: wx.getStorageSync("pushNotificationEnabled") !== false,
      gatewayUrl: getBaseUrl(),
      miniappVersion: version
    });
    this.checkGateway(false);
  },

  checkGateway(showToast = true) {
    if (showToast) {
      wx.showLoading({ title: "检测网关连接中..." });
    }
    getGatewayStatus().then(res => {
      if (showToast) wx.hideLoading();
      if (res.ok) {
        this.setData({ gatewayOnline: true });
        checkGatewayAuth().then(auth => this.setData({ tokenAccepted: auth.ok }));
        if (showToast) {
          wx.showToast({ title: "网关在线，数据畅通！", icon: "success" });
        }
      } else {
        this.setData({ gatewayOnline: false });
        if (showToast) {
          wx.showModal({
            title: "网关提示",
            content: "检测到未直接连通。若在体验版中，请点击小程序右上角【···】选择【开发调试】开启调试模式即可解除微信限制直连！",
            showCancel: false
          });
        }
      }
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
