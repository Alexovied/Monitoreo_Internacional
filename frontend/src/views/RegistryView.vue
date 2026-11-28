<script setup>
// Registro: todo lo que pasó (ingresos, comportamientos raros y avistamientos),
// del más nuevo al más viejo. Cada fila abre su repetición en 3D.
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { confirmDialog } from '../confirm'
import { KINDS, kindOf, when } from '../incidents'
import { live } from '../live'
import { session } from '../session'

const router = useRouter()
const incidents = ref([])
const loading = ref(true)
const loadError = ref('')
const filter = ref('all')
const simulating = ref(false)
const simOpen = ref(false) // menú de simulacros (sin el JS de Bootstrap)
const message = ref('')

const SIMULATIONS = ['entry', 'loiter', 'emergency', 'rapid_descent', 'signal_lost']
const FILTERS = [
  { key: 'all', label: 'Todo' },
  { key: 'entry', label: 'Ingresos' },
  { key: 'behavior', label: 'Comportamiento raro' },
  { key: 'sighting', label: 'Avistamientos' },
]

const shown = computed(() =>
  incidents.value.filter((i) => {
    if (filter.value === 'all') return true
    if (filter.value === 'behavior') return i.kind !== 'entry' && i.kind !== 'sighting'
    return i.kind === filter.value
  }),
)
const counts = computed(() => {
  const c = { all: incidents.value.length, entry: 0, behavior: 0, sighting: 0 }
  for (const i of incidents.value) c[i.kind === 'entry' || i.kind === 'sighting' ? i.kind : 'behavior'] += 1
  return c
})

async function load({ quiet = false } = {}) {
  if (!quiet) loading.value = true
  try {
    incidents.value = await api('/incidents/')
    loadError.value = ''
  } catch (err) {
    loadError.value = err.message
  } finally {
    loading.value = false
  }
}

onMounted(load)
// Llega algo nuevo por el WebSocket: la lista se actualiza sola.
watch(() => live.incidentSeq, () => load({ quiet: true }))

function open(incident) {
  router.push({ name: 'replay', params: { id: incident.id } })
}

async function simulate(kind) {
  simOpen.value = false
  simulating.value = true
  message.value = ''
  try {
    const created = await api('/incidents/simulate/', { method: 'POST', body: { kind } })
    await load({ quiet: true })
    message.value = `Simulacro creado: ${created.summary}`
  } catch (err) {
    message.value = err.message
  } finally {
    simulating.value = false
  }
}

async function remove(incident) {
  const ok = await confirmDialog({
    title: 'Borrar del registro',
    message: `¿Borrar «${incident.label || kindOf(incident.kind).title}» (${when(incident.time, live.config?.time_zone)})? No se puede deshacer.`,
    confirmText: 'Borrar',
    danger: true,
  })
  if (!ok) return
  try {
    await api(`/incidents/${incident.id}/`, { method: 'DELETE' })
    incidents.value = incidents.value.filter((i) => i.id !== incident.id)
  } catch (err) {
    message.value = err.message
  }
}
</script>

