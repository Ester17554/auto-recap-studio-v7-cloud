import os, json, uuid, asyncio, re, subprocess, threading, time, csv, io
from pathlib import Path
from urllib.parse import unquote
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

ROOT = Path(os.environ.get('RECAP_DATA','/data/recap')).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
WEB = Path(__file__).resolve().parent
app = FastAPI(title='Auto Recap Studio V7 Cloud')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])
jobs = {}
lock = threading.Lock()

@app.get('/health')
def health():
    return {'ok': True, 'version': 'V7 Cloud', 'ffmpeg': ffmpeg_version()}

@app.get('/')
def index():
    return FileResponse(WEB/'index.html')
@app.get('/app.js')
def appjs(): return FileResponse(WEB/'app.js', media_type='application/javascript')
@app.get('/styles.css')
def css(): return FileResponse(WEB/'styles.css', media_type='text/css')
@app.get('/manifest.json')
def manifest(): return FileResponse(WEB/'manifest.json', media_type='application/manifest+json')

def ffmpeg_version():
    try:
        p=subprocess.run(['ffmpeg','-version'],capture_output=True,text=True,timeout=5)
        return p.stdout.splitlines()[0] if p.stdout else 'ffmpeg'
    except Exception: return 'unavailable'

def jobdir(jid):
    d=ROOT/jid; d.mkdir(parents=True,exist_ok=True); return d

@app.post('/jobs')
async def create(req: Request):
    meta=await req.json()
    jid=str(uuid.uuid4())
    d=jobdir(jid)
    (d/'meta.json').write_text(json.dumps(meta,ensure_ascii=False),encoding='utf-8')
    with lock: jobs[jid]={'status':'created','progress':0,'message':'Aguardando upload','received':0,'total':int(meta.get('movie_size',0) or 0)}
    return {'id':jid}

@app.get('/jobs/{jid}/upload-status')
def upload_status(jid: str):
    if jid not in jobs: raise HTTPException(404,'job')
    p=jobdir(jid)/'movie.bin'
    return {'offset':p.stat().st_size if p.exists() else 0}

@app.post('/upload')
async def upload(req: Request):
    jid=req.headers.get('X-Job-ID')
    if not jid or jid not in jobs: raise HTTPException(404,'job')
    kind=req.headers.get('X-File-Kind','movie')
    idx=int(req.headers.get('X-Audio-Index','0'))
    off=int(req.headers.get('X-Offset','0'))
    name=unquote(req.headers.get('X-File-Name',''))
    d=jobdir(jid)
    p=d/'movie.bin' if kind=='movie' else d/f'audio_{idx}.bin'
    # Append/overwrite exactly at requested offset so retries are safe.
    with open(p,'r+b' if p.exists() else 'wb') as f:
        f.seek(off)
        async for chunk in req.stream():
            if chunk:
                f.write(chunk)
    size=p.stat().st_size
    jobs[jid]['received']=size
    jobs[jid]['total']=int(req.headers.get('X-File-Size',jobs[jid].get('total',0)) or 0)
    return {'offset':size,'name':p.name,'original_name':name}

@app.post('/jobs/{jid}/run')
async def run(jid: str):
    if jid not in jobs: raise HTTPException(404,'job')
    if jobs[jid].get('worker'): return {'ok':True,'message':'já processando'}
    jobs[jid]['worker']=True
    asyncio.create_task(process(jid))
    return {'ok':True}

@app.get('/jobs/{jid}')
def get(jid: str):
    if jid not in jobs: raise HTTPException(404,'job')
    return jobs[jid]

@app.get('/jobs/{jid}/result.csv')
def result_csv(jid: str):
    if jid not in jobs: raise HTTPException(404,'job')
    p=jobdir(jid)/'result.json'
    if not p.exists(): raise HTTPException(404,'resultado')
    d=json.loads(p.read_text(encoding='utf-8'))
    out=io.StringIO(); w=csv.writer(out); w.writerow(['trecho','inicio','fim','cena','confianca'])
    for m in d.get('matches',[]): w.writerow([m['text'],m['start'],m['end'],m['scene_id'],m['confidence']])
    return JSONResponse(content={'csv':out.getvalue()})

def split_script(s,n=500):
    # Preserve paragraph boundaries first, then wrap long paragraphs.
    out=[]
    for p in re.split(r'\n\s*\n',s):
        p=p.strip()
        if not p: continue
        cur=''
        for w in p.split():
            if cur and len(cur)+len(w)+1>n:
                out.append(cur); cur=w
            else: cur=(cur+' '+w).strip()
        if cur: out.append(cur)
    if not out and s.strip(): out=[s.strip()]
    return out[:400]

