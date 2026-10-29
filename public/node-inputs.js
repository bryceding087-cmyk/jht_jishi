// Resolve from graph edges at read/submission time; never copy over the video's own draft.
import { resourceKind } from './script-resources.js?v=asset-library-2';
import { expandAssetMentions } from './asset-mentions.js?v=studio-references-2';

const isImageAsset = asset => ['image', 'prop'].includes(asset?.kind || 'image');
// The two editable fields in script step 3 are the prompt source of truth.
// Older model responses sometimes translated only the hidden final_prompt.
export function scriptScenePrompt(scene) {
  const parts = [scene?.storyboard_prompt, scene?.motion_prompt]
    .map(value => String(value ?? '').trim()).filter(Boolean);
  return parts.length ? parts.join('；') : String(scene?.final_prompt ?? '').trim() || String(scene?.prompt ?? '').trim();
}

export function resolveVideoInput(video, nodes, edges) {
  const sources = edges.filter(edge => edge.to === video?.id && edge.relation !== 'agent-flow')
    .map(edge => nodes.find(node => node.id === edge.from && ['prompt', 'script'].includes(node.type)))
    .filter((node, index, list) => node && list.findIndex(item => item?.id === node.id) === index);
  const source = sources.find(node => node.id === video?.settings?.prompt_source_id) || sources[0] || null;
  const scenes = source?.type === 'script' ? source.script_data?.scenes || [] : [];
  const scene = scenes.find(item => Number(item.index) === Number(video?.settings?.script_scene_index)) || scenes[0] || null;
  const settings = source?.settings || video?.settings || {};
  const prompt = source?.type === 'script' ? scriptScenePrompt(scene) : settings.prompt;
  return { source, sources, scene, scenes, prompt: expandAssetMentions(prompt,settings.mentions,nodes), negative_prompt: String(source?.type === 'script' ? video?.settings?.negative_prompt || '' : settings.negative_prompt ?? '') };
}

export function resolveVideoAssets(video, nodes, edges, assets, characters = []) {
  const entries = [], missing = [];
  const add = (assetId, source = null) => {
    const asset = assets.find(item => item.id === assetId&&!item.deleted_at);
    if (!asset) { missing.push({ assetId, source }); return; }
    if (!isImageAsset(asset) && !['audio','video'].includes(asset.kind)) { missing.push({ assetId, source, reason: '当前参考类型尚未接入' }); return; }
    let entry = entries.find(item => item.asset.id === assetId);
    if (!entry) { entry = { asset, sources: [], manual: false }; entries.push(entry); }
    if (source) {
      if (!entry.sources.some(node => node.id === source.id)) entry.sources.push(source);
    } else entry.manual = true;
  };
  for (const edge of edges.filter(item => item.to === video?.id && item.relation !== 'agent-flow')) {
    const source = nodes.find(node => node.id === edge.from);
    if (source?.type === 'script') {
      if (resolveVideoInput(video, nodes, edges).source?.id !== source.id) continue;
      const scenes = source.script_data?.scenes || [];
      const scene = scenes.find(item => Number(item.index) === Number(video?.settings?.script_scene_index)) || scenes[0];
      if (!scene) continue;
      const ids = scene.resource_ids || [];
      for (const resource of source.script_data?.resources || []) {
        const indices = resource.scene_indices || [];
        const matches = ids.includes(resource.id) || indices.some(index => Number(index) === Number(scene.index)) || (!ids.length && !indices.length);
        if (matches && ['image','audio','video'].includes(resourceKind(resource)) && resource.asset_id) add(resource.asset_id, source);
      }
      continue;
    }
    if (!source || !['asset', 'prop', 'character', 'audio', 'video-asset', 'video'].includes(source.type)) continue;
    const assetId = (source.type === 'video' ? source.referenceAssetId : source.assetId) || (source.type === 'character' ? characters.find(item => item.id === source.characterId)?.asset_id : '');
    add(assetId, source);
  }
  for (const id of video?.settings?.asset_ids || []) add(id);
  for(const owner of [video,resolveVideoInput(video,nodes,edges).source].filter(Boolean)) {
    const settings=owner.settings||{};
    for(const mention of settings.mentions||[])if(!['text','script'].includes(mention.kind)&&String(settings.prompt||'').includes(mention.token)){
      const source=nodes.find(n=>n.id===mention.node_id);
      const id=mention.node_id?referenceAssetId(source,characters):mention.asset_id;
      if(mention.node_id||id)add(id,source|| (owner===video?null:owner));
    }
  }
  // Keep user ordering only for references which still exist in the graph/current draft.
  const order = video?.settings?.reference_order || [];
  const rank = id => order.includes(id) ? order.indexOf(id) : order.length;
  entries.sort((a, b) => rank(a.asset.id) - rank(b.asset.id));
  return { entries, missing, assetIds: entries.map(item => item.asset.id) };
}

// Video mode is a consequence of the graph.  Keep this in the graph resolver so
// the inspector and task submission cannot drift apart when a reference node is
// added or removed.
export function deriveVideoMode(video, nodes, edges, assets, characters = []) {
  if(video?.settings?.video_mode==='first-last')return 'first-last';
  const count = resolveVideoAssets(video, nodes, edges, assets, characters).entries.filter(entry=>isImageAsset(entry.asset)).length;
  return count ? 'image-to-video' : 'text-to-video';
}

export function referenceAssetId(node, characters = []) {
  return (node?.type === 'video' ? node.referenceAssetId : node?.assetId) || (node?.type === 'character' ? characters.find(item => item.id === node.characterId)?.asset_id : '') || '';
}

// Turn legacy inspector-only references into graph inputs once, then clear the
// duplicate list so disconnected edges cannot silently come back on selection.
export function materializeVideoAssets(video, nodes, edges, assets, characters = [], makeId = () => crypto.randomUUID()) {
  const ids = [...new Set(video?.settings?.asset_ids || [])];
  if (video?.type !== 'video' || !ids.length) return false;
  for (const id of ids) {
    const linked = edges.some(edge => edge.to === video.id && edge.relation !== 'agent-flow' && nodes.some(node => node.id === edge.from && ['asset', 'prop', 'character', 'audio', 'video-asset', 'video'].includes(node.type) && referenceAssetId(node, characters) === id));
    if (linked) continue;
    let source = nodes.find(node => ['asset', 'prop','audio','video-asset'].includes(node.type) && node.assetId === id);
    if (!source) {
      const asset = assets.find(item => item.id === id);
      const x = (video.x || 0) - 340;
      let y = video.y || 0;
      while (nodes.some(node => Math.abs(node.x - x) < 300 && Math.abs(node.y - y) < 200)) y += 220;
      source = { id: makeId(), type: asset?.kind === 'video'?'video-asset':asset?.kind === 'audio'?'audio':asset?.kind === 'prop' ? 'prop' : 'asset', title: asset?.name || '失效的参考素材', assetId: id, assetUrl: asset?.url || '', assetName: asset?.name || '', x, y };
      nodes.push(source);
    }
    edges.push({ from: source.id, to: video.id });
  }
  video.settings.asset_ids = [];
  return true;
}
