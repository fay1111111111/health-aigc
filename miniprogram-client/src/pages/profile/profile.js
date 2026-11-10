const api = require('../../utils/api')
const { ROLE_NAMES, toast } = require('../../utils/util')

Page({
  data: {
    roles: Object.keys(ROLE_NAMES).map((id) => ({ id, name: ROLE_NAMES[id] })),
    avatar: '',
    newAvatar: '', // 新选的头像临时文件，保存时才上传
    nickname: '',
    role: '',
    saving: false
  },

  onLoad() {
    const user = getApp().globalData.user || {}
    this.setData({ avatar: api.fileUrl(user.avatar_url), nickname: user.nickname || '', role: user.role || 'self' })
  },

  onAvatar(e) {
    this.setData({ avatar: e.detail.avatarUrl, newAvatar: e.detail.avatarUrl })
  },

  onNick(e) {
    this.setData({ nickname: e.detail.value })
  },

  pickRole(e) {
    this.setData({ role: e.currentTarget.dataset.id })
  },

  save() {
    if (this.data.saving) return
    const { nickname, role, newAvatar } = this.data
    if (!nickname.trim()) return toast('昵称不能为空')
    this.setData({ saving: true })
    const app = getApp()
    api.patch('/api/c/me', { nickname: nickname.trim(), role })
      .then((user) => {
        app.setUser(user)
        if (newAvatar) return api.upload('/api/c/me/avatar', newAvatar).then((u) => app.setUser(u))
      })
      .then(() => {
        wx.showToast({ title: '已保存', icon: 'success' })
        setTimeout(() => wx.navigateBack(), 700)
      })
      .catch((err) => {
        this.setData({ saving: false })
        toast(err.message)
      })
  }
})
