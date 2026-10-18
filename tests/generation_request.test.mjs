import assert from 'node:assert/strict';
import { buildRegenerationRequest } from '../public/generation-request.js';

const metadata = { model: 'provider-model-name', model_config_id: 'saved-model', size: '2K', count: 2, aspect_ratio: '16:9', watermark: true,
  reference_asset_ids: ['image-a'], asset_ids: ['video-a'], mode: 'voice', speaker: 'speaker-a', audio_config: { format: 'wav', speech_rate: 20 },
  negative_prompt: '低清晰度', duration: 5, resolution: '720p', id: 'old-task', status: 'done', created_at: 123, error: '', asset: { id: 'out' },
  prompt: '旧内容', project_id: 'old-project', request_id: 'old-request' };
const base = { prompt: '原来的完整描述\n第二行', conversationId: 'conversation-a', settings: metadata };
const original = structuredClone(metadata);
const image = buildRegenerationRequest({ ...base, kind: 'image' }, 'new-image');
assert.deepEqual(image, { model_config_id: 'saved-model', size: '2K', count: 2, aspect_ratio: '16:9', prompt: base.prompt,
  project_id: 'conversation-a', request_id: 'new-image', name: base.prompt, reference_asset_ids: ['image-a'], watermark: false });
const audio = buildRegenerationRequest({ ...base, kind: 'audio' }, 'new-audio');
assert.equal('model' in audio, false); assert.equal('size' in audio, false); assert.equal('asset_ids' in audio, false);
assert.deepEqual(audio.audio_config, metadata.audio_config); assert.equal(audio.speaker, 'speaker-a');
const video = buildRegenerationRequest({ ...base, kind: 'video', settings: { ...metadata, mode: 'image-to-video' } }, 'new-video');
assert.equal(video.model, metadata.model); assert.equal(video.duration, 5); assert.equal(video.resolution, '720p');
assert.equal(video.node_id, 'conversation-video'); assert.deepEqual(video.asset_ids, ['video-a']);
assert.equal('reference_asset_ids' in video, false); assert.equal('size' in video, false);
assert.deepEqual(metadata, original, 'regeneration does not mutate saved history');
const fallback = buildRegenerationRequest({ kind: 'image', task: { generation_settings: metadata }, prompt: '历史记录', conversationId: 'c' }, 'r');
assert.equal(fallback.model_config_id, 'saved-model'); assert.equal('model' in fallback, false);
const explicitEmpty = buildRegenerationRequest({ ...base, kind: 'image', settings: { reference_asset_ids: [] }, references: [{ id: 'stale' }] }, 'r');
assert.deepEqual(explicitEmpty.reference_asset_ids, []);
assert.notEqual(image.request_id, metadata.request_id);
for(const kind of ['image','video']){
  const visual_style={id:'custom',custom_prompt:'纸雕材质'};
  const saved={...metadata,visual_style,effective_prompt:'已经拼接的提示词',style_snapshot:{name:'自定义风格'}};
  const styled=buildRegenerationRequest({...base,kind,settings:saved},'styled');
  assert.deepEqual(styled.visual_style,visual_style);
  assert.notEqual(styled.visual_style,visual_style,'style selection is a snapshot');
  assert.equal(styled.prompt,base.prompt,'regeneration uses original content, avoiding double style composition');
  assert.equal('effective_prompt' in styled,false);
  assert.equal('style_snapshot' in styled,false);
  assert.equal(buildRegenerationRequest({...base,kind,settings:{visual_style:null}},'clear').visual_style,null);
}
assert.equal('visual_style' in buildRegenerationRequest({...base,kind:'audio',settings:{visual_style:{id:'feature-animation'}}},'audio'),false);
console.log('PASS: image/audio/video request whitelists, display metadata filtered, original settings/prompt/references preserved, new request ID, empty references and legacy records.');
