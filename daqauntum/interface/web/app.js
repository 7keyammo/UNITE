const S = { status:null, call:null, recognition:null, listening:false, recognitionMode:"call", voice:null, localRecorder:null, wakeLoop:false, universe:null, workspaceId:null,
  activeTurn:null, streamAbort:null, speechQueue:[], speechBuffer:"", ttsSpeaking:false, streamDone:true, streamStartedAt:0,
  duplexSocket:null, duplexReady:false, duplexSession:null, duplexRecorder:null, duplexLive:null, duplexLastUser:"", duplexConnectedAt:0,
  screenStream:null, screenWatchTimer:null, latestFrameId:null };
const $ = id => document.getElementById(id);

async function api(path, method='GET', body=null){
  const opt={method,headers:{}}; if(body){opt.headers['Content-Type']='application/json';opt.body=JSON.stringify(body)}
  const r=await fetch(path,opt); const data=await r.json();
  if(!r.ok || data.ok===false) throw new Error(data.error || data.result?.message || `HTTP ${r.status}`);
  return data;
}
async function streamRequest(path, body, onEvent){
  const controller=new AbortController();S.streamAbort=controller;S.streamStartedAt=performance.now();
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal});
  if(!r.ok)throw new Error(`HTTP ${r.status}`);
  if(!r.body)throw new Error('Streaming response body is unavailable in this browser');
  const reader=r.body.getReader();const decoder=new TextDecoder();let buffer='';
  try{
    while(true){const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});let nl;while((nl=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,nl).trim();buffer=buffer.slice(nl+1);if(!line)continue;const event=JSON.parse(line);if(event.type==='error')throw new Error(event.error||'Streaming error');await onEvent(event)}}
    if(buffer.trim())await onEvent(JSON.parse(buffer.trim()));
  }finally{if(S.streamAbort===controller)S.streamAbort=null}
}

function duplexEnabled(){return !!($('fullDuplex')?.checked && S.status?.runtime?.modules?.duplex!==false && S.status?.duplex?.enabled)}
function updateDuplexChip(){const chip=$('duplexChip');if(!chip)return;const configured=!!S.status?.duplex?.enabled;if(S.duplexReady){chip.textContent='DUPLEX: live';chip.className='voice-chip ready duplex-live'}else if(configured){chip.textContent='DUPLEX: ready';chip.className='voice-chip ready'}else{chip.textContent='DUPLEX: HTTP fallback';chip.className='voice-chip duplex-fallback'}}
function duplexSend(obj){if(!S.duplexSocket||S.duplexSocket.readyState!==WebSocket.OPEN)throw new Error('Full-duplex session is not connected');S.duplexSocket.send(JSON.stringify(obj))}
async function ensureDuplex(){
  if(!duplexEnabled())return false;
  if(S.duplexSocket&&S.duplexSocket.readyState===WebSocket.OPEN)return true;
  if(!S.status?.duplex?.port)await refreshStatus();
  const d=S.status?.duplex||{};if(!d.enabled||!d.port)return false;
  const host=(location.hostname==='localhost'||location.hostname==='127.0.0.1')?location.hostname:(d.host||location.hostname);
  const url=`ws://${host}:${d.port}`;
  await new Promise((resolve,reject)=>{let settled=false;const ws=new WebSocket(url);ws.binaryType='arraybuffer';S.duplexSocket=ws;
    const timer=setTimeout(()=>{if(!settled){settled=true;try{ws.close()}catch(e){};reject(new Error('Realtime WebSocket connection timed out'))}},3500);
    ws.onopen=()=>{if(settled)return;settled=true;clearTimeout(timer);S.duplexReady=true;S.duplexConnectedAt=performance.now();updateDuplexChip();resolve()};
    ws.onmessage=e=>handleDuplexEvent(e).catch(err=>toast(err.message,true));
    ws.onerror=()=>{if(!settled){settled=true;clearTimeout(timer);reject(new Error('Realtime WebSocket connection failed'))}};
    ws.onclose=()=>{S.duplexReady=false;S.duplexSession=null;S.duplexSocket=null;updateDuplexChip()};
  });
  return true;
}
function closeDuplex(){if(S.duplexSocket){try{S.duplexSocket.close(1000,'call ended')}catch(e){}}S.duplexSocket=null;S.duplexReady=false;S.duplexSession=null;updateDuplexChip()}
function beginDuplexLive(){
  const box=$('callTranscript');if(box.querySelector('.empty'))box.innerHTML='';
  const d=document.createElement('div');d.className='transcript-turn assistant streaming';d.innerHTML='<strong>DaQauntum</strong><div></div><small class="stream-meta">thinking…</small>';box.appendChild(d);box.scrollTop=box.scrollHeight;
  S.duplexLive={node:d,text:d.querySelector('div'),meta:d.querySelector('small'),full:''};return S.duplexLive;
}
async function handleDuplexEvent(e){
  if(typeof e.data!=='string')return;let ev;try{ev=JSON.parse(e.data)}catch(err){return}
  if(ev.type==='hello'){S.duplexSession=ev.session;updateDuplexChip();return}
  if(ev.type==='session.ready'){S.duplexSession=ev.session;updateDuplexChip();return}
  if(ev.type==='transcript.partial'){const t=String(ev.text||'').trim();if(t){$('livePartial').textContent=t;$('callHeading').textContent=`Listening: ${t}`};return}
  if(ev.type==='transcript.final'){const t=String(ev.text||'').trim();$('livePartial').textContent=ev.stt_ms!=null?`STT ${Number(ev.stt_ms).toFixed(0)} ms · ${Number(ev.audio_ms||0).toFixed(0)} ms audio`:'';if(t)$('callHeading').textContent='DaQauntum is thinking…';return}
  if(ev.type==='user.final'){
    const t=String(ev.text||'').trim();if(t&&t!==S.duplexLastUser){appendTranscript('user',t);S.duplexLastUser=t}S.activeTurn=ev.turn_id||S.activeTurn;S.duplexLastTurnId=S.activeTurn;S.firstAudioReported=null;S.streamStartedAt=performance.now();S.streamDone=false;S.speechBuffer='';S.speechQueue=[];setDQState('thinking');beginDuplexLive();return
  }
  if(ev.type==='start'){S.activeTurn=ev.turn_id||S.activeTurn;S.duplexLastTurnId=S.activeTurn;S.firstAudioReported=null;$('talkBtn').disabled=false;$('talkBtn').textContent='↯ Interrupt & Talk';if(!S.duplexLive)beginDuplexLive();return}
  if(ev.type==='meta'){if(!S.duplexLive)beginDuplexLive();const first=Math.max(0,performance.now()-S.streamStartedAt);S.duplexLive.meta.textContent=`${ev.provider||'—'} / ${ev.model||'—'} · first token ${first.toFixed(0)} ms`;return}
  if(ev.type==='delta'){if(!S.duplexLive)beginDuplexLive();const delta=ev.text||'';S.duplexLive.full+=delta;S.duplexLive.text.textContent=S.duplexLive.full;$('callTranscript').scrollTop=$('callTranscript').scrollHeight;queueSpeechDelta(delta,false);return}
  if(ev.type==='done'||ev.type==='interrupted'){
    S.streamDone=true;S.activeTurn=null;queueSpeechDelta('',true);if(ev.call)S.call=ev.call;const rt=ev.realtime||ev.result?.realtime||{};
    if(ev.latency)renderTurnLatency(ev.latency);
    if(S.duplexLive)S.duplexLive.meta.textContent=`${ev.type==='interrupted'?'interrupted · ':''}TTFT ${Number(rt.time_to_first_token_ms||0).toFixed(0)} ms · total ${Number(rt.duration_ms||0).toFixed(0)} ms`;
    S.duplexLive=null;if(S.call)renderCall();await refreshStatus();if($('autoSpeak').checked)pumpSpeechQueue();return
  }
  if(ev.type==='interrupted'){S.activeTurn=null;stopSpeaking();return}
  if(ev.type==='session.ended'){if(ev.call)S.call=ev.call;setCallActive(false);renderCall();return}
  if(ev.type==='error'){toast(ev.error||'Realtime session error',true);return}
}
function pcm16Chunk(floatData,inRate,outRate=16000){const data=downsample(floatData,inRate,outRate);const out=new ArrayBuffer(data.length*2);const v=new DataView(out);for(let i=0;i<data.length;i++){const x=Math.max(-1,Math.min(1,data[i]));v.setInt16(i*2,x<0?x*0x8000:x*0x7fff,true)}return out}
async function startDuplexRecording(){
  if(S.duplexRecorder||S.listening)return;await ensureDuplex();if(!S.duplexReady)throw new Error('Full-duplex transport unavailable');if(!navigator.mediaDevices?.getUserMedia)throw new Error('Microphone capture is unavailable in this browser');
  const stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});const Ctx=window.AudioContext||window.webkitAudioContext;const ctx=new Ctx();const source=ctx.createMediaStreamSource(stream);const proc=ctx.createScriptProcessor(2048,1,1);
  const rec={stream,ctx,source,proc,startedAt:performance.now(),voiceStarted:false,lastVoice:performance.now(),interruptSent:false,stopping:false};S.duplexRecorder=rec;duplexSend({type:'audio.start',sample_rate:16000,channels:1});setListeningUi(true);
  proc.onaudioprocess=e=>{if(rec.stopping||!S.duplexReady)return;const data=new Float32Array(e.inputBuffer.getChannelData(0));let sum=0;for(let i=0;i<data.length;i++)sum+=data[i]*data[i];const rms=Math.sqrt(sum/Math.max(1,data.length));const now=performance.now();
    try{S.duplexSocket.send(pcm16Chunk(data,ctx.sampleRate,16000))}catch(err){}
    if(rms>0.018){if(!rec.voiceStarted){rec.voiceStarted=true;rec.interruptSent=false;if(S.activeTurn&&$('bargeIn').checked&&!rec.interruptSent){rec.interruptSent=true;try{duplexSend({type:'interrupt'})}catch(err){};stopSpeaking()}}rec.lastVoice=now}
    const pause=Number($('turnPause')?.value||700);if(rec.voiceStarted&&now-rec.lastVoice>pause&&now-rec.startedAt>450){try{duplexSend({type:'audio.commit'})}catch(err){};rec.voiceStarted=false;rec.interruptSent=false;rec.startedAt=now;$('callHeading').textContent='Transcribing locally…'}
    else if(!rec.voiceStarted&&now-rec.startedAt>1600){try{duplexSend({type:'audio.clear'})}catch(err){};rec.startedAt=now}
  };
  source.connect(proc);proc.connect(ctx.destination);
}
async function stopDuplexRecording(commit=false){const rec=S.duplexRecorder;if(!rec)return;rec.stopping=true;S.duplexRecorder=null;if(commit&&rec.voiceStarted&&S.duplexReady){try{duplexSend({type:'audio.commit'})}catch(e){}}try{rec.proc.disconnect();rec.source.disconnect()}catch(e){}rec.stream.getTracks().forEach(t=>t.stop());try{await rec.ctx.close()}catch(e){}setListeningUi(false)}
async function attachDuplexToCall(){if(!S.call||!duplexEnabled())return false;try{await ensureDuplex();duplexSend({type:'session.attach',call_id:S.call.id,interaction_mode:'voice_call',sample_rate:16000,channels:1});return true}catch(e){toast(`Duplex fallback: ${e.message}`,true);return false}}

