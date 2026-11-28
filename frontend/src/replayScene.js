// Repetición de un incidente en 3D (three.js): la zona como cilindro, el
// recorrido de la aeronave y el modelo moviéndose por él según la línea de
// tiempo. Se puede girar, acercar y alejar con el dedo o el mouse.
//
// Coordenadas: x = Este, y = Norte, z = Arriba, en kilómetros desde el centro
// de la zona. La altura se exagera (V_EXAG) porque los recorridos miden
// decenas de km de largo y pocos km de alto: a escala real se verían planos.

import {
  AmbientLight,
  BufferGeometry,
  CanvasTexture,
  CircleGeometry,
  Color,
  ConeGeometry,
  CylinderGeometry,
  DirectionalLight,
  DoubleSide,
  Float32BufferAttribute,
  Group,
  HemisphereLight,
  Line,
  LineBasicMaterial,
  LineLoop,
  LineSegments,
  Mesh,
  MeshBasicMaterial,
  MeshStandardMaterial,
  PerspectiveCamera,
  Scene,
  SphereGeometry,
  Sprite,
  SpriteMaterial,
  Vector3,
  WebGLRenderer,
} from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { modelFileFor } from './skyModels'
import { loadModel } from './skyScene'

export const V_EXAG = 2
const KM_PER_DEG = 111.195
const KM_PER_NM = 1.852
const KM_PER_FT = 0.0003048
const RAD = Math.PI / 180

const KIND_COLOR = {
  entry: 0xff3b4e,
  emergency: 0xff3b4e,
  loiter: 0xffc107,
  rapid_descent: 0xffc107,
  signal_lost: 0xffc107,
  sighting: 0x39c0ed,
}

const isNum = (v) => typeof v === 'number' && Number.isFinite(v)

// lat/lon -> [este km, norte km] respecto del centro de la zona.
export function toLocal(zone, lat, lon) {
  return [(lon - zone.lon) * KM_PER_DEG * Math.cos(zone.lat * RAD), (lat - zone.lat) * KM_PER_DEG]
}

export const altKm = (alt) => (isNum(alt) ? alt * KM_PER_FT : 0)

// Estado de la aeronave en el instante `t` (s Unix), interpolando el recorrido
// [[hora, lat, lon, altitud, kt, rumbo], ...]. Antes del primer punto o después
// del último devuelve el extremo.
export function stateAt(track, t) {
  if (!track.length) return null
  if (t <= track[0][0]) return pointState(track[0], 0)
  const last = track.length - 1
  if (t >= track[last][0]) return pointState(track[last], last)
  let lo = 0
  let hi = last
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1
    if (track[mid][0] <= t) lo = mid
    else hi = mid
  }
  const a = track[lo]
  const b = track[hi]
  const f = (t - a[0]) / Math.max(b[0] - a[0], 1e-6)
  const mix = (x, y) => (isNum(x) && isNum(y) ? x + (y - x) * f : isNum(x) ? x : y)
  let heading = isNum(a[5]) ? a[5] : b[5]
  if (isNum(a[5]) && isNum(b[5])) heading = a[5] + ((((b[5] - a[5]) % 360) + 540) % 360 - 180) * f
  const ground = a[3] === 'ground' && b[3] === 'ground'
  return {
    index: lo,
    fraction: f,
    lat: a[1] + (b[1] - a[1]) * f,
    lon: a[2] + (b[2] - a[2]) * f,
    alt: ground ? 'ground' : mix(a[3] === 'ground' ? 0 : a[3], b[3] === 'ground' ? 0 : b[3]),
    gs: mix(a[4], b[4]),
    heading: isNum(heading) ? ((heading % 360) + 360) % 360 : null,
    climbFpm: isNum(a[3]) && isNum(b[3]) ? ((b[3] - a[3]) / Math.max(b[0] - a[0], 1e-6)) * 60 : 0,
  }
}

function pointState(p, index) {
  return { index, fraction: 0, lat: p[1], lon: p[2], alt: p[3], gs: p[4], heading: isNum(p[5]) ? p[5] : null, climbFpm: 0 }
}

// ¿Dentro del cilindro de la zona? (mismas reglas que backend/monitor/zone.py)
export function insideZone(zone, state) {
  if (!state || !isNum(state.alt)) return false
  const [x, y] = toLocal(zone, state.lat, state.lon)
  if (!(Math.hypot(x, y) < zone.radius_nm * KM_PER_NM)) return false
  const altM = state.alt * 0.3048
  if (!(altM < zone.ceiling_m)) return false
  return zone.floor_m <= 0 || altM >= zone.floor_m
}

