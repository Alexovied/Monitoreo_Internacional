<script setup>
// Modo cielo: se apunta el celular al cielo y, sobre la imagen de la cámara,
// aparece una etiqueta encima de cada aeronave cercana (identificación,
// altitud, distancia y si va a entrar a la zona). Las que quedan fuera del
// cuadro se señalan con flechas en el borde.
//
// Usa la cámara trasera (getUserMedia), el GPS (geolocation) y la brújula y
// el giroscopio (DeviceOrientation). Los tres necesitan HTTPS. Sin sensores
// (computadora, permiso negado) queda un modo simulado: se arrastra para mirar.
import { computed, markRaw, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { kindOf } from '../incidents'
import { hideOwnIncident, live } from '../live'
import { session } from '../session'
import { SHAPES, shapeOf } from '../aircraftShapes'
import { modelFileFor } from '../skyModels'
import { altitude, dec1, heading as fmtHeading, flightradarUrl, identification, int, M_PER_FT, speed } from '../format'
import {
  aircraftAltitudeM,
  blendCamera,
  cameraFromHeadingPitch,
  cameraFromOrientation,
  cameraHeadingPitch,
  cardinal,
  direction,
  distanceBearing,
  edgePoint,
  focalForCover,
  lookAngles,
  norm360,
  project,
  rotateCameraYaw,
} from '../sky'

const router = useRouter()

const SETTINGS_KEY = 'avion.sky'
const DEFAULT_FOV = 66 // grados que abarca el lado largo de la cámara principal de un celular típico
const MAX_ARROWS = 8
const ARROW_GAP = 76 // separación entre flechas (círculo + texto)
const LABEL_W = 168
// Vista desde el centro de la zona: cuánto se "acerca" el cielo para que los
// aviones se vean arriba y no pegados al horizonte (1 = ángulos reales).
const DEFAULT_LIFT = 4
const FAR_MARGIN_M = 30000 // más allá del área de cobertura + esto, se considera "lejos"
const LABEL_H = 64 // con la línea de estado (en la zona / entra en…)
const LABEL_H_SHORT = 44

const secure = window.isSecureContext
const ORIENTATION_EVENT = 'ondeviceorientationabsolute' in window ? 'deviceorientationabsolute' : 'deviceorientation'
const needsPermission = () => typeof window.DeviceOrientationEvent?.requestPermission === 'function'
const screenAngle = () => screen.orientation?.angle ?? window.orientation ?? 0

const settings = reactive(loadSettings())
function loadSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}')
    return { fov: Number(saved.fov) || DEFAULT_FOV, offset: Number(saved.offset) || 0, lift: Number(saved.lift) || DEFAULT_LIFT }
  } catch {
    return { fov: DEFAULT_FOV, offset: 0, lift: DEFAULT_LIFT }
  }
}
watch(settings, () => {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings))
  } catch {
    /* sin almacenamiento */
  }
})

// --- estado ------------------------------------------------------------------

const started = ref(false)
const showSettings = ref(false)
const selectedHex = ref(null)
const videoRef = ref(null)
const canvasRef = ref(null)
const scene3d = shallowRef(null) // SkyScene, si el navegador tiene WebGL
const rootRef = ref(null)

const cameraState = ref('off') // off | starting | on | denied | unavailable
const sensorState = ref('off') // off | waiting | on | none
const gps = reactive({ state: 'off', lat: null, lon: null, alt: null, accuracy: null }) // state: off | waiting | on | denied
const size = reactive({ w: window.innerWidth, h: window.innerHeight, vw: 0, vh: 0 })

// Cámara virtual: la que llega de los sensores (target) y la que se dibuja,
// suavizada en cada cuadro.
let targetCam = null
const cam = ref(null)
const manual = reactive({ heading: 0, pitch: 12 }) // modo simulado
const simulated = computed(() => started.value && sensorState.value === 'none')

let stream = null
let watchId = null
let rafId = null
let sensorTimer = null
let iosOffsetGood = false // ya se midió con el celular en una posición confiable
let iosOffset = null // iOS: alpha es relativo; webkitCompassHeading da el norte

// Posición del usuario: GPS, o el centro de la zona mientras no hay GPS.
// Lejos de la zona (p. ej. en Argentina mirando el sistema de Brasil) los
// aviones están a cientos de km: quedan pegados al horizonte o debajo, por la
// curvatura de la Tierra. En ese caso se mira "como si estuviera" en el centro
// de la zona, salvo que el usuario elija su ubicación real.
const zoneDistM = computed(() => {
  const z = live.config?.zone
  if (gps.state !== 'on' || !z) return null
  return distanceBearing(gps.lat, gps.lon, z.lat, z.lon).dist
})
const farFromZone = computed(() => {
  const cfg = live.config
  if (zoneDistM.value === null || !cfg) return false
  return zoneDistM.value > (cfg.query_radius_nm || 50) * 1852 + FAR_MARGIN_M
})
const forceOwnPosition = ref(false)
const virtual = computed(() => farFromZone.value && !forceOwnPosition.value)

const observer = computed(() => {
  if (gps.state === 'on' && !virtual.value) return { lat: gps.lat, lon: gps.lon, alt: gps.alt ?? 0, source: 'gps' }
  const z = live.config?.zone
  return z ? { lat: z.lat, lon: z.lon, alt: 0, source: 'zone' } : null
})
// Con tu ubicación real los ángulos son los reales (para que la etiqueta caiga
// sobre el avión); desde el centro de la zona se levanta el cielo.
const lift = computed(() => (observer.value?.source === 'zone' ? settings.lift : 1))

// --- cuentas por aeronave ------------------------------------------------------

// Lo que no depende de hacia dónde mira la cámara (cambia solo con los datos).
const targets = computed(() => {
  const obs = observer.value
  if (!obs) return []
  return live.aircraft
    .map((ac) => {
      const heightM = aircraftAltitudeM(ac.alt_baro, obs.alt)
      if (heightM === null) return null
      const { dist, bearing } = distanceBearing(obs.lat, obs.lon, ac.lat, ac.lon)
      const { azimuth, elevation } = lookAngles(dist, bearing, heightM, obs.alt, lift.value)
      return { ac, dist, azimuth, elevation, dir: direction(azimuth, elevation), status: statusOf(ac) }
    })
    .filter(Boolean)
    .sort((a, b) => a.dist - b.dist)
})

function statusOf(ac) {
  if (ac.inside) return ac.excluded ? 'quiet' : 'inside'
  if (typeof ac.eta_min === 'number' && !ac.excluded) return 'eta'
  return 'normal'
}

const zoneTarget = computed(() => {
  const obs = observer.value
  const z = live.config?.zone
  if (!obs || !z) return null
  const { dist, bearing } = distanceBearing(obs.lat, obs.lon, z.lat, z.lon)
  if (dist < 300) return { inside: true, dist }
  const { azimuth, elevation } = lookAngles(dist, bearing, obs.alt, obs.alt)
  return { inside: dist < z.radius_nm * 1852, dist, azimuth, dir: direction(azimuth, elevation) }
})

