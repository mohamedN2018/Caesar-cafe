/**
 * Render a screen in a real browser, measure it, and save a picture of it.
 *
 * Some claims are LAYOUT claims — "it all fits", "nothing scrolls", "the cells
 * are square" — and no unit test can check one. happy-dom has no layout engine,
 * so `getBoundingClientRect` there returns zeroes nobody computed; a test that
 * asserts on them is asserting on a number the renderer never produced.
 *
 * Three real defects in the floor screen were found by this and by nothing else:
 * the room overflowing a shell that hides its overflow, a bar seat drawn so its
 * label floated above the stool, and two rooms side by side whose cells came out
 * 93px wide and 171px tall because an explicit height silently beat the aspect
 * ratio meant to keep them square. Every one of them passed the unit suite.
 *
 * Drives Chrome over the DevTools protocol with nothing but WebSocket frames, so
 * it needs no puppeteer and nothing installed.
 *
 *   node tools/render_screen.js out.png
 *   ROUTE=/pos/orders W=1024 H=768 node tools/render_screen.js tablet.png
 *   CLICK="الكل" node tools/render_screen.js rooms.png
 *   CLICK_SEL=".table-card:nth-child(1)" node tools/render_screen.js sheet.png
 *
 * BASE defaults to the local stack. It prints a JSON line of measurements —
 * scroll overflow first, because that is the one that is never acceptable.
 */
const { spawn } = require('child_process')
const fs = require('fs')
const http = require('http')
const path = require('path')
const crypto = require('crypto')
const net = require('net')

/** Whatever Chrome this machine has. `CHROME=` overrides it. */
const CHROME_CANDIDATES = [
  process.env.CHROME,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].filter(Boolean)

const BASE = process.env.BASE || 'http://127.0.0.1:8080'
const OUT = process.argv[2] || 'shot.png'
const WIDTH = Number(process.env.W || 1440)
const HEIGHT = Number(process.env.H || 900)
const PORT = 9223

const EMAIL = 'admin@caesar.deplois.net'
const PASSWORD = 'admin'

function get(url) {
  return new Promise((resolve, reject) => {
    http
      .get(url, (res) => {
        let body = ''
        res.on('data', (c) => (body += c))
        res.on('end', () => resolve(body))
      })
      .on('error', reject)
  })
}

/** Minimal RFC6455 client — enough for one CDP session. */
class Ws {
  constructor(url) {
    const u = new URL(url)
    this.pending = new Map()
    this.id = 0
    this.buf = Buffer.alloc(0)
    this.ready = new Promise((resolve, reject) => {
      const key = crypto.randomBytes(16).toString('base64')
      this.sock = net.connect(Number(u.port), u.hostname, () => {
        this.sock.write(
          `GET ${u.pathname}${u.search} HTTP/1.1\r\nHost: ${u.host}\r\n` +
            `Upgrade: websocket\r\nConnection: Upgrade\r\n` +
            `Sec-WebSocket-Key: ${key}\r\nSec-WebSocket-Version: 13\r\n\r\n`,
        )
      })
      let handshaken = false
      this.sock.on('data', (chunk) => {
        if (!handshaken) {
          const s = chunk.indexOf('\r\n\r\n')
          if (s === -1) return
          handshaken = true
          this.buf = chunk.slice(s + 4)
          resolve()
          this.drain()
          return
        }
        this.buf = Buffer.concat([this.buf, chunk])
        this.drain()
      })
      this.sock.on('error', reject)
    })
  }

