'use strict';
const $ = id => document.getElementById(id);
// Relative URLs preserve a JupyterHub /user/.../proxy/<port>/ mount prefix.
const base = new URL('./', location.href);
const url = path => new URL(path, base).href;
const state = {health: null, meeting: null, socket: null, connected: false,
  demoTimer: null, demoPlaying: false, demoBusy: false, microphone: null,
  recording: false, preparing: false, drafts: new Map(), audioPending: 0, elapsed: 0, started: 0, filter: '',
  finishing: false, summaryRunning: false, retranslating: false, reconnect: null, generation: 0, ping: null};
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = name => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const clock = ms => {
  const seconds = Math.floor(ms / 1000);
  return `${String(Math.floor(seconds / 60)).padStart(2,'0')}:${String(seconds % 60).padStart(2,'0')}`;
};
const prettyDate = value => new Date(value).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
function toast(message, error = false) {
  const item = document.createElement('div');
  item.className = `toast${error ? ' error' : ''}`;
  item.textContent = message;
  $('toasts').append(item);
  setTimeout(() => item.remove(), error ? 10000 : 4500);
}
async function api(path, options = {}) {
  const response = await fetch(url(path), options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : `请求失败 (${response.status})`);
  }
  return response.json();
}
function send(message) {
  if (!state.connected || state.socket?.readyState !== WebSocket.OPEN) throw new Error('连接尚未就绪，请稍候重试。');
  state.socket.send(JSON.stringify(message));
}
function live() { return state.meeting?.status === 'live'; }
function elapsed() { return state.elapsed + (state.recording ? performance.now() - state.started : 0); }

