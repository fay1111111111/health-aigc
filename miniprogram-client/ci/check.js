// 不打开开发者工具也能做的基础检查：页面四件套是否齐全、JSON 是否合法、JS 能否解析、
// wxml 标签是否配对、wxml 里绑定的事件在 js 里是否存在。
const fs = require('fs')
const path = require('path')
const vm = require('vm')

const src = path.join(__dirname, '..', 'src')
const errors = []
const read = (p) => fs.readFileSync(p, 'utf8')

const app = JSON.parse(read(path.join(src, 'app.json')))
for (const page of app.pages) {
  for (const ext of ['js', 'json', 'wxml', 'wxss']) {
    if (!fs.existsSync(path.join(src, `${page}.${ext}`))) errors.push(`缺文件：${page}.${ext}`)
  }
}
for (const tab of app.tabBar.list) {
  if (!app.pages.includes(tab.pagePath)) errors.push(`tabBar 页面不在 pages 里：${tab.pagePath}`)
  for (const key of ['iconPath', 'selectedIconPath']) {
    if (!fs.existsSync(path.join(src, tab[key]))) errors.push(`缺图标：${tab[key]}`)
  }
}

const VOID = new Set(['input', 'image', 'import', 'include', 'icon', 'progress', 'slider', 'switch', 'textarea'])
function walk(dir) {
  for (const name of fs.readdirSync(dir)) {
    const p = path.join(dir, name)
    if (fs.statSync(p).isDirectory()) { walk(p); continue }
    const rel = path.relative(src, p)
    if (name.endsWith('.json')) {
      try { JSON.parse(read(p)) } catch (e) { errors.push(`${rel} 不是合法 JSON：${e.message}`) }
    } else if (name.endsWith('.js')) {
      try { new vm.Script(read(p), { filename: rel }) } catch (e) { errors.push(`${rel} 语法错误：${e.message}`) }
    } else if (name.endsWith('.wxml')) {
      const text = read(p).replace(/<!--[\s\S]*?-->/g, '')
      const stack = []
      const tagRe = /<(\/)?([a-zA-Z][\w-]*)((?:"[^"]*"|'[^']*'|[^>"'])*)>/g
      let m
      while ((m = tagRe.exec(text))) {
        const [, close, tag, attrs] = m
        if (close) {
          const top = stack.pop()
          if (top !== tag) errors.push(`${rel} 标签没配对：</${tag}>（期望 </${top}>）`)
        } else if (!/\/\s*$/.test(attrs) && !VOID.has(tag)) stack.push(tag)
      }
      if (stack.length) errors.push(`${rel} 有没闭合的标签：${stack.join(', ')}`)
      const js = read(p.replace(/\.wxml$/, '.js'))
      const handlers = new Set()
      const hRe = /(?:bind|catch)[\w:]*="([A-Za-z_]\w*)"/g
      while ((m = hRe.exec(text))) handlers.add(m[1])
      for (const fn of handlers) {
        if (!new RegExp(`(^|\\s)(async\\s+)?${fn}\\s*\\(`, 'm').test(js)) errors.push(`${rel} 绑定了 ${fn}，但 js 里没有这个方法`)
      }
    }
  }
}
walk(src)

if (errors.length) { console.error(errors.join('\n')); process.exit(1) }
console.log(`检查通过：${app.pages.length} 个页面`)
