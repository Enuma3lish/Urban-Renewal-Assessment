# -*- coding: utf-8 -*-
# %% [markdown]
# # 都市更新基地評估簡報 — 自動擷取（Google Colab / Selenium）v2
#
# 依實地勘查真實網站流程重寫，全程使用 **可從 Colab 連線** 的兩個站：
# - **都更門檻估算服務**（cloud.land.gov.taipei）：STEP 1–2 門檻估算 + STEP 4–5 圖層（取代連不到的 bim.udd）
# - **建築資訊e點通**（bmenew.gov.taipei，免登入）：STEP 6–7 建照查詢
#
# 僅客觀呈現，不分析、不建議、不下結論。完成後等待人工判讀。
#
# ## 已知客觀限制
# - **建照存根（基地面積/使用分區/樓高/戶數/使照）是掃描影像**（建築影像管理系統），無法以文字擷取，
#   程式會抓到「建照號碼清單」並截圖，這些欄位標為「待人工判讀」。
# - STEP 5「是否位於公劃更新區」需看圖判讀，預設標「待人工判讀」。
# - 圖層透明度為盡力設定；若站方改版，依各 STEP 內 selector 微調。
#
# 用法：整段貼進 Colab 一個 cell（會顯示成表單）→ 填行政區/地段/地號 → 執行。

# %%
# === 安裝環境（含 guard，已裝就跳過）===
import os, sys, subprocess, shutil
def _sh(c): print("＄",c); subprocess.run(c, shell=True, check=False)
def ensure_env():
    need_chrome = not (shutil.which("google-chrome") or os.path.exists("/usr/bin/google-chrome"))
    try: import selenium; need_sel=False
    except Exception: need_sel=True
    if not need_chrome and not need_sel: print("✅ 環境已就緒"); return
    print("⏳ 安裝環境中（首次約 1–2 分鐘）…")
    _sh("pip -q install --upgrade selenium pillow")
    if need_chrome:
        _sh("apt-get -qq update")
        _sh("wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -O /tmp/chrome.deb")
        _sh("apt-get -qq install -y /tmp/chrome.deb")
        _sh("apt-get -qq install -y fonts-noto-cjk")
    print("✅ 環境安裝完成")
ensure_env()

# %%
#@title 🏙️ 基地評估設定（填好欄位後執行） { display-mode: "form" }
#@markdown 直接用下方欄位填寫，不必改程式碼。
行政區 = "大安區"  #@param ["士林區","大同區","大安區","中山區","中正區","內湖區","文山區","北投區","松山區","信義區","南港區","萬華區"]
地段 = "仁愛段四小段"  #@param {type:"string"}
地號 = "477"  #@param {type:"string"}
#@markdown 　多地號用逗號分隔，如 `477,483,489`，會全部納入分析。
STRICT_MODE = True  #@param {type:"boolean"}
MAP_WAIT = 9  #@param {type:"slider", min:3, max:30, step:1}
EST_WAIT = 180  #@param {type:"slider", min:60, max:360, step:10}
地形圖透明度 = 96  #@param {type:"slider", min:0, max:100, step:1}
土地使用分區圖透明度 = 40  #@param {type:"slider", min:0, max:100, step:1}
地籍圖透明度 = 100  #@param {type:"slider", min:0, max:100, step:1}
OUTPUT_DIR = "/content/output"

# %%
# === 引擎：依實測流程擷取 ===
import re, time, traceback, json
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException
os.makedirs(OUTPUT_DIR, exist_ok=True)

URL_URLAND = "https://cloud.land.gov.taipei/urland/urban.html"
URL_BME    = "https://bmenew.gov.taipei/e/indexBud.aspx"

def new_driver():
    o = Options()
    o.add_argument("--headless=new")          # 需要看畫面除錯時可註解此行
    o.add_argument("--no-sandbox"); o.add_argument("--disable-dev-shm-usage")
    o.add_argument("--disable-gpu"); o.add_argument("--ignore-gpu-blocklist"); o.add_argument("--enable-unsafe-swiftshader")
    o.add_argument("--window-size=1600,1100")
    o.add_argument("--lang=zh-TW"); o.add_argument("--hide-scrollbars")
    o.page_load_strategy = "none"             # 圖台持續載入，不等 load 事件
    d = webdriver.Chrome(options=o); d.set_page_load_timeout(90); return d

def safe_get(d, url, ready_js, timeout=45):
    try: d.get(url)
    except TimeoutException: pass
    end=time.time()+timeout
    while time.time()<end:
        try:
            if d.execute_script("return "+ready_js): return True
        except Exception: pass
        time.sleep(0.5)
    return False

