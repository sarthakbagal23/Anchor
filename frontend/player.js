let yt = null;
let currentVideoId = null;
window.onYouTubeIframeAPIReady = () => {};

const Player = {
  load(videoId, atSec = 0) {
    if (currentVideoId === videoId && yt && yt.seekTo) { yt.seekTo(atSec, true); yt.playVideo(); return; }
    currentVideoId = videoId;
    const wrap = document.getElementById("player-wrap");
    wrap.innerHTML = '<div id="yt"></div>';
    const YT = window.YT;
    if (!YT || !YT.Player) { wrap.innerHTML = '<div class="player-placeholder">YouTube player loading…</div>'; return; }
    yt = new YT.Player("yt", { width:"100%", height:"215", videoId, playerVars:{start:Math.floor(atSec),autoplay:1,modestbranding:1,rel:0}, events:{onReady:e=>e.target.seekTo(atSec,true)} });
  },
  seek(sec) { if (yt && yt.seekTo) { yt.seekTo(sec,true); yt.playVideo(); } },
  pause() { if (yt && yt.pauseVideo) { try { yt.pauseVideo(); } catch (e) {} } },
  currentTime() { return yt && yt.getCurrentTime ? yt.getCurrentTime() : 0; },
};
window.Player = Player;
