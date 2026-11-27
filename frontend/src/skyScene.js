// Render 3D de las aeronaves del Modo cielo (three.js, fondo transparente sobre
// la cámara).
//
// El mundo es el mismo marco ENU de sky.js (x = Este, y = Norte, z = Arriba)
// con el usuario en el origen, y la cámara 3D copia exactamente la cámara
// virtual (misma orientación y misma distancia focal), así que cada modelo cae
// justo donde se dibuja su etiqueta. Cada aeronave se ubica en su dirección
// real a una distancia fija y se escala para que se vea de un tamaño legible;
// como su orientación es la real (rumbo y ascenso), se la ve de abajo, de
// costado o de frente según desde dónde se la mire.

import {
  AmbientLight,
  BoxGeometry,
  ConeGeometry,
  CylinderGeometry,
  DirectionalLight,
  Group,
  HemisphereLight,
  Mesh,
  MeshStandardMaterial,
  PerspectiveCamera,
  Scene,
  SphereGeometry,
  Vector3,
  WebGLRenderer,
} from 'three'

const RENDER_DIST = 100 // unidades a las que se ubica cada modelo
const RAD = Math.PI / 180

const COLORS = {
  normal: 0xf2f4f7,
  inside: 0xff3b4e,
  eta: 0xffc107,
  quiet: 0x9aa3ad,
}

const materials = {}
function mat(status, part = 'body') {
  const key = `${status}-${part}`
  if (!materials[key]) {
    const color = part === 'body' ? COLORS[status] : part === 'dark' ? 0x2b3440 : 0x5b8def
    materials[key] = new MeshStandardMaterial({ color, roughness: 0.45, metalness: part === 'body' ? 0.25 : 0.1 })
  }
  return materials[key]
}

const box = (w, l, h, m) => new Mesh(new BoxGeometry(w, l, h), m)

// Fuselaje a lo largo de +Y (nariz al norte), alas en X, arriba en Z. Largo
// total ≈ 1 unidad.
function fuselage(length, radius, m) {
  const g = new Group()
  const body = new Mesh(new CylinderGeometry(radius, radius, length * 0.7, 14), m) // eje Y
  g.add(body)
  const nose = new Mesh(new ConeGeometry(radius, length * 0.18, 14), m)
  nose.position.y = length * 0.44
  g.add(nose)
  const tail = new Mesh(new ConeGeometry(radius, length * 0.14, 14), m)
  tail.rotation.x = Math.PI
  tail.position.y = -length * 0.42
  g.add(tail)
  return g
}

// Ala en flecha: una caja inclinada por lado.
function sweptWing(span, chord, sweepDeg, m, z = 0, y = 0) {
  const g = new Group()
  for (const side of [-1, 1]) {
    const w = box(span / 2, chord, 0.02, m)
    w.position.set((side * span) / 4, y - Math.tan(sweepDeg * RAD) * (span / 4), z)
    w.rotation.z = -side * sweepDeg * RAD
    g.add(w)
  }
  return g
}

function jet(status) {
  const g = new Group()
  const m = mat(status)
  g.add(fuselage(1, 0.06, m))
  g.add(sweptWing(0.95, 0.16, 25, m, -0.02, 0.02))
  g.add(sweptWing(0.36, 0.08, 30, m, 0, -0.42))
  const fin = box(0.02, 0.14, 0.17, m)
  fin.position.set(0, -0.42, 0.1)
  g.add(fin)
  for (const side of [-1, 1]) {
    const engine = new Mesh(new CylinderGeometry(0.035, 0.035, 0.14, 10), mat(status, 'dark'))
    engine.position.set(side * 0.2, 0.05, -0.07)
    g.add(engine)
  }
  const windows = box(0.07, 0.06, 0.03, mat(status, 'glass'))
  windows.position.set(0, 0.38, 0.04)
  g.add(windows)
  return g
}

function light(status) {
  const g = new Group()
  const m = mat(status)
  g.add(fuselage(0.8, 0.055, m))
  const wing = box(1, 0.13, 0.025, m) // ala alta y recta
  wing.position.set(0, 0.08, 0.06)
  g.add(wing)
  const tail = box(0.34, 0.08, 0.02, m)
  tail.position.set(0, -0.33, 0)
  g.add(tail)
  const fin = box(0.02, 0.1, 0.14, m)
  fin.position.set(0, -0.33, 0.08)
  g.add(fin)
  const prop = box(0.22, 0.01, 0.02, mat(status, 'dark'))
  prop.position.y = 0.42
  prop.userData.spin = 'y'
  g.add(prop)
  const glass = box(0.08, 0.1, 0.04, mat(status, 'glass'))
  glass.position.set(0, 0.16, 0.05)
  g.add(glass)
  return g
}

function glider(status) {
  const g = new Group()
  const m = mat(status)
  g.add(fuselage(0.75, 0.035, m))
  const wing = box(1.4, 0.07, 0.015, m)
  wing.position.set(0, 0.05, 0.02)
  g.add(wing)
  const tail = box(0.26, 0.05, 0.012, m)
  tail.position.set(0, -0.34, 0.12) // cola en T
  g.add(tail)
  const fin = box(0.015, 0.08, 0.13, m)
  fin.position.set(0, -0.34, 0.06)
  g.add(fin)
  return g
}

