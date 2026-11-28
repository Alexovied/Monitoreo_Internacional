// Conexión en tiempo real con el backend (WebSocket /ws/aircraft/).
//
// Mensajes que llegan: snapshot (al conectar), aircraft (cada ciclo),
// alert (ingreso a la zona), incident (comportamiento raro, avistamiento sin
// transponder o simulacro) y config (cambio de configuración).

import { reactive } from 'vue'
import { api } from './api'
import { beep } from './sound'

const MAX_ALERTS = 50
const TOAST_SECONDS = 30

export const live = reactive({
  connected: false,
  aircraft: [],
  config: null,
  status: null,
  alerts: [], // alertas recibidas en esta sesión del navegador (solo en memoria)
  incidents: [], // comportamientos raros y avistamientos recibidos en esta sesión
  incidentSeq: 0, // cambia con cada incidente nuevo (el registro se vuelve a cargar)
  selectedHex: null,
  focusHex: null, // pedido de centrar el mapa en una aeronave (desde un aviso)
  sound: readSoundSetting(),
  demo: readDemoSetting(),
})

let socket = null
let wanted = false
let retries = 0
let retryTimer = null
let alertSeq = 0

function readSoundSetting() {
  try {
    return localStorage.getItem('avion.sound') !== 'off'
  } catch {
    return true
  }
}

// Modo demo (?demo=1 en la URL, ?demo=0 lo apaga): suma al mapa una aeronave
// simulada de cada tipo. Solo en este navegador; no genera alertas ni Telegram.
function readDemoSetting() {
  const q = new URLSearchParams(location.search).get('demo')
  try {
    if (q !== null) sessionStorage.setItem('avion.demo', q === '0' ? 'off' : 'on')
    return sessionStorage.getItem('avion.demo') === 'on'
  } catch {
    return q !== null && q !== '0'
  }
}

// [categoría, tipo, distancia NM, rumbo desde el centro, altitud ft, velocidad kt, track]
const DEMO_AIRCRAFT = [
  ['B6', 'DRON', 0.5, 45, 300, 25, 120],
  ['A7', 'EC35', 12, 300, 1500, 110, 80],
  ['A1', 'C152', 18, 200, 3500, 95, 20],
  ['B1', 'ASK2', 25, 120, 4500, 50, 250],
  ['B2', 'BALL', 30, 30, 2000, 8, null],
  ['A3', 'A320', 40, 250, 18000, 380, 60],
]

function withDemo(aircraft) {
  const zone = live.config?.zone
  if (!live.demo || !zone) return aircraft
  const now = live.status?.last_update ?? Date.now() / 1000
  const rad = Math.PI / 180
  const demo = DEMO_AIRCRAFT.map(([category, type, dst, bearing, alt, gs, track], i) => ({
    hex: `demo${i}`,
    flight: `DEMO${i + 1}`,
    registration: 'SIMULADO',
    type,
    category,
    lat: zone.lat + (dst / 60) * Math.cos(bearing * rad),
    lon: zone.lon + ((dst / 60) * Math.sin(bearing * rad)) / Math.cos(zone.lat * rad),
    alt_baro: alt,
    gs,
    track,
    true_heading: null,
    dst,
    seen_pos: 0,
    timestamp: now,
    inside: dst <= zone.radius_nm,
    // Como el backend (eta_min): las que vienen derecho hacia la zona.
    eta_min: demoEta(dst, bearing, gs, track, alt, zone),
  }))
  return [...aircraft, ...demo]
}

// Minutos hasta entrar a la zona si vuela derecho hacia el centro (±30°) y por
// debajo del techo, o null.
function demoEta(dst, bearing, gs, track, alt, zone) {
  const radius = zone.radius_nm
  if (dst <= radius || !gs || track === null || alt >= zone.ceiling_m / 0.3048) return null
  const toCenter = (bearing + 180) % 360
  const off = Math.abs(((track - toCenter + 540) % 360) - 180)
  if (off > 30) return null
  const minutes = ((dst - radius) / gs) * 60
  return minutes <= 10 ? Math.round(minutes * 10) / 10 : null
}

