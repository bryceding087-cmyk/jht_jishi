// One chronological history for graph edits. Viewport and task progress are not
// part of an edit, and undo patches only the fields that operation changed.
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const copy = value => value === undefined ? undefined : structuredClone(value);
const ignoredNodeFields = new Set(['mediaWidth', 'mediaHeight']);

function changes(before, after, ignored = new Set()) {
  const old = new Map(before.map(item => [item.id, item]));
  const next = new Map(after.map(item => [item.id, item]));
  return [...new Set([...old.keys(), ...next.keys()])].flatMap(id => {
    const a = old.get(id), b = next.get(id);
    if (!a || !b) return [{ id, before: a, after: b }];
    const fields = [...new Set([...Object.keys(a), ...Object.keys(b)])]
      .filter(key => !ignored.has(key) && !equal(a[key], b[key]));
    return fields.length ? [{ id, fields, before: a, after: b }] : [];
  });
}

function apply(items, edits, direction, order) {
  const result = new Map(items.map(item => [item.id, item]));
  for (const edit of edits) {
    const saved = edit[direction];
    if (!edit.fields) {
      if (saved) result.set(edit.id, copy(saved)); else result.delete(edit.id);
    } else {
      const current = result.get(edit.id);
      if (!current) continue;
      for (const field of edit.fields) {
        if (Object.hasOwn(saved, field)) current[field] = copy(saved[field]);
        else delete current[field];
      }
    }
  }
  const rank = new Map(order.map((item, index) => [item.id, index]));
  return [...result.values()].sort((a, b) => (rank.get(a.id) ?? Infinity) - (rank.get(b.id) ?? Infinity));
}

export function createCanvasHistory({ state, onRestore, limit = 80 }) {
  const undo = [], redo = [];
  let baseline, projectId, queued = false, restoring = false;
  const snapshot = () => copy({ nodes: state.nodes, edges: state.edges, groups: state.groups,
    selection: [...state.selectedNodeIds], selectedNodeId: state.selectedNodeId, selectedGroupId: state.selectedGroupId });
  function reset() {
    queued = false;
    projectId = state.project?.id;
    undo.length = redo.length = 0;
    baseline = snapshot();
  }
  function commit() {
    queued = false;
    if (restoring || state.drag || state.switchingProject) return;
    if (projectId !== state.project?.id || !baseline) return reset();
    const after = snapshot();
    const nodes = changes(baseline.nodes, after.nodes, ignoredNodeFields);
    const groups = changes(baseline.groups, after.groups);
    if (nodes.length || groups.length || !equal(baseline.edges, after.edges)) {
      undo.push({ before: baseline, after, nodes, groups });
      if (undo.length > limit) undo.shift();
      redo.length = 0;
    }
    baseline = after;
  }
  function record() {
    if (restoring || queued) return;
    queued = true;
    // Several saves in the same operation (node + edge + settings) are one step.
    queueMicrotask(() => { if (queued) commit(); });
  }
  function restore(forward) {
    commit();
    const source = forward ? redo : undo, target = forward ? undo : redo;
    if (!source.length || state.drag || state.switchingProject) return false;
    const edit = source.pop(), direction = forward ? 'after' : 'before', saved = edit[direction];
    restoring = true;
    try {
      state.nodes = apply(state.nodes, edit.nodes, direction, saved.nodes);
      state.groups = apply(state.groups, edit.groups, direction, saved.groups);
      if (!equal(edit.before.edges, edit.after.edges)) state.edges = copy(saved.edges);
      const ids = new Set(state.nodes.map(node => node.id));
      state.edges = state.edges.filter(edge => ids.has(edge.from) && ids.has(edge.to));
      state.groups = state.groups.map(group => ({ ...group, node_ids: group.node_ids.filter(id => ids.has(id)) })).filter(group => group.node_ids.length);
      state.selectedNodeIds = new Set(saved.selection.filter(id => ids.has(id)));
      state.selectedNodeId = ids.has(saved.selectedNodeId) ? saved.selectedNodeId : [...state.selectedNodeIds][0] || null;
      state.selectedGroupId = state.groups.some(group => group.id === saved.selectedGroupId) ? saved.selectedGroupId : null;
      state.selectedEdgeIndex = null;
      state.pendingPort = null;
      target.push(edit);
      onRestore();
    } finally {
      baseline = snapshot();
      restoring = false;
    }
    return true;
  }
  reset();
  return { record, commit, reset, undo: () => restore(false), redo: () => restore(true) };
}
