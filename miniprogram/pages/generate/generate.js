const api = require('../../utils/api')
const { ROLE } = require('../../utils/format')

Page({
  data: {
    user: null,
    roleText: '',
    productName: '',
    channels: [],
    audiences: [],
    types: [],
    channelIdx: -1,
    audienceIdx: -1,
    typeIdx: -1,
    brief: '',
    loading: false,
    loadError: ''
  },

  onShow() {
    if (!wx.getStorageSync('token')) return wx.reLaunch({ url: '/pages/login/login' })
    const app = getApp()
    Promise.all([app.ensureUser(), app.ensureMeta()])
      .then(([user, meta]) => {
        this.setData({
          user,
          roleText: ROLE[user.role] || user.role,
          productName: meta.product.name,
          channels: meta.channels,
          audiences: meta.audiences,
          types: meta.content_types,
          loadError: ''
        })
      })
      .catch(err => this.setData({ loadError: err.message }))
  },

  onChannel(e) { this.setData({ channelIdx: Number(e.detail.value) }) },
  onAudience(e) { this.setData({ audienceIdx: Number(e.detail.value) }) },
  onType(e) { this.setData({ typeIdx: Number(e.detail.value) }) },
  onBrief(e) { this.setData({ brief: e.detail.value }) },

  generate() {
    const d = this.data
    if (d.channelIdx < 0 || d.audienceIdx < 0 || d.typeIdx < 0) {
      wx.showToast({ title: '请选择渠道、受众和内容类型', icon: 'none' })
      return
    }
    this.setData({ loading: true })
    wx.showLoading({ title: '生成中，约 10-40 秒', mask: true })
    api.post('/api/generate', {
      channel: d.channels[d.channelIdx].id,
      audience: d.audiences[d.audienceIdx].id,
      content_type: d.types[d.typeIdx].id,
      brief: d.brief
    })
      .then(g => {
        wx.hideLoading()
        wx.navigateTo({ url: '/pages/detail/detail?id=' + g.id })
      })
      .catch(err => {
        wx.hideLoading()
        wx.showModal({ title: '生成失败', content: err.message, showCancel: false })
      })
      .finally(() => this.setData({ loading: false }))
  },

  retry() { this.onShow() },

  logout() {
    wx.showModal({
      title: '退出登录',
      content: '确定退出吗？',
      success: res => { if (res.confirm) api.logout() }
    })
  }
})
