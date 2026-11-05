const app = getApp();
const { getQuestions, getMessages, getClaims } = require("../../utils/api");
const { formatTime } = require("../../utils/format");

Page({
  data: {
    activeTab: 0,
    questions: [],
    messages: [],
    claims: [],
    pendingQuestionsCount: 0,
    unreadMessagesCount: 0,
    pendingClaimsCount: 0
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
      // 1. 售前咨询：未回复排在最前
      const sortedQ = [...qList].sort((a, b) => (a.is_answered === b.is_answered ? 0 : (a.is_answered ? 1 : -1)));
      const formattedQ = sortedQ.map(q => ({
        ...q,
        created_at: formatTime(q.created_at)
      }));

      // 2. 售后私信
      const formattedM = mList.map(m => ({
        ...m,
        updated_at: formatTime(m.updated_at)
      }));

      // 3. 索赔纠纷：进行中紧急排前，已结案归档排后
      const sortedC = [...cList].sort((a, b) => (a.is_closed === b.is_closed ? 0 : (a.is_closed ? 1 : -1)));

      const pendingQ = qList.filter(q => !q.is_answered).length;
      const unreadM = mList.reduce((acc, m) => acc + (m.unread_count || 0), 0);
      const pendingC = cList.filter(c => !c.is_closed).length;

      this.setData({
        questions: formattedQ,
        messages: formattedM,
        claims: sortedC,
        pendingQuestionsCount: pendingQ,
        unreadMessagesCount: unreadM,
        pendingClaimsCount: pendingC
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
