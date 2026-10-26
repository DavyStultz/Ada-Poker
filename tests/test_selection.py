import unittest
from dataclasses import replace

from ada_poker.windows import Monitor, selection_pixels


class SelectionTests(unittest.TestCase):
    def test_external_monitor_coordinates(self):
        monitor = Monitor("second", 1920, 0, 1920, 1080, False, 1920, 0)
        self.assertEqual(selection_pixels(monitor, (960, 540), (125, 10), (835, 515)),
                         (2170, 20, 1420, 1010))

    def test_reverse_drag_and_clipping_on_negative_monitor(self):
        monitor = Monitor("left", -1920, -100, 1920, 1080, False, -1920, -100)
        self.assertEqual(selection_pixels(monitor, (960, 540), (1000, 600), (-20, -10)),
                         (-1920, -100, 1920, 1080))

    def test_rounds_outward_without_losing_edge_pixels(self):
        monitor = Monitor("scaled", 0, 0, 1920, 1080, True, 0, 0)
        x, y, w, h = selection_pixels(monitor, (700, 393), (1, 1), (699, 392))
        self.assertTrue(monitor.contains(x, y, w, h))
        self.assertLessEqual(x, 1920 / 700)
        self.assertGreaterEqual(x+w, 699 * 1920 / 700)
        self.assertGreaterEqual(y+h, 392 * 1080 / 393)

    def test_monitor_rejects_out_of_bounds(self):
        monitor = Monitor("test", 0, 0, 100, 100, True, 0, 0)
        self.assertFalse(monitor.contains(50, 50, 51, 50))
        self.assertFalse(monitor.contains(0, 0, 0, 10))

    def test_work_area_change_does_not_invalidate_capture(self):
        monitor = Monitor("external", 1920, 0, 1920, 1080, False, 1920, 0)
        self.assertTrue(monitor.matches_capture(replace(monitor, work_y=40)))
        self.assertFalse(monitor.matches_capture(replace(monitor, x=2000)))
        self.assertFalse(monitor.matches_capture(replace(monitor, width=1280)))


if __name__ == "__main__":
    unittest.main()
