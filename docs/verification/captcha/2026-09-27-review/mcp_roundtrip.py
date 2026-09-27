import asyncio,json,os,socket,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT/'src'))
from tabpilot.backends.cdp import CDPBackend
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
for key in ('TABPILOT_REMOTE','SSH_CONNECTION'):os.environ.pop(key,None)
with socket.socket() as sock:
    sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
profile=tempfile.TemporaryDirectory(prefix='tabpilot-mcp-review-')
chrome=subprocess.Popen(['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome','--headless=new','--no-first-run',f'--remote-debugging-port={port}',f'--user-data-dir={profile.name}'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
b=CDPBackend(port=port)
async def run():
    env={**os.environ,'PYTHONPATH':str(ROOT/'src')}
    config=StdioServerParameters(command=str(ROOT/'.venv/bin/python'),args=['-m','tabpilot','serve','--backend','cdp','--cdp-port',str(port),'--return-images','auto'],env=env)
    async with stdio_client(config) as (read,write):
        async with ClientSession(read,write) as client:
            await client.initialize()
            tools=await client.list_tools()
            r=await client.call_tool('solve_captcha',{'tab_id':tab.id,'strategy':'agent_vision'})
            out={'tool_count':len(tools.tools),'content_types':[i.type for i in r.content],'is_error':getattr(r,'is_error',getattr(r,'isError',None)),'status':json.loads(next(i.text for i in r.content if i.type=='text'))['status']}
            print(json.dumps(out),flush=True)
            Path(__file__).with_name('mcp_result.json').write_text(json.dumps(out,indent=2))
try:
    for _ in range(100):
        try:b.health();break
        except Exception:time.sleep(.1)
    tab=b.open_tab('about:blank')
    b.eval_js(tab.id,'document.body.innerHTML='+json.dumps('<div id="grid" class="recaptcha-challenge" style="width:300px;height:180px;background:lightblue">Select a square<div class="captcha-tile">X</div></div>'),3)
    asyncio.run(run())
finally:
    b.close();chrome.terminate()
    try:chrome.wait(5)
    except subprocess.TimeoutExpired:chrome.kill();chrome.wait()
    profile.cleanup()
