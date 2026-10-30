// Fails the Android build if Plaid made it into the bundle (phase 12): the Android app offers
// statement import only, and Google Play reviews the app as shipped.
// With --release <url>, also fails unless the bundle talks to that HTTPS backend (never localhost
// or the laptop over Tailscale), so a release can't ship pointing at a development server.
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

const MARKERS = ['cdn.plaid.com', 'react-plaid-link', 'PlaidLinkOpener', 'Linked banks (Plaid)']

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? files(path) : [path]
  })
}

const args = process.argv.slice(2)
const releaseAt = args.indexOf('--release')
const releaseUrl = releaseAt === -1 ? null : args[releaseAt + 1]
const dir = args.find((arg, index) => !arg.startsWith('--') && index !== releaseAt + 1) ?? 'dist'

const texts = files(dir)
  .filter((path) => /\.(js|html|css|map)$/.test(path))
  .map((path) => ({ path, text: readFileSync(path, 'utf8') }))

const found = texts.flatMap(({ path, text }) =>
  MARKERS.filter((marker) => text.includes(marker)).map((marker) => `${path}: ${marker}`),
)
if (found.length) {
  console.error(`Plaid code is in the Android bundle:\n${found.join('\n')}`)
  process.exit(1)
}
console.log('Android bundle check: no Plaid code.')

if (releaseAt !== -1) {
  if (!releaseUrl || !/^https:\/\/[^/]+$/.test(releaseUrl) || /localhost|\.ts\.net/.test(releaseUrl)) {
    console.error(`--release needs the production backend's HTTPS origin, got: ${releaseUrl ?? '(none)'}`)
    process.exit(1)
  }
  const dev = texts.filter(({ text }) => text.includes('.ts.net')).map(({ path }) => path)
  if (dev.length) {
    console.error(`The release bundle mentions a Tailscale address:\n${dev.join('\n')}`)
    process.exit(1)
  }
  if (!texts.some(({ text }) => text.includes(releaseUrl))) {
    console.error(`The release bundle doesn't use ${releaseUrl}; was VITE_API_BASE_URL set?`)
    process.exit(1)
  }
  console.log(`Android release check: talks to ${releaseUrl}.`)
}
