# -*- coding: utf-8 -*-
"""
▦ SHIKAKU STUDIO — Profesyonel, web tabanlı Shikaku bulmaca oyunu
==================================================================
Tek dosyalık Streamlit uygulaması.

Mimari
------
1. OYUN TAHTASI  : st.components.v1.declare_component(path=...) ile çift yönlü
                   (bi-directional) haberleşen, saf Vanilla JS/HTML/CSS bileşeni.
                   Sürükle-bırak ile dikdörtgen çizimi, Undo / Clear All,
                   canlı doğrulama, kronometre ve Co-op modu tamamen tarayıcı
                   tarafında çalışır; bulmaca çözüldüğünde süre Streamlit'e
                   geri gönderilir (setComponentValue).
2. JENERATÖR     : Sabit seed'li "giyotin bölme" (guillotine partition)
                   algoritması. Izgara, kurallara uygun dikdörtgenlere bölünür
                   ve her dikdörtgenin içine alanına eşit bir ipucu yazılır.
                   Yapısı gereği her bölümün GARANTİLİ bir çözümü vardır ve
                   aynı seed her oyuncuda aynı haritayı üretir.
3. LİDERLİK      : Yerel JSON dosyası (shikaku_leaderboard.json). Oyuncu
                   sınırı yoktur; en iyi süreler ve en çok bölüm çözenler
                   zorluk/bölüm bazında filtrelenebilir.

Çalıştırma:  streamlit run app.py
"""

import hashlib
import json
import os
import random
import time
from datetime import datetime

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# ════════════════════════════════════════════════════════════════════════════
# 1. SABİTLER VE ZORLUK AYARLARI
# ════════════════════════════════════════════════════════════════════════════

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LEADERBOARD_PATH = os.path.join(APP_DIR, "shikaku_leaderboard.json")
COMPONENT_DIR = os.path.join(APP_DIR, "shikaku_board_component")
COMPONENT_VERSION = "1.4.0"  # HTML değişirse artır → dosya yeniden yazılır

LEVELS_PER_DIFFICULTY = 20

DIFFICULTIES = {
    "easy":   {"label": "Kolay",  "size": 5,  "max_area": 6,  "cell": 64, "seed_base": 11_000},
    "medium": {"label": "Orta",   "size": 8,  "max_area": 9,  "cell": 52, "seed_base": 22_000},
    "hard":   {"label": "Zor",    "size": 12, "max_area": 12, "cell": 42, "seed_base": 33_000},
    "expert": {"label": "Uzman",  "size": 15, "max_area": 16, "cell": 35, "seed_base": 44_000},
    "master": {"label": "Usta",   "size": 20, "max_area": 24, "cell": 28, "seed_base": 55_000},
}
DIFF_ORDER = list(DIFFICULTIES.keys())


# ════════════════════════════════════════════════════════════════════════════
# 2. PROSEDÜREL BÖLÜM JENERATÖRÜ  (deterministik, garantili çözümlü)
# ════════════════════════════════════════════════════════════════════════════

def _split_weights(n: int, is_strip: bool) -> list[float]:
    """1..n-1 arasındaki kesme noktalarına ağırlık verir; merkeze yakın
    kesimleri teşvik eder, 1 birimlik ince parçalardan kaçınır.
    is_strip=True (1 hücre genişliğinde şerit) ise kenar kesimleri 1×1
    üreteceğinden, mümkün olduğunca tamamen yasaklanır."""
    w = []
    for cut in range(1, n):
        base = min(cut, n - cut) + 0.25      # merkez tercih edilir
        if cut == 1 or cut == n - 1:
            base = 0.0 if (is_strip and n >= 4) else base * 0.4
        w.append(base)
    if sum(w) <= 0:                          # güvenlik: tüm kesimler yasaklandıysa
        w = [1.0] * (n - 1)
    return w


def generate_puzzle(diff_key: str, level: int) -> dict:
    """
    Izgarayı rastgele (ama seed'e bağlı deterministik) dikdörtgenlere böler.
    Dönen sözlük: {width, height, clues:[[r, c, value], ...]}
    Üretim yöntemi gereği bulmacanın en az bir geçerli çözümü her zaman vardır.
    """
    cfg = DIFFICULTIES[diff_key]
    rng = random.Random(cfg["seed_base"] + level)   # sabit seed → herkese aynı harita
    W = H = cfg["size"]
    max_area = cfg["max_area"]

    stack: list[tuple[int, int, int, int]] = [(0, 0, W, H)]  # (x, y, w, h)
    final_rects: list[tuple[int, int, int, int]] = []

    while stack:
        x, y, w, h = stack.pop()
        area = w * h

        must_split = area > max_area
        if not must_split:
            # Küçük alanlarda durma olasılığı yüksek → boyut çeşitliliği.
            # area<=3 her zaman durur: 1×1'lik (önemsiz) kutular engellenir.
            p_stop = 0.18 + 0.72 * (1.0 - area / max_area)
            if area <= 3 or rng.random() < p_stop:
                final_rects.append((x, y, w, h))
                continue

        # Bölme yönü: kenar uzunluğuyla orantılı olasılık
        can_v, can_h = w >= 2, h >= 2
        if can_v and can_h:
            vertical = rng.random() < (w / (w + h))
        elif can_v:
            vertical = True
        elif can_h:
            vertical = False
        else:  # 1×1 — bölünemez
            final_rects.append((x, y, w, h))
            continue

        if vertical:
            cut = rng.choices(range(1, w), weights=_split_weights(w, h == 1))[0]
            stack.append((x, y, cut, h))
            stack.append((x + cut, y, w - cut, h))
        else:
            cut = rng.choices(range(1, h), weights=_split_weights(h, w == 1))[0]
            stack.append((x, y, w, cut))
            stack.append((x, y + cut, w, h - cut))

    # Her dikdörtgenin içine, alanına eşit bir ipucu yerleştir
    clues = []
    for (x, y, w, h) in final_rects:
        cr = y + rng.randrange(h)
        cc = x + rng.randrange(w)
        clues.append([cr, cc, w * h])
    clues.sort()

    return {"width": W, "height": H, "clues": clues}


