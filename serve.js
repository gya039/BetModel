// Diamond Edge — local static server
// Run from E:\BettingModel: node serve.js
// Serves site/ (the built app) + mlb/ (prediction data) on the same origin

import http from 'http'
import https from 'https'
import fs   from 'fs'
import path from 'path'
import os   from 'os'
import { fileURLToPath } from 'url'
import { spawn } from 'child_process'
import { randomUUID } from 'crypto'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const PORT = process.env.PORT || 4002

const MIME = {
  '.html':  'text/html; charset=utf-8',
  '.js':    'application/javascript',
  '.css':   'text/css',
  '.json':  'application/json; charset=utf-8',
  '.csv':   'text/csv; charset=utf-8',
  '.svg':   'image/svg+xml',
  '.png':   'image/png',
  '.ico':   'image/x-icon',
  '.woff2': 'font/woff2',
  '.woff':  'font/woff',
  '.webmanifest': 'application/manifest+json',
}

const statusCache = new Map()
const STATUS_TTL_MS = 30_000

// ── Run Center ────────────────────────────────────────────
const RUN_TOKEN = process.env.RUN_TOKEN || 'de-local-run'
const PYTHON    = process.env.PYTHON    || 'python'
const runs      = new Map()
let   activeRun = null

function ordinal(n) {
  const s = ['th','st','nd','rd'], v = n % 100
  return n + (s[(v-20)%10] || s[v] || s[0])
}

function todayYesterday() {
  const now  = new Date()
  const pad  = n => String(n).padStart(2,'0')
  const fmt  = d => `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`
  const yday = new Date(now); yday.setDate(yday.getDate()-1)
  return { today: fmt(now), yesterday: fmt(yday) }
}

function predictionsFilePath(dateStr) {
  const dt    = new Date(dateStr + 'T12:00:00')
  const month = dt.toLocaleString('en-US', { month: 'long' })
  const day   = dt.getDate()
  const year  = dt.getFullYear()
  const ord   = ordinal(day)
  return path.join(__dirname, 'mlb','predictions',
    `${month} Predictions`, `${month} ${ord}`,
    `${month} ${ord} ${year} Predictions.json`)
}

function getRunStatus() {
  const { today, yesterday } = todayYesterday()
  const base    = predictionsFilePath(today)
  const updated = base.replace('.json', ' (Updated).json')
  return {
    today,
    yesterday,
    generated: fs.existsSync(base),
    movement:  fs.existsSync(updated),
    busy:      activeRun !== null,
  }
}

function spawnRun(scriptArgsList) {
  const id  = randomUUID()
  const run = { id, buffer: [], done: false, exitCode: null, clients: new Set() }
  runs.set(id, run)
  activeRun = id

  function emit(line) {
    run.buffer.push(line)
    for (const res of run.clients) {
      res.write(`data: ${JSON.stringify({ line })}\n\n`)
    }
  }

  function finish(code) {
    run.done     = true
    run.exitCode = code
    activeRun    = null
    const msg = JSON.stringify({ done: true, exitCode: code })
    for (const res of run.clients) {
      res.write(`data: ${msg}\n\n`)
      res.end()
    }
  }

  ;(async () => {
    for (const args of scriptArgsList) {
      emit(`▶ ${PYTHON} ${args.join(' ')}`)
      const code = await new Promise(resolve => {
        const proc = spawn(PYTHON, args, { cwd: __dirname })
        proc.stdout.on('data', d =>
          d.toString().split('\n').filter(Boolean).forEach(emit))
        proc.stderr.on('data', d =>
          d.toString().split('\n').filter(Boolean).forEach(l => emit(`  ${l}`)))
        proc.on('close', resolve)
      })
      if (code !== 0) { finish(code); return }
    }
    finish(0)
  })()

  return id
}

