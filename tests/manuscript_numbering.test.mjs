import assert from 'node:assert/strict';
import { chineseNumber, normalizeManuscriptNumbering as normalize } from '../public/manuscript-numbering.js';

for (const [value, expected] of [[1,'一'],[9,'九'],[10,'十'],[11,'十一'],[20,'二十'],[21,'二十一'],[100,'一百'],[101,'一百零一'],[110,'一百一十'],[1001,'一千零一']]) {
  assert.equal(chineseNumber(value), expected);
}
const source = '# 《后浇带》故事设定\r\n\r\n## 0. 本稿采用\r\n10集，90—120秒，2026/10/22，3号楼。\r\n## 1. 一句话故事与核心梗\r\n### 0. 目标\r\n### 1. 阻力\r\n## 2. 核心主题\r\n### 0. 代价\r\n';
const expected = '# 《后浇带》故事设定\r\n\r\n## 一、本稿采用\r\n10集，90—120秒，2026/10/22，3号楼。\r\n## 二、一句话故事与核心梗\r\n### 一、目标\r\n### 二、阻力\r\n## 三、核心主题\r\n### 一、代价\r\n';
assert.equal(normalize(source), expected);
assert.equal(normalize(expected), expected);
assert.equal(normalize('# 第1集《扶正》\n### 场1-1\n1. 动作顺序\n台词：1个人。\n## 3.5米的裂缝'), '# 第1集《扶正》\n### 场1-1\n1. 动作顺序\n台词：1个人。\n## 3.5米的裂缝');
assert.equal(normalize('## **0. 本稿采用**\n## __1、主线__'), '## **一、本稿采用**\n## __二、主线__');
assert.equal(normalize('## 三、设定\n## 五、主线'), '## 一、设定\n## 二、主线');
assert.equal(normalize('```markdown\n## 0. 示例\n```\n## 0. 正文\n~~~\n## 1. 示例\n~~~\n## 1. 结尾'), '```markdown\n## 0. 示例\n```\n## 一、正文\n~~~\n## 1. 示例\n~~~\n## 二、结尾');
assert.match(normalize(Array.from({length:21}, (_,i)=>`## ${i}. 标题`).join('\n')), /## 二十一、标题$/);
console.log('PASS manuscript sections start at 一; Chinese tens, nested levels, idempotence, factual numbers and code fences');