function toast(msg,error=false){const t=$('toast');t.textContent=msg;t.className='toast show'+(error?' error':'');setTimeout(()=>t.className='toast',2600)}
function esc(s=''){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function setView(name){document.querySelectorAll('.nav').forEach(b=>b.classList.toggle('active',b.dataset.view===name));document.querySelectorAll('.view').forEach(v=>v.classList.toggle('active',v.id===`view-${name}`));if(name==='memory') loadMemory();if(name==='learning') loadLearning();if(name==='connect'){loadConnectors();loadObsidian()}if(name==='workspaces') loadWorkspaces();if(name==='integrations') loadIntegrations();if(name==='perception') loadPerception();if(name==='presence') loadPresence();if(name==='events') loadEvents();if(name==='devices') loadDevices();if(name==='demo') loadDemo();if(name==='system') loadSystem();if(name==='universe') loadUniverse()}
document.querySelectorAll('.nav').forEach(b=>b.addEventListener('click',()=>setView(b.dataset.view)));

function modelText(x){return x?`${x.provider} / ${x.model}`:'—'}
async function refreshStatus(){
  try{
    const d=await api('/api/status');S.status=d.status;const st=d.status;$('versionLabel').textContent=`v${st.version}`;$('onlineDot').style.background='#7cffbe';
    $('plannerModel').textContent=modelText(st.roles.planner);$('executorModel').textContent=modelText(st.roles.executor);$('criticModel').textContent=modelText(st.roles.critic);$('permissionLevel').textContent=`L${st.permission_level}`;
    $('projectInput').value=st.memory?.active_project||'';renderApprovals(st.pending_approvals||[]);
    const rt=st.runtime||{};$('operationMode').value=rt.operation_mode||'auto';$('cognitionMode').value=rt.cognition_mode||'auto';if($('bargeIn'))$('bargeIn').checked=rt.modules?.barge_in!==false;$('modePill').textContent=`${(rt.operation_mode||'auto').toUpperCase()} · ${(rt.cognition_mode||'auto').toUpperCase()}`;
    const a=st.universe?.atom||{};$('atomLevel').textContent=`LEVEL ${a.level||1}`;$('atomXp').textContent=`${a.experience||0} XP`;
    if(st.universe){S.universe=st.universe;window.DQUniverse?.setData(st.universe)}updateDuplexChip();renderEventsBadge(st.events)
  }catch(e){$('onlineDot').style.background='#ff7787';toast(e.message,true)}
}
function renderApprovals(ids){$('approvalCount').textContent=ids.length;const box=$('approvalList');if(!ids.length){box.className='list empty';box.innerHTML='No pending actions.';return}box.className='list';box.innerHTML=ids.map(id=>`<div class="approval-item"><strong>${esc(id)}</strong><div class="dim">State-changing action waiting for approval.</div><button onclick="approveAction('${esc(id)}')">Approve</button></div>`).join('')}
async function approveAction(id){try{const d=await api('/api/approve','POST',{approval_id:id});toast(d.result.message||'Approved');await refreshStatus();if(S.call) await refreshCall()}catch(e){toast(e.message,true)}} window.approveAction=approveAction;

$('setProjectBtn').addEventListener('click',async()=>{try{const d=await api('/api/project','POST',{project:$('projectInput').value.trim()});toast(d.project?`Project: ${d.project}`:'Project cleared');await refreshStatus()}catch(e){toast(e.message,true)}});

async function setRuntimeModes(){try{const d=await api('/api/runtime/modes','POST',{operation_mode:$('operationMode').value,cognition_mode:$('cognitionMode').value});S.status=d.status;toast(`Compute ${d.runtime.operation_mode} · cognition ${d.runtime.cognition_mode}`);await refreshStatus();if(document.getElementById('view-universe').classList.contains('active'))await loadUniverse()}catch(e){toast(e.message,true)}}
$('operationMode').addEventListener('change',setRuntimeModes);$('cognitionMode').addEventListener('change',setRuntimeModes);
function setDQState(state){document.body.dataset.dqState=state||'idle'}

function addChat(who,text,meta=''){const log=$('chatLog');const div=document.createElement('div');div.className=`message ${who==='You'?'user':'assistant'}`;div.innerHTML=`<div class="who">${esc(who)}</div><div class="bubble"><span class="bubble-text">${esc(text)}</span>${meta?`<div class="task-meta">${esc(meta)}</div>`:''}</div>`;log.appendChild(div);log.scrollTop=log.scrollHeight;return div}
function addStreamingChat(){const div=addChat('DaQauntum','');const bubble=div.querySelector('.bubble-text');const meta=document.createElement('div');meta.className='task-meta';div.querySelector('.bubble').appendChild(meta);return {div,bubble,meta}}
$('chatForm').addEventListener('submit',async e=>{e.preventDefault();const input=$('chatInput');const text=input.value.trim();if(!text)return;if(S.activeTurn)await interruptActiveTurn(false);addChat('You',text);input.value='';input.disabled=true;setDQState('thinking');$('turnTelemetry').innerHTML='<span>connecting…</span><span>streaming</span>';const live=addStreamingChat();let full='';let result=null;try{
  await streamRequest('/api/chat/stream',{text},async ev=>{if(ev.type==='start'){S.activeTurn=ev.turn_id;$('turnTelemetry').innerHTML='<span>thinking…</span><span>realtime session</span>'}
    else if(ev.type==='meta'){const first=Math.max(0,performance.now()-S.streamStartedAt);$('turnTelemetry').innerHTML=`<span>${esc(ev.effective_cognition||'—')} · ${esc(ev.provider||'—')}</span><span>first token ${first.toFixed(0)} ms</span>`}
    else if(ev.type==='delta'){full+=ev.text||'';live.bubble.textContent=full;$('chatLog').scrollTop=$('chatLog').scrollHeight}
    else if(ev.type==='done'||ev.type==='interrupted'){result=ev.result||{};S.activeTurn=null;const rt=ev.realtime||result.realtime||{};live.meta.textContent=`${result.agent||'general'} • ${result.provider||'—'}/${result.model||'—'} • ${result.effective_cognition||'—'}${ev.type==='interrupted'?' • interrupted':''}`;$('agentLabel').textContent=`agent: ${result.agent||'—'}`;$('turnTelemetry').innerHTML=`<span>${esc(result.effective_cognition||'—')} · ${esc(result.provider||'—')}</span><span>TTFT ${Number(rt.time_to_first_token_ms||0).toFixed(0)} ms · total ${Number(rt.duration_ms||result.latency_ms||0).toFixed(0)} ms</span>`}
  });await refreshStatus();
}catch(err){if(err.name!=='AbortError'){live.bubble.textContent=full||`Error: ${err.message}`;toast(err.message,true)}}finally{S.activeTurn=null;setDQState('idle');input.disabled=false;input.focus()}});
$('chatInput').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('chatForm').requestSubmit()}});

// Call mode -----------------------------------------------------------------
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

async function loadVoiceStatus(){
  try{
    const d=await api('/api/voice/status'); S.voice=d.voice;
    const stt=d.voice?.stt||{}; const tts=d.voice?.tts||{};
    const localStt=!!stt.available; const localTts=!!tts.available;
    $('sttChip').textContent=`STT: ${localStt?stt.backend:'browser fallback'}`;$('sttChip').className='voice-chip '+(localStt?'ready':'fallback');
    $('ttsChip').textContent=`TTS: ${localTts?tts.backend:'browser fallback'}`;$('ttsChip').className='voice-chip '+(localTts?'ready':'fallback');
    $('wakeChip').textContent=`WAKE: ${$('wakeEnabled').checked?'armed':'off'}`;$('wakeChip').className='voice-chip '+($('wakeEnabled').checked?'ready':'');updateDuplexChip();
    const browserStt=!!SpeechRecognition;
    $('speechSupport').textContent=localStt
      ? `Local ${stt.backend} transcription ready. Audio stays on this computer.`
      : (browserStt?'Local STT is not configured yet; browser speech recognition is available as fallback.':'Local STT is not configured. Typed Call Mode is available.');
  }catch(e){$('speechSupport').textContent=`Voice status unavailable: ${e.message}`}
}

if(SpeechRecognition){
  S.recognition=new SpeechRecognition();S.recognition.continuous=false;S.recognition.interimResults=true;S.recognition.lang='en-US';
  S.recognition.onstart=()=>setListeningUi(true);
  S.recognition.onend=()=>{setListeningUi(false);if(S.recognitionMode==='wake'&&$('wakeEnabled').checked&&!S.call)setTimeout(startWakeListening,350)};
  S.recognition.onerror=e=>{setListeningUi(false);if(e.error!=='no-speech')toast(`Speech recognition: ${e.error}`,true)};
  S.recognition.onresult=e=>{let final='';let interim='';for(let i=e.resultIndex;i<e.results.length;i++){const t=e.results[i][0].transcript;if(e.results[i].isFinal)final+=t;else interim+=t}
    if(S.recognitionMode==='call')$('callHeading').textContent=final||interim||'Listening…';
    if(final.trim())handleTranscript(final.trim(),S.recognitionMode);
  };
}

function setListeningUi(active){S.listening=active;$('talkBtn').classList.toggle('listening',active);$('talkBtn').textContent=active?'■ Stop':'🎙 Talk';setDQState(active?'listening':'idle')}
function strictLocalMode(){return (S.status?.runtime?.operation_mode||$('operationMode').value)==='local'}
function localSttReady(){return !!(S.voice?.stt?.available&&$('preferLocalVoice').checked)}
function localTtsReady(){return !!(S.voice?.tts?.available&&$('preferLocalVoice').checked)}

async function startCall(){
  if(S.call?.status==='active')return;
  stopWakeListening();
  const title=`DaQauntum Call ${new Date().toLocaleString()}`;const d=await api('/api/call/start','POST',{title});S.call=d.call;setCallActive(true);renderCall();toast('Call started');
  if(duplexEnabled())await attachDuplexToCall();
  if($('handsFree').checked)setTimeout(()=>startListening('call'),350);
}
$('startCallBtn').addEventListener('click',()=>startCall().catch(e=>toast(e.message,true)));

async function startListening(mode='call'){
  if(mode==='call'&&(!S.call||S.call.status!=='active'))return;
  if(S.listening)return;
  S.recognitionMode=mode;
  if(mode==='call'&&duplexEnabled()&&localSttReady()){try{await startDuplexRecording();return}catch(e){toast(`Full-duplex fallback: ${e.message}`,true)}}
  if(localSttReady()){
    try{await startLocalRecording(mode);return}catch(e){toast(`Local voice fallback: ${e.message}`,true)}
  }
  if(strictLocalMode()){if(mode==='call')toast('LOCAL compute mode will not use browser speech recognition. Configure local Whisper or type below.',true);return}
  if(!S.recognition){if(mode==='call')toast('No speech engine is available. Type into the call box below.',true);return}
  try{S.recognition.start()}catch(e){}
}

$('talkBtn').addEventListener('click',async()=>{
  if(S.activeTurn){if($('bargeIn').checked){await interruptActiveTurn(true)}else{toast('Barge-in is disabled.');}return}
  if(S.duplexRecorder){stopDuplexRecording(false)}else if(S.listening){stopAnyListening()}else{startListening('call')}
});
$('callTextForm').addEventListener('submit',e=>{e.preventDefault();const i=$('callTextInput');const t=i.value.trim();if(t){i.value='';sendCallTurn(t)}});

$('endCallBtn').addEventListener('click',async()=>{
  if(!S.call)return;try{
    stopAnyListening();await stopDuplexRecording(false);if(S.activeTurn)await interruptActiveTurn(false);$('endCallBtn').disabled=true;$('callHeading').textContent='Processing notes and tasks…';
    const d=await api('/api/call/end','POST',{session_id:S.call.id,process_tasks:$('autoProcessTasks').checked});S.call=d.call;setCallActive(false);renderCall();toast($('autoProcessTasks').checked?'Call processed; action items sent through DaQauntum':'Call processed and saved locally');
    await refreshStatus();closeDuplex();if($('wakeEnabled').checked)setTimeout(startWakeListening,500);
  }catch(e){toast(e.message,true);$('endCallBtn').disabled=false}
});
$('runAllTasksBtn').addEventListener('click',()=>runAllTasks(false));
async function runAllTasks(silent=false){if(!S.call)return;try{$('runAllTasksBtn').disabled=true;if(!silent)toast('Processing call tasks…');const d=await api('/api/call/tasks/run-all','POST',{session_id:S.call.id});S.call=d.call;renderCall();await refreshStatus();toast('Action items processed through DaQauntum')}catch(e){toast(e.message,true)}finally{$('runAllTasksBtn').disabled=false}}

async function interruptActiveTurn(beginListening=false){
  const id=S.activeTurn;S.streamDone=true;stopSpeaking();
  if(S.duplexReady){try{duplexSend({type:'interrupt'})}catch(e){}}
  if(id){try{await api('/api/realtime/interrupt','POST',{turn_id:id})}catch(e){}S.activeTurn=null}
  if(S.streamAbort){try{S.streamAbort.abort()}catch(e){}S.streamAbort=null}
  if(beginListening&&S.call?.status==='active')setTimeout(()=>startListening('call'),80);
}
async function stopSpeaking(){S.speechQueue=[];S.speechBuffer='';S.ttsSpeaking=false;if('speechSynthesis'in window)window.speechSynthesis.cancel();try{await api('/api/voice/stop','POST',{})}catch(e){}}
function queueSpeechDelta(delta,force=false){if(!$('autoSpeak').checked)return;S.speechBuffer+=(delta||'');let text=S.speechBuffer;const pieces=text.split(/(?<=[.!?])\s+/);if(!force&&pieces.length<2&&text.length<170)return;let ready=[];if(force){ready=pieces.filter(Boolean);S.speechBuffer=''}else{ready=pieces.slice(0,-1).filter(Boolean);S.speechBuffer=pieces.at(-1)||'';if(!ready.length&&text.length>=170){const cut=Math.max(text.lastIndexOf(',',170),text.lastIndexOf(' ',170));if(cut>60){ready=[text.slice(0,cut+1)];S.speechBuffer=text.slice(cut+1)}}}S.speechQueue.push(...ready.map(x=>x.trim()).filter(Boolean));pumpSpeechQueue()}
async function pumpSpeechQueue(){if(S.ttsSpeaking||!S.speechQueue.length)return;S.ttsSpeaking=true;setDQState('speaking');reportFirstAudio();while(S.speechQueue.length){const sentence=S.speechQueue.shift();if(!sentence)continue;try{await speakSentence(sentence)}catch(e){if(e.name!=='AbortError')toast(`TTS: ${e.message}`,true);break}}S.ttsSpeaking=false;setDQState('idle');if(S.streamDone&&!S.speechQueue.length&&S.call?.status==='active'&&$('handsFree').checked)setTimeout(()=>startListening('call'),120)}
async function speakSentence(text){const clean=String(text).replace(/\[[^\]]+\]/g,'').trim().slice(0,900);if(!clean)return;if(localTtsReady()){try{await api('/api/voice/speak','POST',{text:clean});return}catch(e){if(strictLocalMode())throw e}}
  if(strictLocalMode())return;if(!('speechSynthesis'in window))return;await new Promise(resolve=>{const u=new SpeechSynthesisUtterance(clean);u.rate=1.06;u.pitch=.96;u.onend=resolve;u.onerror=resolve;window.speechSynthesis.speak(u)})}