def parse_scene_times(text):
    # showinfo lines contain pts_time and scene_score when select emits a frame.
    times=[]
    for line in text.splitlines():
        if 'showinfo' not in line: continue
        m=re.search(r'pts_time:([0-9.]+)',line)
        if m:
            t=float(m.group(1))
            sm=re.search(r'lavfi.scene_score=([0-9.]+)',line)
            score=float(sm.group(1)) if sm else 0.0
            times.append((t,score))
    return times

def ffprobe_duration(path):
    p=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(path)],capture_output=True,text=True,timeout=120)
    try:return float(p.stdout.strip())
    except:return 0.0

def detect_scenes(path, step, jid):
    dur=ffprobe_duration(path)
    # Fast mode: inspect keyframes first. This avoids the iPhone-style frame-by-frame seeking.
    # If there are too few keyframes, fall back to a low-rate scene scan.
    cmd=['ffmpeg','-hide_banner','-loglevel','info','-skip_frame','nokey','-i',str(path),'-vf',"select='gt(scene,0.18)',showinfo",'-an','-f','null','-']
    jobs[jid].update(message='Detectando cortes por keyframes · modo rápido',progress=.05)
    try:
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=max(900,int(dur*3+300)))
        times=parse_scene_times(p.stderr)
    except subprocess.TimeoutExpired:
        times=[]
    if len(times)<8:
        jobs[jid].update(message='Keyframes insuficientes · análise leve',progress=.15)
        cmd=['ffmpeg','-hide_banner','-loglevel','info','-i',str(path),'-vf',f"fps=1/{max(4,float(step))},scale=640:-2,select='gt(scene,0.12)',showinfo",'-an','-f','null','-']
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=max(1200,int(dur*6+600)))
        times=parse_scene_times(p.stderr)
    times=sorted({round(t,3):s for t,s in times}.items())
    points=[0.0]+[t for t,_ in times if t>0 and t<dur-0.5]
    # Keep a manageable number of scene anchors, including periodic anchors for quiet scenes.
    anchors=[]
    for t in points:
        if not anchors or t-anchors[-1] >= max(4,float(step)/2): anchors.append(t)
    for t in [i*float(step) for i in range(1,int(dur/float(step))+1)]:
        if not anchors or min(abs(t-a) for a in anchors)>float(step)*0.35: anchors.append(min(t,dur))
    anchors=sorted(set(round(x,3) for x in anchors if x<dur))
    scenes=[]
    for i,t in enumerate(anchors):
        end=anchors[i+1] if i+1<len(anchors) else dur
        if end-t<1: continue
        scenes.append({'id':f'S{i+1:04d}','start':t,'end':end,'duration':end-t})
    return scenes,dur

async def process(jid):
    try:
        d=jobdir(jid); meta=json.loads((d/'meta.json').read_text(encoding='utf-8')); movie=d/'movie.bin'
        if not movie.exists(): raise RuntimeError('Filme ainda não chegou ao servidor.')
        jobs[jid].update(status='processing',progress=.02,message='Preparando vídeo')
        sc,dur=await asyncio.to_thread(detect_scenes,movie,float(meta.get('sample',12)),jid)
        jobs[jid].update(progress=.75,message=f'{len(sc)} cenas encontradas · montando mapa')
        chunks=split_script(meta.get('script',''))
        # V7.1: timeline-aware mapping. It is intentionally explicit that this is a visual shortlist,
        # not a claim of exact semantic recognition. Candidate windows are distributed across the movie
        # using the narration/script duration when available.
        matches=[]
        audio_total=float(meta.get('audio_duration',0) or 0)
        script_total=max(1,len(chunks))
        for i,t in enumerate(chunks):
            target=(i/max(1,script_total-1))*max(0,dur-1) if audio_total<=0 else min(dur-1,(i/max(1,script_total))*audio_total)
            idx=min(range(len(sc)),key=lambda k: abs(sc[k]['start']-target)) if sc else 0
            cands=[]
            for delta in range(-int(meta.get('candidates',3))+1,int(meta.get('candidates',3))):
                k=max(0,min(len(sc)-1,idx+delta)); s=sc[k]
                cands.append({'scene_id':s['id'],'start':s['start'],'end':s['end']})
            best=sc[idx]
            conf=0.30 if not audio_total else 0.42
            matches.append({'text':t,'start':best['start'],'end':best['end'],'scene_id':best['id'],'confidence':conf,'candidates':cands})
        result={'duration':dur,'scenes':sc,'matches':matches,'mode':'cloud-fast-shortlist'}
        (d/'result.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        jobs[jid].update(status='done',progress=1,message='Mapa concluído',result=result)
    except Exception as e:
        jobs[jid].update(status='error',message=str(e),progress=1)
    finally:
        jobs[jid].pop('worker',None)

# Serve static files defensively.
@app.get('/{path:path}')
def static_path(path: str):
    p=(WEB/path).resolve()
    if WEB.resolve() in p.parents and p.is_file(): return FileResponse(p)
    raise HTTPException(404,'not found')
