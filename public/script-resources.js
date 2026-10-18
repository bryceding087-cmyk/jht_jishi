import { icon } from './iconography.js?v=icons-font-2';

export const RESOURCE_TYPES = {
  image: { label: '图片', icon: 'image', accept: '.jpg,.jpeg,.png,.webp', hint: 'JPG / PNG / WebP · 20 MB' },
  video: { label: '视频', icon: 'video', accept: '.mp4,.webm', hint: 'MP4 / WebM · 100 MB' },
  audio: { label: '音频', icon: 'audio', accept: '.mp3,.wav,.m4a,.ogg', hint: 'MP3 / WAV / M4A / OGG · 30 MB' },
  text: { label: '文本', icon: 'text', accept: '.txt', hint: 'UTF-8 TXT · 1 MB' },
  script: { label: '脚本', icon: 'script', accept: '.xlsx', hint: '分镜 Excel .xlsx · 5 MB' },
};

// Text and scripts are edited in the storyboard, not prepared as media files.
export const PREPARATION_TYPES = Object.fromEntries(
  ['image', 'video', 'audio'].map(kind => [kind, RESOURCE_TYPES[kind]])
);
export function preparationResources(data) {
  return (data.resources || []).filter(resource => PREPARATION_TYPES[resourceKind(resource)]);
}

// Keep legacy scripts usable before their next server-side normalization.
export function resourceKind(resource) {
  if (RESOURCE_TYPES[resource.kind]) return resource.kind;
  if (RESOURCE_TYPES[resource.type]) return resource.type;
  const category = String(resource.category || '').toLowerCase();
  if (/道具|prop|物件/.test(category)) return 'image';
  if (/声音|音频|旁白|配音|bgm|音乐|音效|audio|voice|sound|music/.test(category)) return 'audio';
  if (/视频|片段|video/.test(category)) return 'video';
  if (/脚本|剧本|script/.test(category)) return 'script';
  if (/文本|台词|文案|text/.test(category)) return 'text';
  return 'image';
}

export function acceptsResourceAsset(resource, asset) {
  const expected = resourceKind(resource), actual = asset?.kind || 'image';
  return Boolean(asset && (expected === 'image' ? ['image', 'prop'].includes(actual) : expected === actual));
}
