const api = require('../../utils/api')
const { toast, today, duration, confirm, withHealthConsent } = require('../../utils/util')

Page({
  data: {
    today: today(),
    day: today(),
    bedtime: '23:00',
    wakeTime: '07:00',
    quality: 0,
    note: '',
    saving: false,
    records: []
  },

  onLoad() {
    this.load()
  },

  load() {
    // 还没同意记录健康相关信息时，列表先空着，保存时再征求同意
    if (!(getApp().globalData.user || {}).health_consent) return
    api.get('/api/c/sleep-records', { limit: 30 }).then((rows) => {
      this.setData({ records: rows.map((r) => Object.assign({ durationText: duration(r.minutes) }, r)) })
    }).catch((err) => toast(err.message))
  },

  pickDay(e) { this.setData({ day: e.detail.value }) },
  pickBed(e) { this.setData({ bedtime: e.detail.value }) },
  pickWake(e) { this.setData({ wakeTime: e.detail.value }) },
  pickQuality(e) { this.setData({ quality: e.currentTarget.dataset.v }) },
  onNote(e) { this.setData({ note: e.detail.value }) },

  save() {
    if (this.data.saving) return
    const d = this.data
    if (!d.quality) return toast('选一下整体感觉（1–5 分）')
    this.setData({ saving: true })
    withHealthConsent(() => api.post('/api/c/sleep-records', {
      day: d.day, bedtime: d.bedtime, wake_time: d.wakeTime, quality: d.quality, note: d.note.trim()
    })).then(() => {
      wx.showToast({ title: '已记录', icon: 'success' })
      this.setData({ saving: false, note: '', quality: 0 })
      this.load()
    }).catch((err) => {
      this.setData({ saving: false })
      if (!err.cancelled) toast(err.message)
    })
  },

  remove(e) {
    const id = e.currentTarget.dataset.id
    confirm('删除这条记录？', '删除后无法恢复。', '删除').then((ok) => {
      if (!ok) return
      return api.del('/api/c/sleep-records/' + id).then(() => this.load())
    }).catch((err) => toast(err.message))
  }
})
