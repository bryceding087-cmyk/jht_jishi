const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

export function portAttraction(rect, point, zoom = 1) {
  return { x: clamp((point.x - rect.left - rect.width / 2) / zoom * .6, -10, 10),
    y: clamp((point.y - rect.top - rect.height / 2) / zoom * .75, -44, 44) };
}

export function connectionTargetGeometry(rect, point) {
  const x = clamp(point.x, rect.left, rect.right), y = clamp(point.y, rect.top, rect.bottom);
  const sides = [{ x: rect.left, y }, { x: rect.right, y }, { x, y: rect.top }, { x, y: rect.bottom }];
  const anchor = sides.reduce((best, side) => Math.hypot(side.x-point.x, side.y-point.y) < Math.hypot(best.x-point.x, best.y-point.y) ? side : best);
  const nx = clamp((point.x-rect.left) / Math.max(1,rect.width) * 2-1, -1, 1);
  const ny = clamp((point.y-rect.top) / Math.max(1,rect.height) * 2-1, -1, 1);
  return { anchor, rotateX: -ny*3.5, rotateY: nx*5, rotateZ: ny*1.2 };
}

// Pointer feedback never writes node coordinates or saved edge geometry. The
// stable port hit area stays still while only the circular mark follows motion.
export function installCanvasConnections({ canvas, state, canConnect, connect, renderEdges, highlights, onError }) {
  const doc = canvas.ownerDocument, win = doc.defaultView;
  const reducedMotion = win.matchMedia('(prefers-reduced-motion: reduce)');
  const chrome = '.workspace-chrome,.inspector,.canvas-panel,.canvas-dock';
  let gesture = null, target = null, magnet = null, frame = 0, latest = null, submitting = false;
  const active = () => state.view === 'canvas' && state.canvasTool !== 'hand' && !state.spaceDown;
  const stop = event => { event.preventDefault(); event.stopImmediatePropagation(); };
  const world = point => {
    const rect = canvas.getBoundingClientRect();
    return { x: (point.x-rect.left-state.panX)/state.zoom, y: (point.y-rect.top-state.panY)/state.zoom };
  };
  function clearMagnet() {
    if (magnet) { magnet.style.removeProperty('--port-x'); magnet.style.removeProperty('--port-y'); }
    magnet = null;
  }
  function clearTarget() {
    if (target) {
      target.classList.remove('connection-target');
      for (const name of ['--connection-rx','--connection-ry','--connection-rz']) target.style.removeProperty(name);
    }
    target = null;
  }
  function resetFeedback() {
    clearMagnet(); clearTarget();
    canvas.classList.remove('wire-connecting');
  }
  function hitTarget(point) {
    const hit = doc.elementFromPoint(point.x,point.y);
    if (!hit || !canvas.contains(hit) || hit.closest(chrome)) return null;
    const card = hit.closest('.node');
    return card && card.querySelector('[data-port="in"]') && canConnect(card.dataset.node) ? card : null;
  }
  function paint() {
    frame = 0;
    if (!latest || !active() || state.drag || submitting) return resetFeedback();
    const point = { x: latest.clientX, y: latest.clientY };
    if (state.pendingPort) {
      clearMagnet(); canvas.classList.add('wire-connecting');
      const next = hitTarget(point);
      if (target !== next) { clearTarget(); target = next; }
      let anchor = point;
      if (target) {
        const bounds = (target.querySelector('.frame-stage') || target).getBoundingClientRect();
        const geometry = connectionTargetGeometry(bounds,point);
        target.classList.add('connection-target');
        target.style.setProperty('--connection-rx', `${reducedMotion.matches?0:geometry.rotateX}deg`);
        target.style.setProperty('--connection-ry', `${reducedMotion.matches?0:geometry.rotateY}deg`);
        target.style.setProperty('--connection-rz', `${reducedMotion.matches?0:geometry.rotateZ}deg`);
        anchor = geometry.anchor;
      }
      state.wireCursor = world(anchor);
      renderEdges();
      return;
    }
    clearTarget(); canvas.classList.remove('wire-connecting');
    const port = latest.target.closest?.('[data-node-port]');
    if (port !== magnet) { clearMagnet(); magnet = port; }
    if (magnet && !reducedMotion.matches && latest.pointerType !== 'touch') {
      const delta = portAttraction(magnet.getBoundingClientRect(),point,state.zoom);
      magnet.style.setProperty('--port-x',`${delta.x}px`);
      magnet.style.setProperty('--port-y',`${delta.y}px`);
    }
  }
  function queue(event) { latest = event; if (!frame) frame = win.requestAnimationFrame(paint); }
  function release() {
    const previous = gesture; gesture = null;
    if (previous && canvas.hasPointerCapture(previous.pointerId)) canvas.releasePointerCapture(previous.pointerId);
  }
  function cancel() {
    release(); resetFeedback(); latest = null;
    if (frame) { win.cancelAnimationFrame(frame); frame = 0; }
    if (state.pendingPort) { state.pendingPort = null; state.wireCursor = null; highlights(); }
  }
  async function complete(card) {
    const from = state.pendingPort, to = card.dataset.node;
    submitting = true; clearTarget();
    try {
      await connect(to,'in');
      if (!state.pendingPort && card.isConnected && state.edges.some(edge=>edge.from===from && edge.to===to && edge.relation!=='agent-flow')) {
        card.classList.remove('connection-landed');
        // Restart a brief landing animation even when reconnecting quickly.
        void card.offsetWidth;
        card.classList.add('connection-landed');
        win.setTimeout(()=>card.classList.remove('connection-landed'),500);
      }
    } catch(error) { onError(error.message); }
    finally { submitting=false; if (!state.pendingPort) resetFeedback(); }
  }
  canvas.addEventListener('pointerdown',event=>{
    if (!active() || state.drag || event.button!==0 || event.isPrimary===false) return;
    if (submitting) { if (event.target.closest('[data-node-port]')) stop(event); return; }
    const port = event.target.closest('[data-port="out"]');
    if (!port) {
      if (state.pendingPort && !event.target.closest('[data-node-port]') && hitTarget({x:event.clientX,y:event.clientY})) stop(event);
      return;
    }
    state.suppressClickUntil=0;
    gesture = { pointerId:event.pointerId, x:event.clientX, y:event.clientY, source:port.dataset.nodePort, moved:false };
    // Leave a stationary click to the existing keyboard/click connection flow.
    stop(event);
  },true);
  canvas.addEventListener('pointermove',event=>{
    if (gesture && event.pointerId!==gesture.pointerId) return;
    if (gesture && (!active() || !state.nodes.some(node=>node.id===gesture.source))) { cancel(); return; }
    if (gesture && !gesture.moved && Math.hypot(event.clientX-gesture.x,event.clientY-gesture.y)>=4) {
      gesture.moved=true;
      state.pendingPort=gesture.source;
      canvas.setPointerCapture(event.pointerId);
      highlights();
    }
    if (gesture?.moved || state.pendingPort && active() && !state.drag) stop(event);
    queue(event);
  },true);
  canvas.addEventListener('pointerup',event=>{
    if (!gesture || event.pointerId!==gesture.pointerId) return;
    const moved=gesture.moved;
    if (moved) {
      stop(event);
      state.suppressClickUntil=win.performance.now()+250;
      const card=hitTarget({x:event.clientX,y:event.clientY});
      release();
      if (card) complete(card); else cancel();
    } else release();
  },true);
  canvas.addEventListener('click',event=>{
    if (!active() || state.drag || !state.pendingPort || event.target.closest(chrome)) return;
    if (win.performance.now()<(state.suppressClickUntil||0)) return;
    if (submitting) { stop(event); return; }
    if (event.target.closest('[data-node-port]')) return;
    const card=event.target.closest('.node');
    if (card && card.querySelector('[data-port="in"]') && canConnect(card.dataset.node)) { stop(event); complete(card); }
  },true);
  canvas.addEventListener('pointerout',event=>{ if (magnet && !magnet.contains(event.relatedTarget)) clearMagnet(); });
  canvas.addEventListener('pointerleave',()=>{
    clearMagnet();
    if (!gesture) {
      latest=null;
      if (frame) { win.cancelAnimationFrame(frame); frame=0; }
      clearTarget(); state.wireCursor=null; renderEdges();
    }
  });
  canvas.addEventListener('pointercancel',cancel,true);
  canvas.addEventListener('lostpointercapture',()=>{ if (gesture) cancel(); },true);
  win.addEventListener('pointerup',event=>{ if (gesture && event.pointerId===gesture.pointerId && !canvas.contains(event.target)) cancel(); });
  win.addEventListener('keydown',event=>{ if (event.key==='Escape') cancel(); });
  win.addEventListener('blur',cancel);
  doc.addEventListener('visibilitychange',()=>{ if (doc.hidden) cancel(); });
  reducedMotion.addEventListener('change',()=>{ resetFeedback(); if (latest) queue(latest); });
  return { reset:cancel, sync() { if (!state.pendingPort) resetFeedback(); } };
}
