// State
let currentThreadId = null, pendingFiles = [], isStreaming = false, chatHistory = [];
let apiKey = localStorage.getItem('pdf_agent_api_key') || '';
const $ = id => document.getElementById(id);
const $messages = $('messages'), $input = $('msgInput'), $threadList = $('threadList');
const $emptyState = $('emptyState'), $fileChips = $('fileChips'), $chatTitle = $('chatTitle');
const $uploadZone = $('uploadZone');

// API
function hdrs(extra={}) { const h={...extra}; if(apiKey) h['X-API-Key']=apiKey; return h; }
async function api(path,opts={}) { opts.headers=hdrs(opts.headers||{}); const r=await fetch(path,opts); if(!r.ok){const b=await r.json().catch(()=>({}));throw new Error(b.detail||`HTTP ${r.status}`);}return r.json();}

// Theme
function toggleTheme() {
  const t = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = t;
  localStorage.setItem('pdf_agent_theme', t);
  $('themeBtn').innerHTML = t==='dark' ? '&#9788;' : '&#9790;';
  $('themeBtn').title = t==='dark' ? 'Light mode' : 'Dark mode';
}
(function(){const t=localStorage.getItem('pdf_agent_theme');if(t){document.documentElement.dataset.theme=t;if(t==='dark')$('themeBtn').innerHTML='&#9788;';}})();

// Threads
async function loadThreads(){try{const d=await api('/api/agent/threads');renderThreadList(d.threads||[]);}catch{}}
function renderThreadList(threads){
  $threadList.innerHTML='';
  threads.forEach(t=>{const el=document.createElement('div');el.className='thread-item'+(t.thread_id===currentThreadId?' active':'');
    const s=t.thread_id.slice(0,8),d=new Date(t.updated_at*1000).toLocaleString();
    el.innerHTML=`<div class="ti-text" onclick="openThread('${t.thread_id}')"><div class="ti-id">${s}...</div><div class="ti-meta">${d}</div></div><button class="ti-del" onclick="event.stopPropagation();deleteThread('${t.thread_id}')" title="Delete">&times;</button>`;
    $threadList.appendChild(el);});
}
async function openThread(tid){
  currentThreadId=tid;$chatTitle.textContent=tid.slice(0,12)+'...';clearMessages();chatHistory=[];
  try{const d=await api(`/api/agent/threads/${tid}`);
    (d.messages||[]).forEach(m=>{
      if(m.type==='human'){addMessage('user',m.content);chatHistory.push({role:'user',content:m.content});}
      else if(m.type==='ai'&&m.content){addMessage('agent',m.content);chatHistory.push({role:'agent',content:m.content});}
      else if(m.type==='tool'&&m.content){addToolCard(m.name||'tool',{},m.content,[],null,true);chatHistory.push({role:'tool',name:m.name,content:m.content});}
    });
  }catch(e){addMessage('agent','Failed to load: '+e.message);}
  loadThreads();
}
async function deleteThread(tid){if(!confirm('Delete this thread?'))return;try{await api(`/api/agent/threads/${tid}`,{method:'DELETE'});if(currentThreadId===tid)newChat();loadThreads();}catch(e){alert(e.message);}}
function newChat(){currentThreadId=null;$chatTitle.textContent='New Chat';pendingFiles=[];renderFileChips();clearMessages();chatHistory=[];$emptyState.style.display='';hidePagePreviewer();loadThreads();}

// Messages
function clearMessages(){$messages.innerHTML='';$emptyState.style.display='';$messages.appendChild($emptyState);}
function addMessage(role,content){
  $emptyState.style.display='none';
  const msg=document.createElement('div');msg.className='msg '+role;msg.dataset.text=content;
  const bubble=document.createElement('div');bubble.className='msg-bubble';bubble.innerHTML=renderMd(content);
  msg.appendChild(bubble);$messages.appendChild(msg);scrollBottom();return bubble;
}
function addStreamingBubble(){
  $emptyState.style.display='none';
  const msg=document.createElement('div');msg.className='msg agent';
  const bubble=document.createElement('div');bubble.className='msg-bubble';bubble.id='streaming-bubble';
  msg.appendChild(bubble);$messages.appendChild(msg);scrollBottom();return bubble;
}
function addToolCard(name,args,output,files,elapsed,done){
  $emptyState.style.display='none';
  const card=document.createElement('div');card.className='tool-card'+(done?' expanded':'');
  let argsStr='';if(args&&Object.keys(args).length)argsStr=Object.entries(args).map(([k,v])=>`${k}: ${JSON.stringify(v)}`).join('\n');
  let filesHtml='';if(files&&files.length)filesHtml='<div class="tc-files">'+files.map(f=>{const n=f.split('/').pop();return`<a href="${f}" download>&#128196; ${n}</a>`;}).join('')+'</div>';
  const st=done?(elapsed!=null?`Done (${elapsed}s)`:'Done'):'Running...';
  card.innerHTML=`<div class="tc-header" onclick="this.parentElement.classList.toggle('expanded')"><div class="tc-icon">T</div><span class="tc-name">${esc(name)}</span><span class="tc-status">${st}</span><span class="tc-chevron">&#9654;</span></div><div class="tc-body">${argsStr?`<div class="tc-args-label">Args</div><div class="tc-args">${esc(argsStr)}</div>`:''}${output?`<div class="tc-output-label">Output</div><div class="tc-output">${esc(output)}</div>`:''}${filesHtml}</div>${!done?'<div class="progress-bar"><div class="progress-fill"></div></div>':''}`;
  $messages.appendChild(card);scrollBottom();return card;
}
function scrollBottom(){requestAnimationFrame(()=>{$messages.scrollTop=$messages.scrollHeight;});}