const focal = computed(() => focalForCover(size.w, size.h, size.vw, size.vh, settings.fov))

// Cámara con la corrección de brújula del usuario.
const viewCam = computed(() => (cam.value ? rotateCameraYaw(cam.value, settings.offset) : null))
const look = computed(() => (viewCam.value ? cameraHeadingPitch(viewCam.value) : null))

// Posición en pantalla de cada aeronave, etiquetas sin superponerse y flechas.
const layout = computed(() => {
  const c = viewCam.value
  if (!c) return { labels: [], arrows: [], hiddenArrows: 0 }
  const { w, h } = size
  const onscreen = []
  const offscreen = []
  for (const t of targets.value) {
    const p = project(c, t.dir, w, h, focal.value)
    if (p.visible) onscreen.push({ ...t, x: p.x, y: p.y, px: modelPx(t.dist / lift.value), markerSize: modelPx(t.dist / lift.value) * 0.7 })
    else offscreen.push({ t, p })
  }
  // Varias en la misma dirección: se reparten a lo largo del borde para que
  // no se tapen (la más cercana queda en su lugar exacto).
  const arrows = []
  for (const { t, p } of offscreen.slice(0, MAX_ARROWS)) {
    const e = edgePoint(p.angle, w, h, 34, 96)
    const vertical = e.x <= 35 || e.x >= w - 35 // borde izquierdo o derecho: se corre en y
    let pos = null
    for (let k = 0; k < 2 * MAX_ARROWS && !pos; k++) {
      const shift = Math.ceil(k / 2) * ARROW_GAP * (k % 2 ? 1 : -1)
      const x = vertical ? e.x : Math.min(Math.max(e.x + shift, 34), w - 34)
      const y = vertical ? Math.min(Math.max(e.y + shift, 96), h - 96) : e.y
      const clash =
        arrows.some((a) => Math.abs(a.x - x) < ARROW_GAP - 2 && Math.abs(a.y - y) < ARROW_GAP + 10) ||
        onscreen.some((o) => Math.abs(o.x - x) < 30 + o.markerSize / 2 && o.y > y - 30 - o.markerSize / 2 && o.y < y + 56 + o.markerSize / 2)
      if (!clash) pos = { x, y }
    }
    pos ||= e
    arrows.push({ ...t, x: pos.x, y: pos.y, deg: (p.angle * 180) / Math.PI })
  }

  // Obstáculos: los puntos de todas las aeronaves, las flechas y las etiquetas
  // ya ubicadas. Se prueba arriba, abajo y a los costados del punto; si todo
  // choca, la etiqueta sube hasta encontrar lugar (con una línea al punto).
  const obstacles = [
    ...onscreen.map((t) => ({ hex: t.ac.hex, x: t.x - t.markerSize / 2, y: t.y - t.markerSize / 2, w: t.markerSize, h: t.markerSize })),
    ...arrows.map((a) => ({ x: a.x - 26, y: a.y - 26, w: 52, h: 76 })),
    { x: 0, y: 0, w, h: 64 }, // barra superior
    { x: 0, y: h - 40, w, h: 40 }, // estado de cámara, GPS y brújula
  ]
  const labels = []
  for (const t of onscreen) {
    const lh = chip(t) ? LABEL_H : LABEL_H_SHORT
    const gap = t.markerSize / 2 + 8
    const clampX = (x) => Math.min(Math.max(x, 6), w - LABEL_W - 6)
    const candidates = [
      [clampX(t.x - LABEL_W / 2), t.y - gap - lh],
      [clampX(t.x - LABEL_W / 2), t.y + gap],
      [clampX(t.x + gap), t.y - lh / 2],
      [clampX(t.x - gap - LABEL_W), t.y - lh / 2],
    ]
    for (let k = 1; k <= 8; k++) {
      const step = k * (LABEL_H_SHORT / 2 + 4)
      candidates.push([clampX(t.x - LABEL_W / 2), t.y - gap - lh - step], [clampX(t.x - LABEL_W / 2), t.y + gap + step])
      candidates.push([clampX(t.x + gap), t.y - lh / 2 - step], [clampX(t.x - gap - LABEL_W), t.y - lh / 2 - step])
      candidates.push([clampX(t.x + gap), t.y - lh / 2 + step], [clampX(t.x - gap - LABEL_W), t.y - lh / 2 + step])
    }
    // Lo que tapa cada lugar posible (área superpuesta + salirse de la pantalla);
    // se elige el primero libre o, si no hay, el que menos tapa.
    const cost = ([x, y]) => {
      let c = (Math.max(0, 64 - y) + Math.max(0, y + lh - (h - 40))) * LABEL_W * 4
      for (const o of obstacles) {
        if (o.hex === t.ac.hex) continue
        const ox = Math.min(x + LABEL_W + 3, o.x + o.w + 3) - Math.max(x, o.x)
        const oy = Math.min(y + lh + 3, o.y + o.h + 3) - Math.max(y, o.y)
        if (ox > 0 && oy > 0) c += ox * oy
      }
      return c
    }
    let best = candidates[0]
    let bestCost = Infinity
    for (const cand of candidates) {
      const c = cost(cand)
      if (c < bestCost) [best, bestCost] = [cand, c]
      if (c === 0) break
    }
    const [lx, ly] = best
    obstacles.push({ x: lx, y: ly, w: LABEL_W, h: lh })
    // Línea del punto al borde más cercano de la etiqueta (si no está pegada).
    const ex = Math.min(Math.max(t.x, lx), lx + LABEL_W)
    const ey = Math.min(Math.max(t.y, ly), ly + lh)
    const far = Math.hypot(ex - t.x, ey - t.y) > gap + 4
    // La línea arranca en el borde del modelo, no en su centro.
    const len = Math.hypot(ex - t.x, ey - t.y) || 1
    const k = Math.min(t.markerSize * 0.55, len) / len
    const leader = far ? { x1: t.x + (ex - t.x) * k, y1: t.y + (ey - t.y) * k, x2: ex, y2: ey } : null
    labels.push({ ...t, lx, ly, lh, leader })
  }
  return { labels, arrows, hiddenArrows: Math.max(0, offscreen.length - MAX_ARROWS) }
})

// Tamaño en pantalla del modelo 3D: más cerca, más grande.
function modelPx(dist) {
  return Math.max(64, Math.min(190, 190 - dist / 130))
}

// Ascenso o descenso en grados (velocidad vertical en ft/min sobre la horizontal).
const RAD_DEG = Math.PI / 180
function climbDeg(ac) {
  if (!ac.gs || !ac.baro_rate) return 0
  return Math.max(-20, Math.min(20, Math.atan2(ac.baro_rate, ac.gs * 101.27) / RAD_DEG))
}

// Escalera de elevación: arcos cada 15° alrededor de donde mira la cámara, con
// los grados, para leer a qué altura del cielo está cada avión.
const ladder = computed(() => {
  const c = viewCam.value
  if (!c || !look.value) return []
  const out = []
  for (const el of [15, 30, 45, 60, 75]) {
    let path = ''
    let label = null
    for (let d = -30; d <= 30; d += 3) {
      const p = project(c, direction(look.value.heading + d, el), size.w, size.h, focal.value)
      if (p.behind) continue
      path += `${path ? 'L' : 'M'}${p.x.toFixed(1)},${p.y.toFixed(1)}`
      if (d === -12 && p.visible) label = { x: p.x, y: p.y }
    }
    if (path) out.push({ el, path, label })
  }
  return out
})

