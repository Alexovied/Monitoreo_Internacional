import { reactive } from 'vue'
import { api, ensureCsrf } from './api'

export const session = reactive({
  // { id, username, first_name, last_name, role, is_admin, is_guest }
  // Un visitante tiene id null, role 'guest' e is_guest true: solo mira el mapa.
  user: null,
  checked: false,
})

export async function loadSession() {
  try {
    session.user = await api('/auth/me/', { silent401: true })
  } catch {
    session.user = null
  } finally {
    session.checked = true
  }
}

export async function login(username, password) {
  await ensureCsrf()
  session.user = await api('/auth/login/', { method: 'POST', body: { username, password } })
  return session.user
}

// El backend dice si hoy se puede entrar como visitante (.env + Configuración).
export async function loginOptions() {
  try {
    return await api('/auth/options/', { silent401: true })
  } catch {
    return { guest_access: false }
  }
}

export async function enterAsGuest() {
  await ensureCsrf()
  session.user = await api('/auth/guest/', { method: 'POST' })
  return session.user
}

export async function logout() {
  try {
    await api('/auth/logout/', { method: 'POST', silent401: true })
  } finally {
    session.user = null
  }
}

export function roleLabel(user) {
  if (!user) return ''
  if (user.is_guest) return 'Visitante · solo lectura'
  return user.is_admin ? 'Administrador' : 'Usuario'
}

export function displayName(user) {
  if (!user) return ''
  const full = [user.first_name, user.last_name].filter(Boolean).join(' ')
  return full || user.username
}
