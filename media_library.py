"""Remove legacy non-media library records without removing editable canvas text."""
import json
import time
from pathlib import Path
from storage.backup import backup_database


def migrate_media_library(connect, data):
    with connect() as db:
        records = [(kind, rid, json.loads(body)) for kind, rid, body in db.execute('SELECT kind,id,body FROM records')]
        assets = {rid: body for kind, rid, body in records if kind == 'asset'}
        characters = {rid: body for kind, rid, body in records if kind == 'character'}
        removed = {rid for rid, asset in assets.items() if asset.get('kind', 'image') not in ('image', 'prop', 'video', 'audio')}
        props = {rid for rid, asset in assets.items() if asset.get('kind') == 'prop'}
        legacy_nodes = any(node.get('type') == 'character' for kind, _, body in records if kind == 'project' for node in body.get('nodes', []))
        if not removed and not props and not characters and not legacy_nodes:
            return {'removed_assets': 0, 'removed_characters': 0, 'converted_images': 0, 'updated_projects': 0}
        backup_database(connect, data, 'media-library')
        db.execute('BEGIN IMMEDIATE')

        def clean(value):
            if isinstance(value, list):
                return [clean(item) for item in value]
            if not isinstance(value, dict):
                return value
            result = {}
            for key, item in value.items():
                if key in ('asset_id', 'assetId', 'sourceAssetId', 'referenceAssetId') and isinstance(item, str) and item in removed:
                    result[key] = ''
                elif key in ('asset_ids', 'reference_asset_ids', 'selected_asset_ids') and isinstance(item, list):
                    result[key] = [aid for aid in item if aid not in removed]
                elif key == 'mentions' and isinstance(item, list):
                    result[key] = [clean(mention) for mention in item if mention.get('asset_id') not in removed or mention.get('node_id')]
                else:
                    result[key] = clean(item)
            # Keep the actual words when a standalone text-asset mention disappears.
            if isinstance(result.get('prompt'), str):
                for mention in value.get('mentions', []):
                    if mention.get('asset_id') in removed and not mention.get('node_id') and mention.get('token'):
                        content = assets[mention['asset_id']].get('content') or mention.get('content') or ''
                        result['prompt'] = result['prompt'].replace(mention['token'], content)
            return result

        updated_projects = 0
        for kind, rid, body in records:
            if kind == 'asset' and rid in removed or kind == 'character':
                db.execute('DELETE FROM records WHERE kind=? AND id=?', (kind, rid))
                continue
            result = clean(body)
            if kind == 'asset' and rid in props:
                result['kind'] = 'image'
            if kind == 'project':
                nodes = []
                for original in body.get('nodes', []):
                    node = clean(original)
                    if original.get('type') == 'character':
                        character = characters.get(original.get('characterId'), {})
                        aid = original.get('assetId') or character.get('asset_id')
                        asset = assets.get(aid, {})
                        if aid in removed or asset.get('kind', 'image') not in ('image', 'prop') or not asset.get('url'):
                            continue
                        node.update(type='asset', assetId=aid, assetUrl=asset['url'], title=original.get('title') or character.get('name') or asset.get('name'))
                        for key in ('characterId', 'characterName', 'role', 'tone'):
                            node.pop(key, None)
                    source = assets.get(original.get('assetId') or original.get('sourceAssetId'), {})
                    if source.get('id') in removed and original.get('type') in ('asset', 'text', 'script', 'prompt'):
                        if source.get('kind') == 'script' and not node.get('script_data'):
                            node.update(type='script', script_data=source.get('script_data', {}))
                        elif not (node.get('settings') or {}).get('prompt') and source.get('content'):
                            node.update(type='prompt', settings={**node.get('settings', {}), 'prompt': source['content']})
                        node.pop('assetUrl', None)
                    nodes.append(node)
                result['nodes'] = nodes
                ids = {node['id'] for node in nodes}
                result['edges'] = [edge for edge in result.get('edges', []) if edge.get('from') in ids and edge.get('to') in ids]
                groups = []
                for group in result.get('groups', []):
                    group['node_ids'] = [nid for nid in group.get('node_ids', []) if nid in ids]
                    if group['node_ids']:
                        groups.append(group)
                if 'groups' in result:
                    result['groups'] = groups
                if result != body:
                    result['revision'] = body.get('revision', 0) + 1
                    result['updated_at'] = time.time()
                    updated_projects += 1
            if result != body:
                db.execute('UPDATE records SET body=? WHERE kind=? AND id=?', (json.dumps(result, ensure_ascii=False), kind, rid))
    # Only local files owned exclusively by removed records may be unlinked.
    surviving_urls = {asset.get('url') for aid, asset in assets.items() if aid not in removed}
    uploads = (Path(data) / 'uploads').resolve()
    for aid in removed:
        url = assets[aid].get('url', '')
        if url.startswith('/uploads/') and url not in surviving_urls:
            path = (uploads / url.removeprefix('/uploads/')).resolve()
            if path.is_relative_to(uploads) and path.is_file():
                path.unlink()
    return {'removed_assets': len(removed), 'removed_characters': len(characters), 'converted_images': len(props), 'updated_projects': updated_projects}