// File upload
function toggleUpload(){$uploadZone.classList.toggle('active');}
async function handleFileSelect(fl){for(const f of fl){try{const fd=new FormData();fd.append('file',f);const r=await fetch('/api/files',{method:'POST',body:fd,headers:hdrs()});if(!r.ok)throw new Error('Upload failed');const d=await r.json();pendingFiles.push({id:d.id,name:d.orig_name,mime:d.mime_type,page_count:d.page_count});renderFileChips();
// Show page previewer for PDF uploads
if(d.mime_type==='application/pdf'&&d.page_count>1){showPagePreviewer(d.id,d.orig_name,d.page_count);}
}catch(e){alert('Upload error: '+e.message);}}$uploadZone.classList.remove('active');}
function renderFileChips(){
  $fileChips.innerHTML='';
  pendingFiles.forEach((f,i)=>{
    const c=document.createElement('span');c.className='file-chip';
    const thumb=f.mime==='application/pdf'?`<img src="/api/files/${f.id}/thumbnail" onerror="this.style.display='none'" alt="">`:'&#128206;';
    c.innerHTML=`${thumb}<span class="chip-name">${esc(f.name)}</span><button onclick="removeFile(${i})">&times;</button>`;
    $fileChips.appendChild(c);
  });
}
function removeFile(i){pendingFiles.splice(i,1);renderFileChips();}
document.addEventListener('dragover',e=>{e.preventDefault();$uploadZone.classList.add('active','dragover');});
document.addEventListener('dragleave',e=>{if(!e.relatedTarget||e.relatedTarget===document.documentElement)$uploadZone.classList.remove('dragover');});
document.addEventListener('drop',e=>{e.preventDefault();$uploadZone.classList.remove('dragover');if(e.dataTransfer.files.length)handleFileSelect(e.dataTransfer.files);});

// Send (SSE)
async function sendMessage(){
  const text=$input.value.trim();if(!text||isStreaming)return;
  addMessage('user',text);chatHistory.push({role:'user',content:text});
  $input.value='';autoResize($input);
  const fileIds=pendingFiles.map(f=>f.id);pendingFiles=[];renderFileChips();
  isStreaming=true;$('btnSend').disabled=true;
  const body={message:text,file_ids:fileIds};if(currentThreadId)body.thread_id=currentThreadId;
  try{
    const res=await fetch('/api/agent/chat',{method:'POST',headers:hdrs({'Content-Type':'application/json'}),body:JSON.stringify(body)});
    if(!res.ok){const err=await res.json().catch(()=>({}));addMessage('agent','Error: '+(err.detail||res.status));return;}
    const reader=res.body.getReader(),dec=new TextDecoder();
    let buf='',streamBubble=null,streamText='',activeCard=null;
    while(true){
      const{done,value}=await reader.read();if(done)break;
      buf+=dec.decode(value,{stream:true});const lines=buf.split('\n');buf=lines.pop();
      let evt='';
      for(const line of lines){
        if(line.startsWith('event: '))evt=line.slice(7).trim();
        else if(line.startsWith('data: ')&&evt){handleEvt(evt,JSON.parse(line.slice(6)));evt='';}
      }
    }
    function handleEvt(ev,d){
      switch(ev){
        case'thread':currentThreadId=d.thread_id;$chatTitle.textContent=d.thread_id.slice(0,12)+'...';break;
        case'token':if(!streamBubble)streamBubble=addStreamingBubble();streamText+=d.content;streamBubble.innerHTML=renderMd(streamText);scrollBottom();break;
        case'tool_start':if(streamBubble&&streamText){streamBubble.innerHTML=renderMd(streamText);streamBubble.parentElement.dataset.text=streamText;chatHistory.push({role:'agent',content:streamText});streamBubble=null;streamText='';}activeCard=addToolCard(d.tool,d.args,'',[]  ,null,false);break;
        case'tool_progress':if(activeCard){const s=activeCard.querySelector('.tc-status');if(s){const pct=d.percent!=null?` ${d.percent}%`:'';const msg=d.message?` — ${d.message}`:'';s.textContent=`Running...${pct}${msg} ${d.elapsed_seconds||''}s`;}}break;
        case'tool_end':if(activeCard){const p=activeCard.parentElement||$messages;const nc=document.createElement('div');nc.className='tool-card';const ae=activeCard.querySelector('.tc-args');const at=ae?ae.textContent:'';let fh='';if(d.files&&d.files.length)fh='<div class="tc-files">'+d.files.map(f=>`<a href="${f}" download>&#128196; ${f.split('/').pop()}</a>`).join('')+'</div>';const st=d.elapsed_seconds!=null?`Done (${d.elapsed_seconds}s)`:'Done';
// Build "continue processing" quick actions
let continueHtml='';
if(d.files&&d.files.length){
  const suggestions=['compress','ocr','watermark_text','rotate','metadata_info'];
  continueHtml='<div class="tc-continue">'+suggestions.map(t=>`<button onclick="continueWithFile('${d.files[0]}','${t}')">${t}</button>`).join('')+'</div>';
}
nc.innerHTML=`<div class="tc-header" onclick="this.parentElement.classList.toggle('expanded')"><div class="tc-icon">T</div><span class="tc-name">${esc(d.tool)}</span><span class="tc-status">${st}</span><span class="tc-chevron">&#9654;</span></div><div class="tc-body">${at?`<div class="tc-args-label">Args</div><div class="tc-args">${esc(at)}</div>`:''}${d.output?`<div class="tc-output-label">Output</div><div class="tc-output">${esc(d.output)}</div>`:''}${fh}${continueHtml}</div>`;$messages.replaceChild(nc,activeCard);activeCard=null;chatHistory.push({role:'tool',name:d.tool,content:d.output});}break;
        case'error':addMessage('agent','Error: '+d.message);break;
        case'done':if(streamBubble&&streamText){streamBubble.innerHTML=renderMd(streamText);streamBubble.parentElement.dataset.text=streamText;chatHistory.push({role:'agent',content:streamText});}break;
      }
    }
  }catch(e){addMessage('agent','Connection error: '+e.message);}
  finally{isStreaming=false;$('btnSend').disabled=false;loadThreads();}
}

