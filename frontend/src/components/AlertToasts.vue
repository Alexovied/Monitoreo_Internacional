<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { dismissIncident, dismissToast, live } from '../live'
import { altitude, clock, distance, heading, identification, speed } from '../format'
import { kindOf } from '../incidents'

const router = useRouter()
const toasts = computed(() => live.alerts.filter((a) => a.toast))
const incidentToasts = computed(() => live.incidents.filter((i) => i.toast))

// Comportamiento raro o avistamiento: se abre su repetición en el registro.
function openIncident(incident) {
  dismissIncident(incident.key)
  if (incident.id) router.push({ name: 'replay', params: { id: incident.id } })
  else router.push({ name: 'registry' })
}

function replay(alert) {
  dismissToast(alert.id)
  router.push({ name: 'replay', params: { id: alert.incident_id } })
}

function open(alert) {
  dismissToast(alert.id)
  live.selectedHex = alert.hex
  live.focusHex = alert.hex
  if (router.currentRoute.value.name !== 'map') router.push({ name: 'map' })
}
</script>

<template>
  <div
    class="toast-container position-fixed p-3 alert-toasts"
    :class="{ 'beside-sidebar': $route.name === 'map' }"
    aria-live="assertive"
  >
    <TransitionGroup name="toast-slide">
      <div
        v-for="a in toasts"
        :key="a.id"
        class="toast show border-danger border-2 shadow"
        role="alert"
        aria-atomic="true"
        @click="open(a)"
      >
        <div class="toast-header text-bg-danger">
          <i class="bi bi-exclamation-triangle-fill me-2"></i>
          <strong class="me-auto">Ingreso a {{ a.zone }}</strong>
          <small class="text-white-50">{{ clock(a.time, live.config?.time_zone) }}</small>
          <button
            type="button"
            class="btn-close btn-close-white ms-2"
            aria-label="Cerrar"
            @click.stop="dismissToast(a.id)"
          ></button>
        </div>
        <div class="toast-body">
          <div class="fs-6 fw-bold mb-2"><i class="bi bi-airplane-fill me-1 text-danger"></i> {{ identification(a) }}</div>
          <dl class="row small mb-1">
            <dt class="col-4 fw-normal text-body-secondary">Altitud</dt>
            <dd class="col-8 mb-1 fw-semibold">{{ altitude(a.alt_ft) }}</dd>
            <dt class="col-4 fw-normal text-body-secondary">Velocidad</dt>
            <dd class="col-8 mb-1 fw-semibold">{{ speed(a.gs) }}</dd>
            <dt class="col-4 fw-normal text-body-secondary">Rumbo</dt>
            <dd class="col-8 mb-1 fw-semibold">{{ heading(a.track) }}</dd>
            <dt class="col-4 fw-normal text-body-secondary">Distancia</dt>
            <dd class="col-8 mb-1 fw-semibold">{{ distance(a.dst) }}</dd>
          </dl>
          <div class="d-flex align-items-center small text-body-secondary">
            <span><i class="bi bi-cursor me-1"></i>Clic para verla en el mapa</span>
            <button v-if="a.incident_id" type="button" class="btn btn-sm btn-outline-danger ms-auto py-0" @click.stop="replay(a)">
              <i class="bi bi-box me-1"></i>Ver en 3D
            </button>
          </div>
        </div>
      </div>
      <div
        v-for="i in incidentToasts"
        :key="`i${i.key}`"
        class="toast show border-2 shadow"
        :class="`border-${kindOf(i.kind).color}`"
        role="alert"
        aria-atomic="true"
        data-incident-toast
        @click="openIncident(i)"
      >
        <div class="toast-header" :class="`text-bg-${kindOf(i.kind).color}`">
          <i class="bi me-2" :class="kindOf(i.kind).icon"></i>
          <strong class="me-auto">{{ kindOf(i.kind).title }}</strong>
          <small class="opacity-75">{{ clock(i.time, live.config?.time_zone) }}</small>
          <button
            type="button"
            class="btn-close ms-2"
            :class="{ 'btn-close-white': kindOf(i.kind).color === 'danger' }"
            aria-label="Cerrar"
            @click.stop="dismissIncident(i.key)"
          ></button>
        </div>
        <div class="toast-body">
          <div class="fs-6 fw-bold mb-1">
            <span v-if="i.simulated" class="badge text-bg-secondary me-1">SIMULACRO</span>{{ i.label || 'Sin identificar' }}
          </div>
          <div class="small mb-1">{{ i.summary }}</div>
          <div class="small text-body-secondary"><i class="bi bi-box me-1"></i>Clic para ver qué pasó</div>
        </div>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.alert-toasts {
  top: 56px;
  right: 0;
  z-index: 2000;
}
/* En el mapa: a la izquierda del panel lateral y del selector de capas. */
@media (min-width: 992px) {
  .alert-toasts.beside-sidebar {
    right: 400px;
  }
}
/* Tablet: el panel va debajo del mapa; solo se deja libre el selector de capas. */
@media (min-width: 576px) and (max-width: 991.98px) {
  .alert-toasts.beside-sidebar {
    right: 56px;
  }
}
/* Pantallas chicas: los avisos ocupan el ancho disponible, debajo de la barra. */
@media (max-width: 575.98px) {
  .alert-toasts {
    left: 0;
    padding: 0.5rem !important;
  }
  .alert-toasts .toast {
    width: 100%;
  }
}
.toast {
  --bs-toast-bg: var(--bs-body-bg); /* opaco: sobre el mapa el semitransparente no se lee */
  cursor: pointer;
}
.toast-slide-enter-active,
.toast-slide-leave-active {
  transition: all 0.25s ease;
}
.toast-slide-enter-from {
  opacity: 0;
  transform: translateX(30px);
}
.toast-slide-leave-to {
  opacity: 0;
}
</style>
