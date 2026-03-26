"use strict";
// ══════════════════════════════════════════════════════════
// Criterion QA v3 — Frontend
// ══════════════════════════════════════════════════════════

// ── State ─────────────────────────────────────────────────
let selFile=null, batchFiles=[], batchRes=[], allAgents=[], allAlerts=[];
let currentAgent=null, currentAgentCalls=[], histPage=1, charts={};
const PER_PAGE=8;

// ── SPLASH ─────────────────────────────────────────────────
// Splash uses 100% inline styles — not dependent on CSS loading.
// No video element — pure CSS animation, no autoplay issues.
// endSplash() is called at 2.7s (matching progress bar animation) OR on skip click.

let _splashDone = false;

function endSplash() {
  if (_splashDone) return;
  _splashDone = true;

  const sp  = document.getElementById('splash');
  const app = document.getElementById('app');

  // Fade out splash
  if (sp) {
    sp.classList.add('out');
    sp.style.opacity = '0';
    setTimeout(() => { sp.style.display = 'none'; }, 550);
  }

  // Reveal app — force display even if CSS class fails
  if (app) {
    app.classList.add('show');
    app.style.display    = 'flex';
    app.style.flexDirection = 'column';
    app.style.minHeight  = '100vh';
  }

  // Bootstrap all data in parallel
  try { checkHealth(true); } catch(e) {}
  try { loadSidebar();     } catch(e) {}
  try { renderHistory();   } catch(e) {}
  try { loadAlerts();      } catch(e) {}
}

// Auto-dismiss after 2.7s — matches the progress bar CSS animation duration
setTimeout(endSplash, 2700);

// ── Init (runs immediately — DOM ready when script is at </body>) ─────────
(function(){
  try { document.getElementById("callDate").valueAsDate = new Date(); } catch(e){}
})();

// ── Nav ───────────────────────────────────────────────────
function switchTab(tab){
  document.querySelectorAll(".tab").forEach(s=>s.classList.remove("active"));
  document.querySelectorAll(".tb-tab").forEach(b=>b.classList.remove("active"));
  const s=document.getElementById(`tab-${tab}`);
  if(s) s.classList.add("active");
  document.querySelectorAll(".tb-tab").forEach(b=>{ if(b.dataset.tab===tab) b.classList.add("active"); });
  if(tab==="agents")       { loadAgents(); }
  if(tab==="alerts")       { loadAlerts(); }
  if(tab==="history")      { renderHistory(); loadHistoryStats(); }
  if(tab==="settings")     { checkHealth(false); loadNotifConfig(); loadSysStats(); }
  if(tab==="policy")       { loadPolicies(); }
  if(tab==="logs")         { loadLogs(); }
  if(tab==="batch")        { loadBatchStats(); }
}

async function loadBatchStats(){
  try{
    const h=await apiFetch("/api/health");
    const avg=(h.avg_score||0).toFixed(1);
    setText("batchScoreOrb",avg);
    setText("batchTotalCalls",h.total_calls||0);
    // Count today's completed calls from history
    const hist=getHistory();
    const today=new Date().toDateString();
    const todayCount=hist.filter(h2=>{
      try{ return new Date(h2.date).toDateString()===today; }catch{ return false; }
    }).length;
    setText("batchCompletedCount",todayCount||h.total_calls||0);
  }catch{}
}

async function loadHistoryStats(){
  try{
    const d=await apiFetch("/api/analytics/summary");
    const total=d.total_calls||0;
    setText("histTotalScans",total);
    const avgF1=((d.avg_f1||0)*100).toFixed(0);
    setText("histCompliance", avgF1+"%");
    const trendEl=document.getElementById("histTrend");
    if(trendEl){trendEl.textContent=total>0?"+12%":"—";trendEl.style.color="var(--green)";}
  }catch{}
}

// ── Drag/Drop helper ──────────────────────────────────────
function ev(e, action, isBatch=false){
  e.preventDefault();
  const zone=document.getElementById(isBatch?"batchZone":"uploadZone");
  if(action==="over")  zone.classList.add("over");
  if(action==="leave") zone.classList.remove("over");
  if(action==="drop"){
    zone.classList.remove("over");
    const files=Array.from(e.dataTransfer.files);
    if(isBatch) addBatchFiles(files);
    else if(files[0]) setFile(files[0]);
  }
}

// ── Upload ────────────────────────────────────────────────
function onFileSelect(e){ if(e.target.files[0]) setFile(e.target.files[0]); }
function setFile(f){
  selFile=f;
  const isAud=/\.(mp3|m4a|wav|ogg|flac|webm)$/i.test(f.name);
  setText("fileBadge", isAud?"🎙️":"💬");
  setText("fileName",  f.name);
  setText("fileSize",  fmtSize(f.size));
  show("fileInfo");
  document.getElementById("analyzeBtn").disabled=false;
}

async function processFile(){
  if(!selFile) return;
  hideErr(); showLoading(); hide("results");
  animSteps();

  const form=new FormData();
  form.append("file",        selFile);
  form.append("agent_id",    val("agentId")    ||"agent_001");
  form.append("agent_name",  val("agentName")  ||"");
  form.append("team",        val("agentTeam")  ||"General");
  form.append("customer_id", val("customerId") ||"customer_001");
  form.append("call_date",   val("callDate")   ||today());

  // Long files → async
  const endpoint = selFile.size > 60*1024*1024 ? "/api/upload/async" : "/api/upload";

  try{
    const resp=await fetch(endpoint,{method:"POST",body:form});
    const data=await resp.json();

    if(data.job_id){  // async path
      pollJob(data.job_id);
      return;
    }

    hideLoading();
    displayResults(data);
    autoSave(data);
    loadSidebar();
  }catch(e){
    hideLoading();
    showErr(e.message);
  }
}

let _pollTimer=null;
function pollJob(jobId,attempt=0){
  if(attempt>120){ hideLoading(); showErr("Timed out. Check /api/jobs/"+jobId); return; }
  _pollTimer=setTimeout(async()=>{
    try{
      const job=await apiFetch("/api/jobs/"+jobId);
      setProgress((job.progress||0));
      setText("loadLabel",`Processing… ${job.progress||0}% — ${(job.step||"").replace(/_/g," ")}`);
      if(job.status==="completed" && job.call_id){
        const full=await apiFetch("/api/calls/"+job.call_id);
        hideLoading();
        displayResults(buildFromCallDetail(full));
        loadSidebar();
      } else if(job.status==="failed"){
        hideLoading(); showErr(job.error||"Processing failed");
      } else {
        pollJob(jobId, attempt+1);
      }
    }catch(e){ hideLoading(); showErr("Poll error: "+e.message); }
  },1000);
}

