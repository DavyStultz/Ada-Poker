'use strict';
const capture={enabled:false,status:null,previewSeq:0,previewURL:null,polling:false,previewing:false,revision:null,ready:false};
async function recordingPost(action,data={}){
 const response=await fetch('/api/recording/'+action,{method:'POST',headers:{'X-Ada-Token':token,'Content-Type':'application/json'},body:JSON.stringify(data)});
 const result=await response.json();if(!response.ok)throw Error(result.error||'Recording request failed.');return result;
}
function recordingError(message){$('recording-error').textContent=message;$('recording-error').hidden=!message;}
function recordingTime(seconds){return [Math.floor(seconds/3600),Math.floor(seconds/60)%60,Math.floor(seconds)%60].map(n=>String(n).padStart(2,'0')).join(':');}
function recordingControls(){
 const status=capture.status||{},busy=status.busy;
 for(const id of ['recording-window','recording-mode','recording-refresh','recording-folder','recording-browse','recording-duration'])$(id).disabled=!!busy;
 $('recording-start').disabled=!!busy||!capture.ready||!$('recording-window').value;
 const stoppable=['starting','recording'].includes(status.state);
 $('recording-stop').disabled=!stoppable;$('recording-quick-stop').hidden=!stoppable;
}
function renderRecording(status){
 capture.status=status;
 const labels={idle:'Ready to record',starting:'Starting...',recording:'Recording',stopping:'Saving...',analyzing:'Analyzing saved recording',complete:'Saved and analyzed',error:'Needs attention'};
 $('recording-status').textContent=labels[status.state]||status.state;
 $('recording-toolbar').classList.toggle('is-recording',['starting','recording','stopping'].includes(status.state));
 $('recording-timer').textContent=recordingTime(status.elapsed||0);
 $('recording-message').textContent=status.message||'';
 $('recording-warning').textContent=status.warning||'';$('recording-warning').hidden=!status.warning;
 $('recording-retry').hidden=!status.can_retry;$('recording-open').hidden=!status.session;
 if(!$('recording-folder').value)$('recording-folder').value=status.output_root;
 if(capture.revision!==null&&capture.revision!==status.revision&&state.player)loadResults();
 capture.revision=status.revision;recordingControls();
}
async function recordingPoll(){
 if(!capture.enabled||capture.polling)return;capture.polling=true;
 try{renderRecording(await api('/api/recording/status'));}catch(e){recordingError(e.message);}finally{capture.polling=false;}
}
async function refreshRecordingWindows(automatic=false){
 if(capture.listing||capture.status?.busy)return;
 const select=$('recording-window');
 if(automatic&&(document.activeElement===select||$('recording-panel').hidden))return;
 capture.listing=true;
 if(!automatic)recordingError('');
 try{
  const entries=await api('/api/recording/windows'),selected=select.value;
  if(capture.status?.busy||(automatic&&document.activeElement===select))return;
  const signature=JSON.stringify(entries);
  if(signature===capture.windowSignature)return;
  capture.windowSignature=signature;
  $('recording-window').replaceChildren(el('option','','Choose a window'));$('recording-window').firstChild.value='';
  entries.forEach((w,i)=>{const option=el('option','',`${w.title} (${w.width} x ${w.height})${w.minimized?' — minimized':''}${w.blocked?' — capture blocked':''}`);option.value=w.id;$('recording-window').append(option);});
  if(entries.some(w=>w.id===selected))$('recording-window').value=selected;
  if(!entries.length)$('recording-preview-note').textContent='Open ClubGG, then click Refresh.';
  if(select.value!==selected){capture.previewSeq++;capture.ready=false;}
  recordingControls();await recordingPreview();
 }catch(e){recordingError(e.message);}finally{capture.listing=false;}
}
async function recordingPreview(){
 if(!capture.enabled||$('recording-panel').hidden||capture.previewing)return;
 const selected=$('recording-window').value,mode=$('recording-mode').value,sequence=++capture.previewSeq;
 if(!selected){$('recording-preview-image').hidden=true;$('recording-preview-note').hidden=false;return;}
 capture.previewing=true;
 try{
  const blob=await api('/api/recording/preview',{window:selected,mode},true);
  if(sequence!==capture.previewSeq||selected!==$('recording-window').value||mode!==$('recording-mode').value)return;
  if(capture.previewURL)URL.revokeObjectURL(capture.previewURL);
  capture.previewURL=URL.createObjectURL(blob);$('recording-preview-image').src=capture.previewURL;
  $('recording-preview-image').hidden=false;$('recording-preview-note').hidden=true;capture.ready=true;
 }catch(e){
  if(sequence===capture.previewSeq){capture.ready=false;$('recording-preview-image').hidden=true;$('recording-preview-note').hidden=false;$('recording-preview-note').textContent=e.message;}
 }finally{capture.previewing=false;recordingControls();}
}
function toggleRecording(show){
 document.body.classList.toggle('recording-expanded',show);$('recording-panel').hidden=!show;$('recording-toggle').setAttribute('aria-expanded',String(show));
 if(show){recordingPoll();if(!$('recording-window').value)refreshRecordingWindows();else recordingPreview();}
}
$('recording-toggle').onclick=()=>toggleRecording($('recording-panel').hidden);
$('recording-collapse').onclick=()=>toggleRecording(false);
$('recording-refresh').onclick=()=>refreshRecordingWindows();
$('recording-window').onchange=()=>{capture.previewSeq++;capture.ready=false;recordingControls();recordingPreview();};
$('recording-mode').onchange=()=>{
 capture.previewSeq++;capture.ready=false;$('recording-preview-image').hidden=true;
 $('recording-mode-note').textContent=$('recording-mode').value==='visible_window'
  ?"Records the selected table's visible screen area. Keep it uncovered and in place; overlapping windows will appear in the video. No manual area selection."
  :'Captures the window directly. Some games return a frozen image with this method; use Visible table if the preview does not update.';
 recordingControls();recordingPreview();
};
$('recording-start').onclick=async()=>{
 recordingError('');$('recording-start').disabled=true;
 try{renderRecording(await recordingPost('start',{window:$('recording-window').value,mode:$('recording-mode').value,output_root:$('recording-folder').value,seconds:Number($('recording-duration').value)}));}
 catch(e){recordingError(e.message);}finally{recordingControls();}
};
async function stopRecording(){
 recordingError('');$('recording-stop').disabled=true;$('recording-quick-stop').disabled=true;
 try{renderRecording(await recordingPost('stop'));}catch(e){recordingError(e.message);}finally{$('recording-quick-stop').disabled=false;recordingControls();}
}
$('recording-stop').onclick=stopRecording;$('recording-quick-stop').onclick=stopRecording;
$('recording-browse').onclick=async()=>{
 $('recording-browse').disabled=true;recordingError('');
 try{const result=await recordingPost('folder');if(result.folder)$('recording-folder').value=result.folder;}
 catch(e){recordingError(e.message);}finally{recordingControls();}
};
$('recording-retry').onclick=async()=>{try{renderRecording(await recordingPost('retry'));recordingError('');}catch(e){recordingError(e.message);}};
$('recording-open').onclick=async()=>{try{await recordingPost('open');}catch(e){recordingError(e.message);}};
window.addEventListener('pagehide',()=>{
 if(capture.enabled)navigator.sendBeacon('/api/detach',new Blob([JSON.stringify({token})],{type:'application/json'}));
});
api('/api/config').then(config=>{
 capture.enabled=config.recording===true;$('recording-toolbar').hidden=!capture.enabled;
 if(capture.enabled){recordingPoll();setInterval(recordingPoll,1000);setInterval(recordingPreview,1500);setInterval(()=>refreshRecordingWindows(true),3000);}
}).catch(e=>recordingError(e.message));
