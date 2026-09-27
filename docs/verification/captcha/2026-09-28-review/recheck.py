"""Independent local-fixture recheck. Never uses provider pages or a user profile."""
import sys,os,json,time,socket,tempfile,subprocess,threading,http.server
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[4]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
from tabpilot import captcha
from tabpilot.session import Session
from tabpilot.config import Config
from tabpilot.captcha_solvers.agent_vision import AgentVisionSolver
from tabpilot.captcha_verify import verify_widget_passed,verify_access
from tabpilot.captcha_state import CaptchaCandidate
from tabpilot.errors import JSError
from tabpilot.backends.base import Capability
from conftest import FakeBackend,FakeSession
results={}
def record(name,value):
    results[name]=value;print(name+': '+json.dumps(value,ensure_ascii=False),flush=True)
    Path(__file__).with_name('recheck-results.json').write_text(json.dumps(results,indent=2,ensure_ascii=False))
def data(res):return json.loads(res[0] if isinstance(res,list) else res)
GRID='<div class="recaptcha-challenge" id="grid" style="width:300px;height:180px;background:lightblue"><b class="prompt-text">Choose the blue square</b><div class="captcha-tile" id="tile-0" style="width:50px;height:50px;background:blue">square</div></div>'
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body=('<html><title>Review fixture</title><body>'+('FRAME-B' if self.path.startswith('/childB') else 'FRAME-A' if self.path.startswith('/childA') else GRID if self.path.startswith('/newgrid') else '<div id="ready">Account ready</div>')+'</body></html>').encode()
        self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(body)
    def log_message(self,*args):pass
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
profile=tempfile.TemporaryDirectory(prefix='tabpilot-review3-')
proc=subprocess.Popen(['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome','--headless=new','--no-first-run','--no-default-browser-check','--site-per-process',f'--remote-debugging-port={port}',f'--user-data-dir={profile.name}'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
s=Session(Config(backend='cdp',cdp_port=port,return_images='auto'))
base=f'http://127.0.0.1:{server.server_port}'
def navigate(path):
    target=base+path
    s.backend.navigate(tab.id,target)
    for _ in range(80):
        try:
            state=s.backend.eval_js(tab.id,'({url:location.href,ready:document.readyState})',2)
            if state['url']==target and state['ready']=='complete':return
        except Exception:pass
        time.sleep(.025)
    raise RuntimeError('Fixture navigation failed')
def page(html):
    s._active_solves.clear();navigate('/case-'+str(time.time_ns()))
    s.backend.eval_js(tab.id,'document.body.innerHTML='+json.dumps(html),3)
def start(**kw):return data(captcha.solve_captcha(s,tab_id=tab.id,strategy='agent_vision',**kw))
def act(initial,action,ident='a'):
    return data(captcha.solve_captcha(s,tab_id=tab.id,operation='act',solve_id=initial['solve_id'],observation_id=initial['observation_id'],action_id=ident,action=action))
try:
    for _ in range(100):
        try:s.backend.health();break
        except Exception:time.sleep(.1)
    tab=s.backend.open_tab(base)
    record('browser',s.backend.browser_version().get('Browser'))
    # Confirm fixed baseline checks without weakening oracle.
    page(GRID); first=start(); second=start()
    record('F08_sequential_start',{'same_id':first['solve_id']==second['solve_id']})
    page('<div class="g-recaptcha" style="width:300px;height:80px"></div><textarea name="g-recaptcha-response" hidden></textarea><div role="checkbox" aria-checked="true">Newsletter</div>')
    out=data(captcha.solve_captcha(s,tab_id=tab.id))
    record('F02_newsletter_baseline',{'status':out['status'],'success':out['success']})
    page('<div id="ready">Account ready</div>')
    ok,detail,_=verify_access(s,s.resolve(tab_id=tab.id),{'visible_selector':'#ready','stable_ms':0},time.monotonic()+5)
    record('F09_selector_only',{'verified':ok,'detail':detail})
    # Remaining Verify scope escapes into another widget.
    page('<div class="captcha-box"><canvas id="captcha-img" width="120" height="40"></canvas><input id="answer" type="text"></div><form id="other"><div class="captcha-box"><button id="captcha-verify-btn" type="submit">Verify and send unrelated form</button></div></form>')
    s.backend.eval_js(tab.id,"window.submits=0;document.querySelector('#other').onsubmit=e=>{e.preventDefault();window.submits++}",3)
    first=start();out=act(first,{'kind':'verify'})
    record('F01_cross_widget_verify',{'submit_count':s.backend.eval_js(tab.id,'window.submits',3),'status':out['status']})
    # Missing document id after navigation is treated as permission to use old observation.
    page(GRID);first=start();navigate('/newgrid-'+str(time.time_ns()))
    before=s.backend.eval_js(tab.id,"window.__tabpilot_doc_id || null",3)
    s.backend.eval_js(tab.id,"window.clicks=0;document.querySelector('#grid').onclick=()=>window.clicks++",3)
    out=act(first,{'kind':'click_point','point':{'x':.5,'y':.5},'image_id':first['observation']['image_id']})
    record('F03_new_document_same_selector',{'document_id_before_action':before,'clicks':s.backend.eval_js(tab.id,'window.clicks',3),'status':out['status']})
    # Same document, old image replaced but IDs/geometry unchanged.
    page(GRID);first=start()
    s.backend.eval_js(tab.id,"window.clicks=0;document.querySelector('.prompt-text').textContent='Do not choose the red square';let t=document.querySelector('#tile-0');t.style.background='red';t.onclick=()=>window.clicks++",3)
    out=act(first,{'kind':'select_tile','target_id':'tile-0'})
    record('F03_changed_challenge',{'clicks':s.backend.eval_js(tab.id,'window.clicks',3),'status':out['status']})
    # UI success in a sibling widget within the same form remains a false pass.
    page('<form>'+GRID+'<div class="captcha-passed">Other widget passed</div></form>')
    out=start()
    record('F02_sibling_pass',{'status':out['status'],'success':out['success'],'attempts':out['attempts']})
    # Interstitial disappearing into an error screen is not solved access.
    page('<div id="challenge-stage">Check browser</div>')
    s.backend.eval_js(tab.id,"document.title='Just a moment...'",3)
    first=data(captcha.solve_captcha(s,tab_id=tab.id))
    s.backend.eval_js(tab.id,"document.body.innerHTML='<h1>403 Access denied</h1>';document.title='Access denied'",3)
    out=data(captcha.solve_captcha(s,tab_id=tab.id,operation='observe',solve_id=first['solve_id']))
    record('F02_interstitial_error_page',{'status':out['status'],'success':out['success']})
    # New token takes precedence over the explicit expired/error UI.
    page('<div class="g-recaptcha"><div class="rc-anchor-error">Expired</div></div><textarea id="response" name="g-recaptcha-response">fresh-but-invalid-token</textarea>')
    candidate=CaptchaCandidate(candidate_id='c',provider='recaptcha',challenge_kind='checkbox',state='expired',confidence='high',widget_ref='.g-recaptcha',response_field_ref='#response')
    ok,detail,_=verify_widget_passed(s,tab,candidate,None)
    record('F02_fresh_token_with_error',{'passed':ok,'detail':detail})
    # Crop describes parent box, click_point silently switches to child canvas rect.
    page('<div class="captcha-box" style="width:400px;height:200px;background:lightblue;position:relative"><canvas id="captcha-img" width="100" height="40"></canvas><input id="answer" type="text"><button id="captcha-verify-btn" style="position:absolute;left:250px;top:100px;width:100px;height:40px">Verify</button></div>')
    s.backend.eval_js(tab.id,"window.verifyClicks=0;window.canvasClicks=0;document.querySelector('#captcha-img').onclick=()=>window.canvasClicks++;document.querySelector('button').onclick=()=>window.verifyClicks++",3)
    first=start();r=first['observation']['viewport_rect'];pos=s.backend.eval_js(tab.id,"(()=>{let r=document.querySelector('button').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()",3)
    point={'x':(pos['x']-r['x'])/r['width'],'y':(pos['y']-r['y'])/r['height']}
    out=act(first,{'kind':'click_point','point':point,'image_id':first['observation']['image_id']})
    record('F06_crop_input_mismatch',{'verify_clicks':s.backend.eval_js(tab.id,'window.verifyClicks',3),'canvas_clicks':s.backend.eval_js(tab.id,'window.canvasClicks',3),'status':out['status']})
    # Distinct local OOPIFs in two tabs: only each own child is allowed.
    page(f'<iframe src="http://localhost:{server.server_port}/childA"></iframe>')
    other=s.backend.open_tab(base+'/tabB');time.sleep(.15)
    s.backend.eval_js(other.id,'document.body.innerHTML='+json.dumps(f'<iframe src="http://localhost:{server.server_port}/childB"></iframe>'),3)
    time.sleep(.4)
    refs=s.backend.list_frames(tab.id)
    markers=[]
    for ref in refs:
        if '/childA' in ref.url or '/childB' in ref.url:
            try:markers.append({'url_path':ref.url.split('/')[-1],'value':s.backend.evaluate_in_frame(tab.id,ref,'document.body.innerText',2).strip()})
            except Exception as exc:markers.append({'error':str(exc)})
    record('F05_cross_tab_frame_leak',{'children_returned_for_A':markers,'has_foreign_B':any(x.get('value')=='FRAME-B' for x in markers)})
finally:
    s.close();proc.terminate()
    try:proc.wait(5)
    except subprocess.TimeoutExpired:proc.kill();proc.wait()
    server.shutdown();server.server_close();profile.cleanup()

# Offline fault injections: side-effect sent, then response failed.
candidate={'candidate_id':'c1','provider':'custom','challenge_kind':'image_grid','state':'actionable','confidence':'high','widget_ref':'#grid','rect_css':{'x':0,'y':0,'width':300,'height':180}}
def fake(backend=None):return FakeSession(backend or FakeBackend(responses={'captcha_detect':{'ok':True,'candidates':[candidate]},'raw':None}))
fs=fake();initial=data(captcha.solve_captcha(fs,tab_id='t1',strategy='agent_vision'))
def dispatched_then_lost(self,session,tab,state,action):
    session.backend.click_at(tab.id,50,50);raise JSError('lost response after dispatch')
errors=[]
with patch.object(AgentVisionSolver,'handle_action',dispatched_then_lost):
    for _ in range(2):
        try:captcha.solve_captcha(fs,tab_id='t1',operation='act',solve_id=initial['solve_id'],observation_id=initial['observation_id'],action_id='same-action',action={'kind':'click_point','point':{'x':.5,'y':.5}})
        except Exception as exc:errors.append(type(exc).__name__)
record('F07_unknown_outcome_replay',{'click_count':sum(x[0]=='click_at' for x in fs.backend.calls),'exceptions':errors})
class SlowCapture(FakeBackend):
    def screenshot(self,*args,**kwargs):
        self.capture_timeout=kwargs.get('timeout_s');time.sleep(.15);return self.screenshot_bytes
b=SlowCapture(responses={'captcha_detect':{'ok':True,'candidates':[candidate]},'raw':None});fs=fake(b)
t=time.monotonic();out=data(captcha.solve_captcha(fs,tab_id='t1',strategy='agent_vision',timeout_ms=50))
record('F10_solve_deadline',{'requested_ms':50,'elapsed_ms':round((time.monotonic()-t)*1000),'capture_timeout_s':b.capture_timeout,'status':out['status'],'remaining_ms':out['remaining_ms']})
fs=fake()
try:captcha.solve_captcha(fs,tab_id='t1',strategy='does-not-exist')
except Exception as exc:record('invalid_strategy_leaves_lease',{'error':type(exc).__name__,'active_lease':fs.get_active_solve('t1') is not None})
