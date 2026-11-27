// Qué modelo 3D real (public/models/*.glb) se usa para cada aeronave: primero
// por su tipo ICAO (campo `type` de adsb.fi, p. ej. "A320", "B38M"), y si no
// está en la lista, por su categoría ADS-B. Orígenes y licencias de los
// modelos en public/models/LICENCIAS.md.

const BY_TYPE = {
  'a320.glb': ['A318', 'A319', 'A320', 'A19N', 'A20N'],
  'a321.glb': ['A321', 'A21N'],
  'b738.glb': ['B731', 'B732', 'B733', 'B734', 'B735', 'B736', 'B737', 'B738', 'B739', 'B37M', 'B38M', 'B39M', 'B3XM', 'B752', 'B753'],
  'e190.glb': ['E170', 'E175', 'E75L', 'E75S', 'E190', 'E195', 'E290', 'E295', 'E135', 'E145', 'E45X', 'BCS1', 'BCS3', 'A220'],
  'crj900.glb': ['CRJ1', 'CRJ2', 'CRJ7', 'CRJ9', 'CRJX', 'F70', 'F100', 'B712', 'MD82', 'MD83', 'MD88'],
  'atr42.glb': ['AT43', 'AT44', 'AT45', 'AT46', 'AT72', 'AT73', 'AT75', 'AT76', 'SF34', 'JS41', 'D328', 'F50', 'C212', 'CN35'],
  'q400.glb': ['DH8A', 'DH8B', 'DH8C', 'DH8D', 'C295', 'C130', 'A400'],
  'b788.glb': ['B787', 'B788', 'B789', 'B78X', 'B762', 'B763', 'B764', 'A332', 'A333', 'A338', 'A339', 'A359', 'A35K', 'A306', 'A310'],
  'b772.glb': ['B772', 'B773', 'B77L', 'B77W', 'B778', 'B779', 'A342', 'A343', 'A345', 'A346', 'MD11'],
  'b744.glb': ['B741', 'B742', 'B743', 'B744', 'B748', 'B74F', 'A388', 'AN24', 'A124'],
  'citation.glb': [
    'C500', 'C501', 'C510', 'C525', 'C25A', 'C25B', 'C25C', 'C25M', 'C550', 'C560', 'C56X', 'C650', 'C680', 'C68A', 'C700', 'C750',
    'E50P', 'E55P', 'E545', 'E550', 'LJ31', 'LJ35', 'LJ40', 'LJ45', 'LJ60', 'LJ75', 'CL30', 'CL35', 'CL60', 'GLEX', 'GL5T', 'GL7T',
    'GLF4', 'GLF5', 'GLF6', 'G280', 'FA7X', 'FA8X', 'F900', 'F2TH', 'H25B', 'PC24', 'HDJT', 'PRM1', 'BE40',
  ],
  'pa28.glb': [
    'C150', 'C152', 'C162', 'C170', 'C172', 'C175', 'C177', 'C180', 'C182', 'C185', 'C206', 'C210', 'P28A', 'P28B', 'P28R', 'P28T',
    'PA28', 'PA32', 'PA34', 'PA44', 'PA46', 'P32R', 'SR20', 'SR22', 'S22T', 'DA40', 'DA42', 'DA62', 'DA20', 'M20P', 'M20T', 'BE33',
    'BE35', 'BE36', 'BE55', 'BE58', 'BE9L', 'BE20', 'C208', 'PC12', 'TBM7', 'TBM8', 'TBM9', 'RV7', 'RV8', 'RV10', 'AA5', 'PA18',
    'PA22', 'PA25', 'PA38', 'AT3', 'AT8T', 'IPAN', 'BL8', 'C42', 'CRUZ', 'P92', 'TOBA', 'TB20', 'TAMP',
  ],
  'ask21.glb': ['ASK2', 'AS21', 'ASK1', 'ASW2', 'ASW1', 'ASG2', 'ASG3', 'DG80', 'DG1T', 'LS4', 'LS8', 'DISC', 'DUOD', 'JANU', 'GLID', 'ARCP', 'VENT'],
  'heli.glb': [
    'EC35', 'EC30', 'EC45', 'EC55', 'EC20', 'EC25', 'EC75', 'H160', 'AS50', 'AS55', 'AS65', 'AS32', 'B06', 'B407', 'B412', 'B429',
    'B505', 'R22', 'R44', 'R66', 'A109', 'A119', 'A139', 'A169', 'A189', 'S76', 'S92', 'H500', 'MD60', 'BK17', 'UH60', 'HELI',
  ],
  'balloon.glb': ['BALL'],
  'drone.glb': ['DRON', 'UAV'],
}

const BY_CATEGORY = {
  A1: 'pa28.glb', // liviana
  A2: 'citation.glb', // chica (ejecutivos, turbohélices chicos)
  A3: 'a320.glb', // mediana (la mayoría de las líneas aéreas)
  A4: 'b738.glb', // alto vórtice (757)
  A5: 'b772.glb', // pesada
  A6: 'citation.glb', // alta performance
  A7: 'heli.glb',
  B1: 'ask21.glb', // planeador
  B2: 'balloon.glb',
  B4: 'pa28.glb', // ultraliviano
  B6: 'drone.glb',
}

const TYPE_TO_FILE = new Map()
for (const [file, types] of Object.entries(BY_TYPE)) for (const t of types) TYPE_TO_FILE.set(t, file)

export function modelFileFor(ac) {
  const type = (ac.type || '').toUpperCase()
  return TYPE_TO_FILE.get(type) || BY_CATEGORY[ac.category] || 'a320.glb'
}