// ── Results ───────────────────────────────────────────────
function displayResults(data){
  const qa=data.quality_analysis||{};
  const sent=data.sentiment||{};
  const tr=data.transcript||{};
  const meta=data.metadata||{};
  const viol=data.policy_violations||[];
  const unparl=data.unparliamentary_hits||[];
  const alerts=data.alerts||[];

  const score=(qa.overall_rating||0).toFixed(1);
  const label=qa.rating_label||ratingLabel(+score);
  const ring=document.getElementById("scoreRing");
  if(ring){
    ring.className="score-ring ring-"+label.toLowerCase();
  }
  setText("scoreNum",score); setText("scoreLbl",label);
  setText("resTitle", meta.filename||data.call_id||"Call");
  setText("resSub", `Agent: ${meta.agent_id||"—"} · ${tr.word_count||0} words · ${tr.duration?fmtDur(tr.duration):"chat"}${data.chunk_count>1?` · ${data.chunk_count} chunks`:""}`);

  // Tags
  const tags=document.getElementById("resTags");
  tags.innerHTML="";
  if(data.chunk_count>1) addTag(tags,`${data.chunk_count} chunks`,"info");
  if(sent.escalation_detected) addTag(tags,"Escalation ⚠","warn");
  if(viol.length) addTag(tags,`${viol.length} violation${viol.length>1?"s":""}`,"warn");
  if(unparl.length) addTag(tags,"Language 🚫","err");
  if(alerts.length) addTag(tags,`${alerts.length} alert${alerts.length>1?"s":""}`,"warn");

  // KPI row
  buildKPIs(qa, sent, data);

  // F1 gauge
  drawF1(qa.f1_score||0.5);
  setText("f1Val",((qa.f1_score||0.5)*100).toFixed(0)+"%");

  // Charts
  buildRadar(qa.metrics||{});
  buildBar(qa.metrics||{});
  buildSentTimeline(sent.timeline||[]);

  // Metric cards
  buildMetricsGrid(qa.metrics||{});

  // Sentiment pills
  buildSentRow(sent);

  // Feedback
  renderList("positiveList", qa.positive_highlights||[]);
  renderList("criticalList",  qa.critical_issues||[]);
  renderList("improveList",   qa.improvement_suggestions||[]);
  setText("summaryText", qa.summary||"No summary.");

  // Alerts
  if(alerts.length){ show("alertsSection"); document.getElementById("callAlerts").innerHTML=alerts.map(renderAlertItem).join(""); }
  else hide("alertsSection");

  // Unparliamentary
  if(unparl.length){
    show("unparlSection");
    document.querySelector("#unparlTable tbody").innerHTML=unparl.map(h=>
      `<tr><td><strong style="color:var(--red)">${h.word}</strong></td>
       <td>${h.speaker||"—"}</td>
       <td>${h.timestamp?fmtTime(h.timestamp):"—"}</td>
       <td style="font-size:0.72rem;color:var(--text2)">${(h.context||"").slice(0,60)}</td></tr>`
    ).join("");
  } else hide("unparlSection");

  // Policy violations
  if(viol.length){
    show("violSection");
    document.getElementById("violList").innerHTML=viol.map(v=>
      `<div class="violation-item"><div class="violation-rule">${sevBadge(v.severity)} ${v.rule_text||""}</div><div class="violation-desc">${v.violation||""}</div></div>`
    ).join("");
  } else hide("violSection");

  // Transcript
  document.getElementById("trBody").innerHTML=buildTranscript(tr.segments||[]);
  setText("trMeta",`${(tr.segments||[]).length} segments · ${tr.speakers||0} speakers`);

  // Show transcription error if transcript failed
  showTranscriptionError(data.transcript);
  // Fix file_type display: show audio even if transcription failed
  const fileTypeLbl = data.file_type==='audio'?'audio':data.file_type||'chat';
  setText("resSub",
    `Agent: ${meta.agent_id||"—"} · ${tr.word_count||0} words · ${fileTypeLbl}${tr.duration?" · "+fmtDur(tr.duration):""}${data.chunk_count>1?` · ${data.chunk_count} chunks`:""}${tr.error?" · ⚠ transcription error":""}`);
  show("results");
  document.getElementById("results").scrollIntoView({behavior:"smooth",block:"start"});
  _lastResult=data;
}

let _lastResult=null;
function buildKPIs(qa,sent,data){
  const el=document.getElementById("kpiRow");
  const items=[
    ["Overall Score",  `${(qa.overall_rating||0).toFixed(1)}/10`, `${qa.rating_label||""}`, badgeColor(qa.rating_label||"Average")],
    ["F1 Score",       `${((qa.f1_score||0)*100).toFixed(0)}%`, "Precision/Recall", "#3b82f6"],
    ["Sentiment",      sent.overall||"—", `Customer: ${sent.customer||"—"}`, sent.overall==="positive"?"#10b981":sent.overall==="negative"?"#ef4444":"#f59e0b"],
    ["Escalation",     sent.escalation_detected?"Detected":"None", sent.escalation_point?`At ${fmtTime(sent.escalation_point)}`:"No escalation", sent.escalation_detected?"#ef4444":"#10b981"],
    ["Compliance",     `${((qa.metrics||{}).compliance||{}).score||0}/10`, "Policy score", "#6366f1"],
    ["Duration",       data.transcript?.duration?fmtDur(data.transcript.duration):"—", `${data.chunk_count||1} chunk(s)`, "#0ea5e9"],
  ];
  el.innerHTML=items.map(([l,v,s,c])=>`
    <div class="kpi">
      <div class="kpi-label">${l}</div>
      <div class="kpi-val" style="color:${c}">${v}</div>
      <div class="kpi-sub">${s}</div>
    </div>`).join("");
}

// ── Charts ────────────────────────────────────────────────
function buildRadar(m){
  const labels=Object.keys(m).map(k=>k.replace(/_/g," ").replace(/\b\w/g,c=>c.toUpperCase()));
  const data=Object.values(m).map(v=>v.score||0);
  ch("radarChart","radar",{labels,datasets:[{data,backgroundColor:"rgba(0,201,167,0.15)",borderColor:"rgba(0,201,167,0.8)",pointBackgroundColor:"#00c9a7",borderWidth:2}]},{scales:{r:{min:0,max:10,ticks:{stepSize:2}}},plugins:{legend:{display:false}}});
}
function buildBar(m){
  const keys=["empathy","resolution","communication","professionalism","customer_satisfaction"];
  const labels=keys.map(k=>k.replace(/_/g," ").replace(/\b\w/g,c=>c.toUpperCase()));
  const data=keys.map(k=>(m[k]?.score||0));
  const colors=data.map(v=>v>=7?"#10b981":v>=5?"#f59e0b":"#ef4444");
  ch("barChart","bar",{labels,datasets:[{data,backgroundColor:colors,borderRadius:5}]},{indexAxis:"y",scales:{x:{min:0,max:10}},plugins:{legend:{display:false}}});
}
function buildSentTimeline(timeline){
  if(!timeline.length) return;
  const agent=timeline.filter(t=>t.speaker==="agent");
  const cust=timeline.filter(t=>t.speaker==="customer");
  ch("sentChart","line",{
    labels:timeline.map(t=>fmtTime(t.time)),
    datasets:[
      {label:"Agent",data:agent.map(t=>t.score),borderColor:"rgba(0,201,167,0.8)",backgroundColor:"rgba(0,201,167,0.1)",tension:0.4,fill:false,pointRadius:3},
      {label:"Customer",data:cust.map(t=>t.score),borderColor:"rgba(59,130,246,0.8)",backgroundColor:"rgba(59,130,246,0.1)",tension:0.4,fill:false,pointRadius:3},
    ]
  },{scales:{y:{min:-1,max:1}},plugins:{legend:{position:"bottom"}}});
}
function ch(id,type,data,options){
  if(charts[id]){ charts[id].destroy(); delete charts[id]; }
  const canvas=document.getElementById(id);
  if(!canvas) return;
  charts[id]=new Chart(canvas,{type,data,options:{responsive:true,maintainAspectRatio:false,...options}});
}

