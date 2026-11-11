const api = require('./api')

const ROLE_NAMES = { self: '为自己了解', family: '为家人了解', partner: '从业伙伴' }

function toast(title) {
  wx.showToast({ title: String(title).slice(0, 40), icon: 'none', duration: 2200 })
}

function pad(n) {
  return n < 10 ? '0' + n : '' + n
}

function today() {
  const d = new Date()
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

// 2026-11-09T12:00:00Z → 11月9日 20:00（按手机本地时间）
function shortTime(iso) {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  return `${d.getMonth() + 1}月${d.getDate()}日 ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function duration(minutes) {
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return m ? `${h}小时${m}分` : `${h}小时`
}

function confirm(title, content, confirmText) {
  return new Promise((resolve) => {
    wx.showModal({
      title, content, confirmText: confirmText || '确定', confirmColor: '#528977',
      success: (res) => resolve(res.confirm),
      fail: () => resolve(false)
    })
  })
}

// 睡眠记录、自评属于敏感个人信息：第一次用之前单独征得同意。
// run 是真正要做的请求；后端提示需要同意时弹窗，同意后自动再试一次。
function withHealthConsent(run) {
  return run().catch((err) => {
    if (err.message !== 'need_health_consent') throw err
    return confirm(
      '记录前请确认',
      '睡眠记录和体验自评属于与健康相关的个人信息。我们只用它来让你回看自己的变化，以及在去除身份信息后改进问答和课程；不会用于模型训练，除非另外征得你的同意。你可以随时在「我的」里撤回并删除。',
      '同意并继续'
    ).then((ok) => {
      if (!ok) {
        const e = new Error('未同意，暂不记录')
        e.cancelled = true
        throw e
      }
      return api.post('/api/c/me/health-consent', { agree: true }).then((user) => {
        getApp().setUser(user)
        return run()
      })
    })
  })
}

module.exports = { ROLE_NAMES, toast, today, shortTime, duration, confirm, withHealthConsent }
