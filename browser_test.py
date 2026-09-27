"""Run real Chrome smoke tests using Python's standard library only."""
import base64
import functools
import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / '.test-output'


class CDP:
    def __init__(self, url):
        address = urllib.parse.urlsplit(url)
        self.sock = socket.create_connection((address.hostname, address.port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f'GET {address.path} HTTP/1.1\r\nHost: {address.netloc}\r\n'
                           f'Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n'
                           'Sec-WebSocket-Version: 13\r\n\r\n').encode())
        header = b''
        while not header.endswith(b'\r\n\r\n'):
            header += self.sock.recv(1)
        assert b'101' in header, header
        self.sequence = 0

    def read(self, size):
        data = b''
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise ConnectionError('Browser disconnected')
            data += chunk
        return data

    def call(self, method, params=None):
        self.sequence += 1
        data = json.dumps({'id': self.sequence, 'method': method, 'params': params or {}}).encode()
        size = len(data)
        header = bytes([0x81, 0x80 | size]) if size < 126 else bytes([0x81, 0xFE]) + struct.pack('!H', size)
        mask = os.urandom(4)
        self.sock.sendall(header + mask + bytes(value ^ mask[index % 4] for index, value in enumerate(data)))
        while True:
            first, second = self.read(2)
            length = second & 127
            if length == 126:
                length = struct.unpack('!H', self.read(2))[0]
            elif length == 127:
                length = struct.unpack('!Q', self.read(8))[0]
            payload = self.read(length)
            if first & 15 == 8:
                raise ConnectionError('WebSocket closed')
            response = json.loads(payload)
            if response.get('id') == self.sequence:
                if 'error' in response:
                    raise RuntimeError(response['error'])
                return response['result']

    def evaluate(self, expression):
        result = self.call('Runtime.evaluate', {'expression': expression, 'returnByValue': True, 'awaitPromise': True})
        if 'exceptionDetails' in result:
            raise AssertionError(result['exceptionDetails'])
        return result['result'].get('value')

    def loaded(self):
        for _ in range(100):
            if self.evaluate("document.readyState === 'complete' && !!document.querySelector('#todo-form')"):
                return
            time.sleep(0.05)
        raise TimeoutError('Page did not load')


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def handle(self):
        try:
            super().handle()
        except ConnectionResetError:
            pass  # Chrome may close an idle connection during navigation.


