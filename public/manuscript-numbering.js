// Only manuscript section headings are renumbered. Episode/scene identifiers,
// dates, quantities, dialogue, lists and fenced examples retain their meaning.
export function chineseNumber(value) {
  if (value === 0) return '零';
  const digits = '零一二三四五六七八九';
  let result = '', zero = false;
  for (const [unit, label] of [[10000, '万'], [1000, '千'], [100, '百'], [10, '十'], [1, '']]) {
    const digit = Math.floor(value / unit); value %= unit;
    if (digit) {
      if (zero) result += '零';
      result += (unit === 10 && digit === 1 && !result ? '' : digits[digit]) + label;
      zero = false;
    } else if (result && value) zero = true;
  }
  return result;
}

export function normalizeManuscriptNumbering(content) {
  const counters = Array(7).fill(0);
  let fence = null;
  return String(content ?? '').split(/(\r?\n)/).map(line => {
    const marker = line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
    if (marker) {
      if (!fence) fence = marker[1];
      else if (marker[1][0] === fence[0] && marker[1].length >= fence.length && !marker[2].trim()) fence = null;
      return line;
    }
    if (fence) return line;
    const heading = line.match(/^( {0,3}#{1,6}[\t ]+)(.*)$/);
    if (!heading) return line;
    const level = heading[1].trim().length;
    counters.fill(0, level + 1);
    // Require punctuation and a section title; e.g. “第1集” and “场1-1”
    // deliberately do not match. Existing Chinese headings are idempotent.
    const numbered = heading[2].match(/^(\*\*|__)?(?:\d+|[零〇一二三四五六七八九十百千万两]+)[.．、][\t ]*(\S.*)$/);
    if (!numbered || /^(?:\*\*|__)?\d+[.．]\d/.test(heading[2])) return line;
    counters[level] += 1;
    return heading[1] + (numbered[1] || '') + chineseNumber(counters[level]) + '、' + numbered[2];
  }).join('');
}
