import { icon } from './iconography.js?v=library-toggle-1';

// The media element remains the playback engine; all visible controls are ours.
const glyph = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${({play:'<path d="m9 5 11 7-11 7Z" fill="currentColor" stroke="none"/>',pause:'<path d="M8 5v14M16 5v14"/>',volume:'<path d="m11 5-6 4H2v6h3l6 4ZM15 8a6 6 0 0 1 0 8M18 5a10 10 0 0 1 0 14"/>',muted:'<path d="m11 5-6 4H2v6h3l6 4ZM16 9l6 6M22 9l-6 6"/>',fullscreen:'<path d="M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5"/>'})[name]}</svg>`;
export function playerTime(value, audio = false) {
  const seconds = Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds / 60) % 60;
  return `${hours ? hours + ':' : ''}${String(minutes).padStart(audio || hours ? 2 : 1, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}

export function installNodePlayers(root = document) {
  const active = new Map();
  function enhance(media) {
    if (active.has(media) || media.closest('.node-player')) return;
    const audio = media.tagName === 'AUDIO';
    const player = document.createElement('div');
    player.className = `node-player ${audio ? 'node-player-audio' : 'node-player-video media-node-interactive'}`;
    if(!audio&&media.hasAttribute('data-conversation-video'))player.classList.add('node-player-conversation');
    player.setAttribute('role', 'group');
    player.setAttribute('aria-label', media.getAttribute('aria-label') || (audio ? '音频播放器' : '视频播放器'));
    if (audio) {
      const surface = media.closest('.home-results,.al-preview-media,.ag-result');
      if (surface) {
        player.classList.add('node-player-surface');
        surface.querySelector('.ag-wave')?.remove();
      }
      if (media.previousElementSibling?.matches('.media-node-audio-art,.al-audio-art')) media.previousElementSibling.remove();
    }
    media.before(player); player.append(media); media.controls = false; media.preload = 'metadata';
    player.insertAdjacentHTML('beforeend', `${audio ? '<div class="np-wave"><svg viewBox="0 0 600 160" preserveAspectRatio="none" aria-hidden="true"></svg><i class="np-cursor"></i></div>' : ''}<div class="np-controls"><button type="button" class="np-toggle" aria-label="播放">${glyph('play')}</button><span class="np-current">${playerTime(0,audio)}</span>${audio ? '<span class="np-divider">/</span>' : ''}<input class="np-seek" type="range" min="0" max="1000" value="0" step="1" aria-label="播放进度" disabled><span class="np-duration">${playerTime(0,audio)}</span>${audio ? '' : `<button type="button" class="np-volume" aria-label="静音">${glyph('volume')}</button><button type="button" class="np-fullscreen" aria-label="全屏">${glyph('fullscreen')}</button>`}</div><span class="np-status" role="status"></span>`);
    const find = selector => player.querySelector(selector);
    const seek = find('.np-seek'), toggle = find('.np-toggle'), status = find('.np-status');
    const abort = new AbortController();
    const on = (el, event, handler) => el.addEventListener(event, handler, {signal:abort.signal});
    const librarySource=media.closest('[data-home-job],.agent-media-card')?.querySelector('[data-player-library]');
    if(!audio&&librarySource){
      const library=document.createElement('button');
      library.type='button';library.className='np-library';
      library.innerHTML=icon('library')+`<span class="np-library-check">${icon('check')}</span>`;
      player.append(library);
      const syncLibrary=()=>{
        const saved=librarySource.dataset.librarySaved==='true',busy=librarySource.getAttribute('aria-busy')==='true';
        library.dataset.saved=String(saved);library.disabled=librarySource.disabled;
        library.title=busy?'正在保存…':saved?'已保存至资产库':'保存至资产库';
        library.setAttribute('aria-label',library.title);library.setAttribute('aria-busy',String(busy));
      };
      on(library,'click',()=>{if(!library.disabled)librarySource.click();});
      const libraryObserver=new MutationObserver(syncLibrary);
      libraryObserver.observe(librarySource,{attributes:true,attributeFilter:['disabled','data-library-saved','aria-busy']});
      abort.signal.addEventListener('abort',()=>libraryObserver.disconnect(),{once:true});
      syncLibrary();
    }
    const duration = () => Number.isFinite(media.duration) && media.duration > 0 ? media.duration : 0;
    const syncProgress = () => {
      const progress = duration() ? Math.max(0, Math.min(100, media.currentTime / duration() * 100)) : 0;
      player.style.setProperty('--np-progress', `${progress}%`);
      seek.value = String(progress * 10);
      return progress;
    };
    // timeupdate can fire only a few times per second. Keep the playhead
    // on the display clock, reading media time each frame so seeks and stalls
    // never accumulate drift. Only the progress style changes on this path.
    let progressFrame = null;
    let clockTime = media.currentTime, clockSince = performance.now();
    let silentRecovery = false, recoveryAttempted = false;
    const recoveryMessage = '声音输出未就绪，已静音播放；可点击声音按钮重试';
    const resetClock = () => {clockTime=media.currentTime;clockSince=performance.now();};
    const checkClock = () => {
      const now=performance.now();
      if(Math.abs(media.currentTime-clockTime)>.01){clockTime=media.currentTime;clockSince=now;return;}
      // A ready, unpaused video can still freeze if its audio output cannot
      // start. Recover only after observing a stalled media clock, never by
      // pretending that the timeline advanced. Keep this visible to the user.
      if(!audio && !media.muted && media.volume>0 && !recoveryAttempted && now-clockSince>1800){
        recoveryAttempted=true;silentRecovery=true;media.muted=true;
        status.textContent=recoveryMessage;resetClock();
      } else if(!audio && !media.muted && recoveryAttempted && now-clockSince>1800){
        status.textContent='声音输出异常，请检查系统音频设备，或静音继续播放';
      }
    };
    const stopProgress = () => {
      if (progressFrame !== null) cancelAnimationFrame(progressFrame);
      progressFrame = null;
    };
    const startProgress = () => {
      if (progressFrame !== null || media.paused || media.ended || media.seeking || media.readyState < 3 || document.hidden || abort.signal.aborted) return;
      const frame = () => {
        progressFrame = null;
        syncProgress();
        checkClock();
        startProgress();
      };
      progressFrame = requestAnimationFrame(frame);
    };
    for (const event of ['playing','seeked','canplay']) on(media,event,() => {resetClock();startProgress();});
    for (const event of ['pause','ended','waiting','seeking','error','emptied']) on(media,event,stopProgress);
    on(document,'visibilitychange',() => {stopProgress();syncProgress();resetClock();if(!document.hidden)startProgress();});
    abort.signal.addEventListener('abort',stopProgress,{once:true});
    const sync = () => {
      if(!audio&&media.videoWidth&&media.videoHeight)player.style.setProperty('--np-video-ratio',String(media.videoWidth/media.videoHeight));
      const progress = syncProgress();
      seek.value = String(progress * 10); seek.disabled = !duration();
      seek.setAttribute('aria-valuetext', `${playerTime(media.currentTime,audio)} / ${playerTime(media.duration,audio)}`);
      find('.np-current').textContent = playerTime(media.currentTime,audio);
      find('.np-duration').textContent = playerTime(media.duration,audio);
      toggle.innerHTML = glyph(media.paused ? 'play' : 'pause');
      toggle.setAttribute('aria-label', media.paused ? '播放' : '暂停');
      player.classList.toggle('is-playing', !media.paused);
    };
    on(toggle,'click',async () => {
      if (!media.paused) {media.pause();return;}
      status.textContent = silentRecovery ? recoveryMessage : '';
      recoveryAttempted=false;resetClock();
      try { if (media.ended || (duration() && media.currentTime >= duration())) media.currentTime = 0; await media.play(); }
      catch {status.textContent = '无法播放，请检查文件或更换素材';}
    });
    on(seek,'input',() => {if(duration())media.currentTime = Number(seek.value) / 1000 * duration();sync();});
    for (const event of ['loadedmetadata','durationchange','timeupdate','play','pause','ended','seeked']) on(media,event,sync);
    on(media,'waiting',() => {status.textContent='正在缓冲…';});
    on(media,'playing',() => {status.textContent=silentRecovery?recoveryMessage:'';});
    on(media,'canplay',() => {status.textContent=silentRecovery?recoveryMessage:'';});
    on(media,'error',() => {status.textContent='无法播放，请检查文件或更换素材';toggle.disabled=true;seek.disabled=true;});
    if (!audio) {
      const volume = find('.np-volume');
      const syncVolume = () => {volume.innerHTML=glyph(media.muted || !media.volume?'muted':'volume');volume.setAttribute('aria-label',media.muted?'取消静音':'静音');};
      on(volume,'click',() => {
        media.muted=!media.muted;silentRecovery=false;status.textContent='';resetClock();
      });on(media,'volumechange',syncVolume);syncVolume();
      on(find('.np-fullscreen'),'click',async () => {
        try {if(document.fullscreenElement===player)await document.exitFullscreen();else if(player.requestFullscreen)await player.requestFullscreen();else if(media.webkitEnterFullscreen)media.webkitEnterFullscreen();}
        catch {status.textContent='当前浏览器无法进入全屏';}
      });
      on(player,'click',event => {
        // Reuse the play button for fullscreen picture and letterbox clicks.
        // Controls keep their own actions; canvas gestures stay outside fullscreen.
        if(document.fullscreenElement===player && (event.target===media || event.target===player))toggle.click();
      });
      on(player,'dblclick',async event => {
        if(document.fullscreenElement!==player || (event.target!==media && event.target!==player))return;
        event.preventDefault();
        try {await document.exitFullscreen();}
        catch {status.textContent='当前浏览器无法退出全屏';}
      });
    } else {
      const wave = find('.np-wave'), svg = wave.querySelector('svg');
      // A flat baseline until real samples are decoded; never invent a waveform.
      svg.innerHTML = '<path d="M12 80H588" stroke="currentColor" stroke-dasharray="2 6"/>';
      const cursor = find('.np-cursor');
      cursor.title = '拖动红线调整播放进度';
      on(cursor,'pointerdown',event => {
        if(!duration() || event.button!==0)return;
        event.preventDefault();event.stopPropagation();
        cursor.setPointerCapture(event.pointerId);
        const box=wave.getBoundingClientRect(), scale=box.width/wave.clientWidth;
        const start=box.left+8*scale, width=box.width-16*scale;
        const offset=event.clientX-(start+media.currentTime/duration()*width);
        const move=e=>{
          if(e.pointerId!==event.pointerId)return;
          media.currentTime=Math.max(0,Math.min(1,(e.clientX-offset-start)/width))*duration();sync();
        };
        const end=e=>{
          if(e.pointerId!==event.pointerId)return;
          cursor.removeEventListener('pointermove',move);cursor.removeEventListener('pointerup',end);
          cursor.removeEventListener('pointercancel',end);cursor.removeEventListener('lostpointercapture',end);
          if(cursor.hasPointerCapture(event.pointerId))cursor.releasePointerCapture(event.pointerId);
        };
        on(cursor,'pointermove',move);on(cursor,'pointerup',end);on(cursor,'pointercancel',end);on(cursor,'lostpointercapture',end);
      });
      const visibility = new IntersectionObserver(entries => {if(entries.some(entry=>entry.isIntersecting)){visibility.disconnect();decodeWave();}});
      visibility.observe(player);
      async function decodeWave() {
        let context;
        try {
          const response=await fetch(media.currentSrc || media.src,{signal:abort.signal});
          if(!response.ok)throw new Error('waveform');
          // Bound waveform work for large files; playback is independent.
          if(Number(response.headers.get('content-length'))>32*1024*1024)return;
          const buffer=await response.arrayBuffer();if(buffer.byteLength>32*1024*1024)return;
          context=new AudioContext();const decoded=await context.decodeAudioData(buffer);
          if(abort.signal.aborted)return;
          const samples=decoded.getChannelData(0), count=96, stride=Math.max(1,Math.floor(samples.length/count));
          const peaks=Array.from({length:count},(_,i)=>{let sum=0,n=0;for(let j=i*stride;j<Math.min(samples.length,(i+1)*stride);j+=Math.max(1,Math.floor(stride/256))){sum+=samples[j]*samples[j];n++;}return Math.sqrt(sum/Math.max(1,n));});
          const max=Math.max(...peaks,.001);
          svg.innerHTML=peaks.map((peak,i)=>{const h=Math.max(2,peak/max*100);return `<path d="M${12+i*6} ${80-h/2}v${h}"/>`;}).join('');
        }catch {wave.title='波形暂不可用，仍可播放和拖动进度';}
        finally {await context?.close();}

      }
      abort.signal.addEventListener('abort',()=>visibility.disconnect(),{once:true});
    }
    // Audio surfaces remain canvas drag targets; only the playhead and actual
    // controls own their gestures. Video keeps its existing gesture surface.
    for(const event of ['pointerdown','mousedown','click','dblclick','wheel','keydown'])on(player,event,e=>{
      if(!audio || e.target.closest('button,input,.np-cursor'))e.stopPropagation();
    });
    active.set(media,()=>{media.pause();abort.abort();});sync();startProgress();
  }
  function scan() {
    for(const [media,dispose] of active)if(!root.contains(media)){dispose();active.delete(media);}
    root.querySelectorAll('.node video,.node audio,.media-detail-preview video,.media-detail-preview audio,video[data-conversation-video],.home-results audio,.al-preview-media audio,.ag-result audio,.agent-media-card audio').forEach(enhance);
  }
  const observer=new MutationObserver(records=>{
    // Time labels and button glyphs change during playback; they do not add players.
    if(records.some(record=>!record.target.closest?.('.node-player') && [...record.addedNodes,...record.removedNodes].some(node=>node.nodeType===1)))scan();
  });
  observer.observe(root,{childList:true,subtree:true});scan();
  return ()=>{observer.disconnect();for(const dispose of active.values())dispose();active.clear();};
}
