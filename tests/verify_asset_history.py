"""Real browser checks for the three-media dated library; isolated local fixtures."""
import io
import socket
import sys
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_agent_runtime import AgentRuntimeTests
from verify_canvas_workspace import drawing
from auth_service import hash_password
from fastapi import HTTPException
from playwright.sync_api import sync_playwright, expect
import uvicorn


def main():
    AgentRuntimeTests.setUpClass();host=AgentRuntimeTests.host
    with host.connect() as db:db.execute('UPDATE auth_users SET password_hash=? WHERE username=?',(hash_password('history-test'),'admin'))
    output=ROOT/'outputs/asset-history-verification';output.mkdir(parents=True,exist_ok=True)
    audio_data=io.BytesIO()
    with wave.open(audio_data,'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000);stream.writeframes(bytes(96000))
    assets=[]
    for index,(kind,day) in enumerate([('image','2026-10-16'),('audio','2026-10-16'),('audio','2026-10-15'),('audio','2026-10-15'),('video','2026-10-15'),('image','2026-09-21'),('image','2026-09-20'),('image','2026-09-20'),('image','2026-09-20'),('image','2026-09-21')]):
        data,name={'image':(drawing(),f'城市参考 {index}.png'),'audio':(audio_data.getvalue(),f'声音参考 {index}.wav'),'video':(AgentRuntimeTests.clip.read_bytes(),'镜头片段.mp4')}[kind]
        asset=host.store_uploaded_asset(data,name)
        timestamp=datetime.fromisoformat(day+'T12:00:00+00:00').timestamp()+index
        host.save('asset',{**host.get('asset',asset['id']),'created_at':timestamp,'updated_at':timestamp})
        assets.append(asset)
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(128)
    origin=f'http://127.0.0.1:{listener.getsockname()[1]}'
    server=uvicorn.Server(uvicorn.Config(host.app,log_level='error'));thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True);thread.start()
    try:
        while not server.started:time.sleep(.05)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
            context=browser.new_context(viewport={'width':1944,'height':1110},timezone_id='Asia/Shanghai')
            assert context.request.post(origin+'/api/auth/login',headers={'Origin':origin},data={'username':'admin','password':'history-test','remember':False}).ok
            response=context.request.post(origin+'/api/projects',headers={'Origin':origin},data={'name':'素材资产验证','nodes':[],'edges':[]});assert response.ok
            assert context.request.post(origin+'/api/assets/content',headers={'Origin':origin},data={'kind':'text','name':'禁止保存的文本','content':'不应成为资产'}).status==410
            assert context.request.post(origin+'/api/characters',headers={'Origin':origin},data={'name':'禁止保存的角色'}).status==410
            assert context.request.post(origin+'/api/assets',headers={'Origin':origin},multipart={'file':{'name':'old.txt','mimeType':'text/plain','buffer':b'not media'}}).status==422
            assert context.request.get(origin+'/api/characters').json()==[]
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.set_default_timeout(10000)
            page.goto(origin+'/?project='+response.json()['id']);page.locator('#workspaceAssets').click()
            expect(page.locator('#mainNav [data-view="assets"]')).to_have_attribute('aria-label','资产')
            expect(page.locator('#alLibrary h1')).to_have_text('生成历史')
            expect(page.locator('.al-history-card')).to_have_count(10)
            expect(page.locator('[data-al-tab]')).to_have_count(4)
            expect(page.locator('[data-al-tab="image"] small')).to_have_text('6')
            expect(page.locator('[data-al-tab="video"] small')).to_have_text('1')
            expect(page.locator('[data-al-tab="audio"] small')).to_have_text('3')
            expect(page.locator('.al-history-day')).to_have_count(4)
            expect(page.locator('.al-history-day h2').first).to_have_text('2026-10-16')
            expect(page.locator('.al-history-duration:not([hidden])')).to_have_count(4)
            expect(page.locator('.al-history-card').first).to_be_in_viewport()
            page.screenshot(path=str(output/'01-history-desktop.png'))
            card=page.locator('.al-history-card').first;card.hover()
            assert card.locator('.al-history-actions').evaluate('el=>getComputedStyle(el).transitionDuration')=='0.15s'
            expect(card.locator('.al-history-actions')).to_have_css('opacity','1')
            page.screenshot(path=str(output/'02-history-hover.png'))
            page.mouse.move(1500,150)
            expect(card.locator('.al-history-actions')).to_have_css('opacity','0')
            page.locator('[data-al-tab="video"]').click();expect(page.locator('.al-history-card')).to_have_count(1)
            video=page.locator('.al-history-card video');card=page.locator('.al-history-card')
            assert video.evaluate('v=>v.paused')
            card.hover()
            page.wait_for_function('document.querySelector(".al-history-card video").currentTime > .2')
            assert video.evaluate('v=>v.muted&&v.loop&&!v.paused')
            expect(card).to_have_class('al-history-card is-previewing')
            expect(card.locator('.al-history-play')).to_have_css('opacity','0')
            card.locator('[data-al-download]').hover();assert video.evaluate('v=>!v.paused&&v.muted')
            page.screenshot(path=str(output/'04-video-hover-preview.png'))
            page.mouse.move(1500,150);page.wait_for_function('document.querySelector(".al-history-card video").paused && document.querySelector(".al-history-card video").currentTime === 0')
            card.hover();page.wait_for_function('!document.querySelector(".al-history-card video").paused')
            page.locator('[data-al-preview]').click();expect(page.locator('.al-preview-video video')).to_be_visible()
            assert video.evaluate('v=>v.paused&&v.currentTime===0')
            page.locator('#alPreviewClose').click()
            page.locator('[data-al-tab="audio"]').click();expect(page.locator('.al-history-card')).to_have_count(3)
            page.locator('[data-al-preview]').first.click();expect(page.locator('.al-preview-audio audio')).to_be_visible();page.locator('#alPreviewClose').click()
            page.locator('[data-al-tab="image"]').click();expect(page.locator('.al-history-card')).to_have_count(6)
            page.locator('[data-al-preview]').first.click()
            expect(page.get_by_label('设置素材评级')).to_have_count(0)
            page.locator('#alPreviewClose').click()
            expect(page.locator('#alRating')).to_have_count(0)
            page.reload();page.locator('#workspaceAssets').click()
            page.locator('#alHistorySort').click();expect(page.locator('.al-history-day h2').first).to_have_text('2026-09-20')
            card=page.locator('.al-history-card').first;card.hover();card.locator('[data-al-select]').check();expect(page.locator('.al-history-selection')).to_be_visible()
            page.locator('[data-al-action="clear-selection"]').click()
            card=page.locator('.al-history-card').first;card.hover()
            with page.expect_download() as download:card.locator('[data-al-download]').click()
            assert download.value.suggested_filename.endswith('.png') or download.value.suggested_filename.endswith('.jpg')
            card.locator('[data-al-delete]').click();page.locator('#alDeleteConfirm').click();expect(page.locator('.al-history-card')).to_have_count(9)
            for width in [1024,430]:
                page.set_viewport_size({'width':width,'height':1000});page.wait_for_timeout(100)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
                page.screenshot(path=str(output/f'03-history-{width}.png'))
            page.set_viewport_size({'width':1440,'height':1000})
            page.locator('[data-al-tab="video"]').click();page.locator('.al-history-card').hover()
            page.wait_for_function('!document.querySelector(".al-history-card video").paused')
            page.locator('#projectList [data-project]').first.click()
            assert page.locator('.al-history-card video').evaluate('v=>v.paused&&v.currentTime===0')
            page.locator('#workspaceAssets').click();page.locator('[data-al-tab="video"]').click()
            page.locator('.al-history-card').hover();page.wait_for_function('!document.querySelector(".al-history-card video").paused')
            page.locator('[data-al-tab="image"]').click()
            expect(page.locator('.al-history-card video')).to_have_count(0)
            page.emulate_media(reduced_motion='reduce')
            assert page.locator('.al-history-actions').first.evaluate('el=>getComputedStyle(el).transitionDuration')=='0s'
            page.locator('#projectList [data-project]').first.click();expect(page.locator('#canvasView')).to_have_class('workspace view active-view')
            assert not errors,errors
            print('PASS: counts/date groups/media durations, image/video/audio previews, no rating UI, sorting, selection, download, deletion, responsive layouts, canvas return, rejected non-media writes; no browser errors')
            browser.close()
    finally:
        server.should_exit=True;thread.join(timeout=10);listener.close();AgentRuntimeTests.tearDownClass()


if __name__=='__main__':main()