async function sendCallTurn(text){
  if(!S.call)return;if(duplexEnabled()){try{await attachDuplexToCall();S.duplexLastUser='';S.streamStartedAt=performance.now();duplexSend({type:'turn.text',text});return}catch(e){toast(`Duplex text fallback: ${e.message}`,true)}}if(S.activeTurn)await interruptActiveTurn(false);appendTranscript('user',text);$('callHeading').textContent='DaQauntum is thinking…';setDQState('thinking');S.streamDone=false;S.speechBuffer='';S.speechQueue=[];
  const box=$('callTranscript');const d=document.createElement('div');d.className='transcript-turn assistant streaming';d.innerHTML='<strong>DaQauntum</strong><div></div><small class="stream-meta">connecting…</small>';box.appendChild(d);const live=d.querySelector('div');const meta=d.querySelector('small');box.scrollTop=box.scrollHeight;let full='';
  try{await streamRequest('/api/call/turn/stream',{session_id:S.call.id,text},async ev=>{
    if(ev.type==='start'){S.activeTurn=ev.turn_id;$('talkBtn').disabled=false;$('talkBtn').textContent='↯ Interrupt & Talk';meta.textContent='thinking…'}
    else if(ev.type==='meta'){const first=Math.max(0,performance.now()-S.streamStartedAt);meta.textContent=`${ev.provider||'—'} / ${ev.model||'—'} · first token ${first.toFixed(0)} ms`}
    else if(ev.type==='delta'){const delta=ev.text||'';full+=delta;live.textContent=full;box.scrollTop=box.scrollHeight;queueSpeechDelta(delta,false)}
    else if(ev.type==='done'||ev.type==='interrupted'){S.streamDone=true;S.activeTurn=null;queueSpeechDelta('',true);if(ev.call)S.call=ev.call;const rt=ev.realtime||ev.result?.realtime||{};meta.textContent=`${ev.type==='interrupted'?'interrupted · ':''}TTFT ${Number(rt.time_to_first_token_ms||0).toFixed(0)} ms · total ${Number(rt.duration_ms||0).toFixed(0)} ms`;if(S.call)renderCall();await refreshStatus();if(!$('autoSpeak').checked&&$('handsFree').checked)setTimeout(()=>startListening('call'),120);else if($('autoSpeak').checked)pumpSpeechQueue()}
  })}catch(e){if(e.name!=='AbortError')toast(e.message,true)}finally{S.streamDone=true;S.activeTurn=null;if(S.call?.status==='active'){$('talkBtn').disabled=false;$('talkBtn').textContent='🎙 Talk';$('callTextSend').disabled=false;$('callHeading').textContent='Your turn'}}
}

async function speak(text){S.streamDone=true;queueSpeechDelta(String(text),true);await pumpSpeechQueue()}

// Local microphone capture -> 16 kHz mono PCM WAV -> local STT endpoint.
async function startLocalRecording(mode='call'){
  if(!navigator.mediaDevices?.getUserMedia)throw new Error('Microphone capture is unavailable in this browser');
  const stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
  const Ctx=window.AudioContext||window.webkitAudioContext;const ctx=new Ctx();const source=ctx.createMediaStreamSource(stream);const proc=ctx.createScriptProcessor(4096,1,1);const chunks=[];
  const rec={mode,stream,ctx,source,proc,chunks,sampleRate:ctx.sampleRate,startedAt:performance.now(),voiceStarted:false,lastVoice:performance.now(),stopping:false};S.localRecorder=rec;setListeningUi(true);
  proc.onaudioprocess=e=>{if(rec.stopping)return;const data=new Float32Array(e.inputBuffer.getChannelData(0));chunks.push(data);let sum=0;for(let i=0;i<data.length;i++)sum+=data[i]*data[i];const rms=Math.sqrt(sum/data.length);const now=performance.now();if(rms>0.018){rec.voiceStarted=true;rec.lastVoice=now}
    const pause=Number($('turnPause')?.value||700);if(rec.voiceStarted&&now-rec.lastVoice>pause&&now-rec.startedAt>500)stopLocalRecording(true);else if(now-rec.startedAt>30000)stopLocalRecording(true)};
  source.connect(proc);proc.connect(ctx.destination);
}

async function stopLocalRecording(transcribe=true){
  const rec=S.localRecorder;if(!rec||rec.stopping)return;rec.stopping=true;S.localRecorder=null;try{rec.proc.disconnect();rec.source.disconnect()}catch(e){}rec.stream.getTracks().forEach(t=>t.stop());try{await rec.ctx.close()}catch(e){}setListeningUi(false);
  if(!transcribe||!rec.chunks.length)return;
  const wav=encodeWav(rec.chunks,rec.sampleRate,16000);if(rec.mode==='call')$('callHeading').textContent='Transcribing locally…';
  const r=await fetch('/api/voice/transcribe',{method:'POST',headers:{'Content-Type':'audio/wav'},body:wav});const d=await r.json();if(!r.ok||d.ok===false)throw new Error(d.error||`HTTP ${r.status}`);const text=String(d.result?.text||'').trim();
  if(text)handleTranscript(text,rec.mode);else if(rec.mode==='wake'&&$('wakeEnabled').checked)setTimeout(startWakeListening,300);
}
function stopAnyListening(){if(S.duplexRecorder)stopDuplexRecording(false);if(S.localRecorder)stopLocalRecording(false);if(S.recognition&&S.listening){try{S.recognition.stop()}catch(e){}}}
function flatten(chunks){let n=0;chunks.forEach(c=>n+=c.length);const out=new Float32Array(n);let o=0;chunks.forEach(c=>{out.set(c,o);o+=c.length});return out}
function downsample(buf,inRate,outRate){if(inRate===outRate)return buf;const ratio=inRate/outRate;const len=Math.round(buf.length/ratio);const out=new Float32Array(len);let pos=0;for(let i=0;i<len;i++){const next=Math.round((i+1)*ratio);let sum=0,c=0;for(;pos<next&&pos<buf.length;pos++){sum+=buf[pos];c++}out[i]=c?sum/c:0}return out}
function encodeWav(chunks,inRate,outRate){const data=downsample(flatten(chunks),inRate,outRate);const b=new ArrayBuffer(44+data.length*2);const v=new DataView(b);const str=(o,x)=>{for(let i=0;i<x.length;i++)v.setUint8(o+i,x.charCodeAt(i))};str(0,'RIFF');v.setUint32(4,36+data.length*2,true);str(8,'WAVE');str(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,outRate,true);v.setUint32(28,outRate*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);str(36,'data');v.setUint32(40,data.length*2,true);let o=44;for(let i=0;i<data.length;i++,o+=2){const x=Math.max(-1,Math.min(1,data[i]));v.setInt16(o,x<0?x*0x8000:x*0x7fff,true)}return new Blob([b],{type:'audio/wav'})}

function handleTranscript(text,mode){
  if(mode==='wake'){
    const phrase=(S.voice?.wake?.phrase||'daqauntum').toLowerCase();if(text.toLowerCase().includes(phrase)){toast(`Wake phrase heard: ${phrase}`);startCall().then(()=>{if($('autoSpeak').checked)speak("I'm listening.")}).catch(e=>toast(e.message,true))}else if($('wakeEnabled').checked&&!S.call)setTimeout(startWakeListening,250);return;
  }
  sendCallTurn(text);
}
function startWakeListening(){if(!$('wakeEnabled').checked||S.call||S.listening)return;S.wakeLoop=true;$('wakeChip').textContent='WAKE: listening';$('wakeChip').className='voice-chip ready';startListening('wake')}
function stopWakeListening(){S.wakeLoop=false;if(S.recognitionMode==='wake')stopAnyListening();$('wakeChip').textContent='WAKE: off';$('wakeChip').className='voice-chip'}
$('wakeEnabled').addEventListener('change',()=>{if($('wakeEnabled').checked)startWakeListening();else stopWakeListening();loadVoiceStatus()});
$('preferLocalVoice').addEventListener('change',loadVoiceStatus);
$('fullDuplex').addEventListener('change',async()=>{if(!$('fullDuplex').checked){await stopDuplexRecording(false);closeDuplex();toast('Full duplex disabled; HTTP streaming remains available')}else{await refreshStatus();if(S.call?.status==='active')await attachDuplexToCall();toast('Full duplex enabled')}});
$('bargeIn').addEventListener('change',async()=>{try{await api('/api/runtime/module','POST',{name:'barge_in',enabled:$('bargeIn').checked});toast(`Barge-in ${$('bargeIn').checked?'enabled':'disabled'}`);await refreshStatus()}catch(e){toast(e.message,true)}});

function lastAssistant(turns=[]){const a=[...turns].reverse().find(t=>t.speaker==='assistant');return a?.text||''}
function setCallActive(active){$('startCallBtn').disabled=active;$('talkBtn').disabled=!active;$('endCallBtn').disabled=!active;$('callTextInput').disabled=!active;$('callTextSend').disabled=!active;$('callStatus').textContent=active?'LIVE':'ENDED';$('callStatus').classList.toggle('live',active);$('callAtom').classList.toggle('call-live',active);$('callHeading').textContent=active?'Connected to DaQauntum':'Call complete'}
function appendTranscript(speaker,text){const box=$('callTranscript');if(box.querySelector('.empty'))box.innerHTML='';const d=document.createElement('div');d.className=`transcript-turn ${speaker}`;d.innerHTML=`<strong>${speaker==='user'?'You':'DaQauntum'}</strong><div>${esc(text)}</div>`;box.appendChild(d);box.scrollTop=box.scrollHeight}
function renderCall(){if(!S.call)return;const c=S.call;const tr=$('callTranscript');tr.innerHTML=(c.turns||[]).map(t=>`<div class="transcript-turn ${t.speaker}"><strong>${t.speaker==='user'?'You':'DaQauntum'}</strong><div>${esc(t.text)}</div></div>`).join('')||'<div class="empty">Call connected.</div>';tr.scrollTop=tr.scrollHeight;
  const notes=c.notes?.length?c.notes:(c.live_notes||[]);$('noteCount').textContent=notes.length;$('liveNotes').className='list'+(notes.length?'':' empty');$('liveNotes').innerHTML=notes.length?notes.map(n=>`<div class="call-item">${esc(n)}</div>`).join(''):'Notes will appear as you talk.';
  renderTasks(c.tasks||[]);let sum=c.summary||'';if(c.decisions?.length)sum+=`\n\nDecisions:\n• ${c.decisions.join('\n• ')}`;if(c.followups?.length)sum+=`\n\nOpen questions:\n• ${c.followups.join('\n• ')}`;$('callSummary').className='summary'+(sum?'':' empty');$('callSummary').textContent=sum||'End the call to consolidate notes, decisions, and follow-ups.';
}
function renderTasks(tasks){const box=$('taskList');$('runAllTasksBtn').disabled=!tasks.length;if(!tasks.length){box.className='list empty';box.innerHTML='Tasks from the call will appear here.';return}box.className='list';box.innerHTML=tasks.map(t=>`<div class="task-item"><div class="task-status">${esc(t.status)}</div><div>${esc(t.description)}</div><div class="task-meta">priority ${esc(t.priority)}${t.pending_approvals?.length?` • approval ${esc(t.pending_approvals.join(', '))}`:''}</div>${t.result?`<div class="task-meta">${esc(t.result.slice(0,240))}</div>`:''}<button onclick="runTask(${t.id})">Run task</button></div>`).join('')}
async function runTask(id){try{toast(`Running task #${id}…`);await api('/api/call/task/run','POST',{task_id:id});if(S.call)S.call=(await api(`/api/call?session_id=${encodeURIComponent(S.call.id)}`)).call;renderCall();await refreshStatus();toast(`Task #${id} processed`)}catch(e){toast(e.message,true)}} window.runTask=runTask;
async function refreshCall(){if(!S.call)return;try{S.call=(await api(`/api/call?session_id=${encodeURIComponent(S.call.id)}`)).call;renderCall()}catch(e){}}

// Memory/system --------------------------------------------------------------
async function loadMemory(q=''){try{const d=await api(`/api/memory?query=${encodeURIComponent(q)}`);const stats=S.status?.memory||{};$('memoryStats').innerHTML=[['Memories',stats.structured_total||0],['Messages',stats.messages||0],['Relations',stats.intelligence?.relations||0],['Graph nodes',S.status?.knowledge_graph?.nodes||0],['Sources',S.status?.sources?.sources||0]].map(([a,b])=>`<div class="metric"><small>${a}</small><strong>${b}</strong></div>`).join('');const box=$('memoryList');box.innerHTML=d.items.length?d.items.map(m=>`<div class="memory-item"><div class="meta">#${m.id} • ${esc(m.kind)} • ${esc(m.project||'no project')}</div><strong>${esc(m.title||'Memory')}</strong><div>${esc(m.content||'')}</div></div>`).join(''):'<div class="empty">No matching memories.</div>'}catch(e){toast(e.message,true)}}
$('memorySearchBtn').addEventListener('click',()=>loadMemory($('memoryQuery').value.trim()));$('memoryQuery').addEventListener('keydown',e=>{if(e.key==='Enter')loadMemory(e.target.value.trim())});

async function loadLearning(){
  try{
    const d=await api('/api/learning');const l=d.learning||{};const n=d.native_model||{};
    $('learningStats').innerHTML=`<div><small>SESSIONS</small><strong>${l.sessions||0}</strong></div><div><small>CRITIC APPROVED</small><strong>${l.critic_approved||0}</strong></div><div><small>REPORTS</small><strong>${(d.reports||[]).length}</strong></div>`;
    const latest=l.latest;$('learningLatest').innerHTML=latest?`<strong>${esc(latest.topic||'Latest learning')}</strong><br><span class="task-meta">${esc(latest.specialty||'')} · confidence ${Number(latest.confidence||0).toFixed(2)} · ${latest.critic_approved?'critic approved':'needs review'}</span><br><span>${esc((latest.summary||'').slice(0,700))}</span>`:'No autonomous learning sessions yet.';
    $('nativeModelStats').innerHTML=`<strong>${n.training_memories||0}</strong> approved training memories · <strong>${n.approved_learning_sessions||0}</strong> critic-approved learning sessions<br><span class="task-meta">Fine-tuning enabled: ${n.fine_tuning_enabled?'YES':'NO'} · latest export: ${esc(n.latest_export||'none')}</span>`;
    $('learningReports').innerHTML=(d.reports||[]).length?(d.reports||[]).map(r=>`<div class="call-item"><strong>${esc(r.name)}</strong><div class="task-meta">${esc(r.modified)} · ${r.size} bytes</div><div><a class="tiny" href="/api/learning/report?name=${encodeURIComponent(r.name)}">Download .md</a></div></div>`).join(''):'<div class="empty">No learning reports yet.</div>';
  }catch(e){toast(e.message,true)}
}
$('runLearningBtn').addEventListener('click',async()=>{try{$('runLearningBtn').disabled=true;toast('DaQauntum is running a Deep learning session…');const d=await api('/api/learning/run','POST',{});toast(`Learning complete: ${d.result.topic}`);await loadLearning();await refreshStatus()}catch(e){toast(e.message,true)}finally{$('runLearningBtn').disabled=false}});
$('eveningReportBtn').addEventListener('click',async()=>{try{$('eveningReportBtn').disabled=true;const d=await api('/api/learning/evening','POST',{});toast('Evening report created');await loadLearning()}catch(e){toast(e.message,true)}finally{$('eveningReportBtn').disabled=false}});
$('exportDatasetBtn').addEventListener('click',async()=>{try{$('exportDatasetBtn').disabled=true;const d=await api('/api/native-model/export','POST',{});toast(`Native-model seed exported: ${d.result.records} records`);await loadLearning()}catch(e){toast(e.message,true)}finally{$('exportDatasetBtn').disabled=false}});

