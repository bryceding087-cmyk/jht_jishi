// Compact parameter buttons share one top-layer panel, outside the scrolling composer.
export function createHomePreferences({root,icon,escapeHtml:esc}) {
  const panel=document.createElement('div');panel.className='home-preference-panel';panel.setAttribute('popover','manual');panel.setAttribute('role','dialog');document.body.append(panel);
  let anchor=null,sections=[],summary=null,composer=null,previousTranslate='';
  function restoreComposer(){if(composer)composer.style.translate=previousTranslate;composer=null;}
  function close(restore=false){if(!anchor)return;const previous=anchor;anchor=null;previous.setAttribute('aria-expanded','false');panel.hidePopover();restoreComposer();if(restore&&previous.isConnected)previous.focus();}
  function position(){
    if(!anchor)return;
    restoreComposer();
    const textMenu=sections.length===1&&!sections[0].ratios&&sections[0].title!=='选择时长';
    panel.classList.toggle('is-text-menu',textMenu);
    let desiredWidth=sections.some(s=>s.ratios&&s.select.options.length>6)?480:340;
    if(textMenu){
      panel.style.width='max-content';
      const textWidth=element=>{const range=document.createRange();range.selectNodeContents(element);return range.getBoundingClientRect().width;};
      const optionWidth=Math.max(0,...[...panel.querySelectorAll('button span')].map(textWidth));
      const titleWidth=Math.max(0,...[...panel.querySelectorAll('h3')].map(textWidth));
      desiredWidth=Math.ceil(Math.max(optionWidth+82,titleWidth+42));
    }
    const width=Math.min(desiredWidth,innerWidth-24);
    panel.classList.toggle('has-many-ratios',sections.some(s=>s.ratios&&s.select.options.length>6));panel.style.width=width+'px';
    let r=anchor.getBoundingClientRect();
    panel.style.left=Math.max(12,Math.min(r.left,innerWidth-width-12))+'px';
    const landing=root.classList.contains('home-landing')&&!root.classList.contains('agent-docked');
    if(!landing){
      panel.style.maxHeight=Math.max(48,Math.min(520,r.top-22))+'px';
      panel.style.top=Math.max(12,r.top-panel.offsetHeight-10)+'px';
      return;
    }
    const wrap=anchor.closest('.agent-composer-wrap');
    // Only the landing composer opens downward; conversation composers stay in place.
    const liftRoom=wrap?Math.max(0,wrap.getBoundingClientRect().top-12):0;
    panel.style.maxHeight=Math.max(48,Math.min(520,innerHeight-r.bottom-22+liftRoom))+'px';
    const lift=Math.min(liftRoom,Math.max(0,r.bottom+10+panel.offsetHeight+12-innerHeight));
    if(wrap&&lift){composer=wrap;previousTranslate=wrap.style.translate;wrap.style.translate=`0 -${lift}px`;r=anchor.getBoundingClientRect();}
    panel.style.left=Math.max(12,Math.min(r.left,innerWidth-width-12))+'px';panel.style.top=(r.bottom+10)+'px';
  }
  function draw(){
    panel.classList.toggle('is-choice-menu',sections.some(section=>section.menu));
    panel.innerHTML=sections.map((section,i)=>`<section><h3>${esc(section.title)}</h3><div class="home-preference-options ${section.ratios?'ratios':''} ${section.title==='选择时长'?'durations':''}" role="group" aria-label="${esc(section.title)}">${Array.from(section.select.options).map(option=>`<button type="button" data-section="${i}" data-value="${esc(option.value)}" aria-pressed="${option.selected}" ${option.disabled?'disabled':''}>${section.ratios&&/^\d+:\d+$/.test(option.value)?`<i class="home-ratio-shape" style="--ratio:${option.value.replace(':','/')}"></i>`:''}<span>${esc(option.textContent)}</span></button>`).join('')}</div></section>`).join('');position();
  }
  panel.addEventListener('click',e=>{const button=e.target.closest('[data-value]');if(!button)return;const index=button.dataset.section,value=button.dataset.value,section=sections[Number(index)];section.select.value=value;section.select.dispatchEvent(new Event('change',{bubbles:true}));if(anchor){anchor.querySelector('span').textContent=summary();draw();panel.querySelector(`[data-section="${index}"][data-value="${CSS.escape(value)}"]`)?.focus({preventScroll:true});}});
  panel.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close(true);}if(['ArrowRight','ArrowLeft','ArrowDown','ArrowUp','Home','End'].includes(e.key)){const buttons=[...panel.querySelectorAll('button:not(:disabled)')],index=buttons.indexOf(document.activeElement);e.preventDefault();buttons[e.key==='Home'?0:e.key==='End'?buttons.length-1:(index+(['ArrowLeft','ArrowUp'].includes(e.key)?buttons.length-1:1))%buttons.length]?.focus();}if(e.key==='Tab'){const buttons=[...panel.querySelectorAll('button:not(:disabled)')];if(e.shiftKey&&document.activeElement===buttons[0]||!e.shiftKey&&document.activeElement===buttons.at(-1)){e.preventDefault();close(true);}}});
  document.addEventListener('pointerdown',e=>{if(anchor&&!panel.contains(e.target)&&!anchor.contains(e.target))close();});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&anchor){e.preventDefault();close(true);}});
  document.addEventListener('scroll',e=>{if(anchor&&!panel.contains(e.target))close();},true);window.addEventListener('resize',close);
  new MutationObserver(()=>{if(anchor&&(!anchor.isConnected||!anchor.getClientRects().length||!root.classList.contains('active-view')))close();}).observe(root,{attributes:true,childList:true,subtree:true,attributeFilter:['class','hidden','data-home-mode','data-home-stage']});
  function button(container,{label,groups,glyph='settings'}){
    groups.forEach(group=>{group.select.hidden=true;group.select.tabIndex=-1;});
    const trigger=document.createElement('button');trigger.type='button';trigger.className='home-preference-trigger';trigger.setAttribute('aria-haspopup','dialog');trigger.setAttribute('aria-expanded','false');trigger.innerHTML=`${icon(glyph)}<span>${esc(label())}</span>${icon('chevronDown')}`;container.append(trigger);
    groups.forEach(group=>group.select.addEventListener('change',()=>{trigger.querySelector('span').textContent=label();}));
    trigger.onclick=()=>{if(anchor===trigger)return close();close();anchor=trigger;sections=groups;summary=label;panel.setAttribute('aria-label',groups.map(g=>g.title).join('与'));trigger.setAttribute('aria-expanded','true');panel.showPopover();draw();panel.querySelector('[aria-pressed=true],button')?.focus({preventScroll:true});};
    return trigger;
  }
  return {button,close};
}
