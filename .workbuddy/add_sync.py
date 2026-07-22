import sys
sys.path.insert(0, r'C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\firefightAI')
code = open(r'C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\firefightAI\dashboard_server.py', 'r', encoding='utf-8').read()

# Add JS function
old_js = 'function toggleObserveMode(){'
new_js = '''function syncAll(){
  var btn=event.target; btn.textContent='Syncing...'; btn.style.background='#ff9800';
  fetch('/api/sync/all',{method:'POST'}).then(function(r){return r.json()}).then(function(d){
    btn.textContent='Sync+Upload'; btn.style.background='#009688';
    if(d.status==='ok'){
      alert('Sync: GitHub='+d.results.github+' Server='+d.results.server+' Installer='+d.results.installer);
    }else{alert('Sync failed: '+(d.error||''))}
  }).catch(function(e){btn.textContent='Sync+Upload'; btn.style.background='#009688'; alert('Error: '+e)});
}
function toggleObserveMode(){'''
code = code.replace(old_js, new_js)

# Add sync endpoint
marker = '# '+ chr(0x1f525) + ' 自动推送学习参数到服务器'
sync_ep = '''
@app.route("/api/sync/all", methods=["POST"])
def api_sync_all():
    import subprocess as _sp, zipfile
    results = {"github": False, "server": False, "installer": False}
    try:
        _save_all_state()
        repo = str(PROJECT_ROOT)
        _sp.run(["git", "-C", repo, "add", "data/"], capture_output=True, timeout=10)
        r = _sp.run(["git", "-C", repo, "commit", "-m", "sync " + datetime.now().strftime("%Y%m%d-%H%M")], capture_output=True, text=True, timeout=10)
        if r.returncode <= 1:
            r = _sp.run(["git", "-C", repo, "push", "origin", "master"], capture_output=True, text=True, timeout=30)
            results["github"] = r.returncode == 0
        try:
            import requests as _req
            _req.get("http://139.199.69.88:5001/api/git/sync", timeout=15)
            results["server"] = True
        except: pass
        installer = PROJECT_ROOT / "static" / "FirefightAI_Installer.zip"
        installer.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(installer, 'w', zipfile.ZIP_DEFLATED) as z:
            for f in ["dashboard_server.py","启动控制面板.bat"]:
                fp = PROJECT_ROOT / f
                if fp.exists(): z.write(fp, fp.name)
            for f in ["data/ai_knowledge.json","data/tactics_rules.yaml"]:
                fp = PROJECT_ROOT / f
                if fp.exists(): z.write(fp, f)
            for fp in (PROJECT_ROOT / "data" / "params").glob("*.json"):
                z.write(fp, "data/params/" + fp.name)
        results["installer"] = True
        return jsonify({"status": "ok", "results": results})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)[:200]}), 500

'''
pos = code.find(marker)
if pos > 0:
    code = code[:pos] + sync_ep + code[pos:]
else:
    print("Marker NOT FOUND!")
    sys.exit(1)

open(r'C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\firefightAI\dashboard_server.py', 'w', encoding='utf-8').write(code)
compile(code, 'test', 'exec')
print('OK')
