import sys, json, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]

from tabpilot.backends.cdp import CDPBackend
from tabpilot import captcha
from tabpilot.errors import JSError, TimeoutError_
from conftest import FakeBackend, FakeSession

class Flood:
    def settimeout(self, t):
        pass

    def send_text(self, text):
        self.request = json.loads(text)
        self.start = time.monotonic()

    def recv_text(self):
        time.sleep(0.002)
        if time.monotonic() - self.start < 0.10:
            return json.dumps({'method': 'Page.frameNavigated', 'params': {}})
        return json.dumps({'id': self.request['id'], 'result': {}})

backend = CDPBackend()
backend._sockets['t1'] = Flood()
start = time.monotonic()
timed_out = False
try:
    backend._command('t1', 'Page.enable', timeout_s=0.01)
except TimeoutError_:
    timed_out = True
elapsed_ms = round((time.monotonic() - start) * 1000)

results = {
    'event_flood_timeout': {
        'requested_ms': 10,
        'elapsed_ms': elapsed_ms,
        'raised_timeout_error': timed_out,
        'prevented_infinite_extension': elapsed_ms < 50,
    }
}

def fail(_):
    raise JSError('injected inspection failure')

s = FakeSession(FakeBackend(responses={'captcha_detect': fail}))
solve_res = json.loads(captcha.solve_captcha(s, tab_id='t1'))
results['inspection_error_start'] = {
    'status': solve_res['status'],
    'preserved_failure': solve_res['status'] == 'unverified',
    'detail': solve_res.get('detail'),
}

print(json.dumps(results, indent=2))
Path(__file__).with_name('fault_results.json').write_text(json.dumps(results, indent=2))
