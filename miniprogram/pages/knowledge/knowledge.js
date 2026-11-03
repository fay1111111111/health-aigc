const api = require('../../utils/api')

const RISK = { low: '低风险', mid: '中风险', high: '高风险' }
const TABS = [
  { key: 'approved', text: '已入库' },
  { key: 'pending', text: '待审核' }
]

Page({
  data: { tabs: TABS, active: 0, q: '', items: [], stats: '', loading: false, error: '', denied: false },

  onShow() {
    if (!wx.getStorageSync('token')) return wx.reLaunch({ url: '/pages/login/login' })
    getApp().ensureUser().then(u => {
      const denied = u.role === 'operator'
      this.setData({ denied })
      if (!denied) {
        this.loadStats()
        this.search()
      }
    })
  },

  switchTab(e) {
    this.setData({ active: Number(e.currentTarget.dataset.i), q: '' })
    this.search()
  },

  onInput(e) { this.setData({ q: e.detail.value }) },

  loadStats() {
    api.get('/api/knowledge/stats').then(rows => {
      const sum = s => rows.filter(r => r.review_status === s).reduce((n, r) => n + r.count, 0)
      this.setData({ stats: `已入库 ${sum('approved')} 张 · 待审核 ${sum('pending')} 张` })
    }).catch(() => {})
  },

  search() {
    const status = TABS[this.data.active].key
    const params = { status }
    if (status === 'approved' && this.data.q.trim()) params.q = this.data.q.trim()
    this.setData({ loading: true })
    return api.get('/api/knowledge', params)
      .then(rows => this.setData({
        items: rows.map(c => Object.assign({}, c, {
          riskText: RISK[c.risk_level] || c.risk_level,
          poolText: c.pool === 'common' ? '大健康通用' : '本产品线'
        })),
        error: ''
      }))
      .catch(err => this.setData({ error: err.message }))
      .finally(() => this.setData({ loading: false }))
  },

  clear() {
    this.setData({ q: '' })
    this.search()
  },

  review(e) {
    const { id, action } = e.currentTarget.dataset
    api.post(`/api/knowledge/${id}/review`, { action })
      .then(() => {
        wx.showToast({ title: action === 'approve' ? '已通过' : '已驳回', icon: 'none' })
        this.setData({ items: this.data.items.filter(c => c.id !== id) })
        this.loadStats()
      })
      .catch(err => wx.showModal({ title: '操作失败', content: err.message, showCancel: false }))
  }
})
