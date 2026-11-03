const STATUS = {
  pending: { text: '待审核', cls: 'tag-pending' },
  approved: { text: '已通过', cls: 'tag-approved' },
  rejected: { text: '已打回', cls: 'tag-rejected' }
}

const ROLE = { operator: '运营', reviewer: '审核', admin: '管理员' }

function pad(n) {
  return n < 10 ? '0' + n : '' + n
}

function time(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  return `${d.getMonth() + 1}-${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// 用 /api/meta 里的列表把 id 换成中文名
function nameOf(list, id) {
  const hit = (list || []).find(x => x.id === id)
  return hit ? hit.name : id
}

function decorate(g, meta) {
  const m = meta || {}
  const issues = (g.compliance && g.compliance.issues) || []
  return Object.assign({}, g, {
    channelName: nameOf(m.channels, g.channel),
    audienceName: nameOf(m.audiences, g.audience),
    typeName: nameOf(m.content_types, g.content_type),
    statusText: STATUS[g.status] ? STATUS[g.status].text : g.status,
    statusCls: STATUS[g.status] ? STATUS[g.status].cls : '',
    timeText: time(g.created_at),
    passed: !!(g.compliance && g.compliance.passed),
    blockIssues: issues.filter(i => i.level === 'block'),
    warnIssues: issues.filter(i => i.level === 'warn'),
    preview: (g.final_text || '').replace(/\s+/g, ' ').slice(0, 60)
  })
}

module.exports = { STATUS, ROLE, time, nameOf, decorate }
