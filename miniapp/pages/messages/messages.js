const app = getApp();
const { getQuestions, getMessages, getClaims } = require("../../utils/api");
const { formatTime } = require("../../utils/format");

function parseTime(val) {
  if (!val) return 0;
  return new Date(val).getTime() || 0;
}

Page({
  data: {
    activeTab: 0, // 0: 售前咨询, 1: 订单索赔, 2: 售后私信 (严格对齐桌面端)
    questions: [],
    claims: [],
    messages: [],
    pendingQuestionsCount: 0,
    pendingClaimsCount: 0,
    unreadMessagesCount: 0,
    isLoading: true
  },

  onLoad(options) {
    if (options && options.tab !== undefined) {
      this.setData({
        activeTab: Number(options.tab)
      });
    }

    // 1. 优先读取本地秒开快照，0ms 瞬间上屏
    const cachedQ = wx.getStorageSync("cached_questions_list") || [];
    const cachedC = wx.getStorageSync("cached_claims_list") || [];
    const cachedM = wx.getStorageSync("cached_messages_list") || [];

    if (cachedQ.length > 0 || cachedC.length > 0 || cachedM.length > 0) {
      this.setData({
        questions: cachedQ,
        claims: cachedC,
        messages: cachedM,
        pendingQuestionsCount: cachedQ.filter(q => !q.is_answered).length,
        pendingClaimsCount: cachedC.filter(c => !c.is_closed).length,
        unreadMessagesCount: cachedM.filter(m => m.is_unread).length,
        isLoading: false
      });
    }
  },

  onShow() {
    if (app.globalData.activeMessageTab !== undefined && app.globalData.activeMessageTab !== null) {
      this.setData({
        activeTab: Number(app.globalData.activeMessageTab)
      });
      app.globalData.activeMessageTab = null;
    }
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
      getClaims(store.id),
      getMessages(store.id)
    ]).then(([qList, cList, mList]) => {
      // 1. 售前咨询排序：未回复排前，同组内按时间倒序 (严格对齐桌面端 _sort_key)
      const sortedQ = [...qList].sort((a, b) => {
        if (a.is_answered === b.is_answered) {
          return parseTime(b.created_at) - parseTime(a.created_at);
        }
        return a.is_answered ? 1 : -1;
      }).map(q => ({
        ...q,
        created_at: formatTime(q.created_at)
      }));

      // 2. 订单索赔排序：未结案排前，同组内按时间倒序 (严格对齐桌面端)
      const sortedC = [...cList].sort((a, b) => {
        if (a.is_closed === b.is_closed) {
          return parseTime(b.created_at) - parseTime(a.created_at);
        }
        return a.is_closed ? 1 : -1;
      }).map(c => ({
        ...c,
        created_at: formatTime(c.created_at)
      }));

      // 3. 售后私信排序：未读/待回复排前，同组内按时间倒序 (严格对齐桌面端)
      const sortedM = [...mList].sort((a, b) => {
        if (a.is_unread === b.is_unread) {
          return parseTime(b.created_at) - parseTime(a.created_at);
        }
        return a.is_unread ? -1 : 1;
      }).map(m => ({
        ...m,
        created_at: formatTime(m.created_at || m.updated_at)
      }));

      const pendingQ = qList.filter(q => !q.is_answered).length;
      const pendingC = cList.filter(c => !c.is_closed).length;
      const unreadM = mList.filter(m => m.is_unread).length;

      // 持久化缓存快照
      if (sortedQ.length > 0) wx.setStorageSync("cached_questions_list", sortedQ);
      if (sortedC.length > 0) wx.setStorageSync("cached_claims_list", sortedC);
      if (sortedM.length > 0) wx.setStorageSync("cached_messages_list", sortedM);

      this.setData({
        questions: sortedQ,
        claims: sortedC,
        messages: sortedM,
        pendingQuestionsCount: pendingQ,
        pendingClaimsCount: pendingC,
        unreadMessagesCount: unreadM,
        isLoading: false
      });
      if (typeof callback === "function") callback();
    }).catch(() => {
      this.setData({ isLoading: false });
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
