// Cuentas del Modo cielo: dónde está cada aeronave vista desde el usuario y en
// qué punto de la pantalla cae según hacia dónde apunta la cámara.
//
// Marco de referencia "ENU" con origen en el usuario: x = Este, y = Norte,
// z = Arriba. Los vectores de dirección son unitarios.

import { M_PER_FT } from './format'

const RAD = Math.PI / 180
const EARTH_R = 6371000 // m
// Refracción atmosférica estándar: la curvatura "visible" es la de una Tierra
// con 4/3 del radio real (la misma convención que usan los radares).
const EFFECTIVE_R = EARTH_R * (4 / 3)

export const norm360 = (deg) => ((deg % 360) + 360) % 360

// Distancia sobre la superficie (m) y rumbo inicial (grados desde el norte).
export function distanceBearing(lat1, lon1, lat2, lon2) {
  const p1 = lat1 * RAD
  const p2 = lat2 * RAD
  const dp = p2 - p1
  const dl = (lon2 - lon1) * RAD
  const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2
  const dist = 2 * EARTH_R * Math.asin(Math.min(1, Math.sqrt(a)))
  const y = Math.sin(dl) * Math.cos(p2)
  const x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl)
  return { dist, bearing: norm360(Math.atan2(y, x) / RAD) }
}

// Altitud de la aeronave en metros: "ground" = al nivel del usuario.
export function aircraftAltitudeM(alt, observerAltM = 0) {
  if (alt === 'ground') return observerAltM
  return typeof alt === 'number' && Number.isFinite(alt) ? alt * M_PER_FT : null
}

// Azimut (grados desde el norte) y elevación (grados sobre el horizonte) de un
// punto a `dist` metros y `heightM` de altitud, visto desde `observerAltM`.
//
// `lift` > 1 "acerca" el cielo: la elevación se calcula como si la aeronave
// estuviera `lift` veces más cerca (misma dirección, misma altitud), así las que
// en la realidad quedan pegadas al horizonte se ven arriba. Solo para la vista
// desde el centro de la zona, donde no hay un avión real con el que coincidir.
export function lookAngles(dist, bearing, heightM, observerAltM = 0, lift = 1) {
  const drop = (dist * dist) / (2 * EFFECTIVE_R) // lo que "baja" por la curvatura
  const elevation = Math.atan2(heightM - observerAltM - drop, Math.max(dist / lift, 1)) / RAD
  return { azimuth: bearing, elevation }
}

export function direction(azimuth, elevation) {
  const az = azimuth * RAD
  const el = elevation * RAD
  return [Math.cos(el) * Math.sin(az), Math.cos(el) * Math.cos(az), Math.sin(el)]
}

const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
const scale = (a, k) => [a[0] * k, a[1] * k, a[2] * k]
const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
const unit = (a) => {
  const n = Math.hypot(a[0], a[1], a[2]) || 1
  return [a[0] / n, a[1] / n, a[2] / n]
}

// Cámara (base ortonormal en ENU): hacia dónde mira, el "arriba" y la
// "derecha" de la pantalla.
//
// A partir de DeviceOrientation (alpha, beta, gamma en grados, alpha respecto
// del norte). La rotación es R = Rz(alpha)·Rx(beta)·Ry(gamma) (especificación
// W3C): sus columnas son los ejes del celular en ENU. La cámara trasera mira
// hacia -z del celular. `screenAngle` = screen.orientation.angle (celular
// acostado: la pantalla gira respecto del equipo).
export function cameraFromOrientation(alpha, beta, gamma, screenAngle = 0) {
  const [a, b, g] = [alpha * RAD, beta * RAD, gamma * RAD]
  const cA = Math.cos(a), sA = Math.sin(a)
  const cB = Math.cos(b), sB = Math.sin(b)
  const cG = Math.cos(g), sG = Math.sin(g)
  const x = [cA * cG - sA * sB * sG, cG * sA + cA * sB * sG, -cB * sG]
  const y = [-cB * sA, cA * cB, sB]
  const z = [cA * sG + cG * sA * sB, sA * sG - cA * cG * sB, cB * cG]
  const t = screenAngle * RAD
  return {
    forward: scale(z, -1),
    up: add(scale(y, Math.cos(t)), scale(x, Math.sin(t))),
    right: add(scale(x, Math.cos(t)), scale(y, -Math.sin(t))),
  }
}

// Cámara a partir de rumbo y cabeceo (modo simulado: arrastrar con el mouse).
export function cameraFromHeadingPitch(heading, pitch) {
  const forward = direction(heading, pitch)
  const right = [Math.cos(heading * RAD), -Math.sin(heading * RAD), 0]
  return { forward, up: cross(right, forward), right }
}

