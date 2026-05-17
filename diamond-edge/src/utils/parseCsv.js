import Papa from 'papaparse'

export async function fetchCsv(path) {
  const res = await fetch(path, { cache: 'no-store' })
  if (!res.ok) throw new Error(`CSV not found: ${path}`)
  const text = await res.text()
  const { data, errors } = Papa.parse(text, {
    header: true,
    skipEmptyLines: true,
    dynamicTyping: true,
  })
  if (errors.length) console.warn(`CSV parse warnings for ${path}:`, errors)
  return data
}
