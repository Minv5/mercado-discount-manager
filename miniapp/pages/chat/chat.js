const { translateText } = require("../../utils/translator");
const { sendReply, getSessionDetail } = require("../../utils/api");

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
    scrollToView: "chat-bottom-anchor"
  },

  onLoad(options) {
    const chatType = (options && options.type) || "question";
    const sessionId = (options && options.id) || "";
    let itemData = wx.getStorageSync("activeChatItem") || {};

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

    // 权威拉取最新会话详情（包含全部双方历史记录与官方最新消息）
    const targetId = itemData.id || sessionId;
    if (targetId) {
      getSessionDetail(targetId, chatType).then(detail => {
        if (detail && detail.id) {
          const detailBrazil = (detail.site_id || "").toUpperCase() === "MLB";
          const current = this.data.itemData || {};
          const merged = {
            ...current,
            ...detail,
            item_title: (detail.item_title && detail.item_title !== "美客多订单商品") ? detail.item_title : (current.item_title || detail.item_title),
            item_thumbnail: (detail.item_thumbnail && !detail.item_thumbnail.includes("default_product")) ? detail.item_thumbnail : (current.item_thumbnail || detail.item_thumbnail)
          };
          this.setData({
            itemData: merged,
            targetLang: detailBrazil ? "pt" : "es",
            targetLangName: detailBrazil ? "葡萄牙语(巴西)" : "西班牙语(拉美)"
          });
        }
      });
    }
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

      const newHistory = [...(this.data.itemData.history || [])];
      if (this.data.chatType === "message" || this.data.chatType === "claim") {
        newHistory.push({
          sender: "seller",
          sender_name: "我 (商家)",
          text: contentToSend,
          text_cn: this.data.inputCn,
          time: "刚刚"
        });
      }

      const updatedItemData = { ...this.data.itemData, history: newHistory };
      this.setData({
        itemData: updatedItemData,
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
  }
});
