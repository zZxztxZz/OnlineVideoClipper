import sys
import tempfile
import unittest
from pathlib import Path
from http.cookies import SimpleCookie
from http.cookiejar import MozillaCookieJar
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/'app'))
from core import Engine
from site_connection import SiteConnections, netscape_cookies


def cookie(domain='.douyin.com', expires='', value='test'):
    c = SimpleCookie()
    c['sessionid'] = value
    c['sessionid']['domain'] = domain
    c['sessionid']['path'] = '/'
    c['sessionid']['secure'] = True
    c['sessionid']['httponly'] = True
    c['sessionid']['expires'] = expires
    return c


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'work')
        self.e = Engine(Path(self.temp.name), workers=False)
        self.window = Mock()
        self.window.events.closed = MagicMock()
        self.window.get_current_url.return_value = 'https://www.douyin.com/'
        self.window.get_cookies.return_value = [cookie(), cookie('.unrelated.com')]
        self.desktop = SimpleNamespace(data=self.e.data, engine=self.e,
            webview=Mock(), show=Mock())
        self.desktop.webview.create_window.return_value = self.window
        self.c = SiteConnections(self.desktop)

    def tearDown(self):
        self.e.stop()
        self.temp.cleanup()

    def test_connect_reuses_window_and_finish_persists_only_site_cookies(self):
        self.c.start('douyin')
        self.c.start('douyin')
        self.desktop.webview.create_window.assert_called_once()
        self.assertTrue(self.c.status()['douyin'])
        r = self.c.finish('douyin')
        self.assertEqual(self.e.settings()['douyin_cookies'], r['path'])
        jar = MozillaCookieJar(r['path'])
        jar.load(ignore_discard=True, ignore_expires=True)
        self.assertEqual(len(jar), 1)
        saved = list(jar)[0]
        self.assertEqual(saved.domain, '.douyin.com')
        self.assertTrue(saved.has_nonstandard_attr('HTTPOnly'))
        self.assertTrue(saved.secure)
        self.assertTrue(saved.discard)
        self.assertNotIn('test', str(r))
        self.assertFalse(self.c.status()['douyin'])
        self.window.destroy.assert_called_once()

    def test_closed_window_or_unrelated_page_cannot_overwrite_settings(self):
        with self.assertRaises(ValueError): self.c.finish('douyin')
        self.c.start('douyin')
        self.window.get_current_url.return_value = 'https://douyin.com.evil.example/'
        with self.assertRaises(ValueError): self.c.finish('douyin')
        self.window.get_cookies.assert_not_called()
        self.assertEqual(self.e.settings()['douyin_cookies'], '')
        self.c.close_all()
        self.assertFalse(self.c.status()['douyin'])

    def test_expiry_hostonly_and_foreign_cookie_filter(self):
        text = netscape_cookies([cookie('www.bilibili.com'),
            cookie('.bilibili.com', 'Thu, 01 Jan 1970 00:00:01 GMT'),
            cookie('.bilibili.com.evil.example')], 'bilibili')
        self.assertIn('www.bilibili.com\tFALSE', text)
        self.assertEqual(len(text.splitlines()), 3)
        with self.assertRaises(ValueError): netscape_cookies([cookie('.evil.com')], 'douyin')
        with self.assertRaises(ValueError): netscape_cookies([cookie(value='bad\nvalue')], 'douyin')

    def test_invalid_platform_cannot_open_window(self):
        with self.assertRaises(ValueError): self.c.start('unknown')
        with self.assertRaises(ValueError): self.c.finish('unknown')
        self.desktop.webview.create_window.assert_not_called()
