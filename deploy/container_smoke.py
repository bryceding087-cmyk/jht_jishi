"""Offline Linux-runtime smoke check; no database or paid model requests."""
import importlib
import os
from pathlib import Path
import subprocess
import tempfile

for name in ('fastapi', 'uvicorn', 'pydantic_core', 'sqlalchemy', 'psycopg',
             'PIL.Image', 'yaml', 'openpyxl', 'mutagen', 'httptools', 'uvloop', 'watchfiles'):
    importlib.import_module(name)
with tempfile.TemporaryDirectory() as tmp:
    subtitle = Path(tmp) / 'chinese.ass'
    subtitle.write_text('''[Script Info]
ScriptType: v4.00+
PlayResX: 320
PlayResY: 180
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans CJK SC,24,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:00.20,Default,,0,0,0,,建绘通离线测试
''', encoding='utf-8')
    ffmpeg = os.environ.get('JIANHUITONG_FFMPEG', 'ffmpeg')
    result = subprocess.run([ffmpeg, '-hide_banner', '-nostdin', '-f', 'lavfi', '-i',
                             'color=c=blue:s=320x180:d=0.2', '-vf', f'ass={subtitle}',
                             '-frames:v', '1', '-f', 'null', '-'], capture_output=True, text=True, timeout=30)
    if result.returncode or 'NotoSansCJK' not in result.stderr or 'Glyph 0x' in result.stderr:
        raise SystemExit('FFmpeg Chinese-subtitle smoke check failed:\n' + result.stderr[-4000:])
print('PASS: Linux Python/native imports, FFmpeg/libass and Noto CJK rendering.')