def parse_landnos(s): return [x for x in re.split(r"[,\s，、]+", str(s).strip()) if x]
def mu_zi(no):
    """地號 '477' -> ('0477','0000')；'477-2' -> ('0477','0002')"""
    a,_,b = str(no).partition("-")
    return a.zfill(4), (b or "0").zfill(4)

RESULTS={}
def record(step, ok, data=None, shot=None, note=""):
    RESULTS[step]={"ok":ok,"data":data or {},"shot":shot,"note":note}
    print(f"[{step}] {'✅' if ok else '❌'} {note}")

def shot(d, name):
    p=os.path.join(OUTPUT_DIR,name); d.save_screenshot(p)
    try:
        from PIL import Image; im=Image.open(p).convert("RGB"); c=im.getcolors(200000)
        blank = c and max(n for n,_ in c)/sum(n for n,_ in c)>0.995
    except Exception: blank=False
    print(f"  截圖 {name} {'⚠️空白' if blank else '✅'}"); return p, (not blank)

def clean_map(d):
    """截圖前清場：關閉歡迎視窗與所有彈窗（門檻結果/設定面板），讓地圖乾淨。"""
    d.execute_script("""
      [...document.querySelectorAll('.layui-layer-btn0,a,button')].filter(e=>/我知道了/.test(e.textContent||'')).forEach(b=>{try{b.click()}catch(e){}});
      [...document.querySelectorAll('.layui-layer-close')].forEach(b=>{try{b.click()}catch(e){}});
    """)
    time.sleep(1.5)

LANDNOS = parse_landnos(地號)

# ---------- STEP 1,2,4,5：都更門檻估算服務 ----------
# 攔截鉤子：估算後端 RALIDs 回傳「土地/建物私有人數」等原始資料，較畫面解析可靠
HOOK_JS = r"""
window.__ralids=null;
(function(){try{
  function grab(t){try{if(t&&t.indexOf('Lmans')>=0)window.__ralids=t;}catch(e){}}  // 只收含 Lmans 的「人數統計」回應，略過快速的地建號對應回應
  var _f=window.fetch; window.fetch=function(){var p=_f.apply(this,arguments);try{p.then(function(r){return r.clone().text()}).then(grab).catch(function(){});}catch(e){}return p;};
  var _s=XMLHttpRequest.prototype.send; XMLHttpRequest.prototype.send=function(){var x=this;x.addEventListener('load',function(){try{grab(x.responseText)}catch(e){}});return _s.apply(this,arguments);};
}catch(e){}})();
"""

