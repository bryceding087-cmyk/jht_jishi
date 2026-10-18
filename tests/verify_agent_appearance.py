"""Verify Agent branding and natural media sizes using isolated local fixtures.

Run: .venv/Scripts/python.exe tests/verify_agent_appearance.py
Requires Playwright and Edge or Chrome. No provider requests are submitted.
"""
from io import BytesIO
import json
from pathlib import Path
import socket
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright, expect
import uvicorn

from agent_media import execute
from agent_runtime import CreateRun
from auth_service import hash_password
from test_agent_runtime import AgentRuntimeTests


SHAPES = [('landscape', 640, 360), ('portrait', 360, 640),
          ('square', 480, 480), ('wide', 960, 240)]
VIEWPORTS = [('desktop', 1512, 1050), ('laptop', 1024, 768), ('mobile', 390, 844)]


def set_viewport(page, width, height):
    page.set_viewport_size({'width': width, 'height': height})
    # The shared sidebar animates its width across responsive breakpoints.
    page.wait_for_function('''() => document.getAnimations().filter(animation=>
      animation.constructor.name==='CSSTransition').every(animation=>animation.playState!=='running')''')


def create_media(fixture, project_id):
    """Use colored edge markers so clipping and surrounding bars are visible."""
    assets = []
    for index, (shape, width, height) in enumerate(SHAPES):
        drawing = Image.new('RGB', (width, height), (47, 93, 139))
        pen = ImageDraw.Draw(drawing)
        pen.rectangle((0, 0, width // 2, height // 2), fill=(45, 179, 180))
        pen.rectangle((width // 2, height // 2, width, height), fill=(111, 99, 212))
        pen.rectangle((2, 2, width - 3, height - 3), outline=(251, 223, 96), width=6)
        pen.line((0, 0, width, height), fill=(255, 255, 255), width=3)
        pen.text((20, 20), f'{shape}: {width} x {height}', fill=(255, 255, 255))
        stream = BytesIO()
        drawing.save(stream, format='PNG')
        image = fixture.host.store_uploaded_asset(stream.getvalue(), f'{shape}.png', project_id, source='generated')
        assets.append(('image', shape, image))
        source = fixture.host.DATA / f'appearance-{index}.png'
        drawing.save(source)
        video_path = fixture.host.DATA / f'appearance-{index}.mp4'
        execute(['-loop', '1', '-i', str(source), '-t', '1', '-r', '24',
                 '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(video_path)])
        video = fixture.host.store_uploaded_asset(video_path.read_bytes(), f'{shape}.mp4', project_id, source='generated')
        assets.append(('video', shape, video))
    return assets


def assert_no_horizontal_overflow(page):
    metrics = page.evaluate('''() => ['html','body','#agentView','#agentScroll','#agentMessages'].map(selector=>{
      const element=document.querySelector(selector);
      return {selector,width:element.clientWidth,scrollWidth:element.scrollWidth};
    })''')
    if not all(item['scrollWidth'] <= item['width'] + 1 for item in metrics):
        page.screenshot(path=str(ROOT / 'outputs/agent-appearance/overflow-failure.png'), full_page=True)
        beyond = page.evaluate('''() => [...document.querySelectorAll('body *')].map(element=>{
          const r=element.getBoundingClientRect();return {tag:element.tagName,id:element.id,
            className:element.className,left:r.left,right:r.right,width:r.width};
        }).filter(r=>r.width>0&&(r.right>innerWidth+1||r.left< -1)).slice(0,25)''')
        raise AssertionError({'containers': metrics, 'beyond_viewport': beyond})
    return metrics


def media_metrics(card):
    return card.evaluate('''card => {
      const media=card.querySelector('img,video'),body=media.parentElement;
      const r=media.getBoundingClientRect(),c=card.getBoundingClientRect(),b=body.getBoundingClientRect();
      const css=getComputedStyle(media),cardCss=getComputedStyle(card);
      return {kind:media.tagName.toLowerCase(),naturalWidth:media.naturalWidth||media.videoWidth,
        naturalHeight:media.naturalHeight||media.videoHeight,width:r.width,height:r.height,
        bodyWidth:b.width,bodyHeight:b.height,cardWidth:c.width,
        borderWidth:parseFloat(cardCss.borderLeftWidth)+parseFloat(cardCss.borderRightWidth),
        leftGap:r.left-b.left,rightGap:b.right-r.right,topGap:r.top-b.top,bottomGap:b.bottom-r.bottom,
        objectFit:css.objectFit,aspectRatio:css.aspectRatio,background:css.backgroundColor,
        cardSizing:card.style.getPropertyValue('--agent-media-width')};
    }''')


def write_contact_sheet(output, screenshots):
    thumb_width = 350
    cells = []
    for label, path in screenshots:
        with Image.open(path) as screenshot:
            picture = screenshot.convert('RGB')
            picture.thumbnail((thumb_width, 380))
        cell = Image.new('RGB', (thumb_width + 20, 420), '#141414')
        ImageDraw.Draw(cell).text((10, 8), label, fill='white')
        cell.paste(picture, (10, 30))
        cells.append(cell)
    sheet = Image.new('RGB', ((thumb_width + 20) * 4, 420 * 2), '#141414')
    for index, cell in enumerate(cells):
        sheet.paste(cell, ((index % 4) * cell.width, (index // 4) * cell.height))
    sheet.save(output)


def main():
    AgentRuntimeTests.setUpClass()
    fixture = AgentRuntimeTests()
    fixture.setUp()
    host = fixture.host
    service = host.app.state.agent_service
    output = ROOT / 'outputs/agent-appearance'
    output.mkdir(parents=True, exist_ok=True)
    password = 'isolated-agent-appearance-password'
    with host.connect() as db:
        db.execute('UPDATE auth_users SET password_hash=? WHERE username=?', (hash_password(password), 'admin'))
        owner = db.execute('SELECT id FROM auth_users WHERE username=?', ('admin',)).fetchone()[0]
    run = service.create(CreateRun(text='媒体显示比例验证', request_id='appearance-fixture'), owner)['run']
    assets = create_media(fixture, run['project_id'])
    for kind, shape, asset in assets:
        service.event(run['id'], 'media', f'{kind} / {shape} / 完整画面与四边标记', asset=asset)
    service.patch(run['id'], status='completed', phase='delivery')

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    server = uvicorn.Server(uvicorn.Config(host.app, host='127.0.0.1', port=port, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    errors = []
    report = {'branding': {}, 'media': [], 'overflow': []}
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.1)
        assert server.started, 'Isolated application did not start'
        with sync_playwright() as playwright:
            executables = [Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'),
                           Path('C:/Program Files/Google/Chrome/Application/chrome.exe')]
            executable = next((path for path in executables if path.is_file()), None)
            browser = playwright.chromium.launch(headless=True, **({'executable_path': str(executable)} if executable else {'channel': 'chrome'}))
            context = browser.new_context(viewport={'width': 1512, 'height': 1050}, device_scale_factor=1)
            response = context.request.post(origin + '/api/auth/login',
                data={'username': 'admin', 'password': password, 'remember': False}, headers={'Origin': origin})
            assert response.ok, response.status
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(origin + '/')
            brand = page.locator('.agent-brand-name')
            expect(brand).to_be_visible()
            expect(brand).to_have_text('建绘通')
            brand_style = brand.evaluate('''element=>{
              const style=getComputedStyle(element);
              return {gradient:style.backgroundImage,clip:style.backgroundClip,fill:style.webkitTextFillColor,
                animation:style.animationName,duration:style.animationDuration,position:style.backgroundPosition};
            }''')
            assert 'linear-gradient' in brand_style['gradient'], brand_style
            assert brand_style['clip'] == 'text', brand_style
            assert brand_style['animation'] != 'none', brand_style
            assert float(brand_style['duration'].rstrip('s')) >= 8, brand_style
            page.wait_for_function('(position)=>getComputedStyle(document.querySelector(".agent-brand-name")).backgroundPosition!==position',
                                   arg=brand_style['position'], timeout=5000)
            report['branding']['motion'] = brand_style
            for label, width, height in VIEWPORTS:
                set_viewport(page, width, height)
                expect(brand).to_be_in_viewport()
                assert_no_horizontal_overflow(page)
                page.screenshot(path=str(output / f'home-{label}.png'), full_page=True)
            page.emulate_media(reduced_motion='reduce')
            reduced = brand.evaluate('element=>({animation:getComputedStyle(element).animationName,gradient:getComputedStyle(element).backgroundImage})')
            assert reduced['animation'] == 'none' and 'linear-gradient' in reduced['gradient'], reduced
            report['branding']['reduced_motion'] = reduced
            page.emulate_media(reduced_motion='no-preference')

            page.evaluate('(rid)=>localStorage.setItem("jht.agent.lastRun",rid)', run['id'])
            page.reload()
            cards = page.locator('.agent-media-card')
            expect(cards).to_have_count(len(assets))
            for label, width, height in VIEWPORTS:
                set_viewport(page, width, height)
                screenshots = []
                for index, (kind, shape, asset) in enumerate(assets):
                    card = cards.nth(index)
                    card.scroll_into_view_if_needed()
                    media = card.locator('img,video')
                    if kind == 'image':
                        expect(media).to_have_js_property('complete', True)
                        page.wait_for_function('(url)=>[...document.querySelectorAll(".agent-media-card img")].some(m=>m.getAttribute("src")===url&&m.naturalWidth>0)', arg=asset['url'])
                    else:
                        page.wait_for_function('(url)=>[...document.querySelectorAll(".agent-media-card video")].some(m=>m.getAttribute("src")===url&&m.readyState>=1&&m.videoWidth>0)', arg=asset['url'])
                        media.evaluate('(video)=>{video.currentTime=0;return video.play()}')
                        page.wait_for_function('(url)=>[...document.querySelectorAll(".agent-media-card video")].some(m=>m.getAttribute("src")===url&&m.currentTime>.25&&m.readyState>=2&&!m.seeking)', arg=asset['url'])
                        media.evaluate('(video)=>video.pause()')
                    page.wait_for_function('(url)=>[...document.querySelectorAll(".agent-media-card img,.agent-media-card video")].some(m=>m.getAttribute("src")===url&&!!m.closest(".agent-media-card").style.getPropertyValue("--agent-media-width"))', arg=asset['url'])
                    metrics = media_metrics(card)
                    assert metrics['naturalWidth'] > 0 and metrics['naturalHeight'] > 0, metrics
                    ratio = metrics['naturalWidth'] / metrics['naturalHeight']
                    assert abs(metrics['width'] / metrics['height'] - ratio) < .01, metrics
                    assert abs(metrics['cardWidth'] - metrics['borderWidth'] - metrics['width']) <= 1, metrics
                    assert all(abs(metrics[key]) <= 1 for key in ('leftGap', 'rightGap', 'topGap', 'bottomGap')), metrics
                    assert metrics['height'] <= 440.5, metrics
                    assert metrics['objectFit'] != 'cover', metrics
                    assert metrics['background'] == 'rgba(0, 0, 0, 0)', metrics
                    report['media'].append({'viewport': label, 'shape': shape, **metrics})
                    report['overflow'].append({'viewport': label, 'shape': shape, 'metrics': assert_no_horizontal_overflow(page)})
                    path = output / f'{label}-{kind}-{shape}.png'
                    card.screenshot(path=str(path))
                    # A tall card may exceed the mobile scroll area including
                    # its footer. Capture its media separately for an unobscured
                    # contact sheet while retaining the complete card screenshot.
                    media_path = output / f'{label}-{kind}-{shape}-media.png'
                    media.screenshot(path=str(media_path))
                    screenshots.append((f'{label} / {kind} / {shape}', media_path))
                write_contact_sheet(output / f'contact-{label}.png', screenshots)
                page.screenshot(path=str(output / f'conversation-{label}.png'), full_page=True)
            assert not errors, errors
            assert fixture.provider_calls == [], fixture.provider_calls
            (output / 'verification.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
            failure = output / 'overflow-failure.png'
            if failure.exists():
                failure.unlink()
            browser.close()
        print(f'Appearance verified: animated/reduced-motion branding; {len(assets)} image/video cards across {len(VIEWPORTS)} viewports; no provider calls. Output: {output}')
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        fixture.doCleanups()
        AgentRuntimeTests.tearDownClass()


if __name__ == '__main__':
    main()