// Horizonte y marcas de brújula (cada 15°, con letras cada 45°).
const horizon = computed(() => {
  const c = viewCam.value
  if (!c) return { path: '', ticks: [] }
  const { w, h } = size
  let path = ''
  let pen = false
  const ticks = []
  for (let az = 0; az <= 360; az += 3) {
    const p = project(c, direction(az, 0), w, h, focal.value)
    const near = !p.behind && p.x > -w && p.x < 2 * w && p.y > -h && p.y < 2 * h
    if (near) {
      path += `${pen ? 'L' : 'M'}${p.x.toFixed(1)},${p.y.toFixed(1)}`
      pen = true
      if (az % 15 === 0 && az < 360 && p.visible) {
        ticks.push({ az, x: p.x, y: p.y, label: az % 45 === 0 ? cardinal(az) : String(az), major: az % 45 === 0 })
      }
    } else {
      pen = false
    }
  }
  return { path, ticks }
})

const zoneMarker = computed(() => {
  const z = zoneTarget.value
  const c = viewCam.value
  if (!z || z.inside || !c) return null
  const p = project(c, z.dir, size.w, size.h, focal.value)
  return p.visible ? { x: p.x, y: p.y, dist: z.dist } : null
})

const selected = computed(() => targets.value.find((t) => t.ac.hex === selectedHex.value) || null)