const server = http.createServer((req, res) => {
  let parsedUrl
  let urlPath
  try {
    parsedUrl = new URL(req.url, 'http://x')
    urlPath = decodeURIComponent(parsedUrl.pathname)
  } catch {
    res.writeHead(400); res.end(); return
  }

  let filePath

  // ── CORS preflight ───────────────────────────────────────
  res.setHeader('Access-Control-Allow-Origin', '*')
  res.setHeader('Access-Control-Allow-Headers', 'x-run-token, content-type')
  if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return }

  // ── Run Center: status ────────────────────────────────────
  if (urlPath === '/api/status' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify(getRunStatus()))
    return
  }

  // ── Run Center: SSE stream ───────────────────────────────
  if (urlPath.startsWith('/api/run/stream/') && req.method === 'GET') {
    const id  = urlPath.split('/')[4]
    const run = runs.get(id)
    if (!run) { res.writeHead(404); res.end('Run not found'); return }

    res.writeHead(200, {
      'Content-Type':  'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection':    'keep-alive',
    })
    // Replay buffered lines first
    for (const line of run.buffer) {
      res.write(`data: ${JSON.stringify({ line })}\n\n`)
    }
    if (run.done) {
      res.write(`data: ${JSON.stringify({ done: true, exitCode: run.exitCode })}\n\n`)
      res.end(); return
    }
    run.clients.add(res)
    req.on('close', () => run.clients.delete(res))
    return
  }

  // ── Run Center: trigger ──────────────────────────────────
  if (urlPath.startsWith('/api/run/') && req.method === 'POST') {
    if (req.headers['x-run-token'] !== RUN_TOKEN) {
      res.writeHead(401, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ error: 'Unauthorized' })); return
    }
    if (activeRun) {
      res.writeHead(409, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ error: 'A run is already in progress', runId: activeRun })); return
    }

    const action = urlPath.split('/')[3]
    const { today, yesterday } = todayYesterday()

    const SCRIPTS = {
      settle:   [['mlb/scripts/record_results.py', '--date', yesterday]],
      generate: [
        ['mlb/scripts/record_results.py', '--date', yesterday],
        ['mlb/scripts/predict_today.py',  '--date', today],
      ],
      movement: [['mlb/scripts/check_movement.py', '--date', today]],
    }

    if (!SCRIPTS[action]) {
      res.writeHead(400, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ error: `Unknown action: ${action}` })); return
    }

    const id = spawnRun(SCRIPTS[action])
    res.writeHead(200, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ runId: id }))
    return
  }

  if (urlPath === '/api/mlb-status') {
    const date = parsedUrl.searchParams.get('date')
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date || '')) {
      res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' })
      res.end(JSON.stringify({ error: 'Invalid date' }))
      return
    }

    fetchMlbStatuses(date)
      .then(payload => {
        res.writeHead(200, {
          'Content-Type': 'application/json; charset=utf-8',
          'Cache-Control': 'no-cache, no-store, must-revalidate',
        })
        res.end(JSON.stringify(payload))
      })
      .catch(err => {
        res.writeHead(502, { 'Content-Type': 'application/json; charset=utf-8' })
        res.end(JSON.stringify({ error: err.message }))
      })
    return
  } else if (urlPath.startsWith('/mlb/')) {
    // Serve MLB prediction data straight from project root
    filePath = path.join(__dirname, urlPath)
  } else {
    // Serve the React app from site/
    filePath = path.join(__dirname, 'site', urlPath)
    const exists = fs.existsSync(filePath)
    if (!exists || fs.statSync(filePath).isDirectory()) {
      // SPA fallback — all unknown routes serve index.html
      filePath = path.join(__dirname, 'site', 'index.html')
    }
  }

  try {
    const data = fs.readFileSync(filePath)
    const ext  = path.extname(filePath).toLowerCase()
    const ct   = MIME[ext] || 'application/octet-stream'

    // MLb data: no-cache so the app always sees today's fresh file
    // Static assets: short cache (service worker handles longer caching)
    const noCacheStatic = ['.html', '.webmanifest'].includes(ext)
      || ['sw.js', 'registerSW.js', 'workbox-b51dd497.js'].includes(path.basename(filePath))
    const cc = urlPath.startsWith('/mlb/') || noCacheStatic
      ? 'no-cache, no-store, must-revalidate'
      : 'public, max-age=600'

    res.writeHead(200, { 'Content-Type': ct, 'Cache-Control': cc })
    res.end(data)
  } catch {
    res.writeHead(404, { 'Content-Type': 'text/plain' })
    res.end('404 Not Found')
  }
})

function fetchMlbStatuses(date) {
  const cached = statusCache.get(date)
  if (cached && Date.now() - cached.ts < STATUS_TTL_MS) return Promise.resolve(cached.payload)

  const url = `https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=${date}&hydrate=linescore`
  return new Promise((resolve, reject) => {
    https.get(url, response => {
      let body = ''
      response.setEncoding('utf8')
      response.on('data', chunk => { body += chunk })
      response.on('end', () => {
        if (response.statusCode < 200 || response.statusCode >= 300) {
          reject(new Error(`MLB API status ${response.statusCode}`))
          return
        }
        try {
          const data = JSON.parse(body)
          const games = {}
          for (const day of data.dates || []) {
            for (const game of day.games || []) {
              const status = game.status || {}
              const linescore = game.linescore || {}
              const away = game.teams?.away || {}
              const home = game.teams?.home || {}
              games[String(game.gamePk)] = {
                gamePk: String(game.gamePk),
                gameStatus: classifyGameStatus(status),
                detailedState: status.detailedState || '',
                awayScore: away.score ?? linescore.teams?.away?.runs ?? null,
                homeScore: home.score ?? linescore.teams?.home?.runs ?? null,
                currentInning: linescore.currentInningOrdinal || '',
                inningHalf: linescore.inningHalf || '',
              }
            }
          }
          const payload = { date, games, fetchedAt: new Date().toISOString() }
          statusCache.set(date, { ts: Date.now(), payload })
          resolve(payload)
        } catch (err) {
          reject(err)
        }
      })
    }).on('error', reject)
  })
}

function classifyGameStatus(status) {
  if (status.statusCode === 'F' || status.abstractGameState === 'Final') return 'FINAL'
  if (status.abstractGameState === 'Live' || ['I', 'M', 'N', 'PW'].includes(status.statusCode)) return 'LIVE'
  return 'NOT_STARTED'
}

server.listen(PORT, '0.0.0.0', () => {
  const ifaces = os.networkInterfaces()
  let localIP  = 'YOUR_PC_IP'
  for (const name of Object.keys(ifaces)) {
    for (const iface of ifaces[name]) {
      if (iface.family === 'IPv4' && !iface.internal) {
        localIP = iface.address
        break
      }
    }
    if (localIP !== 'YOUR_PC_IP') break
  }

  console.log('\n  ⬡ Diamond Edge')
  console.log('  ─────────────────────────────────')
  console.log(`  Local:   http://localhost:${PORT}`)
  console.log(`  Network: http://${localIP}:${PORT}`)
  console.log('  ─────────────────────────────────')
  console.log('  Open the Network URL on your phone')
  console.log('  Install as PWA: browser menu → Add to Home Screen\n')
})
