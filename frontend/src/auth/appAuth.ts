// Sign-in for the Android app. Google doesn't allow sign-in inside an app's WebView, so:
//  1. the app opens /api/auth/google?app=android&challenge=… in the system browser,
//  2. the backend returns to com.loonietoonie.app://auth/callback?code=… (a 2-minute one-time code),
//  3. the app exchanges the code plus its secret PKCE verifier for a session token.
// The token is the same sealed session the web app keeps in a cookie, sent as a bearer token.

import { Browser } from '@capacitor/browser'
import { Preferences } from '@capacitor/preferences'

const TOKEN_KEY = 'session-token'
const VERIFIER_KEY = 'sign-in-verifier'
export const APP_CALLBACK_PREFIX = 'com.loonietoonie.app://auth/callback'

let cachedToken: string | null | undefined

export async function getAppToken(): Promise<string | null> {
  if (cachedToken === undefined) cachedToken = (await Preferences.get({ key: TOKEN_KEY })).value
  return cachedToken
}

export async function setAppToken(token: string | null): Promise<void> {
  cachedToken = token
  if (token) await Preferences.set({ key: TOKEN_KEY, value: token })
  else await Preferences.remove({ key: TOKEN_KEY })
}

function base64Url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '')
}

/** PKCE (RFC 7636, S256): a random verifier and its SHA-256 challenge. */
export async function newPkcePair(): Promise<{ verifier: string; challenge: string }> {
  const verifier = base64Url(crypto.getRandomValues(new Uint8Array(48)))
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)))
  return { verifier, challenge: base64Url(digest) }
}

/** Opens Google Sign-In in the system browser. The result arrives later as a deep link. */
export async function startAppSignIn(apiBaseUrl: string): Promise<void> {
  const { verifier, challenge } = await newPkcePair()
  // Kept in storage too, in case Android restarts the app while the browser is open.
  await Preferences.set({ key: VERIFIER_KEY, value: verifier })
  const params = new URLSearchParams({ app: 'android', challenge })
  await Browser.open({ url: `${apiBaseUrl}/api/auth/google?${params}` })
}

export type CallbackResult = { code: string; verifier: string } | { error: string } | null

/** Reads a deep link. Returns null for links that aren't the sign-in callback. */
export async function readCallback(url: string): Promise<CallbackResult> {
  if (!url.startsWith(APP_CALLBACK_PREFIX)) return null
  const params = new URL(url).searchParams
  const verifier = (await Preferences.get({ key: VERIFIER_KEY })).value
  await Preferences.remove({ key: VERIFIER_KEY })
  const code = params.get('code')
  if (code && verifier) return { code, verifier }
  return { error: params.get('error') ?? 'invalid_state' }
}
