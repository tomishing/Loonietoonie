import { Capacitor } from '@capacitor/core'

/** True inside the Android (later iOS) app; false in the browser / installed PWA. */
export const isNativeApp = Capacitor.isNativePlatform()