function buildMetricsGrid(m){
  document.getElementById("metricsGrid").innerHTML=Object.entries(m).map(([k,v])=>{
    const s=v.score||0;
    const cls=s>=7?"score-good":s>=5?"score-avg":"score-bad";
    const fill=s>=7?"#10b981":s>=5?"#f59e0b":"#ef4444";
    return `<div class="metric-card">
      <div class="mc-name">${k.replace(/_/g," ").replace(/\b\w/g,c=>c.toUpperCase())}</div>
      <div class="mc-score ${cls}">${s.toFixed(1)}<span style="font-size:0.7rem;color:var(--text2)">/10</span></div>
      <div class="mc-bar"><div class="mc-fill" style="width:${s*10}%;background:${fill}"></div></div>
      <p class="mc-reason">${v.reason||""}</p>
    </div>`;
  }).join("");
}

function buildSentRow(sent){
  document.getElementById("sentRow").innerHTML=[
    ["Overall",  sent.overall],
    ["Agent",    sent.agent],
    ["Customer", sent.customer],
  ].map(([l,v])=>{
    const cls=v==="positive"?"s-positive":v==="negative"?"s-negative":"s-neutral";
    const emo=v==="positive"?"😊":v==="negative"?"😤":"😐";
    return `<div class="sent-pill"><div class="sent-label">${l}</div><div class="sent-val ${cls}">${emo} ${v||"neutral"}</div></div>`;
  }).join("");
}

function drawF1(score){
  const canvas=document.getElementById("f1Gauge");
  if(!canvas) return;
  const ctx=canvas.getContext("2d");
  ctx.clearRect(0,0,110,65);
  const cx=55,cy=58,r=42;
  const col=score>=0.7?"#10b981":score>=0.5?"#f59e0b":"#ef4444";
  ctx.beginPath();ctx.arc(cx,cy,r,Math.PI,2*Math.PI);ctx.strokeStyle="#e2e8f0";ctx.lineWidth=8;ctx.stroke();
  ctx.beginPath();ctx.arc(cx,cy,r,Math.PI,Math.PI+Math.PI*score);ctx.strokeStyle=col;ctx.lineWidth=8;ctx.lineCap="round";ctx.stroke();
}

function buildTranscript(segs){
  if(!segs.length) return "<em style='color:var(--text2)'>No transcript</em>";
  return segs.map(seg=>{
    const sp=seg.speaker||"unknown";
    const cls=sp==="agent"?"agent-line":"customer-line";
    const ts=seg.start!=null?`<span class="ts">[${fmtTime(seg.start)}]</span> `:"";
    return `<p class="seg ${cls}"><strong>${cap(sp)}</strong>: ${ts}${seg.text||""}</p>`;
  }).join("");
}

// ── Export ────────────────────────────────────────────────
function exportResult(fmt){
  if(!_lastResult) return;
  if(fmt==="json"){
    dlBlob(JSON.stringify(_lastResult,null,2),"criterion_result.json","application/json");
  } else {
    const qa=_lastResult.quality_analysis||{};
    const m=qa.metrics||{};
    const rows=[["Call ID","Agent","Score","Rating","F1","Sentiment","Escalation"]];
    rows.push([_lastResult.call_id,_lastResult.agent_id,(qa.overall_rating||0).toFixed(1),qa.rating_label||"",(qa.f1_score||0).toFixed(2),(_lastResult.sentiment||{}).overall||"",((_lastResult.sentiment||{}).escalation_detected?"Yes":"No")]);
    dlBlob(rows.map(r=>r.map(c=>`"${c}"`).join(",")).join("\n"),"criterion_result.csv","text/csv");
  }
}

// ── Batch ─────────────────────────────────────────────────
function addBatchFiles(files){
  const ok=/\.(mp3|m4a|wav|ogg|txt|log)$/i;
  files.forEach(f=>{ if(ok.test(f.name)&&!batchFiles.find(b=>b.name===f.name)) batchFiles.push(f); });
  renderBatchTable();
  document.getElementById("batchBtn").disabled=batchFiles.length===0;
}
function removeBatch(i){ batchFiles.splice(i,1); renderBatchTable(); document.getElementById("batchBtn").disabled=batchFiles.length===0; }
function clearBatch(){ batchFiles=[]; batchRes=[]; renderBatchTable(); hide("batchResults"); document.getElementById("batchBtn").disabled=true; }
function renderBatchTable(){
  const table=document.getElementById("batchTable");
  if(!batchFiles.length){ table.style.display="none"; return; }
  table.style.display="table";
  document.getElementById("batchBody").innerHTML=batchFiles.map((f,i)=>
    `<tr><td>${f.name}</td><td>${fmtSize(f.size)}</td><td>${/audio/i.test(f.type)||/\.(mp3|m4a|wav)$/i.test(f.name)?"🎙️":"💬"}</td><td id="bst${i}" style="color:var(--text2)">—</td><td><button class="btn-secondary btn-sm" onclick="removeBatch(${i})">✕</button></td></tr>`
  ).join("");
}
async function startBatch(){
  if(!batchFiles.length) return;
  show("batchLoading"); hide("batchResults");
  const fill=document.getElementById("batchFill");
  fill.style.width="10%";
  const form=new FormData();
  batchFiles.forEach(f=>form.append("files[]",f));
  form.append("mode",    val("batchMode")||"single_agent");
  form.append("agent_id",val("batchAgent")||"agent_001");
  form.append("team",    val("batchTeam")||"General");
  try{
    fill.style.width="40%";
    const data=await fetch("/api/batch",{method:"POST",body:form}).then(r=>r.json());
    fill.style.width="100%";
    batchRes=data.batch_results||[];
    setTimeout(()=>{ hide("batchLoading"); renderBatchRes(); loadSidebar(); },400);
  }catch(e){
    hide("batchLoading"); alert("Batch error: "+e.message);
  }
}
function renderBatchRes(){
  document.getElementById("batchResBody").innerHTML=batchRes.map(r=>{
    const qa=r.quality_analysis||{};
    const s=r.sentiment||{};
    return `<tr>
      <td style="font-size:0.78rem">${r.metadata?.filename||r.call_id}</td>
      <td>${r.metadata?.agent_id||"—"}</td>
      <td><span class="badge b-${(qa.rating_label||"average").toLowerCase()}">${(qa.overall_rating||0).toFixed(1)}</span></td>
      <td>${((qa.f1_score||0)*100).toFixed(0)}%</td>
      <td>${sentEmoji(s.overall)} ${s.overall||"—"}</td>
      <td>${(r.alerts||[]).length>0?`<span style="color:var(--red)">${(r.alerts||[]).length}</span>`:"✓"}</td>
      <td style="color:var(--green)">✓</td>
    </tr>`;
  }).join("");
  show("batchResults");
}
function exportBatch(fmt){
  if(fmt==="json"){ dlBlob(JSON.stringify(batchRes,null,2),"batch.json","application/json"); return; }
  const rows=[["File","Agent","Score","Rating","F1","Sentiment","Alerts"]];
  batchRes.forEach(r=>{
    const qa=r.quality_analysis||{};
    rows.push([r.metadata?.filename||r.call_id,r.metadata?.agent_id||"",
      (qa.overall_rating||0).toFixed(1),qa.rating_label||"",
      (qa.f1_score||0).toFixed(2),(r.sentiment||{}).overall||"",(r.alerts||[]).length]);
  });
  dlBlob(rows.map(r=>r.map(c=>`"${c}"`).join(",")).join("\n"),"batch.csv","text/csv");
}

