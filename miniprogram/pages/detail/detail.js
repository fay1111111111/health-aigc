const api = require('../../utils/api')
const { decorate } = require('../../utils/format')

Page({
  data: {
    id: null,
    g: null,
    canReview: false,
    editing: false,
    draft: '',
    busy: false,
    error: ''
  },

  onLoad(q) {
    this.setData({ id: q.id })
  },

  onShow() {
    this.load()
  },

  load() {
    const app = getApp()
    return Promise.all([app.ensureUser(), app.ensureMeta(), api.get('/api/generations/' + this.data.id)])
      .then(([user, meta, g]) => this.setData({
        g: decorate(g, meta),
        canReview: user.role === 'reviewer' || user.role === 'admin',
        error: ''
      }))
      .catch(err => this.setData({ error: err.message }))
  },

  update(g) {
    return getApp().ensureMeta().then(meta => this.setData({ g: decorate(g, meta) }))
  },

  // ---------- 修改 ----------
  startEdit() {
    this.setData({ editing: true, draft: this.data.g.final_text })
  },
  onDraft(e) { this.setData({ draft: e.detail.value }) },
  cancelEdit() { this.setData({ editing: false }) },
  saveEdit() {
    if (!this.data.draft.trim()) return wx.showToast({ title: '内容不能为空', icon: 'none' })
    this.run(() => api.patch('/api/generations/' + this.data.id, { edited_output: this.data.draft })
      .then(g => {
        this.setData({ editing: false })
        return this.update(g).then(() => wx.showToast({
          title: g.compliance.passed ? '已保存，检测通过' : '已保存，仍有禁用表述',
          icon: 'none'
        }))
      }))
  },

  // ---------- 审核 ----------
  approve() {
    this.run(() => api.post(`/api/generations/${this.data.id}/review`, { action: 'approve' })
      .then(g => this.update(g)).then(() => wx.showToast({ title: '已通过' })))
  },
  reject() {
    wx.showModal({
      title: '打回修改',
      editable: true,
      placeholderText: '写一下需要怎么改（选填）',
      success: res => {
        if (!res.confirm) return
        this.run(() => api.post(`/api/generations/${this.data.id}/review`, { action: 'reject', note: res.content || '' })
          .then(g => this.update(g)).then(() => wx.showToast({ title: '已打回', icon: 'none' })))
      }
    })
  },

  // ---------- 导出与重新生成 ----------
  copy() {
    this.run(() => api.get(`/api/generations/${this.data.id}/export`)
      .then(r => wx.setClipboardData({ data: r.text })))
  },
  regenerate() {
    const g = this.data.g
    wx.showLoading({ title: '重新生成中', mask: true })
    this.run(() => api.post('/api/generate', {
      channel: g.channel, audience: g.audience, content_type: g.content_type, brief: g.brief
    }).then(ng => {
      wx.hideLoading()
      wx.redirectTo({ url: '/pages/detail/detail?id=' + ng.id })
    }), () => wx.hideLoading())
  },

  // 统一处理忙碌状态和报错
  run(fn, onError) {
    if (this.data.busy) return
    this.setData({ busy: true })
    fn()
      .catch(err => {
        if (onError) onError()
        wx.showModal({ title: '操作失败', content: err.message, showCancel: false })
      })
      .finally(() => this.setData({ busy: false }))
  }
})
