"""Owned local server and wall-time-bounded diagnostic; preserve runtime logs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import build_opener,ProxyHandler


def main():
    out=Path('paper/results/qwen27b_thinking_supervised_v002_2026.09.14-r2')
    out.mkdir(parents=True,exist_ok=False)
    env=os.environ.copy()
    env.update(OLLAMA_HOST='127.0.0.1:11449',OLLAMA_MODELS='/private/tmp/harmo-gemma4-runtime.AD9diU/models',OLLAMA_NO_CLOUD='1')
    state=dict(status='starting',started_at=time.time(),timeout_seconds=240,max_tokens=2048)
    def save(): (out/'status.json').write_text(json.dumps(state,indent=2))
    save();server=None
    with (out/'server.log').open('w') as serverlog,(out/'runner.log').open('w') as runnerlog:
        try:
            server=subprocess.Popen(['/private/tmp/harmo-gemma4-runtime.AD9diU/ollama','serve'],env=env,stdout=serverlog,stderr=subprocess.STDOUT)
            opener=build_opener(ProxyHandler({}))
            for _ in range(30):
                if server.poll() is not None:raise RuntimeError('owned_server_exited')
                try:
                    with opener.open('http://127.0.0.1:11449/api/version',timeout=1) as response:
                        state['runtime']=json.load(response)
                    break
                except OSError:time.sleep(0.5)
            else:raise RuntimeError('server_not_ready')
            state['status']='running';save()
            command=[sys.executable,'scripts/paper/run_local_llm_pilot.py','--packet',
                'paper/results/llm_transfer_packet_v001_2026.09.14-r1/model_input.json',
                '--model','qwen3.5:27b','--output',str(out/'run'),'--port','11449',
                '--case-index','1','--evidence-ids','--target-index','3','--thinking','--max-tokens','2048']
            result=subprocess.run(command,env=env,stdout=runnerlog,stderr=subprocess.STDOUT,timeout=240)
            state.update(status='completed' if result.returncode==0 else 'failed',returncode=result.returncode)
        except subprocess.TimeoutExpired:
            state['status']='wall_time_limit'
        except Exception as exc:
            state.update(status='failed',error=str(exc))
        finally:
            if server is not None:
                server.terminate()
                try:server.wait(timeout=10)
                except subprocess.TimeoutExpired:server.kill();server.wait()
                state['server_returncode']=server.returncode
            state['finished_at']=time.time();save()


if __name__=='__main__':main()
