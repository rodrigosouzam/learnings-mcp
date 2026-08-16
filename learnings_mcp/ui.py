"""
Tiny local web UI for browsing/managing learnings. Stdlib only, binds to 127.0.0.1.

    learnings ui [--port 8765] [--no-browser]

Serves one self-contained page + a small JSON API backed by the same Store. A fresh
Store (sqlite connection) is created per request so it's thread-safe under the
threading HTTP server; the embedding model is loaded lazily only on semantic search.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .store import Store, _row_to_dict, _COLS, CORE_CAP, STALE_DAYS

# Set by serve(); when non-empty, every request must present it (?token= or X-Token header).
_TOKEN = ""


def _all_rows(store, workspace, q):
    """List (not semantic) rows for a workspace ('all' = everything)."""
    db = store.db
    if workspace in (None, "", "all", "*"):
        rows = db.execute(f"SELECT {_COLS} FROM learnings ORDER BY is_core DESC, created_at DESC").fetchall()
    else:
        rows = db.execute(
            f"SELECT {_COLS} FROM learnings WHERE workspace IN (?, 'base') "
            f"ORDER BY is_core DESC, created_at DESC",
            (workspace,),
        ).fetchall()
    out = [_row_to_dict(r) for r in rows]
    if q:
        ql = q.lower()
        out = [r for r in out if ql in r["title"].lower() or ql in r["content"].lower()
               or any(ql in t.lower() for t in r["tags"])]
    return out


def _stats(store):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    stale_cut = (now - timedelta(days=STALE_DAYS)).isoformat()
    cold_cut = (now - timedelta(days=30)).isoformat()
    q = store.db.execute
    return {
        "total": q("SELECT count(*) c FROM learnings").fetchone()["c"],
        "core": q("SELECT count(*) c FROM learnings WHERE is_core=1").fetchone()["c"],
        "stale": q("SELECT count(*) c FROM learnings WHERE verified_at < ?", (stale_cut,)).fetchone()["c"],
        "cold": q("SELECT count(*) c FROM learnings WHERE hit_count=0 AND created_at < ?", (cold_cut,)).fetchone()["c"],
    }


def _mappings():
    """Directory → workspace map from ~/.learnings/workspaces.txt (or LEARNINGS_WORKSPACES)."""
    from .workspace import _roots_map
    return [{"dir": k, "workspace": v} for k, v in sorted(_roots_map().items())]


def _projects(store):
    """One row per workspace (deduped) — union of workspaces that have learnings and
    workspaces that folders map to — with counts and the directories that feed each."""
    db = {r["workspace"]: (r["n"], r["c"] or 0) for r in store.db.execute(
        "SELECT workspace, COUNT(*) n, SUM(is_core) c FROM learnings GROUP BY workspace").fetchall()}
    dirs = {}
    for m in _mappings():
        dirs.setdefault(m["workspace"], []).append("~/" + m["dir"])
    out = []
    for name in sorted(set(db) | set(dirs)):
        n, c = db.get(name, (0, 0))
        out.append({"name": name, "count": n, "core": c, "dirs": dirs.get(name, [])})
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _authed(self):
        if not _TOKEN:
            return True
        if parse_qs(urlparse(self.path).query).get("token", [""])[0] == _TOKEN:
            return True
        return self.headers.get("X-Token") == _TOKEN

    def do_GET(self):
        if not self._authed():
            return self._send(401, {"error": "unauthorized — open the URL with ?token=... shown in the terminal"})
        u = urlparse(self.path)
        if u.path == "/":
            return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        store = Store()
        try:
            if u.path == "/api/state":
                return self._send(200, {"projects": _projects(store), "core_cap": CORE_CAP,
                                        "stats": _stats(store)})
            if u.path == "/api/learnings":
                qs = parse_qs(u.query)
                ws = (qs.get("workspace") or ["all"])[0]
                q = (qs.get("q") or [""])[0]
                mode = (qs.get("mode") or ["list"])[0]
                if mode == "semantic" and q:
                    sws = "*" if ws in ("all", "", None) else ws
                    return self._send(200, store.search(q, workspace=sws, limit=25))
                return self._send(200, _all_rows(store, ws, q))
            return self._send(404, {"error": "not found"})
        finally:
            store.close()

    def do_POST(self):
        if not self._authed():
            return self._send(401, {"error": "unauthorized"})
        u = urlparse(self.path)
        store = Store()
        try:
            body = self._body()
            parts = u.path.strip("/").split("/")
            if u.path == "/api/backup":
                return self._send(200, store.backup())
            if u.path == "/api/learnings":
                res = store.create(
                    title=body.get("title", ""), content=body.get("content", ""),
                    tags=body.get("tags") or [], project=body.get("project") or None,
                    workspace=body.get("workspace") or None, is_core=bool(body.get("is_core")),
                    source="ui", force=bool(body.get("force")),
                )
                return self._send(200, res if res.get("status") != "created" else {"status": "created", "learning": res["learning"]})
            if len(parts) == 4 and parts[:2] == ["api", "learnings"] and parts[3] == "core":
                try:
                    r = store.set_core(parts[2], bool(body.get("value")))
                except ValueError as e:
                    return self._send(200, {"error": str(e)})
                return self._send(200, r or {"error": "not found"})
            if len(parts) == 4 and parts[:2] == ["api", "learnings"] and parts[3] == "enrich":
                r = store.enrich(parts[2], body.get("context", ""))
                return self._send(200, r or {"error": "not found"})
            if len(parts) == 4 and parts[:2] == ["api", "learnings"] and parts[3] == "verify":
                r = store.verify(parts[2])
                return self._send(200, r or {"error": "not found"})
            return self._send(404, {"error": "not found"})
        finally:
            store.close()

    def do_DELETE(self):
        if not self._authed():
            return self._send(401, {"error": "unauthorized"})
        parts = urlparse(self.path).path.strip("/").split("/")
        store = Store()
        try:
            if len(parts) == 3 and parts[:2] == ["api", "learnings"]:
                return self._send(200, {"deleted": store.remove(parts[2])})
            return self._send(404, {"error": "not found"})
        finally:
            store.close()


def _lan_ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def serve(port=8765, host="127.0.0.1", token=None, open_browser=True):
    global _TOKEN
    loopback = host in ("127.0.0.1", "localhost", "::1")
    # Secure-by-default: any non-loopback bind requires a token; auto-generate if none given.
    if not loopback and not token:
        import secrets
        token = secrets.token_urlsafe(16)
    _TOKEN = token or ""

    httpd = ThreadingHTTPServer((host, port), Handler)
    shown = (_lan_ip() or host) if host == "0.0.0.0" else host
    url = f"http://{shown}:{port}" + (f"/?token={token}" if token else "")
    print(f"learnings UI → {url}")
    if not loopback:
        print(f"  bound to {host}:{port} — reachable from your LAN")
        print("  token required; keep this URL private. Stop with Ctrl-C.")
    else:
        print("  (local only) Ctrl-C to stop")
    if loopback and open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
        httpd.shutdown()


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Learnings</title>
<style>
:root{--bg:#0f1115;--panel:#171a21;--panel2:#1f2530;--line:#2a3140;--fg:#e6e9ef;--mut:#8b94a7;--acc:#6ea8fe;--core:#f6c453;--danger:#ef6b6b}
@media(prefers-color-scheme:light){:root{--bg:#f6f7f9;--panel:#fff;--panel2:#eef1f5;--line:#dde2ea;--fg:#1a1f2b;--mut:#5b6577;--acc:#2f6fed}}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--fg)}
.app{display:flex;height:100vh}
.side{width:230px;flex:none;background:var(--panel);border-right:1px solid var(--line);padding:14px;overflow:auto}
.side h1{font-size:15px;margin:0 0 12px;display:flex;align-items:center;gap:8px}
.ws{padding:7px 10px;border-radius:8px;cursor:pointer;display:flex;justify-content:space-between;gap:8px;color:var(--mut)}
.ws:hover{background:var(--panel2)}.ws.on{background:var(--panel2);color:var(--fg)}
.ws .n{font-variant-numeric:tabular-nums;font-size:12px;opacity:.8}
.main{flex:1;display:flex;flex-direction:column;overflow:hidden}
.bar{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;gap:8px;align-items:center;background:var(--panel)}
input,textarea,select{background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font:inherit}
input#q{flex:1}
button{background:var(--panel2);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:8px 12px;cursor:pointer}
button:hover{border-color:var(--acc)}button.pri{background:var(--acc);color:#fff;border-color:var(--acc)}
.seg{display:flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.seg button{border:0;border-radius:0}.seg button.on{background:var(--acc);color:#fff}
.list{flex:1;overflow:auto;padding:16px;display:flex;flex-direction:column;gap:10px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.card h3{margin:0 0 6px;font-size:14px;display:flex;align-items:center;gap:8px}
.badges{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0}
.b{font-size:11px;padding:2px 7px;border-radius:20px;background:var(--panel2);color:var(--mut)}
.b.ws{color:var(--acc)}.b.core{color:var(--core);border:1px solid var(--core)}.b.stale{color:var(--danger);border:1px solid var(--danger)}
.b.ref{cursor:pointer}.refs{margin:2px 0 8px;padding:8px 10px;background:var(--panel2);border-radius:8px;font-size:12px}
.refrow{padding:2px 0;color:var(--mut)}.refrow b{color:var(--fg)}.rp{font-family:monospace;font-size:11px;opacity:.7;word-break:break-all}
.content{color:var(--fg);white-space:pre-wrap;margin-top:6px}.content.clip{max-height:3em;overflow:hidden;-webkit-mask-image:linear-gradient(#000 60%,transparent)}
.row{display:flex;gap:8px;margin-top:10px}.row button{padding:5px 10px;font-size:12px}
.star{cursor:pointer;color:var(--mut)}.star.on{color:var(--core)}
.empty{color:var(--mut);text-align:center;margin-top:40px}
.content code{background:var(--panel2);padding:1px 5px;border-radius:4px;font-size:12.5px}
.content a.lnk{color:var(--acc);text-decoration:none;border-bottom:1px dotted var(--acc)}
.stats{margin-top:14px;padding-top:12px;border-top:1px solid var(--line);color:var(--mut);font-size:12px;line-height:1.8}
.stats b{color:var(--fg);font-weight:600}
.wshead{display:none;padding:12px 16px;border-bottom:1px solid var(--line);background:var(--panel)}
.wshead .wsname{font-weight:600;font-size:15px;margin-right:10px}
.wshead .wsdirs code{font-size:11px;background:var(--panel2);padding:2px 6px;border-radius:5px;margin-right:5px;color:var(--mut)}
.wshead .wslbl{color:var(--mut);font-size:11px;margin-right:6px}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.5);display:none;align-items:center;justify-content:center}
.modal.on{display:flex}.dlg{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;width:min(560px,92vw);display:flex;flex-direction:column;gap:10px}
.dlg input,.dlg textarea{width:100%}.dist{font-size:11px;color:var(--mut);font-variant-numeric:tabular-nums}
</style></head><body><div class="app">
<div class="side"><h1>📓 Learnings</h1><div id="wsList"></div><div class="stats" id="stats"></div></div>
<div class="main">
  <div class="bar">
    <input id="q" placeholder="Filter or search…" autocomplete="off">
    <div class="seg"><button id="mList" class="on">Filter</button><button id="mSem">Semantic</button></div>
    <button id="coreBtn" title="show only always-on core learnings">★ core</button>
    <button id="backupBtn" title="save a timestamped copy of learnings.db">⇩ Backup</button>
    <button class="pri" id="addBtn">+ Add</button>
  </div>
  <div id="wsHeader" class="wshead"></div>
  <div class="list" id="list"></div>
</div></div>
<div class="modal" id="modal"><div class="dlg">
  <h3 style="margin:0">New learning</h3>
  <input id="f_title" placeholder="Title">
  <textarea id="f_content" rows="5" placeholder="Content — what was learned & how to apply it"></textarea>
  <input id="f_tags" placeholder="tags, comma, separated">
  <div class="row"><input id="f_ws" placeholder="workspace (e.g. project-a / base)" style="flex:1">
  <label style="display:flex;align-items:center;gap:6px;color:var(--mut)"><input type="checkbox" id="f_core" style="width:auto"> core</label></div>
  <div id="f_err" style="color:var(--danger);font-size:12px"></div>
  <div class="row" style="justify-content:flex-end"><button id="cancel">Cancel</button><button class="pri" id="save">Save</button></div>
</div></div>
<script>
let WS="all", MODE="list", CAP=15, CORE_ONLY=false, PROJECTS=[];
const TOKEN=new URLSearchParams(location.search).get('token')||'';
const $=s=>document.querySelector(s);
const api=(u,o={})=>{o.headers=Object.assign({'X-Token':TOKEN},o.headers||{});return fetch(u,o).then(r=>r.json());};
async function loadState(){const s=await api('/api/state');CAP=s.core_cap;PROJECTS=s.projects||[];
  const total=PROJECTS.reduce((a,w)=>a+w.count,0);
  const rows=[{name:'all',count:total,core:0,dirs:[]}].concat(PROJECTS);
  $('#wsList').innerHTML=rows.map(w=>`<div class="ws ${w.name===WS?'on':''}" data-w="${w.name}">
    <span>${esc(w.name)}</span><span class="n">${w.count}${w.core?` · ★${w.core}`:''}</span></div>`).join('');
  document.querySelectorAll('.ws').forEach(e=>e.onclick=()=>{WS=e.dataset.w;loadState();load()});
  const st=s.stats||{};
  $('#stats').innerHTML=`<b>${st.total||0}</b> learnings · <b>${st.core||0}</b> core (cap ${CAP})<br>`+
    `<b>${st.stale||0}</b> stale · <b>${st.cold||0}</b> never used`;
  renderHeader();}
function renderHeader(){const p=PROJECTS.find(x=>x.name===WS), h=$('#wsHeader');
  if(WS==='all'||!p){h.style.display='none';h.innerHTML='';return;}
  h.style.display='block';
  const dirs=(p.dirs||[]).map(d=>`<code>${esc(d)}</code>`).join(' ');
  h.innerHTML=`<span class="wsname">${esc(p.name)}</span>`+
    (dirs?`<span class="wslbl">folders:</span><span class="wsdirs">${dirs}</span>`:'<span class="wslbl">no folder mapping</span>');}
async function load(){const q=encodeURIComponent($('#q').value.trim());
  let items=await api(`/api/learnings?workspace=${encodeURIComponent(WS)}&mode=${MODE}&q=${q}`);
  if(CORE_ONLY) items=items.filter(r=>r.is_core);
  const L=$('#list');
  if(!items.length){L.innerHTML='<div class="empty">No learnings.</div>';return;}
  L.innerHTML=items.map(r=>card(r)).join('');
  document.querySelectorAll('[data-star]').forEach(e=>e.onclick=()=>toggleCore(e.dataset.star,e.dataset.on!=='1'));
  document.querySelectorAll('[data-del]').forEach(e=>e.onclick=()=>del(e.dataset.del));
  document.querySelectorAll('[data-enr]').forEach(e=>e.onclick=()=>enrich(e.dataset.enr));
  document.querySelectorAll('[data-ver]').forEach(e=>e.onclick=async()=>{await api(`/api/learnings/${e.dataset.ver}/verify`,{method:'POST'});load();});
  document.querySelectorAll('.content').forEach(e=>e.onclick=ev=>{if(ev.target.classList.contains('lnk'))return;e.classList.toggle('clip')});
  document.querySelectorAll('a.lnk').forEach(e=>e.onclick=ev=>{ev.preventDefault();
    $('#q').value=e.dataset.q;MODE='semantic';$('#mSem').classList.add('on');$('#mList').classList.remove('on');load();});
  document.querySelectorAll('[data-refs]').forEach(e=>e.onclick=()=>{const el=$('#refs-'+e.dataset.refs);el.style.display=el.style.display==='none'?'block':'none';});
}
function daysAgo(iso){if(!iso)return null;return Math.floor((Date.now()-new Date(iso))/86400000);}
function refLine(x){const d=(x.at||'').slice(0,10);const note=x.note?' — '+esc(x.note):'';
  const sess=x.session?' · '+esc(String(x.session).slice(0,8)):'';
  return `<div class="refrow"><b>${esc(x.op||'ref')}</b> ${d}${sess}${note}${x.transcript?`<br><span class="rp">${esc(x.transcript)}</span>`:''}</div>`;}
function card(r){const tags=(r.tags||[]).map(t=>`<span class="b">${t}</span>`).join('');
  const dist=r.distance!=null?`<span class="dist">d=${r.distance.toFixed(3)}</span>`:'';
  const vAge=daysAgo(r.verified_at), stale=vAge!=null&&vAge>180;
  const meta=`<span class="dist">hits ${r.hit_count||0}${vAge!=null?` · verified ${vAge}d ago`:''}</span>`;
  const staleB=stale?'<span class="b stale">stale</span>':'';
  const refs=(r.references||[]);
  const refBadge=refs.length?`<span class="b ref" data-refs="${r.id}" title="show provenance">📎 ${refs.length}</span>`:'';
  return `<div class="card"><h3><span class="star ${r.is_core?'on':''}" data-star="${r.id}" data-on="${r.is_core?1:0}" title="toggle core">★</span>
    ${esc(r.title)} ${dist}</h3>
    <div class="badges"><span class="b ws">${r.workspace}</span>${r.is_core?'<span class="b core">core</span>':''}${staleB}${refBadge}${tags}${meta}</div>
    <div class="refs" id="refs-${r.id}" style="display:none">${refs.map(refLine).join('')}</div>
    <div class="content clip">${md(r.content)}${r.context?'<br><br>— '+md(r.context):''}</div>
    <div class="row"><button data-enr="${r.id}">+ context</button><button data-ver="${r.id}" title="mark re-verified now">✓ verify</button><button data-del="${r.id}" style="color:var(--danger)">Delete</button></div></div>`;}
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
// minimal markdown: escape first, then `code`, **bold**, [[links]]
function md(s){return esc(s)
  .replace(/`([^`]+)`/g,'<code>$1</code>')
  .replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>')
  .replace(/\[\[([^\]]+)\]\]/g,'<a href="#" class="lnk" data-q="$1">$1</a>');}
async function toggleCore(id,val){const r=await api(`/api/learnings/${id}/core`,{method:'POST',body:JSON.stringify({value:val})});
  if(r.error)alert(r.error);loadState();load();}
async function del(id){if(!confirm('Delete this learning?'))return;await fetch(`/api/learnings/${id}`,{method:'DELETE',headers:{'X-Token':TOKEN}});loadState();load();}
async function enrich(id){const c=prompt('Add context to append:');if(!c)return;await api(`/api/learnings/${id}/enrich`,{method:'POST',body:JSON.stringify({context:c})});load();}
$('#q').oninput=()=>{if(MODE==='list')load();};
$('#q').onkeydown=e=>{if(e.key==='Enter')load();};
$('#mList').onclick=()=>{MODE='list';$('#mList').classList.add('on');$('#mSem').classList.remove('on');load();};
$('#mSem').onclick=()=>{MODE='semantic';$('#mSem').classList.add('on');$('#mList').classList.remove('on');load();};
$('#backupBtn').onclick=async()=>{const b=$('#backupBtn');b.textContent='…';
  try{const r=await api('/api/backup',{method:'POST'});
    alert('Backup saved:\n'+r.path+'\n('+(r.bytes/1024).toFixed(0)+' KB)');}
  catch(e){alert('Backup failed');}b.textContent='⇩ Backup';};
$('#coreBtn').onclick=()=>{CORE_ONLY=!CORE_ONLY;$('#coreBtn').classList.toggle('on',CORE_ONLY);
  $('#coreBtn').style.background=CORE_ONLY?'var(--acc)':'';$('#coreBtn').style.color=CORE_ONLY?'#fff':'';load();};
$('#addBtn').onclick=()=>{$('#f_err').textContent='';$('#f_ws').value=(WS==='all'?'':WS);$('#modal').classList.add('on');};
$('#cancel').onclick=()=>$('#modal').classList.remove('on');
$('#save').onclick=async()=>{const b={title:$('#f_title').value,content:$('#f_content').value,
  tags:$('#f_tags').value.split(',').map(s=>s.trim()).filter(Boolean),
  workspace:$('#f_ws').value.trim()||null,is_core:$('#f_core').checked};
  const r=await api('/api/learnings',{method:'POST',body:JSON.stringify(b)});
  if(r.status==='duplicate'){$('#f_err').textContent='Near-duplicate exists: '+r.match.title+' (add force to override)';return;}
  if(r.error){$('#f_err').textContent=r.error;return;}
  $('#modal').classList.remove('on');['f_title','f_content','f_tags'].forEach(i=>$('#'+i).value='');$('#f_core').checked=false;
  loadState();load();};
loadState().then(load);
</script></body></html>"""