# ════════════════════════════════════════════════════════════════════════════
# 3. LİDERLİK TABLOSU — yerel JSON kalıcılığı
# ════════════════════════════════════════════════════════════════════════════

def load_leaderboard() -> list[dict]:
    try:
        with open(LEADERBOARD_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_leaderboard(entries: list[dict]) -> None:
    tmp = LEADERBOARD_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=1)
    os.replace(tmp, LEADERBOARD_PATH)


def record_solve(username: str, diff_key: str, level: int, seconds: float, coop: bool) -> None:
    entries = load_leaderboard()
    entries.append({
        "username": username,
        "difficulty": diff_key,
        "level": level,
        "time": round(float(seconds), 2),
        "coop": bool(coop),
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    save_leaderboard(entries)


def fmt_time(seconds: float) -> str:
    s = int(round(seconds))
    if s >= 3600:
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    return f"{s // 60:02d}:{s % 60:02d}"


def user_best_times(entries: list[dict], username: str) -> dict[tuple, float]:
    """{(diff, level): en_iyi_süre} — sadece verilen kullanıcı için."""
    best: dict[tuple, float] = {}
    for e in entries:
        if e.get("username") != username:
            continue
        k = (e.get("difficulty"), e.get("level"))
        t = e.get("time", 1e18)
        if k not in best or t < best[k]:
            best[k] = t
    return best


# ════════════════════════════════════════════════════════════════════════════
# 4. OYUN TAHTASI BİLEŞENİ  (Vanilla JS — çift yönlü Streamlit protokolü)
# ════════════════════════════════════════════════════════════════════════════
# declare_component(path=...) statik bir index.html servis eder. İçindeki JS,
# Streamlit bileşen protokolünü (componentReady / render / setComponentValue /
# setFrameHeight) elle uygular → ekstra paket gerekmeden tam çift yönlü iletişim.

COMPONENT_HTML = r"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=IBM+Plex+Mono:wght@500;600;700&display=swap" rel="stylesheet">
<style>
  :root{
    --card:#101729; --card-edge:#1e2a47;
    --cellbg:#16203a; --line:rgba(124,146,196,.16); --line-strong:rgba(124,146,196,.42);
    --ink:#e8edf9; --ink-dim:#8b97b8; --accent:#5ec8d8; --gold:#e8b04b;
    --ok:#67d29a; --bad:#e4707e;
  }
  *{box-sizing:border-box; -webkit-tap-highlight-color:transparent;}
  html,body{margin:0; padding:0; background:transparent;}
  body{font-family:'Space Grotesk',system-ui,sans-serif; color:var(--ink); user-select:none;}

  #root{
    background:linear-gradient(180deg,#111a30 0%,#0e1526 100%);
    border:1px solid var(--card-edge); border-radius:18px;
    padding:14px 16px 16px; margin:2px;
    box-shadow:0 12px 36px rgba(0,0,0,.35);
  }

  /* ── üst bar ─────────────────────────────────────── */
  #topbar{display:flex; align-items:center; gap:12px; margin-bottom:12px; flex-wrap:wrap;}
  #tag{
    font-family:'IBM Plex Mono',monospace; font-weight:600; font-size:12px;
    letter-spacing:.14em; color:var(--accent);
    border:1px solid rgba(94,200,216,.35); background:rgba(94,200,216,.08);
    padding:5px 10px; border-radius:999px;
  }
  #status{font-size:12.5px; color:var(--ink-dim); flex:1; min-width:120px;}
  #timer{
    font-family:'IBM Plex Mono',monospace; font-weight:700; font-size:21px;
    color:var(--ink); letter-spacing:.05em; font-variant-numeric:tabular-nums;
  }
  #timer.done{color:var(--ok);}

  /* ── co-op oyuncu seçici ─────────────────────────── */
  #coop{display:none; gap:6px;}
  #coop.on{display:flex;}
  .pbtn{
    display:flex; align-items:center; gap:7px; cursor:pointer;
    font:inherit; font-size:12.5px; font-weight:600; color:var(--ink-dim);
    background:rgba(255,255,255,.03); border:1px solid var(--card-edge);
    border-radius:999px; padding:5px 12px; transition:all .15s;
  }
  .pbtn .dot{width:11px; height:11px; border-radius:50%;}
  .pbtn[data-p="0"] .dot{background:#5ea8f5;}
  .pbtn[data-p="1"] .dot{background:#e8b04b;}
  .pbtn.active{color:var(--ink); border-color:rgba(255,255,255,.35); background:rgba(255,255,255,.08);}

  /* ── tahta ───────────────────────────────────────── */
  #boardwrap{position:relative; display:flex; justify-content:center;}
  #board{
    position:relative; border-radius:12px; background-color:var(--cellbg);
    background-image:
      linear-gradient(to right,var(--line) 1px,transparent 1px),
      linear-gradient(to bottom,var(--line) 1px,transparent 1px);
    box-shadow:inset 0 0 0 1.5px var(--line-strong), 0 4px 18px rgba(0,0,0,.3);
    touch-action:none; overflow:hidden;
  }
  #rects,#cluesL,#fx{position:absolute; inset:0; pointer-events:none;}

  .rect{
    position:absolute; border-radius:8px; border:2px dashed; 
    transition:background-color .12s, border-color .12s;
  }
  .rect.ok{border-style:solid;}
  .rect.pop{animation:pop .45s cubic-bezier(.34,1.56,.64,1) both;}
  @keyframes pop{0%{transform:scale(.85);opacity:.4} 100%{transform:scale(1);opacity:1}}

  #preview{
    position:absolute; border-radius:8px; display:none;
    border:2px solid rgba(232,237,249,.85); background:rgba(232,237,249,.14);
    box-shadow:0 0 0 4px rgba(232,237,249,.07);
  }

  .chip{
    position:absolute; display:flex; align-items:center; justify-content:center;
    pointer-events:none;
  }
  .chip span{
    font-family:'IBM Plex Mono',monospace; font-weight:700; color:var(--ink);
    background:rgba(13,19,36,.78); border:1.5px solid rgba(139,151,184,.55);
    border-radius:8px; min-width:1.65em; padding:.12em .3em; text-align:center;
    line-height:1.4; transition:all .18s; backdrop-filter:blur(2px);
  }
  .chip.ok span{
    color:#0b1322; background:var(--ok); border-color:var(--ok);
    box-shadow:0 0 12px rgba(103,210,154,.5);
  }

  /* ── alt kontroller ──────────────────────────────── */
  #controls{display:flex; gap:10px; margin-top:14px; align-items:center;}
  .ctl{
    font:inherit; font-size:13.5px; font-weight:600; cursor:pointer;
    color:var(--ink); background:rgba(255,255,255,.05);
    border:1px solid var(--card-edge); border-radius:10px; padding:8px 16px;
    transition:all .15s;
  }
  .ctl:hover{background:rgba(255,255,255,.1); border-color:rgba(255,255,255,.3);}
  .ctl:active{transform:translateY(1px);}
  .ctl:disabled{opacity:.35; cursor:default;}
  #hint{margin-left:auto; font-size:11.5px; color:var(--ink-dim); text-align:right;}

  /* ── çözüldü paneli ──────────────────────────────── */
  #banner{
    position:absolute; inset:0; display:none; align-items:center; justify-content:center;
    background:rgba(10,15,28,.55); backdrop-filter:blur(3px); border-radius:12px;
    z-index:5;
  }
  #banner.show{display:flex; animation:fadein .4s ease both;}
  @keyframes fadein{from{opacity:0} to{opacity:1}}
  #banner .panel{
    text-align:center; background:#121c33; border:1px solid rgba(103,210,154,.5);
    border-radius:16px; padding:22px 38px; box-shadow:0 16px 48px rgba(0,0,0,.5);
    animation:pop .5s cubic-bezier(.34,1.56,.64,1) both;
  }
  #banner h2{margin:0 0 4px; font-size:21px; letter-spacing:.04em; color:var(--ok);}
  #banner .t{font-family:'IBM Plex Mono',monospace; font-weight:700; font-size:30px;}
  #banner p{margin:6px 0 0; font-size:12.5px; color:var(--ink-dim);}
