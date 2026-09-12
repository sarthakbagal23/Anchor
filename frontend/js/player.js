/* player.js — CORE. Two things make the YouTube IFrame API annoying, both
 * handled here:
 *
 * 1. window.YT loads asynchronously and may not be ready the first time a
 *    citation is clicked. If we call `new YT.Player` before the API script
 *    has finished loading, it silently does nothing. So `load()` checks
 *    readiness and, if not ready, stashes the request and replays it from
 *    window.onYouTubeIframeAPIReady.
 * 2. Re-creating the iframe on every citation click causes a visible reload
 *    flash even when it's the same video. If the video is already loaded,
 *    we just seek the existing player instead of rebuilding it.
 */
(function () {
  let ytPlayer = null;
  let currentVideoId = null;
  let pending = null; // { videoId, atSec } queued until the API is ready

  window.onYouTubeIframeAPIReady = function () {
    if (pending) {
      const { videoId, atSec } = pending;
      pending = null;
      load(videoId, atSec);
    }
  };

  function wrap() {
    const el = document.getElementById("player-wrap");
    el.innerHTML = '<div id="yt"></div>';
    return el;
  }

  function load(videoId, atSec = 0) {
    if (currentVideoId === videoId && ytPlayer && ytPlayer.seekTo) {
      ytPlayer.seekTo(atSec, true);
      ytPlayer.playVideo();
      return;
    }
    currentVideoId = videoId;
    if (!window.YT || !window.YT.Player) {
      pending = { videoId, atSec };
      document.getElementById("player-wrap").innerHTML = '<div class="panel-empty">Loading player…</div>';
      return;
    }
    wrap();
    ytPlayer = new YT.Player("yt", {
      width: "100%",
      height: "215",
      videoId,
      playerVars: { start: Math.floor(atSec), autoplay: 1, modestbranding: 1, rel: 0 },
      events: { onReady: (e) => e.target.seekTo(atSec, true) },
    });
  }

  function seek(sec) {
    if (ytPlayer && ytPlayer.seekTo) { ytPlayer.seekTo(sec, true); ytPlayer.playVideo(); }
  }

  function pause() {
    if (ytPlayer && ytPlayer.pauseVideo) {
      try { ytPlayer.pauseVideo(); } catch { /* player not fully initialized yet — nothing to pause */ }
    }
  }

  function currentTime() {
    return ytPlayer && ytPlayer.getCurrentTime ? ytPlayer.getCurrentTime() : 0;
  }

  window.Player = { load, seek, pause, currentTime };
})();
