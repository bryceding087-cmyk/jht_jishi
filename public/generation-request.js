// History records contain display/provider metadata as well as request options.
// Reconstruct each API contract instead of forwarding the entire saved record.
const fields = {
  image: ['model_config_id', 'folder_id', 'size', 'aspect_ratio', 'count', 'asset_kind', 'visual_style'],
  audio: ['model_config_id', 'folder_id', 'mode', 'speaker', 'audio_config', 'watermark'],
  video: ['model_config_id', 'model', 'negative_prompt', 'mode', 'aspect_ratio', 'duration', 'resolution', 'visual_style'],
};

export function buildRegenerationRequest(job, requestId) {
  if (!fields[job.kind]) throw new Error('不支持的生成类型');
  const settings = job.settings || job.task?.generation_settings || job.task?.params || job.task || {};
  const body = structuredClone(Object.fromEntries(fields[job.kind]
    .filter(key => settings[key] !== undefined).map(key => [key, settings[key]])));
  Object.assign(body, { prompt: job.prompt, project_id: job.conversationId, request_id: requestId });
  const references = (job.references || []).map(asset => asset.id);
  if (job.kind === 'video') {
    body.node_id = 'conversation-video';
    body.asset_ids = [...(settings.asset_ids ?? settings.reference_asset_ids ?? references)];
  } else {
    body.name = job.prompt.slice(0, 60);
    body.reference_asset_ids = [...(settings.reference_asset_ids ?? settings.asset_ids ?? references)];
    if (job.kind === 'image') body.watermark = false;
  }
  return body;
}
