// Fails the Android build if Plaid made it into the bundle (phase 12): the Android app offers
// statement import only, and Google Play reviews the app as shipped.
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

const MARKERS = ['cdn.plaid.com', 'react-plaid-link', 'PlaidLinkOpener', 'Linked banks (Plaid)']

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? files(path) : [path]
  })
}

const found = files(process.argv[2] ?? 'dist')
  .filter((path) => /\.(js|html|css|map)$/.test(path))
  .flatMap((path) => {
    const text = readFileSync(path, 'utf8')
    return MARKERS.filter((marker) => text.includes(marker)).map((marker) => `${path}: ${marker}`)
  })

if (found.length) {
  console.error(`Plaid code is in the Android bundle:\n${found.join('\n')}`)
  process.exit(1)
}
console.log('Android bundle check: no Plaid code.')
