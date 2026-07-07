export function parseOAuthCallbackInput(value, explicitState = null, resolveStateForCodeOnly = null) {
  const textValue = String(value || '').trim();
  const textState = String(explicitState || '').trim();
  if (!textValue) {
    const error = new Error('请粘贴 Mercado 授权后的回调地址或授权 code');
    error.status = 400;
    throw error;
  }
  if (!textValue.includes('://') && !textValue.includes('=') && !textValue.includes('?')) {
    if (textState) return { code: textValue, state: textState };
    if (typeof resolveStateForCodeOnly === 'function') {
      const resolvedState = resolveStateForCodeOnly();
      if (resolvedState) return { code: textValue, state: resolvedState };
    }
    const error = new Error('缺少 state。请重新点击“新增账号授权”，授权后优先粘贴浏览器地址栏完整回调链接；如果只复制到 code，请粘贴刚刚获得的 code。');
    error.status = 400;
    throw error;
  }
  const target = textValue.includes('://') ? textValue : `http://localhost/callback?${textValue.replace(/^\?/, '')}`;
  const parsed = new URL(target);
  const hashParams = new URLSearchParams(parsed.hash.replace(/^#/, ''));
  const params = parsed.searchParams.size ? parsed.searchParams : hashParams;
  const errorCode = params.get('error');
  if (errorCode) {
    const errorDescription = params.get('error_description') || '';
    const error = new Error(`Mercado 授权失败：${errorCode} ${errorDescription}`.trim());
    error.status = 400;
    throw error;
  }
  const code = params.get('code');
  const state = params.get('state') || textState;
  if (!code || !state) {
    const error = new Error('授权回调中缺少 code 或 state');
    error.status = 400;
    throw error;
  }
  return { code, state };
}

export function selectCodeOnlyOAuthState(pendingStates) {
  if (pendingStates.length === 1) return pendingStates[0].state;
  const error = new Error(
    pendingStates.length > 1
      ? '检测到多条未完成授权记录。为避免匹配错误，请粘贴浏览器地址栏完整回调链接。'
      : '没有找到可匹配的未完成授权记录。请重新点击“新增账号授权”，授权后粘贴完整回调链接或刚刚获得的 code。'
  );
  error.status = 400;
  throw error;
}
