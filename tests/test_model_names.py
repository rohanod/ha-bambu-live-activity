"""Model title regression tests: run without a Home Assistant installation."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'custom_components' / 'bambu_live_activity' / 'model_names.py'
spec = importlib.util.spec_from_file_location('bambu_model_names', MODULE)
names = importlib.util.module_from_spec(spec)
spec.loader.exec_module(names)


class TestPrintNames(unittest.TestCase):
    def test_real_bambu_file(self):
        self.assertEqual(names.clean_title('cone-customised.gcode.3mf'), 'cone-customised')

    def test_download_file_with_cache_id(self):
        self.assertEqual(names.clean_title('5637793-cone-customised.gcode.gcode'), 'cone-customised')

    def test_unsafe_profile_id(self):
        self.assertIsNone(names.clean_title('2989019#profileId-338848'))

    def test_generic_print_labels(self):
        for value in ['1 piece', 'A1 mini, 1 piece', '2 pieces', 'plate_1.gcode']:
            with self.subTest(value=value):
                self.assertIsNone(names.clean_title(value))

    def test_strip_bambu_path(self):
        self.assertEqual(names.clean_title('/cache/print/My Cube.gcode.3mf'), 'My Cube')

    def test_stale_cloud_task_not_used(self):
        job = SimpleNamespace(task_id='888', _task_data={'id': '777', 'designTitle': 'OLD MODEL'},
                              subtask_name='new-part.gcode.3mf', gcode_file=None,
                              gcode_file_downloaded=None)
        self.assertEqual(names.title_from_job(job), 'new-part')

    def test_cloud_model_name_if_task_matches(self):
        job = SimpleNamespace(task_id='888', _task_data={'id': 888, 'designTitle': 'Lattice Flexagon'},
                              subtask_name='1 piece', gcode_file=None,
                              gcode_file_downloaded=None)
        self.assertEqual(names.title_from_job(job), 'Lattice Flexagon')

    def test_fallback_when_no_cloud(self):
        self.assertEqual(names.title_from_job(None, 'cone-customised.gcode.3mf'), 'cone-customised')


if __name__ == '__main__':
    unittest.main()