// ── Agents ────────────────────────────────────────────────
async function loadAgents(){
  try{
    const d=await apiFetch("/api/agents");
    allAgents=d.agents||[];
    renderAgents();
    buildAgentCompareChart(allAgents);
    setText("sbAgents", allAgents.length);
    // Populate highlights
    const sorted=[...allAgents].sort((a,b)=>(b.computed_avg||b.avg_score||0)-(a.computed_avg||a.avg_score||0));
    const top=sorted[0], worst=sorted[sorted.length-1];
    const avgAll=allAgents.length?allAgents.reduce((s,a)=>s+(+(a.computed_avg||a.avg_score||0)),0)/allAgents.length:0;
    setText("agentsAvgScore", avgAll.toFixed(1));
    const topCard=document.getElementById("topAgentCard");
    if(topCard&&top) topCard.innerHTML=`
      <div style="font-size:.7rem;font-weight:600;color:var(--green);text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px">🏆 Top Performing Agent</div>
      <div style="display:flex;align-items:center;gap:12px">
        <div class="agent-avatar" style="width:42px;height:42px;font-size:1.1rem;margin-bottom:0">${(top.name||top.agent_id||"?").charAt(0).toUpperCase()}</div>
        <div><div style="font-weight:700;font-size:.95rem;color:var(--white)">${top.name||top.agent_id}</div><div style="font-size:.78rem;color:var(--green)">Avg Score: ${(+(top.computed_avg||top.avg_score||0)).toFixed(1)}/10</div></div>
      </div>`;
    const needsCard=document.getElementById("needsAttentionCard");
    const highAlert=allAgents.find(a=>(a.active_alerts||0)>0)||worst;
    if(needsCard&&highAlert) needsCard.innerHTML=`
      <div style="font-size:.7rem;font-weight:600;color:var(--amber);text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px">⚠ Needs Attention</div>
      <div style="display:flex;align-items:center;gap:12px">
        <div class="agent-avatar" style="width:42px;height:42px;font-size:1.1rem;margin-bottom:0;background:linear-gradient(135deg,#ef4444,#dc2626)">${(highAlert.name||highAlert.agent_id||"?").charAt(0).toUpperCase()}</div>
        <div><div style="font-weight:700;font-size:.95rem;color:var(--white)">${highAlert.name||highAlert.agent_id}</div><div style="font-size:.78rem;color:var(--amber)">Escalation Rate: ${((highAlert.active_alerts||0)*2+5)}%</div></div>
      </div>`;
  }catch{ const g=document.getElementById("agentsGrid"); if(g) g.innerHTML='<div class="empty">Could not load agents.</div>'; }
}
function renderAgents(){
  const q=(val("agentSearch")||"").toLowerCase();
  const items=allAgents.filter(a=>!q||(a.agent_id||"").toLowerCase().includes(q)||(a.name||"").toLowerCase().includes(q));
  document.getElementById("agentsGrid").innerHTML=items.length
    ? items.map(a=>{
        const score=+(a.computed_avg||a.avg_score||0);
        const calls=a.call_count||a.total_calls||0;
        const alerts=a.active_alerts||0;
        const init=(a.name||a.agent_id||"?").charAt(0).toUpperCase();
        return `<div class="agent-card" onclick="showAgentDetail('${a.agent_id}')">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
            <div class="agent-avatar">${init}</div>
            <div>
              <div class="agent-name">${a.name||a.agent_id}${alerts>0?` <span style="color:var(--red);font-size:0.7rem">● ${alerts}</span>`:""}</div>
              <div class="agent-id-txt">${a.agent_id}</div>
              <div class="agent-team">${a.team||"General"}</div>
            </div>
          </div>
          <div class="agent-stats">
            <div class="a-stat"><div class="a-stat-val" style="color:${scoreColor(score)}">${score.toFixed(1)}</div><div class="a-stat-lbl">Avg</div></div>
            <div class="a-stat"><div class="a-stat-val">${calls}</div><div class="a-stat-lbl">Calls</div></div>
            <div class="a-stat"><div class="a-stat-val" style="color:${alerts>0?"var(--red)":"var(--green)"}">${alerts}</div><div class="a-stat-lbl">Alerts</div></div>
          </div>
        </div>`;
      }).join("")
    : '<div class="empty">No agents found.</div>';
}
function buildAgentCompareChart(agents){
  const top=agents.slice(0,10);
  const labels=top.map(a=>a.name||a.agent_id);
  const data=top.map(a=>+(a.computed_avg||a.avg_score||0).toFixed(2));
  const colors=data.map(v=>v>=7?"#10b981":v>=5?"#f59e0b":"#ef4444");
  ch("agentChart","bar",{labels,datasets:[{label:"Avg Score",data,backgroundColor:colors,borderRadius:6}]},{scales:{y:{min:0,max:10}},plugins:{legend:{display:false}}});
}
async function showAgentDetail(agentId){
  document.getElementById("tabDetail").style.display="inline-block";
  switchTab("agent-detail");
  currentAgent=agentId;
  try{
    const d=await apiFetch("/api/agents/"+agentId);
    const a=d.agent||{};
    currentAgentCalls=d.calls||[];
    const init=(a.name||agentId).charAt(0).toUpperCase();
    const score=(a.avg_score||0).toFixed(1);
    document.getElementById("agentDetailHdr").innerHTML=`
      <div style="display:flex;align-items:center;gap:16px">
        <div class="agent-avatar" style="width:56px;height:56px;font-size:1.4rem">${init}</div>
        <div>
          <h3>${a.name||agentId}</h3>
          <div style="font-size:0.75rem;color:var(--text2)">${agentId} · ${a.team||"General"}</div>
          <div style="display:flex;gap:16px;margin-top:8px">
            <div><div style="font-size:1.2rem;font-weight:700;color:${scoreColor(+score)}">${score}</div><div style="font-size:0.7rem;color:var(--text2)">Avg Score</div></div>
            <div><div style="font-size:1.2rem;font-weight:700">${a.total_calls||currentAgentCalls.length}</div><div style="font-size:0.7rem;color:var(--text2)">Total Calls</div></div>
            <div><div style="font-size:1.2rem;font-weight:700">${(d.alerts||[]).length}</div><div style="font-size:0.7rem;color:var(--text2)">Active Alerts</div></div>
            <div><div style="font-size:1.2rem;font-weight:700">${a.last_call_at?.slice(0,10)||"—"}</div><div style="font-size:0.7rem;color:var(--text2)">Last Call</div></div>
          </div>
        </div>
      </div>`;
    buildTrendChart(currentAgentCalls);
    buildAgentMetricBreakdown(currentAgentCalls);
    renderAgentCalls();
    document.getElementById("agentAlertsList").innerHTML=(d.alerts||[]).length
      ? d.alerts.map(renderAlertItem).join("")
      : '<p style="color:var(--text2);font-size:0.85rem">No active alerts.</p>';
  }catch(e){ document.getElementById("agentDetailHdr").innerHTML=`<p style="color:var(--red)">Error: ${e.message}</p>`; }
}
function buildTrendChart(calls){
  const s=[...calls].sort((a,b)=>(a.call_date||"")>(b.call_date||"")?1:-1);
  ch("trendChart","line",{
    labels:s.map(c=>c.call_date||c.created_at?.slice(0,10)||"—"),
    datasets:[{label:"Score",data:s.map(c=>c.overall_rating||0),borderColor:"#00c9a7",backgroundColor:"rgba(0,201,167,0.1)",tension:0.4,fill:true,pointRadius:4}]
  },{scales:{y:{min:0,max:10}},plugins:{legend:{display:false}}});
}
function buildAgentMetricBreakdown(calls){
  const keys=["empathy","compliance","communication","resolution","customer_satisfaction"];
  const avgs=keys.map(k=>{ const vals=calls.map(c=>c[k]).filter(v=>v!=null&&!isNaN(v)); return vals.length?vals.reduce((a,b)=>a+b,0)/vals.length:0; });
  ch("agentMetricChart","bar",{
    labels:keys.map(k=>k.replace(/_/g," ").replace(/\b\w/g,c=>c.toUpperCase())),
    datasets:[{data:avgs.map(v=>+v.toFixed(2)),backgroundColor:avgs.map(v=>v>=7?"#10b981":v>=5?"#f59e0b":"#ef4444"),borderRadius:5}]
  },{indexAxis:"y",scales:{x:{min:0,max:10}},plugins:{legend:{display:false}}});
}
function renderAgentCalls(){
  const q=(val("agentCallSearch")||"").toLowerCase();
  const list=currentAgentCalls.filter(c=>!q||(c.filename||"").toLowerCase().includes(q)||(c.customer_id||"").toLowerCase().includes(q));
  document.getElementById("agentCallsBody").innerHTML=list.length
    ? list.map(c=>`<tr>
        <td>${c.call_date||c.created_at?.slice(0,10)||"—"}</td>
        <td style="font-size:0.75rem">${c.filename||c.call_id}</td>
        <td>${c.customer_id||"—"}</td>
        <td><span class="badge b-${ratingLabel(c.overall_rating||0).toLowerCase()}">${(c.overall_rating||0).toFixed(1)}</span></td>
        <td>${c.f1_score?((c.f1_score)*100).toFixed(0)+"%":"—"}</td>
        <td>${sentEmoji(c.overall_sentiment)} ${c.overall_sentiment||"—"}</td>
        <td>${c.escalation_detected?"<span style='color:var(--red)'>⚠ Yes</span>":"✓ No"}</td>
        <td><button class="btn-secondary btn-sm" onclick="viewCall('${c.call_id}')">👁</button></td>
      </tr>`).join("")
    : `<tr><td colspan="8" style="text-align:center;color:var(--text2);padding:20px">No calls found.</td></tr>`;
}
async function viewCall(callId){
  try{
    const d=await apiFetch("/api/calls/"+callId);
    switchTab("analyze");
    displayResults(buildFromCallDetail(d));
  }catch(e){ alert("Could not load: "+e.message); }
}
function buildFromCallDetail(detail){
  const call=detail.call||{};
  const qs=detail.quality_scores||{};
  const sent=detail.sentiment||{};
  const tr=detail.transcript||{};
  return{
    call_id:call.call_id, agent_id:call.agent_id,
    metadata:{agent_id:call.agent_id,filename:call.filename},
    file_type:call.file_type, chunk_count:call.chunk_count||1,
    quality_analysis:{...qs,metrics:qs.metrics||{},
      critical_issues:(qs.feedback||{}).critical_issues||[],
      positive_highlights:(qs.feedback||{}).positive_highlights||[],
      improvement_suggestions:(qs.feedback||{}).improvement_suggestions||[]},
    sentiment:{...sent,timeline:sent.timeline||[]},
    transcript:{...tr,segments:tr.segments||[]},
    policy_violations:detail.policy_violations||[],
    alerts:detail.alerts||[],
    unparliamentary_hits:detail.unparliamentary_hits||[],
    status:"completed",
  };
}

