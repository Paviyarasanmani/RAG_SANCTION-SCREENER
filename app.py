
"""
app.py — Sanctions Screening System
Pure Flask app. No Gradio UI interference.
Serves the exact HTML design from the mockup.
"""

import os
import json
from pathlib import Path
import threading
from flask import Flask, request, jsonify, render_template_string
import google.generativeai as genai
from dotenv import load_dotenv
import gdown

from parsers.moha_parser  import parse_moha
from parsers.unscr_parser import parse_unscr
from parsers.ofac_parser  import parse_ofac
from matcher import search

# ── Gemini setup ──────────────────────────────────────────────────────────────
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MOHA_GOOGLE_DRIVE_FILE_ID = os.getenv("MOHA_GOOGLE_DRIVE_FILE_ID", "")
UNSCR_GOOGLE_DRIVE_FILE_ID = os.getenv("UNSCR_GOOGLE_DRIVE_FILE_ID", "")
OFAC_GOOGLE_DRIVE_FILE_ID = os.getenv("OFAC_GOOGLE_DRIVE_FILE_ID", "")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model     = genai.GenerativeModel("gemini-2.0-flash")
    GEMINI_AVAILABLE = True
    print("[APP] Gemini AI configured successfully")
else:
    gemini_model     = None
    GEMINI_AVAILABLE = False
    print("[APP] WARNING: No GEMINI_API_KEY — AI explanations disabled.")

def ensure_file(path: str, file_id: str):
    """
    Check whether file exists.
    If missing, download from Google Drive.
    """

    file_path = Path(path)

    if file_path.exists():
        print(f"[OK] Found: {path}")
        return path

    print(f"[DOWNLOAD] Missing: {path}")

    file_path.parent.mkdir(parents=True, exist_ok=True)

    gdown.download(
        id=file_id,
        output=str(file_path),
        quiet=False
    )

    return path


# ── Load data ─────────────────────────────────────────────────────────────────
MOHA_PATH  = "data/MOHA.pdf"
UNSCR_PATH = "data/UNSCR.xml"
OFAC_PATH  = "data/OFAC.xml"

MOHA_PATH = ensure_file(
    MOHA_PATH,
    MOHA_GOOGLE_DRIVE_FILE_ID
)

UNSCR_PATH = ensure_file(
    UNSCR_PATH,
    UNSCR_GOOGLE_DRIVE_FILE_ID
)

OFAC_PATH = ensure_file(
    OFAC_PATH,
    OFAC_GOOGLE_DRIVE_FILE_ID
)

print("\n" + "="*60)
print("SANCTIONS SCREENER — LOADING DATA")
print("="*60)

ALL_RECORDS   = []
SOURCE_COUNTS = {"MOHA": 0, "UNSCR": 0, "OFAC": 0}

for label, path, parser in [
    ("MOHA",  MOHA_PATH,  parse_moha),
    ("UNSCR", UNSCR_PATH, parse_unscr),
    ("OFAC",  OFAC_PATH,  parse_ofac),
]:
    try:
        recs = parser(path)
        ALL_RECORDS.extend(recs)
        SOURCE_COUNTS[label] = len(recs)
        print(f"[APP] {label} loaded: {len(recs):,} records")
    except Exception as e:
        print(f"[APP] ERROR — {label}: {e}")

print(f"[APP] TOTAL: {len(ALL_RECORDS):,}")
print("="*60 + "\n")

_HISTORY = []

