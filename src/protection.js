export function realSubmitProtection(body, { batch = false } = {}) {
  if (body.mode !== 'real' || body.confirmText !== 'REAL_SUBMIT') {
    return {
      allowed: false,
      status: 400,
      error: batch
        ? '批量真实提交被保护：必须先由主管确认影响范围，再输入 REAL_SUBMIT。'
      : '真实提交被保护：必须选择真实模式并输入确认文本 REAL_SUBMIT。测试模式请使用生成计划。'
    };
  }
  return {
    allowed: true,
    status: 200,
    message: batch
      ? 'REAL_SUBMIT 已确认，允许执行批量真实提交。'
      : 'REAL_SUBMIT 已确认，允许执行单活动真实提交。'
  };
}
