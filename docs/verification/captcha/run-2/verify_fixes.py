import sys, os, json, time, socket, tempfile, subprocess, threading, http.server
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]

from tabpilot import captcha
from tabpilot.session import Session
from tabpilot.config import Config
from tabpilot.captcha_verify import verify_access
from tabpilot.captcha_solvers.agent_vision import AgentVisionSolver
from conftest import FakeBackend, FakeSession

for key in ('TABPILOT_REMOTE', 'SSH_CONNECTION'):
    os.environ.pop(key, None)

results = {}

def record(name, value):
    results[name] = value
    print(name + ': ' + json.dumps(value, ensure_ascii=False), flush=True)

def data(result):
    return json.loads(result[0] if isinstance(result, list) else result)

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'<html><body><div id="child-marker">Child frame</div></body></html>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()

with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]

profile = tempfile.TemporaryDirectory(prefix='tabpilot-run2-chrome-')
proc = subprocess.Popen(
    [
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '--headless=new',
        '--no-first-run',
        '--no-default-browser-check',
        '--site-per-process',
        f'--remote-debugging-port={port}',
        f'--user-data-dir={profile.name}',
    ],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)

s = Session(Config(backend='cdp', cdp_port=port, return_images='auto'))
base = f'http://127.0.0.1:{server.server_port}'

def page(html):
    s._active_solves.clear()
    s.backend.navigate(tab.id, base + '/case-' + str(time.time_ns()))
    for _ in range(50):
        try:
            if s.backend.eval_js(tab.id, 'document.readyState', 2) == 'complete':
                break
        except Exception:
            pass
        time.sleep(0.05)
    s.backend.eval_js(tab.id, 'document.body.innerHTML=' + json.dumps(html), 3)
    return tab

GRID = '<div class="recaptcha-challenge" id="grid" style="width:300px;height:180px;background:lightblue"><b class="prompt-text">Choose a square</b><div class="captcha-tile" id="tile-0" style="width:50px;height:50px">X</div></div>'

def start(**kwargs):
    return captcha.solve_captcha(s, tab_id=tab.id, strategy='agent_vision', **kwargs)

def act(res, action, action_id='a'):
    return captcha.solve_captcha(
        s,
        tab_id=tab.id,
        operation='act',
        solve_id=res['solve_id'],
        observation_id=res['observation_id'],
        action_id=action_id,
        action=action,
    )

