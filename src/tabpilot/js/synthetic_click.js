/**
 * Click via DOM, for backends without trusted input (AppleScript).
 *
 * Dispatches the full pointer/mouse sequence a framework listens for rather than
 * only ``el.click()``, which never fires mousedown and so leaves most custom
 * dropdowns closed.
 *
 * opts: { selector?, text?, nth }
 */
(function (opts) {
  var located = (LOCATE)(opts);
  if (!located.ok) return located;

  var el;
  if (opts.selector) {
    el = document.querySelectorAll(opts.selector)[opts.nth || 0];
  }
  if (!el) {
    // Re-find by text the same way locate did.
    var needle = (opts.text || '').trim().toLowerCase();
    var scope = document.querySelectorAll(
      'button,a,[role="button"],input[type="submit"],input[type="button"],label,li,td,th,span,div');
    var matches = [];
    for (var i = 0; i < scope.length; i++) {
      var label = (scope[i].innerText || scope[i].value || '').replace(/\s+/g, ' ').trim().toLowerCase();
      if (label && (label === needle || label.indexOf(needle) !== -1)) matches.push(scope[i]);
    }
    matches.sort(function (a, b) { return (a.innerText || '').length - (b.innerText || '').length; });
    el = matches[opts.nth || 0];
  }
  if (!el) return { ok: false, error: 'Element vanished between locating and clicking it.' };

  var rect = el.getBoundingClientRect();
  var cx = rect.left + rect.width / 2;
  var cy = rect.top + rect.height / 2;
  var base = { bubbles: true, cancelable: true, view: window, clientX: cx, clientY: cy };

  try { el.focus({ preventScroll: true }); } catch (e) { /* not focusable */ }
  if (window.PointerEvent) {
    el.dispatchEvent(new PointerEvent('pointerdown', Object.assign({ pointerId: 1, isPrimary: true }, base)));
  }
  el.dispatchEvent(new MouseEvent('mousedown', base));
  if (window.PointerEvent) {
    el.dispatchEvent(new PointerEvent('pointerup', Object.assign({ pointerId: 1, isPrimary: true }, base)));
  }
  el.dispatchEvent(new MouseEvent('mouseup', base));
  el.dispatchEvent(new MouseEvent('click', base));

  return { ok: true, synthetic: true, tag: located.tag, text: located.text, matched: located.matched };
})
