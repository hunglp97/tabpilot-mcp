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
    Path(__file__).with_name('extended-results.json').write_text(json.dumps(results,indent=2,ensure_ascii=False))
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
    page(GRID);first=start()
    s.backend.eval_js(tab.id,"window.clicks=0;let t=document.querySelector('#tile-0');t.style.background='red';t.onclick=()=>window.clicks++",3)
    out=act(first,{'kind':'select_tile','target_id':'tile-0'})
    record('image_changed_same_prompt',{'clicks':s.backend.eval_js(tab.id,'window.clicks',3),'status':out['status']})
    page('<form><div class="captcha-box"><canvas id="captcha-img" width="120" height="40"></canvas><input id="answer" type="text"></div><div class="captcha-box captcha-passed">Unrelated widget passed</div></form>')
    out=start()
    record('text_sibling_false_pass',{'status':out['status'],'success':out['success'],'attempts':out['attempts']})
    page('<div id="challenge-stage">Check browser</div>')
    s.backend.eval_js(tab.id,"document.title='Just a moment...'",3)
    first=data(captcha.solve_captcha(s,tab_id=tab.id))
    s.backend.eval_js(tab.id,"document.body.innerHTML='<h1>503 Service Unavailable</h1>';document.title='Service Unavailable'",3)
    out=data(captcha.solve_captcha(s,tab_id=tab.id,operation='observe',solve_id=first['solve_id']))
    record('interstitial_503_false_pass',{'status':out['status'],'success':out['success']})
    page(f'<iframe src="http://localhost:{server.server_port}/newgrid-frame" style="width:400px;height:250px"></iframe>')
    time.sleep(.3)
    refs=s.backend.list_frames(tab.id)
    frame=next(f for f in refs if '/newgrid-frame' in f.url)
    frame_content=s.backend.evaluate_in_frame(tab.id,frame,"document.querySelector('#grid')!==null",2)
    detection=data(captcha.detect_captcha(s,tab_id=tab.id));out=start()
    record('iframe_captcha_not_discovered',{'child_has_challenge':frame_content,'detection_status':detection['status'],'candidate_count':len(detection['candidates']),'solve_status':out['status']})
    page(f'<iframe name="FRAME-ONE" src="http://localhost:{server.server_port}/childA"></iframe><iframe name="FRAME-TWO" src="http://localhost:{server.server_port}/childA"></iframe>')
    time.sleep(.3)
    refs=s.backend.list_frames(tab.id);children=[f for f in refs if '/childA' in f.url]
    record('same_url_frame_identity',{'dom_frame_count':2,'returned_count':len(children),'names':[s.backend.evaluate_in_frame(tab.id,f,'window.name',2) for f in children]})
    page('<button id="tile-0">Unrelated action</button>'+GRID.replace('id="tile-0"','id="actual-grid-tile"'))
    s.backend.eval_js(tab.id,"window.foreignClicks=0;window.gridClicks=0;document.querySelector('#tile-0').onclick=()=>window.foreignClicks++;document.querySelector('#actual-grid-tile').onclick=()=>window.gridClicks++",3)
    first=start();out=act(first,{'kind':'select_tile','target_id':'tile-0'})
    record('tile_id_not_widget_scoped',{'status':out['status'],'foreign_clicks':s.backend.eval_js(tab.id,'window.foreignClicks',3),'grid_clicks':s.backend.eval_js(tab.id,'window.gridClicks',3)})
    page(GRID+'<button class="refresh-btn">Unrelated refresh</button>')
    s.backend.eval_js(tab.id,"window.foreignClicks=0;document.querySelector('.refresh-btn').onclick=()=>window.foreignClicks++",3)
    first=start();out=act(first,{'kind':'refresh'})
    record('refresh_not_widget_scoped',{'status':out['status'],'foreign_clicks':s.backend.eval_js(tab.id,'window.foreignClicks',3)})
finally:
    s.close();proc.terminate()
    try:proc.wait(5)
    except subprocess.TimeoutExpired:proc.kill();proc.wait()
    server.shutdown();server.server_close();profile.cleanup()

candidate={'candidate_id':'c1','provider':'custom','challenge_kind':'image_grid','state':'actionable','confidence':'high','widget_ref':'#grid','rect_css':{'x':0,'y':0,'width':300,'height':180}}
class SlowPrecheck(FakeBackend):
    def eval_js(self,tab_id,expression,timeout_s=20):
        if 'widget_found' in expression:
            self.precheck_timeout=timeout_s
            time.sleep(.1)
        return super().eval_js(tab_id,expression,timeout_s)
    def click_at(self,*args,**kwargs):
        self.clicked_after_deadline=time.monotonic()>self.deadline
        return super().click_at(*args,**kwargs)
b=SlowPrecheck(responses={'captcha_detect':{'ok':True,'candidates':[candidate]},'raw':None})
fs=FakeSession(b)
initial=data(captcha.solve_captcha(fs,tab_id='t1',strategy='agent_vision',timeout_ms=10000))
state=fs.get_active_solve('t1');state.deadline_monotonic=time.monotonic()+.05;b.deadline=state.deadline_monotonic
out=data(captcha.solve_captcha(fs,tab_id='t1',operation='act',solve_id=initial['solve_id'],observation_id=initial['observation_id'],action_id='late-click',action={'kind':'click_point','point':{'x':.5,'y':.5},'image_id':initial['observation']['image_id']}))
record('dispatch_after_deadline',{'remaining_at_start_ms':50,'precheck_timeout_s':b.precheck_timeout,'clicked_after_deadline':b.clicked_after_deadline,'status':out['status']})
