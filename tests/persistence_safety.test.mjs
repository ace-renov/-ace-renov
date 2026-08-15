import assert from "node:assert/strict";

const STORAGE_KEY="ace_app_state_v17";
const STORAGE_META_KEY="ace_app_state_meta_v1";

const deepClone=v=>JSON.parse(JSON.stringify(v));
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

function createLocalStorage(initial={}){
  const store=new Map(Object.entries(initial));
  return {
    getItem:key=>store.has(key)?store.get(key):null,
    setItem:(key,val)=>store.set(key,String(val)),
    removeItem:key=>store.delete(key),
    dump:()=>Object.fromEntries(store.entries())
  };
}

function createHarness({initialLocalState,initialIdbState,hydrateDelayMs=0,writeFailError=null}={}){
  const localStorage=createLocalStorage(
    initialLocalState===undefined?{}:{[STORAGE_KEY]:JSON.stringify(initialLocalState)}
  );
  let appState={customers:[]};
  let idbState=initialIdbState===undefined?null:deepClone(initialIdbState);
  let saveStateToken=0;
  let hydrationStatePromise=null;
  let hydratedIndexedState=false;
  let persistStateQueue=Promise.resolve();
  let saveStatus="";

  function setSaveStatusText(text){saveStatus=text;}
  function isLargeDataUrl(v){return typeof v==="string"&&v.startsWith("data:");}
  function currentAceState(){return appState;}
  function applyLoadedState(s){appState=deepClone(s);}
  function statePayloadSummary(){return {dataUrlCount:0,dataUrlBytes:0};}
  function isStorageQuotaError(err){
    const name=String(err?.name||"");
    const msg=String(err?.message||"");
    return name==="QuotaExceededError"||name==="NS_ERROR_DOM_QUOTA_REACHED"||/quota|capacity|容量|storage/i.test(msg);
  }
  function compactStateForLocalStorage(sourceState){
    const state=deepClone(sourceState);
    const clearField=(obj,key)=>{
      if(!obj||!Object.prototype.hasOwnProperty.call(obj,key))return;
      if(!isLargeDataUrl(obj[key]))return;
      obj[key]=null;
    };
    (state.customers||[]).forEach(c=>(c.photos||[]).forEach(p=>{clearField(p,"data");clearField(p,"markedData");}));
    state._storageMeta={schema:1,usesIndexedDb:true,savedAt:new Date().toISOString()};
    return state;
  }
  async function writeFullStateToIndexedDb(state){
    if(writeFailError)throw writeFailError;
    idbState=deepClone(state);
  }
  async function readFullStateFromIndexedDb(){
    if(hydrateDelayMs>0)await sleep(hydrateDelayMs);
    return idbState?deepClone(idbState):null;
  }
  async function persistState(state,token){
    const compact=compactStateForLocalStorage(state);
    try{
      const summary=statePayloadSummary(state);
      await writeFullStateToIndexedDb(state);
      if(token!==saveStateToken)return;
      localStorage.setItem(STORAGE_KEY,JSON.stringify(compact));
      localStorage.setItem(STORAGE_META_KEY,JSON.stringify({
        savedAt:new Date().toISOString(),
        usesIndexedDb:true,
        dataUrlCount:summary.dataUrlCount,
        dataUrlBytes:summary.dataUrlBytes
      }));
      setSaveStatusText("保存済み");
    }catch(e){
      if(token!==saveStateToken)return;
      setSaveStatusText(isStorageQuotaError(e)?"端末の保存容量が不足しています":"保存失敗");
    }
  }
  function waitForIndexedHydration(){
    if(hydrationStatePromise)return hydrationStatePromise.catch(()=>{});
    return Promise.resolve();
  }
  function saveState(){
    const token=++saveStateToken;
    persistStateQueue=persistStateQueue
      .then(()=>waitForIndexedHydration())
      .then(()=>persistState(currentAceState(),token));
    return persistStateQueue;
  }
  function hydrateLargeStateFromIndexedDb(){
    if(hydratedIndexedState)return Promise.resolve(true);
    if(hydrationStatePromise)return hydrationStatePromise;
    hydrationStatePromise=(async()=>{
      try{
        const full=await readFullStateFromIndexedDb();
        if(!full){hydratedIndexedState=true;return;}
        applyLoadedState(full);
        hydratedIndexedState=true;
      }catch{}
    })().finally(()=>{hydrationStatePromise=null;});
    return hydrationStatePromise;
  }
  function loadState(){
    const raw=localStorage.getItem(STORAGE_KEY);
    if(!raw)return false;
    const s=JSON.parse(raw);
    applyLoadedState(s);
    if(s?._storageMeta?.usesIndexedDb)hydrateLargeStateFromIndexedDb();
    return true;
  }

  return {
    loadState,saveState,
    getAppState:()=>deepClone(appState),
    setAppState:s=>{appState=deepClone(s);},
    getIdbState:()=>deepClone(idbState),
    getStorageRaw:()=>localStorage.getItem(STORAGE_KEY),
    getStorageState:()=>JSON.parse(localStorage.getItem(STORAGE_KEY)),
    getSaveStatus:()=>saveStatus
  };
}