// Foto real de la aeronave elegida (Planespotters.net, por su código ICAO). Sus
// condiciones piden mostrar el nombre del fotógrafo y enlazar a la foto.
const photo = ref(null)
const photoCache = new Map()
watch(selectedHex, async (hex) => {
  photo.value = null
  if (!hex || !/^[0-9a-f]{6}$/i.test(hex)) return
  if (!photoCache.has(hex)) {
    photoCache.set(
      hex,
      fetch(`https://api.planespotters.net/pub/photos/hex/${hex}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          const p = data?.photos?.[0]
          const src = p?.thumbnail_large?.src || p?.thumbnail?.src
          return src ? { src, link: p.link, photographer: p.photographer } : null
        })
        .catch(() => null),
    )
  }
  const result = await photoCache.get(hex)
  if (selectedHex.value === hex) photo.value = result
})
const visibleCount = computed(() => layout.value.labels.length)

// --- textos ------------------------------------------------------------------

function km(m) {
  const v = m / 1000
  return v >= 10 ? `${int(v)} km` : `${dec1(v)} km`
}

function shortName(ac) {
  return ac.flight || ac.registration || ac.hex.toUpperCase()
}

function altShort(alt) {
  if (alt === 'ground') return 'en tierra'
  return typeof alt === 'number' ? `${int(alt)} ft · ${int(alt * M_PER_FT)} m` : 'sin altitud'
}

function etaText(min) {
  return min < 1 ? 'Entra en menos de 1 min' : `Entra en ~${Math.round(min)} min`
}

function chip(t) {
  // Comportamiento raro reciente (backend/monitor/behavior.py): va primero.
  const odd = t.ac.behavior?.[0]
  if (odd) return { text: kindOf(odd).short, cls: `text-bg-${kindOf(odd).color}` }
  if (t.status === 'inside') return { text: 'EN LA ZONA', cls: 'text-bg-danger' }
  if (t.status === 'quiet') return { text: 'EN LA ZONA · SIN ALERTA', cls: 'text-bg-secondary' }
  if (t.status === 'eta') return { text: etaText(t.ac.eta_min), cls: 'text-bg-warning' }
  return null
}

// --- avistamiento: algo que se ve y no tiene transponder -----------------------
//
// Se apunta la mira (el centro de la pantalla) al objeto. El servidor dice si en
// esa dirección hay alguna aeronave con transponder; si no hay, se guarda como
// avistamiento con la dirección, la hora y una foto de la cámara.
const canReport = computed(() => !!session.user && !session.user.is_guest)
const report = reactive({ step: 'off', aim: null, result: null, note: '', error: '', photo: null, saved: null })
// off | aim | checking | match | none | saving | done

function openReport() {
  selectedHex.value = null
  showSettings.value = false
  Object.assign(report, { step: 'aim', result: null, note: '', error: '', photo: null, saved: null })
}

function closeReport() {
  report.step = 'off'
}

function reportBody() {
  const obs = observer.value
  return {
    observer: obs.source === 'gps' ? { source: 'gps', lat: obs.lat, lon: obs.lon, alt: obs.alt } : { source: 'zone' },
    azimuth: norm360(report.aim.heading),
    elevation: report.aim.pitch,
    lift: lift.value,
  }
}

// Cuadro actual de la cámara como JPEG (o null si no hay cámara).
function snapshot() {
  const video = videoRef.value
  if (cameraState.value !== 'on' || !video?.videoWidth) return null
  try {
    const k = Math.min(1, 960 / Math.max(video.videoWidth, video.videoHeight))
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(video.videoWidth * k)
    canvas.height = Math.round(video.videoHeight * k)
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height)
    return canvas.toDataURL('image/jpeg', 0.72)
  } catch {
    return null
  }
}

async function checkSighting() {
  if (!look.value || !observer.value) return
  // La dirección y la foto son las del momento en que se toca el botón.
  report.aim = { ...look.value }
  report.photo = snapshot()
  report.step = 'checking'
  report.error = ''
  try {
    report.result = await api('/sightings/check/', { method: 'POST', body: reportBody() })
    report.step = report.result.match ? 'match' : 'none'
  } catch (err) {
    report.error = err.message
    report.step = 'aim'
  }
}

async function saveSighting(force = false) {
  const previous = report.step
  report.step = 'saving'
  report.error = ''
  try {
    report.saved = await api('/sightings/', {
      method: 'POST',
      body: { ...reportBody(), note: report.note.trim().slice(0, 300), photo: report.photo, force },
    })
    hideOwnIncident(report.saved.id) // el aviso para los demás: a quien lo cargó no le hace falta
    report.step = 'done'
  } catch (err) {
    report.error = err.message
    report.step = previous
  }
}

function openSaved() {
  const id = report.saved?.id
  closeReport()
  router.push(id ? { name: 'replay', params: { id } } : { name: 'registry' })
}

const lookText = computed(() => {
  if (!look.value) return ''
  const { heading, pitch } = look.value
  const sign = pitch >= 0 ? '+' : '−'
  return `${String(Math.round(heading) % 360).padStart(3, '0')}° ${cardinal(heading)} · ${sign}${Math.abs(Math.round(pitch))}°`
})

// --- sensores ----------------------------------------------------------------

async function start({ simulate = false } = {}) {
  started.value = true
  startGps()
  if (simulate || !secure) {
    useSimulated()
  } else {
    // iOS: el permiso de orientación solo se puede pedir dentro del toque.
    // Se pide primero, antes de cualquier await.
    const permission = needsPermission() ? window.DeviceOrientationEvent.requestPermission().catch(() => 'denied') : null
    startCamera()
    if (permission) await permission
    startOrientation()
  }
  loop()
}

function useSimulated() {
  sensorState.value = 'none'
  // Arranca mirando a la aeronave más cercana, un poco arriba del horizonte.
  const first = targets.value[0]
  manual.heading = first ? Math.round(first.azimuth) : 0
  manual.pitch = first ? Math.max(3, Math.min(40, Math.round(first.elevation))) : 12
  targetCam = cameraFromHeadingPitch(manual.heading, manual.pitch)
}

async function startCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    cameraState.value = 'unavailable'
    return
  }
  cameraState.value = 'starting'
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } },
    })
    if (!started.value) return stopCamera()
    const video = videoRef.value
    video.srcObject = stream
    video.onloadedmetadata = () => {
      size.vw = video.videoWidth
      size.vh = video.videoHeight
    }
    await video.play().catch(() => {})
    cameraState.value = 'on'
  } catch (err) {
    cameraState.value = err?.name === 'NotAllowedError' ? 'denied' : 'unavailable'
  }
}

function stopCamera() {
  stream?.getTracks().forEach((t) => t.stop())
  stream = null
}

function startGps() {
  if (!navigator.geolocation || !secure) return
  gps.state = 'waiting'
  watchId = navigator.geolocation.watchPosition(
    (pos) => {
      Object.assign(gps, {
        state: 'on',
        lat: pos.coords.latitude,
        lon: pos.coords.longitude,
        alt: pos.coords.altitude,
        accuracy: pos.coords.accuracy,
      })
    },
    (err) => {
      if (err.code === err.PERMISSION_DENIED) gps.state = 'denied'
    },
    { enableHighAccuracy: true, maximumAge: 5000, timeout: 20000 },
  )
}

function startOrientation() {
  sensorState.value = 'waiting'
  window.addEventListener(ORIENTATION_EVENT, onOrientation)
  // Sin lecturas con norte en 3 s (computadora o equipo sin brújula): simulado.
  sensorTimer = setTimeout(() => {
    if (sensorState.value === 'waiting') {
      window.removeEventListener(ORIENTATION_EVENT, onOrientation)
      useSimulated()
    }
  }, 3000)
}

function onOrientation(e) {
  if (e.alpha == null || e.beta == null || e.gamma == null) return
  let alpha = e.alpha
  if (typeof e.webkitCompassHeading === 'number' && e.webkitCompassHeading >= 0) {
    // iOS: alpha arranca en un rumbo arbitrario. En cualquier posición vale
    // rumbo = 360 − alpha (con alpha respecto del norte), así que se calcula
    // la diferencia y se suaviza para que la brújula no tiemble.
    // La brújula de iOS solo es confiable con el celular por debajo de la
    // vertical; apuntando al cielo se deja la última diferencia buena (alpha
    // sigue al giroscopio, que no se desvía en ese rato).
    const offset = norm360(360 - e.webkitCompassHeading - e.alpha)
    const reliable = Math.abs(e.beta) < 70
    if (iosOffset === null || (reliable && !iosOffsetGood)) iosOffset = offset
    else if (reliable) iosOffset += 0.1 * angleDiff(offset, iosOffset)
    if (reliable) iosOffsetGood = true
    alpha = e.alpha + iosOffset
  } else if (!e.absolute && ORIENTATION_EVENT === 'deviceorientation') {
    return // orientación relativa, sin norte: no sirve
  }
  targetCam = cameraFromOrientation(alpha, e.beta, e.gamma, screenAngle())
  if (sensorState.value !== 'on') {
    sensorState.value = 'on'
    clearTimeout(sensorTimer)
  }
}

const angleDiff = (a, b) => ((a - b + 540) % 360) - 180

function loop(time = 0) {
  rafId = requestAnimationFrame(loop)
  if (!targetCam) return
  // Con sensores se suaviza; en simulado se sigue el arrastre tal cual.
  cam.value = simulated.value ? targetCam : blendCamera(cam.value, targetCam, 0.25)
  render3d(time)
}

function render3d(time) {
  const sc = scene3d.value
  if (!sc || !viewCam.value) return
  const items = layout.value.labels.map((t) => ({
    hex: t.ac.hex,
    shape: shapeOf(t.ac),
    file: modelFileFor(t.ac),
    status: t.status,
    dir: t.dir,
    track: t.ac.track ?? t.ac.true_heading ?? 0,
    climbDeg: climbDeg(t.ac),
    px: t.px,
  }))
  sc.render(viewCam.value, focal.value, items, time)
}

async function init3d() {
  try {
    const { SkyScene } = await import('../skyScene')
    if (!canvasRef.value) return
    const sc = new SkyScene(canvasRef.value)
    sc.setSize(size.w, size.h)
    scene3d.value = markRaw(sc)
  } catch (err) {
    console.warn('Modo cielo sin 3D (WebGL no disponible):', err)
  }
}

// Modo simulado: arrastrar para mirar alrededor.
let drag = null
function onPointerDown(e) {
  if (!simulated.value || e.target.closest('button, a, .sky-label, .sky-arrow')) return
  drag = { x: e.clientX, y: e.clientY, heading: manual.heading, pitch: manual.pitch }
  e.currentTarget.setPointerCapture?.(e.pointerId)
}
function onPointerMove(e) {
  if (!drag) return
  const degPerPx = (Math.atan(size.w / 2 / focal.value) * 2 * 180) / Math.PI / size.w
  manual.heading = norm360(drag.heading - (e.clientX - drag.x) * degPerPx)
  manual.pitch = Math.max(-30, Math.min(85, drag.pitch + (e.clientY - drag.y) * degPerPx))
  targetCam = cameraFromHeadingPitch(manual.heading, manual.pitch)
}
function onPointerUp() {
  drag = null
}

// Tocar una flecha: en simulado gira la vista hacia esa aeronave.
function onArrow(t) {
  if (simulated.value) {
    manual.heading = Math.round(t.azimuth)
    manual.pitch = Math.max(-10, Math.min(80, Math.round(t.elevation) + 3))
    targetCam = cameraFromHeadingPitch(manual.heading, manual.pitch)
  }
  selectedHex.value = t.ac.hex
}

// El mapa centra y selecciona la aeronave pedida en focusHex al abrirse.
function openOnMap(hex) {
  live.focusHex = hex
  router.push('/')
}

function close() {
  router.push('/')
}

function onResize() {
  size.w = rootRef.value?.clientWidth || window.innerWidth
  size.h = rootRef.value?.clientHeight || window.innerHeight
  scene3d.value?.setSize(size.w, size.h)
}

onMounted(() => {
  onResize()
  window.addEventListener('resize', onResize)
  screen.orientation?.addEventListener?.('change', onResize)
  init3d()
  // ?simulado=1 abre directo el modo simulado (para mostrar en una computadora).
  if (new URLSearchParams(location.search).has('simulado')) start({ simulate: true })
})

onBeforeUnmount(() => {
  started.value = false
  scene3d.value?.dispose()
  cancelAnimationFrame(rafId)
  clearTimeout(sensorTimer)
  window.removeEventListener('resize', onResize)
  screen.orientation?.removeEventListener?.('change', onResize)
  window.removeEventListener(ORIENTATION_EVENT, onOrientation)
  if (watchId !== null) navigator.geolocation.clearWatch(watchId)
  stopCamera()
})
</script>

<template>
  <div
    ref="rootRef"
    class="sky"
    :class="{ 'sky-sim': simulated || cameraState !== 'on', dragging: simulated }"
    @pointerdown="onPointerDown"
    @pointermove="onPointerMove"
    @pointerup="onPointerUp"
    @pointercancel="onPointerUp"
  >
    <video ref="videoRef" class="sky-video" :class="{ on: cameraState === 'on' }" playsinline muted autoplay></video>

    <!-- Horizonte, marcas de brújula y centro de la zona -->
    <svg v-if="cam" class="sky-overlay" :width="size.w" :height="size.h" aria-hidden="true">
      <g v-for="l in ladder" :key="`el-${l.el}`">
        <path :d="l.path" class="ladder" />
        <text v-if="l.label" :x="l.label.x" :y="l.label.y - 4" class="ladder-label">{{ l.el }}°</text>
      </g>
      <path :d="horizon.path" class="horizon" />
      <g v-for="t in horizon.ticks" :key="t.az">
        <line :x1="t.x" :y1="t.y - (t.major ? 10 : 6)" :x2="t.x" :y2="t.y + (t.major ? 10 : 6)" class="tick" />
        <text :x="t.x" :y="t.y + 26" class="tick-label" :class="{ major: t.major, north: t.az === 0 }">{{ t.label }}</text>
      </g>
      <g v-if="zoneMarker" class="zone-marker">
        <circle :cx="zoneMarker.x" :cy="zoneMarker.y" r="9" />
        <line :x1="zoneMarker.x" :y1="zoneMarker.y - 9" :x2="zoneMarker.x" :y2="zoneMarker.y - 36" />
        <text :x="zoneMarker.x" :y="zoneMarker.y - 42">Zona · {{ km(zoneMarker.dist) }}</text>
      </g>
    </svg>

    <!-- Modelos 3D de las aeronaves (three.js) -->
    <canvas ref="canvasRef" class="sky-3d" :style="{ width: `${size.w}px`, height: `${size.h}px` }"></canvas>

    <!-- Aeronaves en cuadro: líneas, puntos y etiquetas (en ese orden, para que
         las etiquetas queden siempre arriba) -->
    <svg class="sky-leader" :width="size.w" :height="size.h" aria-hidden="true">
      <template v-for="t in layout.labels" :key="t.ac.hex">
        <line v-if="t.leader" :x1="t.leader.x1" :y1="t.leader.y1" :x2="t.leader.x2" :y2="t.leader.y2" :class="t.status" />
      </template>
    </svg>
    <div
      v-for="t in layout.labels"
      :key="`m-${t.ac.hex}`"
      v-show="!scene3d || t.status === 'inside'"
      class="sky-marker"
      :class="[t.status, { ring: scene3d }]"
      :style="{ left: `${t.x}px`, top: `${t.y}px`, width: `${t.markerSize}px`, height: `${t.markerSize}px` }"
    ></div>
    <button
      v-for="t in layout.labels"
      :key="`l-${t.ac.hex}`"
      type="button"
      class="sky-label"
      :class="[t.status, { selected: selectedHex === t.ac.hex }]"
      :style="{ left: `${t.lx}px`, top: `${t.ly}px`, width: `${LABEL_W}px`, height: `${t.lh}px` }"
      :data-hex="t.ac.hex"
      @click="selectedHex = t.ac.hex"
    >
      <span class="d-flex align-items-center gap-1 lh-1">
        <span class="sky-icon" v-html="SHAPES[shapeOf(t.ac)].svg"></span>
        <b class="text-truncate">{{ shortName(t.ac) }}</b>
        <span class="ms-auto opacity-75 small">{{ km(t.dist) }}</span>
      </span>
      <span class="d-flex small mt-1 lh-1 opacity-75">
        {{ altShort(t.ac.alt_baro) }}<span class="ms-auto" title="Elevación sobre el horizonte">↑{{ int(Math.round(t.elevation)) }}°</span>
      </span>
      <span v-if="chip(t)" class="badge mt-1" :class="chip(t).cls">{{ chip(t).text }}</span>
    </button>

    <!-- Aeronaves fuera del cuadro: flechas en el borde -->
    <button
      v-for="t in layout.arrows"
      :key="`a-${t.ac.hex}`"
      type="button"
      class="sky-arrow"
      :class="t.status"
      :style="{ left: `${t.x}px`, top: `${t.y}px` }"
      :title="`${shortName(t.ac)} · ${km(t.dist)}`"
      @click="onArrow(t)"
    >
      <i class="bi bi-caret-up-fill" :style="{ transform: `rotate(${t.deg}deg)` }"></i>
      <span class="sky-arrow-text">{{ shortName(t.ac) }}<br />{{ km(t.dist) }}</span>
    </button>

    <!-- Barra superior -->
    <div class="sky-top d-flex align-items-start gap-2">
      <button type="button" class="btn btn-dark btn-sm sky-btn" aria-label="Volver al mapa" @click="close">
        <i class="bi bi-x-lg"></i>
      </button>
      <div class="sky-pill flex-grow-1 text-center">
        <div class="fw-semibold"><i class="bi bi-binoculars-fill me-1"></i>Modo cielo</div>
        <div v-if="started" class="small tabular">
          <template v-if="look">{{ lookText }} · </template>{{ visibleCount }} en pantalla de {{ targets.length }}
        </div>
      </div>
      <button
        v-if="started"
        type="button"
        class="btn btn-dark btn-sm sky-btn"
        aria-label="Ajustes del Modo cielo"
        :class="{ active: showSettings }"
        @click="showSettings = !showSettings"
      >
        <i class="bi bi-sliders"></i>
      </button>
    </div>

    <!-- Estado de cámara, GPS y brújula -->
    <div v-if="started" class="sky-status d-flex flex-wrap gap-1 justify-content-center">
      <span class="badge" :class="cameraState === 'on' ? 'text-bg-success' : 'text-bg-secondary'">
        <i class="bi me-1" :class="cameraState === 'on' ? 'bi-camera-video-fill' : 'bi-camera-video-off-fill'"></i>
        {{ { on: 'Cámara', starting: 'Abriendo cámara…', denied: 'Cámara sin permiso', unavailable: 'Sin cámara', off: 'Sin cámara' }[cameraState] }}
      </span>
      <span class="badge" :class="gps.state === 'on' ? 'text-bg-success' : 'text-bg-secondary'">
        <i class="bi bi-geo-alt-fill me-1"></i>
        <template v-if="gps.state === 'on' && virtual">Vista desde el centro de la zona</template>
        <template v-else-if="gps.state === 'on'">GPS ±{{ int(gps.accuracy) }} m</template>
        <template v-else-if="gps.state === 'waiting'">Buscando GPS… (centro de la zona)</template>
        <template v-else>Sin GPS · desde el centro de la zona</template>
      </span>
      <span class="badge" :class="sensorState === 'on' ? 'text-bg-success' : 'text-bg-secondary'">
        <i class="bi bi-compass-fill me-1"></i>
        {{ { on: 'Brújula', waiting: 'Esperando brújula…', none: 'Simulado: arrastrá para mirar', off: '' }[sensorState] }}
      </span>
      <span v-if="live.demo" class="badge text-bg-warning">MODO DEMO</span>
      <span v-if="!live.connected" class="badge text-bg-danger"><i class="bi bi-wifi-off me-1"></i>Reconectando…</span>
    </div>

    <!-- Lejos de la zona: se mira desde el centro de la zona -->
    <div v-if="started && farFromZone && !selected && report.step === 'off'" class="sky-panel sky-far small" data-sky-far>
      <template v-if="virtual">
        <i class="bi bi-geo-fill text-warning me-1"></i>
        Estás a <b>{{ km(zoneDistM) }}</b> de la zona: desde acá esos aviones no se ven (quedan detrás del horizonte).
        Te mostramos el cielo <b>como si estuvieras en el centro de la zona</b>, con tu brújula.
        <template v-if="lift > 1">Los aviones se dibujan más arriba que en la realidad (cielo ×{{ lift }}, se cambia en ajustes).</template>
        <button type="button" class="btn btn-link btn-sm text-light p-0 ms-1 align-baseline" @click="forceOwnPosition = true">Usar mi ubicación</button>
      </template>
      <template v-else>
        <i class="bi bi-geo-alt-fill me-1"></i>Usando tu ubicación real, a {{ km(zoneDistM) }} de la zona.
        <button type="button" class="btn btn-link btn-sm text-light p-0 ms-1 align-baseline" @click="forceOwnPosition = false">Ver desde la zona</button>
      </template>
    </div>

    <!-- Avistamiento: mira en el centro y panel con el resultado -->
    <div v-if="report.step !== 'off' && report.step !== 'done'" class="sky-crosshair" aria-hidden="true">
      <span></span>
    </div>
    <button
      v-if="started && canReport && cam && report.step === 'off' && !selected"
      type="button"
      class="btn btn-info shadow rounded-pill sky-report-btn"
      title="¿Ves algo en el cielo que no aparece acá? Apuntale y reportalo"
      data-sky-report
      @click="openReport"
    >
      <i class="bi bi-eye-fill me-1"></i>Reportar
    </button>
    <div v-if="report.step !== 'off'" class="sky-panel sky-card sky-report small" data-sky-report-panel>
      <div class="d-flex align-items-center gap-2 mb-1">
        <strong><i class="bi bi-eye-fill me-1"></i>Reportar lo que veo</strong>
        <button type="button" class="btn-close btn-close-white ms-auto" aria-label="Cerrar" @click="closeReport"></button>
      </div>

      <template v-if="report.step === 'aim' || report.step === 'checking'">
        <div>Poné la mira sobre lo que ves en el cielo y tocá <b>Comprobar</b>. Te decimos si es una aeronave con transponder.</div>
        <div class="tabular opacity-75 mt-1">Mira: {{ lookText }}</div>
        <div v-if="report.error" class="text-warning mt-1">{{ report.error }}</div>
        <button type="button" class="btn btn-info btn-sm mt-2 w-100" :disabled="report.step === 'checking' || !look" data-sky-check @click="checkSighting">
          <span v-if="report.step === 'checking'" class="spinner-border spinner-border-sm me-1"></span>Comprobar
        </button>
      </template>

      <template v-else-if="report.step === 'match'">
        <div>
          <i class="bi bi-check-circle-fill text-success me-1"></i>Ahí está <b>{{ report.result.match.label }}</b>
          <template v-if="report.result.match.type"> ({{ report.result.match.type }})</template>,
          a {{ int(report.result.match.separation) }}° de la mira y {{ km(report.result.match.distance_m) }}. Tiene transponder.
        </div>
        <div v-if="report.error" class="text-warning mt-1">{{ report.error }}</div>
        <div class="d-flex gap-2 mt-2">
          <button type="button" class="btn btn-outline-light btn-sm flex-grow-1" @click="closeReport">Es ese</button>
          <button type="button" class="btn btn-warning btn-sm flex-grow-1" data-sky-force @click="saveSighting(true)">No es ese: reportar</button>
        </div>
      </template>

      <template v-else-if="report.step === 'none' || report.step === 'saving'">
        <div>
          <i class="bi bi-exclamation-triangle-fill text-warning me-1"></i><b>No hay ninguna aeronave con transponder en esa dirección.</b>
          <template v-if="report.result.candidates.length">
            La más cercana es {{ report.result.candidates[0].label }}, a {{ int(report.result.candidates[0].separation) }}° de la mira.
          </template>
          <template v-else>El sistema no ve ninguna aeronave en el área.</template>
        </div>
        <div v-if="!report.result.feed_ok" class="text-warning mt-1">Ojo: en este momento la fuente de datos no responde, puede faltar tránsito.</div>
        <input v-model="report.note" type="text" class="form-control form-control-sm mt-2" maxlength="300" placeholder="¿Qué ves? (opcional: avioneta blanca, dron…)" data-sky-note />
        <div class="opacity-75 mt-1">
          Se guarda la dirección<template v-if="report.photo">, la hora y una foto de la cámara</template><template v-else> y la hora</template>.
        </div>
        <div v-if="report.error" class="text-warning mt-1">{{ report.error }}</div>
        <div class="d-flex gap-2 mt-2">
          <button type="button" class="btn btn-outline-light btn-sm" :disabled="report.step === 'saving'" @click="report.step = 'aim'">Volver a apuntar</button>
          <button type="button" class="btn btn-info btn-sm flex-grow-1" :disabled="report.step === 'saving'" data-sky-save @click="saveSighting(false)">
            <span v-if="report.step === 'saving'" class="spinner-border spinner-border-sm me-1"></span>Guardar avistamiento
          </button>
        </div>
      </template>

      <template v-else-if="report.step === 'done'">
        <div><i class="bi bi-check-circle-fill text-success me-1"></i>Avistamiento guardado. Quedó en el registro y se avisó a los demás.</div>
        <div class="d-flex gap-2 mt-2">
          <button type="button" class="btn btn-outline-light btn-sm flex-grow-1" @click="closeReport">Seguir mirando</button>
          <button type="button" class="btn btn-info btn-sm flex-grow-1" data-sky-open-saved @click="openSaved">Ver en el registro</button>
        </div>
      </template>
    </div>

    <!-- Ajustes: campo de visión y corrección de brújula -->
    <div v-if="showSettings" class="sky-panel sky-settings">
      <label class="form-label small mb-1" for="sky-fov">
        Campo de visión de la cámara: <b>{{ settings.fov }}°</b>
      </label>
      <input id="sky-fov" v-model.number="settings.fov" type="range" class="form-range" min="40" max="100" step="1" />
      <div class="form-text text-light opacity-75 mb-2">Si las etiquetas se separan del avión hacia los bordes, cambialo.</div>
      <label class="form-label small mb-1" for="sky-offset">
        Corrección de brújula: <b>{{ settings.offset > 0 ? '+' : '' }}{{ settings.offset }}°</b>
      </label>
      <input id="sky-offset" v-model.number="settings.offset" type="range" class="form-range" min="-45" max="45" step="1" />
      <div class="form-text text-light opacity-75 mb-2">Si un avión que ves queda corrido, movelo hasta que coincida.</div>
      <label class="form-label small mb-1" for="sky-lift">
        Levantar el cielo: <b>{{ settings.lift === 1 ? 'real' : `×${settings.lift}` }}</b>
      </label>
      <input id="sky-lift" v-model.number="settings.lift" type="range" class="form-range" min="1" max="8" step="1" :disabled="lift === 1 && settings.lift !== 1" />
      <div class="form-text text-light opacity-75">
        Solo en la vista desde el centro de la zona: sube los aviones para verlos arriba y no contra el horizonte. Con tu
        ubicación real siempre se usan los ángulos reales.
      </div>
      <button type="button" class="btn btn-outline-light btn-sm mt-2" @click="Object.assign(settings, { fov: DEFAULT_FOV, offset: 0, lift: DEFAULT_LIFT })">
        Restablecer
      </button>
    </div>

    <!-- Ficha de la aeronave tocada -->
    <div v-if="selected" class="sky-panel sky-card" data-sky-card>
      <div class="d-flex align-items-center gap-2">
        <strong class="text-truncate">{{ identification(selected.ac) }}</strong>
        <span v-if="chip(selected)" class="badge" :class="chip(selected).cls">{{ chip(selected).text }}</span>
        <button type="button" class="btn-close btn-close-white ms-auto" aria-label="Cerrar ficha" @click="selectedHex = null"></button>
      </div>
      <a v-if="photo" :href="photo.link" target="_blank" rel="noopener noreferrer" class="sky-photo d-block mt-2" data-sky-photo>
        <img :src="photo.src" :alt="`Foto de ${shortName(selected.ac)}`" />
        <span>Foto: © {{ photo.photographer }} · Planespotters.net</span>
      </a>
      <div class="d-flex flex-wrap column-gap-3 small mt-1">
        <span><span class="opacity-75">Tipo</span> {{ SHAPES[shapeOf(selected.ac)].label }}</span>
        <span><span class="opacity-75">Altitud</span> <b>{{ altitude(selected.ac.alt_baro) }}</b></span>
        <span><span class="opacity-75">Velocidad</span> <b>{{ speed(selected.ac.gs) }}</b></span>
        <span><span class="opacity-75">Rumbo</span> <b>{{ fmtHeading(selected.ac.track) }}</b></span>
        <span>
          <span class="opacity-75">Está a</span> <b>{{ km(selected.dist) }}</b> hacia el {{ cardinal(selected.azimuth) }}
          ({{ int(selected.azimuth) }}°), {{ int(Math.abs(selected.elevation)) }}°
          {{ selected.elevation >= 0 ? 'sobre' : 'bajo' }} el horizonte<template v-if="lift > 1"> (cielo ×{{ lift }})</template>
        </span>
      </div>
      <div class="d-flex gap-2 mt-2">
        <button type="button" class="btn btn-sm btn-primary" @click="openOnMap(selected.ac.hex)">
          <i class="bi bi-map me-1"></i>Ver en el mapa
        </button>
        <a :href="flightradarUrl(selected.ac)" target="_blank" rel="noopener noreferrer" class="btn btn-sm btn-outline-light">
          <i class="bi bi-box-arrow-up-right me-1"></i>Flightradar24
        </a>
      </div>
    </div>
    <div v-else-if="started && layout.hiddenArrows" class="sky-more small">
      y {{ layout.hiddenArrows }} más lejos fuera de cuadro
    </div>

    <!-- Pantalla inicial: explica y pide los permisos con un toque -->
    <div v-if="!started" class="sky-intro">
      <div class="sky-intro-card">
        <div class="display-6 mb-2"><i class="bi bi-binoculars-fill text-primary"></i></div>
        <h1 class="h4">Modo cielo</h1>
        <p class="mb-2">
          Apuntá el celular al cielo y vas a ver, sobre la imagen de la cámara, cada aeronave cercana con su identificación,
          altitud, distancia y si va a entrar a la zona.
        </p>
        <p class="small opacity-75">
          Usa la cámara trasera, tu ubicación y la brújula del celular. No se graba ni se envía nada.
        </p>
        <div v-if="!secure" class="alert alert-warning small text-start py-2">
          <i class="bi bi-shield-lock-fill me-1"></i>
          <b>Hace falta HTTPS.</b> El navegador solo deja usar la cámara, el GPS y la brújula si la página se abre con
          <code>https://</code>. Entrá por la dirección segura del sistema (ver README, «Modo cielo y HTTPS»).
          Mientras tanto podés probar el modo simulado.
        </div>
        <div class="d-grid gap-2">
          <button v-if="secure" type="button" class="btn btn-primary btn-lg" data-sky-start @click="start()">
            <i class="bi bi-camera-fill me-2"></i>Activar cámara y sensores
          </button>
          <button type="button" class="btn btn-outline-light" data-sky-sim @click="start({ simulate: true })">
            <i class="bi bi-hand-index me-2"></i>Probar sin cámara (arrastrar para mirar)
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sky {
  position: fixed;
  inset: 0;
  z-index: 1100;
  overflow: hidden;
  color: #fff;
  background: #000;
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
}
/* Sin cámara: cielo de fondo (degradé) para que se lea igual. */
.sky.sky-sim {
  background: linear-gradient(to bottom, #0b2a55 0%, #2f6db5 45%, #8fc0ea 70%, #d9e9f5 100%);
}
.sky.dragging {
  cursor: grab;
}
.sky-video {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  opacity: 0;
}
.sky-video.on {
  opacity: 1;
}
.sky-overlay,
.sky-leader {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
.horizon {
  fill: none;
  stroke: rgba(255, 255, 255, 0.55);
  stroke-width: 1.5;
  stroke-dasharray: 6 6;
}
.tick {
  stroke: rgba(255, 255, 255, 0.8);
  stroke-width: 1.5;
}
.tick-label {
  fill: rgba(255, 255, 255, 0.85);
  font-size: 11px;
  text-anchor: middle;
  paint-order: stroke;
  stroke: rgba(0, 0, 0, 0.55);
  stroke-width: 3px;
}
.tick-label.major {
  font-size: 15px;
  font-weight: 700;
  fill: #fff;
}
.tick-label.north {
  fill: #ff6b6b;
}
.zone-marker circle {
  fill: rgba(220, 53, 69, 0.35);
  stroke: #ff6b6b;
  stroke-width: 2;
}
.zone-marker line {
  stroke: #ff6b6b;
  stroke-width: 1.5;
}
.zone-marker text {
  fill: #fff;
  font-size: 12px;
  font-weight: 600;
  text-anchor: middle;
  paint-order: stroke;
  stroke: rgba(150, 0, 0, 0.8);
  stroke-width: 3px;
}
.sky-3d {
  position: absolute;
  left: 0;
  top: 0;
  pointer-events: none;
}
.ladder {
  fill: none;
  stroke: rgba(255, 255, 255, 0.28);
  stroke-width: 1;
}
.ladder-label {
  fill: rgba(255, 255, 255, 0.7);
  font-size: 11px;
  text-anchor: middle;
  paint-order: stroke;
  stroke: rgba(0, 0, 0, 0.45);
  stroke-width: 3px;
}
.sky-marker.ring {
  border-width: 2px;
  background: transparent;
}
.sky-marker.ring::after {
  display: none;
}
.sky-photo {
  position: relative;
  border-radius: 8px;
  overflow: hidden;
  color: #fff;
  text-decoration: none;
}
.sky-photo img {
  display: block;
  width: 100%;
  max-height: 180px;
  object-fit: cover;
}
.sky-photo span {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  padding: 2px 6px;
  font-size: 10px;
  background: rgba(0, 0, 0, 0.55);
}
.sky-far {
  top: calc(max(10px, env(safe-area-inset-top)) + 52px);
}
.sky-leader line {
  stroke: rgba(255, 255, 255, 0.85);
  stroke-width: 1.5;
}
.sky-leader line.inside {
  stroke: #ff6b6b;
}
.sky-leader line.eta {
  stroke: #ffc107;
}

/* Punto sobre la aeronave: anillo con mira. */
.sky-marker {
  position: absolute;
  transform: translate(-50%, -50%);
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 0 0 2px rgba(0, 0, 0, 0.35), inset 0 0 0 2px rgba(0, 0, 0, 0.25);
  pointer-events: none;
}
.sky-marker::after {
  content: '';
  position: absolute;
  left: 50%;
  top: 50%;
  width: 4px;
  height: 4px;
  margin: -2px 0 0 -2px;
  border-radius: 50%;
  background: currentColor;
}
.sky-marker.inside {
  border-color: #ff4d5e;
  color: #ff4d5e;
  animation: pulse 1.2s ease-in-out infinite;
}
.sky-marker.eta {
  border-color: #ffc107;
  color: #ffc107;
}
.sky-marker.quiet {
  border-color: #adb5bd;
  color: #adb5bd;
}
@keyframes pulse {
  50% {
    box-shadow: 0 0 0 8px rgba(255, 77, 94, 0.35);
  }
}

.sky-label {
  position: absolute;
  overflow: hidden;
  padding: 6px 8px;
  border: 1px solid rgba(255, 255, 255, 0.35);
  border-left: 4px solid #fff;
  border-radius: 8px;
  background: rgba(15, 20, 30, 0.72);
  backdrop-filter: blur(4px);
  color: #fff;
  font-size: 13px;
  text-align: left;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.35);
}
.sky-label.inside {
  border-left-color: #ff4d5e;
  background: rgba(90, 10, 20, 0.78);
}
.sky-label.eta {
  border-left-color: #ffc107;
}
.sky-label.quiet {
  border-left-color: #adb5bd;
}
.sky-label.selected {
  outline: 2px solid #0d6efd;
}
.sky-label .badge {
  font-size: 10px;
}
.sky-icon {
  display: inline-flex;
  width: 14px;
  height: 14px;
  flex-shrink: 0;
}
.sky-icon :deep(svg) {
  width: 100%;
  height: 100%;
}

.sky-arrow {
  position: absolute;
  transform: translate(-50%, -50%);
  width: 46px;
  height: 46px;
  border: 2px solid rgba(255, 255, 255, 0.8);
  border-radius: 50%;
  background: rgba(15, 20, 30, 0.7);
  color: #fff;
  padding: 0;
  font-size: 20px;
  line-height: 1;
}
.sky-arrow i {
  display: inline-block;
}
.sky-arrow.inside {
  border-color: #ff4d5e;
  color: #ff4d5e;
}
.sky-arrow.eta {
  border-color: #ffc107;
  color: #ffc107;
}
.sky-arrow-text {
  position: absolute;
  left: 50%;
  top: 100%;
  transform: translateX(-50%);
  margin-top: 2px;
  font-size: 10px;
  line-height: 1.15;
  white-space: nowrap;
  color: #fff;
  text-shadow: 0 1px 2px #000, 0 0 4px #000;
}

.sky-top {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  padding: max(10px, env(safe-area-inset-top)) 10px 0;
}
.sky-btn {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: rgba(15, 20, 30, 0.7);
  border-color: rgba(255, 255, 255, 0.3);
}
.sky-pill {
  max-width: 360px;
  margin: 0 auto;
  padding: 4px 12px;
  border-radius: 20px;
  background: rgba(15, 20, 30, 0.7);
  font-size: 14px;
}
.sky-status {
  position: absolute;
  left: 8px;
  right: 8px;
  bottom: max(10px, env(safe-area-inset-bottom));
}
.sky-status .badge {
  font-weight: 500;
}
.sky-panel {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  width: min(420px, calc(100% - 16px));
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(15, 20, 30, 0.88);
  backdrop-filter: blur(6px);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
}
.sky-settings {
  top: calc(max(10px, env(safe-area-inset-top)) + 52px);
}
.sky-card {
  bottom: calc(max(10px, env(safe-area-inset-bottom)) + 34px);
}
.sky-report-btn {
  position: absolute;
  right: 10px;
  bottom: calc(max(10px, env(safe-area-inset-bottom)) + 62px);
  z-index: 3;
}
.sky-crosshair {
  position: absolute;
  left: 50%;
  top: 50%;
  width: 74px;
  height: 74px;
  margin: -37px 0 0 -37px;
  border: 2px solid #39c0ed;
  border-radius: 50%;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.5), inset 0 0 0 1px rgba(0, 0, 0, 0.5);
  pointer-events: none;
}
.sky-crosshair::before,
.sky-crosshair::after {
  content: '';
  position: absolute;
  background: #39c0ed;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.4);
}
.sky-crosshair::before {
  left: 50%;
  top: -14px;
  bottom: -14px;
  width: 2px;
  margin-left: -1px;
}
.sky-crosshair::after {
  top: 50%;
  left: -14px;
  right: -14px;
  height: 2px;
  margin-top: -1px;
}
.sky-crosshair span {
  position: absolute;
  left: 50%;
  top: 50%;
  width: 8px;
  height: 8px;
  margin: -4px 0 0 -4px;
  border-radius: 50%;
  background: #0c1622;
  border: 2px solid #39c0ed;
}
.sky-more {
  position: absolute;
  left: 0;
  right: 0;
  bottom: calc(max(10px, env(safe-area-inset-bottom)) + 30px);
  text-align: center;
  text-shadow: 0 1px 2px #000;
}

.sky-intro {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: rgba(5, 15, 35, 0.55);
}
.sky-intro-card {
  max-width: 420px;
  padding: 20px;
  border-radius: 16px;
  background: rgba(15, 20, 30, 0.85);
  text-align: center;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.5);
}
</style>
