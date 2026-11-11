const api = require('../../utils/api')
const { shortTime } = require('../../utils/util')

const STATUS = { new: '待联系', contacted: '已联系', closed: '已结束' }

Page({
  data: { questions: [], lead: null, error: '' },

  onShow() {
    this.load()
  },

  load() {
    const app = getApp()
    app.ensureUser().then((user) => {
      if (!user.registered) return wx.reLaunch({ url: '/pages/welcome/welcome' })
      return Promise.all([api.get('/api/c/home', { scene: 'partner' }), api.get('/api/c/leads')]).then(([home, leads]) => {
        const latest = leads[0]
        this.setData({
          error: '',
          questions: home.categories[0] ? home.categories[0].questions : [],
          lead: latest ? {
            statusText: STATUS[latest.status] || '待联系',
            time: shortTime(latest.created_at)
          } : null
        })
      })
    }).catch((err) => this.setData({ error: err.message }))
  },

  // 带着问题去「问答」页
  ask(e) {
    const { code, question } = e.currentTarget.dataset
    getApp().globalData.pendingAsk = { message: question, code, scene: 'partner' }
    wx.switchTab({ url: '/pages/chat/chat' })
  },

  openLead() {
    wx.navigateTo({ url: '/pages/lead/lead' })
  }
})
