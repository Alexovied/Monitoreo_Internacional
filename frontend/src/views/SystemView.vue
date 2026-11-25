<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'
import { dec1, int } from '../format'

const REFRESH_MS = 15000
// Rango de la barra de temperatura (la Raspberry Pi se frena a 80 °C).
const TEMP_MIN = 30
const TEMP_MAX = 85

const info = ref(null)
const error = ref('')
const loading = ref(true)
let timer = null

async function load() {
  try {
    info.value = await api('/system/')
    error.value = ''
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

const gb = (bytes) => dec1(bytes / 1024 ** 3)
const clampPct = (n) => Math.max(0, Math.min(100, n))

// Nivel por porcentaje de uso (memoria y disco).
function usageLevel(percent) {
  if (percent >= 90) return { cls: 'danger', label: 'Casi lleno' }
  if (percent >= 75) return { cls: 'warning', label: 'Alto' }
  return { cls: 'success', label: 'Bien' }
}

const temp = computed(() => {
  const t = info.value?.temperature_c
  if (t === null || t === undefined) return null
  const limit = info.value.temp_alert.threshold_c
  let level = { cls: 'success', label: 'Normal' }
  if (t >= limit) level = { cls: 'danger', label: 'Muy caliente' }
  else if (t >= limit - 10) level = { cls: 'warning', label: 'Tibio' }
  return { value: t, level, pct: clampPct(((t - TEMP_MIN) / (TEMP_MAX - TEMP_MIN)) * 100) }
})

const cards = computed(() => {
  if (!info.value) return []
  const out = []
  for (const [key, title, icon] of [
    ['memory', 'Memoria', 'bi-memory'],
    ['disk', 'Almacenamiento', 'bi-device-hdd'],
  ]) {
    const u = info.value[key]
    if (!u) continue
    out.push({
      key,
      title,
      icon,
      value: `${int(u.percent)} %`,
      pct: clampPct(u.percent),
      level: usageLevel(u.percent),
      detail: `${gb(u.used)} GB usados de ${gb(u.total)} GB`,
    })
  }
  return out
})

const uptime = computed(() => {
  const s = info.value?.uptime_s
  if (!s) return ''
  const days = Math.floor(s / 86400)
  const hours = Math.floor((s % 86400) / 3600)
  const minutes = Math.floor((s % 3600) / 60)
  if (days) return `${days} ${days === 1 ? 'día' : 'días'} y ${hours} h`
  if (hours) return `${hours} h y ${minutes} min`
  return `${minutes} min`
})

// Versión: se busca una sola vez al abrir la página (GitHub limita las consultas).
const ver = ref(null)
const verError = ref('')

async function loadVersion() {
  try {
    ver.value = await api('/system/version/')
  } catch (err) {
    verError.value = err.message
  }
}

// Estado de la versión en palabras simples: un ícono, un título y una frase.
const version = computed(() => {
  if (verError.value) return { icon: 'bi-question-circle', cls: 'text-body-secondary', title: 'No se pudo comprobar' }
  if (!ver.value) return null
  if (!ver.value.sha) return { icon: 'bi-question-circle', cls: 'text-body-secondary', title: 'No se sabe qué versión está instalada' }
  const u = ver.value.updates
  if (u?.status === 'ok' && u.behind) {
    return {
      icon: 'bi-arrow-up-circle-fill',
      cls: 'text-warning',
      title: 'Hay una versión nueva',
      text: `Tiene ${u.behind} ${u.behind === 1 ? 'mejora' : 'mejoras'} que este equipo todavía no tiene. Pedile a quien mantiene el sistema que lo actualice.`,
      link: u.compare_url,
    }
  }
  if (u?.status === 'ok') {
    return { icon: 'bi-check-circle-fill', cls: 'text-success', title: 'Está al día', text: 'Tiene la última versión.' }
  }
  return {
    icon: 'bi-question-circle',
    cls: 'text-body-secondary',
    title: 'No se pudo comprobar si hay una versión nueva',
    text: 'Se vuelve a intentar en unos minutos.',
  }
})

const verDate = computed(() => {
  const d = ver.value?.updates?.date
  return d ? new Date(d).toLocaleDateString('es-AR', { day: 'numeric', month: 'long', year: 'numeric' }) : ''
})

onMounted(() => {
  load()
  loadVersion()
  timer = setInterval(() => document.visibilityState === 'visible' && load(), REFRESH_MS)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="page">
    <div class="mb-4">
      <h1 class="h3 mb-1"><i class="bi bi-cpu me-2"></i>Sistema</h1>
      <p class="text-body-secondary mb-0">Cómo está el equipo donde corre el monitor. Se actualiza solo cada 15 segundos.</p>
    </div>

    <p v-if="loading" class="text-body-secondary"><span class="spinner-border spinner-border-sm me-2"></span> Cargando…</p>

    <div v-if="error" class="alert alert-danger d-flex align-items-center gap-2" role="alert">
      <i class="bi bi-exclamation-circle-fill"></i> {{ error }}
    </div>

    <template v-if="info">
      <div class="row g-3 mb-3">
        <!-- Temperatura -->
        <div class="col-md-4">
          <section class="card shadow-sm h-100" data-system="temperature">
            <div class="card-body">
              <div class="d-flex align-items-center justify-content-between mb-2">
                <h2 class="h6 mb-0 text-body-secondary"><i class="bi bi-thermometer-half me-2"></i>Temperatura</h2>
                <span v-if="temp" class="badge" :class="`text-bg-${temp.level.cls}`">{{ temp.level.label }}</span>
              </div>
              <template v-if="temp">
                <div class="display-6 fw-semibold tabular mb-2">{{ dec1(temp.value) }} °C</div>
                <div class="progress" role="progressbar" style="height: 6px" :aria-valuenow="temp.value" aria-valuemin="30" aria-valuemax="85">
                  <div class="progress-bar" :class="`bg-${temp.level.cls}`" :style="{ width: `${temp.pct}%` }"></div>
                </div>
                <div class="small text-body-secondary mt-2">Límite de aviso: {{ int(info.temp_alert.threshold_c) }} °C</div>
              </template>
              <div v-else class="text-body-secondary mt-3">No disponible en este equipo.</div>
            </div>
          </section>
        </div>

        <!-- Memoria y almacenamiento -->
        <div v-for="c in cards" :key="c.key" class="col-md-4">
          <section class="card shadow-sm h-100" :data-system="c.key">
            <div class="card-body">
              <div class="d-flex align-items-center justify-content-between mb-2">
                <h2 class="h6 mb-0 text-body-secondary"><i class="bi me-2" :class="c.icon"></i>{{ c.title }}</h2>
                <span class="badge" :class="`text-bg-${c.level.cls}`">{{ c.level.label }}</span>
              </div>
              <div class="display-6 fw-semibold tabular mb-2">{{ c.value }}</div>
              <div class="progress" role="progressbar" style="height: 6px" :aria-valuenow="c.pct" aria-valuemin="0" aria-valuemax="100">
                <div class="progress-bar" :class="`bg-${c.level.cls}`" :style="{ width: `${c.pct}%` }"></div>
              </div>
              <div class="small text-body-secondary mt-2 tabular">{{ c.detail }}</div>
            </div>
          </section>
        </div>
      </div>

      <!-- Versión -->
      <section class="card shadow-sm mb-3" data-system="version">
        <div class="card-body">
          <h2 class="h6 mb-3 text-body-secondary"><i class="bi bi-box-seam me-2"></i>Versión del programa</h2>
          <div v-if="!version" class="small text-body-secondary">
            <span class="spinner-border spinner-border-sm me-1"></span> Comprobando…
          </div>
          <div v-else class="d-flex align-items-start gap-3" data-version-status>
            <i class="bi fs-3 lh-1" :class="[version.icon, version.cls]"></i>
            <div>
              <div class="fw-semibold">{{ version.title }}</div>
              <div v-if="version.text" class="small text-body-secondary">{{ version.text }}</div>
              <a v-if="version.link" class="small" :href="version.link" target="_blank" rel="noopener">
                Ver qué cambió <i class="bi bi-box-arrow-up-right"></i>
              </a>
            </div>
          </div>
          <!-- Datos para quien mantiene el sistema, discretos. -->
          <div v-if="ver?.sha" class="small text-body-secondary mt-3 pt-2 border-top">
            Versión instalada<template v-if="verDate"> del {{ verDate }}</template> · código {{ ver.sha.slice(0, 7) }}
          </div>
        </div>
      </section>

      <div class="d-flex flex-wrap gap-3 small text-body-secondary">
        <span v-if="uptime"><i class="bi bi-power me-1"></i>Encendido hace {{ uptime }}</span>
        <span>
          <i class="bi me-1" :class="info.temp_alert.enabled ? 'bi-bell' : 'bi-bell-slash'"></i>
          <template v-if="info.temp_alert.enabled">
            Aviso por Telegram si pasa los {{ int(info.temp_alert.threshold_c) }} °C<template v-if="!info.temp_alert.telegram_enabled"> (Telegram sin conectar)</template>.
          </template>
          <template v-else>Aviso de temperatura desactivado.</template>
          <RouterLink to="/configuracion" class="ms-1">Cambiar</RouterLink>
        </span>
      </div>
    </template>
  </div>
</template>
