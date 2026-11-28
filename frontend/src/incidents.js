// Tipos de incidente del registro (los mismos de backend/monitor/behavior.py):
// cómo se llama, con qué ícono y de qué color se muestra cada uno.

export const KINDS = {
  entry: { title: 'Ingreso a la zona', short: 'INGRESO', icon: 'bi-exclamation-triangle-fill', color: 'danger' },
  emergency: { title: 'Emergencia declarada', short: 'EMERGENCIA', icon: 'bi-broadcast', color: 'danger' },
  loiter: { title: 'Da vueltas cerca de la zona', short: 'DANDO VUELTAS', icon: 'bi-arrow-repeat', color: 'warning' },
  rapid_descent: { title: 'Descenso brusco', short: 'DESCENSO BRUSCO', icon: 'bi-arrow-down-circle-fill', color: 'warning' },
  signal_lost: { title: 'Dejó de transmitir en la zona', short: 'SEÑAL PERDIDA', icon: 'bi-wifi-off', color: 'warning' },
  sighting: { title: 'Avistamiento sin transponder', short: 'AVISTAMIENTO', icon: 'bi-eye-fill', color: 'info' },
}

export const kindOf = (kind) => KINDS[kind] || { title: kind, short: String(kind).toUpperCase(), icon: 'bi-info-circle-fill', color: 'secondary' }

// Fecha y hora de un incidente, en la hora de la zona si se conoce.
export function when(epochSeconds, timeZone) {
  if (typeof epochSeconds !== 'number') return '—'
  const date = new Date(epochSeconds * 1000)
  const options = { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }
  try {
    return date.toLocaleString('es-AR', { ...options, timeZone: timeZone || undefined })
  } catch {
    return date.toLocaleString('es-AR', options)
  }
}
