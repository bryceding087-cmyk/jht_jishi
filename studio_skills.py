"""Reviewed, offline SkillHub adaptations used by the Studio text executor.

Only repository-owned rules are loaded. Upstream installers, scripts, provider
configuration and future remote updates are never executed by a production run.
"""
import hashlib
import json
from pathlib import Path

from fastapi import HTTPException


SKILL_ROOT = Path(__file__).parent / 'agents/script-studio/skills'


def load_skill_bundle(stage):
    if stage not in ('writing', 'extraction', 'image', 'video'):
        raise ValueError('Unknown Studio skill stage')
    try:
        root = SKILL_ROOT.resolve()
        manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
        selected = []
        for entry in manifest['skills']:
            if stage not in entry['stages']:
                continue
            path = (root / entry['rules']).resolve()
            path.relative_to(root)
            rules = path.read_text(encoding='utf-8').strip()
            if not rules:
                raise ValueError('Empty skill rules')
            selected.append({'id': entry['id'], 'version': entry['version'], 'rules': rules})
        if not selected:
            raise ValueError('No matching skills')
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise HTTPException(503, '创作技能文件不完整，请恢复随应用发布的技能包后重试；已有稿件保留') from error
    revision = hashlib.sha256(json.dumps(
        [manifest['version'], stage, selected], ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()
    return {
        'system': '\n\n'.join(f"创作技能 {item['id']}（建绘通适配规则）：\n{item['rules']}" for item in selected),
        'metadata': {'skills': [item['id'] for item in selected], 'version': revision},
    }
