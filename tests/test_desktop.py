import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'app'))
from desktop import Desktop


class DesktopTests(TestCase):
    def setUp(self):
        self.engine = Mock(data=Path(__file__).resolve().parent.parent / 'work')
        self.engine.jobs.return_value = []
        self.webview = SimpleNamespace(FileDialog=SimpleNamespace(FOLDER=1,SAVE=2))
        self.desktop = Desktop(self.engine, Mock(), self.webview)
        self.desktop.window = Mock()

    def test_close_hides_and_reopen_restores_without_exit(self):
        self.assertFalse(self.desktop.closing())
        self.desktop.window.hide.assert_called_once()
        self.desktop.window.destroy.assert_not_called()
        self.desktop.show()
        self.desktop.window.show.assert_called_once()
        self.desktop.window.restore.assert_called_once()

    def test_exit_confirms_active_jobs_and_cancel_keeps_running(self):
        self.engine.jobs.return_value = [dict(state='downloading')]
        self.desktop.window.create_confirmation_dialog.return_value = False
        self.assertTrue(self.desktop.exit()['cancelled'])
        self.assertFalse(self.desktop.exiting)
        self.desktop.window.destroy.assert_not_called()
        self.desktop.window.create_confirmation_dialog.return_value = True
        self.desktop.exit()
        self.assertTrue(self.desktop.exiting)
        self.assertTrue(self.desktop.closing())
        self.desktop.window.destroy.assert_called_once()

    def test_owned_directory_dialog_cancel_and_lock(self):
        self.desktop.window.create_file_dialog.return_value = None
        self.assertTrue(self.desktop.choose(self.engine.data / 'missing')['cancelled'])
        self.desktop.window.create_file_dialog.assert_called_with(1, directory=str(self.engine.data))
        self.desktop.window.create_file_dialog.return_value = (str(self.engine.data),)
        self.assertEqual(self.desktop.choose(self.engine.data)['path'], str(self.engine.data))
        self.desktop.dialog_lock.acquire()
        with self.assertRaises(ValueError):
            self.desktop.choose(self.engine.data)
        self.desktop.dialog_lock.release()

    def test_failed_window_load_exits_instead_of_leaving_blank_window(self):
        self.desktop.window.events.loaded.wait.return_value = False
        self.desktop.watch_startup()
        self.assertTrue(self.desktop.exiting)
        self.assertIn('WebView2', self.desktop.startup_error)
        self.desktop.window.destroy.assert_called_once()

    def test_native_save_file_returns_name_and_cancel(self):
        target=self.engine.data/'custom.mp4'
        self.desktop.window.create_file_dialog.return_value=str(target)
        self.assertEqual(self.desktop.choose_save_file(str(target))['path'],str(target))
        self.desktop.window.create_file_dialog.assert_called_once_with(2,
            directory=str(self.engine.data),save_filename='custom.mp4',file_types=('Media files (*.mp4)',))
        self.desktop.window.create_file_dialog.return_value=None
        self.assertTrue(self.desktop.choose_save_file(str(target))['cancelled'])

    def test_window_size_and_maximized_state_are_saved(self):
        self.desktop.window_resized(1100,780)
        self.desktop.maximized=True
        self.desktop.window_resized(1920,1080)
        self.desktop.remember_window()
        self.engine.save_settings.assert_called_with(dict(window_width=1100,window_height=780,window_maximized=True))