// Search / filter
function filterMessages(q){
  const msgs=$messages.querySelectorAll('.msg, .tool-card');
  msgs.forEach(el=>{
    if(!q){el.classList.remove('hidden');const b=el.querySelector('.msg-bubble');if(b&&el.dataset.text)b.innerHTML=renderMd(el.dataset.text);return;}
    const t=(el.dataset.text||el.textContent||'').toLowerCase();
    if(t.includes(q.toLowerCase())){el.classList.remove('hidden');const b=el.querySelector('.msg-bubble');if(b&&el.dataset.text)b.innerHTML=highlightMd(el.dataset.text,q);}
    else el.classList.add('hidden');
  });
}
function highlightMd(text,q){let html=renderMd(text);const re=new RegExp(`(${q.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')})`,'gi');return html.replace(re,'<mark>$1</mark>');}

// Export
function exportChat(fmt){
  if(!chatHistory.length){alert('No messages to export');return;}
  let content,filename,mime;
  if(fmt==='json'){content=JSON.stringify({thread_id:currentThreadId,messages:chatHistory},null,2);filename='chat.json';mime='application/json';}
  else{content=chatHistory.map(m=>{if(m.role==='user')return`**User:** ${m.content}`;if(m.role==='agent')return`**Agent:** ${m.content}`;return`**Tool (${m.name}):** ${m.content}`;}).join('\n\n---\n\n');filename='chat.md';mime='text/markdown';}
  const blob=new Blob([content],{type:mime});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=filename;a.click();URL.revokeObjectURL(a.href);
}

