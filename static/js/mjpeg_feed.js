(function(){
  function startFeed(img){
    const cam = img.dataset.cam;
    if(!cam) return;
    fetch(`/api/cameras/${cam}/show`,{method:'POST'}).catch(e=>console.error('show',e));
    img.src = `/api/cameras/${cam}/mjpeg`;
    const modal = img.closest('.modal');
    if(modal){
      modal.addEventListener('hidden.bs.modal',()=>{
        fetch(`/api/cameras/${cam}/hide`,{method:'POST'}).catch(e=>console.error('hide',e));
        img.removeAttribute('src');
      },{once:true});
    }
  }
  function initMjpegFeeds(root=document){
    root.querySelectorAll('img.feed-img').forEach(startFeed);
  }
  if(typeof module!=='undefined'){
    module.exports={initMjpegFeeds};
  }else{
    globalThis.initMjpegFeeds=initMjpegFeeds;
  }
  if(typeof document!=='undefined' && !globalThis.__TEST__){
    initMjpegFeeds();
  }
})();
