<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../api'
import { confirmDialog } from '../confirm'
import FormField from '../components/FormField.vue'
import { live } from '../live'
import { dec1, dms, int, isNumber, M_PER_FT } from '../format'

const MAX_RADIUS_NM = 250
const MIN_POLL_S = 2 // igual que MIN_POLL_INTERVAL_S del backend
const MIN_TEMP_C = 40 // igual que MIN_TEMP_ALERT_C y MAX_TEMP_ALERT_C del backend
const MAX_TEMP_C = 95
const FIELDS = [
  'zone_name', 'zone_lat', 'zone_lon', 'zone_radius_nm', 'zone_floor_m', 'zone_ceiling_m',
  'poll_interval_s', 'query_radius_nm', 'telegram_chat_id', 'guest_access',
  'temp_alert_enabled', 'temp_alert_threshold_c',
]
const NOTICE_STYLE = {
  ok: { cls: 'alert-success', icon: 'bi-check-circle-fill' },
  error: { cls: 'alert-danger', icon: 'bi-exclamation-circle-fill' },
  info: { cls: 'alert-info', icon: 'bi-info-circle-fill' },
}

const saved = ref(null) // última configuración recibida del servidor
const form = reactive(Object.fromEntries(FIELDS.map((f) => [f, ''])))
const token = ref('') // nuevo token; vacío = conservar el guardado
const errors = ref({})
const notice = ref(null) // { kind: 'ok' | 'error' | 'info', text }
const loading = ref(true)
const saving = ref(false)
const testing = ref(false)
const fileInput = ref(null)

const dirty = computed(
  () => !!saved.value && (token.value !== '' || FIELDS.some((f) => String(form[f]) !== String(saved.value[f]))),
)
const telegramDirty = computed(
  () => !!saved.value && (token.value !== '' || String(form.telegram_chat_id) !== String(saved.value.telegram_chat_id)),
)

function fill(config) {
  saved.value = config
  for (const f of FIELDS) form[f] = config[f]
  token.value = ''
}

async function load() {
  loading.value = true
  try {
    fill(await api('/config/'))
  } catch (err) {
    notice.value = { kind: 'error', text: err.message }
  } finally {
    loading.value = false
  }
}

// Si otro administrador guarda cambios y acá no hay nada sin guardar, recargar.
watch(
  () => live.config?.updated_at,
  (updatedAt) => {
    if (saved.value && updatedAt && updatedAt !== saved.value.updated_at && !dirty.value && !saving.value) load()
  },
)

const num = (v) => (v === '' || v === null ? NaN : Number(v))

function validate() {
  const e = {}
  const lat = num(form.zone_lat)
  const lon = num(form.zone_lon)
  const radius = num(form.zone_radius_nm)
  const floor = num(form.zone_floor_m)
  const ceiling = num(form.zone_ceiling_m)
  const poll = num(form.poll_interval_s)
  const query = num(form.query_radius_nm)

  if (!String(form.zone_name).trim()) e.zone_name = 'Poné un nombre para la zona.'
  if (!(lat >= -90 && lat <= 90)) e.zone_lat = 'Tiene que ser un número entre -90 y 90.'
  if (!(lon >= -180 && lon <= 180)) e.zone_lon = 'Tiene que ser un número entre -180 y 180.'
  if (!(radius > 0)) e.zone_radius_nm = 'Tiene que ser mayor que 0.'
  else if (radius > MAX_RADIUS_NM) e.zone_radius_nm = `Como máximo ${MAX_RADIUS_NM} NM.`
  if (!(floor >= 0)) e.zone_floor_m = 'Poné 0 para empezar desde el suelo, o un número mayor.'
  if (!(ceiling > 0)) e.zone_ceiling_m = 'Tiene que ser mayor que 0.'
  else if (floor >= 0 && !(ceiling > floor)) e.zone_ceiling_m = 'Tiene que ser más alta que la altura mínima.'
  if (!(Number.isInteger(poll) && poll >= MIN_POLL_S)) e.poll_interval_s = `Un número entero, de ${MIN_POLL_S} segundos o más.`
  const tempLimit = num(form.temp_alert_threshold_c)
  if (!(tempLimit >= MIN_TEMP_C && tempLimit <= MAX_TEMP_C)) e.temp_alert_threshold_c = `Entre ${MIN_TEMP_C} y ${MAX_TEMP_C} °C.`
  if (!(query > 0 && query <= MAX_RADIUS_NM)) e.query_radius_nm = `Entre 0 y ${MAX_RADIUS_NM} NM.`
  else if (radius > 0 && query < radius) e.query_radius_nm = 'Tiene que ser igual o mayor que el radio de la zona.'

  const chat = String(form.telegram_chat_id).trim()
  const newToken = token.value.trim()
  if (newToken && !/^\d+:[A-Za-z0-9_-]+$/.test(newToken)) {
    e.telegram_bot_token = 'No parece un token. Copialo entero desde @BotFather (es algo como 123456:ABC…).'
  }
  if (chat && !/^-?\d+$/.test(chat)) e.telegram_chat_id = 'Tiene que ser un número (el de los grupos empieza con -).'
  const hasToken = newToken !== '' || saved.value?.telegram_bot_token_masked
  if (chat && !hasToken) e.telegram_bot_token = 'Falta el token del bot.'
  if (!chat && newToken) e.telegram_chat_id = 'Falta elegir el grupo. Usá «Buscar mi grupo».'
  if (!chat && !newToken && saved.value?.telegram_bot_token_masked) {
    e.telegram_chat_id = 'Para dejar de usar Telegram tocá «Desconectar Telegram».'
  }

  errors.value = e
  return !Object.keys(e).length
}

