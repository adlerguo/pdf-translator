// Exercise the uncertain-submit controller without a browser or model service.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map(),storage=new Map();
const context=vm.createContext({
 document:{getElementById:id=>{if(!nodes.has(id))nodes.set(id,{value:'en',disabled:false,classList:{toggle(){}},options:[]});return nodes.get(id);},querySelectorAll:()=>[],addEventListener(){}},
 window:{addEventListener(){}},localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},
 FormData,AbortSignal,setTimeout,clearTimeout,crypto:require('node:crypto').webcrypto,console
});
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/workbench.js'),'utf8').replace(/init\(\);\s*$/,'');
vm.runInContext(source,context);
vm.runInContext('update=()=>{}; persist=()=>{}; error=()=>{}; toast=()=>{}; refreshTasks=async()=>{};',context);
(async()=>{
 await vm.runInContext(`(async()=>{
   const a={job_id:'asset-one',filename:'test.png',state:'ready',request_id:'one-request',submission:{request_id:'one-request',job_id:'asset-one',target_lang:'en'}};
   assets=[a];api=async()=>{const e=new Error('Lost response');e.status=502;throw e;};
   await sendAsset(a);
   if(a.state!=='unknown'||assets.length!==1)throw Error('Unknown submission lost its asset');
   let lookups=0;api=async(path,options)=>{if(options?.method==='POST')throw Error('Must not repost');lookups++;return {job_id:'asset-one'};};
   await reconcile(a,true);
   if(lookups!==1||assets.length!==0)throw Error('Reconciliation did not recover original task');
   const b={job_id:'asset-two',filename:'second.png',request_id:'same-request',state:'unknown'};assets=[b];
   api=async()=>{const e=new Error('Missing');e.status=404;throw e;};await reconcile(b,true);
   if(b.state!=='unconfirmed'||b.request_id!=='same-request')throw Error('Missing task must preserve its request ID');
 })()`,context);
 console.log('PASS: uncertain response retains asset; lookup recovers without POST; missing task preserves request ID');
})().catch(e=>{console.error(e);process.exitCode=1;});