def run_urland():
    d=new_driver()
    try:
        if not safe_get(d, URL_URLAND, "typeof UrbanUpdateSupplementFun_LandNum==='function'"):
            record("STEP2_門檻估算",False,note="urland 載入逾時"); return
        time.sleep(2)
        d.execute_script(HOOK_JS)   # 安裝鉤子以擷取 RALIDs 後端回應
        # 關閉歡迎視窗並開啟分析面板（重試到面板控制項就緒）
        for _ in range(4):
            d.execute_script("try{[...document.querySelectorAll('.layui-layer-btn0,a,button')].filter(e=>/我知道了/.test(e.textContent||'')).forEach(b=>{try{b.click()}catch(e){}});}catch(e){}")
            d.execute_script("try{UrbanUpdateSupplementFun_LandNum();}catch(e){}")
            time.sleep(1.3)
            if d.execute_script("return !!document.getElementById('selsect_UUSF')"): break
        for no in LANDNOS:                                  # 多地號全部加入
            d.execute_script("var t=document.getElementById('seltown_UUSF');var o=[...t.options].find(o=>o.text.trim()===arguments[0]);if(o){t.value=o.value;t.dispatchEvent(new Event('change',{bubbles:true}));}", 行政區)
            tend=time.time()+15                            # 等「地段」連動載入（headless/網路慢可能較久）
            while time.time()<tend and d.execute_script("var s=document.getElementById('selsect_UUSF');return s?s.options.length:0")<=1:
                time.sleep(0.5)
            d.execute_script("var s=document.getElementById('selsect_UUSF');var o=[...s.options].find(o=>o.text.trim()===arguments[0]);if(o){s.value=o.value;s.dispatchEvent(new Event('change',{bubbles:true}));}", 地段)
            time.sleep(0.8)
            d.execute_script("document.getElementById('selnum_UUSF').value=arguments[0];var b=document.getElementById('selnum_UUSF_add');if(b)b.click();", no)
            time.sleep(1.8)
        if not d.execute_script("return /共\\s*[1-9]\\d*\\s*筆/.test(document.body.innerText)"):
            print("  ⚠️ 似乎未成功加入地號（地段連動可能逾時），仍嘗試估算")
        # 後端估算約需 50–70 秒且偶爾整個失敗；在 EST_WAIT 預算內每 ~60 秒重按一次以提高成功率
        d.execute_script("window.__ralids=null;")
        click_est=lambda: d.execute_script("var b=document.getElementById('UrbanQueryLandNum');if(b)b.click();")
        click_est(); last=time.time(); end=time.time()+EST_WAIT
        while time.time()<end:
            body=d.execute_script("return document.body.innerText;") or ""
            if re.search(r"需超過\s*[\d,]+\s*人", body) or d.execute_script("return !!window.__ralids;"): break
            if time.time()-last>60: click_est(); last=time.time()   # 後端逾時/失敗 → 重試
            time.sleep(3)
        time.sleep(1.5)
        api_txt=d.execute_script("return window.__ralids||null;")
        p1,ok1 = shot(d,"01_都更門檻估算.png")
        body = d.execute_script("return document.body.innerText;") or ""
        data = parse_threshold(body)                       # 門檻人數/面積/比例（取自畫面）
        if api_txt:                                        # 私有人數/筆數（取自 API 原始資料，較可靠）
            try:
                a=json.loads(api_txt); a=a[0] if isinstance(a,list) and a else a
                if a.get("Lmans") is not None: data["土地私有人數"]=str(a["Lmans"])
                if a.get("Bmans") is not None: data["建物私有人數"]=str(a["Bmans"])
            except Exception as e: print("  RALIDs 解析失敗：",e)
        got = any(v for v in data.values())
        try: open(os.path.join(OUTPUT_DIR,"_debug_step2.txt"),"w",encoding="utf-8").write("[RALIDS]"+str(api_txt)+"\n\n"+body[:3500])
        except Exception: pass   # 失敗時保留診斷
        record("STEP2_門檻估算", ok1 and got, data=data, shot=p1,
               note="" if got else "門檻數值未解析到（請回傳 zip 內 _debug_step2.txt 與 01 截圖）")
        # STEP4：地形/使用分區/地籍（基地周邊）
        ensure_layer(d,'lyUtoMap',True,地形圖透明度)
        ensure_layer(d,'layer_urban',True,土地使用分區圖透明度)
        ensure_layer(d,'layer_land',True,地籍圖透明度)
        ensure_layer(d,'map_redevelop_segment_42',False,None)  # 先關公劃更新避免干擾分區圖
        time.sleep(max(MAP_WAIT,6))
        clean_map(d)                                          # 截圖前關閉歡迎視窗/門檻結果彈窗
        p2,ok2 = shot(d,"02_都市更新開發審議地圖.png")
        record("STEP4_使用分區圖", ok2, shot=p2, note="地形/使用分區/地籍 已套用")
        # STEP5：115年公劃更新地區
        ensure_layer(d,'layer_urban',False,None)
        ensure_layer(d,'map_redevelop_segment_42',True,100)
        time.sleep(max(MAP_WAIT,6))
        clean_map(d)
        p3,ok3 = shot(d,"03_公劃更新圖.png")
        record("STEP5_公劃更新", ok3, data={"更新地區":"待人工判讀"}, shot=p3, note="115年公劃更新圖層已套用")
    except Exception as e:
        traceback.print_exc(); record("STEP2_門檻估算",False,note=str(e)[:80])
    finally:
        d.quit()

def ensure_layer(d, cb_id, on, opacity):
    """以點擊切換圖層到指定狀態，並盡力設定透明度。"""
    d.execute_script("""
      var id=arguments[0], on=arguments[1], op=arguments[2];
      var cb=document.getElementById(id); if(!cb) return 'nf';
      if(!!cb.checked!==on){ cb.click(); }
      if(op!==null){ var row=cb.closest('li')||cb.parentElement; var r=row&&row.querySelector('input[type=range]');
        if(r){ r.value=op; r.dispatchEvent(new Event('input',{bubbles:true})); r.dispatchEvent(new Event('change',{bubbles:true})); } }
      return 'ok';
    """, cb_id, on, opacity)
    time.sleep(0.6)

