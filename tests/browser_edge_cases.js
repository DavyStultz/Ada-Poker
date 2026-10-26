async function edgeCases(){
 const failures=[],passed=[];
 const assert=(v,m)=>{if(!v)throw Error(m);};
 const until=async(f,m)=>{for(let n=0;n<200;n++){if(f())return;await new Promise(r=>setTimeout(r,15));}throw Error('Timeout '+m);};
 const field=n=>$('filters').elements.namedItem(n);
 const run=async(name,fn)=>{try{await fn();passed.push(name);}catch(e){failures.push(name+': '+e.message+' '+JSON.stringify({filters:state.filters,total:state.results?.total,form:filterValues()}));}};
 const lookup=async(name,count)=>{
  clearSearch();resetFilters();$('query').value=name;$('search-form').requestSubmit();
  await until(()=>!$('results').hidden&&$('hand-rows').children.length===count,'lookup '+name);
 };
 await run('typing suggests without loading hands or replacing text',async()=>{
  clearSearch();const realFetch=window.fetch;let handRequests=0;
  window.fetch=(url,opts)=>{if(String(url).startsWith('/api/hands?'))handRequests++;return realFetch(url,opts);};
  try{
   for(const query of ['Mir','Mira11']){
    $('query').value=query;$('query').dispatchEvent(new Event('input',{bubbles:true}));
    await until(()=>!$('suggestions').hidden,'typed suggestion');
    assert($('query').value===query,'typing was autocompleted');
    assert($('results').hidden&&handRequests===0,'typing loaded hands');
   }
   $('suggestions').firstChild.click();
   await until(()=>!$('results').hidden,'clicked name');
   assert(handRequests===1,'name selection did not load exactly once');
   $('query').value='Cedar';$('query').dispatchEvent(new Event('input',{bubbles:true}));
   await until(()=>!$('suggestions').hidden,'next player suggestion');
   assert($('player-name').textContent==='Mira11'&&!$('results').hidden,'typing changed previous results');
   assert(handRequests===1,'typing fetched another player history');
   $('search-submit').click();await until(()=>$('player-name').textContent==='Cedar'&&!$('results').hidden,'Search button');
   assert(handRequests===2,'Search button did not load hands');
   $('query').value='Mira11';$('query').dispatchEvent(new Event('input',{bubbles:true}));
   $('search-form').requestSubmit();await until(()=>$('player-name').textContent==='Mira11'&&!$('results').hidden,'form submit');
   await new Promise(r=>setTimeout(r,300));
   assert($('suggestions').hidden&&handRequests===3,'pending debounce reopened suggestions or loaded twice');
  }finally{window.fetch=realFetch;}
 });
 await run('empty and unmatched search',async()=>{
  clearSearch();$('query').value='no-such-example';$('search-form').requestSubmit();
  await until(()=>$('search-message').textContent.startsWith('No matching'),'no match');
  assert($('results').hidden,'old results left visible');
  $('query').value=' ';$('search-form').requestSubmit();
  assert($('results').hidden&&$('suggestions').hidden,'blank directory');
 });
 await run('partial search suggestions',async()=>{
  $('query').value='r';$('search-form').requestSubmit();
  await until(()=>!$('suggestions').hidden,'suggestions');
  assert($('suggestions').children.length>1,'multiple matches');
  $('suggestions').firstChild.click();await until(()=>!$('results').hidden,'selected suggestion');
 });
 await run('unapplied filters do not relabel active filters',async()=>{
  await lookup('Mira11',30);field('cards').value='unseen';$('filters').requestSubmit();
  await until(()=>$('result-count').textContent.startsWith('32 '),'unseen');
  field('cards').value='seen';$('next-page').click();
  await until(()=>$('hand-rows').children.length===2,'second page');
  assert($('active-filters').textContent.includes('Cards not seen'),'draft filter label replaced applied label');
  $('hand-rows').firstChild.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
  await until(()=>!$('detail-view').hidden,'keyboard hand');
  document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}));
  assert(!$('search-view').hidden&&$('hand-rows').children.length===2,'Escape/page preserved');
 });
 await run('home starts a fresh unfiltered search',async()=>{
  await lookup('Mira11',30);field('cards').value='seen';$('filters').requestSubmit();
  await until(()=>$('hand-rows').children.length===2,'seen');
  $('home').click();$('query').value='Mira11';$('search-form').requestSubmit();
  await until(()=>!$('results').hidden,'fresh search');
  assert($('hand-rows').children.length===30,'old filters remained after Home');
 });
 await run('each card filter and clear filters',async()=>{
  await lookup('Mira11',30);
  for(const [value,count] of [['showdown',1],['seen',2],['unseen',32]]){
   field('cards').value=value;$('filters').requestSubmit();
   await until(()=>$('result-count').textContent.startsWith(count+' '),'cards '+value);
  }
  $('reset-filters').click();await until(()=>$('result-count').textContent.startsWith('34 '),'clear');
  assert(!$('active-filters').children.length,'stale filter chips after clear');
 });
 await run('keyboard screenshot and evidence navigation',async()=>{
  await lookup('Mira11',30);
  const button=document.querySelector('#hand-rows .check-street');
  const event=new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true});
  button.dispatchEvent(event);
  assert(!event.defaultPrevented,'row stole screenshot button keyboard activation');
  button.click();await until(()=>!$('evidence-view').hidden&&$('evidence-image').naturalWidth===640,'image');
  $('evidence-back').click();assert(!$('search-view').hidden,'back to grid');
 });
 await run('missing screenshot response and image paging recover',async()=>{
  await lookup('Mira11',30);const realFetch=window.fetch;
  window.fetch=(url,opts)=>String(url).includes('/api/evidence')?
   Promise.resolve(new Response(JSON.stringify({error:'Test screenshot is unavailable.'}),{status:404,headers:{'Content-Type':'application/json'}})):realFetch(url,opts);
  try{
   document.querySelector('#hand-rows .check-street').click();
   await until(()=>$('evidence-message').textContent==='Test screenshot is unavailable.','missing image');
   assert($('evidence-image').hidden,'failed image visible');
  }finally{window.fetch=realFetch;}
  $('evidence-next').click();
  await until(()=>!$('evidence-image').hidden&&$('evidence-image').naturalWidth===640,'next image');
  assert($('evidence-count').textContent==='2 of 2','image paging count');
  $('evidence-previous').click();await until(()=>$('evidence-count').textContent==='1 of 2','previous image');
  $('evidence-back').click();assert(!$('search-view').hidden,'back after missing image');
 });
 await run('outdated search response is ignored',async()=>{
  clearSearch();resetFilters();const realFetch=window.fetch;
  let release;
  window.fetch=(url,opts)=>String(url).includes('/api/search?q=Mira11')?
   new Promise(resolve=>{release=()=>realFetch(url,opts).then(resolve);}):realFetch(url,opts);
  try{
   $('query').value='Mira11';$('search-form').requestSubmit();
   $('query').value='Cedar';$('search-form').requestSubmit();
   await until(()=>$('player-name').textContent==='Cedar'&&!$('results').hidden,'new result');
   await release();await new Promise(r=>setTimeout(r,80));
   assert($('player-name').textContent==='Cedar','stale search overwrote new player');
  }finally{window.fetch=realFetch;}
 });
 await run('recover after a request fails',async()=>{
  clearSearch();resetFilters();const realFetch=window.fetch;
  window.fetch=()=>Promise.reject(Error('Simulated unavailable library'));
  try{$('query').value='Mira11';$('search-form').requestSubmit();await until(()=>!$('error').hidden,'error');}
  finally{window.fetch=realFetch;}
  $('search-form').requestSubmit();await until(()=>!$('results').hidden,'recovery');
  assert($('error').hidden,'error persisted after successful retry');
 });
 document.body.dataset.edgeChecks=JSON.stringify({passed,failures});
 if(failures.length)throw Error(failures.join('; '));
}