// Helpers
function handleKey(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMessage();}}
function autoResize(el){el.style.height='auto';el.style.height=Math.min(el.scrollHeight,120)+'px';}
function toggleSidebar(){$('sidebar').classList.toggle('open');}
function renderMd(t){if(!t)return'';let h=esc(t);h=h.replace(/```(\w*)\n([\s\S]*?)```/g,(_,l,c)=>`<pre><code>${c}</code></pre>`);h=h.replace(/`([^`]+)`/g,'<code>$1</code>');h=h.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');h=h.replace(/\*(.+?)\*/g,'<em>$1</em>');h=h.replace(/\[([^\]]+)\]\(([^)]+)\)/g,'<a href="$2" target="_blank">$1</a>');h=h.replace(/\n\n+/g,'</p><p>');h=h.replace(/\n/g,'<br>');return'<p>'+h+'</p>';}
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML;}

// ---------------------------------------------------------------------------
// Batch Run
// ---------------------------------------------------------------------------
let _batchSelected = new Set(); // file IDs

function updateBatchBar() {
  const bar = $('batchBar');
  const count = _batchSelected.size;
  $('batchCount').textContent = `${count} file${count !== 1 ? 's' : ''} selected`;
  bar.classList.toggle('open', count > 0);
}

function toggleBatchFile(fileId, checked) {
  if (checked) _batchSelected.add(fileId);
  else _batchSelected.delete(fileId);
  updateBatchBar();
}

function clearBatchSelection() {
  _batchSelected.clear();
  document.querySelectorAll('.fi-check').forEach(cb => { cb.checked = false; });
  updateBatchBar();
}

async function openBatchRun() {
  if (_batchSelected.size === 0) return;
  const toolName = prompt('Enter tool name to run on all selected files (e.g. rotate, compress, ocr):');
  if (!toolName || !toolName.trim()) return;
  const tool = _allTools.find(t => t.name === toolName.trim());
  if (!tool) { alert(`Tool "${toolName}" not found`); return; }

  // Collect params via prompt for required params
  const params = {};
  if (tool.params) {
    for (const p of tool.params) {
      if (p.required) {
        const val = prompt(`${p.label}${p.options ? ' (' + p.options.join('/') + ')' : ''}:`, p.default || '');
        if (val === null) return;
        params[p.name] = val || p.default || '';
      } else if (p.default !== null && p.default !== undefined) {
        params[p.name] = p.default;
      }
    }
  }

  const fileIds = [..._batchSelected];
  const bar = $('batchBar');
  bar.querySelector('.btn-batch').disabled = true;
  bar.querySelector('.btn-batch').textContent = 'Running...';

  // Switch to chat tab and show progress
  switchTab('chat', document.querySelectorAll('.sidebar-tab')[0]);
  addMessage('agent', `&#9889; Running **${toolName}** on ${fileIds.length} file(s)...`);

  let success = 0, failed = 0;
  for (const fid of fileIds) {
    try {
      const d = await api(`/api/tools/${toolName}/run`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ file_ids: [fid], params }),
      });
      success++;
      const files = (d.output_files || []).map(f => `<a href="${f.download_url}" download style="color:var(--accent)">&#8681; ${esc(f.filename)}</a>`).join(' ');
      addMessage('agent', `File ${fid.slice(0, 8)}: ${esc(d.log || 'Done')}${files ? '<br>' + files : ''}`);
    } catch (e) {
      failed++;
      addMessage('agent', `File ${fid.slice(0, 8)}: Error — ${esc(e.message)}`);
    }
  }
  addMessage('agent', `Batch complete: ${success} succeeded, ${failed} failed.`);

  bar.querySelector('.btn-batch').disabled = false;
  bar.querySelector('.btn-batch').textContent = '&#9889; Batch Run';
  clearBatchSelection();
}

// ---------------------------------------------------------------------------
// PDF Page Previewer
// ---------------------------------------------------------------------------
let _previewFileId = null;
let _previewPageCount = 0;
let _selectedPages = new Set();

async function showPagePreviewer(fileId, fileName, pageCount) {
  _previewFileId = fileId;
  _previewPageCount = pageCount || 1;
  _selectedPages.clear();

  const $prev = $('pagePreviewer');
  const $strip = $('pageStrip');
  $('previewerTitle').textContent = `${fileName} — ${_previewPageCount} page${_previewPageCount > 1 ? 's' : ''}`;
  $strip.innerHTML = '';

  // Load first 10 pages lazily
  const limit = Math.min(_previewPageCount, 12);
  for (let i = 1; i <= limit; i++) {
    const thumb = document.createElement('div');
    thumb.className = 'page-thumb';
    thumb.dataset.page = i;
    thumb.onclick = () => togglePageSelection(thumb, i);
    thumb.ondblclick = () => openFsView(_previewFileId, i, _previewPageCount);
    thumb.innerHTML = `<img src="/api/files/${fileId}/pages/${i}" loading="lazy" onerror="this.style.display='none'"><div class="pg-num">${i}</div>`;
    _makeDraggable(thumb);
    $strip.appendChild(thumb);
  }
  if (_previewPageCount > 12) {
    $strip.innerHTML += `<div style="display:flex;align-items:center;font-size:11px;color:var(--text-secondary);padding:0 8px">+${_previewPageCount - 12} more pages</div>`;
  }

  $prev.classList.add('open');
  updatePageRangeHint();
}

function togglePageSelection(thumb, page) {
  if (_selectedPages.has(page)) {
    _selectedPages.delete(page);
    thumb.classList.remove('selected');
  } else {
    _selectedPages.add(page);
    thumb.classList.add('selected');
  }
  updatePageRangeHint();
}

function updatePageRangeHint() {
  const hint = $('pageRangeHint');
  if (_selectedPages.size === 0) {
    hint.textContent = 'Click pages to select, or use "all" for all pages';
  } else if (_selectedPages.size === _previewPageCount) {
    hint.textContent = 'Selected: all pages';
  } else {
    const sorted = [..._selectedPages].sort((a, b) => a - b);
    hint.textContent = `Selected: ${sorted.join(', ')} → page_range: "${sorted.join(',')}"`;
  }
}

