import assert from 'node:assert/strict';
import { createImageLightbox } from '../public/image-lightbox.js';

// Model the dialog boundary; native focus trapping remains the browser's job.
let activeElement, reduced = false;
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.events = {}; this.attributes = {}; this.animations = []; }
  setAttribute(key, value) { this.attributes[key] = value; }
  removeAttribute(key) { delete this[key]; delete this.attributes[key]; }
  append(...items) { this.children.push(...items); }
  addEventListener(type, handler) { this.events[type] = handler; }
  showModal() { this.open = true; this.opener = activeElement; activeElement = this; }
  close() { this.open = false; activeElement = this.opener; this.events.close(); }
  getAnimations() { return this.animations; }
  animate() {
    let resolve, reject;
    const animation = { finished: new Promise((yes, no) => { resolve = yes; reject = no; }),
      finish: () => resolve(), cancel: () => reject(new Error('cancelled')) };
    this.animations.push(animation);
    return animation;
  }
}
const body = new Element('body'), errors = [];
globalThis.document = { body, createElement: tag => new Element(tag) };
globalThis.matchMedia = () => ({ matches: reduced });
const viewer = createImageLightbox({ onError: error => errors.push(error) });
const source = { kind: 'image', name: '参考图', url: '/uploads/reference.png' };
viewer.open({ kind: 'audio', url: '/uploads/audio.wav' });
assert.equal(body.children.length, 0);
const opener = {}; activeElement = opener;
viewer.open(source);
const dialog = body.children[0], [image, closeButton] = dialog.children;
assert.equal(dialog.tag, 'dialog');
assert.deepEqual(dialog.children.map(child => child.tag), ['img', 'button'], 'no asset information or actions');
assert.equal(image.src, source.url);
assert.equal(image.alt, source.name);
assert.equal(image.draggable, false);
assert.equal(closeButton.attributes['aria-label'], '关闭图片预览');
dialog.events.click({ target: image });
assert.equal(dialog.open, true, 'clicking the image keeps the viewer open');
dialog.events.click({ target: dialog });
assert.equal(dialog.open, true, 'remain modal until the exit animation finishes');
dialog.animations.at(-1).finish();
await Promise.resolve();
assert.equal(dialog.open, false);
assert.equal(image.src, undefined);
assert.equal(activeElement, opener);

viewer.open(source);
let prevented = false;
dialog.events.cancel({ preventDefault() { prevented = true; } });
assert.ok(prevented, 'Escape closes with the exit transition');
// Reopening during exit must not let the old completion close the new image.
const previousExit = dialog.animations.at(-1);
viewer.open({ ...source, url: '/uploads/next.png' });
previousExit.finish();
await Promise.resolve();
assert.equal(dialog.open, true);
assert.equal(image.src, '/uploads/next.png');
reduced = true;
closeButton.onclick();
assert.equal(dialog.open, false, 'reduced motion closes immediately');
viewer.open(source);
image.events.error();
assert.equal(dialog.open, false);
assert.equal(errors.length, 1);
viewer.open(source);
assert.equal(body.children.length, 1, 'reuse one standalone viewer');
viewer.close();
console.log('PASS: image-only viewer, backdrop/Escape/button close, exit cancellation, reduced motion and load failure');
