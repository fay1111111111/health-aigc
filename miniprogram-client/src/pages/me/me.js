const api = require('../../utils/api')
const config = require('../../config')
const { ROLE_NAMES, toast, confirm, duration } = require('../../utils/util')

Page({
  data: {
    user: {},
    avatar: '',
    initial: '沁',
    roleName: '',
    week: [],
    avgText: '',
    appName: config.APP_NAME,
    version: 'v' + config.VERSION
  },

  onShow() {
    const app = getApp()
    app.ensureUser().then(() => api.get('/api/c/me')).then((user) => {
      if (!user.registered) return wx.reLaunch({ url: '/pages/welcome/welcome' })
      app.setUser(user)
      this.showUser(user)
      return this.loadSleep(user)
    }).catch((err) => toast(err.message))
  },

  showUser(user) {
    this.setData({
      user,
      avatar: api.fileUrl(user.avatar_url),
      initial: (user.nickname || '沁').slice(0, 1),
      roleName: ROLE_NAMES[user.role] || ''
    })
  },

  loadSleep(user) {
    if (!user.health_consent) return this.setData({ week: [], avgText: '' })
    return api.get('/api/c/sleep-records', { limit: 7 }).then((rows) => {
      const list = rows.slice().reverse() // 从早到晚
      const longest = Math.max(1, ...list.map((r) => r.minutes))
      const total = list.reduce((sum, r) => sum + r.minutes, 0)
      this.setData({
        avgText: list.length ? duration(Math.round(total / list.length)) : '',
        week: list.map((r) => ({
          day: r.day,
          label: Number(r.day.slice(5, 7)) + '/' + Number(r.day.slice(8, 10)),
          height: Math.round((r.minutes / longest) * 100),
          opacity: 0.35 + r.quality * 0.13 // 睡得越好颜色越实
        }))
      })
    })
  },

  openProfile() {
    wx.navigateTo({ url: '/pages/profile/profile' })
  },

  openSleep() {
    wx.navigateTo({ url: '/pages/sleep/sleep' })
  },

  openAssess(e) {
    wx.navigateTo({ url: '/pages/assess/assess?kind=' + e.currentTarget.dataset.kind })
  },

  openLegal(e) {
    wx.navigateTo({ url: '/pages/legal/legal?type=' + e.currentTarget.dataset.type })
  },

  toggleHealth(e) {
    const agree = e.detail.value
    const apply = () => api.post('/api/c/me/health-consent', { agree }).then((user) => {
      getApp().setUser(user)
      this.showUser(user)
      return this.loadSleep(user)
    })
    const revert = () => this.setData({ 'user.health_consent': !agree })
    if (agree) return apply().catch((err) => { revert(); toast(err.message) })
    confirm('关闭并删除记录？', '关闭后，已保存的睡眠记录和体验自评会被全部删除，无法恢复。', '关闭并删除').then((ok) => {
      if (!ok) return revert()
      return apply().then(() => toast('已关闭，记录已删除'))
    }).catch((err) => { revert(); toast(err.message) })
  },

  clearChat() {
    confirm('清空对话记录？', '清空后无法恢复。', '清空').then((ok) => {
      if (!ok) return
      return api.del('/api/c/chat/history').then(() => {
        toast('已清空')
        const pages = getCurrentPages()
        // 问答页如果还在后台，让它下次显示时重新加载
        pages.forEach((p) => { if (p.route === 'pages/chat/chat') p.loaded = false })
      })
    }).catch((err) => toast(err.message))
  },

  deleteAccount() {
    confirm('注销账号？', '你的资料、对话、睡眠记录、自评和意向登记都会被删除，无法恢复。', '注销').then((ok) => {
      if (!ok) return
      return api.del('/api/c/me').then(() => {
        wx.removeStorageSync('token')
        const app = getApp()
        app.setUser(null)
        app.ready = app.login()
        app.ready.catch(() => {})
        wx.reLaunch({ url: '/pages/welcome/welcome' })
      })
    }).catch((err) => toast(err.message))
  }
})
