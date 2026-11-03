/**
 * 小程序网络请求与统一 API 交互模块
 * 内置美客多真实业务仿真模型 (开箱即用体验)
 */

const app = getApp();

// 真实仿真数据源
const MOCK_QUESTIONS = [
  {
    id: "Q1001",
    store_id: "3332096437",
    store_name: "广东店",
    site_id: "MLM",
    item_id: "MLM289102834",
    item_title: "Funda Para Teléfono De Alta Protección Anticaída Militar",
    item_thumbnail: "https://http2.mlstatic.com/D_NQ_NP_2X_841285-MLM43849301298_102020-F.webp",
    buyer_id: "BUYER_9982",
    buyer_name: "Carlos Mendoza",
    text_es: "¿Tiene disponible en color negro para envío inmediato?",
    text_cn: "请问黑色有现货可以立即发出吗？",
    status: "UNANSWERED",
    created_at: "2026-11-03T10:15:00Z"
  },
  {
    id: "Q1002",
    store_id: "2651442567",
    store_name: "湖北店",
    site_id: "MLB",
    item_id: "MLB398102911",
    item_title: "Relógio Inteligente Smartwatch À Prova D'água Bluetooth",
    item_thumbnail: "https://http2.mlstatic.com/D_NQ_NP_2X_720192-MLB49201928374_022022-F.webp",
    buyer_id: "BUYER_7721",
    buyer_name: "Lucas Silva",
    text_es: "Funciona tanto com Android quanto com iPhone?",
    text_cn: "请问安卓和苹果手机都可以用吗？",
    status: "UNANSWERED",
    created_at: "2026-11-03T09:30:00Z"
  },
  {
    id: "Q1003",
    store_id: "3408885754",
    store_name: "湖南店",
    site_id: "MLC",
    item_id: "MLC192837465",
    item_title: "Auriculares Inalámbricos Bluetooth Con Cancelación De Ruido",
    item_thumbnail: "https://http2.mlstatic.com/D_NQ_NP_2X_910293-MLC50192837465_052022-F.webp",
    buyer_id: "BUYER_4412",
    buyer_name: "Valentina Gomez",
    text_es: "¿Cuánto tiempo dura la batería con uso continuo?",
    text_cn: "连续使用的话电池大概能用多久？",
    status: "UNANSWERED",
    created_at: "2026-11-03T08:45:00Z"
  }
];

const MOCK_MESSAGES = [
  {
    id: "MSG2001",
    store_id: "3332096437",
    store_name: "广东店",
    site_id: "MLM",
    order_id: "200000918273645",
    buyer_name: "Alejandro Ruiz",
    last_message: "Hola, ¿podrían indicarme cuándo llegará mi paquete?",
    last_message_cn: "你好，请问我的包裹预计什么时候能到？",
    unread_count: 2,
    updated_at: "2026-11-03T11:20:00Z",
    history: [
      { sender: "buyer", text: "Hola, ya realicé el pago de mi compra.", text_cn: "你好，我已经付款了。", time: "11-02 18:20" },
      { sender: "seller", text: "¡Hola! Gracias por su compra, prepararemos su pedido hoy mismo.", text_cn: "感谢您的购买，我们今天就会打包发出。", time: "11-02 18:30" },
      { sender: "buyer", text: "Hola, ¿podrían indicarme cuándo llegará mi paquete?", text_cn: "你好，请问我的包裹预计什么时候能到？", time: "11-03 11:20" }
    ]
  },
  {
    id: "MSG2002",
    store_id: "2651442567",
    store_name: "湖北店",
    site_id: "MLB",
    order_id: "200000827163542",
    buyer_name: "Mariana Santos",
    last_message: "Recebi o produto hoje, tudo perfeito! Muito obrigado.",
    last_message_cn: "今天收到包裹了，包装非常完好！十分感谢。",
    unread_count: 1,
    updated_at: "2026-11-03T09:10:00Z",
    history: [
      { sender: "buyer", text: "Recebi o produto hoje, tudo perfeito! Muito obrigado.", text_cn: "今天收到包裹了，包装非常完好！十分感谢。", time: "11-03 09:10" }
    ]
  }
];

