import { useEffect, useRef } from 'react'
import { BaseballArtwork } from './PremiumBaseball'

const interactiveSelector = 'a, button, [role="button"], input, select, textarea, label, .de-results-table th.sortable'

export default function BaseballCursor() {
  const cursorRef = useRef(null)

  useEffect(() => {
    const cursor = cursorRef.current
    if (!cursor) return undefined

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
    const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)')
    if (reducedMotion.matches || !finePointer.matches) return undefined

    document.documentElement.classList.add('de-baseball-cursor-on')

    let frame = 0
    let mouseX = window.innerWidth / 2
    let mouseY = window.innerHeight / 2
    let currentX = mouseX
    let currentY = mouseY
    let lastX = currentX
    let lastY = currentY
    let roll = 0
    let targetScale = 1
    let scale = 1
    let visible = false

    const setVisible = (nextVisible) => {
      visible = nextVisible
      cursor.classList.toggle('is-visible', visible)
    }

    const onPointerMove = (event) => {
      mouseX = event.clientX
      mouseY = event.clientY
      targetScale = event.target?.closest?.(interactiveSelector) ? 1.18 : 1
      if (!visible) setVisible(true)
    }

    const onPointerDown = () => {
      cursor.classList.add('is-pressing')
    }

    const onPointerUp = () => {
      cursor.classList.remove('is-pressing')
    }

    const onPointerLeave = () => {
      setVisible(false)
    }

    const tick = () => {
      currentX += (mouseX - currentX) * 0.16
      currentY += (mouseY - currentY) * 0.16

      const dx = currentX - lastX
      const dy = currentY - lastY
      const speed = Math.hypot(dx, dy)
      const direction = Math.atan2(dy, dx) * (180 / Math.PI)
      const stretch = Math.min(speed * 0.006, 0.085)
      const tilt = Math.max(-11, Math.min(11, dy * 0.34))

      scale += (targetScale - scale) * 0.18
      roll += speed * 2.2

      cursor.style.setProperty('--cursor-x', `${currentX.toFixed(2)}px`)
      cursor.style.setProperty('--cursor-y', `${currentY.toFixed(2)}px`)
      cursor.style.setProperty('--cursor-roll', `${roll.toFixed(2)}deg`)
      cursor.style.setProperty('--cursor-direction', `${direction.toFixed(2)}deg`)
      cursor.style.setProperty('--cursor-direction-inverse', `${(-direction).toFixed(2)}deg`)
      cursor.style.setProperty('--cursor-scale', scale.toFixed(3))
      cursor.style.setProperty('--cursor-stretch-x', (1 + stretch).toFixed(3))
      cursor.style.setProperty('--cursor-stretch-y', (1 - stretch * 0.72).toFixed(3))
      cursor.style.setProperty('--cursor-tilt', `${tilt.toFixed(2)}deg`)

      lastX = currentX
      lastY = currentY
      frame = requestAnimationFrame(tick)
    }

    window.addEventListener('pointermove', onPointerMove, { passive: true })
    window.addEventListener('pointerdown', onPointerDown)
    window.addEventListener('pointerup', onPointerUp)
    document.documentElement.addEventListener('mouseleave', onPointerLeave)
    frame = requestAnimationFrame(tick)

    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerdown', onPointerDown)
      window.removeEventListener('pointerup', onPointerUp)
      document.documentElement.removeEventListener('mouseleave', onPointerLeave)
      document.documentElement.classList.remove('de-baseball-cursor-on')
    }
  }, [])

  return (
    <div ref={cursorRef} className="baseball-cursor" aria-hidden="true">
      <div className="baseball-cursor__shadow" />
      <div className="baseball-cursor__ball">
        <BaseballArtwork className="baseball-cursor__art" />
      </div>
    </div>
  )
}