<template>
  <div class="container py-4" data-registry>
    <div class="d-flex flex-wrap align-items-center gap-2 mb-3">
      <h1 class="h4 mb-0 me-auto"><i class="bi bi-journal-text me-2"></i>Registro</h1>
      <div v-if="session.user?.is_admin" class="dropdown">
        <button
          class="btn btn-outline-secondary btn-sm dropdown-toggle"
          type="button"
          :aria-expanded="simOpen"
          :disabled="simulating"
          @click="simOpen = !simOpen"
          title="Crea un incidente de mentira, marcado como simulacro, para probar o mostrar el sistema. No avisa por Telegram."
          data-simulate
        >
          <span v-if="simulating" class="spinner-border spinner-border-sm me-1"></span>
          <i v-else class="bi bi-joystick me-1"></i>Simulacro
        </button>
        <ul class="dropdown-menu dropdown-menu-end" :class="{ show: simOpen }" style="right: 0">
          <li v-for="kind in SIMULATIONS" :key="kind">
            <button class="dropdown-item" type="button" :data-simulate-kind="kind" @click="simulate(kind)">
              <i class="bi me-2" :class="KINDS[kind].icon"></i>{{ KINDS[kind].title }}
            </button>
          </li>
        </ul>
      </div>
    </div>

    <p class="text-body-secondary small">
      Ingresos a la zona, comportamientos raros (vueltas cerca de la zona, emergencia declarada, descenso brusco,
      transpondedor que se apaga) y avistamientos sin transponder cargados desde el Modo cielo. Tocá uno para volver a
      ver qué pasó en 3D.
    </p>

    <div v-if="message" class="alert alert-info py-2 small d-flex align-items-center" role="status">
      <span class="me-auto">{{ message }}</span>
      <button type="button" class="btn-close" aria-label="Cerrar" @click="message = ''"></button>
    </div>

    <ul class="nav nav-pills mb-3 gap-1">
      <li v-for="f in FILTERS" :key="f.key" class="nav-item">
        <button class="nav-link py-1" :class="{ active: filter === f.key }" type="button" @click="filter = f.key">
          {{ f.label }} <span class="badge rounded-pill ms-1" :class="filter === f.key ? 'text-bg-light' : 'text-bg-secondary'">{{ counts[f.key] }}</span>
        </button>
      </li>
    </ul>

    <div v-if="loading" class="text-center py-5 text-body-secondary">
      <div class="spinner-border" role="status"></div>
    </div>
    <div v-else-if="loadError" class="alert alert-danger">
      No se pudo cargar el registro: {{ loadError }}
      <button class="btn btn-sm btn-outline-danger ms-2" @click="load()">Reintentar</button>
    </div>
    <div v-else-if="!shown.length" class="text-center py-5 text-body-secondary" data-registry-empty>
      <i class="bi bi-journal fs-1 d-block mb-2"></i>
      <template v-if="incidents.length">No hay nada de este tipo en el registro.</template>
      <template v-else>Todavía no pasó nada. Cuando una aeronave entre a la zona o haga algo raro, va a quedar acá.</template>
    </div>

    <div v-else class="list-group shadow-sm">
      <div
        v-for="i in shown"
        :key="i.id"
        class="list-group-item list-group-item-action d-flex align-items-center gap-3 registry-row"
        role="button"
        tabindex="0"
        data-registry-row
        @click="open(i)"
        @keydown.enter="open(i)"
      >
        <span class="registry-icon rounded-circle d-flex align-items-center justify-content-center flex-shrink-0" :class="`text-bg-${kindOf(i.kind).color}`">
          <i class="bi" :class="kindOf(i.kind).icon"></i>
        </span>
        <div class="flex-grow-1 overflow-hidden">
          <div class="d-flex flex-wrap align-items-center column-gap-2">
            <strong>{{ i.label || 'Sin identificar' }}</strong>
            <span v-if="i.type" class="text-body-secondary small">{{ i.type }}</span>
            <span class="badge" :class="`text-bg-${kindOf(i.kind).color}`">{{ kindOf(i.kind).short }}</span>
            <span v-if="i.simulated" class="badge text-bg-secondary">SIMULACRO</span>
            <i v-if="i.has_photo" class="bi bi-camera-fill text-body-secondary" title="Con foto"></i>
          </div>
          <div class="small text-truncate">{{ i.summary }}</div>
          <div class="small text-body-secondary">
            {{ when(i.time, live.config?.time_zone) }}
            <template v-if="i.points > 1"> · recorrido de {{ i.points }} puntos<template v-if="!i.closed"> (se sigue grabando)</template></template>
          </div>
        </div>
        <button
          v-if="session.user?.is_admin"
          type="button"
          class="btn btn-sm btn-outline-danger flex-shrink-0"
          title="Borrar del registro"
          aria-label="Borrar del registro"
          @click.stop="remove(i)"
        >
          <i class="bi bi-trash"></i>
        </button>
        <i class="bi bi-chevron-right text-body-secondary flex-shrink-0"></i>
      </div>
    </div>
  </div>
</template>

<style scoped>
.registry-icon {
  width: 42px;
  height: 42px;
  font-size: 1.15rem;
}
.registry-row {
  cursor: pointer;
}
</style>
