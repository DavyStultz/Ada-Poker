(async()=>{
 const mode=new URLSearchParams(location.search).get('test');
 const assert=(value,message)=>{if(!value)throw Error(message);};
 const until=async(check,label)=>{for(let n=0;n<200;n++){if(check())return;await new Promise(r=>setTimeout(r,15));}throw Error('Timed out: '+label);};
 const field=name=>$('filters').elements.namedItem(name);
 const submit=()=>{$('filters').requestSubmit();};
 try{
  await until(()=>$('example-label').hidden===false,'configuration');
  assert($('results').hidden&&$('suggestions').hidden,'directory on landing');
  assert(!$('query').value,'initial search not empty');
  if(mode==='landing'){document.body.dataset.testResult='pass';return;}
  $('query').value='Mira11';$('search-form').requestSubmit();
  await until(()=>$('hand-rows').children.length===30,'search results');
  assert($('hand-rows').firstChild.textContent.includes('SHOWDOWN'),'showdown priority');
  assert($('hand-rows').firstChild.querySelector('.suit').textContent==='\u2660','suit encoding');
  assert($('result-count').textContent.includes('34 hands'),'name aliases combined');
  $('next-page').click();await until(()=>$('hand-rows').children.length===4,'next page');
  $('previous-page').click();await until(()=>$('hand-rows').children.length===30,'previous page');
  $('filter-toggle').click();assert(!$('filters').hidden,'filter button');
  field('action').value='raise';field('position').value='early';field('order').value='1';field('size').value='18';submit();
  await until(()=>!$('empty-results').hidden,'same action negative filter');
  field('size').value='2';submit();
  await until(()=>$('hand-rows').children.length===2,'combined filters');
  $('hand-rows').firstChild.click();await until(()=>!$('detail-view').hidden,'detail');
  assert($('street-columns').children.length===5,'five street columns');
  assert(document.querySelectorAll('.table-seat').length===7,'table seats');
  assert(document.querySelectorAll('#board .playing-card').length===5,'community cards');
  assert($('street-columns').textContent.includes('Raise to'),'action bubbles');
  assert($('detail-back').getBoundingClientRect().left>window.innerWidth/2,'detail back top right');
  $('detail-back').click();assert(!$('search-view').hidden&&field('size').value==='2','back preserves filters');
  assert($('hand-rows').children.length===2,'back preserves results');
  $('hand-rows').children[1].click();await until(()=>!$('detail-view').hidden&&state.detail.id==='example:1','uncertain hand');
  document.querySelector('[data-street="turn"] .check-street').click();
  await until(()=>!$('evidence-image').hidden&&$('evidence-image').naturalWidth===640,'screenshot');
  assert($('evidence-back').getBoundingClientRect().left<window.innerWidth/2,'screenshot back top left');
  $('evidence-back').click();assert(!$('detail-view').hidden,'screenshot back to detail');
  $('detail-back').click();
  document.querySelector('#hand-rows .check-street').click();
  await until(()=>!$('evidence-view').hidden,'grid screenshot');
  $('evidence-back').click();assert(!$('search-view').hidden,'grid screenshot back to results');
  field('action').value='reraise';field('order').value='3';field('size').value='18';submit();
  await until(()=>$('active-filters').textContent.includes('Reraiser'),'reraise filter');
  assert($('hand-rows').children.length===2,'reraise results');
  if(mode==='detail'||mode==='compact'||mode==='narrow'){
   $('hand-rows').firstChild.click();await until(()=>!$('detail-view').hidden,'final detail');
   const boxes=[...document.querySelectorAll('.seat-label')].map(n=>n.getBoundingClientRect());
   for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++){
    const a=boxes[i],b=boxes[j];assert(a.right<=b.left||b.right<=a.left||a.bottom<=b.top||b.bottom<=a.top,'seat labels overlap');
   }
  }
  assert(document.documentElement.scrollWidth<=window.innerWidth,'page overflows horizontally');
  assert($('error').hidden,'visible application error');
  if(mode==='edge-cases')await edgeCases();
  document.body.dataset.testResult='pass';
 }catch(error){document.body.dataset.testResult='FAIL: '+error.message;}
})();