</style>
</head>
<body>
<div id="root">
  <div id="topbar">
    <div id="tag">#PUZZLE</div>
    <div id="coop">
      <button class="pbtn active" data-p="0"><span class="dot"></span>1. Oyuncu</button>
      <button class="pbtn" data-p="1"><span class="dot"></span>2. Oyuncu</button>
    </div>
    <div id="status"></div>
    <div id="timer">00:00</div>
  </div>

  <div id="boardwrap">
    <div id="board">
      <div id="rects"></div>
      <div id="cluesL"></div>
      <div id="preview"></div>
      <div id="banner"><div class="panel">
        <h2>ÇÖZÜLDÜ ✓</h2><div class="t" id="finalTime">00:00</div>
        <p>Süren liderlik tablosuna kaydedildi.</p>
      </div></div>
    </div>
  </div>

  <div id="controls">
    <button class="ctl" id="undo">↩&nbsp; Geri Al</button>
    <button class="ctl" id="clear">⟲&nbsp; Tümünü Temizle</button>
    <div id="hint">Sürükle: alan çiz &nbsp;·&nbsp; Tıkla: dikdörtgeni sil / 1×1 koy</div>
  </div>
</div>

<script>
/* ───────── Streamlit bileşen protokolü (elle uygulanmış) ───────── */
function smsg(type, extra){ window.parent.postMessage(Object.assign({isStreamlitMessage:true, type}, extra||{}), "*"); }
function setFrameHeight(h){ smsg("streamlit:setFrameHeight", {height:h}); }
function setComponentValue(v){ smsg("streamlit:setComponentValue", {value:v, dataType:"json"}); }