// Suaviza el temblor de los sensores: mezcla la cámara anterior con la nueva y
// vuelve a dejar los ejes perpendiculares.
export function blendCamera(prev, next, k) {
  if (!prev) return next
  const forward = unit(add(scale(prev.forward, 1 - k), scale(next.forward, k)))
  let up = add(scale(prev.up, 1 - k), scale(next.up, k))
  up = unit(add(up, scale(forward, -dot(up, forward))))
  return { forward, up, right: cross(forward, up) }
}

// Rumbo (grados desde el norte) y cabeceo (grados) hacia donde mira la cámara.
export function cameraHeadingPitch(cam) {
  const [e, n, u] = cam.forward
  // Mirando casi vertical el rumbo de "adelante" no se define: se usa el de
  // la parte de abajo de la pantalla (hacia el cielo) o de arriba (hacia el piso).
  const ref = Math.hypot(e, n) > 0.15 ? cam.forward : scale(cam.up, u > 0 ? -1 : 1)
  return {
    heading: norm360(Math.atan2(ref[0], ref[1]) / RAD),
    pitch: Math.asin(Math.max(-1, Math.min(1, u))) / RAD,
  }
}

// Gira la cámara alrededor de la vertical (corrección de brújula).
export function rotateCameraYaw(cam, deg) {
  if (!deg) return cam
  const c = Math.cos(-deg * RAD)
  const s = Math.sin(-deg * RAD)
  const rot = ([e, n, u]) => [e * c - n * s, e * s + n * c, u]
  return { forward: rot(cam.forward), up: rot(cam.up), right: rot(cam.right) }
}

// Proyección perspectiva. `focal` en píxeles de pantalla. Devuelve la posición
// en pantalla (si está delante de la cámara) y la dirección en el plano de la
// pantalla (para las flechas de los que quedan fuera del cuadro).
export function project(cam, dir, width, height, focal) {
  const xc = dot(dir, cam.right)
  const yc = dot(dir, cam.up)
  const zc = dot(dir, cam.forward)
  const angle = Math.atan2(xc, yc) // 0 = arriba, horario
  if (zc <= 0.01) return { visible: false, angle, behind: true }
  const x = width / 2 + (focal * xc) / zc
  const y = height / 2 - (focal * yc) / zc
  return { visible: x >= 0 && x <= width && y >= 0 && y <= height, x, y, angle, behind: false }
}

// Hacia dónde apunta la nariz de la aeronave en pantalla (grados, 0 = arriba,
// horario): se proyecta un punto 1 km adelante sobre su rumbo. null si no hay
// rumbo o queda detrás de la cámara.
export function noseAngle(cam, dist, azimuth, elevation, track, width, height, focal) {
  if (typeof track !== 'number') return null
  const [az, tr] = [azimuth * RAD, track * RAD]
  const p = [dist * Math.sin(az), dist * Math.cos(az), dist * Math.tan(elevation * RAD)]
  const a = project(cam, p, width, height, focal)
  const b = project(cam, add(p, [1000 * Math.sin(tr), 1000 * Math.cos(tr), 0]), width, height, focal)
  if (a.behind || b.behind) return null
  return Math.atan2(b.x - a.x, a.y - b.y) / RAD
}

// Punto del borde de la pantalla en la dirección `angle`, dejando `marginX` a
// los costados y `marginY` arriba y abajo (barras de la interfaz).
export function edgePoint(angle, width, height, marginX, marginY = marginX) {
  const cx = width / 2
  const cy = height / 2
  const dx = Math.sin(angle)
  const dy = -Math.cos(angle)
  const hw = Math.max(cx - marginX, 1)
  const hh = Math.max(cy - marginY, 1)
  const k = Math.min(Math.abs(dx) > 1e-6 ? hw / Math.abs(dx) : Infinity, Math.abs(dy) > 1e-6 ? hh / Math.abs(dy) : Infinity)
  return { x: cx + dx * k, y: cy + dy * k }
}

// Distancia focal en píxeles de pantalla para un video de `vw`×`vh` mostrado con
// object-fit: cover en `width`×`height`, si el lado largo del sensor abarca
// `fovLongDeg` grados.
export function focalForCover(width, height, vw, vh, fovLongDeg) {
  if (!vw || !vh) {
    vw = Math.max(width, height)
    vh = Math.min(width, height)
    if (height > width) [vw, vh] = [vh, vw]
  }
  const fLong = Math.max(vw, vh) / 2 / Math.tan((fovLongDeg / 2) * RAD)
  return fLong * Math.max(width / vw, height / vh)
}

const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SO', 'O', 'NO']
export const cardinal = (deg) => COMPASS[Math.round(norm360(deg) / 45) % 8]