def main():
    chrome = Path(os.environ.get('PROGRAMFILES', r'C:\Program Files')) / 'Google/Chrome/Application/chrome.exe'
    if not chrome.exists():
        chrome = Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
    OUTPUT.mkdir(exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix='browser-', dir=OUTPUT))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    process = subprocess.Popen([str(chrome), '--headless=new', '--disable-gpu', '--no-first-run',
                                '--no-default-browser-check', '--remote-debugging-port=0',
                                f'--user-data-dir={profile}', 'about:blank'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    client = None
    try:
        port_file = profile / 'DevToolsActivePort'
        for _ in range(200):
            if port_file.exists():
                break
            if process.poll() is not None:
                raise RuntimeError(f'Browser exited: {process.returncode}')
            time.sleep(0.05)
        port = port_file.read_text().splitlines()[0]
        pages = json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json'))
        client = CDP(next(page['webSocketDebuggerUrl'] for page in pages if page['type'] == 'page'))
        client.call('Page.enable')
        client.call('Emulation.setDeviceMetricsOverride', {'width': 1360, 'height': 960, 'deviceScaleFactor': 1, 'mobile': False})
        client.call('Page.navigate', {'url': f'http://127.0.0.1:{server.server_port}/index.html'})
        client.loaded()

        def check(expression, description):
            assert client.evaluate(expression), description
            print('PASS:', description)

        def add(text):
            client.evaluate(f"document.querySelector('#todo-input').value = {json.dumps(text)}; document.querySelector('#todo-input').dispatchEvent(new Event('input')); document.querySelector('#todo-form').requestSubmit()")

        def click(selector):
            client.evaluate(f'document.querySelector({json.dumps(selector)}).click()')

        def reload():
            client.call('Page.reload')
            time.sleep(0.2)
            client.loaded()

        def screenshot(name):
            result = client.call('Page.captureScreenshot', {'format': 'png'})
            (OUTPUT / name).write_bytes(base64.b64decode(result['data']))

        check("document.querySelectorAll('.todo-item').length === 0 && !document.querySelector('#empty-state').hidden", 'Initial empty state')
        screenshot('desktop-empty.png')
        add('   ')
        check("document.querySelectorAll('.todo-item').length === 0 && !document.querySelector('#todo-input').checkValidity()", 'Reject whitespace')
        add('整理本周的学习计划')
        add('完成 Todo App 的五项基本功能')
        add('<img src=x onerror=alert(1)>')
        check("document.querySelectorAll('.todo-item').length === 3 && !document.querySelector('#todo-list img')", 'Add and safely display text')
        click('.todo-check')
        check("document.querySelector('#stat-completed').textContent === '1' && document.querySelector('#progress-text').textContent === '33%'", 'Complete a task and update progress')
        reload()
        check("document.querySelectorAll('.todo-item').length === 3 && document.querySelector('.todo-check').checked", 'Persist additions and completion across reload')
        click('.todo-check')
        check("document.querySelector('#stat-completed').textContent === '0'", 'Return task to incomplete')
        click('.delete-button')
        reload()
        check("document.querySelectorAll('.todo-item').length === 2", 'Persist deletion across reload')
        click('.todo-check')
        click('[data-filter="active"]')
        check("document.querySelectorAll('.todo-item').length === 1 && !document.querySelector('.todo-check').checked", 'Filter active tasks')
        click('[data-filter="completed"]')
        check("document.querySelectorAll('.todo-item').length === 1 && document.querySelector('.todo-check').checked", 'Filter completed tasks')
        add('读完一章喜欢的书')
        check("document.querySelectorAll('.todo-item').length === 3 && document.querySelector('[data-filter=all]').getAttribute('aria-pressed') === 'true'", 'Show new task when adding from completed filter')
        screenshot('desktop-tasks.png')
        client.call('Emulation.setDeviceMetricsOverride', {'width': 390, 'height': 844, 'deviceScaleFactor': 1, 'mobile': True})
        add('长任务内容' * 40)
        check('document.documentElement.scrollWidth <= window.innerWidth', 'Mobile layout with 200-character task has no horizontal overflow')
        click('.delete-button')
        screenshot('mobile-tasks.png')
        client.evaluate("window.originalSetItem = Storage.prototype.setItem; Storage.prototype.setItem = function() { throw new DOMException('Full', 'QuotaExceededError'); }")
        add('模拟保存失败')
        check("document.querySelectorAll('.todo-item').length === 3 && !document.querySelector('#storage-error').hidden && document.querySelector('#todo-input').value === '模拟保存失败'", 'Storage failure preserves list and input')
        client.evaluate('Storage.prototype.setItem = window.originalSetItem')
        client.evaluate("localStorage.setItem('shixu.todos.v1', '{broken')")
        reload()
        check("document.querySelector('#todo-input').disabled && !document.querySelector('#storage-error').hidden && localStorage.getItem('shixu.todos.v1') === '{broken'", 'Corrupt storage is reported and preserved')
        client.evaluate("localStorage.removeItem('shixu.todos.v1'); window.dispatchEvent(new StorageEvent('storage', {key: 'shixu.todos.v1', newValue: null}))")
        check("!document.querySelector('#todo-input').disabled && document.querySelector('#storage-error').hidden", 'Recover after damaged storage is removed')
        client.call('Page.navigate', {'url': (ROOT / 'index.html').as_uri()})
        time.sleep(0.2)
        client.loaded()
        add('直接打开文件测试')
        reload()
        check("document.querySelectorAll('.todo-item').length === 1 && document.querySelector('.todo-label').textContent === '直接打开文件测试'", 'Direct file opening persists data in Chrome')
        print('All 15 browser checks passed. Screenshots:', OUTPUT)
    finally:
        if client:
            client.sock.close()
        process.terminate()
        process.wait(timeout=10)
        server.shutdown()
        # Only delete this test run's unique browser profile inside .test-output.
        if profile.resolve().parent == OUTPUT.resolve():
            shutil.rmtree(profile, ignore_errors=True)


if __name__ == '__main__':
    main()
