"""Local, reproducible video assembly. Only library asset IDs reach FFmpeg."""
from pathlib import Path
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import uuid

from fastapi import HTTPException


def ffmpeg_executable():
    explicit = os.environ.get('JIANHUITONG_FFMPEG')
    if explicit:
        return explicit
    found = shutil.which('ffmpeg')
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        raise HTTPException(503, '请安装 requirements.txt 中的 imageio-ffmpeg，或配置 JIANHUITONG_FFMPEG') from None


def execute(arguments, cwd=None, timeout=240):
    result = subprocess.run([ffmpeg_executable(), '-hide_banner', '-nostdin', '-y', *arguments],
                            cwd=cwd, capture_output=True, timeout=timeout,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        # The log contains local paths, but never provider configuration or secrets.
        detail = result.stderr.decode('utf-8', errors='replace')[-1800:]
        raise HTTPException(422, '媒体处理失败：' + detail)
    return result


def asset_path(host, asset_id):
    asset = host.require_live_asset(asset_id)
    url = str(asset.get('url', ''))
    name = url.removeprefix('/uploads/')
    if not url.startswith('/uploads/') or Path(name).name != name or '\\' in name:
        raise HTTPException(422, '合成只接受已保存到本地历史文件的媒体')
    path = (host.UPLOADS / name).resolve()
    if path.parent != host.UPLOADS.resolve() or not path.is_file():
        raise HTTPException(422, '素材文件缺失，请重新上传或缓存')
    return path


def inspect_file(path):
    result = subprocess.run([ffmpeg_executable(), '-hide_banner', '-nostdin', '-i', str(path)],
                            capture_output=True, timeout=30,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    log = result.stderr.decode('utf-8', errors='replace')
    match = re.search(r'Duration: (\d+):(\d+):(\d+(?:\.\d+)?)', log)
    if not match:
        raise HTTPException(422, '无法读取媒体时长')
    h, m, s = map(float, match.groups())
    video = re.search(r'Video:.*?\b(\d{2,5})x(\d{2,5})\b', log)
    return {'duration': h * 3600 + m * 60 + s, 'has_audio': bool(re.search(r'Audio:', log)),
            'width': int(video[1]) if video else None, 'height': int(video[2]) if video else None}


def inspect_asset(host, asset_id):
    return {'asset_id': asset_id, **inspect_file(asset_path(host, asset_id))}


def ass_time(seconds):
    centis = max(0, round(float(seconds) * 100))
    return f'{centis // 360000}:{centis // 6000 % 60:02d}:{centis // 100 % 60:02d}.{centis % 100:02d}'


def ass_text(text):
    # ASS overrides must never be accepted from story content.
    return str(text).replace('\\', '／').replace('{', '｛').replace('}', '｝').replace('\r', '').replace('\n', r'\N')


def subtitle_file(path, subtitles, brand, duration, width, height):
    size = max(22, round(height * .036))
    font = 'Microsoft YaHei' if os.name == 'nt' else 'Noto Sans CJK SC'
    content = ('[Script Info]\nScriptType: v4.00+\n'
               f'PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 0\n'
               '[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, '
               'BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, '
               'Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n'
               f'Style: Default,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00202020,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,40,40,{round(height*.09)},1\n'
               f'Style: Brand,{font},{size+3},&H00FFFFFF,&H00FFFFFF,&H00202020,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,8,50,50,{round(height*.12)},1\n'
               '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n')
    for item in subtitles:
        content += f'Dialogue: 0,{ass_time(item["start"])},{ass_time(item["end"])},Default,,0,0,0,,{ass_text(item["text"])}\n'
    if brand:
        content += f'Dialogue: 1,{ass_time(max(0, duration-6))},{ass_time(duration)},Brand,,0,0,0,,{{\\fad(400,400)}}{ass_text(brand)}\n'
    path.write_text(content, encoding='utf-8-sig')


def render_timeline(host, timeline, progress=lambda text: None, cancelled=lambda: False):
    clips = timeline.get('clips', [])
    if not clips or len(clips) > 80:
        raise HTTPException(422, '时间线需要 1 至 80 个视频片段')
    ratio = timeline.get('aspect_ratio', '16:9')
    width, height = {'16:9': (1280, 720), '9:16': (720, 1280), '1:1': (960, 960)}[ratio]
    total = sum(float(c['duration']) for c in clips)
    if not math.isfinite(total) or not 0 < total <= 1200:
        raise HTTPException(422, '成片时长须在 20 分钟以内')
    if any(not math.isfinite(float(c['duration'])) or not 0 < float(c['duration']) <= 60 for c in clips):
        raise HTTPException(422, '镜头时长无效')
    with tempfile.TemporaryDirectory(prefix='jht-render-', dir=host.DATA) as directory:
        work = Path(directory)
        for i, clip in enumerate(clips):
            if cancelled():
                raise HTTPException(409, '合成已暂停，可稍后恢复')
            path = asset_path(host, clip['asset_id'])
            meta = inspect_file(path)
            if meta['duration'] + .15 < clip['duration']:
                raise HTTPException(422, f'第 {i+1} 段实际长度不足，需调整分镜或补足片段')
            progress(f'正在整理第 {i+1}/{len(clips)} 段视频')
            use_original = meta['has_audio'] and not clip.get('mute_original', False)
            execute(['-i', str(path), '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
                     '-map', '0:v:0', '-map', '0:a:0' if use_original else '1:a:0',
                     '-t', str(clip['duration']), '-vf',
                     f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24',
                     '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
                     '-c:a', 'aac', '-ar', '48000', '-ac', '2', str(work/f'clip{i}.mp4')])
        (work/'concat.txt').write_text(''.join(f"file 'clip{i}.mp4'\n" for i in range(len(clips))), encoding='ascii')
        execute(['-f', 'concat', '-safe', '1', '-i', 'concat.txt', '-c', 'copy', 'joined.mp4'], cwd=work)
        subtitle_file(work/'captions.ass', timeline.get('subtitles', []), timeline.get('brand', ''), total, width, height)
        args = ['-i', 'joined.mp4']
        filters, labels = ['[0:a]volume=0.8[a0]'], ['[a0]']
        index = 1
        for audio in timeline.get('audio', []):
            path = asset_path(host, audio['asset_id'])
            start, end = float(audio.get('start', 0)), float(audio.get('end', total))
            if not 0 <= start < end <= total + .05:
                raise HTTPException(422, '音轨时间区间无效')
            if audio.get('loop'):
                args += ['-stream_loop', '-1']
            args += ['-i', str(path)]
            gain = max(0, min(2, float(audio.get('volume', 1))))
            length = end-start
            # Fade at the end of the whole track, after any source looping.
            # A full-film score must not restart or fade at each video cut.
            fade_duration = float(audio.get('fade_out', .5 if audio.get('loop') else 0))
            if not math.isfinite(fade_duration) or fade_duration < 0:
                raise HTTPException(422, '音轨淡出时长无效')
            fade_duration = min(length, fade_duration)
            fade = (f'afade=t=out:st={length-fade_duration}:d={fade_duration},'
                    if fade_duration else '')
            filters.append(f'[{index}:a]atrim=duration={length},asetpts=PTS-STARTPTS,volume={gain},'
                           f'{fade}adelay={round(start*1000)}:all=1[a{index}]')
            labels.append(f'[a{index}]')
            index += 1
        filters.append(''.join(labels) + f'amix=inputs={len(labels)}:duration=first:normalize=0,alimiter=limit=0.95[mixed]')
        filters.append('[0:v]subtitles=captions.ass[v]')
        output = host.UPLOADS/(str(uuid.uuid4())+'.mp4')
        progress('正在混音并合成字幕与片尾')
        try:
            execute([*args, '-filter_complex', ';'.join(filters), '-map', '[v]', '-map', '[mixed]',
                     '-t', str(total), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
                     '-c:a', 'aac', '-movflags', '+faststart', str(output)], cwd=work, timeout=900)
            meta = inspect_file(output)
            if abs(meta['duration']-total) > .3:
                raise HTTPException(422, '成片实测时长与时间线不一致')
            # Local rendered files can exceed the upload limit; they are streamed for playback.
            asset = {'id': str(uuid.uuid4()), 'name': timeline.get('title', '完整视频')+'.mp4',
                     'kind': 'video', 'url': '/uploads/'+output.name, 'mime_type': 'video/mp4',
                     'size_bytes': output.stat().st_size, 'project_id': timeline['project_id'],
                     'source': 'generated', 'tags': ['Agent 成片'], 'folder_id': '',
                     'created_at': host.time.time(), 'duration': meta['duration'], 'agent_render': True}
            return host.asset_public(host.save('asset', asset))
        except Exception:
            output.unlink(missing_ok=True)
            raise