function payload() {
  const body = {
    zone_name: String(form.zone_name).trim(),
    zone_lat: num(form.zone_lat),
    zone_lon: num(form.zone_lon),
    zone_radius_nm: num(form.zone_radius_nm),
    zone_floor_m: num(form.zone_floor_m),
    zone_ceiling_m: num(form.zone_ceiling_m),
    poll_interval_s: num(form.poll_interval_s),
    query_radius_nm: num(form.query_radius_nm),
    telegram_chat_id: String(form.telegram_chat_id).trim(),
    guest_access: !!form.guest_access,
    temp_alert_enabled: !!form.temp_alert_enabled,
    temp_alert_threshold_c: num(form.temp_alert_threshold_c),
  }
  if (token.value.trim()) body.telegram_bot_token = token.value.trim()
  return body
}

async function send(body, okText) {
  saving.value = true
  notice.value = null
  try {
    fill(await api('/config/', { method: 'PATCH', body }))
    errors.value = {}
    notice.value = { kind: 'ok', text: okText }
  } catch (err) {
    errors.value = err.fieldErrors
    notice.value = { kind: 'error', text: Object.keys(err.fieldErrors).length ? 'Hay datos para corregir: están marcados en rojo.' : err.message }
  } finally {
    saving.value = false
  }
}

async function save() {
  notice.value = null
  if (!validate()) {
    notice.value = { kind: 'error', text: 'Hay datos para corregir: están marcados en rojo.' }
    return
  }
  await send(payload(), 'Listo, se guardó. El mapa y las alertas ya usan los datos nuevos.')
}

function discard() {
  fill(saved.value)
  errors.value = {}
  notice.value = null
}

async function clearTelegram() {
  const ok = await confirmDialog({
    title: 'Desconectar Telegram',
    message: 'Se borran el bot y el grupo guardados. Las alertas dejan de llegar por Telegram (en la web se siguen viendo).',
    confirmText: 'Desconectar',
    danger: true,
  })
  if (ok) await send({ telegram_bot_token: '', telegram_chat_id: '' }, 'Telegram desconectado. Las alertas ya no se mandan al grupo.')
}

async function testTelegram() {
  testing.value = true
  notice.value = null
  try {
    const res = await api('/config/telegram-test/', { method: 'POST' })
    notice.value = { kind: 'ok', text: res.detail }
  } catch (err) {
    notice.value = { kind: 'error', text: err.message }
  } finally {
    testing.value = false
  }
}

// Buscar el grupo: Telegram dice en qué grupos recibió mensajes el bot.
const chats = ref(null) // null = sin buscar; [] = no encontró ninguno
const searchingChats = ref(false)

