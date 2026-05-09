import { useEffect, useRef } from 'react'

const BASEBALL_IMAGE = '/images/baseball-cutout.png'
const clamp = (value, min, max) => Math.max(min, Math.min(max, value))

export function BaseballArtwork({ className = '' }) {
  return (
    <span
      className={className}
      style={{ '--baseball-image': `url("${BASEBALL_IMAGE}")` }}
      aria-hidden="true"
    />
  )
}

export default function PremiumBaseball({ className = '' }) {
  const rootRef = useRef(null)

  useEffect(() => {
    const root = rootRef.current
    if (!root || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return undefined

    let frame = 0
    let targetTiltX = -7
    let targetTiltY = 12
    let targetRoll = -10
    let currentTiltX = -7
    let currentTiltY = 12
    let currentRoll = -10
    let lastX = null
    let lastY = null

    const setVars = () => {
      currentTiltX += (targetTiltX - currentTiltX) * 0.085
      currentTiltY += (targetTiltY - currentTiltY) * 0.085
      currentRoll += (targetRoll - currentRoll) * 0.075

      root.style.setProperty('--bb-tilt-x', `${currentTiltX.toFixed(2)}deg`)
      root.style.setProperty('--bb-tilt-y', `${currentTiltY.toFixed(2)}deg`)
      root.style.setProperty('--bb-roll', `${currentRoll.toFixed(2)}deg`)

      frame = requestAnimationFrame(setVars)
    }

    const onPointerMove = (event) => {
      const rect = root.getBoundingClientRect()
      const centerX = rect.left + rect.width / 2
      const centerY = rect.top + rect.height / 2
      const dx = clamp((event.clientX - centerX) / rect.width, -1, 1)
      const dy = clamp((event.clientY - centerY) / rect.height, -1, 1)
      const velocityX = lastX == null ? 0 : event.clientX - lastX
      const velocityY = lastY == null ? 0 : event.clientY - lastY

      targetTiltX = clamp(-dy * 18, -18, 18)
      targetTiltY = clamp(dx * 26, -26, 26)
      targetRoll += clamp((velocityX * 0.28) + (velocityY * 0.12), -16, 16)

      lastX = event.clientX
      lastY = event.clientY
    }

    const onPointerLeave = () => {
      targetTiltX = -7
      targetTiltY = 12
      targetRoll = Math.round(targetRoll / 360) * 360 - 10
      lastX = null
      lastY = null
    }

    frame = requestAnimationFrame(setVars)
    window.addEventListener('pointermove', onPointerMove, { passive: true })
    window.addEventListener('pointerleave', onPointerLeave)
    window.addEventListener('blur', onPointerLeave)

    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerleave', onPointerLeave)
      window.removeEventListener('blur', onPointerLeave)
    }
  }, [])

  return (
    <div
      ref={rootRef}
      className={`premium-baseball ${className}`.trim()}
      aria-hidden="true"
    >
      <div className="premium-baseball__aura" />
      <div className="premium-baseball__shadow" />
      <div className="premium-baseball__stage">
        <BaseballArtwork className="premium-baseball__sphere" />
      </div>
    </div>
  )
}
