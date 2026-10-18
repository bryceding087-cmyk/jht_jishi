import { marked } from './vendor/marked.js';
import DOMPurify from './vendor/purify.js';

// Documents are model/user content, never trusted application HTML.
export function renderMarkdown(content) {
  let source = String(content ?? '');
  let structured = false;
  try {
    const value = JSON.parse(source);
    if (value !== null && typeof value === 'object') {
      source = JSON.stringify(value, null, 2);
      structured = true;
    }
  } catch { /* Ordinary Markdown. */ }
  const fragment = DOMPurify.sanitize(structured ? '' : marked.parse(source, { gfm: true, breaks: true }), {
    RETURN_DOM_FRAGMENT: true,
    ALLOWED_TAGS: ['p','br','hr','h1','h2','h3','h4','h5','h6','strong','em','del','ul','ol','li',
      'blockquote','pre','code','table','thead','tbody','tr','th','td','a'],
    ALLOWED_ATTR: ['href','title','start','align'],
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
  });
  if (structured) {
    const pre = document.createElement('pre'), code = document.createElement('code');
    code.textContent = source;
    pre.append(code); fragment.append(pre);
  }
  fragment.querySelectorAll('a').forEach(link => {
    const href = link.getAttribute('href') || '';
    try {
      const url = new URL(href, location.href);
      if (!href || !['https:','http:','mailto:'].includes(url.protocol)) throw new Error('Unsupported URL');
      // Explicit external links open separately without access to this page.
      link.setAttribute('rel', 'noopener noreferrer');
      if (url.origin !== location.origin) link.setAttribute('target', '_blank');
    } catch { link.removeAttribute('href'); }
  });
  fragment.querySelectorAll('table').forEach(table => {
    const scroll = document.createElement('div');
    scroll.className = 'agent-table-scroll';
    table.replaceWith(scroll); scroll.append(table);
  });
  return fragment;
}