def parse_threshold(text):
    # 實測結果文字（已正規化空白）範例：
    # 「土地私有所有權 需超過105人同意 … 建物私有所有權 需超過107人同意 …
    #   (本項同意門檻需超過80%) … 土地私有人數 土地私有面積 建物私有人數 建物私有面積 132人 … 134人 …」
    t=re.sub(r"\s+"," ",text)
    f=lambda p:(re.search(p,t).group(1).strip() if re.search(p,t) else "")
    # 私有人數：表頭與數值分列，需一次抓「土地132 / 建物134」這組
    pair=re.search(r"土地私有人數\s*土地私有面積\s*建物私有人數\s*建物私有面積\s*([\d,]+)\s*人\s*[\d,.]+\s*平方公尺\s*([\d,]+)\s*人", t)
    return {
      "同意比例門檻": f(r"同意門檻需超過\s*(\d+)\s*%"),
      "土地私有人數": pair.group(1) if pair else "",
      "建物私有人數": pair.group(2) if pair else "",
      "土地同意門檻人數": f(r"土地私有所有權\s*需超過\s*([\d,]+)\s*人"),
      "建物同意門檻人數": f(r"建物私有所有權\s*需超過\s*([\d,]+)\s*人"),
      "土地同意門檻面積": f(r"土地私有\s*同意面積需超過\s*([\d,.]+)"),
      "建物同意門檻面積": f(r"建物私有\s*同意面積需超過\s*([\d,.]+)"),
    }

# ---------- STEP 6,7：建築資訊e點通 ----------
def run_bmenew():
    d=new_driver()
    try:
        if not safe_get(d, URL_BME, "document.getElementById('OtherQMemu')!=null"):
            record("STEP7_建照查詢",False,note="bmenew 載入逾時"); return
        time.sleep(2)
        mo, zi = mu_zi(LANDNOS[0])
        d.execute_script("var m=document.getElementById('OtherQMemu');m.value='tqM5';m.dispatchEvent(new Event('change',{bubbles:true}));")
        time.sleep(1.5)
        d.execute_script("""
          var t=arguments[0];
          var sels=[...document.querySelectorAll('select')].filter(s=>s.id!=='OtherQMemu'&&[...s.options].some(o=>o.text===t));
          var ts=sels.find(s=>s.offsetParent)||sels[0];
          if(ts){var o=[...ts.options].find(o=>o.text===t); if(o){ts.value=o.value; ts.dispatchEvent(new Event('change',{bubbles:true}));}}
        """, 行政區)
        time.sleep(1.8)
        d.execute_script("var s=document.getElementById('tqM5_land');var o=[...s.options].find(o=>o.text.trim()===arguments[0]);if(o){s.value=o.value;s.dispatchEvent(new Event('change',{bubbles:true}));}", 地段)
        time.sleep(0.9)
        # 母號/子號 + 搜尋，皆鎖定 tqM5 模組容器
        d.execute_script("""
          var mo=arguments[0], zi=arguments[1];
          var ls=document.getElementById('tqM5_land'); var box=ls;
          for(var k=0;k<7&&box;k++){ if(box.querySelector&&[...box.querySelectorAll('button')].some(b=>/搜尋/.test(b.textContent))) break; box=box.parentElement; }
          if(box){ var ins=[...box.querySelectorAll('input')].filter(i=>/4碼/.test(i.placeholder));
            if(ins[0])ins[0].value=mo; if(ins[1])ins[1].value=zi;
            var b=[...box.querySelectorAll('button')].find(b=>/搜尋/.test(b.textContent)); if(b)b.click(); }
        """, mo, zi)
        time.sleep(max(MAP_WAIT,6))
        p4,ok4 = shot(d,"04_建築資訊定位.png")
        permits = d.execute_script("var l=document.getElementById('qListCoor');return l?[...l.options].map(o=>o.text):[];") or []
        permits = [x for x in permits if x and "共" not in x and "0477" not in x and "04770000" not in x]
        permits = list(dict.fromkeys(permits))   # 去重（清單常重覆列出同一建照）
        ok = ok4 and len(permits)>0
        record("STEP7_建照查詢", ok,
               data={"建照清單":permits[:30],
                     "基地面積":"待人工判讀（建照存根掃描）","使用分區":"待人工判讀（見分區圖02）",
                     "使照":"待人工判讀（建照存根）","樓高":"待人工判讀（建照存根）","戶數":"待人工判讀（建照存根）"},
               shot=p4, note=f"找到 {len(permits)} 筆建照" if ok else "未找到建照（請看截圖）")
    except Exception as e:
        traceback.print_exc(); record("STEP7_建照查詢",False,note=str(e)[:80])
    finally:
        d.quit()