/* ───────── renk paletleri (yarı saydam, mücevher tonları) ───────── */
const SOLO = ["94,200,216","232,176,75","155,140,255","103,210,154",
              "228,112,126","95,168,245","240,142,91","212,120,200"];
const COOL = ["94,200,216","95,168,245","155,140,255","103,210,154"];   // 1. oyuncu
const WARM = ["232,176,75","228,112,126","240,142,91","212,120,200"];   // 2. oyuncu

const $ = id => document.getElementById(id);
let S = null;            // oyun durumu
let timerInt = null;

/* ───────── render: Streamlit'ten gelen argümanlar ───────── */
window.addEventListener("message", (e)=>{
  const d = e.data;
  if(!d || d.type !== "streamlit:render") return;
  const a = d.args || {};
  if(!S || S.puzzleId !== a.puzzle_id){
    initPuzzle(a);
  }else{
    S.coop = !!a.coop;
    $("coop").classList.toggle("on", S.coop);
  }
});
smsg("streamlit:componentReady", {apiVersion:1});
setFrameHeight(180);

/* ───────── bulmaca kurulumu ───────── */
function initPuzzle(a){
  if(timerInt) clearInterval(timerInt);
  let cell = a.cell || 48;
  const avail = Math.max(220, window.innerWidth - 40);
  if(cell * a.width > avail) cell = Math.max(17, Math.floor(avail / a.width));

  S = {
    W:a.width, H:a.height, cell, clues:a.clues||[],
    puzzleId:a.puzzle_id, tag:a.tag||"#PUZZLE", coop:!!a.coop,
    rects:[], undo:[], player:0, colorIdx:[0,0],
    solved:false, start:Date.now(),
  };
  buildBoard();
  $("coop").classList.toggle("on", S.coop);
  $("banner").classList.remove("show");
  $("timer").classList.remove("done");
  timerInt = setInterval(tick, 250);
  tick(); evaluate(); layout();
}

function tick(){
  if(!S || S.solved) return;
  $("timer").textContent = fmt((Date.now()-S.start)/1000);
}
function fmt(sec){
  const s = Math.floor(sec);
  const m = Math.floor(s/60), r = s%60;
  if(m >= 60) return Math.floor(m/60)+":"+String(m%60).padStart(2,"0")+":"+String(r).padStart(2,"0");
  return String(m).padStart(2,"0")+":"+String(r).padStart(2,"0");
}
function layout(){
  requestAnimationFrame(()=> setFrameHeight(document.documentElement.scrollHeight + 6));
}

/* ───────── tahta DOM'u ───────── */
function buildBoard(){
  const b = $("board");
  const bw = S.W*S.cell, bh = S.H*S.cell;
  b.style.width = bw+"px"; b.style.height = bh+"px";
  b.style.backgroundSize = S.cell+"px "+S.cell+"px";
  $("tag").textContent = S.tag;
  $("rects").innerHTML = ""; 

  const L = $("cluesL"); L.innerHTML = "";
  const fontPx = Math.max(11, Math.min(19, Math.round(S.cell*0.42)));
  for(const [r,c,v] of S.clues){
    const d = document.createElement("div");
    d.className = "chip";
    d.style.cssText = `left:${c*S.cell}px; top:${r*S.cell}px; width:${S.cell}px; height:${S.cell}px;`;
    d.dataset.key = r+","+c;
    const sp = document.createElement("span");
    sp.style.fontSize = fontPx+"px";
    sp.textContent = v;
    d.appendChild(sp); L.appendChild(d);
  }

  b.onpointerdown = onDown;
  b.onpointermove = onMove;
  b.onpointerup   = onUp;
  b.onpointercancel = ()=>{ drag=null; $("preview").style.display="none"; };
}

/* ───────── işaretçi etkileşimi ───────── */
let drag = null;

