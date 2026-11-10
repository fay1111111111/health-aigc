// 命令行上传体验版（可选）。用法：
//   1. 微信公众平台 → 开发管理 → 开发设置 →「小程序代码上传」下载密钥，放到 ci/private.key（不要提交到仓库）
//      并把运行这条命令的电脑 / 服务器 IP 加入白名单
//   2. npm install
//   3. MP_APPID=wx开头的AppID npm run upload -- 0.1.0 "第一版：问答、睡眠记录、伙伴入口"
const path = require('path')
const fs = require('fs')
const ci = require('miniprogram-ci')

const appid = process.env.MP_APPID
const [version = '0.1.0', desc = '更新'] = process.argv.slice(2)
const keyPath = path.join(__dirname, 'private.key')

if (!appid) throw new Error('请先设置环境变量 MP_APPID')
if (!fs.existsSync(keyPath)) throw new Error('找不到 ci/private.key，请先在微信公众平台下载上传密钥')

const config = require('../src/config')
if (!/^https:\/\//.test(config.API_BASE)) {
  throw new Error('src/config.js 里的 API_BASE 还不是 HTTPS 域名，体验版和正式版都连不上，先改好再上传')
}

const project = new ci.Project({
  appid,
  type: 'miniProgram',
  projectPath: path.join(__dirname, '..'),
  privateKeyPath: keyPath,
  ignores: ['node_modules/**/*', 'ci/**/*']
})

ci.upload({ project, version, desc, setting: { es6: true, minify: true }, onProgressUpdate: () => {} })
  .then(() => console.log(`已上传 ${version}，到微信公众平台「版本管理」里设为体验版`))
  .catch((e) => { console.error('上传失败：', e.message || e); process.exit(1) })
