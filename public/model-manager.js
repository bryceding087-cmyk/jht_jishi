import { icon } from './iconography.js?v=icons-font-2';
import { loadingMarkup } from './loading-state.js?v=1';

const KINDS = {
  text: { title: '文本大模型', icon: 'text', detail: '提示词润色、分镜脚本与资源分析' },
  image: { title: '图片生成模型', icon: 'image', detail: '图片节点、图片资产与分镜资源生成' },
  video: { title: '视频生成模型', icon: 'video', detail: '视频节点与分镜视频生成' },
  audio: { title: '音频生成模型', icon: 'audio', detail: '文生音效、配音与背景音乐生成' },
};

export function createModelManager(ctx) {
  const { api, escapeHtml: esc, toast } = ctx;
  const ui = { kind: 'text', models: [], providers: {}, canManage: false, loading: false, loaded: false, error: '', notice: '', busy: false, deleteId: '', editors: {} };
  let host = null, loadSequence = 0, openSequence = 0;
  const endpoint = model => `/api/model-registry/${encodeURIComponent(model.kind)}/${encodeURIComponent(model.id)}`;
  const json = (body, method = 'POST') => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const find = id => ui.models.find(model => model.id === id && model.kind === ui.kind);
  const disabled = () => ui.busy ? ' disabled' : '';
  const modelsFor = kind => ui.models.filter(model => model.kind === kind);
  const activeFor = kind => modelsFor(kind).find(model => model.is_default);
  const errorMessage = error => typeof error?.message === 'string' ? error.message : '操作未完成，请稍后重试。';
  const keyStatus = model => model.credential_source === 'market-session'
    ? (model.api_key_set ? '使用当前集市会话凭据' : '当前集市连接不支持此模型')
    : model.api_key_set ? (model.credential_source === 'account' ? '已配置个人专属凭据' : `已配置${model.api_key_hint ? ` · ${esc(model.api_key_hint)}` : ''}`) : '待配置 API Key';

  function card(model) {
    const isDeleting = ui.deleteId === model.id;
    const personalActions = `<div class="mm-card-actions">${model.can_activate && !model.is_default ? `<button type="button" class="mm-button mm-activate" data-mm-action="activate" data-mm-id="${esc(model.id)}"${disabled()}>设为个人默认</button>` : `<span class="mm-current-label">${model.can_activate && model.is_default ? '默认使用' : '由管理员统一配置'}</span>`}${model.is_account_model ? '<span class="mm-current-label">仅本人使用</span>' : ''}</div>`;
    return `<article class="mm-card${model.is_default ? ' is-default' : ''}" data-mm-model="${esc(model.id)}">
      <div class="mm-card-head"><span class="mm-model-icon">${icon(KINDS[model.kind].icon)}</span><div><h3>${esc(model.name)}</h3><p>${esc(model.provider || '兼容接口')}</p></div>${model.is_default ? '<span class="mm-badge">当前默认</span>' : model.is_builtin ? '<span class="mm-badge mm-builtin">内置</span>' : ''}</div>
      <div class="mm-model-id" title="${esc(model.model_id || '')}">${esc(model.model_id || '未填写模型 ID')}</div>
      <dl class="mm-card-details"><div><dt>服务地址</dt><dd title="${esc(model.base_url || '')}">${esc(model.base_url || '未填写')}</dd></div><div><dt>${model.kind === 'video' ? '提交路径' : 'API 路径'}</dt><dd title="${esc(model.api_path || '')}">${esc(model.api_path || '未填写')}</dd></div>${model.kind === 'video' ? `<div><dt>查询路径</dt><dd title="${esc(model.query_path || '')}">${esc(model.query_path || '未填写')}</dd></div>` : ''}</dl>
      <div class="mm-key-status${model.api_key_set ? ' is-configured' : ''}"><i></i>${keyStatus(model)}</div>
      ${!ui.canManage ? personalActions : isDeleting ? `<div class="mm-delete-confirm" role="alert"><p>删除「${esc(model.name)}」的模型配置？${model.is_default ? '请先将其他模型设为默认。' : '已生成的作品会保留。'}</p><div><button type="button" class="mm-button" data-mm-action="cancel-delete"${disabled()}>取消</button><button type="button" class="mm-button mm-danger" data-mm-action="confirm-delete" data-mm-id="${esc(model.id)}"${disabled()}>确认删除</button></div></div>` : `<div class="mm-card-actions"><button type="button" class="mm-button" data-mm-action="edit" data-mm-id="${esc(model.id)}"${disabled()}>编辑配置</button>${!model.is_default ? `<button type="button" class="mm-button mm-activate" data-mm-action="activate" data-mm-id="${esc(model.id)}"${disabled()}>设为默认</button>` : '<span class="mm-current-label">默认使用</span>'}${!model.is_builtin ? `<button type="button" class="mm-text-button mm-delete" aria-label="删除 ${esc(model.name)}" data-mm-action="delete" data-mm-id="${esc(model.id)}"${disabled()}>删除</button>` : ''}</div>`}
    </article>`;
  }

  function editorHtml(editor) {
    const info = KINDS[ui.kind];
    const providers = ui.providers[ui.kind] || [];
    const provider = providers.find(item => item.id === editor.draft.provider_id);
    const input = (name, label, options = {}) => `<label class="mm-field${options.wide ? ' mm-wide' : ''}"><span>${label}${options.required !== false ? ' <em>*</em>' : ''}</span><input name="${name}" type="${options.type || 'text'}" value="${esc(editor.draft[name] || '')}" maxlength="${options.max || 300}"${options.type === 'number' ? ' min="5" max="900" step="1"' : ''} placeholder="${esc(options.placeholder || '')}"${options.required !== false ? ' required' : ''}${name === 'api_key' ? ' autocomplete="new-password" spellcheck="false"' : ''}${disabled()}>${options.hint ? `<small>${esc(options.hint)}</small>` : ''}</label>`;
    const oldModel = editor.id ? ui.models.find(model => model.id === editor.id && model.kind === ui.kind) : null;
    const marketCredentials = oldModel?.credential_source === 'market-session';
    const keepsKey = !marketCredentials && oldModel?.api_key_set && oldModel.provider_id === editor.draft.provider_id;
    return `<section class="mm-editor" aria-labelledby="mmEditorTitle"><header><div><span class="mm-eyebrow">${info.title}</span><h3 id="mmEditorTitle">${editor.id ? '编辑模型配置' : '添加模型'}</h3></div><button type="button" class="mm-icon-button" data-mm-action="cancel-edit" aria-label="关闭模型表单"${disabled()}>${icon('close','ui-icon-sm')}</button></header>
      <p class="mm-protocol-note">${esc(provider?.description || '请选择服务商并填写模型配置。')}</p>
      <form id="mmModelForm" autocomplete="off"><div class="mm-form-grid">
        ${input('name', '显示名称', { max: 80, placeholder: `例如：我的${info.title}` })}
        <label class="mm-field"><span>服务商 <em>*</em></span><select name="provider_id" required${disabled()}>${providers.map(item => `<option value="${esc(item.id)}"${item.id === editor.draft.provider_id ? ' selected' : ''}>${esc(item.name)}</option>`).join('')}</select></label>
        ${input('model_id', '模型 ID', { max: 160, wide: true, placeholder: '填写模型名称或推理接入点 ID' })}
        ${input('base_url', 'Base URL', { type: 'url', wide: true, placeholder: 'https://…', hint: '完整请求地址由 Base URL 与下方 API 路径组合。' })}
        ${input('api_path', ui.kind === 'video' ? '提交路径（POST）' : 'API 路径', { wide: true, placeholder: provider?.defaults.api_path || '/', hint: ui.kind==='text'?'支持 /chat/completions 和 /responses，请按服务商文档填写。':'' })}
        ${ui.kind === 'text' ? `<label class="mm-field mm-wide"><span>思考模式</span><select name="enable_thinking"${disabled()}><option value=""${editor.draft.enable_thinking==null?' selected':''}>由模型决定</option><option value="true"${editor.draft.enable_thinking===true?' selected':''}>启用</option><option value="false"${editor.draft.enable_thinking===false?' selected':''}>关闭</option></select><small>仅在服务商支持 enable_thinking 时指定，千问可选择启用。</small></label>` : ''}
        ${ui.kind === 'video' ? input('query_path', '任务查询路径（GET）', { wide: true, placeholder: provider?.defaults.query_path || '/tasks/{task_id}', hint: '使用 {task_id} 表示提交接口返回的任务 ID；查询与提交共用 Base URL 和 API Key。' }) : ''}
        ${['text', 'audio'].includes(ui.kind) ? input('timeout_seconds', '请求超时（秒）', { type: 'number', max: 4, wide: true, hint: ui.kind === 'text' ? '5～900 秒，默认 300 秒。启用思考模式或生成长剧本时，建议设为 900 秒（15 分钟）。超时后不会自动重新请求。' : '5～900 秒，默认 300 秒。长音频需要更多生成时间。' }) : ''}
        ${input('api_key', 'API Key', { type: 'password', required: false, wide: true, max: 500, placeholder: marketCredentials ? '留空保留现有独立入口密钥' : keepsKey ? '已配置，留空保留现有密钥' : '粘贴服务商提供的 API Key', hint: marketCredentials ? '当前集市会话的临时凭据不会保存到此处。填写此项会更新独立入口使用的密钥。' : keepsKey ? `当前密钥：${oldModel.api_key_hint || '已配置'}。密钥仅保存在服务端。` : '可以先保存配置；切换服务商后需填写对应服务商的 API Key。' })}
      </div><div class="mm-form-error" role="alert"${editor.error ? '' : ' hidden'}>${esc(editor.error || '')}</div><footer><span>${editor.id ? '保存不会自动切换默认模型' : '新增后可在模型卡片中设为默认'}</span><button type="button" class="mm-button" data-mm-action="cancel-edit"${disabled()}>取消</button><button type="submit" class="mm-button mm-primary"${disabled()}>${ui.busy ? '保存中…' : '保存配置'}</button></footer></form>
    </section>`;
  }

  function render() {
    host ||= document.querySelector('#modelManagerContent');
    if (!host) return;
    const info = KINDS[ui.kind], models = modelsFor(ui.kind), current = activeFor(ui.kind), editor = ui.editors[ui.kind];
    host.innerHTML = `<div class="mm-manager" data-current-kind="${ui.kind}" aria-busy="${ui.busy || ui.loading}">
      <div class="mm-category-tabs" role="tablist" aria-label="模型类型">${Object.entries(KINDS).map(([kind, value]) => `<button type="button" role="tab" aria-selected="${ui.kind === kind}" class="${ui.kind === kind ? 'is-active' : ''}" data-mm-kind="${kind}"${disabled()}><span class="mm-tab-icon">${icon(value.icon)}</span><div><strong>${value.title}</strong><small>${value.detail}</small></div><b>${modelsFor(kind).length}</b></button>`).join('')}</div>
      <div class="mm-category-heading"><div><h2>${info.title}</h2><p>${ui.loaded&&!ui.canManage?(models.some(model=>model.can_activate)?'你可以切换个人默认模型，也可以在视频生成面板为每个节点选择模型。':'模型和 API Key 由管理员统一配置，你可以在创作时选择使用。'):info.detail}</p></div><div><button type="button" class="mm-button" data-mm-action="refresh"${disabled()}>刷新</button>${ui.canManage?`<button type="button" class="mm-button mm-primary" data-mm-action="add"${disabled()}>＋ 添加模型</button>`:''}</div></div>
      ${ui.error ? `<div class="mm-error" role="alert">${esc(ui.error)}</div>` : ''}
      ${ui.notice ? `<div class="mm-notice" role="status">${esc(ui.notice)}</div>` : ''}
      ${current ? `<div class="mm-active-summary"><span class="mm-summary-mark">${icon('check','ui-icon-sm')}</span><div><span>当前默认模型</span><strong>${esc(current.name)}</strong></div><p>用于${info.detail.replaceAll('、', ' / ')}<small>${current.credential_source === 'market-session' ? '集市会话使用临时凭据，实际可用性以服务商响应为准。' : '“已配置”表示已保存密钥，实际可用性以服务商响应为准。'}</small></p></div>` : ui.loaded ? '<div class="mm-no-default">尚未选择默认模型，请添加配置并设为默认。</div>' : ''}
      ${editor && ui.canManage ? editorHtml(editor) : ''}
      ${ui.loading?loadingMarkup('正在读取模型配置…',ui.loaded):ui.busy?loadingMarkup('正在更新模型配置…',true):''}
      ${models.length ? `<div class="mm-model-grid">${models.map(card).join('')}</div>` : !ui.loading&&!ui.error ? '<div class="mm-empty"><span>＋</span><h3>还没有模型配置</h3><p>添加此类型的模型，将用于对应的创作功能。</p></div>' : ''}
    </div>`;
    bind();
  }

  async function refresh() {
    const sequence = ++loadSequence;
    ui.loading = true;
    render();
    try {
      const result = await api('/api/model-registry');
      if (sequence !== loadSequence) return;
      ui.models = (Array.isArray(result) ? result : result.models || []).filter(model => KINDS[model.kind]);
      ui.providers = result.providers || {};
      ui.canManage = result.can_manage === true;
      if (!ui.canManage) { ui.editors = {}; ui.deleteId = ''; }
      ui.loaded = true;
      ui.error = '';
    } catch (error) {
      if (sequence === loadSequence) ui.error = errorMessage(error);
    } finally {
      if (sequence === loadSequence) { ui.loading = false; render(); }
    }
  }

  async function open(kind = 'text', options = {}) {
    ++openSequence;
    host = options.host || document.querySelector('#modelManagerContent');
    if (!KINDS[kind]) kind = 'text';
    ui.kind = kind;
    ui.deleteId = '';
    ui.notice = '';
    render();
    await refresh();
  }

  function startEditor(model = null) {
    if (!ui.canManage) return;
    const provider = (ui.providers[ui.kind] || []).find(item => item.id === model?.provider_id) || ui.providers[ui.kind]?.[0];
    if (!provider) { ui.error = '服务商配置尚未加载，请刷新后重试。'; render(); return; }
    ui.deleteId = '';
    ui.notice = '';
    ui.editors[ui.kind] = { id: model?.id || '', draft: { name: '', timeout_seconds: 300, ...provider.defaults, provider: provider.name, provider_id: provider.id, ...(model || {}), api_key: '' }, error: '' };
    render();
    host.querySelector('.mm-editor')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    host.querySelector('#mmModelForm input')?.focus({ preventScroll: true });
  }

  async function notifyChanged() {
    try { await ctx.onChanged?.(); }
    catch { ui.notice = '配置已保存，部分页面尚未刷新，可返回创作页面后重试。'; }
  }

  async function saveEditor(form) {
    if (!ui.canManage || ui.busy || !form.reportValidity()) return;
    const kind = ui.kind, editor = ui.editors[kind], sequence = openSequence;
    if (!editor) return;
    const values = Object.fromEntries(new FormData(form));
    const body = editor.id ? {} : { kind };
    for (const key of ['name', 'provider_id', 'model_id', 'base_url', 'api_path']) body[key] = String(values[key] || '').trim();
    body.provider = editor.draft.provider;
    if (kind === 'video') body.query_path = String(values.query_path || '').trim();
    if (kind === 'text' || kind === 'audio') body.timeout_seconds = Number(values.timeout_seconds || 300);
    if (kind === 'text') body.enable_thinking = values.enable_thinking === '' ? null : values.enable_thinking === 'true';
    const apiKey = String(values.api_key || '').trim();
    if (apiKey) body.api_key = apiKey;
    if (!body.api_path.startsWith('/')) { editor.error = 'API 路径请以 / 开头。'; render(); return; }
    if (kind === 'video' && (!body.query_path.startsWith('/') || body.query_path.split('{task_id}').length !== 2)) { editor.error = '查询路径请以 / 开头，并包含一个 {task_id}。'; render(); return; }
    let parsedUrl;
    try { parsedUrl = new URL(body.base_url); }
    catch { editor.error = '请填写完整的 HTTP 或 HTTPS 服务地址。'; render(); return; }
    if (!['http:', 'https:'].includes(parsedUrl.protocol) || parsedUrl.username || parsedUrl.password) { editor.error = '请填写不含用户名和密码的 HTTP 或 HTTPS 服务地址。'; render(); return; }
    ui.busy = true;
    editor.error = '';
    render();
    let saved = false;
    try {
      await api(editor.id ? endpoint({ kind, id: editor.id }) : '/api/model-registry', json(body, editor.id ? 'PUT' : 'POST'));
      saved = true;
      editor.draft.api_key = '';
      if (ui.editors[kind] === editor) delete ui.editors[kind];
      await notifyChanged();
      if (sequence === openSequence && kind === ui.kind) ui.notice = editor.id ? '模型配置已保存。' : '模型已添加，可在卡片中设为默认。';
      toast?.(editor.id ? '模型配置已保存' : '模型已添加');
    } catch (error) {
      if (ui.editors[kind] === editor) editor.error = errorMessage(error);
    } finally {
      ui.busy = false;
      if (saved) await refresh(); else render();
    }
  }

  async function changeModel(model, action) {
    if (!model || ui.busy || (action === 'activate' ? !ui.canManage && !model.can_activate : !ui.canManage)) return;
    const sequence = openSequence, kind = ui.kind;
    ui.busy = true;
    ui.error = '';
    ui.notice = '';
    render();
    let changed = false;
    try {
      await api(endpoint(model) + (action === 'activate' ? '/activate' : ''), action === 'activate' ? json({}) : { method: 'DELETE' });
      changed = true;
      ui.deleteId = '';
      if (action === 'delete' && ui.editors[kind]?.id === model.id) delete ui.editors[kind];
      await notifyChanged();
      if (sequence === openSequence && kind === ui.kind) ui.notice = action === 'activate' ? `已将「${model.name}」设为默认模型。` : '模型配置已删除。';
      toast?.(action === 'activate' ? '默认模型已切换' : '模型配置已删除');
    } catch (error) {
      if (sequence === openSequence && kind === ui.kind) ui.error = errorMessage(error);
    } finally {
      ui.busy = false;
      if (changed) await refresh(); else render();
    }
  }

  function bind() {
    host.querySelectorAll('[data-mm-kind]').forEach(button => { button.onclick = () => {
      if (ui.busy) return;
      ui.kind = button.dataset.mmKind;
      ui.deleteId = '';
      ui.error = '';
      ui.notice = '';
      render();
    }; });
    host.querySelectorAll('[data-mm-action]').forEach(button => { button.onclick = () => {
      if (ui.busy) return;
      const action = button.dataset.mmAction, model = find(button.dataset.mmId);
      if (action === 'add') startEditor();
      else if (action === 'edit' && model) startEditor(model);
      else if (action === 'cancel-edit') { delete ui.editors[ui.kind]; render(); }
      else if (action === 'refresh') refresh();
      else if (action === 'delete' && model && !model.is_builtin) { ui.deleteId = model.id; render(); }
      else if (action === 'cancel-delete') { ui.deleteId = ''; render(); }
      else if (action === 'confirm-delete' && model && !model.is_builtin) changeModel(model, 'delete');
      else if (action === 'activate' && model && !model.is_default) changeModel(model, 'activate');
    }; });
    const form = host.querySelector('#mmModelForm');
    if (form) {
      form.oninput = event => { const editor = ui.editors[ui.kind]; if (editor && event.target.name) editor.draft[event.target.name] = event.target.name==='enable_thinking'?(event.target.value===''?null:event.target.value==='true'):event.target.value; };
      form.querySelector('[name="provider_id"]').onchange = event => {
        const editor = ui.editors[ui.kind];
        const provider = (ui.providers[ui.kind] || []).find(item => item.id === event.target.value);
        if (!editor || !provider) return;
        const values = Object.fromEntries(new FormData(form));
        editor.draft = { ...editor.draft, ...values, ...provider.defaults, provider_id: provider.id, provider: provider.name, api_key: '', enable_thinking:null };
        editor.error = '';
        render();
        host.querySelector('[name="provider_id"]')?.focus();
      };
      form.onsubmit = event => { event.preventDefault(); saveEditor(form); };
    }
  }

  function close() {
    ++openSequence;
    ++loadSequence;
    ui.loading = false;
    for (const editor of Object.values(ui.editors)) editor.draft.api_key = '';
  }

  return { open, render, refresh, close };
}
