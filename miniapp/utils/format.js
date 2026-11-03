/**
 * 数据与业务展示格式化辅助工具
 */

const SITE_NAMES = {
  MLM: "墨西哥",
  MLB: "巴西",
  MLC: "智利",
  MCO: "哥伦比亚",
  MLA: "阿根廷",
  MLU: "乌拉圭"
};

const SITE_CURRENCY = {
  MLM: "MXN $",
  MLB: "R$",
  MLC: "CLP $",
  MCO: "COP $",
  MLA: "ARS $",
  MLU: "UYU $"
};

function formatSiteName(siteId) {
  const code = (siteId || "").toUpperCase();
  return SITE_NAMES[code] ? `${SITE_NAMES[code]} (${code})` : (code || "全站点");
}

function formatCurrency(amount, siteId = "") {
  const num = Number(amount) || 0;
  const prefix = SITE_CURRENCY[(siteId || "").toUpperCase()] || "$";
  return `${prefix} ${num.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatTime(isoStr) {
  if (!isoStr) return "";
  const d = new Date(isoStr);
  if (isNaN(d.getTime())) return isoStr;
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  const h = String(d.getHours()).padStart(2, "0");
  const min = String(d.getMinutes()).padStart(2, "0");
  return `${m}-${day} ${h}:${min}`;
}

module.exports = {
  formatSiteName,
  formatCurrency,
  formatTime
};
