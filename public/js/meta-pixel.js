/**
 * Meta Pixel (client-side) — loaded dynamically using the Pixel ID configured
 * in the admin panel (/admin → Integração Meta Conversions API).
 *
 * Why dynamic: the Pixel ID is admin-editable and lives in the database
 * (app_settings), not hardcoded in static HTML — so every page asks the
 * backend for the current config before deciding whether to load the pixel.
 *
 * This complements (does not replace) the server-side Conversions API (CAPI)
 * already wired into registration / trial-start / purchase. Meta recommends
 * running Pixel + CAPI together — using a shared `event_id` lets Meta
 * deduplicate events that are reported from both the browser and the server.
 *
 * This file ONLY fires top-of-funnel browser events that have no server-side
 * equivalent today: PageView, ViewContent, InitiateCheckout. Lead / StartTrial
 * / Purchase remain server-side only (more reliable — can't be blocked by
 * ad-blockers / browser privacy modes), so we don't fire them here to avoid
 * double counting.
 */
(function () {
  function randomId(prefix) {
    return prefix + '-' + Date.now() + '-' + Math.random().toString(36).slice(2, 10);
  }

  // Safe wrapper: no-ops silently if the pixel never loaded (not configured,
  // not enabled, blocked by an ad-blocker, network error, etc).
  window.metaTrack = function (eventName, params, eventId) {
    if (typeof window.fbq !== 'function') return;
    try {
      if (eventId) {
        window.fbq('track', eventName, params || {}, { eventID: eventId });
      } else {
        window.fbq('track', eventName, params || {});
      }
    } catch (e) { /* never break the page because of tracking */ }
  };
  window.metaEventId = randomId;

  fetch('/api/public/meta-pixel')
    .then(function (r) { return r.json(); })
    .then(function (cfg) {
      if (!cfg || !cfg.enabled || !cfg.pixel_id) return;

      /* eslint-disable */
      !function (f, b, e, v, n, t, s) {
        if (f.fbq) return; n = f.fbq = function () {
          n.callMethod ? n.callMethod.apply(n, arguments) : n.queue.push(arguments)
        };
        if (!f._fbq) f._fbq = n; n.push = n; n.loaded = !0; n.version = '2.0';
        n.queue = []; t = b.createElement(e); t.async = !0; t.src = v;
        s = b.getElementsByTagName(e)[0]; s.parentNode.insertBefore(t, s)
      }(window, document, 'script', 'https://connect.facebook.net/en_US/fbevents.js');
      /* eslint-enable */

      window.fbq('init', cfg.pixel_id);
      window.fbq('track', 'PageView');
    })
    .catch(function () { /* integration not reachable — just skip silently */ });
})();
