/**
 * 美客多拉美双语智能翻译与常用快捷话术助手 (对标智赢核心能力)
 * 支持西班牙语 (墨西哥/智利/哥伦比亚/阿根廷) 与 葡萄牙语 (巴西)
 */

// 常用电商客服快捷模板库
const TEMPLATES = [
  {
    category: "售前咨询",
    items: [
      {
        cn: "您好！商品全为正品现货，下单后我们会第一时间为您打包发出，请放心购买。",
        es: "¡Hola! Todos los productos son 100% originales y están disponibles en stock. Realizaremos el envío de inmediato tras su compra. ¡Compre con confianza!",
        pt: "Olá! Todos os produtos são 100% originais e estão em estoque. Faremos o envio imediatamente após a compra. Compre com tranquilidade!"
      },
      {
        cn: "您好，此款商品尺码标准，建议按照您平时的日常尺码选购即可。",
        es: "¡Hola! La talla de este producto es estándar. Le sugerimos elegir la talla habitual que usa normalmente.",
        pt: "Olá! O tamanho deste produto é padrão. Recomendamos escolher o tamanho que costuma usar no dia a dia."
      },
      {
        cn: "您好，请问您具体需要哪种颜色或规格？我们随时为您确认库存。",
        es: "¡Hola! ¿Qué color o especificación necesita en particular? Con gusto verificaremos el stock para usted.",
        pt: "Olá! De qual cor ou modelo você precisa exatamente? Teremos prazer em verificar o estoque para você."
      }
    ]
  },
  {
    category: "物流查件",
    items: [
      {
        cn: "您好，您的包裹正在国际干线运输途中，通常需 7-15 个工作日送达，请您耐心等待。",
        es: "¡Hola! Su paquete se encuentra en tránsito internacional. Generalmente tarda entre 7 y 15 días hábiles en llegar. Le agradecemos su paciencia.",
        pt: "Olá! O seu pacote está em trânsito internacional. Normalmente leva de 7 a 15 dias úteis para entrega. Agradecemos sua paciência."
      },
      {
        cn: "您好，物流单号已为您更新，您可随时在平台订单页面跟踪包裹轨迹。",
        es: "¡Hola! El número de seguimiento ya ha sido actualizado. Puede rastrear su pedido en cualquier momento desde la plataforma.",
        pt: "Olá! O código de rastreamento já foi atualizado. Você pode acompanhar o envio a qualquer momento na plataforma."
      }
    ]
  },
  {
    category: "售后安抚与索赔",
    items: [
      {
        cn: "非常抱歉给您带来不好的体验！请您拍照发送商品外观与包装，我们立即为您安排补发或全额退款。",
        es: "¡Lamentamos mucho los inconvenientes causados! Por favor envíenos fotos del producto y del empaque; gestionaremos de inmediato un reenvío o reembolso total.",
        pt: "Lamentamos muito pelo inconveniente! Por favor, envie fotos do produto e da embalagem; providenciaremos imediatamente o reenvio ou reembolso total."
      },
      {
        cn: "您好，退款申请已为您在平台加急提交，款项会在 1-3 个工作日内原路退回您的支付账户。",
        es: "¡Hola! Su solicitud de reembolso ha sido aprobada con prioridad en la plataforma. El dinero volverá a su cuenta en 1-3 días hábiles.",
        pt: "Olá! Sua solicitação de reembolso foi aprovada com prioridade na plataforma. O valor retornará à sua conta em 1-3 dias úteis."
      }
    ]
  }
];

/**
 * 将中文内容翻译为西班牙语或葡萄牙语
 * @param {string} text 中文内容
 * @param {string} targetLang 'es' (西班牙语) 或 'pt' (葡萄牙语)
 * @returns {Promise<string>}
 */
function translateText(text, targetLang = "es") {
  return new Promise((resolve) => {
    const clean = (text || "").trim();
    if (!clean) {
      resolve("");
      return;
    }

    // 1. 优先在快捷字典中精确/模糊匹配
    for (const group of TEMPLATES) {
      for (const item of group.items) {
        if (item.cn === clean || clean.includes(item.cn) || item.cn.includes(clean)) {
          resolve(targetLang === "pt" ? item.pt : item.es);
          return;
        }
      }
    }

    // 2. 调用公共免密在线翻译或后端 API 代理
    const encoded = encodeURIComponent(clean);
    const to = targetLang === "pt" ? "pt" : "es";
    const url = `https://translate.googleapis.com/translate_a/single?client=gtx&sl=zh-CN&tl=${to}&dt=t&q=${encoded}`;

    wx.request({
      url,
      method: "GET",
      timeout: 5000,
      success(res) {
        try {
          if (res.data && Array.isArray(res.data[0])) {
            const translated = res.data[0].map(item => item[0]).join("");
            resolve(translated);
            return;
          }
        } catch (e) {}
        // 若第三方网络异常，给出基础礼貌兜底
        resolve(targetLang === "pt" ? `Olá! ${clean}` : `¡Hola! ${clean}`);
      },
      fail() {
        resolve(targetLang === "pt" ? `Olá! ${clean}` : `¡Hola! ${clean}`);
      }
    });
  });
}

module.exports = {
  TEMPLATES,
  translateText
};