# ── Gemini explanation ────────────────────────────────────────────────────────
def get_gemini_explanation(user_input, top_result):
    if not GEMINI_AVAILABLE or not top_result or top_result["decision"] == "CLEAR":
        return ""
    record   = top_result["record"]
    decision = top_result["decision"]
    source_full = {"MOHA":"Malaysian Ministry of Home Affairs","UNSCR":"UN Security Council","OFAC":"US Treasury OFAC"}.get(record.get("source",""), record.get("source",""))
    prompt = f"""You are a compliance officer reviewing a sanctions screening result.
SEARCHED: Name={user_input.get('name')}, DOB={user_input.get('dob')}, Nat={user_input.get('nationality')}, PP={user_input.get('passport')}, IC={user_input.get('ic')}
MATCHED: Source={source_full}, Ref={record.get('ref')}, Names={', '.join(record.get('names',[])[:4])}, DOB={record.get('dob')}, Nat={record.get('nationality')}, Listed={record.get('date_listed')}
SCORES: Overall={top_result['final_score']}% Decision={decision}, Name={top_result['name_score']}%, DOB={top_result['dob_score']}%, Nat={top_result['nat_score']}%, ID={'EXACT' if top_result['id_matched'] else str(top_result['id_score'])+'%'}
Write 3-4 sentences: state decision and why, which fields matched, recommended action. No bullet points."""
    try:
        return gemini_model.generate_content(prompt).text.strip()
    except Exception as e:
        err = str(e)
        if "quota" in err.lower() or "429" in err or "rate" in err.lower():
            return "⚠️ Gemini free tier quota reached for this minute. The screening result above is still valid. Wait 60 seconds and screen again."
        return "⚠️ AI explanation unavailable. Screening result is unaffected."

