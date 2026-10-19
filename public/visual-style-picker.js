let catalogPromise;
const catalog = api => catalogPromise ||= api('/api/visual-styles').catch(error => { catalogPromise = null; throw error; });

export function createVisualStylePicker({ api, escapeHtml: esc, icon, onError = () => {} }) {
  let panel, anchor, current, styles = [], category = '全部', query = '', revision = 0;
  const buttons = new Set();
  const motion = () => !matchMedia('(prefers-reduced-motion: reduce)').matches;
  const name = value => !value ? '风格' : value.id === 'custom' ? '自定义风格' : styles.find(s => s.id === value.id)?.display_name || '已选风格';
  function updateSymbol(entry, value) {
    const preview = styles.find(s => s.id === value?.id)?.preview_url;
    const key = preview || (value?.id === 'custom' ? 'custom' : 'none');
    if (entry.symbolKey === key) return;
    const symbol = entry.trigger.querySelector('.visual-style-symbol');
    const animate = entry.symbolKey !== undefined && motion() && !!symbol.animate;
    entry.symbolKey = key;
    const version = entry.symbolVersion = (entry.symbolVersion || 0) + 1;
    symbol.getAnimations?.().forEach(animation => animation.cancel());
    const replace = () => {
      if (version !== entry.symbolVersion || !entry.trigger.isConnected) return;
      symbol.innerHTML = preview ? `<img src="${esc(preview)}" alt="" draggable="false" decoding="async">` : icon(key === 'custom' ? 'pencil' : 'visualStyle');
      symbol.classList.toggle('has-preview', !!preview);
      if (animate) symbol.animate([
        { opacity: 0, transform: 'scale(.25)' },
        { opacity: 1, transform: 'scale(1.12)', offset: .72 },
        { opacity: 1, transform: 'scale(1)' },
      ], { duration: 280, easing: 'cubic-bezier(.22,1,.36,1)' });
    };
    if (animate) symbol.animate([{ opacity: 1, transform: 'scale(1)' }, { opacity: 0, transform: 'scale(.25)' }],
      { duration: 120, easing: 'ease-in' }).finished.then(replace).catch(() => {});
    else replace();
  }
  function refresh() {
    for (const entry of buttons) {
      if (!entry.trigger.isConnected) { buttons.delete(entry); continue; }
      const value = entry.getValue();
      entry.trigger.querySelector('.visual-style-label').textContent = name(value);
      entry.trigger.classList.toggle('has-style', !!value);
      entry.trigger.title = value ? name(value) : '选择视觉风格';
      entry.trigger.setAttribute('aria-label', value ? `视觉风格：${name(value)}` : '选择视觉风格');
      updateSymbol(entry, value);
    }
  }
  function close(restore = false) {
    if (!anchor) { revision++; return; }
    const old = anchor; ++revision;
    anchor = null; current = null;
    old.setAttribute('aria-expanded', 'false');
    if (restore && old.isConnected) old.focus({ preventScroll: true });
    if (panel.matches(':popover-open')) panel.hidePopover();
  }
  function position() {
    if (!anchor) return;
    const rect = anchor.getBoundingClientRect(), height = panel.offsetHeight, width = panel.offsetWidth;
    const left = Math.max(16, Math.min(innerWidth - width - 16, rect.left + rect.width / 2 - width / 2));
    const top = rect.bottom + height + 12 <= innerHeight ? rect.bottom + 10 : Math.max(16, rect.top - height - 10);
    panel.style.left = left + 'px'; panel.style.top = Math.min(top, Math.max(16, innerHeight - height - 16)) + 'px';
  }
  function drawGrid() {
    const selected = current?.getValue();
    const matches = styles.filter(s => (category === '全部' || s.category === category) &&
      [s.display_name, s.source_name, s.summary, ...s.aliases].join(' ').toLowerCase().includes(query.toLowerCase()));
    const special = category === '全部' && !query;
    panel.querySelector('.style-picker-grid').innerHTML =
      (special ? `<button type="button" class="style-picker-card" data-style="" aria-pressed="${!selected}"><span class="style-picker-empty">∅</span><span>无风格</span></button><button type="button" class="style-picker-card" data-style="custom" aria-pressed="${selected?.id === 'custom'}"><span class="style-picker-empty">${icon('pencil')}</span><span>自定义风格提示词</span></button>` : '') +
      matches.map(s => `<button type="button" class="style-picker-card" data-style="${esc(s.id)}" aria-pressed="${selected?.id === s.id}" title="${esc(s.display_name + ' · ' + s.summary)}"><span class="style-picker-image"><img src="${esc(s.preview_url)}" alt="" loading="lazy" decoding="async" draggable="false"></span><span>${esc(s.display_name)}</span></button>`).join('') +
      (!matches.length && !special ? '<p class="style-picker-no-results">没有找到相关风格</p>' : '');
    panel.querySelectorAll('[data-style-category]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.styleCategory === category)));
  }
  function drawCategories() {
    const available = [...new Set(styles.map(style => style.category))];
    const preferred = ['真人', '2D', '3D', '建筑'];
    const categories = ['全部', ...preferred.filter(c => available.includes(c)), ...available.filter(c => !preferred.includes(c))];
    panel.querySelector('nav').innerHTML = categories.map(c => `<button type="button" data-style-category="${esc(c)}" aria-pressed="${c === category}">${esc(c)}</button>`).join('');
  }
  async function choose(value) {
    if (!current || panel.dataset.busy === 'true') return;
    const entry = current;
    panel.dataset.busy = 'true'; panel.setAttribute('aria-busy', 'true');
    try { await entry.onChange(value); refresh(); if (current === entry) close(true); }
    catch (error) { onError(error.message); }
    finally { delete panel.dataset.busy; panel.removeAttribute('aria-busy'); }
  }
  function initialize() {
    if (panel) return;
    panel = document.createElement('div'); panel.className = 'home-preference-panel visual-style-picker';
    panel.setAttribute('popover', 'manual'); panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-label', '选择视觉风格');
    panel.innerHTML = `<div class="style-picker-search">${icon('search')}<input type="search" aria-label="搜索风格" placeholder="搜索风格"><button type="button" data-style-close aria-label="关闭风格选择">${icon('close')}</button></div><div class="style-picker-body"><nav aria-label="风格分类"></nav><div class="style-picker-grid"></div><form class="style-picker-custom" hidden><label>自定义风格提示词<textarea maxlength="1500" rows="7" placeholder="描述媒介、造型、材质、光影与色彩，例如：纸雕质感，柔和侧光，低饱和配色…"></textarea></label><small>主体与场景请在生成描述中填写。</small><div><button type="button" data-style-back>返回风格库</button><button type="submit">使用此风格</button></div></form></div>`;
    document.body.append(panel);
    panel.querySelector('input').oninput = event => { query = event.target.value.trim(); drawGrid(); };
    panel.onclick = event => {
      if (event.target.closest('[data-style-close]')) return close(true);
      if (!current || panel.dataset.busy === 'true') return;
      const cat = event.target.closest('[data-style-category]');
      if (cat || event.target.closest('[data-style-back]')) {
        panel.querySelector('form').hidden = true; panel.querySelector('.style-picker-grid').hidden = false;
        if (cat) category = cat.dataset.styleCategory;
        drawGrid(); panel.querySelector('.style-picker-grid').scrollTop = 0; return;
      }
      const card = event.target.closest('[data-style]'); if (!card) return;
      if (card.dataset.style === 'custom') {
        panel.querySelector('.style-picker-grid').hidden = true; panel.querySelector('form').hidden = false;
        const text = panel.querySelector('textarea'); text.value = current.getValue()?.custom_prompt || ''; text.focus();
      } else choose(card.dataset.style ? { id: card.dataset.style } : null);
    };
    panel.querySelector('form').onsubmit = event => {
      event.preventDefault(); const custom_prompt = panel.querySelector('textarea').value.trim();
      if (!custom_prompt) return onError('请填写自定义风格提示词');
      choose({ id: 'custom', custom_prompt });
    };
    panel.addEventListener('keydown', event => {
      if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close(true);}
      if(event.key==='Tab'){
        const focusable=[...panel.querySelectorAll('button,input,textarea')].filter(el=>!el.disabled&&el.getClientRects().length);
        if(event.shiftKey&&document.activeElement===focusable[0]||!event.shiftKey&&document.activeElement===focusable.at(-1)){
          event.preventDefault();close(true);
        }
      }
    });
    document.addEventListener('pointerdown', event => { if (anchor && !panel.contains(event.target) && !anchor.contains(event.target)) close(); });
    document.addEventListener('keydown', event => { if (anchor && event.key === 'Escape') { event.preventDefault(); close(true); } });
    // Mouse focus can briefly land on body between blur and focus. Closing on
    // focusout here discards current before the category/card click arrives.
    // Outside pointer, Escape and the Tab boundary own dismissal instead.
    document.addEventListener('scroll', event => { if (anchor && !panel.contains(event.target)) close(); }, true);
    window.addEventListener('resize', () => close());
    new MutationObserver(() => { if (anchor && (!anchor.isConnected || !anchor.getClientRects().length)) close(); }).observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['hidden', 'class'] });
  }
  function button(container, { getValue, onChange, disabled = false }) {
    const trigger = document.createElement('button'); trigger.type = 'button'; trigger.className = 'visual-style-trigger'; trigger.disabled = disabled;
    trigger.setAttribute('aria-haspopup', 'dialog'); trigger.setAttribute('aria-expanded', 'false');
    trigger.innerHTML = `<span class="visual-style-symbol" aria-hidden="true">${icon('visualStyle')}</span><span class="visual-style-label">${esc(name(getValue()))}</span>${icon('chevronDown')}`;
    const entry = { trigger, getValue, onChange }; buttons.add(entry); container.append(trigger);
    catalog(api).then(data => { styles = data.styles; refresh(); }).catch(() => {});
    trigger.onclick = async () => {
      if (anchor === trigger) return close(true);
      close(); initialize(); const token = ++revision;
      try {
        const data = await catalog(api); if (token !== revision || !trigger.isConnected) return;
        styles = data.styles; anchor = trigger; current = entry; category = '全部'; query = '';
        panel.querySelector('input').value = '';
        panel.querySelector('form').hidden = true; panel.querySelector('.style-picker-grid').hidden = false;
        trigger.setAttribute('aria-expanded', 'true'); drawCategories(); drawGrid();
        panel.showPopover(); position();
        panel.querySelector('input').focus({ preventScroll: true });
      } catch (error) { onError(error.message); }
    };
    return trigger;
  }
  return { button, close, refresh };
}