function getSelectedPageRange() {
  if (_selectedPages.size === 0 || _selectedPages.size === _previewPageCount) return 'all';
  return [..._selectedPages].sort((a, b) => a - b).join(',');
}

function hidePagePreviewer() {
  $('pagePreviewer').classList.remove('open');
  _previewFileId = null;
  _previewPageCount = 0;
  _selectedPages.clear();
  _dragOrder = [];
  $('btnApplyReorder').style.display = 'none';
}

// ---------------------------------------------------------------------------
// Drag-and-drop page reorder
// ---------------------------------------------------------------------------
let _dragOrder = []; // current page order after dragging
let _dragSrc = null;

function _makeDraggable(thumb) {
  thumb.draggable = true;
  thumb.addEventListener('dragstart', e => {
    _dragSrc = thumb;
    e.dataTransfer.effectAllowed = 'move';
    thumb.style.opacity = '0.4';
  });
  thumb.addEventListener('dragend', () => { thumb.style.opacity = '1'; });
  thumb.addEventListener('dragover', e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; });
  thumb.addEventListener('drop', e => {
    e.preventDefault();
    if (_dragSrc === thumb) return;
    const strip = $('pageStrip');
    const thumbs = [...strip.querySelectorAll('.page-thumb')];
    const srcIdx = thumbs.indexOf(_dragSrc);
    const tgtIdx = thumbs.indexOf(thumb);
    if (srcIdx < tgtIdx) strip.insertBefore(_dragSrc, thumb.nextSibling);
    else strip.insertBefore(_dragSrc, thumb);
    // Update order display
    _dragOrder = [...strip.querySelectorAll('.page-thumb')].map(t => parseInt(t.dataset.page));
    $('pageRangeHint').textContent = `New order: ${_dragOrder.join(', ')} — click "Apply reorder" to save`;
    $('btnApplyReorder').style.display = '';
  });
}

async function applyDragReorder() {
  if (!_previewFileId || !_dragOrder.length) return;
  $('btnApplyReorder').textContent = 'Applying...';
  $('btnApplyReorder').disabled = true;
  try {
    const d = await api('/api/tools/reorder', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ file_id: _previewFileId, order: _dragOrder }),
    });
    const files = (d.output_files || []).map(f => `<a href="${f.download_url}" download style="color:var(--accent)">&#8681; ${esc(f.filename)}</a>`).join(' ');
    addMessage('agent', `Pages reordered (${_dragOrder.join(', ')}). ${files}`);
    hidePagePreviewer();
    $emptyState.style.display = 'none';
  } catch (e) {
    $('pageRangeHint').textContent = 'Error: ' + e.message;
  }
  $('btnApplyReorder').textContent = '\u2195 Apply reorder';
  $('btnApplyReorder').disabled = false;
}

// ---------------------------------------------------------------------------
// Direct Tool Runner
// ---------------------------------------------------------------------------
let _allTools = [];
let _selectedFileForTool = null;

async function loadToolList() {
  try {
    const d = await api('/api/tools');
    _allTools = d.tools || [];
    const sel = $('trToolSelect');
    while (sel.options.length > 1) sel.remove(1);
    _allTools.forEach(t => {
      const opt = document.createElement('option');
      opt.value = t.name; opt.textContent = t.label || t.name;
      sel.appendChild(opt);
    });
  } catch {}
}

function onToolSelect() {
  const toolName = $('trToolSelect').value;
  const $params = $('trParams');
  $params.innerHTML = '';
  $('trResult').className = 'tr-result';
  if (!toolName) { $('btnRun').disabled = true; return; }
  const tool = _allTools.find(t => t.name === toolName);
  if (tool && tool.params) {
    tool.params.forEach(p => {
      const div = document.createElement('div');
      div.className = 'tr-param';
      const opts = p.options ? p.options.join(', ') : '';
      div.innerHTML = `<label>${esc(p.label)}${p.required?' *':''}${opts?' ('+esc(opts)+')':''}</label>`;
      if (p.type === 'page_range') {
        // Visual page_range selector
        const defaultVal = _selectedPages.size > 0 ? getSelectedPageRange() : (p.default || 'all');
        div.innerHTML += `<input id="trp_${p.name}" type="text" value="${esc(defaultVal)}" placeholder="all, 1-3, odd, even">`;
        div.innerHTML += `<div class="pr-helper"><button class="pr-chip" onclick="setPR('trp_${p.name}','all')">all</button><button class="pr-chip" onclick="setPR('trp_${p.name}','odd')">odd</button><button class="pr-chip" onclick="setPR('trp_${p.name}','even')">even</button><button class="pr-chip" onclick="setPR('trp_${p.name}','1')">first</button>${_previewPageCount>0?'<button class="pr-chip" onclick="setPR(\'trp_'+p.name+'\',getSelectedPageRange())">use selected</button>':''}</div>`;
      } else if (p.options && p.options.length) {
        div.innerHTML += `<select id="trp_${p.name}" style="width:100%;border:1px solid var(--border);border-radius:4px;padding:4px 8px;font-size:12px;background:var(--bg-input);color:var(--text)">${p.options.map(o=>`<option value="${esc(o)}"${o===p.default?' selected':''}>${esc(o)}</option>`).join('')}</select>`;
      } else {
        div.innerHTML += `<input id="trp_${p.name}" type="text" value="${esc(p.default||'')}" placeholder="${esc(p.description||'')}">`;
      }
      $params.appendChild(div);
    });
  }
  $('btnRun').disabled = !_selectedFileForTool;
}

