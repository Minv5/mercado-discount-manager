const ci = require('miniprogram-ci');
const fs = require('fs');
const os = require('os');
const path = require('path');

const MINIAPP_VERSION = '1.2.12';
const PAIRING_FILE = path.resolve(__dirname, '../miniapp/utils/desktop_pairing.js');

// 与电脑端 mobile_gateway.get_access_token 同源：环境变量优先，其次电脑端数据目录里的口令文件
function readDesktopAccessToken() {
  const envToken = String(process.env.MDM_GATEWAY_TOKEN || '').trim();
  if (envToken) return envToken;
  let dataDir = process.env.MDM_DATA_DIR;
  if (!dataDir) {
    const base = process.platform === 'darwin'
      ? path.join(os.homedir(), 'Library', 'Application Support')
      : process.platform === 'win32'
        ? (process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'))
        : path.join(os.homedir(), '.local', 'share');
    dataDir = path.join(base, 'MercadoDiscountManagerStandalone', 'data');
  }
  try {
    return fs.readFileSync(path.join(dataDir, 'mobile_gateway_token.txt'), 'utf8').trim();
  } catch (_) {
    return '';
  }
}

// 只在上传期间生成配对文件，上传结束立即删除，口令不会留在项目源码里
function writePairingFile(token) {
  fs.writeFileSync(
    PAIRING_FILE,
    `module.exports = ${JSON.stringify({ accessToken: token, version: MINIAPP_VERSION })};\n`,
    'utf8'
  );
}

const project = new ci.Project({
  appid: 'wxad7319a4b69eec78',
  type: 'miniProgram',
  projectPath: path.resolve(__dirname, '../miniapp'),
  privateKeyPath: '/Users/minv5/Downloads/图片/private.wxad7319a4b69eec78.key',
  ignores: ['node_modules/**/*', '.git/**/*'],
});

async function run() {
  const token = readDesktopAccessToken();
  if (!token) {
    throw new Error('没有找到电脑端访问口令，请先打开一次电脑端软件（会自动生成口令）后再上传。');
  }
  writePairingFile(token);
  try {
    await uploadAndPreview();
  } finally {
    fs.rmSync(PAIRING_FILE, { force: true });
  }
}

async function uploadAndPreview() {
  console.log(`>>> 正在通过微信官方 CI 上传体验版 (v${MINIAPP_VERSION})...`);
  const uploadResult = await ci.upload({
    project,
    version: MINIAPP_VERSION,
    desc: '自动与电脑端认证连接，无需手动填写口令',
    setting: {
      es6: true,
      es7: true,
      minify: true,
      codeProtect: false,
      autoPrefixWXSS: true,
    },
    onProgressUpdate: (msg) => {
      if (typeof msg === 'string') {
        console.log('[CI 进度]', msg);
      } else if (msg && msg.message) {
        console.log('[CI 进度]', msg.message);
      }
    },
  });
  console.log('>>> 体验版上传成功！', uploadResult);

  console.log('>>> 正在生成最新真机扫码预览码至桌面...');
  const previewResult = await ci.preview({
    project,
    version: MINIAPP_VERSION,
    desc: '真机实时预览码',
    setting: { es6: true, minify: true },
    qrcodeFormat: 'image',
    qrcodeOutputDest: '/Users/minv5/Desktop/小程序黑金版真机预览码.jpg',
    onProgressUpdate: (msg) => {
      if (typeof msg === 'string') console.log('[Preview 进度]', msg);
    }
  });
  console.log('>>> 真机预览码已生成并落盘至桌面！');
}

run().catch((err) => {
  console.error('>>> 上传出现错误：', err && err.message ? err.message : err);
  process.exit(1);
});