async function run(){
  const photoData="data:image/png;base64,AAA";
  const fullState={customers:[{id:1,photos:[{data:photoData}]}]};
  const compactState={customers:[{id:1,photos:[{data:null}]}],_storageMeta:{usesIndexedDb:true}};

  {
    const h=createHarness({initialLocalState:compactState,initialIdbState:fullState,hydrateDelayMs:30});
    h.loadState();
    await h.saveState();
    assert.equal(h.getIdbState().customers[0].photos[0].data,photoData,"起動時競合でIndexedDB画像が消えてはいけない");
  }

  {
    const previousFull={customers:[{id:1,photos:[{data:photoData}]}]};
    const h=createHarness({
      initialLocalState:previousFull,
      writeFailError:Object.assign(new Error("quota reached"),{name:"QuotaExceededError"})
    });
    h.loadState();
    h.setAppState({customers:[{id:1,photos:[{data:null}]}]});
    const before=h.getStorageRaw();
    await h.saveState();
    assert.equal(h.getStorageRaw(),before,"IndexedDB保存失敗時はlocalStorageを上書きしない");
    assert.equal(h.getSaveStatus(),"端末の保存容量が不足しています");
  }

  {
    const legacyFull={customers:[{id:1,photos:[{data:photoData}]}]};
    const h=createHarness({initialLocalState:legacyFull});
    h.loadState();
    await h.saveState();
    assert.equal(h.getIdbState().customers[0].photos[0].data,photoData,"初回移行で完全データをIndexedDBへ保存");
    assert.equal(h.getStorageState().customers[0].photos[0].data,null,"初回移行後のみcompactへ切替");
    assert.equal(h.getStorageState()._storageMeta.usesIndexedDb,true);
  }

  {
    const legacyFull={customers:[{id:1,photos:[{data:photoData}]}]};
    const h=createHarness({
      initialLocalState:legacyFull,
      writeFailError:Object.assign(new Error("quota reached"),{name:"QuotaExceededError"})
    });
    h.loadState();
    const before=h.getStorageRaw();
    await h.saveState();
    assert.equal(h.getStorageRaw(),before,"初回移行失敗時は元データ保持");
  }

  {
    const previousFull={customers:[{id:1,photos:[{data:photoData}]}]};
    const h=createHarness({
      initialLocalState:previousFull,
      writeFailError:Object.assign(new Error("quota reached"),{name:"QuotaExceededError"})
    });
    h.loadState();
    await h.saveState();
    assert.equal(h.getAppState().customers[0].photos[0].data,photoData,"保存失敗後の再読込でも画像保持");
  }

  console.log("PASS: persistence safety scenarios");
}

run().catch(e=>{
  console.error("FAIL:",e.message);
  process.exit(1);
});
