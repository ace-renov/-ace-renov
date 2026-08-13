
const CACHE_NAME="ace-v3.5-cache-v1";
const APP_SHELL=[
  "./",
  "./index.html",
  "./manifest.json",
  "./icon-192.png",
  "./icon-512.png"
];

self.addEventListener("install",event=>{
  event.waitUntil(caches.open(CACHE_NAME).then(cache=>cache.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate",event=>{
  event.waitUntil(
    caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE_NAME).map(k=>caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch",event=>{
  const req=event.request;
  if(req.method!=="GET")return;
  event.respondWith(
    fetch(req).then(res=>{
      const copy=res.clone();
      caches.open(CACHE_NAME).then(cache=>cache.put(req,copy));
      return res;
    }).catch(()=>caches.match(req).then(cached=>cached||caches.match("./index.html")))
  );
});

self.addEventListener("push",event=>{
  let data={title:"ACE",body:"新しい通知があります"};
  try{data=event.data.json()}catch(e){}
  event.waitUntil(self.registration.showNotification(data.title||"ACE",{
    body:data.body||"",
    data:data.data||{},
    badge:"",
    icon:""
  }));
});

self.addEventListener("notificationclick",event=>{
  event.notification.close();
  const target=(event.notification.data||{}).target||"home";
  const url="./index.html#"+encodeURIComponent(target);
  event.waitUntil(
    self.clients.matchAll({type:"window",includeUncontrolled:true}).then(clients=>{
      for(const c of clients){
        if("focus" in c){
          c.postMessage({type:"navigate",target});
          return c.focus();
        }
      }
      if(self.clients.openWindow)return self.clients.openWindow(url);
    })
  );
});

self.addEventListener("sync", event => {
  if (event.tag === "ace-offline-sync") {
    event.waitUntil(self.clients.matchAll({includeUncontrolled:true,type:"window"}).then(clients=>{
      clients.forEach(c=>c.postMessage({type:"ACE_FLUSH_OFFLINE_QUEUE"}));
    }));
  }
});