// Connected Knowledge -------------------------------------------------------
async function loadConnectors(){
  try{
    const d=await api('/api/connectors');const st=d.stats||{};const bridge=d.device_bridge||{};
    $('connectorStats').innerHTML=`<div><small>CONNECTORS</small><strong>${st.connectors||0}</strong></div><div><small>ACTIVE</small><strong>${st.active||0}</strong></div><div><small>LEARNING</small><strong>${st.learning_enabled||0}</strong></div><div><small>INDEXED ITEMS</small><strong>${st.items||0}</strong></div>`;
    const list=d.connectors||[];$('connectorList').className='list'+(list.length?'':' empty');
    $('connectorList').innerHTML=list.length?list.map(c=>`<div class="connector-item"><div class="row"><div><strong>${esc(c.name)}</strong><div class="task-meta">#${c.id} · ${esc(c.kind)} · ${esc(c.locator)}</div><div class="task-meta">${c.project?`project ${esc(c.project)} · `:''}${c.last_sync_at?`last sync ${esc(c.last_sync_at)} · `:''}${c.last_status?esc(c.last_status):'not synced'}</div>${c.last_error?`<div class="task-meta" style="color:#ff91a1">${esc(c.last_error)}</div>`:''}</div><div><span class="chip">${c.enabled?'ACTIVE':'PAUSED'}</span> <span class="chip">${c.learn_enabled?'LEARN':'NO LEARN'}</span></div></div><div class="connector-actions"><button class="tiny" onclick="syncConnector(${c.id})">Sync</button><button class="tiny" onclick="setConnectorLearning(${c.id},${!c.learn_enabled})">${c.learn_enabled?'Stop learning':'Learn from this'}</button><button class="tiny" onclick="setConnectorEnabled(${c.id},${!c.enabled})">${c.enabled?'Pause':'Enable'}</button>${c.kind!=='device_inbox'?`<button class="tiny danger" onclick="removeConnector(${c.id})">Disconnect</button>`:''}</div></div>`).join(''):'No connected sources yet.';
    if(bridge.enabled){$('bridgeStatus').innerHTML=`<div class="bridge-code">${esc(bridge.pairing_code||'------')}</div><div class="bridge-url">${esc(bridge.url||'')}</div><div class="task-meta">Open this address on a phone connected to the same LAN, enter the six-digit code, then send files or notes.</div>`;$('rotatePairBtn').disabled=false}
    else{$('bridgeStatus').innerHTML='Device Bridge is not running.<br><span class="task-meta">Launch DaQauntum with <code>python daqauntum_gui.py --device-bridge</code> to pair a phone/tablet.</span>';$('rotatePairBtn').disabled=true}
  }catch(e){toast(e.message,true)}
}
$('addFolderBtn').addEventListener('click',async()=>{try{const path=$('folderPath').value.trim();if(!path)throw new Error('Enter a folder path');$('addFolderBtn').disabled=true;const d=await api('/api/connectors/add-folder','POST',{path,name:$('folderName').value.trim(),project:$('folderProject').value.trim(),recursive:$('folderRecursive').checked,learn_enabled:$('folderLearn').checked});toast(`Connected ${d.connector.name}`);$('folderPath').value='';await loadConnectors();await refreshStatus()}catch(e){toast(e.message,true)}finally{$('addFolderBtn').disabled=false}});
$('addWebBtn').addEventListener('click',async()=>{try{const url=$('webUrl').value.trim();if(!url)throw new Error('Enter a URL');$('addWebBtn').disabled=true;const path=$('webConnectorType').value==='rss'?'/api/connectors/add-rss':'/api/connectors/add-url';const d=await api(path,'POST',{url,name:$('webName').value.trim(),project:$('webProject').value.trim(),learn_enabled:$('webLearn').checked});toast(`Connected ${d.connector.name}`);$('webUrl').value='';await loadConnectors();await refreshStatus()}catch(e){toast(e.message,true)}finally{$('addWebBtn').disabled=false}});
$('addApifyBtn').addEventListener('click',async()=>{try{const dataset_id=$('apifyDatasetId').value.trim();if(!dataset_id)throw new Error('Enter an Apify dataset ID');$('addApifyBtn').disabled=true;const d=await api('/api/connectors/add-apify','POST',{dataset_id,name:$('apifyName').value.trim(),project:$('apifyProject').value.trim(),token_env:$('apifyTokenEnv').value.trim()||'APIFY_TOKEN',limit:Number($('apifyLimit').value||100),learn_enabled:$('apifyLearn').checked});toast(`Connected ${d.connector.name}`);$('apifyDatasetId').value='';await loadConnectors();await refreshStatus()}catch(e){toast(e.message,true)}finally{$('addApifyBtn').disabled=false}});
$('syncConnectorsBtn').addEventListener('click',async()=>{try{$('syncConnectorsBtn').disabled=true;toast('Synchronizing connected knowledge…');const d=await api('/api/connectors/sync','POST',{});toast(`Sync complete: ${d.result.indexed||0} new/changed · ${d.result.errors||0} errors`);await loadConnectors();await refreshStatus()}catch(e){toast(e.message,true)}finally{$('syncConnectorsBtn').disabled=false}});
$('rotatePairBtn').addEventListener('click',async()=>{try{const d=await api('/api/device-bridge/rotate','POST',{});toast('Phone pairing code rotated');await loadConnectors()}catch(e){toast(e.message,true)}});
async function syncConnector(id){try{toast(`Syncing connector #${id}…`);const d=await api('/api/connectors/sync','POST',{connector_id:id});toast(`Connector #${id}: ${d.result.indexed||0} new/changed`);await loadConnectors();await refreshStatus()}catch(e){toast(e.message,true)}} window.syncConnector=syncConnector;
async function setConnectorLearning(id,enabled){try{await api('/api/connectors/learning','POST',{connector_id:id,enabled});toast(enabled?'Learning enabled':'Learning disabled');await loadConnectors()}catch(e){toast(e.message,true)}} window.setConnectorLearning=setConnectorLearning;
async function setConnectorEnabled(id,enabled){try{await api('/api/connectors/enabled','POST',{connector_id:id,enabled});toast(enabled?'Connector enabled':'Connector paused');await loadConnectors()}catch(e){toast(e.message,true)}} window.setConnectorEnabled=setConnectorEnabled;
async function removeConnector(id){if(!confirm('Disconnect this source? Existing indexed evidence will remain in DaQauntum unless separately removed.'))return;try{await api('/api/connectors/remove','POST',{connector_id:id});toast('Connector removed; existing source records preserved');await loadConnectors();await refreshStatus()}catch(e){toast(e.message,true)}} window.removeConnector=removeConnector;

async function loadSystem(){try{await refreshStatus();$('systemStatus').textContent=JSON.stringify(S.status,null,2);const md=await api('/api/models');$('modelMatrix').innerHTML=Object.entries(md.models.roles).map(([role,x])=>`<div class="model-item"><strong>${role.toUpperCase()}</strong><div>${esc(modelText(x.selected))}</div><div class="task-meta">${(x.candidates||[]).map(c=>esc(c.provider)).join(' → ')}</div></div>`).join('');$('toolList').innerHTML=(S.status.tools||[]).map(t=>`<span class="chip">${esc(t)}</span>`).join('');const calls=await api('/api/calls/recent');$('recentCalls').innerHTML=calls.calls.length?calls.calls.map(c=>`<div class="call-item"><strong>${esc(c.title)}</strong><div class="task-meta">${esc(c.status)} • ${esc(c.started_at)}</div></div>`).join(''):'<div class="empty">No calls yet.</div>'}catch(e){toast(e.message,true)}}

async function loadUniverse(){
  try{const d=await api('/api/universe');S.universe=d.universe;const u=d.universe,a=u.atom||{};window.DQUniverse?.setData(u);$('universeAtomId').textContent=a.atom_id||'—';$('universeLevel').textContent=`LEVEL ${a.level||1}`;$('universeEnergy').textContent=`${Math.round((a.energy||0)*100)}%`;$('universeMolecules').textContent=String((u.molecules||[]).length);$('universeNetwork').textContent=u.network?.connected?'NETWORKED':'LOCAL UNIVERSE';
    loadBrain();
    $('moleculeList').className='list'+((u.molecules||[]).length?'':' empty');$('moleculeList').innerHTML=(u.molecules||[]).length?(u.molecules||[]).map(m=>`<div class="molecule-item"><strong>${esc(m.name)}</strong><div class="task-meta">mass ${m.mass} · project molecule</div></div>`).join(''):'No project molecules yet.';
    $('peerList').className='list'+((u.peers||[]).length?'':' empty');$('peerList').innerHTML=(u.peers||[]).length?(u.peers||[]).map(p=>`<div class="peer-item"><strong>${esc(p.name||p.atom_id||'Peer atom')}</strong></div>`).join(''):'Peer networking is not enabled yet. This release keeps your universe local.';renderModuleToggles(u.modules||{});
  }catch(e){toast(e.message,true)}
}
function renderModuleToggles(mods){$('moduleToggles').innerHTML=Object.entries(mods).map(([name,on])=>`<label class="module-toggle"><span>${esc(name)}</span><input type="checkbox" ${on?'checked':''} onchange="toggleModule('${esc(name)}',this.checked)" /></label>`).join('')}
async function toggleModule(name,enabled){try{await api('/api/runtime/module','POST',{name,enabled});toast(`${name} ${enabled?'enabled':'disabled'}`);await refreshStatus();await loadUniverse()}catch(e){toast(e.message,true)}} window.toggleModule=toggleModule;

refreshStatus();loadVoiceStatus();setInterval(refreshStatus,10000);