function cellAt(ev){
  const r = $("board").getBoundingClientRect();
  const c = Math.min(S.W-1, Math.max(0, Math.floor((ev.clientX-r.left)/S.cell)));
  const w = Math.min(S.H-1, Math.max(0, Math.floor((ev.clientY-r.top )/S.cell)));
  return {r:w, c:c};
}
function onDown(ev){
  if(!S || S.solved) return;
  ev.preventDefault();
  try{ ev.target.setPointerCapture(ev.pointerId); }catch(_){}
  const cl = cellAt(ev);
  drag = {a:cl, b:cl, moved:false};
  showPreview();
}
function onMove(ev){
  if(!drag) return;
  const cl = cellAt(ev);
  if(cl.r !== drag.b.r || cl.c !== drag.b.c){
    drag.b = cl; drag.moved = true; showPreview();
  }
}
function onUp(ev){
  if(!drag) return;
  $("preview").style.display = "none";
  const d = drag; drag = null;
  if(S.solved) return;
  if(!d.moved){
    const hit = rectIndexAt(d.a.r, d.a.c);
    if(hit >= 0){ pushUndo(); S.rects.splice(hit,1); commit(); return; }
  }
  addRect(d.a, d.b);
}
function showPreview(){
  const p = $("preview");
  const r0 = Math.min(drag.a.r, drag.b.r), r1 = Math.max(drag.a.r, drag.b.r);
  const c0 = Math.min(drag.a.c, drag.b.c), c1 = Math.max(drag.a.c, drag.b.c);
  p.style.display = "block";
  p.style.left   = (c0*S.cell+2)+"px";
  p.style.top    = (r0*S.cell+2)+"px";
  p.style.width  = ((c1-c0+1)*S.cell-4)+"px";
  p.style.height = ((r1-r0+1)*S.cell-4)+"px";
}
function rectIndexAt(r,c){
  for(let i=S.rects.length-1; i>=0; i--){
    const t = S.rects[i];
    if(r>=t.r0 && r<=t.r1 && c>=t.c0 && c<=t.c1) return i;
  }
  return -1;
}
function addRect(a,b){
  pushUndo();
  const nr = {
    r0:Math.min(a.r,b.r), r1:Math.max(a.r,b.r),
    c0:Math.min(a.c,b.c), c1:Math.max(a.c,b.c),
  };
  // çakışan dikdörtgenleri kaldır
  S.rects = S.rects.filter(t => t.r1<nr.r0 || t.r0>nr.r1 || t.c1<nr.c0 || t.c0>nr.c1);
  const owner = S.coop ? S.player : 0;
  const pal = S.coop ? (owner===1 ? WARM : COOL) : SOLO;
  nr.owner = owner;
  nr.rgb = pal[(S.colorIdx[owner]++) % pal.length];
  nr.fresh = true;
  S.rects.push(nr);
  commit();
}
function pushUndo(){
  S.undo.push(JSON.stringify(S.rects));
  if(S.undo.length > 200) S.undo.shift();
}

/* ───────── çizim + doğrulama ───────── */
function commit(){ redraw(); evaluate(); }

function redraw(){
  const R = $("rects"); R.innerHTML = "";
  for(const t of S.rects){
    const d = document.createElement("div");
    d.className = "rect" + (t.fresh ? " pop" : "");
    t.fresh = false;
    d.style.left   = (t.c0*S.cell+2)+"px";
    d.style.top    = (t.r0*S.cell+2)+"px";
    d.style.width  = ((t.c1-t.c0+1)*S.cell-4)+"px";
    d.style.height = ((t.r1-t.r0+1)*S.cell-4)+"px";
    d.style.backgroundColor = `rgba(${t.rgb},0.27)`;
    d.style.borderColor     = `rgba(${t.rgb},0.92)`;
    t._el = d;
    R.appendChild(d);
  }
}

function evaluate(){
  const cover = new Int16Array(S.W*S.H);
  const clueMap = new Map();
  for(const [r,c,v] of S.clues) clueMap.set(r+","+c, v);

  let allRectsOk = S.rects.length > 0;
  const okChips = new Set();

  for(const t of S.rects){
    let inside = [];
    for(let r=t.r0; r<=t.r1; r++)
      for(let c=t.c0; c<=t.c1; c++){
        cover[r*S.W+c]++;
        if(clueMap.has(r+","+c)) inside.push([r+","+c, clueMap.get(r+","+c)]);
      }
    const area = (t.r1-t.r0+1)*(t.c1-t.c0+1);
    const ok = inside.length === 1 && inside[0][1] === area;
    if(t._el) t._el.classList.toggle("ok", ok);
    if(ok) okChips.add(inside[0][0]);
    else allRectsOk = false;
  }
  document.querySelectorAll(".chip").forEach(ch =>
    ch.classList.toggle("ok", okChips.has(ch.dataset.key)));

  let empty = 0;
  for(let i=0; i<cover.length; i++) if(cover[i]===0) empty++;

  const doneClues = okChips.size;
  $("status").textContent = S.solved ? "" :
    `${doneClues}/${S.clues.length} ipucu tamam · ${empty} boş hücre`;

  if(allRectsOk && empty === 0 && doneClues === S.clues.length && !S.solved){
    finish();
  }
}

