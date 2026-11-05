const ci = require('miniprogram-ci');
const path = require('path');

const project = new ci.Project({
  appid: 'wxad7319a4b69eec78',
  type: 'miniProgram',
  projectPath: path.resolve(__dirname, '../miniapp'),
  privateKeyPath: '/Users/minv5/Downloads/图片/private.wxad7319a4b69eec78.key',
  ignores: ['node_modules/**/*', '.git/**/*'],
});

async function run() {
  console.log('>>> 正在通过微信官方 CI 上传体验版 (v1.2.2)...');
  const uploadResult = await ci.upload({
    project,
    version: '1.2.2',
    desc: '索赔元数据整合至顶部3行-彻底移除中间大卡空出聊天流',
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
    version: '1.2.2',
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
  console.error('>>> 上传出现错误：', err);
  process.exit(1);
});