async function refreshHistory() {
  const meetings = await api('api/meetings');
  $('history-count').textContent = meetings.length;
  if (!meetings.length) {
    $('history').innerHTML = '<p class="history-empty">暂无记录</p>';
    return;
  }
  $('history').innerHTML = meetings.map(m => `<button class="history-item${m.id === state.meeting?.id ? ' current' : ''}" data-meeting="${escapeHTML(m.id)}"><strong>${escapeHTML(m.title)}</strong><small>${escapeHTML(prettyDate(m.created_at))} · ${m.segment_count} 句${m.provider === 'demo' ? ' · 演示' : ''}</small></button>`).join('');
}
function setConnection(connected, label) {
  state.connected = connected;
  $('connection').classList.toggle('offline', !connected);
  $('connection').innerHTML = `<i></i>${escapeHTML(label)}`;
  renderControls();
}
async function openMeeting(id) {
  if (state.recording || state.preparing || state.demoPlaying || state.finishing) {
    toast('请先暂停录音／演示并等待结束操作完成，再切换记录。', true);
    return;
  }
  const generation = ++state.generation;
  clearTimeout(state.reconnect); clearInterval(state.ping);
  state.socket?.close(); state.audioPending = 0; state.drafts.clear();
  setConnection(false, '正在连接');
  const meeting = await api(`api/meetings/${id}`);
  if (generation !== state.generation) return;
  state.meeting = meeting;
  state.elapsed = Math.max(0, ...meeting.segments.map(s => s.end_ms));
  state.summaryRunning = false;
  state.retranslating = false;
  localStorage.setItem('tingjian.lastMeeting', id);
  renderMeeting();
  await connect(id, generation);
  await refreshHistory();
}
function connect(id, generation) {
  return new Promise((resolve, reject) => {
    const endpoint = new URL(`ws/${id}`, base);
    endpoint.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(endpoint);
    state.socket = socket;
    let opened = false;
    socket.onopen = () => {
      if (generation !== state.generation) { socket.close(); return; }
      opened = true; setConnection(true, '已连接');
      clearInterval(state.ping);
      state.ping = setInterval(() => { if (socket.readyState === WebSocket.OPEN) socket.send('{"type":"ping"}'); }, 2500);
      resolve();
    };
    socket.onmessage = event => {
      if (generation !== state.generation) return;
      let message;
      try { message = JSON.parse(event.data); } catch { return; }
      if (message.type === 'snapshot') {
        state.meeting = message.meeting;
        state.drafts = new Map((message.drafts || []).map(d => [d.utterance_id, d]));
        state.summaryRunning = message.summary_running;
        state.retranslating = Boolean(message.retranslating);
        state.finishing = message.meeting.status === 'finishing';
        renderMeeting(); renderMetrics(message.metrics);
      } else if (message.type === 'transcript_partial') {
        state.drafts.set(message.utterance_id, {...message,pending:state.drafts.get(message.utterance_id)?.pending}); renderTranscript();
      } else if (message.type === 'transcript_final') {
        state.drafts.delete(message.utterance_id); renderTranscript();
      } else if (message.type === 'metrics') renderMetrics(message.metrics);
      else if (message.type === 'error') toast(message.message, true);
      else if (message.type === 'notice') toast(message.message);
      else if (message.type === 'retranslation_started') { state.retranslating=true;renderControls(); }
      else if (message.type === 'retranslation_done') { state.retranslating=false;renderControls();toast('重译任务已完成'); }
      else if (message.type === 'audio_done') state.audioPending = Math.max(0, state.audioPending - 1);
      else if (message.type === 'demo_done') { pauseDemo(); toast('示例播放完成'); }
      else if (message.type === 'finished') {
        pauseDemo(); state.finishing = false;
        if (state.recording) stopMicrophone().catch(e => toast(e.message, true));
        refreshHistory().catch(() => {}); toast('已保存');
      }
    };
    socket.onclose = () => {
      if (generation !== state.generation) return;
      clearInterval(state.ping); pauseDemo();
      if (state.recording) stopMicrophone().catch(() => {});
      setConnection(false, '连接断开，正在重试');
      if (!opened) reject(new Error('无法建立实时连接。请检查服务是否启动，以及代理是否支持 WebSocket。'));
      state.reconnect = setTimeout(() => {
        if (generation === state.generation) connect(id, generation).catch(() => {});
      }, 3000);
    };
    socket.onerror = () => { /* close handles recovery and the visible state */ };
  });
}
async function createMeeting(values) {
  if (state.recording || state.preparing || state.demoPlaying || state.finishing) throw new Error('请先暂停当前录音／演示。');
  const meeting = await api('api/meetings', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});
  await openMeeting(meeting.id);
  return meeting;
}
async function ensureMeeting() {
  if (live()) {
    if (!state.connected) throw new Error('实时连接正在恢复，请稍后重试。');
    return;
  }
  await createMeeting({});
}