try:
    for _ in range(100):
        try:
            s.backend.health()
            break
        except Exception:
            time.sleep(0.1)

    tab = s.backend.open_tab(base)
    record('browser', s.backend.browser_version().get('Browser'))

    # F04: Default image output on needs_agent
    page(GRID)
    raw = start()
    first = data(raw)
    record(
        'default_image_output',
        {
            'status': first['status'],
            'python_result_type': type(raw).__name__,
            'has_inline_image': isinstance(raw, list) and len(raw) > 1,
        },
    )

    # F08: Repeated start returns active solve
    second = data(start())
    active = s.get_active_solve(tab.id)
    record(
        'repeated_start',
        {
            'first_id': first['solve_id'],
            'second_id': second['solve_id'],
            'active_id': active.solve_id if active else None,
            'second_is_resumable': second['solve_id'] == (active.solve_id if active else None),
        },
    )

    # F02: Unrelated checked checkbox does not cause false pass
    page(
        '<div class="g-recaptcha" style="width:300px;height:80px"></div><textarea name="g-recaptcha-response" hidden></textarea><div role="checkbox" aria-checked="true">Unrelated newsletter preference</div>'
    )
    result = data(captcha.solve_captcha(s, tab_id=tab.id))
    record(
        'unrelated_checkbox_false_pass',
        {
            'status': result['status'],
            'success': result['success'],
            'attempts': result['attempts'],
        },
    )

    # F01: Verify does not submit business form
    page(
        '<form id="business"><button type="submit">Place order (local fixture only)</button><div class="captcha-box"><canvas id="captcha-img" width="120" height="40"></canvas><input id="answer" type="text"></div></form>'
    )
    s.backend.eval_js(
        tab.id,
        "window.submitCount=0; document.querySelector('form').addEventListener('submit',e=>{e.preventDefault();window.submitCount++})",
        3,
    )
    initial = data(start())
    result = data(act(initial, {'kind': 'verify'}))
    record(
        'verify_submits_business_form',
        {
            'submit_count': s.backend.eval_js(tab.id, 'window.submitCount', 3),
            'status': result['status'],
        },
    )

    # F03: Stale document action rejected before side effects
    page(GRID)
    initial = data(start())
    s.backend.navigate(tab.id, base + '/replaced-document')
    time.sleep(0.2)
    s.backend.eval_js(
        tab.id,
        'document.body.innerHTML=\'<button style="position:fixed;inset:0;width:100vw;height:100vh" onclick="window.wrongClicks++">Unrelated action in new document</button>\';window.wrongClicks=0',
        3,
    )
    result = data(
        act(
            initial,
            {'kind': 'click_point', 'point': {'x': 0.5, 'y': 0.5}, 'image_id': 'wrong-image-id'},
        )
    )
    record(
        'stale_document_action',
        {
            'unrelated_clicks': s.backend.eval_js(tab.id, 'window.wrongClicks', 3),
            'status': result['status'],
        },
    )

    # F09: Optional expected fields JSON encoding
    page('<div id="ready">Account ready</div>')
    fresh = s.resolve(tab_id=tab.id)
    ok, detail, _ = verify_access(
        s, fresh, {'visible_selector': '#ready', 'stable_ms': 0}, time.monotonic() + 5
    )
    record('optional_expected_fields', {'verified': ok, 'detail': detail})

    # F06: Scrolled capture mapping (page coordinates vs viewport coordinates)
    page(GRID)
    s.backend.eval_js(tab.id, "document.body.style.paddingTop='1000px';window.scrollTo(0,850)", 3)
    initial = data(start())
    obs = initial['observation']
    record(
        'scrolled_capture_mapping',
        {
            'scrollY': s.backend.eval_js(tab.id, 'window.scrollY', 3),
            'crop_y_sent_to_page_capture': obs['crop_rect']['y'],
            'widget_viewport_y': s.backend.eval_js(
                tab.id, "document.querySelector('#grid').getBoundingClientRect().y", 3
            ),
        },
    )

    # F05: OOPIF frame discovery and session routing
    page(
        '<iframe id="cross" src="http://localhost:'
        + str(server.server_port)
        + '/child" style="width:300px;height:150px"></iframe>'
    )
    time.sleep(0.5)
    targets = s.backend._command(tab.id, 'Target.getTargets').get('targetInfos', [])
    refs = s.backend.list_frames(tab.id)
    child_refs = [f for f in refs if f.parent_id]
    record(
        'oopif_frame_discovery',
        {
            'iframe_targets': sum(t.get('type') == 'iframe' for t in targets),
            'frame_refs': len(refs),
            'child_refs': len(child_refs),
            'session_ids': [f.session_id for f in refs],
        },
    )
    if child_refs:
        try:
            record(
                'oopif_eval',
                {
                    'value': s.backend.evaluate_in_frame(
                        tab.id, child_refs[0], 'document.body.innerText', 2
                    )
                },
            )
        except Exception as exc:
            record('oopif_eval', {'error': str(exc)})
finally:
    s.close()
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    server.shutdown()
    server.server_close()
    profile.cleanup()

# F07: Atomic Action Deduplication under Concurrent Callers
candidate = {
    'candidate_id': 'c1',
    'provider': 'custom',
    'challenge_kind': 'image_grid',
    'state': 'actionable',
    'confidence': 'high',
    'widget_ref': '#grid',
    'rect_css': {'x': 0, 'y': 0, 'width': 300, 'height': 180},
}
b = FakeBackend(responses={'captcha_detect': {'ok': True, 'candidates': [candidate]}, 'raw': None})
fs = FakeSession(b)
initial = data(captcha.solve_captcha(fs, tab_id='t1', strategy='agent_vision'))

in_flight = threading.Event()
original_handle = AgentVisionSolver.handle_action

def synchronized_handle(self, *args, **kwargs):
    in_flight.set()
    time.sleep(0.1)  # Simulate action execution time while holding in_flight
    return original_handle(self, *args, **kwargs)

def caller(_):
    return captcha.solve_captcha(
        fs,
        tab_id='t1',
        operation='act',
        solve_id=initial['solve_id'],
        observation_id=initial['observation_id'],
        action_id='same-action',
        action={'kind': 'click_point', 'point': {'x': 0.5, 'y': 0.5}},
    )

with patch.object(AgentVisionSolver, 'handle_action', synchronized_handle):
    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(caller, 0)
        assert in_flight.wait(timeout=5)
        f2 = pool.submit(caller, 1)
        res_list = [f1.result(), f2.result()]

record('concurrent_duplicate_action', {
    'click_count': sum(c[0] == 'click_at' for c in b.calls),
    'identical_results': res_list[0] == res_list[1],
})

out_path = Path(__file__).with_name('results.json')
out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
print(f'Verification results saved to {out_path}')
