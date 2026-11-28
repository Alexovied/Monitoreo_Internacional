<script setup>
// Repetición de un incidente: la escena 3D (replayScene.js), la línea de tiempo
// y los datos de la aeronave en el instante elegido. Un avistamiento no tiene
// recorrido: se muestra hacia dónde apuntaba la cámara, la foto y qué había con
// transponder en ese momento.
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { altitude, dec1, heading, int, speed } from '../format'
import { kindOf, when } from '../incidents'
import { live } from '../live'

const route = useRoute()
const router = useRouter()

const incident = ref(null)
const loading = ref(true)
const loadError = ref('')
const canvasRef = ref(null)
const stageRef = ref(null)
const scene = shallowRef(null)
const noWebgl = ref(false)

const time = ref(0) // instante que se está mirando (s Unix)
const playing = ref(false)
const speedX = ref(20)
const follow = ref(false)
const state = ref(null)
const SPEEDS = [1, 5, 20, 60]
const vExag = ref(2) // exageración vertical de la escena (replayScene.V_EXAG)

const kind = computed(() => kindOf(incident.value?.kind))
const isSighting = computed(() => incident.value?.kind === 'sighting')
const detail = computed(() => incident.value?.data?.detail || {})
const track = computed(() => incident.value?.track || [])
const start = computed(() => (track.value.length ? track.value[0][0] : 0))
const end = computed(() => (track.value.length ? track.value[track.value.length - 1][0] : 0))
const duration = computed(() => Math.max(0, end.value - start.value))
const hasTimeline = computed(() => !isSighting.value && track.value.length > 1 && duration.value > 0)
const elapsed = computed(() => Math.max(0, time.value - start.value))
// Dónde cae el incidente en la línea de tiempo (0 a 100).
const incidentMark = computed(() => {
  if (!hasTimeline.value) return null
  const f = (incident.value.time - start.value) / duration.value
  return f >= 0 && f <= 1 ? f * 100 : null
})
const timeZone = computed(() => live.config?.time_zone)

const distanceKm = computed(() => {
  const s = state.value
  const z = incident.value?.zone
  if (!s || !z) return null
  const x = (s.lon - z.lon) * 111.195 * Math.cos((z.lat * Math.PI) / 180)
  const y = (s.lat - z.lat) * 111.195
  return Math.hypot(x, y)
})
const inside = ref(false)