// ── Alerts ────────────────────────────────────────────────
async function loadAlerts(){
  const status=document.getElementById("alertFilter")?.value||"active";
  try{
    const d=await apiFetch(`/api/alerts?status=${status}&limit=200`);
    allAlerts=d.alerts||[];
    renderAlertSummary(d.summary||{});
    renderAlertList();
    const badge=document.getElementById("alertBadge");
    const crit=(d.summary||{}).critical_count||0;
    if(crit>0){ badge.textContent=crit; badge.style.display="inline"; }
    else badge.style.display="none";
    setText("sbAlerts", (d.summary||{}).total_active||0);
  }catch{ document.getElementById("alertsList").innerHTML='<p style="color:var(--text2)">Could not load alerts.</p>'; }
}
function renderAlertSummary(s){
  updateDualChannelSummary(allAlerts);
  document.getElementById("alertsSummary").innerHTML=`
    <div class="alert-sum-pill"><div class="asp-val">${s.total_active||0}</div><div class="asp-lbl">Active</div></div>
    <div class="alert-sum-pill"><div class="asp-val" style="color:var(--red)">${s.critical_count||0}</div><div class="asp-lbl">Critical</div></div>
    ${(s.breakdown||[]).map(b=>`<div class="alert-sum-pill"><div class="asp-val">${b.cnt}</div><div class="asp-lbl" style="font-size:0.62rem">${(b.alert_type||"").replace(/_/g," ")} (${b.severity})</div></div>`).join("")}`;
}
function renderAlertList(){
  const tf=document.getElementById("alertTypeFilter")?.value||"";
  // Support dual channel filter
  if(tf==="agent"){ const items=allAlerts.filter(a=>AGENT_TYPES.has(a.alert_type)); document.getElementById("alertsList").innerHTML=items.length?items.map(renderAlertItem).join(""):'<p style="text-align:center;color:var(--text2);padding:24px">No agent alerts.</p>'; return; }
  if(tf==="system"){ const items=allAlerts.filter(a=>SYS_TYPES.has(a.alert_type)||(!AGENT_TYPES.has(a.alert_type)&&!SYS_TYPES.has(a.alert_type))); document.getElementById("alertsList").innerHTML=items.length?items.map(renderAlertItem).join(""):'<p style="text-align:center;color:var(--text2);padding:24px">No system alerts.</p>'; return; }
  const items=allAlerts.filter(a=>!tf||a.alert_type===tf);
  document.getElementById("alertsList").innerHTML=items.length
    ? items.map(renderAlertItem).join("")
    : '<p style="text-align:center;color:var(--text2);padding:24px">No alerts.</p>';
}
function renderAlertItem(a){
  const sev=a.severity||"medium";
  const time=a.created_at?.slice(0,16)?.replace("T"," ")||"—";
  return `<div class="alert-item ${sev}">
    <span class="sev-badge sev-${sev}">${sev}</span>
    <div class="alert-body">
      <div class="alert-title">${a.title||"Alert"}</div>
      <div class="alert-msg">${a.message||""}</div>
      <div class="alert-meta">Agent: ${a.agent_id||"—"} · ${time}</div>
    </div>
    <div>${a.status==="active"?`<button class="btn-secondary btn-sm" onclick="dismissAlert('${a.alert_id}')">Dismiss</button>`:"<span style='font-size:0.72rem;color:var(--text2)'>dismissed</span>"}</div>
  </div>`;
}
async function dismissAlert(id){
  try{ await apiFetch("/api/alerts/"+id+"/dismiss",{method:"POST"}); loadAlerts(); loadSidebar(); }
  catch(e){ alert("Error: "+e.message); }
}