const MOCK_CLAIMS = [
  {
    id: "CLM5001",
    store_id: "3332096437",
    store_name: "广东店",
    site_id: "MLM",
    order_id: "200000789123456",
    claim_id: "5281920391",
    stage: "dispute",
    reason: "买家投诉：收到外包装破损，商品缺失配件",
    deadline_hours: 6,
    amount: "MXN $459.00",
    created_at: "2026-11-03T06:00:00Z"
  }
];

const MOCK_ORDERS = [
  {
    order_id: "200000918273645",
    store_id: "3332096437",
    store_name: "广东店",
    site_id: "MLM",
    status: "paid",
    status_text: "待打单发货",
    item_title: "Funda Para Teléfono De Alta Protección Anticaída Militar",
    item_thumbnail: "https://http2.mlstatic.com/D_NQ_NP_2X_841285-MLM43849301298_102020-F.webp",
    quantity: 2,
    total_amount: 398.00,
    buyer_name: "Alejandro Ruiz",
    shipping_city: "Ciudad de México",
    created_at: "2026-11-03T11:05:00Z"
  },
  {
    order_id: "200000827163542",
    store_id: "2651442567",
    store_name: "湖北店",
    site_id: "MLB",
    status: "shipped",
    status_text: "国际干线运输中",
    item_title: "Relógio Inteligente Smartwatch À Prova D'água Bluetooth",
    item_thumbnail: "https://http2.mlstatic.com/D_NQ_NP_2X_720192-MLB49201928374_022022-F.webp",
    quantity: 1,
    total_amount: 189.90,
    buyer_name: "Mariana Santos",
    shipping_city: "São Paulo",
    created_at: "2026-11-02T16:20:00Z"
  },
  {
    order_id: "200000789123456",
    store_id: "3408885754",
    store_name: "湖南店",
    site_id: "MLC",
    status: "paid",
    status_text: "待打单发货",
    item_title: "Auriculares Inalámbricos Bluetooth Con Cancelación De Ruido",
    item_thumbnail: "https://http2.mlstatic.com/D_NQ_NP_2X_910293-MLC50192837465_052022-F.webp",
    quantity: 1,
    total_amount: 24990.00,
    buyer_name: "Valentina Gomez",
    shipping_city: "Santiago",
    created_at: "2026-11-03T07:15:00Z"
  }
];

function getQuestions(storeId) {
  return new Promise((resolve) => {
    setTimeout(() => {
      if (!storeId) {
        resolve(MOCK_QUESTIONS);
      } else {
        resolve(MOCK_QUESTIONS.filter(q => q.store_id === storeId));
      }
    }, 150);
  });
}

function getMessages(storeId) {
  return new Promise((resolve) => {
    setTimeout(() => {
      if (!storeId) {
        resolve(MOCK_MESSAGES);
      } else {
        resolve(MOCK_MESSAGES.filter(m => m.store_id === storeId));
      }
    }, 150);
  });
}

function getClaims(storeId) {
  return new Promise((resolve) => {
    setTimeout(() => {
      if (!storeId) {
        resolve(MOCK_CLAIMS);
      } else {
        resolve(MOCK_CLAIMS.filter(c => c.store_id === storeId));
      }
    }, 150);
  });
}

function getOrders(storeId, status) {
  return new Promise((resolve) => {
    setTimeout(() => {
      let list = MOCK_ORDERS;
      if (storeId) {
        list = list.filter(o => o.store_id === storeId);
      }
      if (status && status !== "all") {
        list = list.filter(o => o.status === status);
      }
      resolve(list);
    }, 150);
  });
}

function sendReply(targetId, type, contentText) {
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve({ ok: true, msg: "回复已成功发送至美客多官方平台！" });
    }, 300);
  });
}

module.exports = {
  getQuestions,
  getMessages,
  getClaims,
  getOrders,
  sendReply
};