const mmss = (seconds) => {
  const s = Math.round(seconds)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

let insideZoneFn = null
let raf = 0
let lastFrame = 0
let resizeObserver = null

async function load() {
  loading.value = true
  stop()
  disposeScene()
  try {
    incident.value = await api(`/incidents/${route.params.id}/`)
    loadError.value = ''
  } catch (err) {
    incident.value = null
    loadError.value = err.status === 404 ? 'Ese incidente ya no está en el registro.' : err.message
  } finally {
    loading.value = false
  }
  if (incident.value) await buildScene()
}

async function buildScene() {
  await new Promise((resolve) => requestAnimationFrame(resolve)) // que el canvas ya esté en la página
  if (!canvasRef.value) return
  try {
    const module = await import('../replayScene')
    insideZoneFn = module.insideZone
    vExag.value = module.V_EXAG
    scene.value = new module.ReplayScene(canvasRef.value, incident.value)
  } catch (err) {
    console.warn('Repetición: sin 3D', err)
    noWebgl.value = true
    return
  }
  resize()
  resizeObserver = new ResizeObserver(resize)
  resizeObserver.observe(stageRef.value)
  // Velocidad para que la repetición completa dure unos 25 segundos.
  const ideal = duration.value / 25
  speedX.value = SPEEDS.reduce((best, s) => (Math.abs(s - ideal) < Math.abs(best - ideal) ? s : best), SPEEDS[0])
  seek(start.value)
  if (hasTimeline.value) play()
}

function resize() {
  const el = stageRef.value
  if (el && scene.value) scene.value.resize(el.clientWidth, el.clientHeight)
}

function seek(t) {
  time.value = Math.min(end.value, Math.max(start.value, t))
  if (!scene.value) return
  state.value = scene.value.setTime(time.value)
  inside.value = !!(state.value && insideZoneFn?.(incident.value.zone, state.value))
}

function frame(now) {
  if (!playing.value) return
  const dt = Math.min(0.1, (now - lastFrame) / 1000)
  lastFrame = now
  seek(time.value + dt * speedX.value)
  if (time.value >= end.value) {
    playing.value = false
    return
  }
  raf = requestAnimationFrame(frame)
}

function play() {
  if (!hasTimeline.value) return
  if (time.value >= end.value) seek(start.value)
  playing.value = true
  lastFrame = performance.now()
  raf = requestAnimationFrame(frame)
}

function stop() {
  playing.value = false
  cancelAnimationFrame(raf)
}

function toggle() {
  if (playing.value) stop()
  else play()
}

function onSlider(event) {
  stop()
  seek(start.value + Number(event.target.value))
}

function jumpToIncident() {
  stop()
  seek(incident.value.time)
}

watch(follow, (on) => scene.value?.setFollow(on))

function disposeScene() {
  resizeObserver?.disconnect()
  resizeObserver = null
  scene.value?.dispose()
  scene.value = null
  state.value = null
  follow.value = false
}

onMounted(load)
watch(() => route.params.id, (id) => id && load())
onBeforeUnmount(() => {
  stop()
  disposeScene()
})
</script>

<template>
  <div class="replay d-flex flex-column" data-replay>
    <div class="replay-head d-flex flex-wrap align-items-center gap-2 px-3 py-2 border-bottom bg-body">
      <button type="button" class="btn btn-sm btn-outline-secondary" @click="router.push({ name: 'registry' })">
        <i class="bi bi-arrow-left me-1"></i>Registro
      </button>
      <template v-if="incident">
        <span class="badge fs-6" :class="`text-bg-${kind.color}`"><i class="bi me-1" :class="kind.icon"></i>{{ kind.title }}</span>
        <span v-if="incident.simulated" class="badge text-bg-secondary fs-6">SIMULACRO</span>
        <strong class="fs-6">{{ incident.label || 'Sin identificar' }}</strong>
        <span v-if="incident.type" class="text-body-secondary">{{ incident.type }}</span>
        <span class="text-body-secondary small ms-auto">{{ when(incident.time, timeZone) }}</span>
      </template>
    </div>

    <div v-if="loading" class="flex-grow-1 d-flex align-items-center justify-content-center text-body-secondary">
      <div class="spinner-border" role="status"></div>
    </div>
    <div v-else-if="loadError" class="flex-grow-1 d-flex align-items-center justify-content-center p-4">
      <div class="alert alert-warning mb-0">{{ loadError }}</div>
    </div>

    <template v-else>
      <div class="px-3 py-2 small border-bottom bg-body-tertiary" data-replay-summary>{{ incident.summary }}</div>

      <div class="replay-body flex-grow-1 d-flex flex-column position-relative">
      <div ref="stageRef" class="replay-stage flex-grow-1 position-relative">
        <canvas ref="canvasRef" class="replay-canvas"></canvas>
        <div v-if="noWebgl" class="position-absolute top-50 start-50 translate-middle text-center text-light p-3">
          <i class="bi bi-exclamation-triangle fs-2 d-block mb-2"></i>
          Este navegador no puede mostrar la vista 3D. Los datos del incidente están más abajo.
        </div>

        <!-- Datos de la aeronave en el instante elegido -->
        <div v-if="state && !isSighting" class="replay-card replay-data small" data-replay-data>
          <div class="d-flex align-items-center gap-2 mb-1">
            <strong>{{ when(time, timeZone) }}</strong>
            <span v-if="inside" class="badge text-bg-danger">DENTRO DE LA ZONA</span>
          </div>
          <div><span class="opacity-75">Altitud</span> <b>{{ altitude(state.alt) }}</b></div>
          <div><span class="opacity-75">Velocidad</span> <b>{{ speed(state.gs) }}</b></div>
          <div><span class="opacity-75">Rumbo</span> <b>{{ heading(state.heading) }}</b></div>
          <div v-if="distanceKm !== null"><span class="opacity-75">Al centro</span> <b>{{ dec1(distanceKm) }} km</b></div>
          <div v-if="Math.abs(state.climbFpm) > 200">
            <span class="opacity-75">{{ state.climbFpm > 0 ? 'Sube' : 'Baja' }}</span> <b>{{ int(Math.abs(state.climbFpm)) }} ft/min</b>
          </div>
        </div>

        <div class="replay-hint small"><i class="bi bi-arrows-move me-1"></i>Arrastrá para girar · rueda o pellizco para acercar · la altura se ve ×{{ vExag }}</div>
      </div>

      <!-- Avistamiento: foto, nota y lo más cercano con transponder -->
      <div v-if="isSighting" class="replay-card replay-sighting small" data-replay-sighting>
        <a v-if="incident.has_photo" :href="`/api/incidents/${incident.id}/photo/`" target="_blank" rel="noopener" class="d-block mb-2">
          <img :src="`/api/incidents/${incident.id}/photo/`" alt="Foto del avistamiento" class="img-fluid rounded" />
        </a>
        <div><span class="opacity-75">Apuntaba hacia</span> <b>{{ heading(detail.azimuth) }}</b>, <b>{{ int(Math.abs(detail.elevation || 0)) }}°</b> {{ (detail.elevation || 0) >= 0 ? 'sobre' : 'bajo' }} el horizonte</div>
        <div><span class="opacity-75">Desde</span> {{ detail.observer?.source === 'gps' ? 'su ubicación (GPS)' : 'el centro de la zona' }}</div>
        <div><span class="opacity-75">Informó</span> <b>{{ detail.reported_by || 'sin nombre' }}</b></div>
        <div v-if="detail.note" class="mt-1 fst-italic">«{{ detail.note }}»</div>
        <div class="mt-2">
          <template v-if="detail.candidates?.length">
            <div class="opacity-75">Lo más cercano con transponder:</div>
            <div v-for="c in detail.candidates" :key="c.hex">
              <b>{{ c.label }}</b> a {{ int(c.separation) }}° de donde apuntaba ({{ dec1(c.distance_m / 1000) }} km)
            </div>
          </template>
          <div v-else>No había ninguna aeronave con transponder en el área.</div>
        </div>
      </div>

      </div>

      <!-- Línea de tiempo -->
      <div v-if="hasTimeline" class="replay-controls px-3 py-2 border-top bg-body" data-replay-controls>
        <div class="position-relative">
          <input
            type="range"
            class="form-range"
            min="0"
            :max="duration"
            step="0.5"
            :value="elapsed"
            aria-label="Línea de tiempo"
            data-replay-slider
            @input="onSlider"
          />
          <span v-if="incidentMark !== null" class="replay-mark" :class="`bg-${kind.color}`" :style="{ left: `${incidentMark}%` }" title="Momento del incidente"></span>
        </div>
        <div class="d-flex flex-wrap align-items-center gap-2">
          <button type="button" class="btn btn-primary btn-sm" :aria-label="playing ? 'Pausar' : 'Reproducir'" data-replay-play @click="toggle">
            <i class="bi" :class="playing ? 'bi-pause-fill' : 'bi-play-fill'"></i>
          </button>
          <span class="small font-monospace">{{ mmss(elapsed) }} / {{ mmss(duration) }}</span>
          <div class="btn-group btn-group-sm" role="group" aria-label="Velocidad">
            <button v-for="s in SPEEDS" :key="s" type="button" class="btn" :class="speedX === s ? 'btn-secondary' : 'btn-outline-secondary'" @click="speedX = s">×{{ s }}</button>
          </div>
          <button v-if="incidentMark !== null" type="button" class="btn btn-sm" :class="`btn-outline-${kind.color === 'warning' ? 'secondary' : kind.color}`" @click="jumpToIncident">
            <i class="bi bi-flag-fill me-1"></i>Ir al incidente
          </button>
          <div class="form-check form-switch small mb-0 ms-auto">
            <input id="replay-follow" v-model="follow" class="form-check-input" type="checkbox" />
            <label class="form-check-label" for="replay-follow">Seguir a la aeronave</label>
          </div>
        </div>
      </div>
      <div v-else-if="!isSighting" class="px-3 py-2 border-top bg-body small text-body-secondary">
        De este incidente no quedó un recorrido para repetir: se muestra solo dónde estaba la aeronave.
      </div>
    </template>
  </div>
</template>

<style scoped>
.replay {
  height: 100%;
  min-height: 0;
}
.replay-stage {
  min-height: 240px;
  background: #0c1622;
  overflow: hidden;
}
.replay-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  display: block;
  touch-action: none;
}
.replay-card {
  position: absolute;
  top: 10px;
  left: 10px;
  max-width: min(300px, calc(100% - 20px));
  padding: 8px 10px;
  border-radius: 10px;
  color: #fff;
  background: rgba(12, 22, 34, 0.82);
  border: 1px solid rgba(255, 255, 255, 0.2);
  backdrop-filter: blur(4px);
}
.replay-body {
  min-height: 0;
}
.replay-sighting {
  max-height: calc(100% - 50px);
  overflow-y: auto;
}
/* Celular: la ficha del avistamiento va debajo de la vista 3D, no encima. */
@media (max-width: 767.98px) {
  .replay-sighting {
    position: static;
    max-width: none;
    max-height: 42%;
    border-radius: 0;
    border-width: 1px 0 0;
    flex-shrink: 0;
  }
}
.replay-sighting img {
  max-height: 170px;
}
.replay-hint {
  position: absolute;
  left: 10px;
  bottom: 8px;
  color: rgba(255, 255, 255, 0.7);
  text-shadow: 0 1px 2px #000;
  pointer-events: none;
}
.replay-mark {
  position: absolute;
  top: 2px;
  width: 4px;
  height: 20px;
  margin-left: -2px;
  border-radius: 2px;
  pointer-events: none;
}
</style>