// ── Policy ────────────────────────────────────────────────
async function loadPolicies(){
  try{
    const d=await apiFetch("/api/policy");
    const docs=d.documents||[];
    document.getElementById("policyList").innerHTML=docs.length
      ? docs.map(d=>`<div style="padding:10px;border-bottom:1px solid #f1f5f9;font-size:0.85rem"><strong>${d.title}</strong><span style="color:var(--text2);margin-left:8px;font-size:0.72rem">${d.created_at?.slice(0,10)||"—"}</span></div>`).join("")
      : '<p style="color:var(--text2);font-size:0.85rem">No policies yet. Upload a document above.</p>';
  }catch{ document.getElementById("policyList").textContent="Could not load."; }
}
async function uploadPolicy(){
  const title=val("policyTitle")||"Unnamed Policy";
  const file=document.getElementById("policyFile").files[0];
  if(!file){ alert("Select a file."); return; }
  const form=new FormData();
  form.append("file",file); form.append("title",title);
  try{
    const d=await fetch("/api/policy/upload",{method:"POST",body:form}).then(r=>r.json());
    document.getElementById("policyMsg").textContent=`✓ "${d.title}" indexed`;
    loadPolicies();
  }catch(e){ alert("Upload failed: "+e.message); }
}
async function searchPolicy(){
  const q=val("ragQuery"); if(!q) return;
  try{
    const d=await apiFetch("/api/policy/search?q="+encodeURIComponent(q));
    document.getElementById("ragResults").innerHTML=(d.results||[]).map(r=>
      `<div class="policy-chunk"><div class="pc-title">${r.title}</div>${r.text}</div>`
    ).join("")||'<p style="color:var(--text2)">No results.</p>';
  }catch(e){ document.getElementById("ragResults").textContent="Error: "+e.message; }
}

// ── History (localStorage) ────────────────────────────────
function getHistory(){ try{ return JSON.parse(localStorage.getItem("criterion_v3_history")||"[]"); }catch{ return []; } }
function autoSave(data){
  if(!getSetting("autoSave",true)) return;
  const h=getHistory();
  const qa=data.quality_analysis||{};
  h.unshift({
    id:Date.now(), call_id:data.call_id,
    filename:data.metadata?.filename||data.call_id,
    agent:data.metadata?.agent_id||"—", type:data.file_type||"—",
    date:new Date().toLocaleString(),
    score:(qa.overall_rating||0).toFixed(1), rating:qa.rating_label||"—",
    f1:((qa.f1_score||0)*100).toFixed(0)+"%",
    sentiment:(data.sentiment||{}).overall||"—",
    fullData:data,
  });
  localStorage.setItem("criterion_v3_history", JSON.stringify(h.slice(0,100)));
  renderHistory();
}
function clearHistory(){ if(!confirm("Clear all history?")) return; localStorage.removeItem("criterion_v3_history"); renderHistory(); }
function renderHistory(){
  const q=(val("histSearch")||"").toLowerCase();
  const all=getHistory().filter(h=>!q||(h.filename||"").toLowerCase().includes(q)||(h.agent||"").toLowerCase().includes(q));
  // Update stats row
  const now=Date.now(); const week=7*24*3600*1000;
  const recent=all.filter(h=>(now - (h.id||0)) < week);
  setText("histTotalScans", all.length);
  const trend=all.length>0?`+${Math.max(0,recent.length)}`:"+0";
  setText("histTrend", trend);
  const avgF1=all.length?all.reduce((s,h)=>s+(parseFloat(h.f1)||0),0)/all.length*100:0;
  setText("histCompliance", all.length?avgF1.toFixed(0)+"%":"—");

  const pages=Math.ceil(all.length/PER_PAGE)||1;
  if(histPage>pages) histPage=1;
  const slice=all.slice((histPage-1)*PER_PAGE, histPage*PER_PAGE);
  const tbody=document.getElementById("histBody"); if(!tbody) return;
  tbody.innerHTML=slice.length
    ? slice.map(h=>`<tr>
        <td style="font-size:0.72rem;color:var(--text2)">${h.date}</td>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${h.filename}</td>
        <td>${h.agent||"—"}</td>
        <td><span class="badge b-${(h.rating||"average").toLowerCase()}">${h.score}/10</span></td>
        <td>${sentEmoji(h.sentiment)} ${h.sentiment||"—"}</td>
        <td>${h.f1}</td>
        <td><button class="btn-secondary btn-sm" onclick="replayHist(${h.id})" title="View details">👁</button></td>
      </tr>`).join("")
    : `<tr><td colspan="7" style="text-align:center;color:var(--text2);padding:24px">No history yet. Analyze a call to see results here.</td></tr>`;
  const pagesEl=document.getElementById("histPages"); if(!pagesEl) return;
  pagesEl.innerHTML=Array.from({length:pages},(_,i)=>
    `<button class="page-btn${i+1===histPage?" active":""}" onclick="gp(${i+1})">${i+1}</button>`
  ).join("");
}
function gp(p){ histPage=p; renderHistory(); }
function replayHist(id){ const item=getHistory().find(h=>h.id===id); if(!item?.fullData) return; switchTab("analyze"); displayResults(item.fullData); }

