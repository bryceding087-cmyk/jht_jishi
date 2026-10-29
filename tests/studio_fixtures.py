"""Deterministic text provider for isolated Studio integration/browser tests."""
import json


def script(ep):
    return f'''# 第{ep}集《归途》

### 场{ep}-1
日 外 小镇车站
出场人物：小林
△小林攥着一封旧信走出车站，脚步停在门口。他仔细辨认信封上的地址，抬头看向远处的老街。午后的光从梧桐叶之间洒下来，行人从他身边经过，脚步声渐渐远去。他把信折好放进口袋，向熟悉的路口走去。
小林：这一次，我一定把信交到你手里。
△他拎起行李，顺着路牌的方向走去。身后的列车缓缓驶离站台，车轮的声音逐渐减弱。

### 场{ep}-2
日 外 小镇车站门前
出场人物：小林
△小林再次确认手里的信封，停在门前。他看了一眼旧照片，认出照片里的屋檐就在前方。微风拂动纸角，他用掌心压平，然后认真地收进口袋。
小林：我记得这里的每一条路。
△他迈出一步，沿着有阳光的街道向前走去。背影穿过画面，镜头留在石板路上，远处传来一声门铃。
'''


def reply(config, messages):
    system, context = messages[0]['content'], json.loads(messages[-1]['content'])
    if context.get('task') == 'optimize_node_prompts':
        value = {'items': [{'node_id': node['node_id'], 'direction': '沿用既定角色身份与服装，保持原有空间关系、光源方向与道具归属。让主体轮廓与关键材质清楚可辨，手部与持有物的接触关系明确；严格遵循本节点已有的静态姿势或动作起止，不提前呈现后续事件。'} for node in context['nodes']]}
    elif context.get('task') == 'optimize_node_prompt':
        value = {'direction': '沿用既定角色身份与服装，保持原有空间关系、光源方向与道具归属。让主体轮廓与关键材质清楚可辨，手部与持有物的接触关系明确；严格遵循本节点已有的静态姿势或动作起止，不提前呈现后续事件。'}
    elif '最新消息' in system:
        text = context['latest_message']
        if '制作画布' in text or '短剧' in text or context.get('hint') == 'produce':
            value = {'intent': 'produce', 'reply': '我会先完成剧本，再为每一集安排资产与镜头。', 'title': '归途',
                     'brief_patch': {'idea': '小林回家送信', 'episode_count': 2, 'duration': '8 秒'} if not context.get('brief') else {}, 'instruction': text}
        elif '写剧本' in text or '设定' in text:
            value = {'intent': 'script', 'reply': '开始整理故事。', 'action': 'bible' if '设定' in text else 'all',
                     'brief_patch': {'idea': '小林回家', 'episode_count': 2}}
        elif '生成图片' in text:
            value = {'intent': 'image', 'reply': '正在生成图片。', 'prompt': '窗边一只橘猫', 'title': '窗边橘猫'}
        elif '生成视频' in text:
            value = {'intent': 'video', 'reply': '正在生成视频。', 'prompt': '橘猫缓慢转头', 'title': '窗边橘猫视频'}
        else:
            value = {'intent': 'chat', 'reply': '你好，我可以回答日常问题，也可以帮你创作。普通问答不会创建剧本。'}
    elif '只输出故事设定' in system:
        value = {'title': '故事设定', 'content': '# 故事设定\n\n小林回家送信。\n\n' + '小林希望将旧信交给家人，故事发生在小镇车站。人物以行动解决问题，保持时间顺序和空间连续。' * 4,
                 'summary': '送信回家', 'continuity': '', 'warnings': []}
    elif '分集大纲，每集独立标题' in system:
        value = {'title': '分集大纲', 'content': '# 分集大纲\n\n' + '\n\n'.join(f'## 第{i}集《归途》\n小林来到车站，在门口确认地址，带着旧信向前走去。人物目标是送信，阻力是陌生的道路，他凭旧照片认出方向，结束时进入老街。' for i in range(1, context['brief']['episode_count'] + 1)),
                 'summary': '完整分集安排', 'continuity': '', 'warnings': []}
    elif '完整可拍摄正文' in system:
        ep = int(context['task'].split('-')[1])
        value = {'title': f'第 {ep} 集《归途》', 'content': script(ep), 'summary': '小林抵达车站并确认方向', 'continuity': '小林持有旧信，知道家人的地址，日间从车站步行出发。', 'warnings': []}
    elif '统一美术方向' in system:
        value = {'style_bible': '写实电影质感，暖色日光，旧式小镇车站。小林保持灰色外套，旧信保持黄色纸张。', 'assumptions': []}
    elif context.get('task') == 'register_assets':
        scenes = context['scenes']
        value = {'assets': [] if context.get('catalog') else [
            {'key': key, 'type': kind, 'name': name, 'facts': facts, 'design': '暖色自然光，电影写实质感。', 'anchors': ['统一身份与比例'],
             'sourceScene': scenes[0]['id'], 'sourceLine': scenes[0]['line_start'] + 3}
            for key, kind, name, facts in [('c01', 'character', '小林', '灰外套的年轻人'), ('s01', 'scene', '小镇车站', '旧式车站门口'), ('p01', 'prop', '旧信', '黄色信封')]], 'warnings': []}
    elif '逐场阅读完整剧本' in system:
        ep = context['episode']; scenes = context['scenes']
        value = {'assets': [] if context.get('catalog') else [
            {'key': key, 'type': kind, 'name': name, 'facts': facts, 'design': '暖色自然光，电影写实质感。', 'anchors': ['统一身份与比例'],
             'sourceScene': scenes[0]['id'], 'sourceLine': scenes[0]['line_start'] + 3}
            for key, kind, name, facts in [('c01', 'character', '小林', '灰外套的年轻人'), ('s01', 'scene', '小镇车站', '旧式车站门口'), ('p01', 'prop', '旧信', '黄色信封')]],
            'shots': [{'sceneId': s['id'], 'sourceLine': s['line_start'] + 3, 'sceneKey': 's01', 'characterKeys': ['c01'], 'propKeys': ['p01'],
                       'title': '确认地址', 'seconds': 4, 'framing': '中景', 'staging': '小林站在车站门前', 'startState': '小林握着信', 'action': '低头查看地址，然后抬头',
                       'endState': '小林向前走去', 'camera': '缓慢推进', 'lighting': '暖日光', 'sound': '环境声', 'dialogue': '这一次，我一定把信交到你手里。' if s['id'].endswith('-1') else '我记得这里的每一条路。', 'continuity': '信在右手', 'risks': []}
                      for s in scenes], 'warnings': []}
    else:
        raise AssertionError('Unexpected Studio model call: ' + system[:100])
    return {'choices': [{'message': {'content': json.dumps(value, ensure_ascii=False)}}]}