function label(text, { color = '#ffffff', size = 1 } = {}) {
  const canvas = document.createElement('canvas')
  const ctx = canvas.getContext('2d')
  const font = '600 44px system-ui, -apple-system, "Segoe UI", sans-serif'
  ctx.font = font
  canvas.width = Math.ceil(ctx.measureText(text).width) + 28
  canvas.height = 64
  ctx.font = font
  ctx.textBaseline = 'middle'
  ctx.lineWidth = 8
  ctx.strokeStyle = 'rgba(0, 0, 0, 0.75)'
  ctx.strokeText(text, 14, 34)
  ctx.fillStyle = color
  ctx.fillText(text, 14, 34)
  const sprite = new Sprite(new SpriteMaterial({ map: new CanvasTexture(canvas), depthTest: false, transparent: true }))
  sprite.scale.set((canvas.width / canvas.height) * size, size, 1)
  sprite.renderOrder = 10
  return sprite
}

function ring(radius, z, color, opacity = 1, segments = 96) {
  const points = []
  for (let i = 0; i < segments; i += 1) {
    const a = (i / segments) * Math.PI * 2
    points.push(new Vector3(Math.cos(a) * radius, Math.sin(a) * radius, z))
  }
  return new LineLoop(new BufferGeometry().setFromPoints(points), new LineBasicMaterial({ color, transparent: opacity < 1, opacity }))
}

export class ReplayScene {
  // incident: el detalle de /api/incidents/<id>/ ({ kind, zone, track, type, category, data }).
  constructor(canvas, incident) {
    this.zone = incident.zone
    this.track = incident.track || []
    this.kind = incident.kind
    this.color = KIND_COLOR[incident.kind] ?? 0xffffff
    this.follow = false
    this.disposed = false

    this.renderer = new WebGLRenderer({ canvas, antialias: true })
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    this.scene = new Scene()
    this.scene.background = new Color(0x0c1622)

    const sighting = incident.kind === 'sighting' ? incident.data?.detail : null
    this.points = this.track.map((p) => {
      const [x, y] = toLocal(this.zone, p[1], p[2])
      return new Vector3(x, y, altKm(p[3]) * V_EXAG)
    })
    const zoneR = this.zone.radius_nm * KM_PER_NM
    let extent = zoneR * 1.4
    for (const p of this.points) extent = Math.max(extent, Math.hypot(p.x, p.y) * 1.1)
    const traffic = (sighting?.traffic || []).map(([hex, name, type, category, lat, lon, alt, track]) => {
      const [x, y] = toLocal(this.zone, lat, lon)
      return { hex, name, type, category, track, pos: new Vector3(x, y, altKm(alt) * V_EXAG) }
    })
    // En un avistamiento se muestra el tránsito cercano (hasta ~4 radios), no los 250 NM consultados.
    const trafficRange = Math.max(zoneR * 4, 40)
    this.traffic = traffic.filter((t) => Math.hypot(t.pos.x, t.pos.y) <= trafficRange)
    if (sighting) extent = Math.max(extent, ...this.traffic.map((t) => Math.hypot(t.pos.x, t.pos.y) * 1.1), zoneR * 2.5)
    this.extent = Math.max(extent, 6)

    this.camera = new PerspectiveCamera(45, 1, this.extent / 200, this.extent * 40)
    this.camera.up.set(0, 0, 1)
    this.controls = new OrbitControls(this.camera, canvas)
    this.controls.enableDamping = true
    this.controls.maxPolarAngle = Math.PI / 2 - 0.04
    this.controls.minDistance = this.extent / 12
    this.controls.maxDistance = this.extent * 8
    this.home = new Vector3(0, 0, 0) // adónde mira la cámara cuando no sigue a la aeronave
    this.frameRadius = this.extent

    const hemi = new HemisphereLight(0xffffff, 0x53657a, 1.5)
    hemi.position.set(0, 0, 1)
    this.scene.add(hemi, new AmbientLight(0xffffff, 0.5))
    const sun = new DirectionalLight(0xffffff, 1.5)
    sun.position.set(-0.4, -0.6, 1)
    this.scene.add(sun)

    this.buildGround(zoneR)
    this.buildZone(zoneR)
    this.modelSize = this.extent / 7
    if (sighting) this.buildSighting(sighting)
    else this.buildTrack(incident)
    this.frame()

    this.loop = this.loop.bind(this)
    this.raf = requestAnimationFrame(this.loop)
  }

