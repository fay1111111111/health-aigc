const api = require('./utils/api')

App({
  globalData: {
    user: null,
    pendingAsk: null // 从其他页面带到问答页的问题：{ message, code, scene }
  },

  onLaunch() {
    this.ready = this.login()
    this.ready.catch(() => {}) // 页面自己会处理失败，这里只是避免未捕获的报错
  },

  // 微信静默登录：拿 code 换后端令牌。第一次进入会自动建账号，注册页再补身份和昵称。
  login() {
    return api.login().then((user) => {
      this.globalData.user = user
      return user
    })
  },

  // 页面 onShow 时调用：保证已登录；失败后下次再进会重试
  ensureUser() {
    return this.ready.catch(() => {
      this.ready = this.login()
      return this.ready
    }).then(() => this.globalData.user) // 返回最新的资料（注册、改资料后会更新），而不是登录那一刻的
  },

  setUser(user) {
    this.globalData.user = user
  }
})
