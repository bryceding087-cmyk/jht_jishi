import { icon } from './iconography.js?v=library-toggle-1';
import { createVisualStylePicker } from './visual-style-picker.js?v=4';
import { loadingMarkup } from './loading-state.js?v=1';

/** Shared image generation dialog; its overlay leaves the script editor mounted. */
export function createImageGenerator(ctx) {
  const { api, json, escapeHtml: esc, toast } = ctx;
  const stylePicker=createVisualStylePicker({api,escapeHtml:esc,icon,onError:toast});
  let current = null;
  document.addEventListener('asset-library-change',({detail:asset})=>{
    if(current?.asset?.id===asset.id){Object.assign(current.asset,asset);render(current);}
  });
  const safeImageUrl = value => {
    try { const url = new URL(String(value || ''), location.origin); return ['http:', 'https:'].includes(url.protocol) ? url.href : ''; }
    catch { return ''; }
  };
  const requestId = () => globalThis.crypto?.randomUUID?.() || `image-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const active = session => current === session && !session.suspended && session.overlay.isConnected;
  const busy = session => session.submitting || ['queued', 'running'].includes(session.status);

  function close() {
    if (!current) return;
    const session = current;
    stylePicker.close();current = null;
    clearTimeout(session.timer);
    document.removeEventListener('keydown', session.onKey, true);
    session.overlay.remove();
    if (session.previousFocus?.isConnected) session.previousFocus.focus({ preventScroll: true });
    session.options.onClose?.();
  }

  async function loadModels(session, returning = false) {
    const previousDefault = session.defaultModelId;
    let models, warning = '';
    try {
      const registry = await api('/api/model-registry');
      if (!Array.isArray(registry?.models)) throw new Error('模型列表格式不正确');
      models = registry.models.filter(model => model.kind === 'image').map(model => ({
        ...model, model: model.model_id, configured: Boolean(model.api_key_set), legacy: false,
      }));
    } catch (error) {
      warning = `读取模型列表失败：${error.message || '网络连接异常'}。`;
      try {
        const config = await api('/api/image-model');
        models = [{ ...config, id: config.id || 'default-image-model', is_default: true, legacy: true }];
        warning += ' 当前使用已保存的默认图片配置，可重新加载模型列表。';
      } catch (fallbackError) {
        models = [];
        warning += ` 默认配置也无法读取：${fallbackError.message || '网络连接异常'}。`;
      }
    }
    if (current !== session) return;
    const defaultModel = models.find(model => model.is_default) || models[0];
    const defaultChanged = returning && previousDefault && defaultModel?.id !== previousDefault;
    session.models = models;
    session.config = (!defaultChanged && models.find(model => model.id === session.selectedModelId)) || defaultModel || null;
    session.selectedModelId = session.config?.id || '';
    session.defaultModelId = defaultModel?.id || '';
    session.modelWarning = warning;
    if (!returning && session.config) session.watermark = false;
  }

  function suspend(session = current) {
    if (!session || current !== session || busy(session)) return false;
    stylePicker.close();session.suspended = true;
    clearTimeout(session.timer);
    document.removeEventListener('keydown', session.onKey, true);
    session.overlay.remove();
    return true;
  }

  async function resume(session = current) {
    if (!session || current !== session) return;
    session.suspended = false; session.loading = true;
    document.body.appendChild(session.overlay);
    document.addEventListener('keydown', session.onKey, true);
    render(session);
    await loadModels(session, true);
    session.loading = false;
    render(session);
    if (active(session)) session.overlay.querySelector('#igPrompt')?.focus({ preventScroll: true });
  }

  async function manageModels(session) {
    if (!active(session) || busy(session) || session.loading) return;
    if (!ctx.onManageModels) { session.error = '请从左侧「模型管理」配置图片模型后重新打开。'; render(session); return; }
    suspend(session);
    try { await ctx.onManageModels({ kind: 'image', onReturn: () => resume(session) }); }
    catch (error) { session.error = error.message || '模型管理页面打开失败'; await resume(session); }
  }

  function referenceHtml(session) {
    const references = session.references || [];
    if (!references.length) return '';
    const cards = references.map(item => {
      const name = esc(item.name || '参考图');
      const url = safeImageUrl(item.url);
      const visual = url ? `<img src="${esc(url)}" alt="${name}">` : `<span>${icon('image', 'ui-icon-sm')}</span>`;
      return `<figure class="ig-reference">${visual}<figcaption title="${name}">${name}</figcaption></figure>`;
    }).join('');
    return `<div class="ig-references"><div class="ig-references-head"><span>参考图</span><small>${references.length} 张 · 将随画面描述一起提交</small></div><div class="ig-reference-list">${cards}</div></div>`;
  }

  function previewHtml(session) {
    if (session.asset) {
      const url = safeImageUrl(session.asset.url);
      return `<div class="ig-result"><div class="ig-result-image">${url ? `<img src="${esc(url)}" alt="${esc(session.asset.name || 'AI 生成图片')}">` : '<p>图片已保存，预览地址暂不可用</p>'}</div><div class="ig-result-info"><span class="ig-success-dot"></span><span>已加入生成历史</span><strong>${esc(session.asset.name || session.name)}</strong></div></div>`;
    }
    if (session.loading) return loadingMarkup('正在读取图片生成配置…');
    if (busy(session)) return `<div class="ig-empty ig-generating" role="status">${loadingMarkup('')}<h3>${session.submitting ? '正在提交任务' : session.status === 'queued' ? '已提交，等待生成' : '正在生成图片'}</h3><p>图片完成后会自动加入生成历史。<br>关闭此窗口后，任务仍会在后台继续。</p></div>`;
    return `<div class="ig-empty"><span class="ig-art-symbol" aria-hidden="true">${icon('sparkle')}</span><h3>把创作想法变成参考图</h3><p>描述主体、场景、构图和画面风格，<br>为角色、分镜或视频准备图片素材。</p><div class="ig-example-tags"><span>角色形象</span><span>场景参考</span><span>分镜画面</span></div></div>`;
  }

  function render(session) {
    if (!active(session)) return;
    stylePicker.close();
    const generating = busy(session), config = session.config;
    session.overlay.innerHTML = `<section class="ig-dialog" role="dialog" aria-modal="true" aria-labelledby="igTitle"><header class="ig-header"><div><span class="ig-kicker">图片创作</span><h2 id="igTitle">AI 生成图片</h2><p>生成结果保留在生成历史，可手动保存至资产库</p></div><button class="ig-close" data-ig-action="close" aria-label="关闭图片生成">${icon('close')}</button></header><div class="ig-body"><div class="ig-controls"><div class="ig-model"><div><span class="ig-model-icon">${icon('image')}</span><strong>${esc(config?.name || '图片生成模型')}</strong></div><button class="ig-link" data-ig-action="settings" ${session.loading || generating ? 'disabled' : ''}>模型管理</button></div><p class="ig-model-state ${config?.configured ? 'is-ready' : ''}">${session.loading ? '正在读取模型配置…' : config?.configured ? `已配置 · ${esc(config.model)}${config.api_key_hint ? ` · ${esc(config.api_key_hint)}` : ''}` : '请先绑定图片生成模型'}</p><label class="ig-model-select-label">本次生成模型<select id="igModelSelect" ${session.loading || generating || !session.models.length ? 'disabled' : ''}>${session.models.length ? session.models.map(model => `<option value="${esc(model.id)}" ${model.id === session.selectedModelId ? 'selected' : ''}>${esc(model.name || model.model)}${model.is_default ? ' · 默认' : ''}${model.configured ? '' : ' · 未配置密钥'}</option>`).join('') : '<option value="">暂无图片模型</option>'}</select></label>${!session.loading && !config?.configured ? '<button type="button" class="ig-btn ig-manage-empty" data-ig-action="settings">前往模型管理</button>' : ''}${session.modelWarning ? `<div class="ig-model-warning" role="status">${esc(session.modelWarning)}<button class="ig-link" data-ig-action="reload-models" ${generating ? 'disabled' : ''}>重新加载</button></div>` : ''}<form id="igGenerateForm" class="ig-prompt-form"><label>素材名称<input id="igName" maxlength="160" value="${esc(session.name)}" placeholder="例如：智慧工地晨景" ${generating ? 'disabled' : ''}></label><label class="ig-prompt-label">画面描述<span id="igPromptCount">${session.prompt.length}/4000</span><textarea id="igPrompt" rows="9" maxlength="4000" required placeholder="${esc((session.references || []).length ? '描述希望如何使用这些参考图，例如：保留人物与构图，改成黄昏暖光、电影感宽银幕画面。' : '描述你想生成的画面，例如：清晨的智慧工地全景，塔吊与建筑轮廓被金色晨光照亮，电影感、写实摄影、广角构图。')}" ${generating ? 'disabled' : ''}>${esc(session.prompt)}</textarea></label>${referenceHtml(session)}<div class="ig-options"><div><span>图片尺寸</span><strong>2K</strong></div><span class="ig-watermark-note">无水印输出</span></div><button id="igGenerate" type="submit" class="ig-btn ig-primary ig-generate" ${session.loading || generating || !config?.configured ? 'disabled' : ''}>${generating ? '生成中…' : session.asset ? '重新生成一张' : `${icon('sparkle','ui-icon-sm')}生成图片`}</button><p class="ig-note">每次生成一张图片，按所绑定模型的实际调用计费。</p></form>${session.error ? `<div class="ig-error" role="alert">${esc(session.error)}${session.pollError ? '<button class="ig-link" data-ig-action="check-task">重新查询任务</button>' : ''}</div>` : ''}</div><div class="ig-preview">${previewHtml(session)}</div></div><footer class="ig-footer"><p>${session.asset ? '图片已加入生成历史，可使用或保存至资产库。' : '支持从画布、资产库和脚本资源中创建图片。'}</p><button class="ig-btn" data-ig-action="close">${generating ? '后台继续生成' : '关闭'}</button>${session.asset ? `<button class="ig-btn" data-ig-action="save-library" >${icon(session.asset.in_library?"libraryRemove":"library")}${session.asset.in_library?"移出资产库":"保存至资产库"}</button><button class="ig-btn ig-primary" data-ig-action="use" ${session.using ? 'disabled' : ''}>${session.using ? '正在关联…' : session.options.onUse ? '使用这张图片' : '完成'}</button>` : ''}</footer></section>`;
    const find = selector => session.overlay.querySelector(selector);
    const styleContainer=document.createElement('div');styleContainer.className='ig-visual-style';find('.ig-options').before(styleContainer);
    stylePicker.button(styleContainer,{getValue:()=>session.visual_style,onChange:value=>{session.visual_style=value;},disabled:generating});
    find('#igName').oninput = event => { session.name = event.target.value; };
    find('#igPrompt').oninput = event => { session.prompt = event.target.value; find('#igPromptCount').textContent = `${session.prompt.length}/4000`; };
    find('#igGenerateForm').onsubmit = event => { event.preventDefault(); generate(session); };
    find('#igModelSelect').onchange = event => {
      if (busy(session)) return;
      session.selectedModelId = event.target.value;
      session.config = session.models.find(model => model.id === session.selectedModelId) || null;
      render(session);
    };
    session.overlay.querySelectorAll('[data-ig-action]').forEach(button => {
      button.onclick = async () => {
        const action = button.dataset.igAction;
        if (action === 'close') close();
        if (action === 'settings') manageModels(session);
        if (action === 'reload-models') { session.loading = true; render(session); loadModels(session, true).finally(() => { session.loading = false; render(session); }); }
        if (action === 'check-task') { session.pollFailures = 0; session.pollError = false; session.error = ''; render(session); poll(session); }
        if (action === 'use') useAsset(session);
        if (action === 'save-library') {button.disabled=true;const asset=await ctx.onSaveAsset?.(session.asset,!session.asset.in_library);if(asset)session.asset=asset;render(session);}
      };
    });
    const preview = find('.ig-result-image img');
    if (preview) preview.onerror = () => { const note = document.createElement('p'); note.textContent = '图片预览加载失败，图片仍保存在生成历史中。'; preview.replaceWith(note); };
  }

  async function generate(session) {
    if (!active(session) || busy(session) || session.loading || !session.config?.configured) return;
    if (!session.prompt.trim()) { session.error = '请先填写画面描述'; render(session); return; }
    session.submitting = true; session.error = ''; session.pollError = false; session.asset = null; session.pollFailures = 0;
    const payload = { visual_style:session.visual_style||null, prompt: session.prompt.trim(), name: session.name.trim() || 'AI 生成图片', project_id: session.options.project_id || '', folder_id: session.options.folder_id || '', asset_kind: session.options.asset_kind === 'prop' ? 'prop' : 'image', size: '2K', watermark: false };
    const referenceIds = [...new Set((session.references || []).map(item => String(item.id || '').trim()).filter(Boolean))].slice(0, 10);
    if (referenceIds.length) payload.reference_asset_ids = referenceIds;
    if (!session.config.legacy) payload.model_config_id = session.selectedModelId;
    const fingerprint = JSON.stringify(payload);
    if (!session.submissionUnknown || session.requestFingerprint !== fingerprint) session.requestId = requestId();
    session.requestFingerprint = fingerprint;
    session.submissionUnknown = true;
    session.taskId = null;
    render(session);
    try {
      const task = await api('/api/images/generations', json({ ...payload, request_id: session.requestId }));
      if (!active(session)) return;
      session.submissionUnknown = false;
      session.taskId = task.id; session.status = task.status || 'queued'; session.submitting = false;
      if (!session.taskId) throw new Error('任务已提交，但未返回任务编号。请稍后在生成历史查看结果。');
      if (['succeeded', 'failed', 'done'].includes(task.status)) await acceptTask(session, task);
      else { render(session); poll(session); }
    } catch (error) {
      if (active(session)) { session.error = error.message || '图片生成任务提交失败'; session.status = 'failed'; }
    } finally { session.submitting = false; render(session); }
  }

  async function acceptTask(session, task) {
    if (!active(session)) return;
    session.status = task.status || session.status;
    if (['succeeded', 'done'].includes(task.status)) {
      if (!task.asset) { session.status = 'failed'; session.error = '生成任务已完成，但未返回图片素材，请在生成历史中查看。'; }
      else {
        session.asset = task.asset; session.error = '';
        try { await ctx.onAssetCreated?.(task.asset); }
        catch { toast?.('图片已生成；生成历史刷新失败，可稍后重新进入生成历史。'); }
      }
    } else if (task.status === 'failed') session.error = typeof task.error === 'string' ? task.error : task.error?.message || '图片生成失败，请调整描述后重试。';
    render(session);
  }

  async function poll(session) {
    if (!active(session) || !session.taskId) return;
    clearTimeout(session.timer);
    try {
      const task = await api(`/api/images/generations/${encodeURIComponent(session.taskId)}`);
      if (!active(session)) return;
      session.pollFailures = 0; session.pollError = false; session.error = '';
      await acceptTask(session, task);
      if (active(session) && ['queued', 'running'].includes(session.status)) session.timer = setTimeout(() => poll(session), 1800);
    } catch (error) {
      if (!active(session)) return;
      session.pollFailures += 1;
      if (session.pollFailures >= 3) {
        session.pollError = true; session.error = '暂时无法查询生成进度，后台任务仍会继续。可以重新查询，或稍后在生成历史查看。'; render(session);
      } else session.timer = setTimeout(() => poll(session), 2500);
    }
  }

  async function useAsset(session) {
    if (!active(session) || session.using || !session.asset) return;
    session.using = true; session.error = ''; render(session);
    try {
      const accepted = await session.options.onUse?.(session.asset);
      if (active(session) && accepted !== false) close();
    } catch (error) { if (active(session)) session.error = error.message || '图片关联失败，可在资产库中重新选择。'; }
    finally { session.using = false; render(session); }
  }

  async function open(options = {}) {
    close();
    const overlay = document.createElement('div'); overlay.id = 'imageGenerationOverlay'; overlay.className = 'ig-overlay';
    const references = (Array.isArray(options.references) ? options.references : []).filter(item => item && item.id).slice(0, 10).map(item => ({ id: String(item.id), name: String(item.name || '参考图').slice(0, 160), url: String(item.url || '') }));
    const session = { overlay, options, references, visual_style:structuredClone(options.visual_style||null), previousFocus: document.activeElement, prompt: String(options.prompt || '').slice(0, 4000), name: String(options.name || '').slice(0, 160), watermark: false, loading: true, config: null, models: [], selectedModelId: '', defaultModelId: '', modelWarning: '', suspended: false, error: '', status: '', pollFailures: 0 };
    current = session;
    session.onKey = event => {
      if (!active(session)) return;
      if (document.querySelector('.visual-style-picker:popover-open')) return;
      event.stopPropagation();
      if (event.key === 'Escape') { event.preventDefault(); close(); return; }
      if (event.key !== 'Tab') return;
      const focusable = [...overlay.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href]')].filter(element => !element.hidden && element.getClientRects().length);
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && (!overlay.contains(document.activeElement) || document.activeElement === first)) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && (!overlay.contains(document.activeElement) || document.activeElement === last)) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', session.onKey, true);
    overlay.addEventListener('pointerdown', event => event.stopPropagation());
    overlay.addEventListener('wheel', event => event.stopPropagation(), { passive: true });
    document.body.appendChild(overlay); render(session);
    try { await loadModels(session); }
    finally { session.loading = false; render(session); if (active(session)) overlay.querySelector('#igPrompt')?.focus({ preventScroll: true }); }
  }
  return { open, close, suspend, resume };
}