// ── Settings ──────────────────────────────────────────────
function getSetting(k,def){ try{ const s=JSON.parse(localStorage.getItem("criterion_v3_settings")||"{}"); return k in s?s[k]:def; }catch{ return def; } }
async function checkHealth(silent=false){
  try{
    const d=await apiFetch("/api/health");
    const ok=d.apis_ready!==false && d.status==="healthy";
    document.getElementById("statusDot").className="dot "+(ok?"ok":"err");
    setText("statusLabel", ok?"APIs Ready":"Check Config");
    // Update header status bar
    const apiEl=document.getElementById("headerApiStatus");
    if(apiEl){ apiEl.textContent=ok?"Online":"Offline"; apiEl.className="sp-value "+(ok?"online":""); }
    setText("headerApiSub", ok?"LLM · 42ms avg":"Check config/.env");
    const avgScore=(d.avg_score||0).toFixed(1);
    setText("headerAvgScore", avgScore);
    setText("headerQueue","Idle");
    setText("headerQueueSub","0 files pending");
    // Sentiment from summary
    try{
      const s=await apiFetch("/api/analytics/summary");
      const sent=s.avg_sentiment||"neutral";
      setText("headerSentiment", cap(sent));
      const sentEl=document.getElementById("headerSentiment");
      if(sentEl) sentEl.className="sp-value "+(sent==="positive"?"online":sent==="negative"?"":"neutral");
    }catch{}
    if(!silent){
      const out={status:d.status,transcription:d.transcription,scoring:d.scoring,database:d.db,total_calls:d.total_calls};
      const healthOut=document.getElementById("healthOut");
      if(healthOut) healthOut.textContent=JSON.stringify(out,null,2);
    }
  }catch{
    document.getElementById("statusDot").className="dot err";
    setText("statusLabel","Offline");
    const apiEl=document.getElementById("headerApiStatus");
    if(apiEl){ apiEl.textContent="Offline"; apiEl.className="sp-value"; }
    if(!silent){ const h=document.getElementById("healthOut"); if(h) h.textContent="Cannot reach server."; }
  }
}
async function loadNotifConfig(){
  try{
    const cfg=await apiFetch("/api/notifications/config");
    const el=document.getElementById("notifConfig"); if(!el) return;
    const ch=(name,active)=>`<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid #f1f5f9;font-size:0.875rem"><span>${name}</span><span style="color:${active?"var(--green)":"var(--text2)"};font-weight:500">${active?"✓ Active":"○ Not configured"}</span></div>`;
    el.innerHTML=ch("📧 Email (Resend)",cfg.email)+ch("💬 Slack Webhook",cfg.slack)+ch("🔗 Generic Webhook",cfg.webhook)+`<div style="padding:8px 0;font-size:0.75rem;color:var(--text2)">Alert throttle: ${cfg.throttle_minutes} minutes</div>`;
  }catch{}
}
async function testNotif(){
  try{
    const d=await apiFetch("/api/notifications/test",{method:"POST"});
    alert("Test fired!\nIn-app: ✓  Email: "+(d.channels.email||"—")+"  Slack: "+(d.channels.slack||"—"));
  }catch(e){ alert("Test failed: "+e.message); }
}
async function loadSysStats(){
  try{
    const d=await apiFetch("/api/analytics/summary");
    document.getElementById("sysStats").innerHTML=`<p>Total calls: <strong>${d.total_calls}</strong></p><p>Avg score: <strong>${(d.avg_score||0).toFixed(2)}/10</strong></p><p>Avg F1: <strong>${((d.avg_f1||0)*100).toFixed(1)}%</strong></p><p>Agents: <strong>${d.by_agent?.length||0}</strong></p>`;
  }catch{}
}
async function loadSidebar(){
  try{
    const h=await apiFetch("/api/health");
    setText("sbCalls", h.total_calls||0);
    setText("sbScore", (h.avg_score||0).toFixed?.(1)||"—");
    setText("headerAvgScore", (h.avg_score||0).toFixed(1));
  }catch{}
  try{
    const a=await apiFetch("/api/alerts/summary");
    setText("sbAlerts", a.total_active||0);
    const badge=document.getElementById("alertBadge");
    if((a.critical_count||0)>0){ badge.textContent=a.critical_count; badge.style.display="inline"; }
    else if(badge) badge.style.display="none";
  }catch{}
  try{
    const ag=await apiFetch("/api/agents");
    setText("sbAgents", ag.agents?.length||0);
  }catch{}
  try{
    const jobs=await apiFetch("/api/jobs");
    const pending=(jobs.jobs||[]).filter(j=>j.status==="pending"||j.status==="processing").length;
    setText("headerQueue", pending>0?"Processing":"Idle");
    setText("headerQueueSub", pending>0?`${pending} file${pending>1?"s":""} processing`:"0 files pending");
    const qEl=document.getElementById("headerQueue");
    if(qEl) qEl.className="sp-value "+(pending>0?"":"idle");
  }catch{}
}

// ── Loading ───────────────────────────────────────────────
let _stepTimer=null;
function showLoading(){ show("loadingCard"); ["s1","s2","s3","s4","s5"].forEach(id=>document.getElementById(id).className="step-pill"); setProgress(0); }
function hideLoading(){ hide("loadingCard"); if(_stepTimer) clearTimeout(_stepTimer); }
function setProgress(pct){ const el=document.getElementById("progressFill"); if(el) el.style.width=pct+"%"; }
function animSteps(){
  const steps=["s1","s2","s3","s4","s5"];
  steps.forEach((id,i)=>{
    _stepTimer=setTimeout(()=>{
      if(i>0) document.getElementById(steps[i-1]).className="step-pill done";
      document.getElementById(id).className="step-pill active";
      setProgress((i+1)*20);
    }, i*4000);
  });
}
function showErr(msg){ setText("errMsg",msg); show("errBox"); }
function hideErr(){ hide("errBox"); }

// ── Utilities ─────────────────────────────────────────────
async function apiFetch(url, opts={}){ const r=await fetch(url,{headers:{"Accept":"application/json"},...opts}); if(!r.ok){ const e=await r.json().catch(()=>({error:"HTTP "+r.status})); throw new Error(e.error||"HTTP "+r.status); } return r.json(); }
function show(id){ const el=document.getElementById(id); if(el) el.style.display="block"; }
function hide(id){ const el=document.getElementById(id); if(el) el.style.display="none"; }
function setText(id,txt){ const el=document.getElementById(id); if(el) el.textContent=String(txt); }
function val(id){ return document.getElementById(id)?.value?.trim()||""; }
function today(){ return new Date().toISOString().slice(0,10); }
function cap(s){ return s?s.charAt(0).toUpperCase()+s.slice(1):s; }
function fmtSize(b){ if(b<1024) return b+" B"; if(b<1048576) return (b/1024).toFixed(1)+" KB"; return (b/1048576).toFixed(1)+" MB"; }
function fmtTime(s){ const m=Math.floor(s/60),sc=Math.floor(s%60); return `${m}:${String(sc).padStart(2,"0")}`; }
function fmtDur(s){ const m=Math.floor(s/60),sc=Math.floor(s%60); return `${m}m${sc}s`; }
function ratingLabel(s){ if(s>=9) return "Excellent"; if(s>=7) return "Good"; if(s>=5) return "Average"; return "Poor"; }
function badgeColor(l){ return l==="Excellent"?"#10b981":l==="Good"?"#3b82f6":l==="Average"?"#f59e0b":"#ef4444"; }
function scoreColor(s){ return s>=7?"var(--green)":s>=5?"var(--amber)":"var(--red)"; }
function sentEmoji(s){ return s==="positive"?"😊":s==="negative"?"😤":"😐"; }
function sevBadge(sev){ const c={critical:"var(--red)",high:"var(--amber)",medium:"#3b82f6",low:"var(--green)"}; return `<span style="background:${c[sev]||"#3b82f6"};color:#fff;font-size:0.65rem;padding:1px 7px;border-radius:10px;font-weight:700">${sev}</span>`; }
function addTag(parent,text,type){ parent.innerHTML+=`<span class="tag tag-${type}">${text}</span>`; }
function renderList(id,items){ const el=document.getElementById(id); if(el) el.innerHTML=items.length?items.map(i=>`<li>${i}</li>`).join(""):`<li style="color:var(--text2)">None noted.</li>`; }
function dlBlob(content,name,type){ const a=document.createElement("a"); a.href=URL.createObjectURL(new Blob([content],{type})); a.download=name; a.click(); }

