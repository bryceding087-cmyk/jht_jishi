"""Whole-film BGM integration, including the actual decoded final soundtrack.

All providers use local tones and the database is disposable. Three distinct
frequencies identify clip audio, the pre-generated score, and formal speech.
"""
from array import array
import io
import math
import unittest
import uuid
import wave
from unittest.mock import patch

from agent_media import asset_path, execute, inspect_asset
from agent_runtime import Preferences
import test_agent_runtime as runtime_fixture


class AgentBgmTests(unittest.TestCase):
    tearDownClass = classmethod(runtime_fixture.AgentRuntimeTests.tearDownClass.__func__)
    setUp = runtime_fixture.AgentRuntimeTests.setUp
    model_reply = runtime_fixture.AgentRuntimeTests.model_reply
    add_media = runtime_fixture.AgentRuntimeTests.add_media
    image = runtime_fixture.AgentRuntimeTests.image
    video = runtime_fixture.AgentRuntimeTests.video
    create = runtime_fixture.AgentRuntimeTests.create
    step = runtime_fixture.AgentRuntimeTests.step
    confirm = runtime_fixture.AgentRuntimeTests.confirm
    to_storyboard = runtime_fixture.AgentRuntimeTests.to_storyboard

    @classmethod
    def setUpClass(cls):
        runtime_fixture.AgentRuntimeTests.setUpClass.__func__(cls)
        execute(['-f', 'lavfi', '-i', 'color=c=0x374c60:s=320x180:r=24:d=4',
                 '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=4',
                 '-t', '4', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                 '-c:a', 'aac', str(cls.clip)])

    def tone(self, frequency, project_id, name='模拟整片配乐'):
        rate = 16000
        samples = array('h', (round(4096 * math.sin(2 * math.pi * frequency * i / rate))
                              for i in range(rate // 2)))
        stream = io.BytesIO()
        with wave.open(stream, 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(rate)
            audio.writeframes(samples.tobytes())
        return self.host.store_uploaded_asset(stream.getvalue(), name + '.wav', project_id, source='generated')

    def audio(self, request):
        self.provider_calls.append(request.mode)
        asset = self.tone(880 if request.mode == 'music' else 1760,
                          request.project_id, request.name)
        return self.host.save('audio_task', {
            'id': 'audio-' + str(uuid.uuid4()), 'status': 'succeeded',
            'asset': asset, 'duration': .5,
        })

    def prepared(self, *, voice=False, music=True):
        rid = self.create(preferences=Preferences(voice=voice, music=music, subtitles=False))
        self.to_storyboard(rid)
        self.confirm(rid)
        self.service.phase_music(rid)
        self.service.phase_video(rid)
        return rid

    def amplitudes(self, asset_id, start, duration=.2):
        # Decode the rendered AAC, then measure only the known sentinel tones.
        result = execute(['-ss', str(start), '-i', str(asset_path(self.host, asset_id)),
                          '-t', str(duration), '-vn', '-ac', '1', '-ar', '8000',
                          '-f', 'f32le', '-'])
        pcm = array('f', result.stdout)
        self.assertGreater(len(pcm), 100)
        result = {}
        for frequency in (440, 880, 1760):
            sine = sum(sample * math.sin(2 * math.pi * frequency * i / 8000)
                       for i, sample in enumerate(pcm))
            cosine = sum(sample * math.cos(2 * math.pi * frequency * i / 8000)
                         for i, sample in enumerate(pcm))
            result[frequency] = 2 * math.hypot(sine, cosine) / len(pcm)
        return result

    def test_music_only_render_uses_pre_generated_bgm_through_all_clips(self):
        rid = self.prepared()
        before = self.service.get(rid)
        provider_calls = list(self.provider_calls)
        self.service.phase_postproduction(rid)
        run = self.service.get(rid)
        timeline = run['artifacts']['timeline']['payload']
        self.assertEqual(self.provider_calls, provider_calls)
        self.assertEqual([track['asset_id'] for track in timeline['audio']], [before['bgm']['asset_id']])
        self.assertTrue(all(clip['mute_original'] for clip in timeline['clips']))
        self.assertLess(self.provider_calls.index('music'), self.provider_calls.index('video'))
        asset_id = run['render_asset']['id']
        self.assertAlmostEqual(inspect_asset(self.host, asset_id)['duration'], 8, delta=.2)
        # BGM is only 0.5 seconds long; both cuts and the late section need loops.
        for start in (.15, 3.8, 4.2, 6.5):
            with self.subTest(start=start):
                tones = self.amplitudes(asset_id, start)
                self.assertGreater(tones[880], .05, tones)
                self.assertLess(tones[440], tones[880] * .03, tones)
        middle = self.amplitudes(asset_id, 5)[880]
        ending = self.amplitudes(asset_id, 7.8, .1)[880]
        self.assertLess(ending, middle * .4)

    def test_formal_voice_and_bgm_mix_without_video_model_soundtrack(self):
        rid = self.prepared(voice=True)
        self.service.phase_postproduction(rid)
        run = self.service.get(rid)
        timeline = run['artifacts']['timeline']['payload']
        bgm = next(track for track in timeline['audio'] if track['asset_id'] == run['bgm']['asset_id'])
        speech = next(track for track in timeline['audio'] if track['asset_id'] == run['speech']['line_1']['asset_id'])
        self.assertLess(bgm['volume'], speech['volume'])
        tones = self.amplitudes(run['render_asset']['id'], .15)
        self.assertGreater(tones[1760], .06, tones)
        self.assertGreater(tones[880], .01, tones)
        self.assertLess(tones[880], tones[1760] * .6, tones)
        self.assertLess(tones[440], tones[880] * .03, tones)
        # After speech ends, the same whole-film score continues into shot two.
        later = self.amplitudes(run['render_asset']['id'], 5)
        self.assertGreater(later[880], .01, later)
        self.assertLess(later[1760], later[880] * .03, later)

    def test_disabled_music_and_voice_do_not_mix_retained_audio(self):
        rid = self.prepared(voice=True)
        run = self.service.get(rid)
        self.service.patch(rid, preferences={**run['preferences'], 'music': False, 'voice': False})
        with patch('agent_runtime.render_timeline', return_value=self.add_media('video', '无附加音轨', run['project_id'])):
            self.service.phase_postproduction(rid)
        timeline = self.service.get(rid)['artifacts']['timeline']['payload']
        self.assertEqual(timeline['audio'], [])
        self.assertTrue(all(not clip['mute_original'] for clip in timeline['clips']))

    def test_missing_required_bgm_returns_to_music_before_rendering(self):
        rid = self.prepared()
        for missing in (None, {'duration': .5}):
            with self.subTest(bgm=missing):
                self.service.patch(rid, bgm=missing)
                with patch('agent_runtime.render_timeline') as render:
                    self.service.phase_postproduction(rid)
                render.assert_not_called()
                self.assertEqual(self.service.get(rid)['phase'], 'music')
                calls = list(self.provider_calls)
                self.service.phase_music(rid)
                self.assertTrue(self.service.get(rid)['bgm']['asset_id'])
                # Recover the already-generated score from the media journal.
                self.assertEqual(self.provider_calls, calls)
                self.assertEqual(self.service.get(rid)['phase'], 'video')

    def test_render_cache_depends_on_selected_bgm_and_mix_policy(self):
        rid = self.prepared()
        run = self.service.get(rid)
        old = self.add_media('video', '旧版混音缓存', run['project_id'])
        epoch = run.get('epochs', {}).get('postproduction', 0)
        self.host.save('agent_operation', {
            'id': f'{rid}:op:postproduction:render:{epoch}', 'run_id': rid,
            'key': 'postproduction:render', 'status': 'succeeded', 'result': old,
        })
        new = self.add_media('video', '整片配乐合成', run['project_id'])
        provider_calls = list(self.provider_calls)
        with patch('agent_runtime.render_timeline', return_value=new) as render:
            self.service.phase_postproduction(rid)
            self.service.phase_postproduction(rid)
            self.assertEqual(render.call_count, 1)
            replacement = self.tone(880, run['project_id'], '更换整片配乐')
            self.service.patch(rid, bgm={'asset_id': replacement['id'], 'duration': .5})
            self.service.phase_postproduction(rid)
            self.assertEqual(render.call_count, 2)
        self.assertEqual(self.provider_calls, provider_calls)
        self.assertEqual(self.service.get(rid)['render_asset']['id'], new['id'])


if __name__ == '__main__':
    unittest.main()