// v0.4.0 Workspaces / Agent Studio -----------------------------------------
async function loadWorkspaces(){
  try{
    const d=await api('/api/workspaces');const st=d.stats||{};const active=d.active||null;const list=d.workspaces||[];
    $('workspaceStats').innerHTML=`<div><small>WORKSPACES</small><strong>${st.workspaces||0}</strong></div><div><small>SKILLS</small><strong>${st.skills||0}</strong></div><div><small>AGENTS</small><strong>${st.agents||0}</strong></div><div><small>JOBS</small><strong>${d.workbench?.total||0}</strong></div>`;
    if(!S.workspaceId && active)S.workspaceId=active.id;if(!S.workspaceId && list.length)S.workspaceId=list[0].id;
    $('workspaceList').className='list'+(list.length?'':' empty');
    $('workspaceList').innerHTML=list.length?list.map(w=>`<div class="workspace-item ${Number(S.workspaceId)===Number(w.id)?'active':''}" onclick="selectWorkspace(${w.id})"><div class="row"><strong>${esc(w.name)}</strong><span class="workspace-stage">${esc(w.stage)}</span></div><div class="task-meta">${esc(w.focus.slice(0,120))}</div><div class="connector-actions"><button class="tiny" onclick="event.stopPropagation();activateWorkspace(${w.id})">${active&&Number(active.id)===Number(w.id)?'ACTIVE':'Make active'}</button></div></div>`).join(''):'No workspaces yet. Build one from the FRAME-inspired wizard.';
    if(S.workspaceId)await loadWorkspaceDetail(S.workspaceId);else renderEmptyWorkspace();
  }catch(e){toast(e.message,true)}
}
function renderEmptyWorkspace(){$('activeWorkspaceBadge').textContent='none';$('workspaceDetail').className='summary empty';$('workspaceDetail').textContent='Select or build a workspace.';$('agentSelector').className='agent-selector empty';$('agentSelector').textContent='Create an agent first.';$('workbenchJobs').className='agent-panes empty';$('workbenchJobs').textContent='No agent jobs yet.'}
async function selectWorkspace(id){S.workspaceId=Number(id);await loadWorkspaceDetail(S.workspaceId);await loadWorkspacesListOnly()} window.selectWorkspace=selectWorkspace;
async function loadWorkspacesListOnly(){try{const d=await api('/api/workspaces');const active=d.active||null;$('workspaceList').innerHTML=(d.workspaces||[]).map(w=>`<div class="workspace-item ${Number(S.workspaceId)===Number(w.id)?'active':''}" onclick="selectWorkspace(${w.id})"><div class="row"><strong>${esc(w.name)}</strong><span class="workspace-stage">${esc(w.stage)}</span></div><div class="task-meta">${esc(w.focus.slice(0,120))}</div><div class="connector-actions"><button class="tiny" onclick="event.stopPropagation();activateWorkspace(${w.id})">${active&&Number(active.id)===Number(w.id)?'ACTIVE':'Make active'}</button></div></div>`).join('')}catch(e){}}
async function activateWorkspace(id){try{await api('/api/workspaces/active','POST',{workspace_id:id});S.workspaceId=Number(id);toast('Workspace context activated');await loadWorkspaces();await refreshStatus();await loadUniverse()}catch(e){toast(e.message,true)}} window.activateWorkspace=activateWorkspace;
async function loadWorkspaceDetail(id){
  try{
    const d=await api(`/api/workspace?workspace_id=${encodeURIComponent(id)}`);const w=d.workspace;S.workspaceId=Number(w.id);$('activeWorkspaceBadge').textContent=`#${w.id} · ${w.stage}`;
    $('workspaceDetail').className='summary';$('workspaceDetail').innerHTML=`<strong>${esc(w.name)}</strong><br><span>${esc(w.focus)}</span><br><br><small>DONE LOOKS LIKE</small><br>${esc(w.done_looks_like)}<br><br><span class="workspace-file">${esc(w.path)}</span><br><br><label>MATURITY <select id="workspaceStageSelect" onchange="setWorkspaceStage(this.value)"><option value="manual">MANUAL</option><option value="identify">IDENTIFY</option><option value="semi_automate">SEMI-AUTOMATE</option><option value="expand">EXPAND</option><option value="handoff">HANDOFF</option></select></label><br><br><small>SKILLS</small><br>${(w.skills||[]).length?(w.skills||[]).map(x=>`<span class="chip">${esc(x.name)}</span>`).join(' '):'<span class="dim">none</span>'}<br><br><small>AGENTS</small><br>${(w.agents||[]).length?(w.agents||[]).map(x=>`<span class="chip">${esc(x.name)}</span>`).join(' '):'<span class="dim">none</span>'}<br><br><small>ACCESS DECLARATIONS</small><br>${(w.connections||[]).length?(w.connections||[]).map(x=>`<span class="chip">${esc(x.name)} · ${esc(x.kind)}</span>`).join(' '):'<span class="dim">none</span>'}`;
    $('workspaceStageSelect').value=w.stage;
    const agents=w.agents||[];$('agentSelector').className='agent-selector'+(agents.length?'':' empty');$('agentSelector').innerHTML=agents.length?agents.map(a=>`<label class="agent-check"><input type="checkbox" value="${esc(a.slug)}" /> ${esc(a.name)}</label>`).join(''):'Create an agent first.';
    renderWorkbenchJobs(d.jobs||[]);await loadWorkspacesListOnly();
  }catch(e){toast(e.message,true)}
}
function renderWorkbenchJobs(jobs){$('workbenchStatus').textContent=`${jobs.filter(j=>j.status==='running'||j.status==='queued').length} active · ${jobs.length} recent`;$('workbenchJobs').className='agent-panes'+(jobs.length?'':' empty');$('workbenchJobs').innerHTML=jobs.length?jobs.map(j=>`<div class="agent-pane ${esc(j.status)}"><div class="pane-head"><strong>${esc(j.agent_slug)}</strong><span class="workspace-stage">${esc(j.status)}</span></div><div class="task-meta">${esc(j.prompt)}</div><div class="pane-body">${esc(j.response||j.error||(j.status==='running'?'Thinking…':'Queued…'))}</div>${j.status==='completed'?`<button class="tiny" onclick="handoffJob('${esc(j.id)}')">Hand off to DaQauntum</button>`:''}</div>`).join(''):'No agent jobs yet.'}
async function setWorkspaceStage(stage){if(!S.workspaceId)return;try{await api('/api/workspaces/stage','POST',{workspace_id:S.workspaceId,stage});toast(`Workspace stage: ${stage}`);await loadWorkspaceDetail(S.workspaceId);await loadUniverse()}catch(e){toast(e.message,true)}} window.setWorkspaceStage=setWorkspaceStage;
async function handoffJob(id){try{toast('Handing draft to DaQauntum permission-gated execution…');const d=await api('/api/workbench/handoff','POST',{job_id:id});toast(d.result?.pending_approvals?.length?'Handoff prepared; approval required':'Handoff processed');await refreshStatus();await loadWorkspaceDetail(S.workspaceId)}catch(e){toast(e.message,true)}} window.handoffJob=handoffJob;
$('createWorkspaceBtn').addEventListener('click',async()=>{try{const name=$('wsName').value.trim(),focus=$('wsFocus').value.trim(),done=$('wsDone').value.trim();if(!name||!focus||!done)throw new Error('Name, one job, and done criteria are required');$('createWorkspaceBtn').disabled=true;const d=await api('/api/workspaces/create','POST',{name,focus,done_looks_like:done,stage:$('wsStage').value});S.workspaceId=d.workspace.id;await api('/api/workspaces/active','POST',{workspace_id:S.workspaceId});$('wsName').value='';$('wsFocus').value='';$('wsDone').value='';toast(`Built ${d.workspace.name}`);await loadWorkspaces();await loadUniverse()}catch(e){toast(e.message,true)}finally{$('createWorkspaceBtn').disabled=false}});
$('createSkillBtn').addEventListener('click',async()=>{try{if(!S.workspaceId)throw new Error('Select a workspace');const desc=$('skillDescription').value.trim();if(!desc)throw new Error('Describe the skill');await api('/api/workspaces/skill','POST',{workspace_id:S.workspaceId,name:$('skillName').value.trim(),description:desc});$('skillName').value='';$('skillDescription').value='';toast('Skill created as editable Markdown');await loadWorkspaceDetail(S.workspaceId)}catch(e){toast(e.message,true)}});
$('createAgentBtn').addEventListener('click',async()=>{try{if(!S.workspaceId)throw new Error('Select a workspace');const desc=$('agentDescription').value.trim();if(!desc)throw new Error('Describe the agent');await api('/api/workspaces/agent','POST',{workspace_id:S.workspaceId,name:$('agentName').value.trim(),description:desc});$('agentName').value='';$('agentDescription').value='';toast('Agent added to the workspace shelf');await loadWorkspaceDetail(S.workspaceId)}catch(e){toast(e.message,true)}});
$('createConnectionBtn').addEventListener('click',async()=>{try{if(!S.workspaceId)throw new Error('Select a workspace');const name=$('connectionName').value.trim();if(!name)throw new Error('Name the connection');const env_names=$('connectionEnv').value.split(',').map(x=>x.trim().toUpperCase()).filter(Boolean);await api('/api/workspaces/connection','POST',{workspace_id:S.workspaceId,name,kind:$('connectionKind').value,env_names,config:{}});$('connectionName').value='';$('connectionEnv').value='';toast('Connection declaration added; no execution authority granted');await loadWorkspaceDetail(S.workspaceId)}catch(e){toast(e.message,true)}});
$('runParallelBtn').addEventListener('click',async()=>{try{if(!S.workspaceId)throw new Error('Select a workspace');const prompt=$('workbenchPrompt').value.trim();if(!prompt)throw new Error('Enter a workbench prompt');const slugs=[...document.querySelectorAll('#agentSelector input:checked')].map(x=>x.value);if(!slugs.length)throw new Error('Select at least one agent');$('runParallelBtn').disabled=true;await api('/api/workbench/run-parallel','POST',{workspace_id:S.workspaceId,agent_slugs:slugs,prompt});toast(`Started ${slugs.length} parallel agent job(s)`);await loadWorkspaceDetail(S.workspaceId)}catch(e){toast(e.message,true)}finally{$('runParallelBtn').disabled=false}});
$('exportObsidianBtn').addEventListener('click',async()=>{try{const d=await api('/api/workspaces/obsidian-export','POST',{});toast(`Obsidian vault exported: ${d.result.workspaces} workspace(s)`)}catch(e){toast(e.message,true)}});
setInterval(()=>{if(document.getElementById('view-workspaces')?.classList.contains('active')&&S.workspaceId)loadWorkspaceDetail(S.workspaceId)},2500);


// v0.4.0 Integration Hub ----------------------------------------------------
async function loadIntegrations(){
  try{
    const d=await api('/api/integrations');const list=d.integrations||[];
    $('integrationStats').innerHTML=`<div><small>MODULES</small><strong>${list.length}</strong></div><div><small>AVAILABLE</small><strong>${d.available||0}</strong></div><div><small>CONFIGURED</small><strong>${d.configured||0}</strong></div><div><small>HEALTHY</small><strong>${d.healthy||0}</strong></div>`;
    $('integrationList').innerHTML=list.map(x=>`<div class="connector-item"><div class="row"><div><strong>${esc(x.name.replaceAll('_',' ').toUpperCase())}</strong><div class="task-meta">${esc(x.kind)} · ${esc(x.detail||'')}</div><div>${(x.capabilities||[]).map(c=>`<span class="chip">${esc(c)}</span>`).join(' ')}</div></div><div><span class="chip">${x.available?'AVAILABLE':'MISSING'}</span> <span class="chip">${x.healthy?'HEALTHY':(x.configured?'CHECK':'NOT CONFIGURED')}</span></div></div></div>`).join('');
  }catch(e){toast(e.message,true)}
}
$('refreshIntegrationsBtn')?.addEventListener('click',loadIntegrations);
$('previewTailscaleBtn')?.addEventListener('click',async()=>{try{const d=await api('/api/integrations/tailscale/preview','POST',{});$('tailscaleResult').className='summary';$('tailscaleResult').textContent=d.result}catch(e){toast(e.message,true)}});
$('enableTailscaleBtn')?.addEventListener('click',async()=>{try{const d=await api('/api/integrations/tailscale/enable','POST',{});$('tailscaleResult').className='summary';$('tailscaleResult').textContent=d.pending_approval?`Approval ${d.pending_approval} prepared. Approve it from the Chat/System approval surface.`:(d.result||d.message);await refreshStatus()}catch(e){toast(e.message,true)}});
$('bootstrapPgBtn')?.addEventListener('click',async()=>{try{const d=await api('/api/integrations/postgres/bootstrap','POST',{});$('pgResult').className='summary';$('pgResult').textContent=d.pending_approval?`Approval ${d.pending_approval} prepared.`:(d.result||d.message);await refreshStatus()}catch(e){toast(e.message,true)}});


// v0.4.0 Eyes + Hands ------------------------------------------------------
async function loadPerception(){
  try{
    const d=await api('/api/perception');const p=d.perception||{},c=d.computer||{};S.latestFrameId=p.latest?.id||null;
    $('perceptionMetrics').innerHTML=`<div><small>FRAMES</small><strong>${p.frames||0}</strong></div><div><small>SCREENS</small><strong>${p.screens||0}</strong></div><div><small>CAMERA</small><strong>${p.cameras||0}</strong></div><div><small>ANALYZED</small><strong>${p.analyzed||0}</strong></div>`;
    $('computerAutonomy').value=c.autonomy||'observe';
    const oi=c.open_interpreter||{};$('perceptionStatus').textContent=`vision ${visionStatusText(p.vision)} · computer ${oi.available?'ready':'adapter missing'}`;
    if(p.latest){$('perceptionPreview').src=`/api/perception/image?id=${p.latest.id}&t=${Date.now()}`;if(p.latest.analysis){$('visionResult').className='summary';$('visionResult').textContent=p.latest.analysis}}
    const frames=d.frames||[];$('perceptionFrames').className='perception-frame-list'+(frames.length?'':' empty');$('perceptionFrames').innerHTML=frames.length?frames.map(f=>`<div class="perception-frame"><img src="/api/perception/image?id=${f.id}" alt="${esc(f.frame_type)} frame ${f.id}"/><strong>#${f.id} · ${esc(f.frame_type.toUpperCase())}</strong><div class="task-meta">${esc(f.created_at||'')} · ${f.width||'?'}×${f.height||'?'}</div>${f.analysis?`<div class="task-meta">${esc(String(f.analysis).slice(0,180))}</div>`:''}<button class="tiny" onclick="analyzeFrame(${f.id})">Analyze</button></div>`).join(''):'No visual frames captured yet.';
  }catch(e){toast(e.message,true)}
}
function visionStatusText(v={}){if(v.ollama?.configured)return `local:${v.ollama.model}`;if(v.openai?.configured)return `cloud:${v.openai.model}`;if(v.anthropic?.configured)return `cloud:${v.anthropic.model}`;return 'metadata-only'}
async function frameFromStream(stream,frameType,label=''){
  const video=document.createElement('video');video.muted=true;video.playsInline=true;video.srcObject=stream;await video.play();await new Promise(r=>setTimeout(r,180));
  const w=Math.max(1,video.videoWidth||1280),h=Math.max(1,video.videoHeight||720),maxW=1600,scale=Math.min(1,maxW/w);const canvas=document.createElement('canvas');canvas.width=Math.round(w*scale);canvas.height=Math.round(h*scale);canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);
  const data_url=canvas.toDataURL('image/jpeg',.82);video.pause();video.srcObject=null;const d=await api('/api/perception/frame','POST',{data_url,frame_type:frameType,label,metadata:{browser: navigator.userAgent, captured_at:new Date().toISOString()}});S.latestFrameId=d.frame.id;await loadPerception();return d.frame;
}
async function captureScreenOnce(){if(!navigator.mediaDevices?.getDisplayMedia)throw new Error('Screen capture is unavailable in this browser');const stream=await navigator.mediaDevices.getDisplayMedia({video:true,audio:false});try{return await frameFromStream(stream,'screen','manual screen capture')}finally{stream.getTracks().forEach(t=>t.stop())}}
async function captureCameraOnce(){if(!navigator.mediaDevices?.getUserMedia)throw new Error('Camera capture is unavailable in this browser');const stream=await navigator.mediaDevices.getUserMedia({video:true,audio:false});try{return await frameFromStream(stream,'camera','manual camera snapshot')}finally{stream.getTracks().forEach(t=>t.stop())}}
async function startScreenWatch(){if(S.screenStream)return stopScreenWatch();if(!navigator.mediaDevices?.getDisplayMedia)throw new Error('Screen capture is unavailable');S.screenStream=await navigator.mediaDevices.getDisplayMedia({video:true,audio:false});const track=S.screenStream.getVideoTracks()[0];track.onended=stopScreenWatch;$('watchScreenBtn').textContent='Stop screen context';$('watchScreenBtn').classList.add('screen-live');await frameFromStream(S.screenStream,'screen','live screen context');S.screenWatchTimer=setInterval(()=>{if(S.screenStream)frameFromStream(S.screenStream,'screen','live screen context').catch(()=>{})},4000);toast('Screen context enabled. Stop sharing any time.')}
function stopScreenWatch(){if(S.screenWatchTimer)clearInterval(S.screenWatchTimer);S.screenWatchTimer=null;if(S.screenStream)S.screenStream.getTracks().forEach(t=>t.stop());S.screenStream=null;if($('watchScreenBtn')){$('watchScreenBtn').textContent='Start screen context';$('watchScreenBtn').classList.remove('screen-live')}toast('Screen context stopped')}
async function analyzeFrame(id=null){try{const frame_id=id||S.latestFrameId;if(!frame_id)throw new Error('Capture a frame first');$('visionResult').className='summary';$('visionResult').textContent='Analyzing visual frame…';const d=await api('/api/perception/analyze','POST',{frame_id,prompt:$('visionPrompt').value.trim()});$('visionResult').textContent=d.result.text||'No analysis returned.';await loadPerception()}catch(e){$('visionResult').textContent=e.message;toast(e.message,true)}} window.analyzeFrame=analyzeFrame;
$('captureScreenBtn')?.addEventListener('click',async()=>{try{$('captureScreenBtn').disabled=true;await captureScreenOnce();toast('Screen frame captured locally')}catch(e){toast(e.message,true)}finally{$('captureScreenBtn').disabled=false}});
$('captureCameraBtn')?.addEventListener('click',async()=>{try{$('captureCameraBtn').disabled=true;await captureCameraOnce();toast('Camera frame captured locally')}catch(e){toast(e.message,true)}finally{$('captureCameraBtn').disabled=false}});
$('watchScreenBtn')?.addEventListener('click',async()=>{try{if(S.screenStream)stopScreenWatch();else await startScreenWatch()}catch(e){stopScreenWatch();toast(e.message,true)}});
$('analyzeFrameBtn')?.addEventListener('click',()=>analyzeFrame());
$('computerAutonomy')?.addEventListener('change',async()=>{try{const d=await api('/api/computer/autonomy','POST',{level:$('computerAutonomy').value});toast(`Computer autonomy: ${d.computer.autonomy}`);await loadPerception()}catch(e){toast(e.message,true)}});
$('computerObserveBtn')?.addEventListener('click',async()=>{try{const request=$('computerRequest').value.trim()||'Describe the current screen and application state.';$('computerResult').className='summary';$('computerResult').textContent='Observing without changing state…';const d=await api('/api/computer/observe','POST',{request});$('computerResult').textContent=d.result}catch(e){$('computerResult').textContent=e.message;toast(e.message,true)}});
$('computerActionBtn')?.addEventListener('click',async()=>{try{const request=$('computerRequest').value.trim();if(!request)throw new Error('Describe the computer action first');$('computerResult').className='summary';$('computerResult').textContent='Routing through DaQauntum permissions…';const d=await api('/api/computer/action','POST',{request});$('computerResult').textContent=d.pending_approval?`Prepared approval ${d.pending_approval}. Approve from the Pending Approvals panel before execution.`:(d.result||d.message||'Action processed');await refreshStatus();await loadPerception()}catch(e){$('computerResult').textContent=e.message;toast(e.message,true)}});
window.addEventListener('beforeunload',()=>{if(S.screenStream)S.screenStream.getTracks().forEach(t=>t.stop())});


