import sys,json,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
from tabpilot.backends.cdp import CDPBackend
from tabpilot import captcha
from tabpilot.errors import JSError
from conftest import FakeBackend,FakeSession
class Flood:
    def settimeout(self, t): pass
    def send_text(self,text):self.request=json.loads(text);self.start=time.monotonic()
    def recv_text(self):
        time.sleep(.002)
        return json.dumps({'method':'Page.frameNavigated','params':{}} if time.monotonic()-self.start<.10 else {'id':self.request['id'],'result':{}})
backend=CDPBackend()
backend._sockets['t1']=Flood()
start=time.monotonic();out=backend._command('t1','Page.enable',timeout_s=.01)
results={'event_flood_timeout':{'requested_ms':10,'elapsed_ms':round((time.monotonic()-start)*1000),'returned_success':True}}
def fail(_):raise JSError('injected inspection failure')
s=FakeSession(FakeBackend(responses={'captcha_detect':fail}))
results['inspection_error_start']=json.loads(captcha.solve_captcha(s,tab_id='t1'))['status']
print(json.dumps(results,indent=2))
Path(__file__).with_name('fault_results.json').write_text(json.dumps(results,indent=2))