  drain() {
    for (;;) {
      if (this.buf.length < 2) return
      const len0 = this.buf[1] & 0x7f
      let off = 2
      let len = len0
      if (len0 === 126) {
        if (this.buf.length < 4) return
        len = this.buf.readUInt16BE(2)
        off = 4
      } else if (len0 === 127) {
        if (this.buf.length < 10) return
        len = Number(this.buf.readBigUInt64BE(2))
        off = 10
      }
      if (this.buf.length < off + len) return
      const payload = this.buf.slice(off, off + len).toString()
      this.buf = this.buf.slice(off + len)
      try {
        const msg = JSON.parse(payload)
        if (msg.id && this.pending.has(msg.id)) {
          const { resolve, reject } = this.pending.get(msg.id)
          this.pending.delete(msg.id)
          msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result)
        }
      } catch {
        /* a frame we do not care about */
      }
    }
  }

  send(method, params = {}) {
    const id = ++this.id
    const body = Buffer.from(JSON.stringify({ id, method, params }))
    const mask = crypto.randomBytes(4)
    const masked = Buffer.from(body.map((b, i) => b ^ mask[i % 4]))
    let header
    if (body.length < 126) header = Buffer.from([0x81, 0x80 | body.length])
    else if (body.length < 65536) {
      header = Buffer.alloc(4)
      header[0] = 0x81
      header[1] = 0x80 | 126
      header.writeUInt16BE(body.length, 2)
    } else {
      header = Buffer.alloc(10)
      header[0] = 0x81
      header[1] = 0x80 | 127
      header.writeBigUInt64BE(BigInt(body.length), 2)
    }
    this.sock.write(Buffer.concat([header, mask, masked]))
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }))
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function main() {
  const chromePath = CHROME_CANDIDATES.find((c) => fs.existsSync(c))
  if (!chromePath) {
    throw new Error(
      'no Chrome found — set CHROME=/path/to/chrome. Tried: ' + CHROME_CANDIDATES.join(', '),
    )
  }

  const chrome = spawn(chromePath, [
    '--headless=new',
    `--remote-debugging-port=${PORT}`,
    `--window-size=${WIDTH},${HEIGHT}`,
    '--no-first-run',
    '--no-default-browser-check',
    `--user-data-dir=${path.join(process.env.TEMP || '.', 'caesar-shot-profile')}`,
    'about:blank',
  ])
  chrome.stderr.on('data', () => {})

  let target
  for (let i = 0; i < 40; i++) {
    try {
      const list = JSON.parse(await get(`http://127.0.0.1:${PORT}/json/list`))
      target = list.find((t) => t.type === 'page')
      if (target) break
    } catch {
      /* not up yet */
    }
    await sleep(250)
  }
  if (!target) throw new Error('chrome never came up')

  const ws = new Ws(target.webSocketDebuggerUrl)
  await ws.ready
  await ws.send('Page.enable')
  await ws.send('Runtime.enable')
  await ws.send('Emulation.setDeviceMetricsOverride', {
    width: WIDTH,
    height: HEIGHT,
    deviceScaleFactor: 1,
    mobile: false,
  })

  const evaluate = async (expression) => {
    const r = await ws.send('Runtime.evaluate', {
      expression,
      awaitPromise: true,
      returnByValue: true,
    })
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails))
    return r.result.value
  }

  // Sign in through the real API and seed the tokens the SPA reads.
  await ws.send('Page.navigate', { url: BASE })
  await sleep(1500)
  await evaluate(`(async () => {
    const r = await fetch('${BASE}/api/v1/auth/login/', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email: '${EMAIL}', password: '${PASSWORD}'}),
    })
    const j = await r.json()
    localStorage.setItem('caesar.access', j.data.access)
    localStorage.setItem('caesar.refresh', j.data.refresh)
    return 'ok'
  })()`)

  await ws.send('Page.navigate', { url: `${BASE}${process.env.ROUTE || '/pos'}` })
  await sleep(4000)

  if (process.env.CLICK) {
    const label = JSON.stringify(process.env.CLICK)
    const found = await evaluate(
      '(() => { const b = [...document.querySelectorAll("button")]' +
        '.find((x) => x.textContent.trim() === ' +
        label +
        '); if (b) b.click(); return !!b })()',
    )
    if (!found) throw new Error('no button labelled ' + label)
    await sleep(700)
  }

  if (process.env.CLICK_SEL) {
    await evaluate(
      '(() => { const e = document.querySelector(' +
        JSON.stringify(process.env.CLICK_SEL) +
        '); if (e) e.click(); return !!e })()',
    )
    await sleep(700)
  }

  const report = await evaluate(`(() => {
    const de = document.documentElement
    const cards = document.querySelectorAll('.table-card')
    const grids = document.querySelectorAll('.plan-grid')
    const g = grids[0] ? grids[0].getBoundingClientRect() : null
    const first = cards[0] ? cards[0].getBoundingClientRect() : null
    return JSON.stringify({
      route: location.pathname,
      pageScrollY: de.scrollHeight - de.clientHeight,
      pageScrollX: de.scrollWidth - de.clientWidth,
      tables: cards.length,
      rooms: grids.length,
      grid: g && {w: Math.round(g.width), h: Math.round(g.height), bottom: Math.round(g.bottom)},
      cell: first && {w: Math.round(first.width), h: Math.round(first.height)},
      viewport: {w: window.innerWidth, h: window.innerHeight},
      // Guarded: .table-number is the POS floor's markup and this tool is pointed
      // at any screen. Unguarded, getComputedStyle(null) threw and the whole
      // measurement was lost — reported as a page error, which is the one thing a
      // tool for finding page errors must not invent.
      //
      // No backticks in this comment, deliberately: it lives INSIDE a template
      // literal, and a backtick here ends the string and turns the rest of the
      // browser snippet into Node source. Which is what happened.
      numberPx: (() => {
        const label = cards[0] && cards[0].querySelector('.table-number')
        return label ? getComputedStyle(label).fontSize : null
      })(),
      bodyText: document.body.innerText.slice(0, 160).replace(/\\n/g, ' | '),
    })
  })()`)
  console.log(report)

  const shot = await ws.send('Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(OUT, Buffer.from(shot.data, 'base64'))
  console.log('wrote', OUT)

  chrome.kill()
  process.exit(0)
}

main().catch((e) => {
  console.error('FAILED', e.message)
  process.exit(1)
})
