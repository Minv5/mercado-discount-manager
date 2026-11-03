const { translateText, TEMPLATES } = require("../../utils/translator");
const { sendReply } = require("../../utils/api");

Page({
  data: {
    chatType: "question",
    itemData: {},
    targetLang: "es",
    targetLangName: "西班牙语",
    inputCn: "",
    translatedText: "",
    canSend: false,
    replied: false,
    repliedTextEs: "",
    repliedTextCn: "",
    showDrawer: false,
    templates: TEMPLATES,
    scrollToView: "chat-bottom-anchor"
  },

  onLoad(options) {
    const chatType = (options && options.type) || "question";
    const itemData = wx.getStorageSync("activeChatItem") || {};

    const isBrazil = (itemData.site_id || "").toUpperCase() === "MLB";
    const targetLang = isBrazil ? "pt" : "es";
    const targetLangName = isBrazil ? "葡萄牙语(巴西)" : "西班牙语(拉美)";

    let title = "买家会话回复";
    if (chatType === "question") title = "售前咨询回复";
    else if (chatType === "message") title = "售后订单沟通";
    else if (chatType === "claim") title = "售后索赔处理";

    wx.setNavigationBarTitle({ title });

    this.setData({
      chatType,
      itemData,
      targetLang,
      targetLangName
    });
  },

  onInputCn(e) {
    const val = e.detail.value;
    this.setData({
      inputCn: val,
      canSend: !!val.trim()
    });

    // 简单防抖翻译
    if (this._translateTimer) clearTimeout(this._translateTimer);
    if (!val.trim()) {
      this.setData({ translatedText: "" });
      return;
    }
    this._translateTimer = setTimeout(() => {
      this.doTranslate(val);
    }, 600);
  },

  doTranslate(text) {
    translateText(text, this.data.targetLang).then((res) => {
      this.setData({
        translatedText: res
      });
    });
  },

  translateNow() {
    if (this.data.inputCn) {
      this.doTranslate(this.data.inputCn);
    }
  },

  sendReplyAction() {
    if (!this.data.canSend) return;

    const contentToSend = this.data.translatedText || this.data.inputCn;
    wx.showLoading({ title: "正在同步发送..." });

    sendReply(this.data.itemData.id, this.data.chatType, contentToSend).then(() => {
      wx.hideLoading();
      wx.showToast({
        title: "回复成功！",
        icon: "success"
      });
      this.setData({
        replied: true,
        repliedTextEs: contentToSend,
        repliedTextCn: this.data.inputCn,
        inputCn: "",
        translatedText: "",
        canSend: false,
        scrollToView: "chat-bottom-anchor"
      });
    }).catch((err) => {
      wx.hideLoading();
      wx.showModal({
        title: "发送失败",
        content: String(err),
        showCancel: false
      });
    });
  },

  handleRefund() {
    wx.showModal({
      title: "确认同意退款？",
      content: `确定为订单 ${this.data.itemData.order_id} 办理全额退款？款项将原路退回买家。`,
      confirmColor: "#D32F2F",
      success: (res) => {
        if (res.confirm) {
          wx.showToast({ title: "已提交退款申请", icon: "success" });
        }
      }
    });
  },

  openTemplateDrawer() {
    this.setData({ showDrawer: true });
  },

  closeDrawer() {
    this.setData({ showDrawer: false });
  },

  preventProp() {},

  selectTemplate(e) {
    const tpl = e.currentTarget.dataset.tpl;
    const esOrPt = this.data.targetLang === "pt" ? tpl.pt : tpl.es;
    this.setData({
      inputCn: tpl.cn,
      translatedText: esOrPt,
      canSend: true,
      showDrawer: false
    });
    wx.showToast({
      title: "已填入话术并自动转译",
      icon: "none"
    });
  }
});
