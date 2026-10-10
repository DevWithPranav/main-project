// Video <-> session time. The session video (GET /api/sessions/{id}/video) carries frame_t: the
// session time (sim seconds) of each frame, written at a constant `fps`, so frame i plays at i / fps.

/** Session time shown at video time v. */
export function videoToSession(v: number, frameT: number[], fps: number): number {
  if (!frameT.length) return v;
  const i = Math.min(frameT.length - 1, Math.max(0, Math.round(v * fps)));
  return frameT[i];
}

/** Video time of the last frame at or before session time t (clamped to the video). */
export function sessionToVideo(t: number, frameT: number[], fps: number): number {
  if (!frameT.length) return t;
  let lo = 0;
  let hi = frameT.length - 1;
  if (t <= frameT[0]) return 0;
  if (t >= frameT[hi]) return hi / fps;
  while (hi - lo > 1) {
    const m = (lo + hi) >> 1;
    if (frameT[m] <= t) lo = m;
    else hi = m;
  }
  return lo / fps;
}

/** Trail of a track over [t - span, t] from trajectory samples [t, x, y, v]. */
export function trailOf(samples: [number, number, number, number][], t: number, span = 3): [number, number][] {
  const out: [number, number][] = [];
  for (const s of samples) {
    if (s[0] > t) break;
    if (s[0] >= t - span) out.push([s[1], s[2]]);
  }
  return out;
}
