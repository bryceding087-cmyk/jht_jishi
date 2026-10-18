import assert from 'node:assert/strict';
import { createVideoHoverPreview } from '../public/video-hover-preview.js';

class Target {
  constructor() { this.events = new Map(); }
  addEventListener(name, callback) { this.events.set(name, callback); }
  removeEventListener(name) { this.events.delete(name); }
  emit(name, event = {}) { this.events.get(name)?.(event); }
}
let observer;
globalThis.IntersectionObserver = class {
  constructor(callback) { this.callback = callback; observer = this; }
  observe() {} disconnect() {} takeRecords() {}
  visible(card, isIntersecting) { this.callback([{ target: card, isIntersecting }]); }
};
globalThis.document = new Target(); document.hidden = false;
globalThis.window = new Target();
function card() {
  const element = new Target(), video = new Target();
  Object.assign(video, { dataset: { mediaSrc: '/uploads/video.mp4#t=0.05' }, playing: false, plays: [],
    pause() { this.playing = false; },
    removeAttribute() { delete this.dataset.mediaSrc; },
    play() { return new Promise((resolve, reject) => this.plays.push({
      finish: () => { this.playing = true; resolve(); }, fail: reject,
    })); },
  });
  Object.assign(element, { isConnected: true, getClientRects: () => [1], querySelector: () => video });
  return { element, video };
}
const preview = createVideoHoverPreview(), a = card(), b = card();
preview.bind([a.element, b.element]);
assert.equal(a.video.src, undefined, 'hidden cards must not fetch media');
observer.visible(a.element, true);
assert.equal(a.video.src, '/uploads/video.mp4#t=0.05');
assert.equal(a.video.preload, 'metadata');
assert.equal(a.video.plays.length, 0, 'visibility loads the cover without playing');
a.element.emit('pointerenter', { pointerType: 'touch' });
assert.equal(a.video.plays.length, 0);
a.element.emit('pointerenter', { pointerType: 'mouse' });
assert.equal(a.video.muted, true); assert.equal(a.video.loop, true);
a.element.emit('pointerleave');
a.video.plays[0].finish(); await Promise.resolve();
assert.equal(a.video.playing, false, 'late play completion cannot revive a departed preview');
assert.equal(a.video.currentTime, .05);
a.element.emit('focus'); a.video.plays[1].finish(); await Promise.resolve();
assert.equal(a.video.playing, true);
b.element.emit('pointerenter', { pointerType: 'mouse' });
assert.equal(a.video.playing, false, 'only one preview plays');
b.video.plays[0].finish(); await Promise.resolve();
observer.visible(b.element, false);
assert.equal(b.video.playing, false, 'scrolling off screen stops playback');

a.element.emit('pointerenter', { pointerType: 'mouse' });
a.element.emit('pointerleave');
a.element.emit('pointerenter', { pointerType: 'mouse' });
a.video.plays[2].finish(); await Promise.resolve();
a.video.plays[3].finish(); await Promise.resolve();
assert.equal(a.video.playing, true, 'old completion cannot pause a new hover on the same video');
window.emit('blur'); assert.equal(a.video.playing, false);
a.element.emit('focus'); a.video.plays[4].finish(); await Promise.resolve();
document.hidden = true; document.emit('visibilitychange'); assert.equal(a.video.playing, false);
a.element.emit('focus'); assert.equal(a.video.plays.length, 5, 'hidden tab cannot start playback');
document.hidden = false;
a.element.emit('focus'); a.video.plays[5].fail(new Error('play rejected')); await Promise.resolve();
assert.equal(a.video.playing, false);
a.element.emit('focus'); a.video.plays[6].finish(); await Promise.resolve();
preview.bind([]); assert.equal(a.video.playing, false);
assert.equal(a.element.events.size, 0, 're-render removes old handlers');
console.log('PASS: viewport loading, silent hover/focus preview, async races, single playback, cleanup and media errors');
