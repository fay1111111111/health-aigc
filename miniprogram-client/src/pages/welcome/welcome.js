const api = require('../../utils/api')
const { toast } = require('../../utils/util')

Page({
  data: {
    roles: [
      { id: 'self', icon: 'person', name: '为自己了解', desc: '想放松一下，或者睡前总是静不下来' },
      { id: 'family', icon: 'home', name: '为家人了解', desc: '想陪父母、孩子把睡眠习惯调好一点' },
      { id: 'partner', icon: 'cap', name: '我是从业伙伴', desc: '在康复理疗、养生等一线工作，想了解学习' }
    ],
    role: '',
    nickname: '',
    avatar: '', // 选好的头像临时文件
    agree: false,
    canSubmit: false,
    submitting: false
  },

  refresh(patch) {
    const next = Object.assign({}, this.data, patch)
    patch.canSubmit = !!next.role && next.agree && !next.submitting
    this.setData(patch)
  },

  pickRole(e) {
    this.refresh({ role: e.currentTarget.dataset.id })
  },

  onAvatar(e) {
    this.setData({ avatar: e.detail.avatarUrl })
  },

  onNick(e) {
    this.setData({ nickname: e.detail.value })
  },

  toggleAgree() {
    this.refresh({ agree: !this.data.agree })
  },

  openLegal(e) {
    wx.navigateTo({ url: '/pages/legal/legal?type=' + e.currentTarget.dataset.type })
  },

  submit() {
    if (this.data.submitting) return
    if (!this.data.role) return toast('先选一下你是为谁而来')
    if (!this.data.agree) return toast('请先阅读并同意用户协议和隐私政策')
    const app = getApp()
    const { role, nickname, avatar } = this.data
    this.refresh({ submitting: true })
    app.ensureUser()
      .then(() => api.post('/api/c/register', { role, nickname: nickname.trim(), agree_privacy: true }))
      .then((user) => {
        app.setUser(user)
        if (!avatar) return
        // 头像传不上去不影响注册，之后可以在「我的」里再换
        return api.upload('/api/c/me/avatar', avatar).then((u) => app.setUser(u)).catch(() => {})
      })
      .then(() => {
        wx.switchTab({ url: role === 'partner' ? '/pages/partner/partner' : '/pages/chat/chat' })
      })
      .catch((err) => {
        this.refresh({ submitting: false })
        toast(err.message)
      })
  }
})
