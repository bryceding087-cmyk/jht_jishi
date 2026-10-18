import { referenceIcon, referenceIconNames } from './canvas-reference-icons.js?v=liblib-detail-2';
// Shared canvas glyphs use the reference geometry; remaining app glyphs stay here.
const PATHS = {
  copy: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h3"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 11v6M14 11v6"/>',
  sort: '<path d="M4 5h12M4 10h8M4 15h5M18 8v12m-3-3 3 3 3-3"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3V7Z"/><path d="m3 11 3-1h15"/>',
  group: '<rect x="4" y="4" width="16" height="16" rx="3" stroke-dasharray="3 3"/><rect x="8" y="8" width="8" height="8" rx="1"/>',
  ungroup: '<path d="M8 4H4v4M16 4h4v4M4 16v4h4M20 16v4h-4M3 21 21 3"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  check: '<path d="m5 12 4.2 4.2L19 6.5"/>',
  warning: '<path d="m12 4 9 16H3L12 4Z"/><path d="M12 9v4M12 16h.01"/>',
  chevronDown: '<path d="m6 9 6 6 6-6"/>',
  chevronRight: '<path d="m9 6 6 6-6 6"/>',
  chevronLeft: '<path d="m15 6-6 6 6 6"/>',
  arrowUp: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  arrowUpRight: '<path d="M7 17 17 7M9 7h8v8"/>',
  pencil: '<path d="m4 16-.8 4.8L8 20l10.7-10.7a2.4 2.4 0 0 0-3.4-3.4L4.6 16.6Z"/><path d="m13.8 7.2 3 3"/>',
  grid: '<rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/>',
  panel: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M9 4v16"/>',
  list: '<path d="M8 6h11M8 12h11M8 18h11"/><path d="M4 6h.01M4 12h.01M4 18h.01"/>',
  diamond: '<path d="m12 4 8 8-8 8-8-8 8-8Z"/><path d="m12 9 3 3-3 3-3-3 3-3Z"/>',
  star: '<path d="m12 4 2.4 4.8 5.3.8-3.8 3.7.9 5.2-4.8-2.5-4.8 2.5.9-5.2-3.8-3.7 5.3-.8L12 4Z"/>',
  template: '<path d="M6 4h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z"/><path d="M8 8h8M8 12h8M8 16h5"/>',
  settings: '<path d="m12 3 1.2 2.2 2.4.5.7 2.3 2 1.4-1 2.2 1 2.2-2 1.4-.7 2.3-2.4.5L12 21l-1.2-2.2-2.4-.5-.7-2.3-2-1.4 1-2.2-1-2.2 2-1.4.7-2.3 2.4-.5L12 3Z"/><circle cx="12" cy="12" r="2.7"/>',
  search: '<circle cx="10.8" cy="10.8" r="6.3"/><path d="m16 16 4 4"/>',
  preview: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3V9Z"/>',
  fit: '<path d="M4 9V5a1 1 0 0 1 1-1h4M15 4h3a1 1 0 0 1 1 1v4M20 15v4a1 1 0 0 1-1 1h-4M9 20H5a1 1 0 0 1-1-1v-4"/>',
  reset: '<path d="M5 5v5h5M5.5 10A7.5 7.5 0 1 1 7.8 17"/>',
  text: '<rect x="4" y="5" width="16" height="14" rx="2"/><path d="M8 9h8M8 13h8M8 16h5"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8" cy="9" r="1.5"/><path d="m4 18 5-5 3 3 3-4 5 6"/>',
  video: '<rect x="3" y="5" width="13" height="14" rx="2"/><path d="m16 10 5-3v10l-5-3"/><path d="m8 9 4 3-4 3V9Z"/>',
  audio: '<path d="M5 9v6M9 6v12M13 8v8M17 4v16M21 10v4"/>',
  microphone: '<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M6 10v2a6 6 0 0 0 12 0v-2M12 18v3M9 21h6"/>',
  music: '<path d="M10 17V5l10-2v12M10 9l10-2"/><ellipse cx="7" cy="18" rx="3" ry="2"/><ellipse cx="17" cy="16" rx="3" ry="2"/>',
  script: '<path d="M6 3h9l4 4v14H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z"/><path d="M14 3v5h5M8 12h8M8 16h8"/>',
  character: '<circle cx="12" cy="8" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/>',
  prop: '<path d="m7 5 10 2-1 12-10-2L7 5Z"/><path d="m9 8 5 .9M8.5 12l5 .9M8 16l3 .5"/>',
  play: '<path d="m9 6 9 6-9 6V6Z"/>',
  wand: '<path d="m5 19 10-10M13 5l1.5 2.5L17 9l-2.5 1.5L13 13l-1.5-2.5L9 9l2.5-1.5L13 5ZM18 4v3M19.5 5.5h-3"/>',
  visualStyle: '<circle cx="12" cy="12" r="9"/><path d="m8 20 2.6-12a1.45 1.45 0 0 1 2.8 0L16 20M9.6 12h4.8"/>',
  sparkle: '<path d="M12 .4C12.55 8.2 15.8 11.45 23.6 12C15.8 12.55 12.55 15.8 12 23.6C11.45 15.8 8.2 12.55.4 12C8.2 11.45 11.45 8.2 12 .4Z"/>',
  upload: '<path d="M12 16V4M8 8l4-4 4 4M5 14v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4"/>',
  download: '<path d="M12 4v12M8 12l4 4 4-4M5 20h14"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3 2"/>',
  alarm: '<circle cx="12" cy="13" r="7.5"/><path d="M12 9v4l3 2M5 3 2 6M19 3l3 3M6.5 19.5 5 22M17.5 19.5 19 22M12 3v2"/>',
  help: '<circle cx="12" cy="12" r="8.5"/><path d="M9.5 9a2.7 2.7 0 1 1 4.9 1.6c-.9 1.1-2.4 1.4-2.4 3M12 17h.01"/>',
  keyboard: '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 10h.01M10 10h.01M13 10h.01M16 10h.01M7 14h10"/>',
  link: '<path d="m9 15 6-6M7 17l-1 1a3 3 0 0 1-4-4l3-3a3 3 0 0 1 4 0M17 7l1-1a3 3 0 0 1 4 4l-3 3a3 3 0 0 1-4 0"/>',
};

const FILLED_ICONS = new Set(['sparkle']);

export function icon(name, className = '') {
  const reference = referenceIcon(name, className);
  if (reference) return reference;
  const paths = PATHS[name] || PATHS.diamond;
  const filled = FILLED_ICONS.has(name);
  return `<svg class="ui-icon ui-icon-${name}${className ? ` ${className}` : ''}" viewBox="0 0 24 24" fill="${filled ? 'currentColor' : 'none'}" stroke="${filled ? 'none' : 'currentColor'}" stroke-width="${filled ? 0 : 1.7}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
}

export const iconNames = Object.freeze([...new Set([...Object.keys(PATHS), ...referenceIconNames])]);
