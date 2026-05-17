css = """

/* ============================================================
   UI v3 part 2 — Tier badges, compare, weight class, movement
   ============================================================ */

/* -- Experience tier border colours */
.exp-tier--elite    { border-color: rgba(245,158,11,0.45) !important; }
.exp-tier--veteran  { border-color: rgba(34,211,238,0.35) !important; }
.exp-tier--prospect { border-color: rgba(34,197,94,0.3)   !important; }
.exp-tier--newcomer { border-color: rgba(148,163,184,0.2)  !important; }

.exp-tier--elite:hover    { border-color: rgba(245,158,11,0.7)  !important; box-shadow: 0 28px 70px rgba(0,0,0,0.5), 0 0 44px rgba(245,158,11,0.22) !important; }
.exp-tier--veteran:hover  { border-color: rgba(34,211,238,0.55) !important; }
.exp-tier--prospect:hover { border-color: rgba(34,197,94,0.5)   !important; box-shadow: 0 28px 70px rgba(0,0,0,0.5), 0 0 44px rgba(34,197,94,0.18) !important; }

.exp-tier--elite::before    { border-top-color: rgba(245,158,11,0.7)  !important; }
.exp-tier--veteran::before  { border-top-color: rgba(34,211,238,0.6)  !important; }
.exp-tier--prospect::before { border-top-color: rgba(34,197,94,0.55)  !important; }
.exp-tier--newcomer::before { border-top-color: rgba(148,163,184,0.3) !important; }

/* -- Experience badge on photo */
.exp-badge {
  position: absolute;
  bottom: 7px;
  left: 7px;
  z-index: 4;
  font-family: var(--font-head);
  font-size: 0.58rem;
  font-weight: 800;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  padding: 0.2rem 0.45rem;
  border-radius: 4px;
  backdrop-filter: blur(8px);
  line-height: 1;
}
.exp-badge--elite    { background: rgba(245,158,11,0.9);  color: #1a0d00; }
.exp-badge--veteran  { background: rgba(34,211,238,0.85); color: #011018; }
.exp-badge--prospect { background: rgba(34,197,94,0.85);  color: #001a08; }
.exp-badge--newcomer { background: rgba(100,116,139,0.75);color: #e2e8f0; }

/* -- Weight class tag */
.fcard-tag--wc {
  color: var(--gold);
  border-color: rgba(245,158,11,0.28);
  background: rgba(245,158,11,0.08);
  font-weight: 600;
}

/* -- Weight class section group */
.wc-group { margin-top: 2rem; }

.wc-group__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.55rem 0.25rem 0.7rem;
  margin-bottom: 1rem;
  border-bottom: 1px solid rgba(255,255,255,0.08);
}

.wc-group__label {
  font-family: var(--font-head);
  font-size: clamp(1.2rem, 2.2vw, 1.55rem);
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text);
  padding-left: 0.9rem;
  position: relative;
}

.wc-group__label::before {
  content: "";
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 80%;
  background: linear-gradient(to bottom, var(--cyan), var(--red-bright));
  border-radius: 2px;
}

.wc-group__count {
  font-family: var(--font-head);
  font-size: 0.78rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text-2);
}

/* -- Fighter directory toolbar */
.fd-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
}

.fd-sort-pills { display: flex; gap: 0.4rem; flex-shrink: 0; }

.fd-sort-pill {
  padding: 0.38rem 0.85rem;
  border-radius: 999px;
  font-size: 0.76rem;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  border: 1px solid rgba(255,255,255,0.1);
  background: rgba(255,255,255,0.04);
  color: var(--text-2);
  text-decoration: none;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}

.fd-sort-pill.active,
.fd-sort-pill:hover {
  background: linear-gradient(135deg, rgba(34,211,238,0.15), rgba(239,68,68,0.1));
  border-color: rgba(34,211,238,0.35);
  color: var(--text);
}

/* -- Always 4 stat slots, no gap */
.fcard-stats {
  grid-template-columns: repeat(4, 1fr) !important;
}

/* -- Compare button */
.compare-btn {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 5;
  width: 30px;
  height: 30px;
  border-radius: 6px;
  border: 1px solid rgba(255,255,255,0.14);
  background: rgba(0,0,0,0.55);
  color: var(--text-2);
  font-size: 0.82rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  backdrop-filter: blur(8px);
  transition: background 0.15s, border-color 0.15s, color 0.15s, transform 0.15s;
  opacity: 0;
}

.fighter-card-v2:hover .compare-btn { opacity: 1; }

.compare-btn:hover {
  background: rgba(34,211,238,0.22);
  border-color: rgba(34,211,238,0.5);
  color: var(--cyan);
  transform: scale(1.1);
}

.compare-btn.cmp-sel {
  opacity: 1;
  background: rgba(34,211,238,0.28);
  border-color: var(--cyan);
  color: var(--cyan);
  box-shadow: 0 0 14px rgba(34,211,238,0.4);
}

.fighter-card-v2.cmp-sel {
  border-color: rgba(34,211,238,0.65) !important;
  box-shadow: 0 0 0 2px rgba(34,211,238,0.35), 0 20px 50px rgba(0,0,0,0.4) !important;
}

/* -- Compare banner */
#cmp-banner {
  position: fixed;
  bottom: 1.5rem;
  left: 50%;
  transform: translateX(-50%);
  z-index: 300;
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.75rem 1.2rem;
  background: rgba(4, 10, 20, 0.94);
  border: 1px solid rgba(34,211,238,0.42);
  border-radius: 999px;
  backdrop-filter: blur(20px);
  box-shadow: 0 8px 32px rgba(0,0,0,0.5), 0 0 40px rgba(34,211,238,0.15);
  font-size: 0.88rem;
  white-space: nowrap;
  color: var(--text);
}

#cmp-banner strong { color: var(--cyan); }

#cmp-banner button {
  padding: 0.3rem 0.75rem;
  border-radius: 999px;
  border: 1px solid rgba(255,255,255,0.15);
  background: rgba(255,255,255,0.07);
  color: var(--text-2);
  font-size: 0.78rem;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}

#cmp-banner button:hover {
  background: rgba(239,68,68,0.2);
  border-color: var(--red-bright);
  color: var(--text);
}

/* -- Odds Movement overhaul */
.movement-command-strip {
  border-radius: 12px !important;
  margin-top: 1.25rem;
  padding: 1.4rem 1.75rem !important;
}

.mcs-stats { display: flex; align-items: center; flex-wrap: wrap; gap: 0; }

.mcs-stat { text-align: center; padding: 0 2rem; }
.mcs-stat:first-child { padding-left: 0; }

.mcs-val {
  display: block;
  font-family: var(--font-head);
  font-size: 3rem !important;
  font-weight: 800;
  line-height: 1;
  letter-spacing: -0.02em;
}

.mcs-lbl {
  display: block;
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text-2);
  margin-top: 0.3rem;
}

.mcs-divider { width: 1px; height: 48px; background: rgba(255,255,255,0.1); }
.mcs-actions { margin-left: auto; display: flex; gap: 0.6rem; align-items: center; }

.fight-group { border-radius: 12px !important; overflow: hidden; }
.fight-group__summary { padding: 1rem 1.25rem !important; }
.fight-group__title strong { font-size: 1.15rem !important; letter-spacing: 0.05em; }

.mvrow { border-radius: 8px !important; transition: transform 0.15s, border-color 0.15s; }
.mvrow:hover { transform: translateX(4px); }
.mvrow--up   { border-color: rgba(34,197,94,0.25)  !important; background: rgba(34,197,94,0.04)   !important; }
.mvrow--down { border-color: rgba(239,68,68,0.25)   !important; background: rgba(239,68,68,0.04)   !important; }
.mvrow__arrow--up   { color: var(--green) !important; }
.mvrow__arrow--down { color: var(--red-bright) !important; }
.mvrow__signal { min-width: 90px; }
.mvstat strong { font-family: var(--font-head); font-size: 1.1rem; }

.page-header--movement {
  background:
    radial-gradient(ellipse at 0% 50%, rgba(34,197,94,0.15) 0%, transparent 55%),
    linear-gradient(to right, rgba(34,197,94,0.08), transparent 60%) !important;
  border-bottom-color: rgba(34,197,94,0.22) !important;
}

/* -- Responsive */
@media (max-width: 760px) {
  .fd-toolbar { flex-direction: column; align-items: stretch; }
  .fd-sort-pills { justify-content: center; }
  .fcard-stats { grid-template-columns: repeat(2, 1fr) !important; }
  #cmp-banner { width: calc(100% - 2rem); white-space: normal; border-radius: 12px; bottom: 1rem; }
  .mcs-stat { padding: 0 1rem; }
  .mcs-val { font-size: 2.2rem !important; }
  .mcs-divider { height: 36px; }
}
"""

import os
path = os.path.join(os.path.dirname(__file__), 'static', 'css', 'style-v3.css')
with open(path, 'a') as f:
    f.write(css)
print('done')