# ── Flask app ─────────────────────────────────────────────────────────────────
flask_app = Flask(__name__)

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sanctions Screening System</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:#010409;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#c9d1d9}
body{display:flex;align-items:flex-start;justify-content:center;padding:20px 16px;min-height:100vh}
.shell{background:#0d1117;border-radius:12px;overflow:hidden;border:0.5px solid #30363d;width:100%;max-width:1280px}

/* TOPBAR */
.topbar{background:#161b22;border-bottom:0.5px solid #30363d;padding:12px 20px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
.logo{display:flex;align-items:center;gap:10px;color:#e6edf3;font-size:15px;font-weight:500}
.logo-icon{width:32px;height:32px;background:#1f6feb22;border:1px solid #1f6feb55;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:17px}
.badges{display:flex;gap:6px;flex-wrap:wrap}
.badge{font-size:11px;padding:3px 10px;border-radius:20px;font-weight:500}
.badge-ok{background:#1a3a2a;color:#3fb950;border:0.5px solid #238636}
.badge-ai{background:#1f2d4e;color:#79b8ff;border:0.5px solid #1f6feb}
.badge-err{background:#2d1117;color:#ff7b72;border:0.5px solid #f85149}

/* MAIN LAYOUT */
.main{display:grid;grid-template-columns:320px 1fr;min-height:700px}

/* LEFT PANEL */
.left{background:#161b22;border-right:0.5px solid #30363d;padding:20px;display:flex;flex-direction:column;gap:0;overflow-y:auto}
.section-label{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:.08em;font-weight:500;margin-bottom:10px}
.field-group{margin-bottom:12px}
.field-label{font-size:12px;color:#8b949e;margin-bottom:5px;display:block}
.field-input{width:100%;background:#0d1117;border:0.5px solid #30363d;border-radius:6px;padding:8px 10px;color:#e6edf3;font-size:13px;font-family:inherit;outline:none;transition:border-color .15s,box-shadow .15s}
.field-input:focus{border-color:#1f6feb;box-shadow:0 0 0 3px rgba(31,111,235,.15)}
.field-input::placeholder{color:#484f58}
.field-row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.btn-screen{width:100%;padding:10px 14px;background:#1f6feb;border:none;border-radius:6px;color:#fff;font-size:13px;font-weight:500;cursor:pointer;margin-top:8px;font-family:inherit;display:flex;align-items:center;justify-content:center;gap:7px;transition:background .15s}
.btn-screen:hover{background:#388bfd}
.btn-screen:disabled{opacity:.55;cursor:not-allowed}
.btn-clear{width:100%;padding:8px;background:transparent;border:0.5px solid #30363d;border-radius:6px;color:#8b949e;font-size:13px;cursor:pointer;margin-top:6px;font-family:inherit;transition:border-color .15s,color .15s}
.btn-clear:hover{border-color:#8b949e;color:#c9d1d9}
.divider{border:none;border-top:0.5px solid #21262d;margin:16px 0}
.stat-row{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:14px}
.stat{background:#0d1117;border:0.5px solid #21262d;border-radius:6px;padding:10px 8px;text-align:center}
.stat-num{font-size:15px;font-weight:600;color:#e6edf3}
.stat-lbl{font-size:10px;color:#8b949e;margin-top:2px;text-transform:uppercase;letter-spacing:.05em}
.history-row{display:flex;align-items:center;gap:8px;padding:7px 10px;border-radius:6px;background:#0d1117;border:0.5px solid #21262d;margin-bottom:6px;font-size:12px}
.h-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.h-dot-hit{background:#f85149}
.h-dot-pos{background:#e3b341}
.h-dot-clr{background:#3fb950}
.h-name{color:#c9d1d9;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.h-src{color:#8b949e;font-size:11px;margin-right:4px}
.h-score-hit{color:#ff7b72;font-weight:600;font-size:11px}
.h-score-pos{color:#e3b341;font-weight:600;font-size:11px}
.h-score-ok{color:#3fb950;font-weight:600;font-size:11px}

/* RIGHT PANEL */
.right{padding:20px;background:#0d1117;overflow-y:auto;display:flex;flex-direction:column}
.tabs-row{display:flex;align-items:center;margin-bottom:14px}
.tab{padding:5px 14px;border-radius:6px;font-size:12px;color:#8b949e;cursor:pointer;border:0.5px solid transparent;font-family:inherit;background:transparent}
.tab.active{background:#1f2d4e;color:#79b8ff;border-color:#1f6feb55}

/* BANNERS */
.banner{border-radius:8px;padding:16px 18px;margin-bottom:14px}
.banner-idle{background:#161b22;border:1px dashed #30363d;color:#8b949e;text-align:center;padding:60px 20px}
.banner-hit{background:#2d1117;border:1px solid #f85149;color:#ff7b72}
.banner-pos{background:#271d00;border:1px solid #e3b341;color:#e3b341}
.banner-clr{background:#0d2119;border:1px solid #238636;color:#3fb950}
.banner-title{font-size:16px;font-weight:500;display:flex;align-items:center;gap:8px}
.banner-sub{font-size:12px;margin-top:4px;opacity:.8}
.banner-stats{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
.bstat{font-size:11px;padding:3px 10px;border-radius:20px;background:#ffffff0f;font-weight:500;color:inherit}

/* MATCH CARDS */
.card{background:#161b22;border:0.5px solid #30363d;border-radius:8px;padding:16px;margin-bottom:10px}
.card-hit{border-color:#f85149}
.card-pos{border-color:#e3b341}
.card-clr{border-color:#238636}
.card-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
.card-rank{font-size:12px;color:#8b949e}
.cbadge{border-radius:20px;padding:3px 12px;font-size:12px;font-weight:500}
.cbadge-hit{background:#2d1117;color:#ff7b72;border:0.5px solid #f85149}
.cbadge-pos{background:#271d00;color:#e3b341;border:0.5px solid #e3b341}
.cbadge-clr{background:#0d2119;color:#3fb950;border:0.5px solid #238636}
.score-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:5px}
.score-lbl{font-size:12px;color:#8b949e}
.score-hit{font-size:20px;font-weight:600;color:#ff7b72}
.score-pos{font-size:20px;font-weight:600;color:#e3b341}
.score-clr{font-size:20px;font-weight:600;color:#3fb950}
.bar-bg{background:#21262d;border-radius:4px;height:4px;margin-bottom:14px}
.bar-hit{background:#f85149;height:4px;border-radius:4px}
.bar-pos{background:#e3b341;height:4px;border-radius:4px}
.bar-clr{background:#238636;height:4px;border-radius:4px}
.meta{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap}
.meta-item{background:#0d1117;border:0.5px solid #21262d;border-radius:6px;padding:6px 10px}
.meta-key{font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:.06em}
.meta-val{font-size:12px;color:#e6edf3;font-weight:500;margin-top:2px}
.stbl{width:100%;border-collapse:collapse;font-size:12px}
.stbl th{color:#8b949e;font-weight:500;text-align:left;padding:6px 8px;border-bottom:0.5px solid #21262d;background:#0d1117}
.stbl td{padding:7px 8px;color:#c9d1d9;border-bottom:0.5px solid #0d1117}
.stbl tr:last-child td{border-bottom:none}
.pct-hit{color:#ff7b72;font-weight:600}
.pct-pos{color:#e3b341;font-weight:600}
.pct-ok{color:#3fb950;font-weight:600}
.pct-lo{color:#8b949e}
.exact{color:#3fb950;font-weight:600;font-size:11px}
.names-row{background:#0d1117;border-radius:6px;padding:8px 10px;font-size:11px;color:#8b949e;margin-top:10px;line-height:1.7}
.names-row span{color:#c9d1d9}

/* AI BOX */
.ai-box{background:#161b22;border:0.5px solid #1f6feb44;border-radius:8px;padding:14px 16px;margin-top:6px}
.ai-label{font-size:11px;color:#58a6ff;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;font-weight:500}
.ai-text{font-size:13px;color:#8b949e;line-height:1.65}

/* SPINNER */
@keyframes spin{to{transform:rotate(360deg)}}
.spinner{display:inline-block;width:13px;height:13px;border:2px solid rgba(255,255,255,.2);border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite;vertical-align:middle}
</style>
</head>
<body>
<div class="shell">

  <!-- TOPBAR -->
  <div class="topbar">
    <div class="logo">
      <div class="logo-icon">🛡️</div>
      Sanctions Screening System
    </div>
    <div class="badges" id="top-badges">
      <span class="badge badge-ok">🗃️ {{ total }} records</span>
      {% if ai_on %}
      <span class="badge badge-ai">✨ AI enabled</span>
      {% else %}
      <span class="badge badge-err">✨ AI disabled</span>
      {% endif %}
    </div>
  </div>

  <!-- MAIN -->
  <div class="main">

    <!-- LEFT -->
    <div class="left">
      <div class="section-label">Client details</div>

      <div class="field-group">
        <label class="field-label" for="f-name">Full name *</label>
        <input id="f-name" class="field-input" placeholder="e.g. Muhammad Ali bin Hassan" autocomplete="off">
      </div>

      <div class="field-row">
        <div class="field-group">
          <label class="field-label" for="f-dob">Date of birth</label>
          <input id="f-dob" class="field-input" placeholder="YYYY-MM-DD">
        </div>
        <div class="field-group">
          <label class="field-label" for="f-nat">Nationality</label>
          <input id="f-nat" class="field-input" placeholder="e.g. Malaysia">
        </div>
      </div>

      <div class="field-row">
        <div class="field-group">
          <label class="field-label" for="f-pp">Passport no.</label>
          <input id="f-pp" class="field-input" placeholder="e.g. A12345678">
        </div>
        <div class="field-group">
          <label class="field-label" for="f-ic">IC / National ID</label>
          <input id="f-ic" class="field-input" placeholder="e.g. 850615-14-5678">
        </div>
      </div>

      <button class="btn-screen" id="btn-screen">
        🔍&nbsp; Screen client
      </button>
      <button class="btn-clear" id="btn-clear">Clear</button>

      <hr class="divider">

      <div class="section-label">Database status</div>
      <div class="stat-row">
        <div class="stat">
          <div class="stat-num" style="color:#3fb950">{{ moha }}</div>
          <div class="stat-lbl">MOHA</div>
        </div>
        <div class="stat">
          <div class="stat-num" style="color:#3fb950">{{ unscr }}</div>
          <div class="stat-lbl">UNSCR</div>
        </div>
        <div class="stat">
          <div class="stat-num" style="color:#3fb950">{{ ofac }}</div>
          <div class="stat-lbl">OFAC</div>
        </div>
      </div>

      <div class="section-label" style="margin-top:4px">Recent screens</div>
      <div id="history-panel">
        <div style="color:#484f58;font-size:12px;padding:4px 0">No screens yet.</div>
      </div>
    </div>

    <!-- RIGHT -->
    <div class="right">
      <div class="tabs-row">
        <button class="tab active">Results</button>
      </div>

      <div id="results-area">
        <div class="banner banner-idle">
          <div style="font-size:44px;margin-bottom:12px">🛡️</div>
          <div style="font-size:16px;color:#e6edf3;font-weight:500;margin-bottom:6px">Ready to screen</div>
          <div style="font-size:13px;color:#8b949e">Enter client details on the left and click <strong style="color:#c9d1d9">Screen client</strong></div>
        </div>
      </div>
    </div>

  </div><!-- /main -->
</div><!-- /shell -->

<script>
// ── helpers ───────────────────────────────────────────────────────────────────
function pctClass(v){
  if(v>=85) return 'pct-hit';
  if(v>=60) return 'pct-pos';
  if(v>=35) return 'pct-pos';
  return 'pct-lo';
}
function srcLabel(s){
  return {MOHA:'🇲🇾 MOHA',UNSCR:'🌐 UNSCR',OFAC:'🇺🇸 OFAC'}[s]||s;
}
function cap(s){return s?s.charAt(0).toUpperCase()+s.slice(1):'';}

// ── render card ───────────────────────────────────────────────────────────────
function renderCard(r, rank){
  const dec = r.decision;
  const map = {
    HIT:     {card:'card-hit',   badge:'cbadge-hit',  badgeTxt:'🔴 HIT',      score:'score-hit', bar:'bar-hit'},
    POSSIBLE:{card:'card-pos',   badge:'cbadge-pos',  badgeTxt:'🟡 POSSIBLE', score:'score-pos', bar:'bar-pos'},
    CLEAR:   {card:'card-clr',   badge:'cbadge-clr',  badgeTxt:'🟢 CLEAR',    score:'score-clr', bar:'bar-clr'},
  }[dec]||{card:'card-clr',badge:'cbadge-clr',badgeTxt:dec,score:'score-clr',bar:'bar-clr'};

  const idCell = r.id_matched
    ? '<span class="exact">✓ Exact match</span>'
    : `<span class="${pctClass(r.id_score)}">${r.id_score}%</span>`;

  const pp  = (r.passports||[]).slice(0,2).join(', ');
  const ic  = (r.ic||[]).slice(0,2).join(', ');
  const ids = [pp?'Passports: '+pp:'', ic?'IC: '+ic:''].filter(Boolean).join(' | ')||'No IDs on record';
  const listedMeta = r.date_listed
    ? `<div class="meta-item"><div class="meta-key">Listed</div><div class="meta-val">${r.date_listed}</div></div>` : '';

  return `
<div class="card ${map.card}">
  <div class="card-header">
    <span class="card-rank">Match #${rank}</span>
    <span class="cbadge ${map.badge}">${map.badgeTxt}</span>
  </div>
  <div class="score-row">
    <span class="score-lbl">Overall match score</span>
    <span class="${map.score}">${r.final_score}%</span>
  </div>
  <div class="bar-bg"><div class="${map.bar}" style="width:${Math.min(r.final_score,100)}%"></div></div>
  <div class="meta">
    <div class="meta-item"><div class="meta-key">Source</div><div class="meta-val">${srcLabel(r.source)}</div></div>
    <div class="meta-item"><div class="meta-key">Reference</div><div class="meta-val">${r.ref||'N/A'}</div></div>
    <div class="meta-item"><div class="meta-key">Type</div><div class="meta-val">${cap(r.type)||'N/A'}</div></div>
    ${listedMeta}
  </div>
  <table class="stbl">
    <thead><tr><th>Field</th><th>Score</th><th>Detail</th></tr></thead>
    <tbody>
      <tr>
        <td>Name / aliases</td>
        <td class="${pctClass(r.name_score)}">${r.name_score}%</td>
        <td>Matched: <em style="color:#e6edf3">${r.matched_name||'—'}</em></td>
      </tr>
      <tr>
        <td>Date of birth</td>
        <td class="${pctClass(r.dob_score)}">${r.dob_score}%</td>
        <td>Record: <span style="color:#e6edf3">${r.dob||'N/A'}</span></td>
      </tr>
      <tr>
        <td>Nationality</td>
        <td class="${pctClass(r.nat_score)}">${r.nat_score}%</td>
        <td>Record: <span style="color:#e6edf3">${r.nationality||'N/A'}</span></td>
      </tr>
      <tr>
        <td>Passport / IC</td>
        <td>${idCell}</td>
        <td style="color:#8b949e">${ids}</td>
      </tr>
    </tbody>
  </table>
  <div class="names-row">All names on record: <span>${(r.names||[]).join(' | ')||'—'}</span></div>
</div>`;
}

// ── render full result ────────────────────────────────────────────────────────
function renderResult(data){
  const btn = document.getElementById('btn-screen');
  btn.disabled = false;
  btn.innerHTML = '🔍&nbsp; Screen client';

  const dec = data.overall;
  const bannerCfg = {
    HIT:     {cls:'banner-hit', icon:'🚨', title:'HIT — Sanctioned individual / entity found',   sub:'Immediate escalation required. Do not proceed with this client.'},
    POSSIBLE:{cls:'banner-pos', icon:'⚠️', title:'POSSIBLE MATCH — Manual review required',      sub:'Partial match found. Compliance review required before proceeding.'},
    CLEAR:   {cls:'banner-clr', icon:'✅', title:'CLEAR — No sanctions match found',              sub:'No significant match found in MOHA, UNSCR, or OFAC records.'},
  }[dec];

  const bstatHit  = data.hits>0     ? 'style="color:#ff7b72;font-weight:600"':'';
  const bstatPoss = data.possible>0 ? 'style="color:#e3b341;font-weight:600"':'';

  const banner = `
<div class="banner ${bannerCfg.cls}">
  <div class="banner-title">${bannerCfg.icon} ${bannerCfg.title}</div>
  <div class="banner-sub">${bannerCfg.sub}</div>
  <div class="banner-stats">
    <span class="bstat">Searched ${data.total.toLocaleString()} records</span>
    <span class="bstat" ${bstatHit}>${data.hits} HIT${data.hits!==1?'s':''}</span>
    <span class="bstat" ${bstatPoss}>${data.possible} Possible</span>
  </div>
</div>`;

  const cards = (data.results||[]).length
    ? (data.results||[]).map((r,i)=>renderCard(r,i+1)).join('')
    : '<div style="color:#8b949e;font-size:13px;padding:10px 0">No records passed minimum threshold.</div>';

  const aiBox = data.explanation ? `
<div class="ai-box">
  <div class="ai-label">✨ AI compliance assessment</div>
  <div class="ai-text">${data.explanation}</div>
</div>` : '';

  document.getElementById('results-area').innerHTML = `
<div class="tabs-row" style="margin-bottom:14px">
  <button class="tab active">Results</button>
</div>
${banner}
${cards}
${aiBox}`;

  // History
  const hist = (data.history||[]);
  if(hist.length){
    const dotMap   = {HIT:'h-dot-hit', POSSIBLE:'h-dot-pos', CLEAR:'h-dot-clr'};
    const scoreMap = {HIT:'h-score-hit', POSSIBLE:'h-score-pos', CLEAR:'h-score-ok'};
    const rows = hist.map(h=>`
<div class="history-row">
  <div class="h-dot ${dotMap[h.decision]||'h-dot-clr'}"></div>
  <div class="h-name">${h.name}</div>
  <div class="h-src">${h.src}</div>
  <div class="${scoreMap[h.decision]||'h-score-ok'}">${h.decision==='CLEAR'?'Clear':h.score}</div>
</div>`).join('');
    document.getElementById('history-panel').innerHTML = rows;
  }
}

// ── screen ─────────────────────────────────────────────────────────────────────
async function doScreen(){
  const name = document.getElementById('f-name').value.trim();
  if(!name){document.getElementById('f-name').focus();return;}

  const btn = document.getElementById('btn-screen');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>&nbsp; Screening…';

  document.getElementById('results-area').innerHTML = `
<div style="text-align:center;padding:60px 20px;color:#8b949e">
  <div style="font-size:40px;margin-bottom:14px"><span class="spinner" style="width:36px;height:36px;border-width:3px"></span></div>
  <div style="font-size:14px;color:#c9d1d9">Screening against ${document.querySelector('#top-badges .badge-ok') ? document.querySelector('#top-badges .badge-ok').textContent : 'all lists'}…</div>
</div>`;

  try{
    const res = await fetch('/screen', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        name:        document.getElementById('f-name').value.trim(),
        dob:         document.getElementById('f-dob').value.trim(),
        nationality: document.getElementById('f-nat').value.trim(),
        passport:    document.getElementById('f-pp').value.trim(),
        ic:          document.getElementById('f-ic').value.trim(),
      })
    });
    const data = await res.json();
    renderResult(data);
  }catch(e){
    btn.disabled = false;
    btn.innerHTML = '🔍&nbsp; Screen client';
    document.getElementById('results-area').innerHTML = `
<div style="background:#2d1117;border:1px solid #f85149;padding:16px;border-radius:8px;color:#ff7b72">
  ⚠️ Error contacting server: ${e.message}
</div>`;
  }
}

// ── clear ─────────────────────────────────────────────────────────────────────
function doClear(){
  ['f-name','f-dob','f-nat','f-pp','f-ic'].forEach(id=>{
    document.getElementById(id).value='';
  });
  document.getElementById('results-area').innerHTML = `
<div class="banner banner-idle">
  <div style="font-size:44px;margin-bottom:12px">🛡️</div>
  <div style="font-size:16px;color:#e6edf3;font-weight:500;margin-bottom:6px">Ready to screen</div>
  <div style="font-size:13px;color:#8b949e">Enter client details on the left and click <strong style="color:#c9d1d9">Screen client</strong></div>
</div>`;
}

// ── events ────────────────────────────────────────────────────────────────────
document.getElementById('btn-screen').addEventListener('click', doScreen);
document.getElementById('btn-clear').addEventListener('click', doClear);
document.getElementById('f-name').addEventListener('keydown', e=>{ if(e.key==='Enter') doScreen(); });
</script>
</body>
</html>"""

@flask_app.route("/")
def index():
    moha  = f"{SOURCE_COUNTS['MOHA']:,}"
    unscr = f"{SOURCE_COUNTS['UNSCR']:,}"
    ofac  = f"{SOURCE_COUNTS['OFAC']:,}"
    total = f"{len(ALL_RECORDS):,}"
    return render_template_string(
        HTML_PAGE,
        total=total, moha=moha, unscr=unscr, ofac=ofac,
        ai_on=GEMINI_AVAILABLE
    )

@flask_app.route("/screen", methods=["POST"])
def screen():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"status": "idle"})

    user_input = {
        "name":        name,
        "dob":         (data.get("dob") or "").strip(),
        "nationality": (data.get("nationality") or "").strip(),
        "passport":    (data.get("passport") or "").strip(),
        "ic":          (data.get("ic") or "").strip(),
    }

    print(f"[SCREEN] {user_input}")
    results = search(user_input, ALL_RECORDS)

    if   any(r["decision"] == "HIT"      for r in results): overall = "HIT"
    elif any(r["decision"] == "POSSIBLE" for r in results): overall = "POSSIBLE"
    else:                                                     overall = "CLEAR"

    hits_n     = sum(1 for r in results if r["decision"] == "HIT")
    possible_n = sum(1 for r in results if r["decision"] == "POSSIBLE")
    top_src    = results[0]["record"]["source"] if results else "—"

    def serialise(r):
        rec = r["record"]
        return {
            "decision":     r["decision"],
            "final_score":  r["final_score"],
            "name_score":   r["name_score"],
            "dob_score":    r["dob_score"],
            "nat_score":    r["nat_score"],
            "id_score":     r["id_score"],
            "id_matched":   r["id_matched"],
            "matched_name": r.get("matched_name", ""),
            "source":       rec.get("source", ""),
            "ref":          rec.get("ref", ""),
            "type":         rec.get("type", ""),
            "date_listed":  rec.get("date_listed", ""),
            "dob":          rec.get("dob", ""),
            "nationality":  rec.get("nationality", ""),
            "names":        rec.get("names", [])[:8],
            "passports":    rec.get("passport", [])[:3],
            "ic":           rec.get("ic", [])[:2],
        }

    serialised = [serialise(r) for r in results]

    explanation = ""
    if results and overall in ("HIT", "POSSIBLE"):
        explanation = get_gemini_explanation(user_input, results[0])

    _HISTORY.append({
        "name":     name,
        "decision": overall,
        "score":    f"{results[0]['final_score']}%" if results else "—",
        "src":      top_src if overall != "CLEAR" else "—",
    })

    return jsonify({
        "overall":     overall,
        "hits":        hits_n,
        "possible":    possible_n,
        "total":       len(ALL_RECORDS),
        "results":     serialised,
        "explanation": explanation,
        "history":     list(reversed(_HISTORY[-8:])),
    })

if __name__ == "__main__":
    print("[APP] Starting Flask server on http://0.0.0.0:7860")
    flask_app.run(host="0.0.0.0", port=7860, debug=False, threaded=True)

