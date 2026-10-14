import { useState } from 'react'
import type { User } from '../api/client'

/** Google profile photo, or the first letter of the name when there is none (or it fails to load). */
export function Avatar({ user, size }: { user: User; size: number }) {
  const [failed, setFailed] = useState(false)
  const initial = (user.name ?? user.email).trim().charAt(0).toUpperCase()

  if (user.picture && !failed) {
    return (
      <img
        className="avatar"
        src={user.picture}
        alt=""
        width={size}
        height={size}
        referrerPolicy="no-referrer"
        onError={() => setFailed(true)}
      />
    )
  }
  return (
    <span className="avatar avatar-initial" style={{ width: size, height: size, fontSize: size * 0.42 }} aria-hidden="true">
      {initial}
    </span>
  )
}