function heli(status) {
  const g = new Group()
  const m = mat(status)
  const cabin = new Mesh(new SphereGeometry(0.13, 16, 12), m)
  cabin.scale.set(0.85, 1.2, 0.9)
  g.add(cabin)
  const boom = new Mesh(new CylinderGeometry(0.025, 0.035, 0.5, 8), m)
  boom.position.y = -0.36
  g.add(boom)
  const fin = box(0.015, 0.08, 0.12, m)
  fin.position.set(0, -0.6, 0.05)
  g.add(fin)
  const rotor = new Group()
  for (const r of [0, Math.PI / 2]) {
    const blade = box(0.95, 0.04, 0.01, mat(status, 'dark'))
    blade.rotation.z = r
    rotor.add(blade)
  }
  rotor.position.z = 0.15
  rotor.userData.spin = 'z'
  g.add(rotor)
  const glass = new Mesh(new SphereGeometry(0.08, 12, 10), mat(status, 'glass'))
  glass.position.set(0, 0.08, 0.03)
  g.add(glass)
  return g
}

function drone(status) {
  const g = new Group()
  const m = mat(status)
  g.add(box(0.2, 0.2, 0.06, m))
  for (const a of [45, 135, 225, 315]) {
    const arm = box(0.04, 0.32, 0.03, m)
    arm.rotation.z = a * RAD
    arm.position.set(Math.sin(a * RAD) * 0.16, Math.cos(a * RAD) * 0.16, 0)
    g.add(arm)
    const rotor = box(0.22, 0.03, 0.01, mat(status, 'dark'))
    rotor.position.set(Math.sin(a * RAD) * 0.3, Math.cos(a * RAD) * 0.3, 0.04)
    rotor.userData.spin = 'z'
    g.add(rotor)
  }
  return g
}

function balloon(status) {
  const g = new Group()
  const envelope = new Mesh(new SphereGeometry(0.32, 20, 16), mat(status))
  envelope.scale.set(1, 1, 1.15)
  envelope.position.z = 0.15
  g.add(envelope)
  const basket = box(0.1, 0.1, 0.08, mat(status, 'dark'))
  basket.position.z = -0.3
  g.add(basket)
  return g
}

const BUILDERS = { jet, light, glider, heli, drone, balloon }

export class SkyScene {
  constructor(canvas) {
    this.renderer = new WebGLRenderer({ canvas, alpha: true, antialias: true })
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    this.scene = new Scene()
    this.camera = new PerspectiveCamera(50, 1, 1, 1000)
    // Luz del cielo desde arriba (los aviones se ven de abajo: que no queden negros).
    const hemi = new HemisphereLight(0xffffff, 0x8899aa, 1.6)
    hemi.position.set(0, 0, 1)
    this.scene.add(hemi)
    this.scene.add(new AmbientLight(0xffffff, 0.6))
    const sun = new DirectionalLight(0xffffff, 1.4)
    sun.position.set(0.4, -0.5, 1)
    this.scene.add(sun)
    this.models = new Map() // hex -> { group, key }
  }

  setSize(width, height) {
    this.renderer.setSize(width, height, false)
    this.width = width
    this.height = height
  }

  // cam: base { forward, up, right } en ENU. items: { hex, shape, status, dir,
  // track, climbDeg, px } con px = tamaño aproximado en pantalla.
  render(cam, focal, items, time = 0) {
    if (!this.width) return
    const c = this.camera
    c.fov = (2 * Math.atan(this.height / 2 / focal)) / RAD
    c.aspect = this.width / this.height
    c.updateProjectionMatrix()
    c.position.set(0, 0, 0)
    c.up.set(...cam.up)
    c.lookAt(new Vector3(...cam.forward))

    const seen = new Set()
    for (const it of items) {
      seen.add(it.hex)
      const key = `${it.shape}-${it.status}`
      let entry = this.models.get(it.hex)
      if (!entry || entry.key !== key) {
        if (entry) this.scene.remove(entry.group)
        const group = new Group()
        group.add((BUILDERS[it.shape] || jet)(it.status))
        entry = { group, key }
        this.models.set(it.hex, entry)
        this.scene.add(group)
      }
      const g = entry.group
      g.position.set(it.dir[0] * RENDER_DIST, it.dir[1] * RENDER_DIST, it.dir[2] * RENDER_DIST)
      // Escala para que mida ~px píxeles a esa distancia.
      const s = (it.px * RENDER_DIST) / focal
      g.scale.setScalar(s)
      // Rumbo: horario desde el norte = giro antihorario alrededor de Z. Luego
      // el ascenso (alrededor del eje de las alas).
      g.rotation.set(0, 0, 0)
      g.rotateZ(-(it.track ?? 0) * RAD)
      g.rotateX((it.climbDeg || 0) * RAD)
      g.traverse((o) => {
        if (o.userData.spin === 'z') o.rotation.z = time * 0.02
        if (o.userData.spin === 'y') o.rotation.y = time * 0.03
      })
    }
    for (const [hex, entry] of this.models) {
      if (!seen.has(hex)) {
        this.scene.remove(entry.group)
        this.models.delete(hex)
      }
    }
    this.renderer.render(this.scene, c)
  }

  dispose() {
    this.renderer.dispose()
  }
}
