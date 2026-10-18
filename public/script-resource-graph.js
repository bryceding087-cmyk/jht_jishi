import { resourceKind } from './script-resources.js';

const nodeTypes = { image: 'asset', video: 'video-asset', audio: 'audio' };
const mediaTypes = new Set(Object.values(nodeTypes));
mediaTypes.add('prop');
mediaTypes.add('character');
const isImageAsset = asset => ['image', 'prop'].includes(asset?.kind || 'image');

function matchesBinding(node, binding) {
  const compatible = node?.type === binding.type || (node?.type === 'character' && isImageAsset(binding.asset));
  return compatible && node.assetId === binding.asset.id;
}

function positionFor(script, nodes) {
  const originX = (Number(script.x) || 0) - 380, originY = Number(script.y) || 0;
  let x = originX, y = originY, slot = 0;
  // Media cards are about 280 x 250; leave room for ports and their labels.
  // Explicit dimensions also support older projects with resized nodes.
  const overlaps = node => {
    const left = Number(node.x) || 0, top = Number(node.y) || 0;
    const width = Math.max(280, Number(node.width) || 0);
    const height = Math.max(260, Number(node.height) || 0);
    return x < left + width + 32 && x + 312 > left && y < top + height + 32 && y + 292 > top;
  };
  // Four rows per column keep large resource lists near their script instead
  // of pushing them into one extremely tall column. Existing nodes keep place.
  while (nodes.some(overlaps)) {
    slot++;
    x = originX - Math.floor(slot / 4) * 380;
    y = originY + (slot % 4) * 300;
  }
  return { x, y };
}

/** Project prepared script resources into canvas inputs, preserving manual work. */
export function syncScriptResourceGraph(nodes, edges, assets, {
  makeId = () => crypto.randomUUID(), maxNodes = 200, maxEdges = 400,
} = {}) {
  const result = { changed: false, createdNodeIds: [], addedEdges: [], removedEdges: [], skippedBindings: [] };
  const available = new Map(assets.filter(asset => asset && !asset.deleted_at).map(asset => [asset.id, asset]));
  const desired = new Map();
  for (const script of nodes.filter(node => node.type === 'script')) {
    const bindings = new Map();
    for (const resource of script.script_data?.resources || []) {
      const kind = resourceKind(resource), asset = available.get(resource.asset_id || resource.assetId);
      if (!nodeTypes[kind] || !asset || (kind === 'image' ? !isImageAsset(asset) : (asset.kind || 'image') !== kind)) continue;
      let binding = bindings.get(asset.id);
      if (!binding) {
        binding = { asset, type: asset.kind === 'prop' ? 'prop' : nodeTypes[kind], resourceIds: [] };
        bindings.set(asset.id, binding);
      }
      if (resource.id != null && !binding.resourceIds.includes(resource.id)) binding.resourceIds.push(resource.id);
    }
    desired.set(script.id, { script, bindings });
  }
  const removeEdge = edge => {
    const index = edges.indexOf(edge);
    if (index < 0) return;
    edges.splice(index, 1);
    result.removedEdges.push(edge);
    result.changed = true;
  };
  // Remove only edges created by this projection. Unbound media nodes remain
  // available on the canvas, along with every unrelated/manual connection.
  for (const edge of [...edges]) {
    if (!edge.scriptResourceManaged) continue;
    const source = nodes.find(node => node.id === edge.from);
    const binding = desired.get(edge.to)?.bindings.get(source?.assetId);
    if (!binding || !matchesBinding(source, binding)) removeEdge(edge);
  }
  for (const { script, bindings } of desired.values()) {
    for (const binding of bindings.values()) {
      const linked = edges.filter(edge => edge.to === script.id && edge.relation !== 'agent-flow' && matchesBinding(nodes.find(node => node.id === edge.from), binding));
      const existingEdge = linked.find(edge => !edge.scriptResourceManaged) || linked[0];
      // Reuse an existing manual input without claiming ownership of that edge.
      // Duplicate edges maintained by us may safely be removed.
      for (const edge of linked) if (edge !== existingEdge && edge.scriptResourceManaged) removeEdge(edge);
      if (existingEdge) {
        if (existingEdge.scriptResourceManaged && JSON.stringify(existingEdge.scriptResourceIds) !== JSON.stringify(binding.resourceIds)) {
          existingEdge.scriptResourceIds = [...binding.resourceIds];
          result.changed = true;
        }
        continue;
      }
      if (edges.length >= maxEdges) {
        result.skippedBindings.push({ scriptId: script.id, assetId: binding.asset.id, reason: 'edge-limit' });
        continue;
      }
      let source = nodes.find(node => matchesBinding(node, binding));
      if (!source) {
        if (nodes.length >= maxNodes) {
          result.skippedBindings.push({ scriptId: script.id, assetId: binding.asset.id, reason: 'node-limit' });
          continue;
        }
        source = {
          id: makeId(), type: binding.type, title: binding.asset.name || '分镜参考素材',
          assetId: binding.asset.id, assetName: binding.asset.name || '', assetUrl: binding.asset.url || '',
          scriptResourceManaged: true, ...positionFor(script, nodes),
        };
        nodes.push(source);
        result.createdNodeIds.push(source.id);
      }
      const edge = { from: source.id, to: script.id, scriptResourceManaged: true, scriptResourceIds: [...binding.resourceIds] };
      edges.push(edge);
      result.addedEdges.push(edge);
      result.changed = true;
    }
  }
  return result;
}

/** Unbind resources when their last canvas input is deliberately removed. */
export function detachScriptResourceEdges(nodes, removedEdges, remainingEdges = []) {
  const snapshots = [];
  for (const edge of removedEdges) {
    if (edge.relation === 'agent-flow') continue;
    const source = nodes.find(node => node.id === edge.from);
    const script = nodes.find(node => node.id === edge.to && node.type === 'script');
    if (!source?.assetId || !mediaTypes.has(source.type) || !script) continue;
    const retained = remainingEdges.some(other => other.to === script.id && nodes.some(node =>
      node.id === other.from && mediaTypes.has(node.type) && node.assetId === source.assetId));
    if (retained) continue;
    (script.script_data?.resources || []).forEach((resource, resourceIndex) => {
      if ((resource.asset_id || resource.assetId) !== source.assetId) return;
      snapshots.push({ scriptId: script.id, resourceId: resource.id, resourceIndex,
        previous: { asset_id: resource.asset_id, ...(Object.hasOwn(resource, 'assetId') ? { assetId: resource.assetId } : {}), file_name: resource.file_name, status: resource.status } });
      delete resource.assetId;
      Object.assign(resource, { asset_id: '', file_name: '', status: 'pending' });
    });
  }
  return snapshots;
}