function setPR(inputId, value) {
  const el = document.getElementById(inputId);
  if (el) el.value = value;
}

async function continueWithFile(downloadUrl, toolName) {
  // Extract thread_id and step/filename from URL like /api/agent/threads/{id}/files/{step}/{file}
  const parts = downloadUrl.split('/');
  // URL: /api/agent/threads/TID/files/STEP/FILE
  const tidIdx = parts.indexOf('threads');
  const threadId = tidIdx >= 0 ? parts[tidIdx + 1] : null;

  const msg = `Apply ${toolName} to the output file from the previous step.`;
  $input.value = msg;
  if (threadId) currentThreadId = threadId;
  autoResize($input);
  $input.focus();
}

function selectFileForTool(fileId, fileName) {
  _selectedFileForTool = fileId;
  const h4 = document.querySelector('.tool-runner h4');
  if (h4) h4.textContent = `\u26A1 Run Tool on: ${fileName.length > 20 ? fileName.slice(0,18)+'...' : fileName}`;
  if ($('trToolSelect').value) $('btnRun').disabled = false;
}

async function runToolDirect() {
  const toolName = $('trToolSelect').value;
  if (!toolName || !_selectedFileForTool) return;
  const tool = _allTools.find(t => t.name === toolName);
  const params = {};
  if (tool && tool.params) {
    tool.params.forEach(p => {
      const el = document.getElementById('trp_' + p.name);
      if (el && el.value) params[p.name] = el.value;
    });
  }
  $('btnRun').disabled = true;
  $('btnRun').textContent = 'Running...';
  $('trResult').className = 'tr-result';
  try {
    const d = await api(`/api/tools/${toolName}/run`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ file_ids: [_selectedFileForTool], params }),
    });
    let html = `<strong>Done</strong> \u2014 ${esc(d.log||'')}`;
    if (d.output_files && d.output_files.length) {
      html += d.output_files.map(f => `<a href="${f.download_url}" download>&#8681; ${esc(f.filename)} (${(f.size_bytes/1024).toFixed(0)}KB)</a>`).join('');
    }
    $('trResult').innerHTML = html;
    $('trResult').className = 'tr-result visible';
  } catch (e) {
    $('trResult').innerHTML = `<span style="color:#ef4444">Error: ${esc(e.message)}</span>`;
    $('trResult').className = 'tr-result visible';
  }
  $('btnRun').disabled = false;
  $('btnRun').textContent = 'Run on selected file';
  loadHistory();
}

const _baseRenderFileManager = renderFileManager;
renderFileManager = function() {
  _baseRenderFileManager();
  document.querySelectorAll('.file-item').forEach((el, i) => {
    const f = _fileManagerFiles[i];
    if (!f) return;
    const btn = document.createElement('button');
    btn.className = 'fi-btn'; btn.title = 'Select for tool'; btn.textContent = '\u2713';
    btn.style.color = 'var(--accent)';
    btn.onclick = () => selectFileForTool(f.id, f.orig_name);
    el.querySelector('.fi-actions').prepend(btn);
  });
};

// Workflows
async function loadWorkflows(){
  try{
    const d=await api('/api/workflows');
    const bar=$('workflowBar');
    (d.workflows||[]).forEach(w=>{
      const btn=document.createElement('button');
      btn.className='wf-btn';
      btn.textContent=w.name;
      btn.title=w.description;
      btn.onclick=()=>applyWorkflow(w);
      bar.appendChild(btn);
    });
  }catch{}
}
async function applyWorkflow(w){
  if(!w.params||w.params.length===0){
    // No params needed, just set the prompt
    $input.value=w.prompt_template;
    autoResize($input);
    $input.focus();
    return;
  }
  // Collect params via prompts
  const values={};
  for(const p of w.params){
    const val=prompt(`${p.label}${p.default?' (default: '+p.default+')':''}:`,p.default||'');
    if(val===null)return;
    values[p.name]=val||p.default||'';
  }
  try{
    const d=await api(`/api/workflows/${w.id}/render`,{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({workflow_id:w.id,params:values,file_ids:pendingFiles.map(f=>f.id)}),
    });
    $input.value=d.prompt;
    autoResize($input);
    $input.focus();
  }catch(e){alert('Workflow error: '+e.message);}
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------
function switchTab(name, btn) {
  document.querySelectorAll('.sidebar-tab').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('tab-' + name).classList.add('active');
  if (name === 'files') { loadFileManager(); loadHistory(); }
  if (name === 'workflows') loadWorkflowManager();
}

// ---------------------------------------------------------------------------
// File Manager
// ---------------------------------------------------------------------------
let _fileManagerFiles = [];

async function loadFileManager() {
  const $fm = $('fileManager');
  $fm.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-secondary);font-size:13px">Loading...</div>';
  try {
    const d = await api('/api/files');
    _fileManagerFiles = d.files || [];
    renderFileManager();
  } catch (e) {
    $fm.innerHTML = `<div style="padding:20px;text-align:center;color:#ef4444;font-size:13px">Error: ${esc(e.message)}</div>`;
  }
}

