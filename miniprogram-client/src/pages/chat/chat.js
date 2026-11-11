const api = require('../../utils/api')
const { toast } = require('../../utils/util')

const PAGE_SIZE = 4 // 每个分类一次展示几条常见问题
let seq = 0
const CAT_ICONS = { learn: 'leaf', experience: 'lotus', aftercare: 'heart' }

// 大模型生成的回答和审核过的问答原文都可以评价
function canRate(kind) {
  return kind === 'ai' || kind === 'answer'
}

function isNight() {
  const h = new Date().getHours()
  return h >= 18 || h < 5 // 和问候语的分界一致：18 点后「晚上好」，5 点前「夜深了」
}

function greeting() {
  const h = new Date().getHours()
  if (h < 5) return '夜深了，慢一点'
  if (h < 11) return '早上好'
  if (h < 14) return '中午好'
  if (h < 18) return '下午好'
  return '晚上好'
}

Page({
  data: {
    greeting: greeting(),
    isNight: isNight(),
    categories: [],
    activeCat: 0,
    offset: 0,
    visibleQuestions: [],
    activeIcon: 'leaf',
    canShuffle: false,
    messages: [],
    input: '',
    sending: false,
    anchor: '',
    loadError: ''
  },

  onLoad() {
    this.loaded = false
  },

  onShow() {
    this.setData({ greeting: greeting(), isNight: isNight() })
    getApp().ensureUser().then((user) => {
      if (!user.registered) {
        wx.reLaunch({ url: '/pages/welcome/welcome' })
        return
      }
      if (!this.loaded) return this.load()
    }).then(() => this.consumePending()).catch((err) => {
      this.setData({ loadError: err.message })
    })
  },

  onUnload() {
    clearInterval(this.timer)
  },

  reload() {
    this.setData({ loadError: '' })
    this.onShow()
  },

  load() {
    return Promise.all([api.get('/api/c/home'), api.get('/api/c/chat/history', { limit: 20 })]).then(([home, history]) => {
      this.loaded = true
      const messages = []
      history.forEach((m) => {
        messages.push({ key: 'k' + seq++, role: 'user', text: m.question })
        messages.push({
          key: 'k' + seq++, role: 'bot', id: m.id, kind: m.kind, shown: m.answer, done: true,
          helpful: m.helpful, related: [], canRate: canRate(m.kind)
        })
      })
      const categories = home.categories.map((c) => Object.assign({ icon: CAT_ICONS[c.id] || 'leaf' }, c))
      this.setData({ categories, messages, loadError: '' })
      this.showQuestions(0, 0)
      if (messages.length) this.scrollDown()
    })
  },

  // 其他页面（比如「伙伴」页）点了问题，会带到这里来问
  consumePending() {
    const app = getApp()
    const pending = app.globalData.pendingAsk
    if (!pending || !this.loaded) return
    app.globalData.pendingAsk = null
    this.ask(pending.message, pending.code, pending.scene)
  },

  showQuestions(activeCat, offset) {
    const cat = this.data.categories[activeCat] || {}
    const all = cat.questions || []
    const start = offset >= all.length ? 0 : offset
    this.setData({
      activeCat, offset: start, activeIcon: cat.icon || 'leaf',
      visibleQuestions: all.slice(start, start + PAGE_SIZE),
      canShuffle: all.length > PAGE_SIZE
    })
  },

  pickCategory(e) {
    this.showQuestions(e.currentTarget.dataset.index, 0)
  },

  shuffle() {
    this.showQuestions(this.data.activeCat, this.data.offset + PAGE_SIZE)
  },

  onInput(e) {
    this.setData({ input: e.detail.value })
  },

  send() {
    const text = this.data.input.trim()
    if (!text) return
    this.ask(text, '', 'c')
  },

  askPreset(e) {
    const { code, question } = e.currentTarget.dataset
    this.ask(question, code, 'c')
  },

  ask(message, code, scene) {
    if (this.data.sending) return
    const messages = this.data.messages.concat(
      { key: 'k' + seq++, role: 'user', text: message },
      { key: 'k' + seq++, role: 'bot', loading: true, related: [] }
    )
    const index = messages.length - 1
    this.setData({ messages, input: '', sending: true })
    this.scrollDown()

    const started = Date.now()
    api.post('/api/c/chat', { message, code: code || '', scene: scene || 'c' }).then((res) => {
      // 至少让「正在输入」的小动画停留一会儿，回答不会闪一下就出现
      const wait = Math.max(0, 500 - (Date.now() - started)) // 大模型本身要几秒，这里只保证最短停留
      setTimeout(() => this.reveal(index, res), wait)
    }).catch((err) => {
      this.setData({
        [`messages[${index}]`]: {
          key: messages[index].key, role: 'bot', kind: 'error', done: true, related: [],
          shown: '刚才没有发出去：' + err.message
        },
        sending: false
      })
    })
  },

  // 回答逐段出现
  reveal(index, res) {
    const full = res.answer
    const base = `messages[${index}]`
    this.setData({
      [base]: {
        key: this.data.messages[index].key, role: 'bot', id: res.id, kind: res.kind,
        shown: '', done: false, helpful: null, related: res.related || [], canRate: canRate(res.kind)
      }
    })
    const step = Math.max(3, Math.ceil(full.length / 40))
    let shown = 0
    clearInterval(this.timer)
    this.timer = setInterval(() => {
      shown = Math.min(full.length, shown + step)
      const done = shown >= full.length
      this.setData({ [`${base}.shown`]: full.slice(0, shown), [`${base}.done`]: done })
      if (done) {
        clearInterval(this.timer)
        this.setData({ sending: false })
        this.scrollDown()
      }
    }, 40)
    this.scrollDown()
  },

  rate(e) {
    const { index, helpful } = e.currentTarget.dataset
    const msg = this.data.messages[index]
    if (!msg || !msg.id || msg.helpful === helpful) return
    this.setData({ [`messages[${index}].helpful`]: helpful })
    api.post(`/api/c/chat/${msg.id}/feedback`, { helpful }).then(() => {
      toast(helpful ? '谢谢，已记录' : '谢谢，我们会改进这条回答')
    }).catch((err) => {
      this.setData({ [`messages[${index}].helpful`]: msg.helpful })
      toast(err.message)
    })
  },

  scrollDown() {
    // 先清空再设置，保证同一个锚点能重复触发滚动
    this.setData({ anchor: '' })
    wx.nextTick(() => this.setData({ anchor: 'bottom' }))
  },

  onShareAppMessage() {
    return { title: '药王山沁养｜睡前，慢一点', path: '/pages/chat/chat' }
  }
})
