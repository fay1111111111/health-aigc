const api = require('../../utils/api')

const RISK = { low: '低风险', mid: '中风险', high: '高风险' }

Page({
  data: { q: '', items: [], loading: false, error: '', denied: false },

  onShow() {
    if (!wx.getStorageSync('token')) return wx.reLaunch({ url: '/pages/login/login' })
    getApp().ensureUser().then(u => {
      const denied = u.role === 'operator'
      this.setData({ denied })
      if (!denied && this.data.items.length === 0) this.search()
    })
  },

  onInput(e) { this.setData({ q: e.detail.value }) },

  search() {
    this.setData({ loading: true })
    api.get('/api/knowledge', this.data.q.trim() ? { q: this.data.q.trim() } : {})
      .then(rows => this.setData({
        items: rows.map(c => Object.assign({}, c, { riskText: RISK[c.risk_level] || c.risk_level })),
        error: ''
      }))
      .catch(err => this.setData({ error: err.message }))
      .finally(() => this.setData({ loading: false }))
  },

  clear() {
    this.setData({ q: '' })
    this.search()
  }
})