// v0.4.0 Presence Layer -----------------------------------------------------
async function loadPresence(){
  try{
    const d=await api('/api/presence');const p=d.presence||{},st=d.stats||{},sys=p.system||{},net=p.network||{},bt=p.bluetooth||{},sns=p.sensors||{};
    $('presenceMetrics').innerHTML=`<div><small>SAMPLES</small><strong>${st.samples||0}</strong></div><div><small>WI-FI</small><strong>${st.wifi_connected?'ONLINE':'OFFLINE'}</strong></div><div><small>BLUETOOTH</small><strong>${st.bluetooth_available?'READY':'N/A'}</strong></div><div><small>PAIRED</small><strong>${st.paired_bluetooth||0}</strong></div>`;
    const wifi=net.wifi||{};const temps=sys.temperatures||[];
    $('ambientStatus').innerHTML=`<strong>${esc(sys.hostname||'local host')}</strong><div class="task-meta">${esc(sys.platform||'')} ${esc(sys.platform_release||'')}</div><div>Wi-Fi: ${wifi.connected?`connected to <strong>${esc(wifi.connection||'network')}</strong>`:'not connected'} · IP ${esc(net.local_ip||'?')}</div><div>Battery: ${sys.battery_percent!=null?`${esc(sys.battery_percent)}%`:'unknown'} · Memory ${sys.memory_percent!=null?`${esc(sys.memory_percent)}%`:'unknown'}</div>${temps.length?`<div>Temperature: ${temps.slice(0,4).map(t=>`${esc(t.sensor)} ${esc(t.celsius)}°C`).join(' · ')}</div>`:''}`;
    $('sensorStatus').innerHTML=`IIO sensors: <strong>${(sns.iio||[]).length}</strong><br>Camera devices: <strong>${(sns.video_devices||[]).length}</strong><br>Serial / USB devices: <strong>${(sns.serial_devices||[]).length}</strong><br>Sound interface: <strong>${sns.sound_available?'available':'not detected'}</strong>${(bt.paired||[]).length?`<br><br>Paired Bluetooth:<br>${bt.paired.slice(0,10).map(x=>`• ${esc(x.name||x.address)} <span class="task-meta">${esc(x.address)}</span>`).join('<br>')}`:''}`;
  }catch(e){toast(e.message,true)}
}
$('refreshPresenceBtn')?.addEventListener('click',async()=>{try{$('refreshPresenceBtn').disabled=true;await api('/api/presence/refresh','POST',{});toast('Ambient awareness refreshed');await loadPresence();await refreshStatus()}catch(e){toast(e.message,true)}finally{$('refreshPresenceBtn').disabled=false}});
$('wifiScanBtn')?.addEventListener('click',async()=>{try{$('wifiScanBtn').disabled=true;$('wifiResults').textContent='Scanning nearby Wi-Fi…';const d=await api('/api/presence/wifi-scan','POST',{});const rows=d.networks||[];$('wifiResults').className='list'+(rows.length?'':' empty');$('wifiResults').innerHTML=rows.length?rows.map(x=>`<div class="connector-item"><strong>${esc(x.ssid)}</strong><div class="task-meta">signal ${x.signal??'?'} · ${esc(x.security||'open')} ${x.connected?'· CONNECTED':''}</div></div>`).join(''):'No nearby networks returned.'}catch(e){$('wifiResults').textContent=e.message;toast(e.message,true)}finally{$('wifiScanBtn').disabled=false}});
$('bluetoothScanBtn')?.addEventListener('click',async()=>{try{$('bluetoothScanBtn').disabled=true;$('bluetoothResults').textContent='Scanning nearby Bluetooth…';const d=await api('/api/presence/bluetooth-scan','POST',{seconds:6});const rows=d.devices||[];$('bluetoothResults').className='list'+(rows.length?'':' empty');$('bluetoothResults').innerHTML=rows.length?rows.map(x=>`<div class="connector-item"><strong>${esc(x.name||'Bluetooth device')}</strong><div class="task-meta">${esc(x.address)} · ${x.paired?'paired':'not paired'}</div></div>`).join(''):'No Bluetooth devices returned.'}catch(e){$('bluetoothResults').textContent=e.message;toast(e.message,true)}finally{$('bluetoothScanBtn').disabled=false}});
$('serviceScanBtn')?.addEventListener('click',async()=>{try{$('serviceScanBtn').disabled=true;$('serviceResults').textContent='Discovering local services…';const d=await api('/api/presence/service-scan','POST',{});const rows=d.services||[];$('serviceResults').className='list'+(rows.length?'':' empty');$('serviceResults').innerHTML=rows.length?rows.map(x=>`<div class="connector-item"><strong>${esc(x.name||x.service)}</strong><div class="task-meta">${esc(x.service||'')} · ${esc(x.host||x.address||'')} : ${esc(x.port||'')}</div></div>`).join(''):'No mDNS/Bonjour services returned (avahi-browse may not be installed).'}catch(e){$('serviceResults').textContent=e.message;toast(e.message,true)}finally{$('serviceScanBtn').disabled=false}});
$('wifiConnectBtn')?.addEventListener('click',async()=>{try{const profile=$('wifiProfile').value.trim();if(!profile)throw new Error('Enter a saved Wi-Fi profile name');const d=await api('/api/presence/wifi-connect','POST',{profile});toast(d.pending_approval?`Approval ${d.pending_approval} prepared`:(d.result||'Wi-Fi action complete'));await refreshStatus()}catch(e){toast(e.message,true)}});
$('bluetoothConnectBtn')?.addEventListener('click',async()=>{try{const address=$('bluetoothAddress').value.trim();if(!address)throw new Error('Enter a paired Bluetooth MAC address');const d=await api('/api/presence/bluetooth-connect','POST',{address});toast(d.pending_approval?`Approval ${d.pending_approval} prepared`:(d.result||'Bluetooth action complete'));await refreshStatus()}catch(e){toast(e.message,true)}});

// v0.4.0 Guided Demo --------------------------------------------------------
function renderDemo(d){const demo=d.demo||d||{},ready=d.readiness||null,current=demo.current||{};$('demoProgress').textContent=demo.total?`${Math.min((demo.index||0)+1,demo.total)} / ${demo.total}`:'not started';$('demoTitle').textContent=current.title||'Bring DaQauntum to life';$('demoText').innerHTML=current.title?`<p>${esc(current.say||'')}</p><p class="task-meta">${esc(current.instruction||'')}</p>`:'Start the guided demo and DaQauntum will walk you through its brain, voice, awareness, eyes, hands, connections, and autonomous learning.';if(ready){$('demoReadiness').innerHTML=`<div class="bridge-code">${ready.score}%</div><div class="task-meta">capability readiness on this machine</div>`;$('demoChecklist').innerHTML=(ready.steps||[]).map((x,i)=>`<div class="connector-item"><strong>${x.ready?'✓':'○'} ${esc(x.title)}</strong><div class="task-meta">${esc(x.instruction||'')}</div></div>`).join('')}}
async function loadDemo(){try{const d=await api('/api/demo');renderDemo(d)}catch(e){toast(e.message,true)}}
$('startDemoBtn')?.addEventListener('click',async()=>{try{const d=await api('/api/demo/start','POST',{speak:true});renderDemo({demo:d.demo,readiness:(await api('/api/demo')).readiness});toast('Guided demo started')}catch(e){toast(e.message,true)}});
$('nextDemoBtn')?.addEventListener('click',async()=>{try{const d=await api('/api/demo/next','POST',{speak:true});renderDemo({demo:d.demo,readiness:(await api('/api/demo')).readiness});if(d.demo.completed)toast('Demo complete')}catch(e){toast(e.message,true)}});
$('prevDemoBtn')?.addEventListener('click',async()=>{try{const d=await api('/api/demo/previous','POST',{speak:false});renderDemo({demo:d.demo,readiness:(await api('/api/demo')).readiness})}catch(e){toast(e.message,true)}});
$('speakDemoBtn')?.addEventListener('click',async()=>{try{await api('/api/demo/speak','POST',{});toast('Speaking demo step')}catch(e){toast(e.message,true)}});

if(new URLSearchParams(location.search).get('demo')==='1'){setTimeout(()=>{setView('demo');$('startDemoBtn')?.click()},900)}

// v0.4.0 main-screen shortcuts ------------------------------------------------
$('quickCallBtn')?.addEventListener('click',()=>{setView('call');setTimeout(()=>{if(!S.call||S.call.status!=='active')$('startCallBtn')?.click()},150)});
$('quickDemoBtn')?.addEventListener('click',()=>{setView('demo');setTimeout(()=>$('startDemoBtn')?.click(),150)});
$('quickSenseBtn')?.addEventListener('click',async()=>{setView('presence');try{await api('/api/presence/refresh','POST',{});await loadPresence();toast('DaQauntum refreshed its passive awareness')}catch(e){toast(e.message,true)}});
$('quickCapabilitiesBtn')?.addEventListener('click',()=>{const input=$('chatInput');input.value='Give me a concise live capability report: what can you do on this machine right now, what is configured, and what should I try first?';$('chatForm').requestSubmit()});

// v0.4.1 events, reactions, notifications and device drivers -------------------
const SEVERITY_ICON={debug:'·',info:'•',notice:'◆',warning:'▲',critical:'⨯'};

function renderEventsBadge(events){
  const badge=$('eventsBadge');if(!badge)return;
  const pending=Number(events?.notifications?.pending||0);
  badge.textContent=pending>99?'99+':String(pending);badge.hidden=pending===0;
  badge.className='nav-badge'+(events?.notifications?.highest_pending_severity==='critical'||events?.notifications?.highest_pending_severity==='warning'?' warn':'');
}

async function loadEvents(){
  try{
    const d=await api('/api/events?limit=30');S.events=d;
    const bus=d.stats?.bus||{},rx=d.stats?.reactions||{},nf=d.stats?.notifications||{};
    $('eventMetrics').innerHTML=[['Events',bus.events||0],['Suppressed',bus.suppressed_repeats||0],['Rules',`${rx.rules_enabled||0}/${rx.rules||0}`],['Fires',rx.fires||0],['Pending',nf.pending||0],['Proposals',rx.pending_proposals||0]].map(([a,b])=>`<div class="metric"><small>${a}</small><strong>${b}</strong></div>`).join('');
    $('eventSuppressed').textContent=`${bus.suppressed_repeats||0} duplicate observations suppressed`;
    renderNotifications(d.notifications||[]);renderEventRows(d.events||[]);renderRules(d.rules||[]);
    renderReactionTasks(d.tasks||[]);renderProposals(d.proposals||[]);renderEventsBadge(d.stats);
    await loadDrivers();
  }catch(e){toast(e.message,true)}
}

