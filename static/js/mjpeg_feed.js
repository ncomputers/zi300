(function(){
  function initMjpegFeeds(root=document){
    // leave existing <img> src untouched; template sets /api/cameras/{id}/mjpeg
  }
  if (typeof module !== "undefined") module.exports = { initMjpegFeeds };
  if (typeof document !== "undefined" && !globalThis.__TEST__) initMjpegFeeds();
})();

