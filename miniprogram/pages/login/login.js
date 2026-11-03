const api = require('../../utils/api')
const { ROLE } = require('../../utils/format')

Page({
  data: { apiBase: '', token: '', loading: false },

  onLoad() {
    // 已登录过就直接进入
    if (wx.getStorageSync('token')) {
      getApp().ensureUser()
        .then(() => wx.switchTab({ url: '/pages/generate/generate' }))
        .catch(() => this.setData({ apiBase: api.base() }))
      return
    }
    this.setData({ apiBase: api.base() })
  },

  onBase(e) { this.setData({ apiBase: e.detail.value.trim() }) },
  onToken(e) { this.setData({ token: e.detail.value.trim() }) },

  login() {
    const { apiBase, token } = this.data
    if (!apiBase || !token) {
      wx.showToast({ title: '请填写服务器地址和令牌', icon: 'none' })
      return
    }
    wx.setStorageSync('apiBase', apiBase)
    wx.setStorageSync('token', token)
    this.setData({ loading: true })
    const app = getApp()
    app.globalData.user = null
    app.globalData.meta = null
    app.ensureUser()
      .then(u => {
        wx.showToast({ title: `欢迎，${u.name}（${ROLE[u.role] || u.role}）`, icon: 'none' })
        wx.switchTab({ url: '/pages/generate/generate' })
      })
      .catch(err => {
        wx.removeStorageSync('token')
        wx.showModal({ title: '登录失败', content: err.message, showCancel: false })
      })
      .finally(() => this.setData({ loading: false }))
  }
})