export function setSound(enabled) {
  live.sound = enabled
  try {
    localStorage.setItem('avion.sound', enabled ? 'on' : 'off')
  } catch {
    /* sin almacenamiento: queda solo en memoria */
  }
}

export function connectLive() {
  wanted = true
  if (!socket) open()
}

export function disconnectLive() {
  wanted = false
  clearTimeout(retryTimer)
  if (socket) {
    const s = socket
    socket = null
    s.close(1000)
  }
  live.connected = false
  live.aircraft = []
  live.status = null
  live.alerts = []
  live.incidents = []
  live.selectedHex = null
}

function open() {
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${protocol}://${location.host}/ws/aircraft/`)
  socket = ws
  let opened = false

  ws.onopen = () => {
    opened = true
    retries = 0
    live.connected = true
  }
  ws.onmessage = (event) => {
    try {
      handle(JSON.parse(event.data))
    } catch (err) {
      console.error('Mensaje inválido del servidor', err)
    }
  }
  ws.onclose = async (event) => {
    if (socket !== ws) return // cierre pedido por nosotros
    socket = null
    live.connected = false
    if (!wanted) return
    // Un rechazo en el handshake llega como 1006 sin haber abierto; 4401 es
    // "sesión cerrada" (p. ej. usuario desactivado). En ambos casos se
    // confirma la sesión: si venció, el manejador de 401 lleva al login.
    if (!opened || event.code === 4401) {
      const stillLoggedIn = await checkSession()
      if (!stillLoggedIn || !wanted) return
    }
    retries += 1
    const delay = Math.min(30000, 1000 * 2 ** Math.min(retries - 1, 5))
    retryTimer = setTimeout(() => wanted && !socket && open(), delay)
  }
}

async function checkSession() {
  try {
    await api('/auth/me/')
    return true
  } catch (err) {
    return err.status !== 401
  }
}

function handle(message) {
  switch (message.type) {
    case 'snapshot':
      live.config = message.config
      live.status = message.status
      live.aircraft = withDemo(message.aircraft)
      break
    case 'aircraft':
      live.status = message.status
      live.aircraft = withDemo(message.aircraft)
      break
    case 'config':
      live.config = message.config
      break
    case 'alert':
      addAlert(message.alert)
      live.incidentSeq += 1
      break
    case 'incident':
      addIncident(message.incident)
      break
  }
}

function addAlert(alert) {
  const item = { ...alert, id: ++alertSeq, receivedAt: Date.now(), toast: true }
  live.alerts.unshift(item)
  live.alerts.splice(MAX_ALERTS)
  if (live.sound) beep()
  setTimeout(() => dismissToast(item.id), TOAST_SECONDS * 1000)
}

export function dismissToast(id) {
  const item = live.alerts.find((a) => a.id === id)
  if (item) item.toast = false
}

// Incidentes cargados desde este navegador (un avistamiento): no se le avisa a
// quien lo acaba de cargar. El aviso puede llegar antes o después de la respuesta.
const ownIncidents = new Set()
export function hideOwnIncident(id) {
  ownIncidents.add(id)
  for (const item of live.incidents) if (item.id === id) item.toast = false
}

function addIncident(incident) {
  const own = ownIncidents.has(incident.id)
  const item = { ...incident, key: ++alertSeq, receivedAt: Date.now(), toast: !own }
  live.incidents.unshift(item)
  live.incidents.splice(MAX_ALERTS)
  live.incidentSeq += 1
  if (own) return
  if (live.sound) beep()
  setTimeout(() => dismissIncident(item.key), TOAST_SECONDS * 1000)
}

export function dismissIncident(key) {
  const item = live.incidents.find((i) => i.key === key)
  if (item) item.toast = false
}
