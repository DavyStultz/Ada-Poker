from types import SimpleNamespace
import unittest
from unittest import mock

from ada_poker import app
from ada_poker.recorder import CaptureRegion
from ada_poker.windows import Monitor


class AppFailureTests(unittest.TestCase):
    def test_power_error_still_reaches_user_dialog(self):
        monitor = Monitor("external", 1920, 0, 1920, 1080, False, 1920, 0)
        window = SimpleNamespace(winfo_rootx=lambda: 10, winfo_rooty=lambda: 10,
                                 winfo_width=lambda: 800, winfo_height=lambda: 800)
        instance = SimpleNamespace(recorder=None, region=CaptureRegion(1920, 0, 640, 480),
                                   monitor=monitor, root=window,
                                   folder_var=SimpleNamespace(get=lambda: "C:/test-output"))
        with mock.patch.object(app.windows, "monitors", return_value=[monitor]), \
             mock.patch.object(app, "find_encoder", return_value="ffmpeg.exe"), \
             mock.patch.object(app.windows, "keep_awake", side_effect=OSError("power request denied")), \
             mock.patch.object(app.messagebox, "showerror") as error_dialog:
            app.RecorderApp.start(instance)
        self.assertIsNone(instance.recorder)
        error_dialog.assert_called_once()
        self.assertIn("power request denied", error_dialog.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
