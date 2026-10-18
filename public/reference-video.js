// Validate reference videos before upload or insertion into a prompt.
const durations = new Map();
export async function validateReferenceVideo(source) {
  const file = source instanceof File;
  if (!(file ? source.type.startsWith('video/') || /\.(mp4|webm|mov|m4v)$/i.test(source.name) : source.kind === 'video')) return;
  const url = file ? URL.createObjectURL(source) : source.url;
  try {
    if (!url) throw new Error('无法读取参考视频');
    if (!durations.has(url)) durations.set(url, new Promise((resolve, reject) => {
      const video = document.createElement('video'); video.preload = 'metadata';
      const finish = (error, value) => { clearTimeout(timer); video.onloadedmetadata = video.onerror = null; video.removeAttribute('src'); video.load(); error ? reject(error) : resolve(value); };
      const timer = setTimeout(() => finish(new Error('读取视频时长超时，请重试')), 15000);
      video.onloadedmetadata = () => Number.isFinite(video.duration) && video.duration > 0 ? finish(null, video.duration) : finish(new Error('无法读取视频时长，请使用有效的视频文件'));
      video.onerror = () => finish(new Error('无法读取视频，请检查视频格式'));
      video.src = url;
    }));
    const duration = await durations.get(url);
    if (duration > 15) throw new Error('参考视频不能超过 15 秒，请裁剪后重新上传');
  } catch (error) { durations.delete(url); throw error; }
  finally { if (file) { durations.delete(url); URL.revokeObjectURL(url); } }
}

export function videoReferenceCover(asset, esc) {
  const url = String(asset.url || '').split('#')[0];
  return `<video class="reference-video-cover" src="${esc(url)}#t=0.05" muted playsinline preload="auto" aria-label="${esc(asset.name || '视频封面')}"></video>`;
}
