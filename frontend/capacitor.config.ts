import type { CapacitorConfig } from '@capacitor/cli'

const config: CapacitorConfig = {
  appId: 'com.loonietoonie.app',
  appName: 'LoonieToonie',
  webDir: 'dist',
  android: {
    // Debug builds can be inspected from desktop Chrome (chrome://inspect).
    webContentsDebuggingEnabled: process.env.NODE_ENV !== 'production',
  },
}

export default config
