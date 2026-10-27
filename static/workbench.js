const $ = id => document.getElementById(id);
const STORE = 'ocr.workbench.v1';
const labels = {uploaded:'等待开始',pending:'排队中',processing:'处理中',completed:'已完成',partial:'部分完成',failed:'失败',unknown:'待核对',cancelled:'已取消',paused:'已暂停'};
const activeStates = ['pending','processing','uploaded','paused'];
let assets=[], tasks=[], config=null, uploading=0, submitting=false, uploadChain=Promise.resolve(), filter='all', showHidden=false, listSignature='', pollTimer, polling=false, errors=0, reviewTask=null, reviewResult=null, reviewDirty=false, reviewPage=0, saving=false, exporting=false;
let stored={};
try { stored=JSON.parse(localStorage.getItem(STORE)||'{}'); } catch { /* Corrupt drafts do not prevent loading persisted tasks. */ }
let storageWarned=false;
function persist(){
  try { localStorage.setItem(STORE,JSON.stringify({assets:assets.filter(a=>a.job_id),settings:settings()})); }
  catch { if(!storageWarned){ storageWarned=true; toast('浏览器无法保存草稿，请保持页面打开。已接收的任务仍保存在本机服务。',true); } }
}
function settings(){return {ocr_model:$('ocrModel').value,extract_model:$('extractModel').value,target_lang:$('targetLang').value};}
function toast(message,error=false){$('toast').textContent=message;$('toast').classList.toggle('error-toast',error);$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,6000);}
function error(message=''){$('formError').textContent=message;$('formError').hidden=!message;}
async function api(path,options={}){
  const response=await fetch(path,{...options,signal:AbortSignal.timeout(options.timeout||30000)});
  if(!response.ok){let message=`服务返回 ${response.status}`;try{const data=await response.json();message=typeof data.detail==='string'?data.detail:message;}catch{}const e=new Error(message);e.status=response.status;throw e;}
  if(response.status===204)return null;
  return response.json();
}
function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;}
function button(text,cls,fn){const n=el('button',cls,text);n.type='button';n.onclick=fn;return n;}
function modelName(id){return config?.models.find(m=>m.id===id)?.name||id;}
function language(id){return [...$('targetLang').options].find(o=>o.value===id)?.textContent||id;}
function update(){
 const ready=assets.filter(a=>a.state==='ready');
 $('submit').disabled=!config||uploading>0||submitting||ready.length===0;
 $('submit').textContent=uploading?'素材准备中…':submitting?'正在提交…':'开始处理 →';
 for(const id of ['ocrModel','extractModel','targetLang'])$(id).disabled=!config||uploading>0||submitting;
 $('selectFiles').disabled=submitting;
 $('assetCount').textContent=assets.length?`${assets.length} 份文件`:'尚未添加';
 $('summary').textContent=ready.length?`${ready.length} 份就绪 · 共 ${ready.reduce((s,a)=>s+a.total_pages,0)} 页`:'添加文件后即可开始';
 $('paramSummary').textContent=config?`${modelName(settings().ocr_model)} → ${language(settings().target_lang)}`:'请等待服务连接';
 $('uploadStatus').hidden=uploading===0;
 renderAssets();
}
function renderAssets(){
 const root=$('assets');root.replaceChildren();
 for(const a of assets){
  const row=el('div','asset');
  if(a.preview_url){const img=el('img');img.src=a.preview_url;img.alt='';img.loading='lazy';row.append(img);}else row.append(el('span','file-symbol',a.filename.split('.').pop()?.toUpperCase()||'FILE'));
  const body=el('div','asset-body'),name=el('div','asset-name',a.filename);name.title=a.filename;body.append(name);
  const status={queued:'等待上传',uploading:a.filename.toLowerCase().endsWith('.pdf')?'上传并转换 PDF 页面…':'正在上传…',ready:`${a.total_pages} 页 · 已就绪`,failed:a.message||'上传失败，请重新选择文件',submitting:'正在创建任务…',unknown:'提交结果待核对，素材已保留',unconfirmed:'尚未找到任务，可用原编号继续提交'};
  body.append(el('div','asset-meta'+(['failed','unknown'].includes(a.state)?' problem':''),status[a.state]||a.state));row.append(body);
  const actions=el('div','asset-actions');
  if(['unknown','unconfirmed'].includes(a.state))actions.append(button('核对提交','text-button',()=>reconcile(a)));
  if(a.state==='unconfirmed')actions.append(button('继续原提交','text-button',()=>submitOneAgain(a)));
  if(!['queued','uploading','submitting','unknown','unconfirmed'].includes(a.state)){
   const remove=button('移除','text-button',()=>{assets=assets.filter(x=>x!==a);persist();update();});remove.disabled=submitting;actions.append(remove);
  }
  row.append(actions);root.append(row);
 }
}
async function upload(files){
 if(submitting){toast('正在提交任务，请稍候再添加文件');return;}
 error();
 const incoming=Array.from(files);
 if(assets.length+incoming.length>20){error('每批最多 20 份文件，请先处理或移除部分素材。');return;}
 const allowed=/\.(pdf|png|jpe?g|webp|bmp|tiff?|gif)$/i;
 const valid=[];
 for(const file of incoming){
  if(!allowed.test(file.name)||file.size>50*1024*1024||!file.size){
   assets.push({filename:file.name,state:'failed',message:!allowed.test(file.name)?'格式不支持':!file.size?'文件为空':'超过 50MB，请压缩后重新选择'});continue;
  }
  const a={filename:file.name,state:'queued'};assets.push(a);valid.push({file,a});
 }
 uploading+=valid.length;update();
 for(const {file,a} of valid){
  uploadChain=uploadChain.then(async()=>{
   a.state='uploading';$('uploadStatus').textContent=`正在准备 ${file.name}，PDF 转换可能需要一些时间…`;update();
   try{const form=new FormData();form.append('file',file);Object.assign(a,await api('/api/upload',{method:'POST',body:form,timeout:600000}),{state:'ready'});}
   catch(e){a.state='failed';a.message=e.status?e.message:'连接中断或上传超时，请重新选择此文件。';}
   finally{uploading--;persist();update();}
  });
 }
 await uploadChain;
}
function submissionPayload(a){return a.submission||{...settings(),job_id:a.job_id,filename:a.filename,request_id:a.request_id,auto_start:'true'};}
async function sendAsset(a){
 a.state='submitting';update();persist();
 try{
  const form=new FormData();for(const [k,v]of Object.entries(submissionPayload(a)))form.append(k,v);
  await api('/api/tasks/add',{method:'POST',body:form});
  assets=assets.filter(x=>x!==a);persist();update();await refreshTasks();return true;
 }catch(e){
  if(e.status&&e.status<500&&e.status!==408){a.state='ready';a.message=e.message;error(`${a.filename}：${e.message}`);}
  else{a.state='unknown';a.message=e.message;error(`${a.filename} 的接收结果暂不确定。请点“核对提交”，不会自动重发。`);}
  persist();update();return false;
 }
}
async function submit(){
 if($('submit').disabled)return;
 const batch=assets.filter(a=>a.state==='ready');submitting=true;error();
 for(const a of batch){a.request_id=a.request_id||crypto.randomUUID();a.submission={...settings(),job_id:a.job_id,filename:a.filename,request_id:a.request_id,auto_start:'true'};}
 persist();update();let count=0;
 try{for(const a of batch)if(await sendAsset(a))count++;}
 finally{submitting=false;persist();update();if(count)toast(`已接收 ${count} 个任务，可以继续准备下一批。`);}
}
async function reconcile(a,quiet=false){
 if(submitting)return;
 try{
  const task=await api(`/api/tasks/${encodeURIComponent(a.request_id)}`);
  if(task.job_id!==a.job_id)throw new Error('编号与素材不一致，请核对本机记录');
  assets=assets.filter(x=>x!==a);persist();update();await refreshTasks();if(!quiet)toast('已找到原任务，无需重新提交。');
 }catch(e){
  if(e.status===404){a.state='unconfirmed';persist();update();if(!quiet)toast('本机尚未找到此编号。可点击“继续原提交”，沿用原参数与编号。');}
  else if(!quiet)toast('暂时无法核对，请恢复连接后再试。',true);
 }
}
async function submitOneAgain(a){
 if(submitting||uploading)return;submitting=true;update();try{await sendAsset(a);}finally{submitting=false;update();}
}
function formatDate(value){const d=new Date(value);return Number.isNaN(d.getTime())?'':d.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});}
function renderTasks(){
 const list=tasks.filter(t=>showHidden?!!t.hidden:!t.hidden).filter(t=>filter==='all'||filter==='active'&&activeStates.includes(t.status)||filter==='done'&&['completed','partial'].includes(t.status)||filter==='attention'&&['unknown','failed','partial'].includes(t.status));
 const signature=JSON.stringify({list,filter,showHidden});if(signature===listSignature)return;listSignature=signature;
 const root=$('taskList');root.replaceChildren();$('taskCount').textContent=tasks.filter(t=>showHidden?!!t.hidden:!t.hidden).length;
 $('emptyState').hidden=list.length>0;
 $('emptyState').querySelector('h3').textContent=filter==='all'&&!showHidden?'结果会在这里等你':'没有符合条件的任务';
 for(const t of list){
  const card=el('article','task'),top=el('div','task-top');top.append(el('span',`badge ${t.status}`,labels[t.status]||t.status));const time=el('time','',formatDate(t.created_at));time.dateTime=t.created_at;top.append(time);card.append(top,el('h3','',t.filename));
  card.append(el('p','task-meta',`${t.total_pages} 页 · ${modelName(t.ocr_model)} → ${language(t.target_lang)}`));
  if(t.status==='processing'){
   const progress=el('progress');progress.max=t.total_pages;progress.value=t.processed_pages||0;progress.setAttribute('aria-label',`已处理 ${t.processed_pages||0}/${t.total_pages} 页`);card.append(progress,el('p','task-status',`已处理 ${t.processed_pages||0} / ${t.total_pages} 页，正在识别、提取和翻译…`));
  }else if(t.status==='pending')card.append(el('p','task-status','服务已接收，等待可用处理名额。'));
  if(t.status==='partial')card.append(el('p','warning',`成功 ${t.success_pages} 页，失败 ${t.failed_pages} 页。请查看逐页结果。`));
  if(t.error_message&&t.status!=='partial')card.append(el('p',t.status==='unknown'?'warning':'error',t.error_message));
  const actions=el('div','task-actions');
  if(['completed','partial'].includes(t.status)||t.status==='unknown'&&t.processed_pages>0||t.status==='failed'&&t.processed_pages>0){
   actions.append(button('查看与复核','secondary',()=>openReview(t.task_id)),button('下载译文','text-button',()=>downloadTask(t.task_id)));
  }
  if(t.retry_translation_available){
   const retry=button('仅重试翻译','secondary',async()=>{retry.disabled=true;try{const result=await api(`/api/tasks/${t.task_id}/retry-translation`,{method:'POST'});toast(result.message);await refreshTasks();}catch(e){toast(e.message,true);}finally{retry.disabled=false;}});
   retry.title='重新调用翻译模型，可能产生费用；保留 OCR 和字段提取结果';actions.append(retry);
   card.append(el('p','help','识别字段已保留。重试仅调用翻译模型，可能产生费用。'));
  }
  if(t.status==='uploaded')actions.append(button('开始处理','secondary',async()=>{try{await api(`/api/tasks/${t.task_id}/start`,{method:'POST'});await refreshTasks();}catch(e){toast(e.message,true);}}));
  if(t.status==='paused')actions.append(button('继续排队','secondary',async()=>{try{await api(`/api/tasks/${t.task_id}/resume`,{method:'POST'});await refreshTasks();}catch(e){toast(e.message,true);}}));
  if(!showHidden)actions.append(button('复用配置','text-button',()=>{if(uploading||submitting){toast('素材准备或提交结束后再切换配置');return;}for(const [id,key]of [['ocrModel','ocr_model'],['extractModel','extract_model'],['targetLang','target_lang']]){if([...$(id).options].some(o=>o.value===t[key]))$(id).value=t[key];}persist();update();toast('已填入此任务的模型与语言，请添加要处理的文件。');}));
  actions.append(button('复制编号','text-button',()=>copyId(t.task_id)),button(showHidden?'恢复显示':'隐藏','text-button',async()=>{try{await api(`/api/tasks/${t.task_id}/visibility`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({hidden:!showHidden})});await refreshTasks();}catch(e){toast(e.message,true);}}));
  card.append(actions);const details=el('details');details.append(el('summary','','任务详情'));details.append(el('p','',`编号：${t.task_id}`),el('p','',`识别：${t.ocr_model}；提取与翻译：${t.extract_model}`));if(t.status==='unknown')details.append(el('p','','请先核对模型平台调用记录。查看已保存页面不会重新识别。'));
  card.append(details);root.append(card);
 }
 $('showHidden').textContent=showHidden?'返回未隐藏任务':`显示已隐藏任务${tasks.some(t=>t.hidden)?`（${tasks.filter(t=>t.hidden).length}）`:''}`;
}
async function refreshTasks(){
 if(polling)return;polling=true;clearTimeout(pollTimer);
 try{const data=await api('/api/tasks/list?limit=500');tasks=data.tasks;errors=0;renderTasks();$('connection').textContent='本机服务已连接';$('connection').className='online';$('listNote').textContent='任务自动保存。隐藏记录不会取消处理，可随时恢复显示。';}
 catch(e){errors++;$('connection').textContent='连接中断';$('connection').className='offline';$('listNote').textContent='暂时无法刷新，已有记录保留。恢复连接后自动查询；不会重新提交任务。';}
 finally{polling=false;pollTimer=setTimeout(refreshTasks,errors?Math.min(30000,5000*2**errors):tasks.some(t=>['pending','processing'].includes(t.status))?3000:8000);}
}
async function copyId(id){try{await navigator.clipboard.writeText(id);toast('任务编号已复制');}catch{toast(`任务编号：${id}`);}}
async function loadConfig(){
 config=await api('/api/config');
 for(const [id,role]of [['ocrModel','ocr'],['extractModel','translation']]){
  const select=$(id);select.replaceChildren();for(const m of config.models.filter(m=>!m.use_for||m.use_for.includes(role))){const option=el('option','',m.name);option.value=m.id;select.append(option);}
  const preferred=stored.settings?.[id==='ocrModel'?'ocr_model':'extract_model']||(id==='ocrModel'?config.ocr_model.name:config.translation_model.name);
  if([...select.options].some(o=>o.value===preferred))select.value=preferred;
 }
 if(stored.settings?.target_lang&&[...$('targetLang').options].some(o=>o.value===stored.settings.target_lang))$('targetLang').value=stored.settings.target_lang;
 update();
}
async function restore(){
 for(const a of stored.assets||[]){
  try{const source=await api(`/api/assets/${encodeURIComponent(a.job_id)}`);assets.push({...a,...source,state:['submitting','unknown','unconfirmed'].includes(a.state)?'unknown':'ready'});}
  catch(e){if(!e.status){assets.push({...a,state:a.request_id?'unknown':'ready'});}else if(a.request_id)assets.push({...a,state:'unknown'});}
 }
 update();for(const a of [...assets])if(a.state==='unknown')await reconcile(a,true);persist();
}
function draftKey(id){return `ocr.review.${id}`;}
function stashReview(){
 if(!reviewTask||!reviewResult)return;
 try{localStorage.setItem(draftKey(reviewTask.task_id),JSON.stringify({pages:reviewResult.pages,reviewed_at:reviewResult.reviewed_at||null}));}catch{toast('复核草稿无法保存在浏览器，请点击“保存复核”。',true);}
}
async function openReview(id){
 try{
  const t=await api(`/api/tasks/${encodeURIComponent(id)}`),r=await api(`/api/tasks/${encodeURIComponent(id)}/result`);
  if(!r.pages?.length)throw new Error('尚无可查看的页面结果');
  reviewTask=t;reviewResult=r;reviewDirty=false;
  let draft;try{draft=JSON.parse(localStorage.getItem(draftKey(id))||'null');}catch{}
  if(draft?.pages&&JSON.stringify(draft.pages.map(p=>p.page_num))===JSON.stringify(r.pages.map(p=>p.page_num))&&draft.reviewed_at===(r.reviewed_at||null)){
   reviewResult.pages=draft.pages;reviewDirty=true;
  }
  $('workspace').hidden=true;$('review').hidden=false;$('reviewTitle').textContent=t.filename;
  $('reviewMeta').textContent=`${t.total_pages} 页 · ${labels[t.status]||t.status} · ${language(t.target_lang)} · ${t.task_id}`;
  const missing=t.total_pages-r.pages.filter(p=>p.status==='success').length;
  $('reviewWarning').hidden=missing===0;
  $('reviewWarning').textContent=`有 ${missing} 页失败或尚未完成。导出仅包含成功页面，请先核对，避免遗漏。`;
  const tabs=$('pageTabs');tabs.replaceChildren();
  for(let i=1;i<=t.total_pages;i++)tabs.append(button(`第 ${i} 页${r.pages.find(p=>p.page_num===i)?.status==='success'?'':' · 未成功'}`,'secondary',()=>showPage(i)));
  updateSaveState();showPage(1);window.scrollTo({top:0});
 }catch(e){toast(e.message,true);}
}
function updateSaveState(){
 $('saveState').textContent=saving?'正在保存…':reviewDirty?'有未保存的修改':'已与任务记录同步';
 $('saveReview').disabled=saving||!reviewDirty;
 const canExport=reviewResult?.pages.some(p=>p.status==='success');
 $('exportReview').disabled=exporting||!canExport;$('exportOriginal').disabled=exporting||!canExport;
}
function changed(){reviewDirty=true;stashReview();updateSaveState();}
function showPage(number){
 reviewPage=number;[...$('pageTabs').children].forEach((b,i)=>{b.classList.toggle('active',i+1===number);b.setAttribute('aria-pressed',String(i+1===number));});
 const image=el('img');image.alt=`第 ${number} 页原件`;image.src=`/api/image/${reviewTask.job_id}/${number}`;$('sourceFrame').replaceChildren(image);$('openImage').href=image.src;
 const page=reviewResult.pages.find(p=>p.page_num===number);
 $('pageType').textContent=page?.doc_type_cn||'';const failed=!page||page.status!=='success';
 $('pageError').hidden=!failed;$('pageError').textContent=page?.error||'此页尚无成功结果，已保留原图供核对。';
 const root=$('fieldEditor');root.replaceChildren();
 if(page?.status==='success'&&!Object.keys(page.extracted_fields?.fields||{}).length)root.append(el('p','warning','此页未提取到结构化字段。请展开识别原文核对，译文 Word 可能缺少此页内容。')); 
 $('ocrText').value=page?.ocr_text||'';$('ocrText').disabled=failed;
 $('ocrText').oninput=()=>{page.ocr_text=$('ocrText').value;changed();};
 for(const [key,raw]of Object.entries(page?.extracted_fields?.fields||{})){
  const data=typeof raw==='object'&&raw!==null?raw:{value:String(raw)};
  if(raw!==data)page.extracted_fields.fields[key]=data;
  const row=el('div','field'),head=el('div','field-head');head.append(el('strong','',data.field_type||key));
  if(data.is_handwritten||typeof data.confidence==='number'&&data.confidence<.8)head.append(el('span','','建议对照复核'));
  row.append(head);const pair=el('div','field-pair');
  for(const translated of [false,true]){
   const wrap=el('div'),label=el('label','',translated?'译文':'原文'),input=el('textarea');input.rows=2;input.value=String((translated?page.translations?.[key]:data.value)??'');input.setAttribute('aria-label',`${data.field_type||key} · ${translated?'译文':'原文'}`);
   input.oninput=()=>{if(translated){page.translations??={};page.translations[key]=input.value;}else data.value=input.value;changed();};label.append(input);wrap.append(label);pair.append(wrap);
  }
  row.append(pair);root.append(row);
 }
}
async function saveReview(){
 if(!reviewDirty)return true;if(saving)return false;saving=true;updateSaveState();
 // An immutable snapshot prevents edits during the request being falsely marked saved.
 const snapshot=JSON.stringify(reviewResult.pages);
 try{const saved=await api(`/api/tasks/${reviewTask.task_id}/result`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({pages:JSON.parse(snapshot)})});
  reviewDirty=JSON.stringify(reviewResult.pages)!==snapshot;
  reviewResult.reviewed_at=saved.reviewed_at;
  if(!reviewDirty){try{localStorage.removeItem(draftKey(reviewTask.task_id));}catch{}}else stashReview();
  toast(saved.message);return !reviewDirty;
 }catch(e){toast(`保存失败：${e.message}，请保留页面后再试。`,true);return false;}
 finally{saving=false;updateSaveState();}
}
async function exportResult(task,result,original=false){
 const successful=result.pages.filter(p=>p.status==='success');
 if(!successful.length){toast('此任务没有可导出的成功页面。',true);return;}
 exporting=true;updateSaveState();
 try{
  const path=original?'/api/export/original':'/api/export/merged';
  const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:task.job_id,pages:successful,target_lang:task.target_lang}),signal:AbortSignal.timeout(120000)});
  if(!response.ok){let detail;try{detail=(await response.json()).detail;}catch{}throw new Error(typeof detail==='string'?detail:'导出失败');}
  const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`${original?'原文':'译文'}_${task.filename.replace(/\.[^.]+$/,'')}${successful.length<task.total_pages?'_仅成功页面':''}.docx`;a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
  toast(`已导出 ${successful.length} 页${successful.length<task.total_pages?`，另有 ${task.total_pages-successful.length} 页未包含，请核对`:''}。`);
 }catch(e){toast(e.message,true);}finally{exporting=false;updateSaveState();}
}
async function downloadTask(id){
 try{const t=await api(`/api/tasks/${id}`),r=await api(`/api/tasks/${id}/result`);await exportResult(t,r);}catch(e){toast(e.message,true);}
}
$('selectFiles').onclick=()=>$('fileInput').click();
$('uploadZone').onclick=e=>{if(e.target.closest('button,input')||submitting)return;$('fileInput').click();};
$('fileInput').onchange=e=>{upload(e.target.files);e.target.value='';};
$('uploadZone').ondragover=e=>{e.preventDefault();if(!submitting)$('uploadZone').classList.add('dragover');};
$('uploadZone').ondragleave=e=>{if(!$('uploadZone').contains(e.relatedTarget))$('uploadZone').classList.remove('dragover');};
$('uploadZone').ondrop=e=>{e.preventDefault();$('uploadZone').classList.remove('dragover');upload(e.dataTransfer.files);};
// Stop accidental browser navigation when a file misses the drop target.
window.addEventListener('dragover',e=>e.preventDefault());window.addEventListener('drop',e=>e.preventDefault());
$('submit').onclick=submit;$('refresh').onclick=async()=>{try{if(!config)await loadConfig();await refreshTasks();}catch(e){error(e.message);}};
for(const id of ['ocrModel','extractModel','targetLang'])$(id).onchange=()=>{persist();update();};
for(const b of document.querySelectorAll('[data-filter]'))b.onclick=()=>{filter=b.dataset.filter;for(const n of document.querySelectorAll('[data-filter]')){n.classList.toggle('active',n===b);n.setAttribute('aria-pressed',String(n===b));}renderTasks();};
$('showHidden').onclick=()=>{showHidden=!showHidden;renderTasks();};
$('findTask').onclick=()=>{$('findError').hidden=true;$('findDialog').showModal();};$('closeFind').onclick=()=>$('findDialog').close();
$('findForm').onsubmit=async e=>{e.preventDefault();try{const t=await api(`/api/tasks/${encodeURIComponent($('taskId').value.trim())}`);if(t.hidden)await api(`/api/tasks/${t.task_id}/visibility`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({hidden:false})});showHidden=false;filter='all';listSignature='';for(const b of document.querySelectorAll('[data-filter]')){b.classList.toggle('active',b.dataset.filter==='all');b.setAttribute('aria-pressed',String(b.dataset.filter==='all'));}await refreshTasks();$('findDialog').close();if(t.result_data?.pages?.length)await openReview(t.task_id);else toast(`已找到：${t.filename} · ${labels[t.status]||t.status}`);}catch(e){$('findError').hidden=false;$('findError').textContent=e.status===404?'本机没有此任务编号，请检查后再试。':e.message;}};
$('saveReview').onclick=saveReview;
$('exportReview').onclick=async()=>{if(reviewDirty&&!await saveReview())return;await exportResult(reviewTask,reviewResult);};
$('exportOriginal').onclick=async()=>{if(reviewDirty&&!await saveReview())return;await exportResult(reviewTask,reviewResult,true);};
$('back').onclick=()=>{if(reviewDirty){stashReview();toast('未保存修改已暂存为本机浏览器草稿，导出前请保存复核。');}$('review').hidden=true;$('workspace').hidden=false;refreshTasks();};
window.addEventListener('beforeunload',e=>{if(uploading||submitting||reviewDirty){e.preventDefault();e.returnValue='';}});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshTasks();});
async function init(){
 try{await loadConfig();}catch(e){error('无法读取配置，请确认本机服务已启动，再点右上角刷新任务。');}
 await refreshTasks();await restore();update();
}
init();
