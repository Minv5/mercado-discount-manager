/**
 * 数据与业务展示格式化辅助工具
 * 美客多跨境店铺（CBT）官方统一采用美元（USD）结算与展示
 */

const SITE_NAMES = {
  MLM: "墨西哥",
  MLB: "巴西",
  MLC: "智利",
  MCO: "哥伦比亚",
  MLA: "阿根廷",
  MLU: "乌拉圭"
};

function formatSiteName(siteId) {
  const code = (siteId || "").toUpperCase();
  return SITE_NAMES[code] ? `${SITE_NAMES[code]} (${code})` : (code || "全站点");
}

function formatCurrency(amount) {
  const num = Number(amount) || 0;
  return `$ ${num.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
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

