/**
 * Find one element and report where to click it, scrolling it into view first.
 *
 * Returning viewport coordinates lets the caller dispatch a *trusted* mouse
 * event through CDP rather than calling ``el.click()``. Some frameworks and most
 * anti-bot layers treat synthetic clicks differently, and a dropdown that opens
 * on real mousedown will not open for a synthetic click at all.
 *
 * opts: { selector?, text?, nth, scroll, requireVisible }
 */
(function (opts) {
  var NTH = opts.nth || 0;

  function visible(el) {
    var rect = el.getBoundingClientRect();
    if (!rect.width && !rect.height) return false;
    var style = window.getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none' && style.pointerEvents !== 'none';
  }

  var candidates = [];

  if (opts.selector) {
    try {
      candidates = Array.prototype.slice.call(document.querySelectorAll(opts.selector));
    } catch (e) {
      return { ok: false, error: 'Invalid selector ' + JSON.stringify(opts.selector) + ': ' + e.message };
    }
  }

  if (opts.text) {
    var needle = opts.text.trim().toLowerCase();
    var scope = candidates.length
      ? candidates
      : Array.prototype.slice.call(document.querySelectorAll(
          'button,a,[role="button"],input[type="submit"],input[type="button"],label,li,td,th,span,div'));
    var exact = [], partial = [];
    for (var i = 0; i < scope.length; i++) {
      var label = (scope[i].innerText || scope[i].value || scope[i].getAttribute('aria-label') || '')
        .replace(/\s+/g, ' ').trim().toLowerCase();
      if (!label) continue;
      if (label === needle) exact.push(scope[i]);
      else if (label.indexOf(needle) !== -1) partial.push(scope[i]);
    }
    // Prefer exact matches, and among partial matches prefer the innermost
    // element, since an ancestor's text contains its descendants' text too.
    if (exact.length) {
      candidates = exact;
    } else {
      partial.sort(function (a, b) {
        return (a.innerText || '').length - (b.innerText || '').length;
      });
      candidates = partial;
    }
  }

  if (opts.requireVisible !== false) {
    var shown = candidates.filter(visible);
    if (shown.length) candidates = shown;
  }

  if (!candidates.length) {
    return { ok: false, error: 'Nothing matched.', matched: 0 };
  }
  if (NTH >= candidates.length) {
    return { ok: false, error: 'nth=' + NTH + ' but only ' + candidates.length + ' element(s) matched.',
             matched: candidates.length };
  }

  var el = candidates[NTH];
  if (opts.scroll !== false) {
    el.scrollIntoView({ block: 'center', inline: 'center' });
  }

  var rect = el.getBoundingClientRect();
  var viewportWidth = window.innerWidth || document.documentElement.clientWidth;
  var viewportHeight = window.innerHeight || document.documentElement.clientHeight;
  var inViewport = rect.bottom > 0 && rect.right > 0 &&
                   rect.top < viewportHeight && rect.left < viewportWidth;

  return {
    ok: true,
    matched: candidates.length,
    tag: el.tagName.toLowerCase(),
    text: (el.innerText || el.value || '').replace(/\s+/g, ' ').trim().slice(0, 120),
    disabled: !!el.disabled,
    visible: visible(el),
    inViewport: inViewport,
    x: rect.left + rect.width / 2,
    y: rect.top + rect.height / 2,
    rect: { x: Math.round(rect.x), y: Math.round(rect.y),
            w: Math.round(rect.width), h: Math.round(rect.height) },
    devicePixelRatio: window.devicePixelRatio || 1,
    scrollX: window.scrollX || 0,
    scrollY: window.scrollY || 0
  };
})