function finish(){
  S.solved = true;
  const elapsed = (Date.now()-S.start)/1000;
  clearInterval(timerInt);
  $("timer").textContent = fmt(elapsed);
  $("timer").classList.add("done");
  $("finalTime").textContent = fmt(elapsed);
  $("status").textContent = "";
  // küçük kutlama dalgası
  S.rects.forEach((t,i)=>{ if(t._el){ t._el.style.animation="none"; void t._el.offsetWidth;
    t._el.style.animation = `pop .5s ${i*0.035}s cubic-bezier(.34,1.56,.64,1) both`; }});
  setTimeout(()=> $("banner").classList.add("show"), S.rects.length*35 + 250);
  layout();
  setComponentValue({
    solved:true, elapsed:elapsed, puzzle_id:S.puzzleId,
    nonce: Math.random().toString(36).slice(2),
  });
}

/* ───────── butonlar + klavye ───────── */
$("undo").onclick = ()=>{
  if(!S || S.solved || !S.undo.length) return;
  S.rects = JSON.parse(S.undo.pop());
  commit();
};
$("clear").onclick = ()=>{
  if(!S || S.solved || !S.rects.length) return;
  pushUndo(); S.rects = []; commit();
};
document.querySelectorAll(".pbtn").forEach(b=>{
  b.onclick = ()=>{
    if(!S) return;
    S.player = +b.dataset.p;
    document.querySelectorAll(".pbtn").forEach(x=>x.classList.toggle("active", x===b));
  };
});
window.addEventListener("keydown", e=>{
  if((e.ctrlKey||e.metaKey) && e.key.toLowerCase()==="z"){ e.preventDefault(); $("undo").click(); }
});
window.addEventListener("resize", layout);
</script>
</body>
</html>
"""


@st.cache_resource(show_spinner=False)
def get_board_component():
    """index.html'i diske yazar ve bileşeni bir kez deklare eder."""
    os.makedirs(COMPONENT_DIR, exist_ok=True)
    index_path = os.path.join(COMPONENT_DIR, "index.html")
    ver_path = os.path.join(COMPONENT_DIR, ".version")

    digest = hashlib.sha1((COMPONENT_VERSION + COMPONENT_HTML).encode()).hexdigest()
    current = None
    if os.path.exists(ver_path):
        with open(ver_path, "r", encoding="utf-8") as f:
            current = f.read().strip()
    if current != digest or not os.path.exists(index_path):
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(COMPONENT_HTML)
        with open(ver_path, "w", encoding="utf-8") as f:
            f.write(digest)

    return components.declare_component("shikaku_board", path=COMPONENT_DIR)


# ════════════════════════════════════════════════════════════════════════════
# 5. STREAMLIT ARAYÜZÜ
# ════════════════════════════════════════════════════════════════════════════

