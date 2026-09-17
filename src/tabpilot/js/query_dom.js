/**
 * Return a structured, budgeted list of elements matching a selector.
 *
 * The alternative an agent reaches for by default — dumping innerText or
 * innerHTML and reading it — burns thousands of tokens to answer questions like
 * "is the submit button disabled". This returns only the fields asked for.
 *
 * opts: { selector, limit, attrs?, textMax, visibleOnly }
 */
(function (opts) {
  var LIMIT = opts.limit || 30;
  var TEXT_MAX = opts.textMax || 200;
  var ATTRS = opts.attrs && opts.attrs.length ? opts.attrs : null;

  var nodes;
  try {
    nodes = document.querySelectorAll(opts.selector);
  } catch (e) {
    return { ok: false, error: 'Invalid selector ' + JSON.stringify(opts.selector) + ': ' + e.message };
  }

  function isVisible(el) {
    var rect = el.getBoundingClientRect();
    if (!rect.width && !rect.height) return false;
    var style = window.getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
  }

  function clip(text) {
    text = (text || '').replace(/\s+/g, ' ').trim();
    return text.length > TEXT_MAX ? text.slice(0, TEXT_MAX) + '…' : text;
  }

  var results = [];
  var skippedInvisible = 0;

  for (var i = 0; i < nodes.length && results.length < LIMIT; i++) {
    var el = nodes[i];
    var visible = isVisible(el);
    if (opts.visibleOnly && !visible) { skippedInvisible++; continue; }

    var rect = el.getBoundingClientRect();
    var item = {
      index: i,
      tag: el.tagName.toLowerCase(),
      text: clip(el.innerText || el.textContent),
      visible: visible,
      rect: { x: Math.round(rect.x), y: Math.round(rect.y),
              w: Math.round(rect.width), h: Math.round(rect.height) }
    };

    var attributes = {};
    if (ATTRS) {
      for (var a = 0; a < ATTRS.length; a++) {
        var value = el.getAttribute(ATTRS[a]);
        if (value !== null) attributes[ATTRS[a]] = value;
      }
    } else {
      // A default set that answers most "what is the state of this control" questions.
      var defaults = ['id', 'name', 'type', 'href', 'value', 'placeholder', 'role',
                      'aria-label', 'data-testid', 'class'];
      for (var d = 0; d < defaults.length; d++) {
        var v = el.getAttribute(defaults[d]);
        if (v !== null && v !== '') attributes[defaults[d]] = clip(v);
      }
      if (el.disabled) attributes.disabled = 'true';
      if (el.checked) attributes.checked = 'true';
      if (el.selected) attributes.selected = 'true';
    }
    item.attrs = attributes;
    results.push(item);
  }

  return {
    ok: true,
    selector: opts.selector,
    matched: nodes.length,
    returned: results.length,
    skippedInvisible: skippedInvisible,
    truncated: nodes.length > results.length + skippedInvisible,
    elements: results
  };
})
