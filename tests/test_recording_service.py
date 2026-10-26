from pathlib import Path
from dataclasses import replace
import json
import threading
import time
import unittest
from unittest.mock import patch
from http.client import HTTPConnection
from tempfile import TemporaryDirectory

from ada_poker import recording_service as service
from ada_poker.windows import CaptureWindow
from ada_poker.ui import AppServer
from ada_poker.ui_data import Library


class FakeRecorder:
    def __init__(self,encoder,region,folder,options,window,visible_window=False):
        self.folder=folder/'session';self.done=threading.Event();self.saved=False;self.reason=None
    def start(self):self.folder.mkdir(parents=True);return self.folder
    def stop(self,reason='user_requested'):
        self.reason=reason;self.saved=True;self.done.set()
    def snapshot(self):return {'state':'complete' if self.done.is_set() else 'recording','elapsed_seconds':2,'error':None}
    def join(self,timeout=None):self.done.wait(timeout)


class RecordingServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(dir='.dev');self.folder=Path(self.temp.name).resolve()
        self.target=CaptureWindow(42,99,'Example','Table',320,240,affinity=0)
        self.manager=service.RecordingService(self.folder/'analysis',self.folder/'state.json')
        self.manager.targets={'42':self.target}
        self.patches=[patch.object(service,'Recorder',FakeRecorder),patch.object(service,'find_encoder',return_value='fake'),
                      patch.object(service.windows,'validate_window',side_effect=lambda t,**kw:t),patch.object(service.windows,'keep_awake'),
                      patch.object(service,'verify_session'),patch.object(service,'analyze',side_effect=self.analyze)]
        self.mocks=[p.start() for p in self.patches]
        self.analyses=[]
    def tearDown(self):
        self.manager.close()
        if self.manager.worker:self.manager.worker.join(5)
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def analyze(self,source,output,progress):
        self.assertTrue(self.manager.recorder.saved,'Analysis ran before final save')
        self.analyses.append(source);output.mkdir(parents=True,exist_ok=True)
        (output/'summary.json').write_text(json.dumps({'hands':3}))
        progress('Reading saved frames')
    def start(self):
        self.manager.start('42',str(self.folder/'recordings'),60)
        for _ in range(100):
            if self.manager.recorder:return
            time.sleep(.01)
        self.fail('Worker did not start')
    def finish(self):
        self.manager.worker.join(5);self.assertFalse(self.manager.worker.is_alive())
    def test_stop_analyzes_exactly_once_after_save(self):
        self.start();self.assertEqual(self.analyses,[])
        self.manager.stop();self.manager.stop();self.finish()
        self.assertEqual(len(self.analyses),1)
        self.assertEqual(self.manager.snapshot()['hands'],3)
        self.assertEqual(self.manager.snapshot()['revision'],1)
    def test_closing_stops_and_still_finishes_analysis(self):
        self.start();self.manager.close();self.finish()
        self.assertEqual(self.manager.recorder.reason,'app_closed')
        self.assertEqual(self.manager.snapshot()['state'],'complete')
        self.assertEqual(len(self.analyses),1)
    def test_duplicate_start_rejected(self):
        self.start()
        with self.assertRaisesRegex(ValueError,'Wait'):self.manager.start('42',str(self.folder),60)
    def test_failed_analysis_retains_recording_and_can_retry(self):
        self.mocks[-1].side_effect=OSError('OCR failed')
        self.start();self.manager.stop();self.finish()
        self.assertTrue(self.manager.snapshot()['can_retry'])
        self.assertTrue(Path(self.manager.snapshot()['session']).is_dir())
        self.mocks[-1].side_effect=self.analyze
        self.manager.retry();self.finish()
        self.assertEqual(self.manager.snapshot()['state'],'complete')
    def test_bad_video_never_reaches_analyzer(self):
        self.mocks[-2].side_effect=ValueError('No complete segments')
        self.start();self.manager.stop();self.finish()
        self.assertEqual(self.analyses,[])
        self.assertEqual(self.manager.snapshot()['state'],'error')
    def test_no_table_frames_produce_empty_card_readings(self):
        from ada_poker.cards import add_card_readings
        observations=[{'time':0,'table_visible':False,'evidence':{'image':'unused.jpg'},'issues':[]}]
        with patch('ada_poker.cards.subprocess.Popen') as encoder:
            result=add_card_readings(observations,self.folder,'unused','test-source',lambda message:None)
        encoder.assert_not_called()
        self.assertEqual(result[0]['visible_hole_cards'],[])
        self.assertNotIn('hero_cards',result[0])
    def test_previous_task_is_available_after_reopening(self):
        self.start();self.manager.close();self.finish()
        restored=service.RecordingService(self.folder/'analysis',self.folder/'state.json')
        self.assertEqual(restored.snapshot()['state'],'complete')
        self.assertEqual(restored.snapshot()['hands'],3)
    def test_unselected_window_and_relative_path_are_rejected(self):
        with self.assertRaises(ValueError):self.manager.start('999',str(self.folder),60)
        with self.assertRaises(ValueError):self.manager.start('42','relative',60)
    def test_start_uses_latest_client_dimensions(self):
        current=replace(self.target,width=805,height=607)
        with patch.object(service.windows,'validate_window',return_value=current) as validate, patch.object(service,'Recorder',wraps=FakeRecorder) as recorder:
            self.start();self.manager.stop();self.finish()
            self.assertTrue(validate.call_args.kwargs['allow_resize'])
            args=recorder.call_args
            self.assertEqual((args.args[1].width,args.args[1].height),(805,607))
            self.assertEqual(args.kwargs['window'],current)
    def test_visible_mode_uses_automatic_client_bounds(self):
        with patch.object(service.windows,'window_client_bounds',return_value=(-900,200,320,240)),patch.object(service,'Recorder',wraps=FakeRecorder) as recorder:
            self.manager.start('42',str(self.folder/'recordings'),60,'visible_window')
            self.manager.stop();self.finish()
            self.assertEqual(recorder.call_args.args[1],service.CaptureRegion(-900,200,320,240))
            self.assertTrue(recorder.call_args.kwargs['visible_window'])
    def test_invalid_capture_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'capture method'):
            self.manager.start('42',str(self.folder),60,'unknown')
    def test_recording_mutations_require_token_and_local_origin(self):
        server=AppServer(Library(self.folder,[]),token='test-token',recording=self.manager)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def post(path,headers=None,body=None):
            connection=HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            try:
                connection.request('POST',path,body=json.dumps(body or {}),headers=headers or {})
                response=connection.getresponse();payload=response.read()
                return response.status,payload
            finally:connection.close()
        try:
            self.assertEqual(post('/api/recording/stop')[0],403)
            self.assertEqual(post('/api/recording/stop',{'X-Ada-Token':'test-token','Origin':'https://outside.example'})[0],403)
            self.assertEqual(post('/api/recording/stop',{'X-Ada-Token':'test-token'})[0],200)
            self.assertEqual(post('/api/detach',body={'token':'test-token'})[0],200)
            self.assertIsNotNone(server.detached_at)
        finally:server.shutdown();server.server_close();thread.join(5)


if __name__=='__main__':unittest.main()
