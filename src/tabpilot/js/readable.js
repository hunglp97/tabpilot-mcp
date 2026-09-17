/**
 * Extract a tab's content as markdown, text, or HTML, under a character budget.
 *
 * Runs on the page so only the finished, budgeted string crosses the wire — the
 * whole point is that an agent never pays for a DOM it did not ask for.
 *
 * opts: { selector?, mode: 'readable'|'text'|'html', maxChars, includeLinks }
 */
(function (opts) {
  var MAX = opts.maxChars || 20000;
  var MODE = opts.mode || 'readable';
  var INCLUDE_LINKS = opts.includeLinks !== false;

  var DROP = 'script,style,noscript,template,svg,canvas,iframe,object,embed,link,meta,' +
             'nav,header,footer,aside,[aria-hidden="true"],[hidden],.advertisement,[role="banner"],' +
             '[role="navigation"],[role="complementary"],[role="contentinfo"]';

  function fail(reason) { return { ok: false, error: reason }; }

  /* --- pick a root ------------------------------------------------------- */

  function semanticRoot() {
    var selectors = ['main', 'article', '[role="main"]', '#main', '#content', '.content', '#main-content'];
    for (var i = 0; i < selectors.length; i++) {
      var found = document.querySelector(selectors[i]);
      if (found && (found.innerText || '').trim().length > 200) return found;
    }
    return null;
  }

  /** Text length discounted by link density — boilerplate is mostly links. */
  function score(node) {
    var text = (node.innerText || '').trim();
    if (text.length < 140) return -1;
    var linkChars = 0;
    var links = node.querySelectorAll('a');
    for (var i = 0; i < links.length; i++) linkChars += (links[i].innerText || '').length;
    var density = linkChars / Math.max(text.length, 1);
    if (density > 0.55) return -1;
    return text.length * (1 - density);
  }

  function bestRoot() {
    var semantic = semanticRoot();
    if (semantic) return semantic;
    var best = document.body, bestScore = -1;
    var candidates = document.querySelectorAll('div,section,td,main,article');
    for (var i = 0; i < candidates.length; i++) {
      var s = score(candidates[i]);
      if (s > bestScore) { bestScore = s; best = candidates[i]; }
    }
    return best || document.body;
  }

  var root;
  if (opts.selector) {
    root = document.querySelector(opts.selector);
    if (!root) return fail('No element matches selector ' + JSON.stringify(opts.selector) + '.');
  } else {
    root = MODE === 'readable' ? bestRoot() : document.body;
  }
  if (!root) return fail('The page has no body to read yet.');

  /* --- raw modes --------------------------------------------------------- */

  function truncate(s) {
    s = s.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
    if (s.length <= MAX) return { text: s, truncated: false, fullLength: s.length };
    return { text: s.slice(0, MAX), truncated: true, fullLength: s.length };
  }

  if (MODE === 'html') {
    var cut = truncate(root.outerHTML || '');
    return { ok: true, mode: MODE, title: document.title, url: location.href,
             content: cut.text, truncated: cut.truncated, fullLength: cut.fullLength };
  }
  if (MODE === 'text') {
    var cutText = truncate(root.innerText || '');
    return { ok: true, mode: MODE, title: document.title, url: location.href,
             content: cutText.text, truncated: cutText.truncated, fullLength: cutText.fullLength };
  }

  /* --- readable: DOM -> markdown ---------------------------------------- */

  var clone = root.cloneNode(true);
  var junk = clone.querySelectorAll(DROP);
  for (var k = 0; k < junk.length; k++) {
    if (junk[k].parentNode) junk[k].parentNode.removeChild(junk[k]);
  }

  var out = [];

  function inline(node) {
    if (node.nodeType === 3) return node.nodeValue.replace(/\s+/g, ' ');
    if (node.nodeType !== 1) return '';
    var tag = node.tagName.toLowerCase();
    var inner = '';
    for (var i = 0; i < node.childNodes.length; i++) inner += inline(node.childNodes[i]);

    if (tag === 'br') return '\n';
    if (tag === 'strong' || tag === 'b') return inner.trim() ? '**' + inner.trim() + '**' : '';
    if (tag === 'em' || tag === 'i') return inner.trim() ? '*' + inner.trim() + '*' : '';
    if (tag === 'code') return inner.trim() ? '`' + inner.trim() + '`' : '';
    if (tag === 'img') {
      var alt = (node.getAttribute('alt') || '').trim();
      return alt ? '![' + alt + ']' : '';
    }
    if (tag === 'a' && INCLUDE_LINKS) {
      var href = node.getAttribute('href') || '';
      var label = inner.trim();
      if (!label) return '';
      if (!href || href.charAt(0) === '#' || href.indexOf('javascript:') === 0) return label;
      try { href = new URL(href, location.href).href; } catch (e) { /* keep as written */ }
      return '[' + label + '](' + href + ')';
    }
    return inner;
  }

  function push(text) {
    text = text.replace(/[ \t]+/g, ' ').trim();
    if (text) out.push(text);
  }

  function walk(node, listDepth, listKind, listIndex) {
    if (node.nodeType === 3) { push(node.nodeValue); return; }
    if (node.nodeType !== 1) return;

    var tag = node.tagName.toLowerCase();

    if (/^h[1-6]$/.test(tag)) {
      push(new Array(parseInt(tag.charAt(1), 10) + 1).join('#') + ' ' + inline(node).trim());
      return;
    }
    if (tag === 'p') { push(inline(node)); return; }
    if (tag === 'hr') { out.push('---'); return; }
    if (tag === 'pre') {
      var code = (node.innerText || '').replace(/\s+$/, '');
      if (code) out.push('```\n' + code + '\n```');
      return;
    }
    if (tag === 'blockquote') {
      var quoted = inline(node).trim();
      if (quoted) push('> ' + quoted.replace(/\n/g, '\n> '));
      return;
    }
    if (tag === 'ul' || tag === 'ol') {
      var items = node.children, counter = 1;
      for (var i = 0; i < items.length; i++) {
        if (items[i].tagName && items[i].tagName.toLowerCase() === 'li') {
          walk(items[i], listDepth + 1, tag, counter++);
        }
      }
      return;
    }
    if (tag === 'li') {
      var indent = new Array(Math.max(listDepth - 1, 0) + 1).join('  ');
      var bullet = listKind === 'ol' ? listIndex + '. ' : '- ';
      var own = '';
      for (var j = 0; j < node.childNodes.length; j++) {
        var child = node.childNodes[j];
        var childTag = child.nodeType === 1 ? child.tagName.toLowerCase() : '';
        if (childTag === 'ul' || childTag === 'ol') continue;
        own += inline(child);
      }
      push(indent + bullet + own.trim());
      var nested = node.querySelectorAll(':scope > ul, :scope > ol');
      for (var n = 0; n < nested.length; n++) walk(nested[n], listDepth, listKind, 1);
      return;
    }
    if (tag === 'table') {
      var rows = node.querySelectorAll('tr');
      var rendered = [];
      for (var r = 0; r < rows.length; r++) {
        var cells = rows[r].querySelectorAll('th,td');
        if (!cells.length) continue;
        var line = [];
        for (var c = 0; c < cells.length; c++) line.push(inline(cells[c]).replace(/\|/g, '\\|').trim());
        rendered.push('| ' + line.join(' | ') + ' |');
        if (r === 0) {
          var sep = [];
          for (var d = 0; d < cells.length; d++) sep.push('---');
          rendered.push('| ' + sep.join(' | ') + ' |');
        }
      }
      if (rendered.length) out.push(rendered.join('\n'));
      return;
    }
    if (tag === 'a' || tag === 'span' || tag === 'strong' || tag === 'b' ||
        tag === 'em' || tag === 'i' || tag === 'code' || tag === 'label') {
      push(inline(node));
      return;
    }

    for (var m = 0; m < node.childNodes.length; m++) walk(node.childNodes[m], listDepth, listKind, listIndex);
  }

  walk(clone, 0, null, 1);

  var markdown = out.join('\n\n');
  var cutMd = truncate(markdown);
  return {
    ok: true,
    mode: 'readable',
    title: document.title,
    url: location.href,
    content: cutMd.text,
    truncated: cutMd.truncated,
    fullLength: cutMd.fullLength,
    rootTag: root.tagName ? root.tagName.toLowerCase() : null
  };
})