st.set_page_config(page_title="Shikaku Studio", page_icon="▦",
                   layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=IBM+Plex+Mono:wght@500;700&display=swap');
html, body, [class*="css"]{ font-family:'Space Grotesk',system-ui,sans-serif; }
.stApp{ background:radial-gradient(1200px 600px at 70% -10%, #14203c 0%, #0c1322 55%) #0c1322; }
[data-testid="stSidebar"]{ background:#0e1628; border-right:1px solid #1c2742; }
h1,h2,h3{ letter-spacing:.01em; }
.skk-title{ font-size:2.1rem; font-weight:700; margin:0;
  background:linear-gradient(92deg,#e8edf9 30%,#5ec8d8 75%,#9b8cff);
  -webkit-background-clip:text; background-clip:text; color:transparent; }
.skk-sub{ color:#8b97b8; font-size:.92rem; margin-top:2px; }
.skk-pill{ display:inline-block; font-family:'IBM Plex Mono',monospace; font-size:.74rem;
  font-weight:700; letter-spacing:.12em; color:#5ec8d8; border:1px solid rgba(94,200,216,.35);
  background:rgba(94,200,216,.08); padding:4px 11px; border-radius:999px; margin-bottom:6px; }
div[data-testid="stMetricValue"]{ font-family:'IBM Plex Mono',monospace; }
.stButton button{ border-radius:10px; }
</style>
""", unsafe_allow_html=True)

# ── oturum durumu ────────────────────────────────────────────────────────────
ss = st.session_state
ss.setdefault("username", None)
ss.setdefault("recorded", set())     # mükerrer kayıt engelleme
ss.setdefault("resets", {})          # {(diff, level): sayaç}
ss.setdefault("last_solve_msg", None)


# ── kullanıcı adı kapısı ─────────────────────────────────────────────────────
def username_gate():
    _, mid, _ = st.columns([1, 1.1, 1])
    with mid:
        st.markdown("<div style='height:14vh'></div>", unsafe_allow_html=True)
        st.markdown("<div class='skk-pill'>▦ SHIKAKU STUDIO</div>", unsafe_allow_html=True)
        st.markdown("<p class='skk-title'>Izgarayı dikdörtgenlere böl.</p>", unsafe_allow_html=True)
        st.markdown("<p class='skk-sub'>5 zorluk · 100 bölüm · canlı liderlik tablosu. "
                    "Başlamak için bir kullanıcı adı seç — oyuncu sınırı yok.</p>",
                    unsafe_allow_html=True)
        name = st.text_input("Kullanıcı adı", max_chars=24, placeholder="örn. gridmaster",
                             label_visibility="collapsed")
        if st.button("Oyuna başla →", type="primary", use_container_width=True):
            name = (name or "").strip()
            if len(name) < 2:
                st.warning("Kullanıcı adı en az 2 karakter olmalı.")
            else:
                ss.username = name
                st.rerun()
        with st.expander("Kurallar"):
            st.markdown(
                "- Izgarayı **birbiriyle kesişmeyen** dikdörtgenlere/karelere böl.\n"
                "- Her dikdörtgenin içinde **tam olarak bir sayı** kalmalı.\n"
                "- Dikdörtgenin **alanı** (hücre sayısı) içindeki **sayıya eşit** olmalı.\n"
                "- Tüm ızgara boşluksuz kaplandığında bölüm çözülür.")


if not ss.username:
    username_gate()
    st.stop()


# ── kenar çubuğu ─────────────────────────────────────────────────────────────
entries = load_leaderboard()
my_best = user_best_times(entries, ss.username)

with st.sidebar:
    st.markdown("<div class='skk-pill'>▦ SHIKAKU STUDIO</div>", unsafe_allow_html=True)
    st.markdown(f"**Oyuncu:** `{ss.username}`")
    solved_n = len(my_best)
    st.progress(solved_n / (LEVELS_PER_DIFFICULTY * len(DIFFICULTIES)),
                text=f"{solved_n} / 100 bölüm çözüldü")
    if st.button("Oyuncu değiştir", use_container_width=True):
        ss.username = None
        st.rerun()

    st.divider()
    page = st.radio("Sayfa", ["🎮 Oyna", "🏆 Liderlik Tablosu"], label_visibility="collapsed")
    st.divider()

    coop = st.toggle("👥 Co-op modu", value=False,
                     help="Aynı ekranda iki oyuncu: her oyuncunun kendi seçim "
                          "renk paleti olur. Skor, oturumdaki kullanıcı adına yazılır.")


# ════════════════════════════════════════════════════════════════════════════
# OYNA SAYFASI
# ════════════════════════════════════════════════════════════════════════════
if page.endswith("Oyna"):
    c1, c2 = st.columns([2.2, 1], vertical_alignment="bottom")
    with c1:
        st.markdown("<p class='skk-title'>Shikaku</p>", unsafe_allow_html=True)
        st.markdown("<p class='skk-sub'>Her sayı, alanı kendisine eşit olan tek bir "
                    "dikdörtgene ait olmalı. Sürükleyerek çiz, tıklayarak sil.</p>",
                    unsafe_allow_html=True)

    # zorluk seçimi
    diff_labels = {k: f"{v['label']}  ·  {v['size']}×{v['size']}" for k, v in DIFFICULTIES.items()}
    diff_key = st.radio("Zorluk", DIFF_ORDER, horizontal=True,
                        format_func=lambda k: diff_labels[k])

    # bölüm seçimi (çözülenler ✓ ile işaretli)
    def lvl_label(i: int) -> str:
        done = (diff_key, i) in my_best
        best = f"  ✓ {fmt_time(my_best[(diff_key, i)])}" if done else ""
        return f"Bölüm {i:02d}{best}"

    lc1, lc2, _ = st.columns([1.4, 1, 2])
    with lc1:
        level = st.selectbox("Bölüm", list(range(1, LEVELS_PER_DIFFICULTY + 1)),
                             format_func=lvl_label)
    with lc2:
        st.write("")
        if st.button("↺ Yeniden başlat", use_container_width=True):
            ss.resets[(diff_key, level)] = ss.resets.get((diff_key, level), 0) + 1

    # bulmaca + bileşen
    reset_n = ss.resets.get((diff_key, level), 0)
    puzzle = generate_puzzle(diff_key, level)
    puzzle_id = f"{diff_key}-{level}-r{reset_n}"
    tag = f"#{DIFFICULTIES[diff_key]['label'].upper()}-{level:02d}"

    board = get_board_component()
    value = board(
        width=puzzle["width"], height=puzzle["height"], clues=puzzle["clues"],
        cell=DIFFICULTIES[diff_key]["cell"], puzzle_id=puzzle_id, tag=tag,
        coop=coop, default=None, key=f"board-{puzzle_id}",
    )

    # çözüm geri bildirimi → liderlik kaydı (tek sefer)
    if isinstance(value, dict) and value.get("solved") and value.get("puzzle_id") == puzzle_id:
        rk = (ss.username, puzzle_id, value.get("nonce"))
        if rk not in ss.recorded:
            ss.recorded.add(rk)
            record_solve(ss.username, diff_key, level, value["elapsed"], coop)
            prev_best = my_best.get((diff_key, level))
            t = value["elapsed"]
            if prev_best is None or t < prev_best:
                ss.last_solve_msg = f"🎉 **{tag}** çözüldü — **{fmt_time(t)}** (yeni kişisel rekor!)"
            else:
                ss.last_solve_msg = (f"✅ **{tag}** çözüldü — {fmt_time(t)} "
                                     f"(rekorun: {fmt_time(prev_best)})")
            st.rerun()

    if ss.last_solve_msg:
        st.success(ss.last_solve_msg)
        nxt = level + 1 if level < LEVELS_PER_DIFFICULTY else None
        if nxt and st.button(f"Sonraki bölüm → Bölüm {nxt:02d}", type="primary"):
            ss.last_solve_msg = None
            st.rerun()
        if not nxt:
            st.caption("Bu zorluğun son bölümünü bitirdin — bir üst zorluğu dene!")

    with st.expander("Nasıl oynanır?"):
        st.markdown(
            "1. **Sürükle:** Bir hücreden basılı tutup sürükleyerek dikdörtgen çiz.\n"
            "2. **Tıkla:** Var olan bir dikdörtgene tıklayınca silinir; boş hücreye "
            "tıklayınca 1×1 kutu konur.\n"
            "3. Yeni çizilen alan, çakıştığı eski dikdörtgenleri otomatik kaldırır.\n"
            "4. Doğru boyuttaki dikdörtgenin kenarı **düz çizgiye**, içindeki sayı "
            "**yeşil rozete** döner.\n"
            "5. `Ctrl+Z` ile geri alabilirsin. Tüm ızgara hatasız kaplandığında süre durur "
            "ve skor kaydedilir.")

# ════════════════════════════════════════════════════════════════════════════
# LİDERLİK SAYFASI
# ════════════════════════════════════════════════════════════════════════════
else:
    st.markdown("<p class='skk-title'>Liderlik Tablosu</p>", unsafe_allow_html=True)
    st.markdown("<p class='skk-sub'>Tüm oyuncular, tüm zamanlar. Veriler bu sunucudaki "
                "<code>shikaku_leaderboard.json</code> dosyasında saklanır.</p>",
                unsafe_allow_html=True)

    if not entries:
        st.info("Henüz hiç skor yok — ilk bölümü çözen sen ol! 🏁")
        st.stop()

    df = pd.DataFrame(entries)
    df["Zorluk"] = df["difficulty"].map(lambda k: DIFFICULTIES.get(k, {}).get("label", k))

    f1, f2, f3 = st.columns([1, 1, 2])
    with f1:
        fdiff = st.selectbox("Zorluk filtresi", ["Tümü"] + DIFF_ORDER,
                             format_func=lambda k: "Tümü" if k == "Tümü"
                             else DIFFICULTIES[k]["label"])
    with f2:
        flevel = st.selectbox("Bölüm filtresi",
                              ["Tümü"] + list(range(1, LEVELS_PER_DIFFICULTY + 1)),
                              format_func=lambda v: "Tümü" if v == "Tümü" else f"Bölüm {v:02d}")

    fdf = df.copy()
    if fdiff != "Tümü":
        fdf = fdf[fdf["difficulty"] == fdiff]
    if flevel != "Tümü":
        fdf = fdf[fdf["level"] == flevel]

    t1, t2 = st.tabs(["⏱️ En İyi Süreler", "📈 En Çok Bölüm Çözenler"])

    with t1:
        if fdf.empty:
            st.info("Bu filtreyle eşleşen skor yok.")
        else:
            # her (oyuncu, zorluk, bölüm) için en iyi süre, sonra süreye göre sırala
            best = (fdf.sort_values("time")
                       .groupby(["username", "difficulty", "level"], as_index=False)
                       .first()
                       .sort_values("time")
                       .head(100)
                       .reset_index(drop=True))
            best.index += 1
            show = pd.DataFrame({
                "Oyuncu": best["username"],
                "Zorluk": best["Zorluk"],
                "Bölüm": best["level"].map(lambda v: f"{v:02d}"),
                "Süre": best["time"].map(fmt_time),
                "Mod": best.get("coop", pd.Series([False]*len(best))).map(
                    lambda c: "Co-op" if c else "Solo"),
                "Tarih": best["ts"],
            }, index=best.index)
            show.index.name = "Sıra"
            st.dataframe(show, use_container_width=True, height=520)

    with t2:
        if fdf.empty:
            st.info("Bu filtreyle eşleşen skor yok.")
        else:
            solved = (fdf.drop_duplicates(["username", "difficulty", "level"])
                         .groupby("username")
                         .agg(Çözülen=("level", "size"), Toplam_Süre=("time", "sum"))
                         .sort_values(["Çözülen", "Toplam_Süre"], ascending=[False, True])
                         .head(100)
                         .reset_index())
            solved.index += 1
            show2 = pd.DataFrame({
                "Oyuncu": solved["username"],
                "Çözülen Bölüm": solved["Çözülen"],
                "Toplam Süre": solved["Toplam_Süre"].map(fmt_time),
            }, index=solved.index)
            show2.index.name = "Sıra"
            st.dataframe(show2, use_container_width=True, height=520)
