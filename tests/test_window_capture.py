from dataclasses import replace
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch

from ada_poker import windows
from ada_poker.recorder import CaptureRegion, Recorder, RecordingOptions, build_command


class WindowCaptureTests(unittest.TestCase):
    def setUp(self):
        self.window=windows.CaptureWindow(123456,42,'ExampleClass','Same title',640,480,affinity=0)

    def test_encoder_targets_handle_not_desktop_or_title(self):
        command=build_command('ffmpeg.exe',CaptureRegion(0,0,640,480),RecordingOptions(),Path('C:/test'),self.window)
        self.assertEqual(command[command.index('-i')+1],'hwnd=123456')
        self.assertNotIn('desktop',command)
        self.assertNotIn('Same title',command)
        self.assertEqual(command[command.index('-video_size')+1],'640x480')

    def test_client_dimensions_must_match_encoder(self):
        with self.assertRaises(ValueError):
            Recorder('ffmpeg',CaptureRegion(0,0,320,240),Path('.dev'),RecordingOptions(),self.window)

    def test_visible_capture_uses_desktop_client_coordinates(self):
        region=CaptureRegion(-1200,50,640,480)
        command=build_command('ffmpeg',region,RecordingOptions(),Path('.dev'),self.window,True)
        self.assertEqual(command[command.index('-i')+1],'desktop')
        self.assertEqual(command[command.index('-offset_x')+1],'-1200')
        self.assertEqual(command[command.index('-offset_y')+1],'50')

    def test_visible_capture_stops_if_client_moves(self):
        recorder=Recorder('ffmpeg',CaptureRegion(-1200,50,640,480),Path('.dev'),RecordingOptions(),self.window,True)
        with patch('ada_poker.recorder.window_client_bounds',return_value=(-1100,50,640,480)):
            with self.assertRaisesRegex(OSError,'moved'):recorder._validate_visible_bounds()

    def test_visible_preview_uses_same_source_as_video(self):
        with patch.object(windows,'window_info',return_value=self.window),patch.object(windows,'window_client_bounds',return_value=(-1200,50,640,480)),patch.object(windows,'preview_ppm') as capture:
            windows.window_preview_ppm(self.window,500,300,visible_window=True)
            capture.assert_called_once_with(-1200,50,640,480,500,300)

    def test_title_changes_do_not_change_window_identity(self):
        current=replace(self.window,title='Changed title')
        self.assertEqual(windows.validate_window(self.window,current),current)

    def test_reused_handle_and_other_target_are_rejected(self):
        for changes in ({'pid':99},{'hwnd':66},{'class_name':'AnotherClass'}):
            with self.subTest(changes=changes),self.assertRaisesRegex(OSError,'replaced'):
                windows.validate_window(self.window,replace(self.window,**changes))

    def test_minimized_window_is_rejected(self):
        with self.assertRaisesRegex(OSError,'minimized'):
            windows.validate_window(self.window,replace(self.window,minimized=True))

    def test_capture_exclusion_is_reported(self):
        for affinity in (1,0x11):
            with self.subTest(affinity=affinity),self.assertRaisesRegex(OSError,'blocking Windows capture'):
                windows.validate_window(self.window,replace(self.window,affinity=affinity))

    def test_resize_requires_a_new_selection(self):
        with self.assertRaisesRegex(OSError,'size changed'):
            windows.validate_window(self.window,replace(self.window,width=800))

    def test_unavailable_affinity_is_not_assumed_protected(self):
        windows.validate_window(self.window,replace(self.window,affinity=None))

    def test_window_preview_uses_client_dc(self):
        with patch.object(windows,'window_info',return_value=self.window),patch.object(windows,'preview_ppm') as capture:
            windows.window_preview_ppm(self.window,500,300)
            capture.assert_called_once_with(0,0,640,480,500,300,hwnd=123456)

    def test_preview_tracks_resized_client_without_reselecting(self):
        resized=replace(self.window,width=900,height=600)
        with patch.object(windows,'window_info',return_value=resized),patch.object(windows,'preview_ppm') as capture:
            windows.window_preview_ppm(self.window,500,300)
            capture.assert_called_once_with(0,0,900,600,500,300,hwnd=123456)

    def test_clubgg_tables_are_labeled_by_application_not_just_title(self):
        self.assertEqual(windows.window_label(replace(self.window,application='ClubGG',title='Example Table')),'ClubGG - Example Table')
        self.assertEqual(windows.window_label(replace(self.window,application='ClubGG',title='ClubGG')),'ClubGG - Lobby')
        self.assertEqual(windows.window_label(replace(self.window,application='ClubGG',title='')),'ClubGG - Untitled table')

    def test_protected_preview_does_not_capture_desktop(self):
        with patch.object(windows,'window_info',return_value=replace(self.window,affinity=0x11)),patch.object(windows,'preview_ppm') as capture:
            with self.assertRaises(OSError):windows.window_preview_ppm(self.window)
            capture.assert_not_called()

    def test_unavailable_target_fails_before_creating_a_session(self):
        recorder=Recorder('ffmpeg',CaptureRegion(0,0,640,480),Path('.dev'),RecordingOptions(),self.window)
        with patch('ada_poker.recorder.validate_window',side_effect=OSError('closed')),patch.object(recorder,'_create_session_dir') as create:
            with self.assertRaisesRegex(OSError,'closed'):recorder.start()
            create.assert_not_called()
            self.assertEqual(recorder.snapshot()['state'],'error')

    def test_window_watchdog_finishes_encoder_and_reports_reason(self):
        recorder=Recorder('ffmpeg',CaptureRegion(0,0,640,480),Path('.dev'),RecordingOptions(),self.window)
        proc=Mock();proc.poll.return_value=None;result={}
        with patch('ada_poker.recorder.validate_window',side_effect=OSError('Window closed')),patch.object(recorder,'_terminate_gracefully') as stop:
            flag=threading.Event();timer=threading.Timer(2,flag.set);timer.start()
            try:recorder._watchdog(proc,Path('.dev'),flag,result)
            finally:timer.cancel()
            stop.assert_called_once_with(proc)
            self.assertEqual(result,{'reason':'window_unavailable','error':'Window closed'})
            self.assertEqual(recorder.snapshot()['state'],'stopping')


if __name__=='__main__':unittest.main()
