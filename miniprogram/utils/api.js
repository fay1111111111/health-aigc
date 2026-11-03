const config = require('../config')

function base() {
  return (wx.getStorageSync('apiBase') || config.API_BASE).replace(/\/+$/, '')
}

function token() {
  return wx.getStorageSync('token') || ''
}

function logout() {
  wx.removeStorageSync('token')
  getApp().globalData.user = null
  wx.reLaunch({ url: '/pages/login/login' })
}

// 统一请求：自动带令牌；401 回到登录页；错误信息用后端返回的中文说明
function request(method, path, data) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: base() + path,
      method,
      data,
      header: { Authorization: 'Bearer ' + token(), 'Content-Type': 'application/json' },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) return resolve(res.data)
        if (res.statusCode === 401) {
          logout()
          return reject(new Error('登录已失效，请重新登录'))
        }
        const detail = res.data && res.data.detail
        const msg = typeof detail === 'string' ? detail : '请求失败（' + res.statusCode + '）'
        const err = new Error(msg)
        err.status = res.statusCode
        reject(err)
      },
      fail(e) {
        reject(new Error('连不上服务器，请检查网络和服务器地址：' + (e.errMsg || '')))
      }
    })
  })
}

module.exports = {
  base,
  logout,
  get: (path, data) => request('GET', path, data),
  post: (path, data) => request('POST', path, data),
  patch: (path, data) => request('PATCH', path, data),
  del: (path) => request('DELETE', path)
}