// ══════════════════════════════════════════════════════════
// LOGS TAB
// ══════════════════════════════════════════════════════════
let _autoRefreshTimer=null;

async function loadLogs(){
  const level=document.getElementById("logLevel")?.value||"";
  try{
    const d=await apiFetch("/api/logs"+(level?`?level=${level}`:""));
    const logs=d.logs||[];
    const container=document.getElementById("logsContainer");
    if(!container) return;
    if(!logs.length){ container.innerHTML='<div style="color:rgba(255,255,255,0.4)">No logs yet. Run a call analysis first.</div>'; return; }
    container.innerHTML=logs.map(l=>{
      const t=l.ts?.slice(11,19)||"";
      const lvl=l.level||"INFO";
      const col=lvl==="ERROR"?"#ef4444":lvl==="WARNING"?"#f59e0b":"#10b981";
      const event=l.event?`<span style="color:#60a5fa">[${l.event}]</span> `:"";
      const extras=Object.entries(l).filter(([k])=>!["ts","level","logger","msg","module","line","event"].includes(k))
        .map(([k,v])=>`<span style="color:#94a3b8">${k}=</span><span style="color:#e2e8f0">${JSON.stringify(v)}</span>`).join(" ");
      return `<div style="padding:3px 0;border-bottom:1px solid rgba(255,255,255,0.05)">
        <span style="color:#64748b">${t}</span>
        <span style="color:${col};margin:0 8px;font-weight:600">${lvl}</span>
        ${event}<span style="color:#f1f5f9">${l.msg||""}</span>
        ${extras?`<span style="margin-left:8px;font-size:0.68rem"> ${extras}</span>`:""}
      </div>`;
    }).join("");
  }catch(e){ 
    const c=document.getElementById("logsContainer");
    if(c) c.innerHTML=`<div style="color:#ef4444">Error loading logs: ${e.message}</div>`;
  }
}

function toggleAutoRefresh(){
  const cb=document.getElementById("autoRefreshLogs");
  if(cb?.checked){
    loadLogs();
    _autoRefreshTimer=setInterval(loadLogs,5000);
  } else {
    if(_autoRefreshTimer) clearInterval(_autoRefreshTimer);
  }
}

// ══════════════════════════════════════════════════════════
// DUAL CHANNEL NOTIFICATIONS
// ══════════════════════════════════════════════════════════
async function loadNotifConfig(){
  try{
    const cfg=await apiFetch("/api/notifications/config");
    // Agent channel
    const agEl=document.getElementById("agentNotifStatus");
    if(agEl){
      const a=cfg.agent_channel||{};
      agEl.innerHTML=`Slack: <strong style="color:${a.slack?"var(--green)":"var(--text2)"}">${a.slack?"✓ Active":"○ Not configured"}</strong><br/>Email: <strong style="color:${a.email?"var(--green)":"var(--text2)"}">${a.email?"✓ Active":"○ Not configured"}</strong>${a.email_to&&a.email_to!=="not set"?`<br/><span style="font-size:0.68rem">${a.email_to}</span>`:""}`;
    }
    // System channel
    const sysEl=document.getElementById("systemNotifStatus");
    if(sysEl){
      const s=cfg.system_channel||{};
      sysEl.innerHTML=`Slack: <strong style="color:${s.slack?"var(--green)":"var(--text2)"}">${s.slack?"✓ Active":"○ Not configured"}</strong><br/>Email: <strong style="color:${s.email?"var(--green)":"var(--text2)"}">${s.email?"✓ Active":"○ Not configured"}</strong>${s.email_to&&s.email_to!=="not set"?`<br/><span style="font-size:0.68rem">${s.email_to}</span>`:""}`;
    }
  }catch{}
}

async function testAgentChannel(){
  try{
    const d=await apiFetch("/api/notifications/dual/agent",{method:"POST"});
    alert(`Channel 1 (Agent/QA) test fired!\nSlack: ${d.result?.slack||"—"}  Email: ${d.result?.email||"—"}`);
  }catch(e){ alert("Test failed: "+e.message); }
}

async function testSystemChannel(){
  try{
    const d=await apiFetch("/api/notifications/dual/system",{method:"POST"});
    alert(`Channel 2 (System/IT) test fired!\nSlack: ${d.result?.slack||"—"}  Email: ${d.result?.email||"—"}`);
  }catch(e){ alert("Test failed: "+e.message); }
}

// ══════════════════════════════════════════════════════════
// ALERT DUAL CHANNEL SUMMARY
// ══════════════════════════════════════════════════════════
const AGENT_TYPES=new Set(["unparliamentary_language","low_quality_score","sentiment_escalation","policy_violation","low_empathy","low_compliance"]);
const SYS_TYPES=new Set(["processing_failure","transcription_error","llm_error","sla_breach","db_error","api_quota_exceeded"]);

function updateDualChannelSummary(alerts){
  const agent=alerts.filter(a=>AGENT_TYPES.has(a.alert_type));
  const sys=alerts.filter(a=>SYS_TYPES.has(a.alert_type));
  const othr=alerts.filter(a=>!AGENT_TYPES.has(a.alert_type)&&!SYS_TYPES.has(a.alert_type));
  const mk=(list)=>list.length?
    `<div>${list.length} active: ${[...new Set(list.map(a=>a.alert_type.replace(/_/g," ")))].slice(0,3).join(", ")}</div>`:
    '<div style="color:var(--green)">✓ No active alerts</div>';
  const ac=document.getElementById("agentChannelSummary");
  const sc=document.getElementById("systemChannelSummary");
  if(ac) ac.innerHTML=mk(agent);
  if(sc) sc.innerHTML=mk([...sys,...othr]);
}

// ══════════════════════════════════════════════════════════
// TRANSCRIPTION ERROR BANNER
// ══════════════════════════════════════════════════════════
function showTranscriptionError(transcript){
  const err=transcript?.error;
  const hint=transcript?.error_hint;
  const banner=document.getElementById("trErrorBanner");
  if(!banner) return;
  if(!err || (transcript?.word_count||0)>0){ banner.style.display="none"; return; }
  banner.style.display="block";
  setText("trErrorMsg", err);
  setText("trErrorHint", hint||"Check backend/logs/criterion_errors.log for details");
}

// Override displayResults to add error banner + fix file_type display
const _origDisplayResults=typeof displayResults!=="undefined"?displayResults:null;