# ---------- 驗證閘門 + 產生 Markdown ----------
REQUIRED = ["STEP2_門檻估算","STEP4_使用分區圖","STEP5_公劃更新"]   # STEP7 受掃描影像限制，預設不列必要

def build_markdown():
    no="-".join(LANDNOS); s2=RESULTS.get("STEP2_門檻估算",{}).get("data",{})
    s5=RESULTS.get("STEP5_公劃更新",{}).get("data",{}); s7=RESULTS.get("STEP7_建照查詢",{}).get("data",{})
    g=lambda d,k:(d.get(k) or "待補")
    permits=s7.get("建照清單",[]) or []
    md=[f"# {行政區}{地段}{no} 基地評估簡報\n",
        "> 本簡報僅客觀呈現系統資訊，未做分析、未提供開發建議、未下結論。請人工判讀。\n",
        "## 都更門檻估算\n","![都更門檻估算](01_都更門檻估算.png)\n",
        "| 項目 | 數值 |\n| --- | --- |",
        f"| 同意比例門檻 | {g(s2,'同意比例門檻')}% |",
        f"| 土地私有人數 | {g(s2,'土地私有人數')} 人 |",
        f"| 土地同意門檻 | 需超過 {g(s2,'土地同意門檻人數')} 人；面積 {g(s2,'土地同意門檻面積')} ㎡ |",
        f"| 建物私有人數 | {g(s2,'建物私有人數')} 人 |",
        f"| 建物同意門檻 | 需超過 {g(s2,'建物同意門檻人數')} 人；面積 {g(s2,'建物同意門檻面積')} ㎡ |","",
        "## 都市使用分區圖（地形／使用分區／地籍）\n","![使用分區圖](02_都市更新開發審議地圖.png)\n",
        "## 公劃更新圖　／　基地基本資料\n",'<table><tr>',
        '<td width="55%"><img src="03_公劃更新圖.png" width="100%"></td>','<td width="45%">\n',
        "| 項目 | 內容 |\n| --- | --- |",
        f"| 基地面積 | {g(s7,'基地面積')} |",
        f"| 更新地區 | {g(s5,'更新地區')} |",
        f"| 使用分區 | {g(s7,'使用分區')} |",
        f"| 建照 | {('、'.join(permits[:6]) + (' …' if len(permits)>6 else '')) if permits else '待補'} |",
        f"| 使照 | {g(s7,'使照')} |",
        f"| 樓高／戶數 | {g(s7,'樓高')} ／ {g(s7,'戶數')} |",'\n</td></tr></table>\n',
        "## 建築資訊定位（建照標示）\n","![建築資訊定位](04_建築資訊定位.png)\n"]
    if permits:
        md.append(f"本基地查得建照 **{len(permits)} 筆**：{('、'.join(permits[:30]))}")
        md.append("\n> 基地面積／使用分區／樓高／戶數／使照屬「建照存根」掃描影像內容，需人工點選查詢建照存根後判讀。")
    md += ["\n---\n", f"*產生時間：{time.strftime('%Y-%m-%d %H:%M')}　|　資料來源：臺北市政府相關系統，僅供瀏覽參考。*"]
    out=os.path.join(OUTPUT_DIR, f"{行政區}_{地段}_{no}_基地評估簡報.md")
    open(out,"w",encoding="utf-8").write("\n".join(md)); return out

def run_all():
    print("分析目標：", 行政區, 地段, LANDNOS)
    run_urland(); run_bmenew()
    print("="*46,"\n驗證結果")
    for k,v in RESULTS.items(): print(f"  {'✅' if v['ok'] else '❌'} {k}  {v.get('note','')}")
    print("="*46)
    ok_all = all(RESULTS.get(k,{}).get("ok") for k in REQUIRED)
    out=None
    if ok_all or not STRICT_MODE:
        out=build_markdown(); print("\n✅ 已產生：",out)
    else:
        print("\n⛔ 嚴格模式：必要步驟未全部通過，未產生 Markdown（仍會打包截圖與 _debug 供檢視）。")
    # 一律打包下載（含截圖與除錯檔），失敗時也能回傳診斷
    import shutil as _s; zb=os.path.join("/content", f"{行政區}_{地段}_{'-'.join(LANDNOS)}_基地評估簡報")
    try:
        _s.make_archive(zb,"zip",OUTPUT_DIR); print("已打包：",zb+".zip")
        from google.colab import files; files.download(zb+".zip")
    except Exception as e: print("（非 Colab 或下載被攔截）",e)
    return out

run_all()
