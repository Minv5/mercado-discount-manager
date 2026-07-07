export const PROMOTION_CREATION_STATUS = {
  supported: true,
  status: 'seller_campaign_preview_supported_real_write_blocked',
  uiLabel: '支持 Seller Campaign 创建预检',
  summary: '官方文档已确认可通过 marketplace seller-promotions API 创建 SELLER_CAMPAIGN 自建活动；当前程序只生成请求预览和主管确认包，不执行真实创建。',
  canPreviewDraft: true,
  canRealCreate: false,
  writeProtection: '创建活动属于外部写入；本轮即使参数完整也只返回 409 预检包，必须等待主管最终确认后才可另行放行。',
  createEndpoint: {
    method: 'POST',
    path: '/marketplace/seller-promotions/seller-campaign/{USER_ID}',
    headers: ['version:v2'],
    body: ['promotion_type=SELLER_CAMPAIGN', 'name', 'sub_type=FLEXIBLE_PERCENTAGE', 'start_date', 'finish_date'],
    maxDurationDays: 14
  },
  officialEvidence: [
    {
      title: 'Mercado Libre Global Selling Devsite - Seller Campaign',
      url: 'https://global-selling.mercadolibre.com/devsite/seller-campaign',
      conclusion: '主管线程已重新读取官方文档，确认 SELLER_CAMPAIGN 可通过 marketplace seller-promotions seller-campaign API 创建；最大周期 14 天。'
    },
    {
      title: 'Mercado Libre Developers - DEAL seller-promotions',
      url: 'https://developers.mercadolibre.com.ar/productos-recibe-notificaciones/deals',
      conclusion: 'DEAL 文档继续作为已有官方活动商品报名、更新、取消的依据。'
    },
    {
      title: '当前开发环境访问状态',
      url: 'https://global-selling.mercadolibre.com/devsite/seller-campaign',
      conclusion: '本线程直接访问官方页面返回 403，因此实现按主管线程已读取并下发的官方口径落地。'
    }
  ],
  nextSteps: [
    '在页面填写站点、child_user_id、活动名称、开始和结束时间。',
    '点击“创建活动预检”生成 request preview 和 409 主管确认包。',
    '真实创建前仍需主管按账号、站点、child、名称、时间范围再次确认。'
  ]
};
