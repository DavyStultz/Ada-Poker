(async()=>{
 const wait=async(f,label)=>{for(let n=0;n<500;n++){if(f())return;await new Promise(r=>setTimeout(r,20));}throw Error('Timeout '+label);};
 const assert=(v,label)=>{if(!v)throw Error(label);};
 try{
  await wait(()=>!$('recording-toolbar').hidden,'recording enabled');
  assert($('recording-panel').hidden,'panel should start collapsed');
  $('recording-toggle').click();await wait(()=>$('recording-window').options.length===2,'window list');
  $('recording-window').value='42';$('recording-window').dispatchEvent(new Event('change'));
  await wait(()=>!$('recording-start').disabled,'preview');
  assert($('recording-window').selectedOptions[0].textContent.startsWith('ClubGG -'),'table application label');
  await wait(()=>$('recording-window').options.length===3,'new windows refresh automatically');
  assert($('recording-window').value==='42','auto refresh lost selected table');
  assert($('query').getBoundingClientRect().top<$('recording-panel').getBoundingClientRect().top,'search above panel');
  $('recording-start').click();await wait(()=>!$('recording-quick-stop').hidden,'recording started');
  assert($('recording-window').disabled,'target changes while recording');
  assert($('recording-mode').disabled,'capture method changes while recording');
  $('recording-collapse').click();assert($('recording-panel').hidden,'collapse');
  $('query').value='Mira11';$('query').dispatchEvent(new Event('input',{bubbles:true}));
  await wait(()=>!$('suggestions').hidden,'suggestions while recording');
  assert($('results').hidden,'typing triggered search');
  $('search-submit').click();await wait(()=>$('hand-rows').children.length===30,'search during recording');
  $('hand-rows').firstChild.click();await wait(()=>!$('detail-view').hidden,'hand detail');
  assert(!$('recording-quick-stop').hidden,'Stop missing in hand detail');
  assert($('query').getBoundingClientRect().height>0,'search missing in detail');
  $('recording-quick-stop').click();await wait(()=>$('recording-status').textContent==='Analyzing saved recording','post-save analysis');
  $('detail-back').click();assert(!$('results').hidden,'search state lost');
  await wait(()=>$('recording-status').textContent==='Saved and analyzed','analysis finished');
  $('recording-toggle').click();
  assert(!$('recording-open').hidden,'saved folder action');
  assert(!$('recording-start').disabled,'next recording disabled after analysis');
  assert(document.documentElement.scrollWidth<=innerWidth,'horizontal page overflow');
  document.body.dataset.testResult='pass';
 }catch(e){document.body.dataset.testResult='FAIL '+e.message;}
})();