  buildGround(zoneR) {
    const e = this.extent * 1.25
    const ground = new Mesh(new CircleGeometry(e, 96), new MeshBasicMaterial({ color: 0x15273a }))
    ground.position.z = -0.01
    this.scene.add(ground)
    // Anillos de distancia cada 5, 10 o 20 km según el tamaño.
    const step = e > 120 ? 50 : e > 60 ? 20 : e > 25 ? 10 : 5
    for (let r = step; r < e; r += step) {
      this.scene.add(ring(r, 0, 0x3a5676, 0.7))
      const text = label(`${r} km`, { color: '#8fb0d4', size: this.extent / 22 })
      text.position.set(0, -r, this.extent / 60)
      this.scene.add(text)
    }
    const cross = new BufferGeometry().setFromPoints([new Vector3(-e, 0, 0), new Vector3(e, 0, 0), new Vector3(0, -e, 0), new Vector3(0, e, 0)])
    this.scene.add(new LineSegments(cross, new LineBasicMaterial({ color: 0x3a5676, transparent: true, opacity: 0.7 })))
    for (const [text, x, y] of [['N', 0, 1], ['E', 1, 0], ['S', 0, -1], ['O', -1, 0]]) {
      const sprite = label(text, { color: text === 'N' ? '#ff6b7a' : '#dbe7f5', size: this.extent / 11 })
      sprite.position.set(x * e * 1.06, y * e * 1.06, this.extent / 40)
      this.scene.add(sprite)
    }
  }

  buildZone(zoneR) {
    const floor = (this.zone.floor_m / 1000) * V_EXAG
    const ceiling = (this.zone.ceiling_m / 1000) * V_EXAG
    const wall = new Mesh(
      new CylinderGeometry(zoneR, zoneR, ceiling - floor, 96, 1, true),
      new MeshBasicMaterial({ color: 0xff3b4e, transparent: true, opacity: 0.16, side: DoubleSide, depthWrite: false }),
    )
    wall.rotation.x = Math.PI / 2 // el cilindro de three va sobre Y: se para sobre Z
    wall.position.z = (floor + ceiling) / 2
    this.scene.add(wall)
    const lid = new Mesh(
      new CircleGeometry(zoneR, 96),
      new MeshBasicMaterial({ color: 0xff3b4e, transparent: true, opacity: 0.1, side: DoubleSide, depthWrite: false }),
    )
    lid.position.z = ceiling
    this.scene.add(lid)
    this.scene.add(ring(zoneR, floor + 0.02, 0xff6b7a), ring(zoneR, ceiling, 0xff6b7a))
    const name = label(this.zone.name || 'Zona', { color: '#ff9aa5', size: this.extent / 16 })
    name.position.set(0, 0, ceiling + this.extent / 22)
    this.scene.add(name)
  }