function renderMeeting() {
  const m = state.meeting;
  if (!m) return;
  $('meeting-title').textContent = m.title;
  $('meeting-date').textContent = prettyDate(m.created_at);
  $('language-label').textContent = `${({zh:'中文',en:'English',auto:'自动识别'})[m.source]} → ${m.target === 'en' ? 'English' : '中文'}`;
  const status = {live:'记录中',finishing:'正在整理',completed:'已完成',interrupted:'上次运行已中断'};
  $('session-status').textContent = status[m.status] || m.status;
  $('session-status').classList.toggle('live', live());
  $('segment-count').textContent = `${m.segments.length} 条原文`;
  $('word-count').textContent = m.segments.reduce((n,s) => n + s.text.replace(/\s/g,'').length, 0);
  $('summary-count').textContent = m.summaries.length;
  const last = [...m.segments].reverse().find(s => s.translation_ms != null && !s.translation_skipped);
  $('latency').textContent = last ? `${Math.round(last.translation_ms)} ms` : '—';
  $('export-button').disabled = false;
  renderTranscript(); renderNotes(); renderControls();
}
function renderTranscript() {
  if (!state.meeting) return;
  const container = $('transcript');
  if (!state.meeting.segments.length && !state.drafts.size) {
    if (container.querySelector('.draft')) container.innerHTML = '<div class="empty-state"><p>暂无转写</p></div>';
    return;
  }
  const oldTop = container.scrollTop;
  const segments = state.meeting.segments.filter(s => `${s.text} ${s.translation}`.toLowerCase().includes(state.filter));
  // Reuse unchanged segment nodes so an active audio player survives metrics and translation updates.
  const existing = new Map([...container.querySelectorAll('.segment[data-id]')].map(el => [Number(el.dataset.id), el]));
  const fragment = document.createDocumentFragment();
  for (const s of segments) {
    const signature = JSON.stringify([s.text,s.translation,s.error,s.translation_ms,s.translation_skipped]);
    let item = existing.get(s.id);
    if (!item || item.dataset.signature !== signature) {
      item = document.createElement('section'); item.className = 'segment'; item.id = `segment-${s.id}`;
      item.dataset.id = s.id; item.dataset.signature = signature;
      item.innerHTML = `<div class="segment-top"><span class="segment-time">${clock(s.start_ms)}</span><span class="segment-kind">${state.meeting.provider === 'demo' ? '演示样本' : s.audio_path ? '语音识别' : '文字输入'}</span>${s.audio_path ? `<button class="icon-button segment-play" data-audio="${s.id}" aria-label="播放第 ${s.id} 句的来源录音" title="播放来源录音；同片段内的句子共用录音">${icon('play')}</button>` : ''}</div><p class="segment-text">${escapeHTML(s.text)}</p>${s.translation_skipped ? '' : `<p class="segment-translation${s.error ? ' error' : !s.translation ? ' pending' : ''}">${escapeHTML(s.error ? `翻译失败：${s.error}` : s.translation || '正在翻译…')}</p>`}`;
    }
    fragment.append(item);
  }
  for (const draft of state.drafts.values()) {
    if (state.filter && !String(draft.text || '').toLowerCase().includes(state.filter)) continue;
    const item = document.createElement('section'); item.className = 'segment draft';
    item.dataset.utterance = draft.utterance_id;
    item.innerHTML = `<div class="segment-top"><span class="segment-time">${clock(draft.start_ms)}</span><span class="segment-kind">${draft.pending ? '确认中' : '实时转写'}</span></div><p class="segment-text">${escapeHTML(draft.text || '正在识别…')}</p>`;
    fragment.append(item);
  }
  container.replaceChildren(fragment);
  if (!container.childElementCount) container.innerHTML = '<p class="no-results">没有找到匹配的内容</p>';
  if ($('auto-scroll').checked && !state.filter) container.scrollTop = container.scrollHeight;
  else container.scrollTop = oldTop;
}
function renderNotes() {
  const m = state.meeting;
  if (!m) return;
  if (m.summaries.length) {
    $('notes').innerHTML = m.summaries.map((s,index) => `<section class="note-card"><div class="note-card-head"><strong>段落 ${String(index+1).padStart(2,'0')}</strong><span>${m.provider === 'demo' ? '原文摘录 · 演示' : `${Math.round(s.latency_ms)} ms`}</span></div><div class="note-content">${escapeHTML(s.content)}</div><div class="source-links"><span>查看原文</span>${s.segment_ids.map(id => `<button data-source="${id}">#${id}</button>`).join('')}</div></section>`).join('');
  }
  const covered = new Set(m.summaries.flatMap(s => s.segment_ids));
  const remaining = m.segments.filter(s => !covered.has(s.id)).length;
  $('summary-status').innerHTML = `<i></i>${state.summaryRunning ? '生成中…' : remaining ? `${remaining} 句等待整理` : m.summaries.length ? '已生成' : '等待原文'}`;
  $('summary-button').disabled = !state.connected || !remaining || state.summaryRunning || state.finishing || m.provider !== state.health?.provider;
}
function renderMetrics(metrics) {
  if (!metrics) return;
  $('queue-count').textContent = metrics.pending;
  const samples = metrics.samples || [];
  $('queue-count').title = `正在执行：${metrics.active || '空闲'}；已完成 ${metrics.completed} 个任务。排队不抢占正在执行的任务。`;
  if (samples.length) $('latency').title = `最近任务排队 ${Math.round(samples.at(-1).queue_ms)} ms。此处显示推理耗时，非端到端字幕延迟。`;
}
function renderControls() {
  if (!state.health) return;
  const real = state.health.provider === 'qwen';
  $('demo-button').hidden = real;
  $('demo-button').disabled = state.finishing || (state.meeting && live() && !state.connected);
  $('demo-button').innerHTML = `${icon(state.demoPlaying ? 'pause' : 'play')}<span>${state.demoPlaying ? '暂停示例' : '播放示例'}</span>`;
  $('mic-button').disabled = !real || state.preparing || state.finishing || (state.meeting && live() && !state.connected);
  $('mic-button').innerHTML = `${icon(state.recording ? 'pause' : 'mic')}<span>${state.preparing ? '准备中…' : state.recording ? '暂停录音' : '开始录音'}</span>`;
  $('mic-button').title = real ? '使用本机麦克风，音频发送到当前后端识别' : '演示模式不识别麦克风，请配置 Qwen 模式';
  $('upload-button').disabled = !real || state.recording || state.preparing || state.finishing;
  $('finish-button').disabled = !live() || !state.connected || state.preparing || state.finishing;
  $('retranslate-button').hidden = !real;
  $('retranslate-button').disabled = !state.connected || !state.meeting?.segments.length || state.finishing || state.preparing || state.recording || state.retranslating || state.meeting?.provider !== state.health.provider;
  $('retranslate-button').textContent = state.retranslating ? '重译中…' : '重新翻译';
  $('new-meeting').disabled = state.recording || state.preparing || state.demoPlaying || state.finishing;
  $('text-input').disabled = state.finishing || state.preparing || state.recording;
  $('text-form').querySelector('button').disabled = state.finishing || state.preparing || state.recording;
  $('recording-icon').classList.toggle('recording', state.recording || state.demoPlaying);
  $('recording-title').textContent = state.preparing ? '准备录音' : state.finishing ? '处理中' : state.recording ? '录音中' : state.demoPlaying ? '示例播放中' : '未录音';
  $('recording-description').textContent = state.preparing ? '正在准备模型' : state.finishing ? '等待现有任务完成' : state.demoPlaying ? '预设样本' : '';
}
function pauseDemo() {
  clearInterval(state.demoTimer); state.demoTimer = null; state.demoPlaying = false;
  renderControls(); refreshHistory().catch(() => {});
}
async function toggleDemo() {
  if (state.demoPlaying) { pauseDemo(); return; }
  if (state.demoBusy) return;
  state.demoBusy = true;
  try {
    if (state.health.provider !== 'demo') throw new Error('当前使用实际模型。');
    await ensureMeeting();
    state.demoPlaying = true; renderControls();
    const tick = () => {
      if (!state.demoPlaying || !live()) { pauseDemo(); return; }
      send({type:'demo_next'});
    };
    tick(); state.demoTimer = setInterval(() => { try { tick(); } catch(e) { pauseDemo(); toast(e.message,true); } }, 1600);
  } finally { state.demoBusy = false; }
}

// Cumulative audio previews produce rolling subtitles while capture continues.
// Final ASR is split into sentences; previews never trigger translation/minutes.
async function startMicrophone() {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new Error('麦克风需要 localhost 或 HTTPS，请通过 http://localhost:8765 打开。');
  if (state.health.provider !== 'qwen') throw new Error('请先启用 Qwen 模式。');
  await ensureMeeting();
  state.preparing = true; renderControls();
  let context, stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true},video:false});
    context = new AudioContext();
    await context.resume();
    const prepared = await api('api/asr/prepare', {method:'POST', signal:AbortSignal.timeout(180000)});
    for (const warning of prepared.warnings || []) toast(warning,true);
    if (!state.connected || !live()) throw new Error('连接已断开，请重新开始录音。');
    await context.audioWorklet.addModule(url('static/pcm-worklet.js'));
    await context.resume();
    const source = context.createMediaStreamSource(stream);
    const worklet = new AudioWorkletNode(context,'pcm-processor');
    const mute = context.createGain(); mute.gain.value = 0;
    source.connect(worklet); worklet.connect(mute); mute.connect(context.destination);
    const recorder = {stream,context,source,worklet,mute,rms:0,flushResolve:null};
    recorder.chunker = new SpeechChunker({cursor:state.elapsed,
      onPreview:(pcm,start,id)=>sendAudio(recorder,pcm,start,id,true),
      onFinal:(pcm,start,id)=>sendAudio(recorder,pcm,start,id,false)});
    state.microphone = recorder; state.recording = true; state.started = performance.now();
    worklet.port.onmessage = ({data}) => {
      if (data.flushed) { recorder.flushResolve?.(); return; }
      if (!data.pcm) return;
      const pcm = new Int16Array(data.pcm);
      recorder.chunker.push(pcm); recorder.rms = recorder.chunker.rms;
    };
    renderControls();
  } catch(error) {
    stream?.getTracks().forEach(track => track.stop());
    if (context && context.state !== 'closed') await context.close();
    state.microphone = null; state.recording = false;
    throw error;
  } finally {
    state.preparing = false; renderControls();
  }
}
function sendAudio(recorder, pcm, start, id, preview) {
  if (preview && state.audioPending >= 3) return;
  if (!preview && state.audioPending >= 3) {
    // Stop capture and preserve this last chunk: never silently drop recognized speech.
    toast('处理速度暂时跟不上录音，已暂停采集。现有片段将继续处理，请等待后再开始。',true);
    queueMicrotask(() => stopMicrophone().catch(() => {}));
  }
  let bytes = ''; const values = new Uint8Array(pcm.buffer);
  for (let i=0;i<values.length;i+=8192) bytes += String.fromCharCode(...values.subarray(i,i+8192));
  try {
    send({type:preview?'audio_preview':'audio',utterance_id:id,pcm16:btoa(bytes),sample_rate:16000,start_ms:Math.round(start)});
    const current = state.drafts.get(id) || {utterance_id:id,start_ms:start,text:''};
    state.drafts.set(id,{...current,pending:!preview}); renderTranscript();
    if (!preview) state.audioPending++;
  } catch(error) { toast(`该音频片段未发送：${error.message}`,true); }
}
function stopMicrophone() {
  const recorder = state.microphone;
  if (!recorder) return Promise.resolve();
  if (!recorder.stopPromise) recorder.stopPromise = finishMicrophone(recorder);
  return recorder.stopPromise;
}
async function finishMicrophone(recorder) {
  recorder.stopping = true;
  recorder.stream.getTracks().forEach(track => track.stop());
  await new Promise(resolve => {
    recorder.flushResolve=resolve; recorder.worklet.port.postMessage('flush');
    setTimeout(resolve,250);
  });
  recorder.chunker.flush();
  state.elapsed = Math.max(elapsed(), recorder.chunker.cursor / 16);
  state.recording=false; state.microphone=null;
  recorder.source.disconnect(); recorder.worklet.disconnect(); recorder.mute.disconnect();
  if (recorder.context.state !== 'closed') await recorder.context.close();
  renderControls();
}

