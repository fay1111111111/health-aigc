const api = require('../../utils/api')
const { decorate } = require('../../utils/format')

const TABS = [
  { key: '', text: '全部' },
  { key: 'pending', text: '待审核' },
  { key: 'approved', text: '已通过' },
  { key: 'rejected', text: '已打回' }
]

Page({
  data: { tabs: TABS, active: 0, items: [], loading: false, error: '' },

  onShow() {
    if (!wx.getStorageSync('token')) return wx.reLaunch({ url: '/pages/login/login' })
    this.load()
  },

  onPullDownRefresh() {
    this.load().then(() => wx.stopPullDownRefresh())
  },

  switchTab(e) {
    this.setData({ active: Number(e.currentTarget.dataset.i) })
    this.load()
  },

  load() {
    const status = TABS[this.data.active].key
    this.setData({ loading: true })
    return getApp().ensureMeta()
      .then(meta => api.get('/api/generations', status ? { status } : {})
        .then(rows => this.setData({ items: rows.map(g => decorate(g, meta)), error: '' })))
      .catch(err => this.setData({ error: err.message }))
      .finally(() => this.setData({ loading: false }))
  },

  open(e) {
    wx.navigateTo({ url: '/pages/detail/detail?id=' + e.currentTarget.dataset.id })
  }
})
