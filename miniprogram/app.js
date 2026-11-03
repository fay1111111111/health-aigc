const api = require('./utils/api')

App({
  globalData: {
    user: null,
    meta: null
  },

  // 当前登录用户；没有缓存时向后端取
  ensureUser() {
    if (this.globalData.user) return Promise.resolve(this.globalData.user)
    return api.get('/api/me').then(u => {
      this.globalData.user = u
      return u
    })
  },

  // 渠道、受众、内容类型的中文名
  ensureMeta(force) {
    if (this.globalData.meta && !force) return Promise.resolve(this.globalData.meta)
    return api.get('/api/meta').then(m => {
      this.globalData.meta = m
      return m
    })
  }
})