async function findChats() {
  searchingChats.value = true
  notice.value = null
  chats.value = null
  pickedChat.value = null
  chatError.value = ''
  try {
    const body = token.value.trim() ? { telegram_bot_token: token.value.trim() } : {}
    const res = await api('/config/telegram-chats/', { method: 'POST', body })
    chats.value = res.chats
    if (res.chats.length === 1) pickChat(res.chats[0])
  } catch (err) {
    errors.value = { ...errors.value, ...err.fieldErrors }
    chatError.value = err.message
  } finally {
    searchingChats.value = false
  }
}

// El resultado se muestra junto al botón (el aviso general queda arriba, fuera de la vista).
const pickedChat = ref(null)
const chatError = ref('')

function pickChat(chat) {
  form.telegram_chat_id = chat.id
  errors.value = { ...errors.value, telegram_chat_id: '' }
  pickedChat.value = chat
}

// Aviso de mantenimiento: se publica aparte del resto (no espera a «Guardar»).
const maintenance = reactive({ message: '', notify: true })
const publishing = ref(false)
const maintenanceNotice = ref(null) // se muestra dentro de la tarjeta, junto al botón
// Solo cuando cambia el aviso guardado: «Guardar» del resto no borra un borrador.
watch(
  () => saved.value?.maintenance_message,
  (message) => (maintenance.message = message || ''),
)

async function publishMaintenance(message) {
  publishing.value = true
  maintenanceNotice.value = null
  try {
    const res = await api('/config/maintenance/', {
      method: 'POST',
      body: { message, notify_telegram: maintenance.notify && saved.value.telegram_enabled },
    })
    saved.value = { ...saved.value, maintenance_message: res.maintenance_message, updated_at: res.updated_at }
    maintenance.message = res.maintenance_message
    let text = message ? 'Aviso publicado: todos los usuarios lo ven arriba de la pantalla.' : 'Aviso quitado.'
    if (res.telegram_result === true) text += ' También se mandó al grupo de Telegram.'
    const failed = typeof res.telegram_result === 'string'
    if (failed) text += ` Pero no se pudo mandar a Telegram: ${res.telegram_result}`
    maintenanceNotice.value = { kind: failed ? 'error' : 'ok', text }
  } catch (err) {
    maintenanceNotice.value = { kind: 'error', text: err.fieldErrors?.message || err.message }
  } finally {
    publishing.value = false
  }
}

async function loadOpenAir(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  const data = new FormData()
  data.append('file', file)
  notice.value = null
  try {
    const zone = await api('/config/openair/', { method: 'POST', form: data })
    Object.assign(form, zone)
    errors.value = {}
    notice.value = {
      kind: 'info',
      text: `Se cargó la zona «${zone.zone_name}» del archivo ${file.name}. Todavía no se guardó: revisala y tocá «Guardar».`,
    }
  } catch (err) {
    notice.value = { kind: 'error', text: `No se pudo leer ${file.name}: ${err.message}` }
  }
}