function renderFileManager() {
  const $fm = $('fileManager');
  if (!_fileManagerFiles.length) {
    $fm.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-secondary);font-size:13px">No files yet. Upload a file to get started.</div>';
    return;
  }
  $fm.innerHTML = '';
  _fileManagerFiles.forEach(f => {
    const el = document.createElement('div');
    el.className = 'file-item';
    const thumb = f.thumbnail_url
      ? `<img src="${f.thumbnail_url}" onerror="this.style.display='none'" alt="">`
      : `<div class="fi-icon">&#128196;</div>`;
    const size = f.size_bytes > 1048576 ? (f.size_bytes/1048576).toFixed(1)+'MB' : (f.size_bytes/1024).toFixed(0)+'KB';
    const pages = f.page_count ? ` · ${f.page_count}p` : '';
    const checked = _batchSelected.has(f.id) ? 'checked' : '';
    el.innerHTML = `<input type="checkbox" class="fi-check" ${checked} onchange="toggleBatchFile('${f.id}',this.checked)" title="Select for batch">
      ${thumb}
      <div class="fi-info">
        <div class="fi-name" title="${esc(f.orig_name)}">${esc(f.orig_name)}</div>
        <div class="fi-meta">${size}${pages}</div>
      </div>
      <div class="fi-actions">
        <button class="fi-btn" onclick="useFileInChat('${f.id}','${esc(f.orig_name)}','${f.mime_type}')" title="Use in chat">&#8599;</button>
        <a class="fi-btn" href="${f.download_url}" download title="Download">&#8681;</a>
        <button class="fi-btn" onclick="deleteFileFromManager('${f.id}')" title="Delete" style="color:#ef4444">&#128465;</button>
      </div>`;
    $fm.appendChild(el);
  });
}

async function uploadAndRefreshFiles(fileList) {
  for (const f of fileList) {
    try {
      const fd = new FormData();
      fd.append('file', f);
      await fetch('/api/files', { method: 'POST', body: fd, headers: hdrs() });
    } catch (e) { alert('Upload error: ' + e.message); }
  }
  loadFileManager();
}

async function deleteFileFromManager(fileId) {
  if (!confirm('Delete this file?')) return;
  try {
    await api(`/api/files/${fileId}`, { method: 'DELETE' });
    loadFileManager();
  } catch (e) { alert('Delete failed: ' + e.message); }
}

function useFileInChat(fileId, name, mime) {
  pendingFiles.push({ id: fileId, name, mime });
  renderFileChips();
  switchTab('chat', document.querySelectorAll('.sidebar-tab')[0]);
}

// ---------------------------------------------------------------------------
// Workflow Manager
// ---------------------------------------------------------------------------
let _editingWorkflowId = null;

async function loadWorkflowManager() {
  const $wm = $('wfManager');
  try {
    const d = await api('/api/workflows');
    $wm.innerHTML = '';
    (d.workflows || []).forEach(w => {
      const el = document.createElement('div');
      el.className = 'wf-item';
      const badge = w.builtin ? '<span class="wi-badge">built-in</span>' : '<span class="wi-badge" style="background:#dcfce7;color:#166534">custom</span>';
      el.innerHTML = `
        <div class="wi-header">
          <span class="wi-name">${esc(w.name)}</span>${badge}
        </div>
        <div class="wi-desc">${esc(w.description || '')}</div>
        <div class="wi-actions">
          <button class="wi-btn use" onclick="applyWorkflowById('${w.id}')">&#9889; Use</button>
          ${!w.builtin ? `<button class="wi-btn" onclick="editWorkflow('${w.id}')">Edit</button><button class="wi-btn" onclick="deleteWorkflowById('${w.id}')" style="color:#ef4444">Delete</button>` : ''}
        </div>`;
      $wm.appendChild(el);
    });
  } catch (e) {
    $wm.innerHTML = `<div style="padding:20px;text-align:center;color:#ef4444;font-size:13px">Error: ${esc(e.message)}</div>`;
  }
}

async function applyWorkflowById(wfId) {
  try {
    const w = await api(`/api/workflows/${wfId}`);
    await applyWorkflow(w);
    switchTab('chat', document.querySelectorAll('.sidebar-tab')[0]);
  } catch (e) { alert('Error: ' + e.message); }
}