$('new-meeting').onclick = () => {
  $('new-form').reset();
  $('new-title').value = '新会议';
  $('new-dialog').showModal();
};
$('new-form').onsubmit = async event => {
  event.preventDefault(); const button = event.target.querySelector('[type=submit]'); button.disabled = true;
  try {
    await createMeeting(Object.fromEntries(new FormData(event.target)));
    $('new-dialog').close();
    // Empty views are reset only when opening a new, empty meeting.
    location.hash = state.meeting.id;
  } catch(error) { toast(error.message,true); }
  finally { button.disabled = false; }
};
$('history').onclick = event => {
  const button = event.target.closest('[data-meeting]');
  if (button) openMeeting(button.dataset.meeting).catch(e => toast(e.message,true));
};
$('workspace-link').onclick = () => window.scrollTo({top:0,behavior:'smooth'});
$('demo-button').onclick = $('empty-demo').onclick = () => toggleDemo().catch(e => toast(e.message,true));
$('mic-button').onclick = async () => {
  $('mic-button').disabled=true;
  try { if(state.recording) await stopMicrophone(); else await startMicrophone(); }
  catch(e) { toast(e.name === 'NotAllowedError' ? '麦克风权限被拒绝，请在浏览器地址栏中允许访问。' : e.message,true); }
  finally { renderControls(); }
};
$('finish-button').onclick = async () => {
  state.finishing=true; pauseDemo(); renderControls();
  try { await stopMicrophone(); send({type:'finish'}); }
  catch(e) { state.finishing=false;renderControls();toast(e.message,true); }
};
$('summary-button').onclick = () => { try {send({type:'summary'});} catch(e){toast(e.message,true);} };
$('retranslate-button').onclick = () => { try {send({type:'retranslate'});} catch(e){toast(e.message,true);} };
$('text-form').onsubmit = async event => {
  event.preventDefault(); const text=$('text-input').value.trim(); if(!text) return;
  const button=event.target.querySelector('button'); button.disabled=true;
  try { await ensureMeeting(); send({type:'text',text,start_ms:Math.round(elapsed()),end_ms:Math.round(elapsed())}); $('text-input').value=''; }
  catch(e){toast(e.message,true);} finally{renderControls();}
};
$('bilingual-tab').onclick = () => {
  $('transcript').classList.remove('original-only'); $('bilingual-tab').classList.add('active'); $('original-tab').classList.remove('active');
};
$('original-tab').onclick = () => {
  $('transcript').classList.add('original-only'); $('original-tab').classList.add('active'); $('bilingual-tab').classList.remove('active');
};
$('search-button').onclick = () => {
  $('search-wrap').hidden=!$('search-wrap').hidden;
  if (!$('search-wrap').hidden) $('transcript-search').focus();
  else { $('transcript-search').value='';state.filter='';renderTranscript(); }
};
$('transcript-search').oninput = event => {state.filter=event.target.value.toLowerCase();renderTranscript();};
$('notes').onclick = event => {
  const button=event.target.closest('[data-source]'); if(!button) return;
  state.filter='';$('transcript-search').value='';renderTranscript();
  const segment=$(`segment-${button.dataset.source}`);
  if(segment){$('auto-scroll').checked=false;segment.scrollIntoView({behavior:'smooth',block:'center'});segment.classList.add('highlight');setTimeout(()=>segment.classList.remove('highlight'),2200);}
};
$('transcript').onclick = event => {
  const button=event.target.closest('[data-audio]'); if(!button) return;
  const segment=button.closest('.segment');
  let player=segment.querySelector('audio');
  if(!player){player=document.createElement('audio');player.controls=true;player.className='audio-player';player.src=url(`api/meetings/${state.meeting.id}/audio/${button.dataset.audio}`);segment.append(player);}
  player.play().catch(e=>toast(`播放失败：${e.message}`,true));
};
$('export-button').onclick=()=>$('export-dialog').showModal();
document.querySelectorAll('[data-export]').forEach(button=>button.onclick=()=>{
  const link=document.createElement('a');link.href=url(`api/meetings/${state.meeting.id}/export?format=${button.dataset.export}`);link.download='';link.click();$('export-dialog').close();
});
document.querySelectorAll('.close-dialog').forEach(button=>button.onclick=()=>button.closest('dialog').close());
$('settings-button').onclick=async()=>{
  try{
    const health=await api('api/health');
    const pairs=[['模式',health.provider==='demo'?'演示':'模型'],['设备',health.device],['语音模型',health.asr_model],['翻译模型',health.translation_model],['翻译后端',health.translation_backend],['纪要模型',health.llm_model],['纪要方式',health.summary_style==='extractive'?'抽取原文':'概括'],['语音模型状态',health.models_loaded.asr?'已加载':'未加载'],['翻译模型状态',health.models_loaded.translation?'已加载':'未加载'],['纪要模型状态',health.models_loaded.llm?'已加载':'未加载']];
    $('runtime-details').innerHTML=pairs.map(([key,value])=>`<div class="runtime-detail"><span>${escapeHTML(key)}</span><strong>${escapeHTML(value)}</strong></div>`).join('');$('settings-dialog').showModal();
  }catch(e){toast(e.message,true);}
};
$('upload-button').onclick=()=>$('audio-file').click();
$('audio-file').onchange=async event=>{
  const file=event.target.files[0];if(!file)return;
  try{
    if(file.size>20*1024*1024)throw new Error('文件不能超过 20MB。');
    await ensureMeeting();const body=new FormData();body.append('file',file);
    const result=await api(`api/meetings/${state.meeting.id}/upload`,{method:'POST',body});
    toast(`录音已接收，检测到 ${result.chunk_count} 个语音片段。`);
    if(result.forced_chunks)toast(`其中 ${result.forced_chunks} 个连续语音片段触发了 30 秒上限，已进行兜底切分。`);
  }
  catch(e){toast(e.message,true);}finally{event.target.value='';}
};