const ft = (m) => (isNumber(num(m)) ? int(num(m) / M_PER_FT) : '—')
const km = (nm) => (isNumber(num(nm)) ? dec1(num(nm) * 1.852) : '—')

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="mb-4">
      <h1 class="h3 mb-1"><i class="bi bi-gear me-2"></i>Configuración</h1>
      <p class="text-body-secondary mb-0">Acá elegís qué zona se vigila y adónde llegan los avisos. Los cambios valen apenas tocás «Guardar», para todos los usuarios.</p>
    </div>

    <p v-if="loading" class="text-body-secondary"><span class="spinner-border spinner-border-sm me-2"></span> Cargando…</p>

    <form v-else-if="saved" novalidate @submit.prevent="save">
      <div
        v-if="notice"
        class="alert d-flex align-items-center gap-2"
        :class="NOTICE_STYLE[notice.kind].cls"
        :data-notice="notice.kind"
        role="status"
      >
        <i class="bi" :class="NOTICE_STYLE[notice.kind].icon"></i>
        <span>{{ notice.text }}</span>
      </div>

      <!-- Zona -->
      <section class="card shadow-sm mb-3" data-section="zone">
        <div class="card-header bg-body d-flex flex-wrap align-items-center justify-content-between gap-3 py-3">
          <div>
            <h2 class="h5 mb-0"><i class="bi bi-bullseye text-danger me-2"></i>Zona vigilada</h2>
            <div class="small text-body-secondary">Si un avión entra en este círculo y vuela entre la altura mínima y la máxima, el sistema avisa.</div>
          </div>
          <input ref="fileInput" type="file" accept=".txt,.openair,.air,text/plain" hidden @change="loadOpenAir" />
          <button class="btn btn-outline-primary" type="button" @click="fileInput.click()">
            <i class="bi bi-file-earmark-arrow-up me-1"></i> Cargar zona desde un archivo OpenAir…
          </button>
        </div>
        <div class="card-body">
          <div class="row g-3">
            <FormField id="zone_name" v-model="form.zone_name" class="col-12" label="Nombre de la zona" icon="bi-tag" hint="Es el nombre que aparece en las alertas."
              maxlength="100" :error="errors.zone_name" />
            <FormField id="zone_lat" v-model="form.zone_lat" class="col-md-6 col-lg-3" label="Latitud del centro"
              icon="bi-geo-alt" type="number" step="any" min="-90" max="90" unit="°"
              :hint="dms(num(form.zone_lat), true) || 'Ej.: -35.1119. Al sur del ecuador es negativa.'" :error="errors.zone_lat" />
            <FormField id="zone_lon" v-model="form.zone_lon" class="col-md-6 col-lg-3" label="Longitud del centro"
              icon="bi-geo-alt" type="number" step="any" min="-180" max="180" unit="°"
              :hint="dms(num(form.zone_lon), false) || 'Ej.: -58.7261. Al oeste de Greenwich es negativa.'" :error="errors.zone_lon" />
            <FormField id="zone_radius_nm" v-model="form.zone_radius_nm" class="col-md-4 col-lg-2" label="Radio de la zona"
              type="number" step="any" min="0" unit="NM" :hint="`Unos ${km(form.zone_radius_nm)} km`"
              :error="errors.zone_radius_nm" />
            <FormField id="zone_floor_m" v-model="form.zone_floor_m" class="col-md-4 col-lg-2" label="Altura mínima"
              type="number" step="any" min="0" unit="m"
              :hint="num(form.zone_floor_m) === 0 ? '0 = desde el suelo' : `Unos ${ft(form.zone_floor_m)} pies`"
              :error="errors.zone_floor_m" />
            <FormField id="zone_ceiling_m" v-model="form.zone_ceiling_m" class="col-md-4 col-lg-2" label="Altura máxima"
              type="number" step="any" min="0" unit="m" :hint="`Unos ${ft(form.zone_ceiling_m)} pies`"
              :error="errors.zone_ceiling_m" />
          </div>
        </div>
      </section>

      <!-- Consulta -->
      <section class="card shadow-sm mb-3" data-section="query">
        <div class="card-header bg-body py-3">
          <h2 class="h5 mb-0"><i class="bi bi-broadcast me-2"></i>Búsqueda de aviones</h2>
          <div class="small text-body-secondary">Cada cuánto se buscan aviones (en adsb.fi) y hasta qué distancia del centro de la zona.</div>
        </div>
        <div class="card-body">
          <div class="row g-3">
            <FormField id="poll_interval_s" v-model="form.poll_interval_s" class="col-md-6" label="Buscar cada"
              icon="bi-arrow-repeat" type="number" step="1" :min="MIN_POLL_S" unit="s"
              :hint="`Mínimo ${MIN_POLL_S} segundos. Con menos, el mapa está más al día.`" :error="errors.poll_interval_s" />
            <FormField id="query_radius_nm" v-model="form.query_radius_nm" class="col-md-6" label="Distancia de búsqueda"
              icon="bi-circle" type="number" step="any" min="0" :max="MAX_RADIUS_NM" unit="NM"
              :hint="`Unos ${km(form.query_radius_nm)} km. Es también lo que abarca el mapa. Máximo ${MAX_RADIUS_NM} NM.`" :error="errors.query_radius_nm" />
          </div>
        </div>
      </section>

      <!-- Telegram -->
      <section class="card shadow-sm mb-3" data-section="telegram">
        <div class="card-header bg-body d-flex flex-wrap align-items-center justify-content-between gap-3 py-3">
          <div>
            <h2 class="h5 mb-0"><i class="bi bi-telegram text-primary me-2"></i>Alertas por Telegram</h2>
            <div class="small text-body-secondary">
              Cada alerta se manda a un grupo de Telegram. Para que alguien las reciba, sumalo a ese grupo desde Telegram.
            </div>
          </div>
          <span class="badge" :class="saved.telegram_enabled ? 'text-bg-success' : 'text-bg-secondary'">
            <i class="bi me-1" :class="saved.telegram_enabled ? 'bi-check-circle' : 'bi-dash-circle'"></i>
            {{ saved.telegram_enabled ? 'Conectado' : 'Sin conectar' }}
          </span>
        </div>
        <div class="card-body">
          <div class="row g-3">
            <FormField id="telegram_bot_token" v-model="token" class="col-md-6" label="Token del bot" icon="bi-key"
              type="password" autocomplete="off" :placeholder="saved.telegram_bot_token_masked || 'Sin configurar'"
              :hint="saved.telegram_bot_token_masked ? `Ya hay uno guardado (${saved.telegram_bot_token_masked}). Dejalo vacío para no cambiarlo.` : 'Te lo da @BotFather cuando creás el bot.'"
              :error="errors.telegram_bot_token" />
            <FormField id="telegram_chat_id" v-model="form.telegram_chat_id" class="col-md-6" label="Grupo (chat id)"
              icon="bi-chat-dots" autocomplete="off" placeholder="-1001234567890" hint="Se completa solo con «Buscar mi grupo»."
              :error="errors.telegram_chat_id" />
          </div>
          <div class="mt-3 p-3 rounded bg-body-tertiary">
            <div class="d-flex flex-wrap align-items-center gap-2">
              <button class="btn btn-primary" type="button" data-find-chat :disabled="searchingChats" @click="findChats">
                <span v-if="searchingChats" class="spinner-border spinner-border-sm me-1"></span>
                <i v-else class="bi bi-search me-1"></i>
                {{ searchingChats ? 'Buscando…' : 'Buscar mi grupo' }}
              </button>
              <span v-if="!chats" class="small text-body-secondary">
                Antes: agregá el bot al grupo y escribí <code>/start</code> en ese grupo.
              </span>
            </div>
            <div v-if="pickedChat" class="small text-success-emphasis mt-2" data-picked-chat>
              <i class="bi bi-check-circle-fill me-1"></i>Grupo encontrado: <b>«{{ pickedChat.title }}»</b>.
              <template v-if="pickedChat.id === String(saved.telegram_chat_id)">
                Es el que ya está guardado: no hace falta hacer nada más.
              </template>
              <template v-else>Tocá «Guardar» para usarlo.</template>
            </div>
            <div v-if="chatError" class="small text-danger-emphasis mt-2">
              <i class="bi bi-exclamation-circle me-1"></i>{{ chatError }}
            </div>
            <div v-if="chats && chats.length > 1" class="mt-2">
              <div class="small fw-semibold mb-1">El bot está en estos grupos. Elegí uno:</div>
              <div class="d-flex flex-wrap gap-2">
                <button
                  v-for="c in chats"
                  :key="c.id"
                  type="button"
                  class="btn btn-sm"
                  :class="String(form.telegram_chat_id) === c.id ? 'btn-success' : 'btn-outline-secondary'"
                  @click="pickChat(c)"
                >
                  <i class="bi bi-people me-1"></i>{{ c.title }}
                </button>
              </div>
            </div>
            <div v-else-if="chats && !chats.length" class="small text-warning-emphasis mt-2" data-no-chats>
              <i class="bi bi-exclamation-triangle me-1"></i>No encontré ningún grupo. Fijate que el bot esté en el
              grupo, escribí <code>/start</code> ahí y volvé a buscar. Telegram solo guarda los mensajes de las
              últimas 24 horas.
            </div>
          </div>
          <div class="d-flex flex-wrap align-items-center gap-2 mt-3">
            <button
              class="btn btn-outline-primary"
              type="button"
              data-telegram-test
              :disabled="!saved.telegram_enabled || telegramDirty || testing"
              :title="telegramDirty ? 'Primero guardá los cambios' : ''"
              @click="testTelegram"
            >
              <span v-if="testing" class="spinner-border spinner-border-sm me-1"></span>
              <i v-else class="bi bi-send me-1"></i>
              {{ testing ? 'Enviando…' : 'Enviar mensaje de prueba' }}
            </button>
            <button v-if="saved.telegram_enabled" class="btn btn-outline-danger" type="button" :disabled="saving" @click="clearTelegram">
              <i class="bi bi-x-circle me-1"></i> Desconectar Telegram
            </button>
            <span v-if="telegramDirty" class="small text-body-secondary">
              <i class="bi bi-info-circle me-1"></i>Guardá los cambios para poder mandar la prueba.
            </span>
          </div>
        </div>
      </section>

      <!-- Visitantes -->
      <section class="card shadow-sm mb-3" data-section="guest">
        <div class="card-header bg-body d-flex flex-wrap align-items-center justify-content-between gap-3 py-3">
          <div>
            <h2 class="h5 mb-0"><i class="bi bi-eye me-2"></i>Acceso de visitantes</h2>
            <div class="small text-body-secondary">
              Muestra el botón «Entrar como visitante» en la pantalla de ingreso. Un visitante entra sin cuenta y solo
              puede mirar el mapa: no ve ni cambia la configuración ni los usuarios.
            </div>
          </div>
          <span class="badge" :class="saved.guest_access_enabled ? 'text-bg-success' : 'text-bg-secondary'">
            <i class="bi me-1" :class="saved.guest_access_enabled ? 'bi-check-circle' : 'bi-dash-circle'"></i>
            {{ saved.guest_access_enabled ? 'Activado' : 'Desactivado' }}
          </span>
        </div>
        <div class="card-body">
          <div class="form-check form-switch">
            <input
              id="guest_access"
              v-model="form.guest_access"
              class="form-check-input"
              type="checkbox"
              role="switch"
              :disabled="!saved.guest_access_allowed && !form.guest_access"
            />
            <label class="form-check-label" for="guest_access">Permitir entrar como visitante</label>
          </div>
          <div v-if="!saved.guest_access_allowed" class="small text-warning-emphasis mt-2" data-guest-env-off>
            <i class="bi bi-lock me-1"></i>Está bloqueado en el servidor. Para poder activarlo, poné
            <code>GUEST_ACCESS_ENABLED=true</code> en el <code>.env</code> y reiniciá el backend.
          </div>
          <div v-else class="small text-body-secondary mt-2">
            Al desactivarlo, los visitantes que estén mirando salen en el momento.
          </div>
          <div v-if="errors.guest_access" class="small text-danger-emphasis mt-2">
            <i class="bi bi-exclamation-circle me-1"></i>{{ errors.guest_access }}
          </div>
        </div>
      </section>

      <!-- Temperatura del equipo -->
      <section class="card shadow-sm mb-3" data-section="temperature">
        <div class="card-header bg-body d-flex flex-wrap align-items-center justify-content-between gap-3 py-3">
          <div>
            <h2 class="h5 mb-0"><i class="bi bi-thermometer-high text-danger me-2"></i>Aviso de temperatura</h2>
            <div class="small text-body-secondary">
              Manda un mensaje al grupo de Telegram si el equipo se calienta demasiado, y otro cuando vuelve a la normalidad.
              No se repite: como mucho un aviso de calor por hora.
            </div>
          </div>
          <span class="badge" :class="saved.temp_alert_enabled ? 'text-bg-success' : 'text-bg-secondary'">
            <i class="bi me-1" :class="saved.temp_alert_enabled ? 'bi-check-circle' : 'bi-dash-circle'"></i>
            {{ saved.temp_alert_enabled ? 'Activado' : 'Desactivado' }}
          </span>
        </div>
        <div class="card-body">
          <div class="row g-3 align-items-start">
            <div class="col-md-6">
              <div class="form-check form-switch mt-md-4">
                <input id="temp_alert_enabled" v-model="form.temp_alert_enabled" class="form-check-input" type="checkbox" role="switch" />
                <label class="form-check-label" for="temp_alert_enabled">Avisar cuando el equipo esté caliente</label>
              </div>
              <div v-if="!saved.telegram_enabled" class="small text-warning-emphasis mt-2">
                <i class="bi bi-exclamation-triangle me-1"></i>Primero conectá Telegram: sin eso el aviso no llega.
              </div>
            </div>
            <FormField id="temp_alert_threshold_c" v-model="form.temp_alert_threshold_c" class="col-md-6" label="Avisar a partir de"
              icon="bi-thermometer-half" type="number" step="1" :min="MIN_TEMP_C" :max="MAX_TEMP_C" unit="°C"
              hint="Recomendado: 75 °C. La Raspberry Pi se pone lenta a 80 °C."
              :error="errors.temp_alert_threshold_c" />
          </div>
        </div>
      </section>

      <!-- Mantenimiento: se publica al instante, aparte de «Guardar». -->
      <section class="card shadow-sm mb-3" data-section="maintenance">
        <div class="card-header bg-body d-flex flex-wrap align-items-center justify-content-between gap-3 py-3">
          <div>
            <h2 class="h5 mb-0"><i class="bi bi-cone-striped text-warning me-2"></i>Aviso de mantenimiento</h2>
            <div class="small text-body-secondary">
              Si vas a apagar o arreglar el sistema, avisá antes. El mensaje aparece arriba de la pantalla de todos.
            </div>
          </div>
          <span class="badge" :class="saved.maintenance_message ? 'text-bg-warning' : 'text-bg-secondary'">
            {{ saved.maintenance_message ? 'Publicado' : 'Sin aviso' }}
          </span>
        </div>
        <div class="card-body">
          <label for="maintenance_message" class="form-label fw-semibold small mb-1">Mensaje</label>
          <textarea
            id="maintenance_message"
            v-model="maintenance.message"
            class="form-control"
            rows="2"
            maxlength="300"
            placeholder="Ej.: El lunes 18 de 10 a 12 h el sistema va a estar apagado por mantenimiento."
          ></textarea>
          <div class="form-check mt-2">
            <input
              id="maintenance_notify"
              v-model="maintenance.notify"
              class="form-check-input"
              type="checkbox"
              :disabled="!saved.telegram_enabled"
            />
            <label class="form-check-label" for="maintenance_notify">
              Mandarlo también al grupo de Telegram
              <span v-if="!saved.telegram_enabled" class="text-body-secondary">(primero conectá Telegram)</span>
            </label>
          </div>
          <div class="d-flex flex-wrap gap-2 mt-3">
            <button
              class="btn btn-warning"
              type="button"
              data-publish-maintenance
              :disabled="publishing || !maintenance.message.trim() || maintenance.message.trim() === saved.maintenance_message"
              @click="publishMaintenance(maintenance.message.trim())"
            >
              <i class="bi bi-megaphone me-1"></i> {{ saved.maintenance_message ? 'Actualizar aviso' : 'Publicar aviso' }}
            </button>
            <button
              v-if="saved.maintenance_message"
              class="btn btn-outline-secondary"
              type="button"
              data-clear-maintenance
              :disabled="publishing"
              @click="publishMaintenance('')"
            >
              <i class="bi bi-check2-circle me-1"></i> Terminó el mantenimiento: quitar aviso
            </button>
          </div>
          <div
            v-if="maintenanceNotice"
            class="alert d-flex align-items-center gap-2 mt-3 mb-0 py-2"
            :class="NOTICE_STYLE[maintenanceNotice.kind].cls"
            role="status"
            data-maintenance-notice
          >
            <i class="bi" :class="NOTICE_STYLE[maintenanceNotice.kind].icon"></i>
            <span>{{ maintenanceNotice.text }}</span>
          </div>
        </div>
      </section>

      <div class="save-bar card shadow d-flex flex-row flex-wrap align-items-center gap-2 px-3 py-2">
        <span class="me-auto small" :class="dirty ? 'text-warning-emphasis fw-semibold' : 'text-body-secondary'">
          <i class="bi me-1" :class="dirty ? 'bi-pencil-fill' : 'bi-check2'"></i>
          {{ dirty ? 'Tenés cambios sin guardar.' : 'No hay cambios.' }}
        </span>
        <button class="btn btn-outline-secondary" type="button" :disabled="!dirty || saving" @click="discard">
          <i class="bi bi-arrow-counterclockwise me-1"></i> Deshacer
        </button>
        <button class="btn btn-primary" type="submit" :disabled="!dirty || saving">
          <span v-if="saving" class="spinner-border spinner-border-sm me-1"></span>
          <i v-else class="bi bi-floppy me-1"></i>
          {{ saving ? 'Guardando…' : 'Guardar' }}
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.save-bar {
  position: sticky;
  bottom: 0.75rem;
  z-index: 10;
}
/* Pantallas chicas: el estado en una línea y los dos botones repartidos debajo. */
@media (max-width: 575.98px) {
  .save-bar > span {
    flex-basis: 100%;
  }
  .save-bar .btn {
    flex: 1;
  }
}
</style>