async function deleteWorkflowById(wfId) {
  if (!confirm('Delete this workflow?')) return;
  try {
    await api(`/api/workflows/${wfId}`, { method: 'DELETE' });
    loadWorkflowManager();
  } catch (e) { alert('Delete failed: ' + e.message); }
}

function openWorkflowModal(wf = null) {
  _editingWorkflowId = wf ? wf.id : null;
  $('wfModalTitle').textContent = wf ? 'Edit Workflow' : 'Create Workflow';
  $('wfName').value = wf ? wf.name : '';
  $('wfDesc').value = wf ? (wf.description || '') : '';
  $('wfPrompt').value = wf ? wf.prompt_template : '';
  $('wfModal').classList.add('open');
}

function closeWorkflowModal() {
  $('wfModal').classList.remove('open');
  _editingWorkflowId = null;
}

async function editWorkflow(wfId) {
  try {
    const w = await api(`/api/workflows/${wfId}`);
    openWorkflowModal(w);
  } catch (e) { alert('Error: ' + e.message); }
}

async function saveWorkflow() {
  const name = $('wfName').value.trim();
  const prompt = $('wfPrompt').value.trim();
  if (!name || !prompt) { alert('Name and prompt template are required'); return; }
  const body = { name, description: $('wfDesc').value.trim(), prompt_template: prompt };
  try {
    if (_editingWorkflowId) {
      await api(`/api/workflows/${_editingWorkflowId}`, { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
    } else {
      await api('/api/workflows', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
    }
    closeWorkflowModal();
    loadWorkflowManager();
  } catch (e) { alert('Save failed: ' + e.message); }
}

$('wfModal').addEventListener('click', e => { if (e.target === $('wfModal')) closeWorkflowModal(); });

// ---------------------------------------------------------------------------
// Fullscreen page viewer
// ---------------------------------------------------------------------------
let _fsFileId = null;
let _fsCurrent = 1;
let _fsTotal = 1;

function openFsView(fileId, page, total) {
  _fsFileId = fileId;
  _fsCurrent = page;
  _fsTotal = total;
  _updateFsView();
  $('fsOverlay').classList.add('open');
}

function closeFsView() {
  $('fsOverlay').classList.remove('open');
}

function fsPrevPage() {
  if (_fsCurrent > 1) { _fsCurrent--; _updateFsView(); }
}

function fsNextPage() {
  if (_fsCurrent < _fsTotal) { _fsCurrent++; _updateFsView(); }
}

function _updateFsView() {
  $('fsImg').src = `/api/files/${_fsFileId}/pages/${_fsCurrent}`;
  $('fsInfo').textContent = `Page ${_fsCurrent} / ${_fsTotal}`;
  $('fsPrev').style.opacity = _fsCurrent > 1 ? '1' : '0.3';
  $('fsNext').style.opacity = _fsCurrent < _fsTotal ? '1' : '0.3';
}

document.addEventListener('keydown', e => {
  if (!$('fsOverlay').classList.contains('open')) return;
  if (e.key === 'ArrowLeft') fsPrevPage();
  if (e.key === 'ArrowRight') fsNextPage();
  if (e.key === 'Escape') closeFsView();
});

// ---------------------------------------------------------------------------
// Tool run history
// ---------------------------------------------------------------------------
async function loadHistory() {
  try {
    const d = await api('/api/tools/history');
    const $list = $('historyList');
    const items = d.history || [];
    if (!items.length) {
      $list.innerHTML = '<span style="color:var(--text-secondary);font-size:11px">No runs yet</span>';
      return;
    }
    $list.innerHTML = '';
    items.forEach(h => {
      const el = document.createElement('div');
      el.className = 'hist-item';
      const t = new Date(h.timestamp * 1000);
      const timeStr = t.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
      let dlHtml = '';
      if (h.output_files && h.output_files.length) {
        const urls = h.output_files.map(f => f.download_url);
        dlHtml = `<a class="hist-dl" href="${h.output_files[0].download_url}" download>&#8681;</a>`;
        if (h.output_files.length > 1) {
          dlHtml += `<button class="hist-dl" style="border:none;background:none;cursor:pointer" onclick="downloadZip(${JSON.stringify(urls)})">&#128230;</button>`;
        }
      }
      el.innerHTML = `<span class="hist-tool">${esc(h.tool)}</span><span class="hist-time">${timeStr}</span>${dlHtml}`;
      $list.appendChild(el);
    });
  } catch {}
}

async function downloadZip(urls) {
  try {
    const resp = await fetch('/api/tools/download-zip', {
      method: 'POST',
      headers: hdrs({'Content-Type': 'application/json'}),
      body: JSON.stringify({urls}),
    });
    if (!resp.ok) throw new Error('ZIP failed');
    const blob = await resp.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'pdf_agent_results.zip';
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) { alert('Download failed: ' + e.message); }
}

// Init
loadThreads();loadWorkflows();loadToolList();$input.focus();
