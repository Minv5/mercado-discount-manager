const app = getApp();
const { getQuestions, getMessages, getClaims } = require("../../utils/api");
const { formatTime } = require("../../utils/format");

Page({
  data: {
    activeTab: 0,
    questions: [],
    messages: [],
    claims: []
  },

  onLoad(options) {
    if (options && options.tab) {
      this.setData({
        activeTab: Number(options.tab)
      });
    }
  },

  onShow() {
    this.loadData();
  },

  onPullDownRefresh() {
    this.loadData(() => {
      wx.stopPullDownRefresh();
    });
  },

  onTabChange(e) {
    const tab = Number(e.currentTarget.dataset.tab);
    this.setData({ activeTab: tab });
  },

  loadData(callback) {
    const store = app.getCurrentStore();
    Promise.all([
      getQuestions(store.id),
      getMessages(store.id),
      getClaims(store.id)
    ]).then(([qList, mList, cList]) => {
      const formattedQ = qList.map(q => ({
        ...q,
        created_at: formatTime(q.created_at)
      }));
      const formattedM = mList.map(m => ({
        ...m,
        updated_at: formatTime(m.updated_at)
      }));
      this.setData({
        questions: formattedQ,
        messages: formattedM,
        claims: cList
      });
      if (typeof callback === "function") callback();
    }).catch(() => {
      if (typeof callback === "function") callback();
    });
  },

  openChat(e) {
    const type = e.currentTarget.dataset.type;
    const item = e.currentTarget.dataset.item;
    wx.setStorageSync("activeChatItem", item);
    wx.navigateTo({
      url: `/pages/chat/chat?type=${type}&id=${item.id}`
    });
  }
});
