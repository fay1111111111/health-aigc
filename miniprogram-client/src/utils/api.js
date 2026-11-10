const config = require('../config')

const BASE = config.API_BASE.replace(/\/+$/, '')

function token() {
  return wx.getStorageSync('token') || ''
}

function wxLogin() {
  return new Promise((resolve, reject) => {
    wx.login({
      success: (res) => (res.code ? resolve(res.code) : reject(new Error('微信登录失败，请稍后重试'))),
      fail: () => reject(new Error('微信登录失败，请稍后重试'))
    })
  })
}

function raw(method, path, data) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: BASE + path,
      method,
      data,
      header: { Authorization: 'Bearer ' + token(), 'Content-Type': 'application/json' },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) return resolve(res.data)
        reject(toError(res.statusCode, res.data))
      },
      fail() {
        reject(new Error('网络不太顺畅，请稍后再试'))
      }
    })
  })
}

function toError(status, body) {
  const detail = body && body.detail
  const err = new Error(typeof detail === 'string' ? detail : '出了点问题，请稍后再试')
  err.status = status
  return err
}

let loggingIn = null

// 用微信 code 换令牌；同时发起多次时只登录一次
function login() {
  if (!loggingIn) {
    loggingIn = wxLogin()
      .then((code) => raw('POST', '/api/c/login', { code }))
      .then((res) => {
        wx.setStorageSync('token', res.token)
        return res.user
      })
      .finally(() => { loggingIn = null })
  }
  return loggingIn
}

// 统一请求：令牌过期时自动重新登录一次再重试
function request(method, path, data) {
  return raw(method, path, data).catch((err) => {
    if (err.status !== 401) throw err
    return login().then(() => raw(method, path, data))
  })
}

function upload(path, filePath) {
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: BASE + path,
      filePath,
      name: 'file',
      header: { Authorization: 'Bearer ' + token() },
      success(res) {
        let body = null
        try { body = JSON.parse(res.data) } catch (e) { /* 不是 JSON 就按失败处理 */ }
        if (res.statusCode >= 200 && res.statusCode < 300 && body) return resolve(body)
        reject(toError(res.statusCode, body))
      },
      fail() {
        reject(new Error('上传失败，请稍后再试'))
      }
    })
  })
}

// 后端返回的头像是相对路径，这里补成完整地址
function fileUrl(path) {
  if (!path) return ''
  return /^https?:\/\//.test(path) ? path : BASE + path
}

module.exports = {
  login,
  upload,
  fileUrl,
  get: (path, data) => request('GET', path, data),
  post: (path, data) => request('POST', path, data),
  patch: (path, data) => request('PATCH', path, data),
  del: (path) => request('DELETE', path)
}