function renderNotifications(items){
  $('notificationCount').textContent=items.length;const box=$('notificationList');
  if(!items.length){box.className='list empty';box.innerHTML='No notifications.';return}
  box.className='list';
  box.innerHTML=items.map(n=>`<div class="event-item sev-${esc(n.severity)}"><div class="row"><strong>${SEVERITY_ICON[n.severity]||'•'} ${esc(n.title)}</strong><span class="workspace-stage">${esc(n.severity)}</span></div>${n.body?`<div class="task-meta">${esc(n.body)}</div>`:''}<div class="task-meta dim">${esc(n.source)} · ${esc(n.kind)} · ${esc(n.created_at||'')}</div><div class="connector-actions"><button class="tiny" onclick="setNotificationStatus(${n.id},'read')">Mark read</button><button class="tiny" onclick="setNotificationStatus(${n.id},'dismissed')">Dismiss</button></div></div>`).join('');
}
async function setNotificationStatus(id,status){try{await api('/api/notifications/status','POST',{notification_id:id,status});await loadEvents()}catch(e){toast(e.message,true)}}
window.setNotificationStatus=setNotificationStatus;

function renderEventRows(items){
  const box=$('eventList');
  if(!items.length){box.className='list empty';box.innerHTML='No events yet.';return}
  box.className='list';
  box.innerHTML=items.map(e=>`<div class="event-item sev-${esc(e.severity)}"><div class="row"><strong>${SEVERITY_ICON[e.severity]||'•'} ${esc(e.kind)}</strong><span class="workspace-stage">${esc(e.source)}</span></div><div class="task-meta">${esc(e.message||'(no message)')}</div><div class="task-meta dim">${esc(e.subject)} · ${esc(e.created_at||'')}${e.repeat_count?` · ${e.repeat_count} repeat(s) suppressed`:''}</div></div>`).join('');
}

function renderRules(items){
  const box=$('ruleList');
  if(!items.length){box.className='list empty';box.innerHTML='No rules.';return}
  box.className='list';
  box.innerHTML=items.map(r=>{
    const match=[r.match_source&&`source=${r.match_source}`,r.match_kind&&`kind=${r.match_kind}`,r.match_subject&&`subject=${r.match_subject}`].filter(Boolean).join(' · ')||'any event';
    const conds=(r.conditions||[]).map(c=>`${c.path} ${c.op}${c.value===undefined?'':' '+JSON.stringify(c.value)}`).join(' AND ');
    return `<div class="event-item${r.enabled&&!r.expired?'':' disabled-rule'}"><div class="row"><strong>${esc(r.name)}</strong><span class="workspace-stage">${r.expired?'expired':(r.enabled?'enabled':'disabled')}</span></div>${r.description?`<div class="task-meta">${esc(r.description)}</div>`:''}<div class="task-meta dim">WHEN ${esc(match)}${conds?` AND ${esc(conds)}`:''}</div><div class="task-meta dim">THEN ${esc(r.action?.type||'?')}${r.action?.tool?` ${esc(r.action.tool)}`:''} · cooldown ${Math.round(Number(r.cooldown_seconds||0)/60)} min · fired ${r.fire_count||0}×</div><div class="connector-actions"><button class="tiny" onclick="toggleRule(${r.id},${r.enabled?'false':'true'})">${r.enabled?'Disable':'Enable'}</button><button class="tiny" onclick="deleteRule(${r.id})">Delete</button></div></div>`;
  }).join('');
}
async function toggleRule(id,enabled){try{await api('/api/events/rules/update','POST',{rule_id:id,enabled});toast(`Rule ${enabled?'enabled':'disabled'}`);await loadEvents()}catch(e){toast(e.message,true)}}
window.toggleRule=toggleRule;
async function deleteRule(id){if(!confirm('Delete this reaction rule? Events it already produced are kept.'))return;try{await api('/api/events/rules/delete','POST',{rule_id:id});toast('Rule deleted');await loadEvents()}catch(e){toast(e.message,true)}}
window.deleteRule=deleteRule;

function renderReactionTasks(items){
  const box=$('reactionTaskList');
  if(!items.length){box.className='list empty';box.innerHTML='No queued tasks.';return}
  box.className='list';
  box.innerHTML=items.map(t=>`<div class="event-item"><strong>${esc(t.title)}</strong>${t.detail?`<div class="task-meta">${esc(t.detail)}</div>`:''}<div class="task-meta dim">queued ${esc(t.created_at||'')}</div><div class="connector-actions"><button class="tiny" onclick="setReactionTask(${t.id},'done')">Done</button><button class="tiny" onclick="setReactionTask(${t.id},'dismissed')">Dismiss</button></div></div>`).join('');
}
async function setReactionTask(id,status){try{await api('/api/events/tasks/status','POST',{task_id:id,status});await loadEvents()}catch(e){toast(e.message,true)}}
window.setReactionTask=setReactionTask;

function renderProposals(items){
  const box=$('proposalList');
  if(!items.length){box.className='list empty';box.innerHTML='No proposed actions.';return}
  box.className='list';
  box.innerHTML=items.map(p=>`<div class="event-item sev-notice"><div class="row"><strong>${esc(p.tool)}</strong><span class="workspace-stage">${esc(p.status)}</span></div>${p.reason?`<div class="task-meta">${esc(p.reason)}</div>`:''}<div class="task-meta dim">${esc(JSON.stringify(p.arguments||{}))}</div><div class="task-meta dim">Requires L${p.required_level??'?'} · gate says "${esc(p.permission_outcome)}" · has not run</div><div class="connector-actions"><button class="tiny" onclick="resolveProposal(${p.id},'approve')">Approve &amp; run</button><button class="tiny" onclick="resolveProposal(${p.id},'reject')">Reject</button></div></div>`).join('');
}
async function resolveProposal(id,decision){
  if(decision==='approve'&&!confirm('Run this proposed action now? It executes through the normal permission path.'))return;
  try{const d=await api('/api/events/proposals/resolve','POST',{proposal_id:id,decision});toast(d.result?.message||'Done');await loadEvents();await refreshStatus()}catch(e){toast(e.message,true)}
}
window.resolveProposal=resolveProposal;

async function loadDrivers(){
  try{
    const d=await api('/api/drivers');const box=$('driverList');const list=d.drivers?.drivers||[];
    if(!list.length){box.className='list empty';box.innerHTML='No drivers registered.';return}
    box.className='list';
    box.innerHTML=list.map(x=>{
      const flags=[['enabled',x.enabled],['available',x.available],['configured',x.configured],['writes',x.writes_allowed]].map(([k,v])=>`<span class="chip ${v?'on':'off'}">${k}${v?' ✓':' ✗'}</span>`).join('');
      return `<div class="event-item${x.usable?'':' disabled-rule'}"><div class="row"><strong>${esc(x.name)}</strong><span class="workspace-stage">${x.usable?'usable':'not ready'}</span></div><div class="task-meta">${esc(x.detail||'')}</div><div class="task-meta">${flags}</div>${x.targets?.length?`<div class="task-meta dim">approved: ${esc(x.targets.join(', '))}</div>`:''}${x.last_error?`<div class="task-meta dim">last error: ${esc(x.last_error)}</div>`:''}${x.available&&x.enabled?`<div class="connector-actions"><button class="tiny" onclick="discoverDriver('${esc(x.name)}')">Discover</button></div>`:''}</div>`;
    }).join('');
  }catch(e){toast(e.message,true)}
}
async function discoverDriver(name){try{toast(`Discovering with ${name}…`);const d=await api('/api/drivers/discover','POST',{driver:name});toast(d.ok?'Discovery complete — see result below':'Discovery failed');const box=$('driverList');box.insertAdjacentHTML('afterbegin',`<div class="event-item"><strong>${esc(name)} discovery</strong><pre class="task-meta" style="white-space:pre-wrap;max-height:220px;overflow:auto">${esc(String(d.result).slice(0,4000))}</pre></div>`)}catch(e){toast(e.message,true)}}
window.discoverDriver=discoverDriver;

$('refreshEventsBtn')?.addEventListener('click',loadEvents);
$('dismissAllBtn')?.addEventListener('click',async()=>{try{const d=await api('/api/notifications/dismiss-all','POST',{});toast(`Dismissed ${d.dismissed}`);await loadEvents();await refreshStatus()}catch(e){toast(e.message,true)}});
$('pollDriversBtn')?.addEventListener('click',async()=>{try{$('pollDriversBtn').disabled=true;const d=await api('/api/drivers/poll','POST',{});toast(`Polled ${d.result.polled} driver(s): ${d.result.published} new, ${d.result.suppressed} suppressed`);await loadEvents()}catch(e){toast(e.message,true)}finally{$('pollDriversBtn').disabled=false}});

// v0.4.2 device identity, enrollment and remote access ------------------------
const SCOPE_HELP={read:'Read status, events and notifications',chat:'Hold a conversation',approve:'Approve actions already awaiting approval',ingest:'Send files and notes in',admin:'Enrol and revoke devices'};
const DEFAULT_SCOPES=['read','chat'];

function renderScopePicker(selected){
  const box=$('enrollScopes');if(!box)return;
  box.innerHTML=Object.entries(SCOPE_HELP).map(([name,help])=>
    `<label class="scope-option" title="${esc(help)}"><input type="checkbox" value="${esc(name)}" ${selected.includes(name)?'checked':''}/><span><strong>${esc(name)}</strong><small>${esc(help)}</small></span></label>`).join('');
}
function selectedScopes(){return Array.from(document.querySelectorAll('#enrollScopes input:checked')).map(el=>el.value)}

async function loadDevices(){
  try{
    const d=await api('/api/devices?events=30');S.devices=d;
    const stats=d.stats||{};const counts=stats.devices||{};
    $('deviceMetrics').innerHTML=[['Active',counts.active||0],['Revoked',counts.revoked||0],['Live tokens',stats.active_tokens||0],['Open codes',stats.open_enrollment_codes||0],['Device limit',stats.max_devices||'—']]
      .map(([a,b])=>`<div class="metric"><small>${a}</small><strong>${b}</strong></div>`).join('');
    if(!$('enrollScopes').children.length)renderScopePicker(DEFAULT_SCOPES);
    renderDeviceList(d.devices||[]);renderAuthLog(d.auth_events||[]);
  }catch(e){toast(e.message,true)}
}

function renderDeviceList(devices){
  $('deviceCount').textContent=devices.filter(x=>x.active).length;
  const box=$('deviceList');
  if(!devices.length){box.className='list empty';box.innerHTML='No devices enrolled.';return}
  box.className='list';
  box.innerHTML=devices.map(x=>{
    const seen=x.last_seen_at?new Date(x.last_seen_at*1000).toLocaleString():'never';
    const scopes=(x.scopes||[]).map(s=>`<span class="chip on">${esc(s)}</span>`).join('');
    return `<div class="event-item${x.active?'':' disabled-rule'}"><div class="row"><strong>${esc(x.name)}</strong><span class="workspace-stage">${x.active?'active':'revoked'}</span></div>
      <div class="task-meta">${esc(x.platform)} · ${x.active_tokens||0} live token(s) · last seen ${esc(seen)}</div>
      <div class="task-meta">${scopes}</div>
      ${x.last_remote?`<div class="task-meta dim">last address ${esc(x.last_remote)}</div>`:''}
      ${x.active?`<div class="connector-actions"><button class="tiny" onclick="editDeviceScopes('${esc(x.device_id)}')">Change scopes</button><button class="tiny" onclick="revokeDevice('${esc(x.device_id)}','${esc(x.name)}')">Revoke</button></div>`:''}</div>`;
  }).join('');
}

function renderAuthLog(events){
  const box=$('authLog');
  if(!events.length){box.className='list empty';box.innerHTML='No authentication activity yet.';return}
  const bad=new Set(['auth_failed','enrollment_failed','scope_denied','auth_throttled','enrollment_throttled']);
  box.className='list';
  box.innerHTML=events.map(e=>`<div class="event-item${bad.has(e.event)?' sev-warning':''}"><div class="row"><strong>${esc(e.event)}</strong><span class="workspace-stage">${esc(new Date((e.created_at||0)*1000).toLocaleTimeString())}</span></div><div class="task-meta dim">${esc(e.device_id||'—')}${e.remote?` · ${esc(e.remote)}`:''}${e.detail?` · ${esc(e.detail)}`:''}</div></div>`).join('');
}

$('enrollBtn')?.addEventListener('click',async()=>{
  try{
    const scopes=selectedScopes();
    if(!scopes.length)throw new Error('Grant at least one scope');
    $('enrollBtn').disabled=true;
    const d=await api('/api/devices/enroll-code','POST',{device_name:$('enrollName').value.trim(),scopes});
    const mins=Math.round((d.enrollment.expires_in_seconds||600)/60);
    $('enrollResult').className='summary';
    $('enrollResult').innerHTML=`<div class="enroll-code">${esc(d.enrollment.code)}</div>
      <div class="task-meta">Grants: ${d.enrollment.scopes.map(esc).join(', ')}. Expires in ${mins} minute(s), single use.</div>
      <div class="task-meta dim">On the phone, open DaQauntum over your private network (Tailscale) and enter this code. Creating a new code cancels this one.</div>`;
    toast('Enrollment code created');await loadDevices();
  }catch(e){toast(e.message,true)}finally{$('enrollBtn').disabled=false}
});

async function revokeDevice(id,name){
  if(!confirm(`Revoke "${name}"? Its tokens stop working immediately.`))return;
  try{await api('/api/devices/revoke','POST',{device_id:id});toast('Device revoked');await loadDevices()}catch(e){toast(e.message,true)}
}
window.revokeDevice=revokeDevice;

async function editDeviceScopes(id){
  const device=(S.devices?.devices||[]).find(x=>x.device_id===id);
  if(!device)return;
  const next=prompt(`Scopes for "${device.name}" (comma separated).\nAvailable: ${Object.keys(SCOPE_HELP).join(', ')}`,(device.scopes||[]).join(','));
  if(next===null)return;
  try{
    const scopes=next.split(',').map(s=>s.trim()).filter(Boolean);
    await api('/api/devices/update','POST',{device_id:id,scopes});
    toast('Scopes updated');await loadDevices();
  }catch(e){toast(e.message,true)}
}
window.editDeviceScopes=editDeviceScopes;

