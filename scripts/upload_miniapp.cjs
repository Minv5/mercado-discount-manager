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
  console.log('>>> 正在通过微信官方 CI 上传黑金暗黑版体验包...');
  const uploadResult = await ci.upload({
    project,
    version: '1.1.0',
    desc: '黑金墨绿暗黑版移动工作台',
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
  console.log('>>> 上传成功！', uploadResult);
}

run().catch((err) => {
  console.error('>>> 上传出现错误：', err);
  process.exit(1);
});
