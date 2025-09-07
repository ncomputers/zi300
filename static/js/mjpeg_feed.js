(function(){
  function initMjpegFeeds(root=document){
    root.querySelectorAll('img.feed-img').forEach(img=>{img.src='/camera';});

  }
  if (typeof module !== "undefined") {
    module.exports = { initMjpegFeeds };
  } else {
    globalThis.initMjpegFeeds = initMjpegFeeds;
  }
  if (typeof document !== "undefined" && !globalThis.__TEST__) {
    initMjpegFeeds();
  }
})();
