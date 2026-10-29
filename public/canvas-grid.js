export const CANVAS_GRID_SIZE = 24;

// Snap one anchor, then apply the same world-space delta to every member.
export function gridDragDelta(anchor, dx, dy, enabled) {
  if (!enabled) return { dx, dy };
  return {
    dx: Math.round((anchor.x + dx) / CANVAS_GRID_SIZE) * CANVAS_GRID_SIZE - anchor.x,
    dy: Math.round((anchor.y + dy) / CANVAS_GRID_SIZE) * CANVAS_GRID_SIZE - anchor.y,
  };
}

export function updateCanvasGrid(grid, { zoom, panX, panY }) {
  if (!grid) return;
  const step = CANVAS_GRID_SIZE * zoom;
  grid.style.backgroundSize = `${step}px ${step}px`;
  grid.style.backgroundPosition = `${panX - step / 2}px ${panY - step / 2}px`;
}
