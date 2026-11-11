const api = require('../../utils/api')
const { toast, shortTime, withHealthConsent } = require('../../utils/util')

const KINDS = [
  { id: 'before', icon: 'clock', name: '体验前', hint: '开始之前，记一下此刻的状态。', sleepQuestion: '最近几天睡得怎么样' },
  { id: 'after', icon: 'check', name: '体验后', hint: '做完之后，感受如实写就好，没有明显变化也很正常。', sleepQuestion: '此刻想睡、能安静下来的程度' },
  { id: 'next_day', icon: 'sunrise', name: '次日回访', hint: '体验后的第二天，回看一下昨晚。', sleepQuestion: '昨晚睡得怎么样' }
]
const KIND_NAMES = KINDS.reduce((map, k) => { map[k.id] = k.name; return map }, {})

Page({
  data: {
    kinds: KINDS,
    kind: 'before',
    hint: KINDS[0].hint,
    sleepQuestion: KINDS[0].sleepQuestion,
    relax: 0,
    sleep: 0,
    note: '',
    saving: false,
    records: []
  },

  onLoad(query) {
    this.setKind(KIND_NAMES[query.kind] ? query.kind : 'before')
    this.load()
  },

  setKind(id) {
    const k = KINDS.filter((x) => x.id === id)[0]
    this.setData({ kind: k.id, hint: k.hint, sleepQuestion: k.sleepQuestion })
  },

  load() {
    if (!(getApp().globalData.user || {}).health_consent) return
    api.get('/api/c/assessments', { limit: 30 }).then((rows) => {
      this.setData({
        records: rows.map((r) => Object.assign({ kindName: KIND_NAMES[r.kind], time: shortTime(r.created_at) }, r))
      })
    }).catch((err) => toast(err.message))
  },

  pickKind(e) { this.setKind(e.currentTarget.dataset.id) },
  pickScore(e) { this.setData({ [e.currentTarget.dataset.k]: e.currentTarget.dataset.v }) },
  onNote(e) { this.setData({ note: e.detail.value }) },

  save() {
    if (this.data.saving) return
    const d = this.data
    if (!d.relax || !d.sleep) return toast('两项都选一下分数（1–5 分）')
    this.setData({ saving: true })
    withHealthConsent(() => api.post('/api/c/assessments', {
      kind: d.kind, relax: d.relax, sleep: d.sleep, note: d.note.trim()
    })).then(() => {
      wx.showToast({ title: '已提交', icon: 'success' })
      this.setData({ saving: false, relax: 0, sleep: 0, note: '' })
      this.load()
    }).catch((err) => {
      this.setData({ saving: false })
      if (!err.cancelled) toast(err.message)
    })
  }
})