  buildTrack(incident) {
    const pts = this.points
    if (pts.length > 1) {
      const geometry = new BufferGeometry().setFromPoints(pts)
      this.scene.add(new Line(geometry, new LineBasicMaterial({ color: 0x6f86a3, transparent: true, opacity: 0.55 })))
      this.flown = new Line(geometry.clone(), new LineBasicMaterial({ color: this.color }))
      this.flown.geometry.setDrawRange(0, 0)
      this.scene.add(this.flown)
      // "Cortina" hasta el piso: se lee la altura de cada tramo.
      const stride = Math.max(1, Math.round(pts.length / 60))
      const drops = []
      for (let i = 0; i < pts.length; i += stride) drops.push(pts[i].x, pts[i].y, pts[i].z, pts[i].x, pts[i].y, 0)
      const curtain = new BufferGeometry()
      curtain.setAttribute('position', new Float32BufferAttribute(drops, 3))
      this.scene.add(new LineSegments(curtain, new LineBasicMaterial({ color: 0x6f86a3, transparent: true, opacity: 0.28 })))
      // Sombra del recorrido en el piso.
      const shadow = new BufferGeometry().setFromPoints(pts.map((p) => new Vector3(p.x, p.y, 0.01)))
      this.scene.add(new Line(shadow, new LineBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.35 })))
    }
    // Marca del momento del incidente.
    const at = stateAt(this.track, incident.time)
    if (at) {
      const p = this.position(at)
      const marker = new Mesh(new SphereGeometry(this.extent / 110, 16, 12), new MeshBasicMaterial({ color: this.color }))
      marker.position.copy(p)
      this.scene.add(marker)
      const flag = label('Incidente', { color: '#ffffff', size: this.extent / 20 })
      flag.position.set(p.x, p.y, p.z + this.extent / 16)
      this.scene.add(flag)
    }

    this.aircraft = new Group()
    this.placeholder = new Mesh(new ConeGeometry(0.18, 0.9, 12), new MeshStandardMaterial({ color: 0xf2f4f7 }))
    this.aircraft.add(this.placeholder) // el cono apunta a +Y: la nariz
    this.aircraft.scale.setScalar(this.modelSize)
    this.scene.add(this.aircraft)
    this.pole = new Line(
      new BufferGeometry().setFromPoints([new Vector3(), new Vector3()]),
      new LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.6 }),
    )
    this.scene.add(this.pole)
    this.useModel(this.aircraft, this.placeholder, modelFileFor({ type: incident.type, category: incident.category }))
    this.setTime(this.track.length ? this.track[0][0] : 0)
  }

  buildSighting(detail) {
    this.sightingFramed = true
    const observer = detail.observer || {}
    const [ox, oy] = isNum(observer.lat) ? toLocal(this.zone, observer.lat, observer.lon) : [0, 0]
    const near = Math.hypot(ox, oy) <= this.extent
    const origin = near ? new Vector3(ox, oy, 0) : new Vector3(0, 0, 0)
    const post = new Mesh(new CylinderGeometry(this.extent / 120, this.extent / 120, this.extent / 14, 12), new MeshBasicMaterial({ color: 0x39c0ed }))
    post.rotation.x = Math.PI / 2
    post.position.set(origin.x, origin.y, this.extent / 28)
    this.scene.add(post)
    const who = label('Observador', { color: '#9be3ff', size: this.extent / 18 })
    who.position.set(origin.x, origin.y, this.extent / 9)
    this.scene.add(who)

    // Hacia dónde apuntaba la cámara (la altura va exagerada igual que todo lo demás).
    const az = (detail.azimuth || 0) * RAD
    const el = (detail.elevation || 0) * RAD
    const dir = new Vector3(Math.cos(el) * Math.sin(az), Math.cos(el) * Math.cos(az), Math.sin(el) * V_EXAG).normalize()
    const length = this.extent * 0.9
    const end = origin.clone().addScaledVector(dir, length)
    const beam = new Mesh(
      new CylinderGeometry(length * Math.tan(4 * RAD), this.extent / 300, length, 24, 1, true),
      new MeshBasicMaterial({ color: 0x39c0ed, transparent: true, opacity: 0.2, side: DoubleSide, depthWrite: false }),
    )
    beam.position.copy(origin).addScaledVector(dir, length / 2)
    beam.quaternion.setFromUnitVectors(new Vector3(0, 1, 0), dir)
    this.scene.add(beam)
    this.scene.add(new Line(new BufferGeometry().setFromPoints([origin, end]), new LineBasicMaterial({ color: 0x39c0ed })))
    const unknown = new Mesh(new SphereGeometry(this.extent / 45, 20, 14), new MeshBasicMaterial({ color: 0x39c0ed }))
    unknown.position.copy(end)
    this.scene.add(unknown)
    const what = label('? sin transponder', { color: '#ffffff', size: this.extent / 15 })
    what.position.set(end.x, end.y, end.z + this.extent / 14)
    this.scene.add(what)

    // Lo que el sistema sí veía con transponder en ese momento.
    for (const t of this.traffic) {
      const group = new Group()
      const placeholder = new Mesh(new ConeGeometry(0.18, 0.9, 12), new MeshStandardMaterial({ color: 0xf2f4f7 }))
      group.add(placeholder)
      group.scale.setScalar(this.modelSize * 0.6)
      group.position.copy(t.pos)
      group.rotation.z = -(isNum(t.track) ? t.track : 0) * RAD
      this.scene.add(group)
      this.useModel(group, placeholder, modelFileFor({ type: t.type, category: t.category }))
      const name = label(t.name || t.hex, { color: '#dbe7f5', size: this.extent / 24 })
      name.position.set(t.pos.x, t.pos.y, t.pos.z + this.extent / 20)
      this.scene.add(name)
      this.scene.add(new Line(
        new BufferGeometry().setFromPoints([t.pos, new Vector3(t.pos.x, t.pos.y, 0)]),
        new LineBasicMaterial({ color: 0x6f86a3, transparent: true, opacity: 0.4 }),
      ))
    }
    const middle = origin.clone().addScaledVector(dir, length * 0.45)
    this.home.set(middle.x, middle.y, 0)
    this.frameRadius = length * 0.75
  }

  // Encuadre inicial: el recorrido completo y, si entra, la zona. En pantallas
  // angostas (celular parado) la cámara se aleja para que entre igual.
  frame() {
    if (!this.sightingFramed && this.points.length) {
      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
      for (const p of this.points) {
        minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x)
        minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y)
      }
      const zoneR = this.zone.radius_nm * KM_PER_NM
      const center = new Vector3((minX + maxX) / 2, (minY + maxY) / 2, 0)
      let radius = Math.max(Math.hypot(maxX - minX, maxY - minY) * 0.8, zoneR * 0.9, 4)
      if (center.length() > radius) {
        // El recorrido queda lejos de la zona: se encuadran los dos.
        minX = Math.min(minX, -zoneR); maxX = Math.max(maxX, zoneR)
        minY = Math.min(minY, -zoneR); maxY = Math.max(maxY, zoneR)
        center.set((minX + maxX) / 2, (minY + maxY) / 2, 0)
        radius = Math.hypot(maxX - minX, maxY - minY) * 0.6
      }
      this.home.copy(center)
      this.frameRadius = radius
    }
    const aspect = this.camera.aspect || 1
    const fit = this.frameRadius / Math.tan((this.camera.fov / 2) * RAD)
    const distance = fit * Math.min(2.4, aspect < 1 ? 1 / aspect : 1) * 0.8
    const from = new Vector3(-0.5, -0.85, 0.62).normalize()
    this.camera.position.copy(this.home).addScaledVector(from, distance)
    this.controls.target.copy(this.home)
    this.controls.update()
  }

  // Cambia el cono provisorio por el modelo real cuando termina de cargar.
  useModel(group, placeholder, file) {
    const entry = loadModel(file)
    entry.promise.then(() => {
      if (this.disposed || !entry.model) return
      group.remove(placeholder)
      group.add(entry.model.clone(true))
    })
  }

  position(state) {
    const [x, y] = toLocal(this.zone, state.lat, state.lon)
    return new Vector3(x, y, altKm(state.alt) * V_EXAG)
  }

  // Lleva la aeronave al instante `t` y devuelve su estado (para los datos en pantalla).
  setTime(t) {
    const state = stateAt(this.track, t)
    if (!state || !this.aircraft) return state
    const p = this.position(state)
    this.aircraft.position.copy(p)
    this.aircraft.rotation.set(0, 0, 0)
    this.aircraft.rotateZ(-(state.heading ?? 0) * RAD)
    const gs = isNum(state.gs) && state.gs > 20 ? state.gs : 100
    const climb = Math.atan2(state.climbFpm, gs * 101.27)
    this.aircraft.rotateX(Math.max(-0.6, Math.min(0.6, climb * V_EXAG)))
    const pole = this.pole.geometry.attributes.position
    pole.setXYZ(0, p.x, p.y, p.z)
    pole.setXYZ(1, p.x, p.y, 0)
    pole.needsUpdate = true
    if (this.flown) {
      // El tramo ya volado: los puntos completos más el punto actual.
      const position = this.flown.geometry.attributes.position
      const next = Math.min(state.index + 1, this.points.length - 1)
      position.setXYZ(next, p.x, p.y, p.z)
      if (this.patched !== undefined && this.patched !== next) {
        const o = this.points[this.patched]
        position.setXYZ(this.patched, o.x, o.y, o.z)
      }
      this.patched = next
      position.needsUpdate = true
      this.flown.geometry.setDrawRange(0, next + 1)
    }
    this.current = p
    return state
  }

  setFollow(on) {
    this.follow = on
    if (!on) this.controls.target.copy(this.home)
  }

  resize(width, height) {
    if (!width || !height) return
    this.renderer.setSize(width, height, false)
    this.camera.aspect = width / height
    this.camera.updateProjectionMatrix()
    if (!this.framed) {
      this.framed = true
      this.frame() // recién ahora se conoce la forma de la pantalla
    }
  }

  loop() {
    if (this.disposed) return
    this.raf = requestAnimationFrame(this.loop)
    if (this.follow && this.current) this.controls.target.lerp(this.current, 0.15)
    this.controls.update()
    this.renderer.render(this.scene, this.camera)
  }

  dispose() {
    this.disposed = true
    cancelAnimationFrame(this.raf)
    this.controls.dispose()
    this.scene.traverse((o) => {
      o.geometry?.dispose?.()
      const materials = Array.isArray(o.material) ? o.material : o.material ? [o.material] : []
      for (const m of materials) {
        m.map?.dispose?.()
        m.dispose?.()
      }
    })
    this.renderer.dispose()
  }
}
