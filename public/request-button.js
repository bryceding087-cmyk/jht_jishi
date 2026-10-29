const ARROW='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5m-6 6 6-6 6 6"/></svg>';
const STOP='<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>';
const STATES=new Set(['idle','running','stopping']);

// Shared by icon-only send buttons and labeled generation buttons. The caller
// owns task state and must also guard form/Enter submission while a task runs.
export function createRequestButton(button,{onStop,onError,sendLabel,stopLabel='停止生成',stoppingLabel='正在停止',showLabel=false,initialState='idle',sendIcon}={}){
  const original={html:button.innerHTML,type:button.getAttribute('type'),label:button.getAttribute('aria-label'),title:button.getAttribute('title'),disabled:button.disabled};
  sendLabel??=original.label||button.textContent.trim()||'发送消息';
  const glyph=sendIcon||button.querySelector('svg')?.outerHTML||ARROW;
  let state=STATES.has(initialState)?initialState:'idle',idleDisabled=button.disabled,destroyed=false;
  const labels={idle:sendLabel,running:stopLabel,stopping:stoppingLabel};
  button.classList.add('request-button');
  button.innerHTML=`<span class="request-button-icon" aria-hidden="true"><span class="request-button-send">${glyph}</span><span class="request-button-stop">${STOP}</span></span>`;
  let label;
  if(showLabel){label=document.createElement('span');label.className='request-button-label';button.append(label);}
  function setState(next,{disabled}={}){
    if(destroyed)return;
    if(!STATES.has(next))throw new TypeError('Unknown request button state');
    if(disabled!==undefined)idleDisabled=!!disabled;
    state=next;button.dataset.requestState=state;
    button.setAttribute('aria-label',labels[state]);button.title=labels[state];
    button.setAttribute('aria-busy',String(state==='stopping'));
    button.setAttribute('aria-disabled',String(state==='stopping'||state==='idle'&&idleDisabled));
    button.disabled=state==='idle'&&idleDisabled;
    button.type=state==='idle'?(original.type||'submit'):'button';
    if(label)label.textContent=labels[state];
  }
  function click(event){
    if(state==='idle'||destroyed)return;
    event.preventDefault();event.stopImmediatePropagation();
    if(state==='stopping')return;
    setState('stopping');
    try{Promise.resolve(onStop?.(event)).catch(error=>{if(!destroyed)onError?.(error);});}
    catch(error){onError?.(error);}
  }
  button.addEventListener('click',click,true);
  setState(state);
  return {setState,getState:()=>state,destroy(){
    if(destroyed)return;destroyed=true;button.removeEventListener('click',click,true);
    button.classList.remove('request-button');delete button.dataset.requestState;
    button.innerHTML=original.html;button.disabled=original.disabled;
    for(const [key,value] of [['type',original.type],['aria-label',original.label],['title',original.title]]){if(value===null)button.removeAttribute(key);else button.setAttribute(key,value);}
    button.removeAttribute('aria-busy');button.removeAttribute('aria-disabled');
  }};
}