$('refreshDevicesBtn')?.addEventListener('click',loadDevices);

// Obsidian long-term memory ---------------------------------------------------
async function loadObsidian(){
  try{
    const d=await api('/api/obsidian');const v=d.obsidian?.vault||{};
    $('obsidianVaultLabel').textContent=v.root?String(v.root).replace(/^.*\//,'…/'+String(v.root).split('/').slice(-2).join('/')):'not configured';
    $('obsidianMetrics').innerHTML=[['Vault notes',v.notes||0],['Your notes',v.user_notes||0],['DaQauntum notes',v.generated||0],['Indexed sources',d.sources?.sources||0]]
      .map(([a,b])=>`<div class="metric"><small>${a}</small><strong>${b}</strong></div>`).join('');
  }catch(e){/* the connect view still works without the vault */}
}
async function runObsidian(path,label,btn){
  try{
    btn.disabled=true;toast(`${label}…`);
    const d=await api(path,'POST',{});const r=d.result||{};
    const parts=[];
    const ex=r.export||r, im=r.import||r;
    if(ex.memories!==undefined)parts.push(`exported ${ex.memories} memories, ${ex.knowledge_nodes||0} knowledge notes`);
    if(im.indexed!==undefined)parts.push(`indexed ${im.indexed} of your notes (${im.skipped_generated||0} DaQauntum notes skipped)`);
    $('obsidianResult').className='summary';
    $('obsidianResult').innerHTML=`<div>${esc(parts.join(' · ')||'Done')}</div><div class="task-meta dim">${esc(ex.root||im.root||'')}</div>`;
    toast(`${label} complete`);await loadObsidian();await refreshStatus();
  }catch(e){toast(e.message,true)}finally{btn.disabled=false}
}
$('obsidianExportBtn')?.addEventListener('click',e=>runObsidian('/api/obsidian/export','Export',e.target));
$('obsidianImportBtn')?.addEventListener('click',e=>runObsidian('/api/obsidian/import','Index',e.target));
$('obsidianSyncBtn')?.addEventListener('click',e=>runObsidian('/api/obsidian/sync','Sync',e.target));

// v0.4.3 atom brain structure -------------------------------------------------
async function loadBrain(){
  try{
    const d=await api('/api/universe/brain');const b=d.brain;S.brain=b;
    window.DQUniverse?.setBrain(b);
    if(window.DQUniverse) window.DQUniverse.onSelect=showBrainNode;
    const net=b.network||{};
    $('universeDevices').textContent=String(net.device_count||0);
    $('networkMode').textContent=net.connected?`${net.device_count} device(s)`:'this atom only';
    renderBrainShells(b.shells||[]);
    renderDeviceOrbit(net);
  }catch(e){/* the universe view still renders without the brain overlay */}
}

function renderBrainShells(shells){
  const box=$('brainShells');
  if(!shells.length){box.className='list empty';box.innerHTML='No structure available.';return}
  box.className='list';
  box.innerHTML=shells.map(s=>{
    const dots=(s.nodes||[]).map(n=>{
      const state=!n.enabled?'off':(n.degraded?'degraded':'on');
      return `<i class="dot ${state}" title="${esc(n.name)}: ${esc(n.detail||'')}"></i>`;
    }).join('');
    return `<div class="event-item shell-${esc(s.name)}"><div class="row"><strong>${esc(s.title)}</strong><span class="workspace-stage">${s.active}/${s.total}</span></div><div class="task-meta">${dots}</div><div class="task-meta dim">${esc(s.description)}</div></div>`;
  }).join('');
}

function showBrainNode(selection){
  const box=$('brainInspector');
  if(!selection){
    $('inspectorTitle').textContent='BRAIN STRUCTURE';
    box.className='summary';
    box.textContent='Select a node in the atom to see what it holds right now.';
    return;
  }
  const {node,shell}=selection;
  const state=!node.enabled?'module switched off':(node.degraded?'available but not fully configured':'active');
  $('inspectorTitle').textContent=node.name.toUpperCase();
  box.className='summary';
  box.innerHTML=`<div class="row"><strong>${esc(node.name)}</strong><span class="workspace-stage">${esc(state)}</span></div>
    <div class="task-meta">${esc(node.detail||'—')}</div>
    <div class="task-meta dim">Shell: ${esc(shell.title)} — ${esc(shell.description)}</div>
    ${node.degraded?'<div class="task-meta dim">This subsystem is implemented but something it needs is missing on this machine. Run <code>scripts/doctor.py</code> for the exact fix.</div>':''}`;
}

function renderDeviceOrbit(network){
  const box=$('deviceOrbit');const devices=network.devices||[];
  if(!devices.length){
    box.className='list empty';
    box.innerHTML='No devices enrolled yet. Enrol one in ⛨ Devices to reach DaQauntum from your phone.';
    return;
  }
  box.className='list';
  box.innerHTML=devices.map(d=>`<div class="event-item"><div class="row"><strong>${esc(d.name)}</strong><span class="workspace-stage">${esc(d.platform||'device')}</span></div><div class="task-meta">${(d.scopes||[]).map(s=>`<span class="chip on">${esc(s)}</span>`).join('')}</div>${d.last_seen_at?`<div class="task-meta dim">last seen ${esc(new Date(d.last_seen_at*1000).toLocaleString())}</div>`:''}</div>`).join('');
}

// Perceived-latency reporting -------------------------------------------------
// The server measures speech-end -> transcript -> first token. It cannot see
// when the browser actually starts speaking, so the client reports that mark.
// Without it, "first token to first audible speech" would be a guess.
function reportFirstAudio(){
  const turnId=S.activeTurn||S.duplexLastTurnId;
  if(!turnId||S.firstAudioReported===turnId)return;
  S.firstAudioReported=turnId;
  try{ duplexSend({type:'turn.audio',turn_id:turnId}) }catch(e){ /* HTTP streaming path has no socket */ }
}

function renderTurnLatency(latency){
  const s=latency.stages_ms||{};
  const el=$('callLatency');
  if(!el)return;
  const part=(label,value)=>value==null?`${label} —`:`${label} ${Math.round(value)}ms`;
  el.textContent=[part('hear',s.transcribe),part('think',s.think),part('speak',s.speak),part('total',s.total)].join(' · ');
  el.title='hear = speech end to transcript · think = transcript to first token · speak = first token to first audible reply';
}

// Daily / Lab mode ------------------------------------------------------------
// Daily mode hides developer surfaces; it never removes them. Talking to
// DaQauntum should not require understanding its architecture, but everything
// stays one click away rather than being taken off the machine.
const MODE_KEY='dq_interface_mode';
const DAILY_VIEWS=new Set(['chat','call','demo','events']);

function currentMode(){
  try{ return localStorage.getItem(MODE_KEY)==='lab'?'lab':'daily' }catch(e){ return 'daily' }
}

function applyMode(mode,{navigate=false}={}){
  const lab=mode==='lab';
  document.body.classList.toggle('mode-lab',lab);
  document.body.classList.toggle('mode-daily',!lab);
  $('modeDaily')?.classList.toggle('active',!lab);
  $('modeLab')?.classList.toggle('active',lab);
  try{ localStorage.setItem(MODE_KEY,mode) }catch(e){ /* private mode */ }
  // Leaving Lab while standing in a Lab-only view would strand the user on a
  // hidden page, so send them home.
  if(navigate&&!lab){
    const active=document.querySelector('.view.active')?.id?.replace('view-','');
    if(active&&!DAILY_VIEWS.has(active))setView('chat');
  }
  if(!lab)loadReadiness();
}

$('modeDaily')?.addEventListener('click',()=>applyMode('daily',{navigate:true}));
$('modeLab')?.addEventListener('click',()=>applyMode('lab',{navigate:true}));

// Readiness strip: what actually works on this machine, in plain words.
async function loadReadiness(){
  const strip=$('readinessStrip');
  if(!strip)return;
  try{
    const d=await api('/api/universe/brain');
    const nodes=(d.brain?.shells||[]).flatMap(s=>s.nodes||[]);
    const pick=name=>nodes.find(n=>n.name===name);
    const chips=[];
    const add=(label,node,hint)=>{
      if(!node)return;
      const state=!node.enabled?'off':(node.degraded?'degraded':'on');
      const title=node.degraded?`${node.detail} — ${hint}`:node.detail;
      chips.push(`<span class="ready-chip ${state}" title="${esc(title||'')}"><i class="dot ${state}"></i>${esc(label)}</span>`);
    };
    add('Thinking',pick('planner'),'Configure a real model provider');
    add('Voice',pick('voice'),'Run scripts/setup_local_voice.py');
    add('Memory',pick('memory'),'');
    add('Seeing',pick('perception'),'Share a screen or camera frame first');
    add('Sensing',pick('presence'),'');
    add('Devices',pick('drivers'),'Enable a driver in config.yaml');
    const pending=S.status?.events?.notifications?.pending||0;
    const approvals=(S.status?.pending_approvals||[]).length;
    if(pending)chips.push(`<span class="ready-chip notice" onclick="setView('events')"><i class="dot degraded"></i>${pending} notification(s)</span>`);
    if(approvals)chips.push(`<span class="ready-chip notice"><i class="dot degraded"></i>${approvals} awaiting approval</span>`);
    strip.innerHTML=chips.join('');
  }catch(e){ strip.innerHTML='' }
}

applyMode(currentMode());
setTimeout(loadReadiness,600);
setInterval(()=>{ if(document.body.classList.contains('mode-daily'))loadReadiness() },30000);

// Guided Eyes + Hands task ----------------------------------------------------
// The whole point of this flow is that each stage is visible and the action
// waits for the user. The UI mirrors that: nothing here runs a step implicitly.
const VERDICT_LABEL={pass:'PASS',fail:'FAIL',uncertain:'UNCERTAIN'};

async function guidedLook(){
  const goal=$('guidedGoal').value.trim();
  if(!goal)return toast('Describe what you want DaQauntum to do',true);
  try{
    $('guidedLookBtn').disabled=true;
    const d=await api('/api/computer/task/start','POST',{goal});
    S.guidedTask=d.task;renderGuided(d.task);
    $('guidedProposeBtn').disabled=false;
    toast('DaQauntum looked at your screen');
  }catch(e){
    // A missing frame is the common case and deserves a real instruction.
    toast(e.message,true);
    if(/share/i.test(e.message))$('guidedPanel').innerHTML='<div class="task-meta">Capture a screen frame first — DaQauntum never captures your screen by itself.</div>';
  }finally{$('guidedLookBtn').disabled=false}
}

async function guidedPropose(){
  const task=S.guidedTask;if(!task)return;
  const instruction=prompt('What single action should DaQauntum propose?',task.goal||'');
  if(instruction===null)return;
  try{
    const d=await api('/api/computer/task/propose','POST',{task_id:task.id,instruction});
    S.guidedTask=d.task;renderGuided(d.task);
  }catch(e){toast(e.message,true)}
}

async function guidedDecide(decision){
  const task=S.guidedTask;if(!task)return;
  if(decision==='approve'&&!confirm(`Run this action now?\n\n${task.proposal?.instruction||''}\n\nIt executes through the normal permission checks.`))return;
  const reason=decision==='approve'?'approved from the guided task panel':'declined';
  try{
    const d=await api('/api/computer/task/decision','POST',{task_id:task.id,decision,reason});
    S.guidedTask=d.task;renderGuided(d.task);
    await refreshStatus();
  }catch(e){toast(e.message,true)}
}
window.guidedDecide=guidedDecide;

function renderGuided(task){
  const box=$('guidedPanel');box.className='summary';
  const parts=[`<div class="row"><strong>${esc(task.goal)}</strong><span class="workspace-stage">${esc(task.status)}</span></div>`];
  if(task.observation)parts.push(`<div class="task-meta"><strong>Sees:</strong> ${esc(task.observation.slice(0,600))}</div>`);
  if(task.proposal){
    parts.push(`<div class="event-item sev-notice"><div class="row"><strong>Proposed: ${esc(task.proposal.tool)}</strong><span class="workspace-stage">${esc(task.permission_outcome||'')}</span></div>
      <div class="task-meta">${esc(task.proposal.instruction)}</div>
      <div class="task-meta dim">Needs L${task.proposal.required_level??'?'} · ${esc(task.permission_reason||'')}</div>
      ${task.status==='awaiting_approval'?`<div class="connector-actions"><button class="tiny" onclick="guidedDecide('approve')">Approve &amp; run</button><button class="tiny" onclick="guidedDecide('reject')">Reject</button></div>`:''}</div>`);
  }
  if(task.verdict){
    const cls=task.verdict==='pass'?'on':(task.verdict==='fail'?'off':'degraded');
    parts.push(`<div class="event-item sev-${task.verdict==='fail'?'critical':(task.verdict==='pass'?'notice':'warning')}">
      <div class="row"><strong>Verification</strong><span class="ready-chip ${cls}"><i class="dot ${cls}"></i>${esc(VERDICT_LABEL[task.verdict]||task.verdict)}</span></div>
      <div class="task-meta">${esc((task.verification||'').slice(0,600))}</div>
      ${task.after_frame_id?`<div class="task-meta dim">Verified from fresh capture #${esc(task.after_frame_id)}</div>`:'<div class="task-meta dim">No post-action capture was available, so this is not a confirmation.</div>'}</div>`);
  }
  if((task.steps||[]).length){
    parts.push(`<div class="task-meta dim"><strong>Audit trail</strong></div>`);
    parts.push('<div class="list">'+task.steps.map(s=>`<div class="task-meta dim">· <code>${esc(s.kind)}</code> ${esc(String(s.detail).slice(0,180))}</div>`).join('')+'</div>');
  }
  box.innerHTML=parts.join('');
}

$('guidedLookBtn')?.addEventListener('click',guidedLook);
$('guidedProposeBtn')?.addEventListener('click',guidedPropose);
