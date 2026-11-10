const api = require('../../utils/api')
const { toast } = require('../../utils/util')

Page({
  data: {
    interest: 'learn',
    orgTypes: ['康复理疗机构', '养生馆 / 美容院', '按摩推拿店', '心理咨询工作室', '医疗机构从业者', '酒店 / 民宿', '个人学习', '其他'],
    orgType: '',
    name: '',
    city: '',
    contact: '',
    note: '',
    agree: false,
    submitting: false
  },

  pickInterest(e) {
    this.setData({ interest: e.currentTarget.dataset.v })
  },

  pickOrg(e) {
    this.setData({ orgType: this.data.orgTypes[e.detail.value] })
  },

  onField(e) {
    this.setData({ [e.currentTarget.dataset.k]: e.detail.value })
  },

  toggleAgree() {
    this.setData({ agree: !this.data.agree })
  },

  submit() {
    if (this.data.submitting) return
    const d = this.data
    if (!d.name.trim()) return toast('请填写称呼')
    if (!d.orgType) return toast('请选择机构类型')
    if (d.contact.trim().length < 5) return toast('请填写可以联系到你的方式')
    if (!d.agree) return toast('请先勾选同意我们联系你')
    this.setData({ submitting: true })
    api.post('/api/c/leads', {
      name: d.name.trim(), org_type: d.orgType, city: d.city.trim(), contact: d.contact.trim(),
      interest: d.interest, note: d.note.trim(), agree_contact: true
    }).then(() => {
      wx.showToast({ title: '已收到', icon: 'success' })
      setTimeout(() => wx.navigateBack(), 900)
    }).catch((err) => {
      this.setData({ submitting: false })
      toast(err.message)
    })
  }
})
