import { icon } from './iconography.js?v=library-toggle-1';
import { loadingMarkup } from './loading-state.js?v=1';

const MODES = {
  sfx: { label: '文生音效', symbol: 'audio', placeholder: '例如：雨夜工地上，远处的雷声、近处的雨滴与轻微机械运转声，约 8 秒' },
  voice: { label: '生成配音', symbol: 'microphone', placeholder: '例如：用沉稳、清晰的中文男声朗读：安全生产，人人有责。' },
  music: { label: '生成背景音乐', symbol: 'music', placeholder: '例如：明快温暖的钢琴与弦乐，无人声，适合作为工程宣传片背景音乐，约 20 秒' },
};
const RATES = { wav: [8000, 16000, 24000, 32000, 40000, 44100, 48000], mp3: [8000, 16000, 24000, 32000, 44100, 48000], ogg_opus: [48000] };
const DEFAULT_RATE = { wav: 40000, mp3: 44100, ogg_opus: 48000 };
const AUDIO_EXTENSIONS = /\.(wav|mp3|ogg)$/i;
const IMAGE_EXTENSIONS = /\.(jpe?g|png|webp)$/i;
const PROMPT_LIMIT = 3000;
const requestId = () => crypto.randomUUID();

export function createAudioGenerator(ctx) {
  const { api, json, escapeHtml: esc, toast } = ctx;
  let current = null;
  document.addEventListener('asset-library-change',({detail:asset})=>{
    if(current?.asset?.id===asset.id){Object.assign(current.asset,asset);render(current);}
  });
  const active = session => current === session && session.overlay.isConnected && !session.suspended;
  const busy = session => session.submitting || ['queued', 'running'].includes(session.status);

  function close() {
    if (!current) return;
    const session = current;
    current = null;
    clearTimeout(session.timer);
    document.removeEventListener('keydown', session.onKey, true);
    session.overlay.remove();
    if (session.previousFocus?.isConnected) session.previousFocus.focus({ preventScroll: true });
  }

  function pause(session) {
    if (!active(session) || busy(session)) return false;
    session.suspended = true;
    clearTimeout(session.timer);
    document.removeEventListener('keydown', session.onKey, true);
    session.overlay.remove();
    return true;
  }

  async function resume(session) {
    if (current !== session) return;
    session.suspended = false;
    document.body.appendChild(session.overlay);
    document.addEventListener('keydown', session.onKey, true);
    await loadModels(session);
    render(session);
  }

  async function loadModels(session) {
    try {
      const registry = await api('/api/model-registry');
      if (!Array.isArray(registry.models)) throw new Error('模型列表格式不正确');
      session.models = registry.models.filter(item => item.kind === 'audio');
      session.model = session.models.find(item => item.id === session.modelId) || session.models.find(item => item.is_default) || session.models[0] || null;
      session.modelId = session.model?.id || '';
      session.error = '';
    } catch (error) {
      session.error = `读取音频模型失败：${error.message || '请检查网络连接'}`;
    }
  }

  function referenceError(session, asset) {
    const image = ['image', 'prop'].includes(asset.kind);
    const audio = asset.kind === 'audio';
    if (!image && !audio) return '只能使用图片或音频作为参考';
    const name = String(asset.url || asset.name || '');
    if (image && !IMAGE_EXTENSIONS.test(name)) return '参考图片请选择 JPG、PNG 或 WebP';
    if (audio && !AUDIO_EXTENSIONS.test(name)) return '参考音频请选择 WAV、MP3 或 OGG Opus';
    if (asset.size_bytes > 10 * 1024 * 1024) return '单个参考素材不能超过 10 MB';
    if (session.references.some(item => item.id === asset.id)) return '该素材已添加';
    if (image && (session.references.length || session.speaker)) return '参考图片不能与音频或音色 ID 混用';
    if (audio && session.references.some(item => ['image', 'prop'].includes(item.kind))) return '参考图片不能与音频混用';
    if (session.references.length + (session.speaker ? 1 : 0) >= 3) return '最多使用 3 条参考音频或音色';
    return '';
  }

  function referenceHtml(session) {
    const candidates = session.assets.filter(asset => ['audio', 'image', 'prop'].includes(asset.kind));
    return `<section class="ag-reference"><div class="ag-section-head"><h3>参考素材</h3><span>音频按添加顺序对应 @音频1、@音频2、@音频3</span></div>
      <div class="ag-ref-list">${session.references.length ? session.references.map((asset, index) => `<div class="ag-ref-item"><span class="ag-ref-mark">${icon(asset.kind === 'audio' ? 'audio' : 'image')}</span><span class="ag-ref-name">${esc(asset.name)}</span><small>${asset.kind === 'audio' ? `@音频${index + 1 + (session.speaker ? 1 : 0)}` : '参考图'}</small><button type="button" data-ag-ref-up="${index}" aria-label="前移 ${esc(asset.name)}" title="前移" ${index ? '' : 'disabled'}>${icon('arrowUp')}</button><button type="button" data-ag-ref-down="${index}" aria-label="后移 ${esc(asset.name)}" title="后移" ${index < session.references.length - 1 ? '' : 'disabled'}>${icon('chevronDown')}</button><button type="button" data-ag-ref-remove="${index}" aria-label="移除 ${esc(asset.name)}" title="移除">${icon('close')}</button></div>`).join('') : '<p class="ag-ref-empty">直接输入描述即可生成音频</p>'}</div>
      <div class="ag-ref-add"><select id="agAssetPick" aria-label="从资产库选择参考素材"><option value="">从资产库选择…</option>${candidates.map(asset => `<option value="${esc(asset.id)}">${esc(asset.kind === 'audio' ? '音频' : '图片')} · ${esc(asset.name)}</option>`).join('')}</select><button type="button" id="agUploadReference">${icon('upload')}上传参考</button></div>
      <label class="ag-field"><span>音色 ID <small>可选；与参考图片互斥</small></span><input id="agSpeaker" maxlength="200" value="${esc(session.speaker)}" placeholder="填写豆包语音 2.0 或复刻音色 ID"></label>
    </section>`;
  }

  function render(session) {
    if (!active(session)) return;
    const locked = busy(session), finished = session.asset && session.status === 'succeeded';
    const config = session.audioConfig;
    session.overlay.innerHTML = `<section class="ag-dialog" role="dialog" aria-modal="true" aria-labelledby="agTitle"><header class="ag-header"><div><span>音频创作</span><h2 id="agTitle">AI 生成音频</h2></div><button type="button" id="agClose" aria-label="关闭音频生成">${icon('close')}</button></header>
      <div class="ag-body"><form id="agForm" class="ag-form">${session.loading?loadingMarkup("正在读取音频模型和素材…",true):""}<div class="ag-model-row"><label class="ag-field"><span>本次生成模型</span><select id="agModel" ${locked ? 'disabled' : ''}>${session.models.map(model => `<option value="${esc(model.id)}" ${model.id === session.modelId ? 'selected' : ''}>${esc(model.name)}${model.is_default ? ' · 默认' : ''}${model.api_key_set ? '' : ' · 未配置密钥'}</option>`).join('')}</select></label><button type="button" id="agManageModels" ${locked ? 'disabled' : ''}>模型管理</button></div>
        <div class="ag-modes" role="group" aria-label="音频类型">${Object.entries(MODES).map(([mode, info]) => `<button type="button" data-ag-mode="${mode}" class="${session.mode === mode ? 'is-active' : ''}" aria-pressed="${session.mode === mode}" ${locked ? 'disabled' : ''}>${icon(info.symbol)}${info.label}</button>`).join('')}</div>
        <label class="ag-field"><span>音频名称</span><input id="agName" maxlength="160" value="${esc(session.name)}" required ${locked ? 'disabled' : ''}></label>
        <label class="ag-field ag-prompt"><span>${session.mode === 'voice' ? '配音内容与声音要求' : session.mode === 'music' ? '音乐描述' : '音效描述'} <small><output id="agPromptCount">${session.prompt.length}</output>/${PROMPT_LIMIT}</small></span><textarea id="agPrompt" maxlength="${PROMPT_LIMIT}" rows="5" required placeholder="${esc(MODES[session.mode].placeholder)}" ${locked ? 'disabled' : ''}>${esc(session.prompt)}</textarea></label>
        ${referenceHtml(session)}<details class="ag-advanced" ${session.advanced ? 'open' : ''}><summary>输出设置</summary><div class="ag-options"><label class="ag-field"><span>格式</span><select id="agFormat" ${locked ? 'disabled' : ''}><option value="wav" ${config.format === 'wav' ? 'selected' : ''}>WAV</option><option value="mp3" ${config.format === 'mp3' ? 'selected' : ''}>MP3</option><option value="ogg_opus" ${config.format === 'ogg_opus' ? 'selected' : ''}>OGG Opus</option></select></label><label class="ag-field"><span>采样率</span><select id="agSampleRate" ${locked ? 'disabled' : ''}>${RATES[config.format].map(value => `<option value="${value}" ${value === config.sample_rate ? 'selected' : ''}>${value.toLocaleString()} Hz</option>`).join('')}</select></label><label class="ag-field"><span>语速</span><input id="agSpeechRate" type="number" min="-50" max="100" value="${config.speech_rate}" ${locked ? 'disabled' : ''}></label><label class="ag-field"><span>音量</span><input id="agLoudnessRate" type="number" min="-50" max="100" value="${config.loudness_rate}" ${locked ? 'disabled' : ''}></label><label class="ag-field"><span>音调</span><input id="agPitchRate" type="number" min="-12" max="12" value="${config.pitch_rate}" ${locked ? 'disabled' : ''}></label></div><div class="ag-checks"><label><input id="agSubtitles" type="checkbox" ${config.enable_subtitle ? 'checked' : ''} ${locked ? 'disabled' : ''}>返回字幕时间轴</label><label><input id="agWatermark" type="checkbox" ${session.watermark ? 'checked' : ''} ${locked ? 'disabled' : ''}>添加音频水印</label></div></details>
        <p class="ag-error" role="alert" ${session.error ? '' : 'hidden'}>${esc(session.error || '')}</p><div class="ag-form-actions"><button type="submit" class="ag-primary" ${session.loading || locked || !session.model?.api_key_set ? 'disabled' : ''}>${locked ? '正在生成…' : finished ? '重新生成' : '生成音频'}</button></div></form>
        <aside class="ag-result"><div class="ag-wave" aria-hidden="true">${Array.from({length: 24}, (_, index) => `<i style="--bar:${[18,32,46,26,65,82,45,23,58,92,53,34,76,100,69,39,84,52,25,71,47,29,62,20][index]}%"></i>`).join('')}</div><div class="ag-result-status" role="status" aria-live="polite"><strong>${finished ? '音频已生成' : locked ? session.status === 'queued' ? '任务排队中' : '正在生成音频' : session.status === 'failed' ? '生成未完成' : '等待生成'}</strong>${finished ? `<small>${session.asset.duration ? Number(session.asset.duration).toFixed(1) + ' 秒 · ' : ''}${esc(session.asset.name)}</small>` : '<small>生成完成后可在这里试听</small>'}</div>${finished ? `<audio controls preload="metadata" src="${esc(session.asset.url)}"></audio><a class="ag-download" href="${esc(session.asset.url)}" download="${esc(session.asset.name)}">${icon('download')}下载音频</a>` : ''}</aside></div>
      <footer class="ag-footer"><span>${locked ? '关闭面板后任务会继续，可稍后从生成历史查看' : session.model?.api_key_set ? `${esc(session.model.provider)} · ${esc(session.model.model_id)}` : '请先配置音频模型 API Key'}</span>${finished?`<button type="button" id="agSaveLibrary" >${icon(session.asset.in_library?"libraryRemove":"library")}${session.asset.in_library?"移出资产库":"保存至资产库"}</button>`:""}<button type="button" id="agUse" class="ag-primary" ${finished ? '' : 'disabled'}>${session.options.onUse ? '使用这段音频' : '完成'}</button></footer></section>`;
    bind(session);
  }

  function bind(session) {
    const find = selector => session.overlay.querySelector(selector);
    find('#agClose').onclick = close;
    if(find('#agSaveLibrary'))find('#agSaveLibrary').onclick=async event=>{event.currentTarget.disabled=true;event.currentTarget.textContent=session.asset.in_library?'正在移出…':'正在保存…';const asset=await ctx.onSaveAsset?.(session.asset,!session.asset.in_library);if(asset)session.asset=asset;render(session);};
    find('#agUse').onclick = async () => {
      if (!session.asset) return;
      try { await session.options.onUse?.(session.asset); close(); }
      catch (error) { session.error = error.message || '音频关联失败'; render(session); }
    };
    find('#agManageModels').onclick = async () => {
      if (!ctx.onManageModels || !pause(session)) return;
      try { await ctx.onManageModels({ kind: 'audio', onReturn: () => resume(session) }); }
      catch (error) { session.error = error.message || '无法打开模型管理'; await resume(session); }
    };
    find('#agModel').onchange = event => { session.modelId = event.target.value; session.model = session.models.find(item => item.id === session.modelId); session.options.onDraft?.({model_config_id: session.modelId}); render(session); };
    find('#agName').oninput = event => { session.name = event.target.value; session.options.onDraft?.({name: session.name}); };
    find('#agPrompt').oninput = event => { session.prompt = event.target.value; find('#agPromptCount').value = session.prompt.length; session.options.onDraft?.({prompt: session.prompt}); };
    find('#agSpeaker').oninput = event => { session.speaker = event.target.value.trim(); session.options.onDraft?.({speaker: session.speaker}); };
    find('#agAssetPick').onchange = event => {
      const asset = session.assets.find(item => item.id === event.target.value);
      if (!asset) return;
      const error = referenceError(session, asset);
      if (error) { session.error = error; render(session); return; }
      session.references.push(asset); session.error = ''; session.options.onDraft?.({reference_asset_ids: session.references.map(item => item.id)}); render(session);
    };
    find('#agUploadReference').onclick = () => {
      const input = document.createElement('input'); input.type = 'file'; input.accept = '.wav,.mp3,.ogg,.jpg,.jpeg,.png,.webp';
      input.onchange = async () => {
        const file = input.files?.[0]; if (!file) return;
        if (file.size > 10 * 1024 * 1024) { session.error = '参考素材不能超过 10 MB'; render(session); return; }
        try {
          const form = new FormData(); form.append('file', file);
          if (session.options.project_id) form.append('project_id', session.options.project_id);
          const asset = await api('/api/assets', { method: 'POST', body: form });
          session.assets.unshift(asset);
          await ctx.onAssetCreated?.(asset);
          const error = referenceError(session, asset);
          if (error) throw new Error(`${error}；文件已加入生成历史`);
          session.references.push(asset); session.error = '';
          session.options.onDraft?.({reference_asset_ids: session.references.map(item => item.id)});
        } catch (error) { session.error = error.message || '上传参考素材失败'; }
        render(session);
      };
      input.click();
    };
    find('.ag-advanced').ontoggle = event => { session.advanced = event.target.open; };
    find('#agFormat').onchange = event => { session.audioConfig.format = event.target.value; session.audioConfig.sample_rate = DEFAULT_RATE[event.target.value]; render(session); };
    for (const [selector, key] of [['#agSampleRate','sample_rate'], ['#agSpeechRate','speech_rate'], ['#agLoudnessRate','loudness_rate'], ['#agPitchRate','pitch_rate']]) find(selector).onchange = event => { session.audioConfig[key] = Number(event.target.value); };
    find('#agSubtitles').onchange = event => { session.audioConfig.enable_subtitle = event.target.checked; };
    find('#agWatermark').onchange = event => { session.watermark = event.target.checked; };
    find('#agForm').onsubmit = event => { event.preventDefault(); generate(session); };
    for (const action of ['up', 'down', 'remove']) session.overlay.querySelectorAll(`[data-ag-ref-${action}]`).forEach(button => button.onclick = () => {
      const index = Number(button.dataset[`agRef${action[0].toUpperCase()}${action.slice(1)}`]);
      if (action === 'remove') session.references.splice(index, 1);
      else { const other = index + (action === 'up' ? -1 : 1); [session.references[index], session.references[other]] = [session.references[other], session.references[index]]; }
      session.options.onDraft?.({reference_asset_ids: session.references.map(item => item.id)}); render(session);
    });
    session.overlay.querySelectorAll('[data-ag-mode]').forEach(button => button.onclick = () => { session.mode = button.dataset.agMode; session.options.onDraft?.({mode: session.mode}); render(session); find('#agPrompt')?.focus(); });
  }

  async function acceptTask(session, task) {
    session.status = task.status;
    if (task.status === 'succeeded') {
      session.asset = task.asset || null; session.error = task.asset ? '' : '音频生成完成，但素材未能保存';
      if (task.asset) await ctx.onAssetCreated?.(task.asset);
    } else if (task.status === 'failed') session.error = task.error || '音频生成失败';
    render(session);
  }

  async function poll(session) {
    if (!active(session) || !session.taskId) return;
    clearTimeout(session.timer);
    try {
      const task = await api(`/api/audio/generations/${encodeURIComponent(session.taskId)}`);
      if (!active(session)) return;
      session.pollErrors = 0; await acceptTask(session, task);
      if (busy(session)) session.timer = setTimeout(() => poll(session), 2000);
    } catch (error) {
      if (!active(session)) return;
      session.pollErrors++;
      if (session.pollErrors >= 3) { session.error = '进度查询失败，任务仍在后台运行；请稍后从生成历史查看'; render(session); }
      else session.timer = setTimeout(() => poll(session), 2500);
    }
  }

  async function generate(session) {
    if (!active(session) || busy(session)) return;
    if (!session.model?.api_key_set) { session.error = '请先配置音频模型 API Key'; render(session); return; }
    if (!session.prompt.trim() || !session.name.trim()) { session.error = '请填写名称和音频描述'; render(session); return; }
    if (session.references.some(item => ['image', 'prop'].includes(item.kind)) && (session.references.length > 1 || session.speaker)) { session.error = '参考图片不能与音频或音色 ID 混用'; render(session); return; }
    if (session.references.length + (session.speaker ? 1 : 0) > 3) { session.error = '最多使用 3 条参考音频或音色'; render(session); return; }
    const payload = { name: session.name.trim(), prompt: session.prompt.trim(), mode: session.mode, model_config_id: session.modelId,
      project_id: session.options.project_id || '', folder_id: session.options.folder_id || '',
      reference_asset_ids: session.references.map(item => item.id), speaker: session.speaker.trim(),
      audio_config: session.audioConfig, watermark: session.watermark };
    const fingerprint = JSON.stringify(payload);
    if (session.fingerprint !== fingerprint || !session.uncertain) session.requestId = requestId();
    session.fingerprint = fingerprint; session.uncertain = true;
    session.submitting = true; session.error = ''; session.asset = null; render(session);
    try {
      const task = await api('/api/audio/generations', json({...payload, request_id: session.requestId}));
      session.uncertain = false; session.taskId = task.id;
      await session.options.onTask?.(task);
      if (!active(session)) return;
      session.submitting = false;
      await acceptTask(session, task);
      if (busy(session)) poll(session);
    } catch (error) {
      if (active(session)) { session.status = 'failed'; session.error = error.message || '提交音频任务失败'; }
    } finally { session.submitting = false; render(session); }
  }

  async function open(options = {}) {
    close();
    const overlay = document.createElement('div'); overlay.className = 'ag-overlay'; overlay.id = 'audioGenerationOverlay';
    const saved = options.settings || {};
    const audioConfig = {format: 'wav', sample_rate: 40000, speech_rate: 0, loudness_rate: 0, pitch_rate: 0, enable_subtitle: false, ...saved.audio_config};
    if (!RATES[audioConfig.format]?.includes(audioConfig.sample_rate)) { audioConfig.format = 'wav'; audioConfig.sample_rate = 40000; }
    const session = { overlay, options, previousFocus: document.activeElement, models: [], model: null,
      modelId: saved.model_config_id || '', mode: MODES[options.mode] ? options.mode : MODES[saved.mode] ? saved.mode : 'sfx',
      prompt: String(options.prompt ?? saved.prompt ?? '').slice(0, PROMPT_LIMIT), name: String(options.name || saved.name || 'AI 生成音频').slice(0, 160),
      references: [], assets: [], speaker: String(saved.speaker || ''), audioConfig, watermark: Boolean(saved.watermark),
      loading: true, submitting: false, status: '', error: '', taskId: options.taskId || '', pollErrors: 0, advanced: false };
    current = session;
    session.onKey = event => {
      if (!active(session)) return;
      event.stopPropagation();
      if (event.key === 'Escape') { event.preventDefault(); close(); return; }
      if (event.key !== 'Tab') return;
      const focusable = [...overlay.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]')].filter(item => !item.hidden && item.getClientRects().length);
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', session.onKey, true);
    overlay.addEventListener('pointerdown', event => event.stopPropagation());
    overlay.addEventListener('wheel', event => event.stopPropagation(), {passive: true});
    document.body.appendChild(overlay);
    render(session);
    try {
      const [, assets] = await Promise.all([loadModels(session), api('/api/assets')]);
      if (current !== session) return;
      session.assets = Array.isArray(assets) ? assets : [];
      session.references = [...new Set([...(options.references || []).map(item => item.id), ...(saved.reference_asset_ids || [])])]
        .map(id => session.assets.find(item => item.id === id)).filter(Boolean).slice(0, 3);
      if (session.taskId) {
        try { await acceptTask(session, await api(`/api/audio/generations/${encodeURIComponent(session.taskId)}`)); }
        catch { session.taskId = ''; }
      }
    } catch (error) { session.error = error.message || '无法读取音频素材'; }
    finally { session.loading = false; render(session); if (active(session)) { if (busy(session)) poll(session); else overlay.querySelector('#agPrompt')?.focus({preventScroll: true}); } }
  }

  return { open, close };
}
