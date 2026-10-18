import { icon } from './iconography.js?v=frame-ratio-1';

const mediaKinds = {
  image: { name: '图片', icon: 'image', english: 'IMAGE' },
  prop: { name: '道具', icon: 'prop', english: 'PROP' },
  video: { name: '视频', icon: 'video', english: 'VIDEO' },
  audio: { name: '音频', icon: 'audio', english: 'AUDIO' },
};

function mediaKind(node, asset) {
  if (asset && mediaKinds[asset.kind]) return asset.kind;
  return node.type === 'video-asset' ? 'video' : node.type === 'audio' ? 'audio' : node.type === 'prop' ? 'prop' : 'image';
}

function mediaUrl(node, asset) {
  const value = String(asset?.url || node.assetUrl || '');
  return /^(https?:\/\/|\/(?!\/)|blob:)/i.test(value) ? value : '';
}

export function formatMediaSize(bytes) {
  const size = Number(bytes);
  if (!Number.isFinite(size) || size <= 0) return '大小未知';
  if (size < 1024) return `${Math.round(size)} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function mediaPreview(node, asset, escapeHtml, detail = false) {
  const kind = mediaKind(node, asset);
  const url = mediaUrl(node, asset);
  const name = asset?.name || node.assetName || `${mediaKinds[kind].name}素材`;
  if (!url) return '';
  if (kind === 'image' || kind === 'prop') return `<img class="media-node-image" src="${escapeHtml(url)}" alt="${escapeHtml(name)}" loading="lazy" draggable="false">`;
  if (kind === 'video') return `<video ${detail ? 'id="mediaDetailVideo"' : ''} class="media-node-video media-node-interactive" src="${escapeHtml(url)}" controls playsinline preload="${detail ? 'metadata' : 'none'}" aria-label="${escapeHtml(name)}"></video>`;
  return `<div class="media-node-audio-art" aria-hidden="true"><span>${icon('audio')}</span><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div><audio class="media-node-audio media-node-interactive" src="${escapeHtml(url)}" controls preload="${detail ? 'metadata' : 'none'}" aria-label="${escapeHtml(name)}"></audio>`;
}

export function mediaNodeBody(node, asset, escapeHtml) {
  const kind = mediaKind(node, asset);
  const info = mediaKinds[kind];
  const url = mediaUrl(node, asset);
  if (kind === 'audio') {
    const id = escapeHtml(node.id);
    if (!url) return `<div class="audio-node-empty"><button type="button" class="audio-node-upload" data-media-upload="${id}"><span class="audio-node-symbol" aria-hidden="true">${icon('audio')}</span><span>${icon('upload','ui-icon-sm')}上传</span></button><div class="audio-node-try"><span>尝试:</span><button type="button" data-audio-generate="${id}" data-audio-mode="sfx">${icon('audio','ui-icon-sm')}文生音效</button><button type="button" data-audio-generate="${id}" data-audio-mode="voice">${icon('microphone','ui-icon-sm')}生成配音</button><button type="button" data-audio-generate="${id}" data-audio-mode="music">${icon('music','ui-icon-sm')}生成背景音乐</button></div></div>`;
    return `<div class="audio-node-filled">${mediaPreview(node, asset, escapeHtml)}<div class="media-node-caption"><span title="${escapeHtml(asset?.name || node.assetName || '')}">${escapeHtml(asset?.name || node.assetName || '音频')}</span><small>${asset?.duration ? Number(asset.duration).toFixed(1) + ' 秒 · ' : ''}${formatMediaSize(asset?.size_bytes)}</small></div><div class="audio-node-actions"><button type="button" data-audio-generate="${id}" data-audio-mode="${escapeHtml(node.audio_settings?.mode || 'sfx')}">${icon('sparkle','ui-icon-sm')}生成音频</button><button type="button" data-media-pick="${id}">更换素材</button></div></div>`;
  }
  if (!url) {
    const image = kind === 'image' || kind === 'prop';
    const placeholder = image
      ? '<svg class="frame-placeholder" viewBox="0 0 64 48" aria-hidden="true"><path d="M4 40 18 22l10 12 8-9 20 15H4Z"/><circle cx="46" cy="12" r="4"/></svg>'
      : `<span class="frame-placeholder frame-placeholder-video">${icon('video')}</span>`;
    const actions = image
      ? `<button type="button" data-media-upload="${escapeHtml(node.id)}">${icon('upload','ui-icon-sm')}\u56fe\u751f\u56fe</button><button type="button" data-media-generate="${escapeHtml(node.id)}">${icon('sparkle','ui-icon-sm')}AI \u751f\u56fe</button>`
      : `<button type="button" data-media-upload="${escapeHtml(node.id)}">${icon('upload','ui-icon-sm')}\u4e0a\u4f20\u89c6\u9891</button><button type="button" data-media-pick="${escapeHtml(node.id)}">${icon('image','ui-icon-sm')}\u4ece\u7d20\u6750\u5e93\u9009\u62e9</button>`;
    return `<div class="frame-empty">${placeholder}<div class="frame-try"><span>\u5c1d\u8bd5\uff1a</span>${actions}</div></div>`;
  }
  const name = escapeHtml(asset?.name || node.assetName || `${info.name}\u7d20\u6750`);
  if (kind === 'image' || kind === 'prop') return `<img class="frame-media" src="${escapeHtml(url)}" alt="${name}" draggable="false">`;
  return `<video class="frame-media media-node-interactive" src="${escapeHtml(url)}" controls playsinline preload="metadata" aria-label="${name}"></video>`;
}

export function mediaNodeDetail(node, asset, escapeHtml) {
  const kind = mediaKind(node, asset);
  const info = mediaKinds[kind];
  const url = mediaUrl(node, asset);
  const name = asset?.name || node.assetName || '';
  const preview = url ? `<div class="media-detail-preview">${mediaPreview(node, asset, escapeHtml, true)}</div>` : `<div class="media-detail-empty"><span aria-hidden="true">${icon(info.icon)}</span><p>还没有绑定${info.name}文件</p></div>`;
  const videoTools = kind === 'video' && url ? `<div class="media-video-tools media-node-interactive"><div class="media-video-step"><button type="button" id="mediaFramePrevious" title="向前步进 0.04 秒" aria-label="向前步进 0.04 秒">− 0.04s</button><output id="mediaVideoPosition" aria-live="off">0.00s</output><button type="button" id="mediaFrameNext" title="向后步进 0.04 秒" aria-label="向后步进 0.04 秒">+ 0.04s</button></div><div class="media-video-rate"><label for="mediaPlaybackRate">播放速度</label><select id="mediaPlaybackRate"><option value="0.25">0.25×</option><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="1.5">1.5×</option><option value="2">2×</option></select></div><button type="button" class="secondary" id="mediaCaptureFrame">截取当前画面为图片素材</button><small>步进间隔为 0.04 秒；不同视频帧率下不等于精确一帧。</small></div>` : '';
  const download = url ? `<a class="secondary media-download" href="${escapeHtml(url)}" download="${escapeHtml(name || `${info.name}素材`)}" target="_blank" rel="noopener noreferrer">下载文件 ↗</a>` : '';
  return `<div class="node-detail-card media-node-detail" data-media-detail="${escapeHtml(node.id)}"><span class="node-detail-kicker">${info.english} ASSET · ${info.name}素材</span><h3>${escapeHtml(node.title || `${info.name}素材`)}</h3><label for="mediaNodeTitle">节点名称</label><input id="mediaNodeTitle" value="${escapeHtml(node.title || '')}" maxlength="80" placeholder="${info.name}素材"><div class="media-file-meta"><span>当前文件</span><strong>${escapeHtml(name || '尚未选择')}</strong><small>${escapeHtml(asset?.mime_type || info.name)} · ${formatMediaSize(asset?.size_bytes)}</small></div>${preview}${asset?.description ? `<p class="media-detail-description">${escapeHtml(asset.description)}</p>` : ''}${videoTools}<div class="media-detail-actions media-node-interactive">${kind === 'image' || kind === 'prop' ? `<button type="button" class="secondary media-ai-button" id="mediaGenerateImage">${icon('sparkle','ui-icon-sm')} AI 生成图片</button>` : ''}<button type="button" class="primary" id="mediaUploadFile">${url ? '上传替换' : `上传${info.name}`}</button><button type="button" class="secondary" id="mediaPickFile">${url ? '更换素材' : '从素材库选择'}</button>${download}${(kind === 'image' || kind === 'prop') && url ? '<button type="button" class="secondary" id="mediaGenerateVideo">作为视频参考</button>' : ''}</div><p id="mediaDetailStatus" class="media-detail-status" role="status" aria-live="polite"></p></div>`;
}

function waitForMediaEvent(video, name, timeout = 8000) {
  return new Promise((resolve, reject) => {
    const finish = (error) => {
      clearTimeout(timer);
      video.removeEventListener(name, success);
      video.removeEventListener('error', failed);
      error ? reject(error) : resolve();
    };
    const success = () => finish();
    const failed = () => finish(new Error('视频无法读取，请检查文件或更换素材。'));
    const timer = setTimeout(() => finish(new Error('视频读取超时，请等待视频加载后重试。')), timeout);
    video.addEventListener(name, success, { once: true });
    video.addEventListener('error', failed, { once: true });
  });
}

export function bindMediaNodeDetail({ node, asset, api, toast, onPick, onUpload, onRename, onGenerateFromImage, onGenerateImage, onGenerateAudio, onFrameAsset }) {
  const root = document.querySelector('.media-node-detail');
  if (!root || root.dataset.mediaDetail !== String(node.id)) return;
  const find = selector => root.querySelector(selector);
  const message = text => {
    const status = find('#mediaDetailStatus');
    if (status) status.textContent = text;
  };
  const invoke = callback => async () => {
    try { await callback?.(); } catch (error) { message(error.message || '操作失败，请重试'); toast?.(error.message || '操作失败，请重试'); }
  };
  find('#mediaPickFile').onclick = invoke(onPick);
  find('#mediaUploadFile').onclick = invoke(onUpload);
  find('#mediaNodeTitle').onchange = invoke(() => onRename?.(find('#mediaNodeTitle').value.trim().slice(0, 80) || `${mediaKinds[mediaKind(node, asset)].name}素材`));
  if (find('#mediaGenerateImage')) find('#mediaGenerateImage').onclick = invoke(onGenerateImage);
  if (node.type === 'audio') {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'secondary media-ai-button'; button.id = 'mediaGenerateAudio';
    button.innerHTML = `${icon('audio','ui-icon-sm')} AI 生成音频`;
    button.onclick = invoke(onGenerateAudio);
    find('.media-detail-actions').prepend(button);
  }
  if (find('#mediaGenerateVideo')) find('#mediaGenerateVideo').onclick = invoke(() => onGenerateFromImage?.(asset));

  const video = find('#mediaDetailVideo');
  if (!video) return;
  const position = () => { find('#mediaVideoPosition').value = `${(Number(video.currentTime) || 0).toFixed(2)}s`; };
  video.addEventListener('timeupdate', position);
  video.addEventListener('loadedmetadata', position);
  video.addEventListener('error', () => message('视频无法播放，请检查文件格式或更换素材。'));
  find('#mediaPlaybackRate').onchange = event => { video.playbackRate = Number(event.target.value) || 1; };
  let busy = false;
  const controls = ['#mediaFramePrevious', '#mediaFrameNext', '#mediaCaptureFrame'];
  const setBusy = value => { busy = value; controls.forEach(selector => { find(selector).disabled = value; }); };
  const prepareVideo = async () => {
    if (video.error) throw new Error('视频无法读取，请检查文件或更换素材。');
    if (video.readyState < 1) await waitForMediaEvent(video, 'loadedmetadata');
    video.pause();
  };
  const step = seconds => invoke(async () => {
    if (busy) return;
    setBusy(true);
    message('');
    try {
      await prepareVideo();
      const duration = Number.isFinite(video.duration) ? video.duration : video.currentTime + Math.max(0, seconds);
      const target = Math.max(0, Math.min(duration, video.currentTime + seconds));
      if (Math.abs(target - video.currentTime) > 0.0001) {
        const seeking = waitForMediaEvent(video, 'seeked');
        video.currentTime = target;
        await seeking;
      }
      position();
    } finally { setBusy(false); }
  });
  find('#mediaFramePrevious').onclick = step(-0.04);
  find('#mediaFrameNext').onclick = step(0.04);
  find('#mediaCaptureFrame').onclick = invoke(async () => {
    if (busy) return;
    setBusy(true);
    const button = find('#mediaCaptureFrame');
    button.textContent = '正在保存当前画面…';
    message('');
    try {
      await prepareVideo();
      if (video.seeking) await waitForMediaEvent(video, 'seeked');
      if (video.readyState < 2) await waitForMediaEvent(video, 'loadeddata');
      const width = video.videoWidth, height = video.videoHeight;
      if (!width || !height) throw new Error('当前还没有可截取的画面，请播放视频后重试。');
      if (width * height > 64_000_000) throw new Error('视频画面尺寸过大，请先缩小分辨率再截取。');
      const captureTime = video.currentTime;
      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext('2d');
      if (!context) throw new Error('浏览器无法创建图片，请刷新后重试。');
      context.drawImage(video, 0, 0, width, height);
      let blob;
      try {
        blob = await new Promise((resolve, reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error('画面导出失败，请重试。')), 'image/png'));
      } catch (error) {
        if (error.name === 'SecurityError') throw new Error('该视频源不允许跨域截帧，请下载视频并上传到素材库后重试。');
        throw error;
      }
      if (blob.size > 20 * 1024 * 1024) throw new Error('截取图片超过 20 MB，请缩小视频分辨率后重试。');
      const stem = String(asset?.name || node.assetName || '视频').replace(/\.[^.]+$/, '').replace(/[\\/:*?"<>|]/g, '_').slice(0, 70);
      const form = new FormData();
      form.append('file', blob, `${stem}-截帧-${captureTime.toFixed(2)}s.png`);
      const saved = await api('/api/assets', { method: 'POST', body: form });
      await onFrameAsset?.(saved);
      message(`已将 ${captureTime.toFixed(2)} 秒的画面保存为图片素材。`);
      toast?.('当前画面已保存到图片素材库');
    } finally {
      setBusy(false);
      button.textContent = '截取当前画面为图片素材';
    }
  });
}