// Reset empty panels on meeting switches without reloading the entire page.
const emptyTranscript=$('transcript').innerHTML, emptyNotes=$('notes').innerHTML;
let renderedMeeting=null;
const originalRenderMeeting=renderMeeting;
renderMeeting=function(){
  if(state.meeting?.id!==renderedMeeting){
    renderedMeeting=state.meeting?.id;$('transcript').innerHTML=emptyTranscript;$('notes').innerHTML=emptyNotes;
    const demo=$('empty-demo');if(demo)demo.onclick=()=>toggleDemo().catch(e=>toast(e.message,true));
    if(demo)demo.hidden=state.health?.provider!=='demo';
    state.filter='';$('transcript-search').value='';
  }
  originalRenderMeeting();
};

const canvas=$('waveform'),ctx=canvas.getContext('2d');
function draw(){
  ctx.clearRect(0,0,canvas.width,canvas.height);
  const rms=state.microphone?.rms||0;
  const active=state.recording;
  ctx.strokeStyle=active?'#71717a':'#d4d4d8';ctx.lineWidth=3;ctx.lineCap='round';
  const t=performance.now()/150;
  for(let i=0;i<28;i++){
    const h=active?Math.max(2,Math.min(37,rms*350)*(Math.sin(t+i*.7)*.35+.65)):3+Math.sin(i*.8)**2*7;
    const x=i*8+2;ctx.beginPath();ctx.moveTo(x,22-h/2);ctx.lineTo(x,22+h/2);ctx.stroke();
  }
  const latest=state.meeting?.segments.at(-1)?.end_ms||0;
  $('timer').textContent=clock(state.recording?elapsed():Math.max(state.elapsed,latest));
  requestAnimationFrame(draw);
}
requestAnimationFrame(draw);
window.addEventListener('beforeunload',event=>{
  if(state.recording||state.preparing||state.demoPlaying||state.finishing){event.preventDefault();event.returnValue='';}
});
async function init(){
  try{
    state.health=await api('api/health');
    const real=state.health.provider==='qwen';
    $('mode-badge').textContent=real?'本地模型':'演示模式';$('mode-badge').classList.toggle('real',real);
    $('runtime-label').textContent=real?`${state.health.device.toUpperCase()}`:'演示';
    $('mode-description').textContent=real?'':'演示使用预设样本';
    $('summary-rule').textContent=`每 ${state.health.summary_every} 句${real && state.health.summary_style==='extractive'?'提取重点':'整理一次'}`;
    $('empty-demo').hidden=real;
    setConnection(false,'服务已就绪');renderControls();
    const meetings=await api('api/meetings');
    const last=localStorage.getItem('tingjian.lastMeeting');
    if(last&&meetings.some(m=>m.id===last))await openMeeting(last);
    else await refreshHistory();
  }catch(e){setConnection(false,'服务连接失败');toast(e.message,true);}
}
init();
