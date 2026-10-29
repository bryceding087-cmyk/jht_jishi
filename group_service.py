"""Canvas grouping metadata. Groups never participate in generation edges."""
import math
from fastapi import HTTPException

COLORS = {'#999999','#ef5350','#ff982e','#f6c44b','#2dcc79','#15b9d0','#4285f4','#9061f9','#ef3394','#dddddd'}


def normalize_groups(groups, node_ids):
    result, seen, owned = [], set(), set()
    for group in groups:
        gid = group.get('id')
        if not isinstance(gid, str) or not gid or len(gid) > 120 or gid in seen:
            raise HTTPException(422, '分组 ID 不能为空或重复')
        seen.add(gid)
        members = group.get('node_ids', [])
        if not isinstance(members, list) or len(members) > 400 or any(not isinstance(item, str) for item in members):
            raise HTTPException(422, '分组节点列表无效')
        members = list(dict.fromkeys(item for item in members if item in node_ids))
        if not members:
            continue
        if any(item in owned for item in members):
            raise HTTPException(422, '同一个节点不能同时属于多个组')
        owned.update(members)
        title = str(group.get('title') or 'Group').strip()
        if len(title) > 80:
            raise HTTPException(422, '组名称不能超过 80 个字符')
        entry = {'id': gid, 'title': title or 'Group', 'node_ids': members,
                 'color': group.get('color') if group.get('color') in COLORS else '#999999'}
        for key in ('x', 'y', 'width', 'height'):
            if key not in group:
                continue
            value = group[key]
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or abs(value) > 10_000_000:
                raise HTTPException(422, '分组位置或尺寸无效')
            if key in ('width', 'height') and value <= 0:
                raise HTTPException(422, '分组尺寸必须大于零')
            entry[key] = value
        result.append(entry)
    return result
