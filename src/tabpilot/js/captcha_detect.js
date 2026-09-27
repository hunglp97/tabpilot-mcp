/**
 * Scan the document for CAPTCHA widgets and challenges.
 * Returns {ok: true, candidates: [...]}
 */
(function (opts) {
  opts = opts || {};
  var candidates = [];
  var candidateIdx = 0;

  function nextId(prefix) {
    candidateIdx++;
    return (prefix || 'cand') + '_' + candidateIdx;
  }

  function getRect(el) {
    if (!el || typeof el.getBoundingClientRect !== 'function') return null;
    var r = el.getBoundingClientRect();
    return {
      x: Math.round(r.left),
      y: Math.round(r.top),
      width: Math.round(r.width),
      height: Math.round(r.height)
    };
  }

  function isVisible(el, rect) {
    if (!el) return false;
    var r = rect || getRect(el);
    if (!r || r.width <= 0 || r.height <= 0) return false;
    var style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity || '1') <= 0) {
      return false;
    }
    return true;
  }

  function getParam(url, param) {
    try {
      var u = new URL(url, window.location.href);
      return u.searchParams.get(param);
    } catch (e) {
      return null;
    }
  }

  // --- 1. Cloudflare Interstitial ---
  var isCfTitle = (document.title || '').indexOf('Just a moment...') !== -1;
  var cfStage = document.getElementById('challenge-stage') ||
                document.getElementById('challenge-running') ||
                document.getElementById('cf-challenge-running') ||
                document.querySelector('.cf-browser-verification');
  if (isCfTitle || cfStage) {
    var rect = cfStage ? getRect(cfStage) : {x: 0, y: 0, width: window.innerWidth, height: window.innerHeight};
    candidates.push({
      candidate_id: nextId('cf_interstitial'),
      provider: 'cloudflare',
      challenge_kind: 'interstitial',
      state: 'actionable',
      confidence: (isCfTitle && cfStage) ? 'high' : 'medium',
      signals: [
        isCfTitle ? 'title_just_a_moment' : null,
        cfStage ? 'cf_stage_element' : null
      ].filter(Boolean),
      visible: true,
      blocking: true,
      frame_ref: null,
      widget_ref: cfStage ? '#' + cfStage.id : 'body',
      rect_css: rect,
      sitekey: null,
      response_field_ref: null,
      available_strategies: ['passive_wait', 'agent_vision']
    });
  }

  // --- 2. Cloudflare Turnstile ---
  var turnstileFrames = Array.prototype.slice.call(
    document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]')
  );
  var turnstileContainers = Array.prototype.slice.call(
    document.querySelectorAll('.cf-turnstile, [data-turnstile-sitekey]')
  );
  var turnstileResponse = document.querySelector('input[name="cf-turnstile-response"]');

  if (turnstileFrames.length > 0 || turnstileContainers.length > 0 || turnstileResponse) {
    var primaryEl = turnstileFrames[0] || turnstileContainers[0] || turnstileResponse;
    var rect = primaryEl ? getRect(primaryEl) : null;
    var sitekey = null;
    if (turnstileContainers[0]) {
      sitekey = turnstileContainers[0].getAttribute('data-sitekey') ||
                turnstileContainers[0].getAttribute('data-turnstile-sitekey');
    }
    if (!sitekey && turnstileFrames[0]) {
      sitekey = getParam(turnstileFrames[0].src, 'sitekey');
    }
    var hasToken = turnstileResponse && turnstileResponse.value && turnstileResponse.value.length > 10;
    var isPassed = Boolean(hasToken);

    candidates.push({
      candidate_id: nextId('turnstile'),
      provider: 'cloudflare',
      challenge_kind: 'checkbox',
      state: isPassed ? 'passed' : 'actionable',
      confidence: 'high',
      signals: [
        turnstileFrames.length ? 'turnstile_iframe' : null,
        turnstileContainers.length ? 'turnstile_container' : null,
        turnstileResponse ? 'turnstile_response_field' : null,
        hasToken ? 'token_present' : null
      ].filter(Boolean),
      visible: primaryEl ? isVisible(primaryEl, rect) : true,
      blocking: !isPassed,
      frame_ref: turnstileFrames[0] ? { name: turnstileFrames[0].name || '', src: turnstileFrames[0].src } : null,
      widget_ref: turnstileContainers[0] ? ('.' + (turnstileContainers[0].className || '').split(' ')[0]) : (turnstileFrames[0] ? 'iframe' : null),
      rect_css: rect,
      sitekey: sitekey,
      response_field_ref: turnstileResponse ? 'input[name="cf-turnstile-response"]' : null,
      available_strategies: ['checkbox', 'passive_wait']
    });
  }

  // --- 3. Google reCAPTCHA v2 / Enterprise & Image Grid ---
  var recaptchaFrames = Array.prototype.slice.call(
    document.querySelectorAll('iframe[src*="google.com/recaptcha/api2/anchor"], iframe[src*="recaptcha/enterprise/anchor"]')
  );
  var recaptchaChallengeFrames = Array.prototype.slice.call(
    document.querySelectorAll('iframe[src*="google.com/recaptcha/api2/bframe"], iframe[src*="recaptcha/enterprise/bframe"]')
  );
  var recaptchaContainers = Array.prototype.slice.call(
    document.querySelectorAll('.g-recaptcha, [data-sitekey]:not(.cf-turnstile):not(.h-captcha)')
  );
  var recaptchaResponse = document.querySelector('textarea[name="g-recaptcha-response"]');

  // Check challenge container (iframe or div modal)
  var visibleChallenge = null;
  for (var i = 0; i < recaptchaChallengeFrames.length; i++) {
    var bf = recaptchaChallengeFrames[i];
    if (isVisible(bf)) {
      visibleChallenge = bf;
      break;
    }
  }
  if (!visibleChallenge) {
    var challengeDiv = document.querySelector('.recaptcha-challenge, [class*="image-grid-challenge"], .grid-container');
    if (challengeDiv && isVisible(challengeDiv)) {
      visibleChallenge = challengeDiv.closest('.recaptcha-challenge') || challengeDiv;
    }
  }

  if (visibleChallenge) {
    var chRect = getRect(visibleChallenge);
    candidates.push({
      candidate_id: nextId('recaptcha_grid'),
      provider: 'recaptcha',
      challenge_kind: 'image_grid',
      state: 'actionable',
      confidence: 'high',
      signals: ['recaptcha_challenge_visible', 'image_challenge'],
      visible: true,
      blocking: true,
      frame_ref: visibleChallenge.tagName === 'IFRAME' ? { name: visibleChallenge.name || '', src: visibleChallenge.src } : null,
      widget_ref: visibleChallenge.id ? '#' + visibleChallenge.id : '.recaptcha-challenge',
      rect_css: chRect,
      response_field_ref: (function() {
        var localResp = visibleChallenge.querySelector ? visibleChallenge.querySelector('textarea, input[type="hidden"]') : null;
        if (localResp) return localResp.name ? 'textarea[name="' + localResp.name + '"]' : (localResp.id ? '#' + localResp.id : null);
        return recaptchaResponse ? 'textarea[name="g-recaptcha-response"]' : 'textarea[name*="captcha-response"]';
      })(),
      available_strategies: ['agent_vision']
    });
  }

  if (recaptchaFrames.length > 0 || recaptchaContainers.length > 0 || (recaptchaResponse && !visibleChallenge)) {
    var anchor = recaptchaFrames[0];
    var rect = anchor ? getRect(anchor) : (recaptchaContainers[0] ? getRect(recaptchaContainers[0]) : null);
    var sitekey = null;
    if (anchor) sitekey = getParam(anchor.src, 'k');
    if (!sitekey && recaptchaContainers[0]) sitekey = recaptchaContainers[0].getAttribute('data-sitekey');

    var hasToken = recaptchaResponse && recaptchaResponse.value && recaptchaResponse.value.length > 10;
    var isPassed = Boolean(hasToken);

    candidates.push({
      candidate_id: nextId('recaptcha_checkbox'),
      provider: 'recaptcha',
      challenge_kind: 'checkbox',
      state: isPassed ? 'passed' : 'actionable',
      confidence: 'high',
      signals: [
        anchor ? 'recaptcha_anchor_iframe' : null,
        recaptchaContainers.length ? 'recaptcha_container' : null,
        recaptchaResponse ? 'recaptcha_response_field' : null,
        hasToken ? 'token_present' : null
      ].filter(Boolean),
      visible: anchor ? isVisible(anchor, rect) : true,
      blocking: !isPassed,
      frame_ref: anchor ? { name: anchor.name || '', src: anchor.src } : null,
      widget_ref: recaptchaContainers[0] ? '.g-recaptcha' : (anchor ? 'iframe[src*="anchor"]' : null),
      rect_css: rect,
      sitekey: sitekey,
      response_field_ref: recaptchaResponse ? 'textarea[name="g-recaptcha-response"]' : null,
      available_strategies: ['checkbox', 'agent_vision']
    });
  }

  // --- 4. hCaptcha ---
  var hcaptchaFrames = Array.prototype.slice.call(
    document.querySelectorAll('iframe[src*="hcaptcha.com"]')
  );
  var hcaptchaContainers = Array.prototype.slice.call(
    document.querySelectorAll('.h-captcha, [data-hcaptcha-widget-id]')
  );
  var hcaptchaResponse = document.querySelector('textarea[name="h-captcha-response"]');

  if (hcaptchaFrames.length > 0 || hcaptchaContainers.length > 0 || hcaptchaResponse) {
    var primaryEl = hcaptchaFrames[0] || hcaptchaContainers[0];
    var rect = primaryEl ? getRect(primaryEl) : null;
    var sitekey = null;
    if (hcaptchaContainers[0]) {
      sitekey = hcaptchaContainers[0].getAttribute('data-sitekey');
    }
    if (!sitekey && hcaptchaFrames[0]) {
      sitekey = getParam(hcaptchaFrames[0].src, 'sitekey');
    }
    var hasToken = hcaptchaResponse && hcaptchaResponse.value && hcaptchaResponse.value.length > 10;
    var isPassed = Boolean(hasToken);

    // Is it challenge popup or checkbox anchor?
    var isChallengeModal = false;
    for (var j = 0; j < hcaptchaFrames.length; j++) {
      var hf = hcaptchaFrames[j];
      if (hf.src.indexOf('challenge') !== -1 || (hf.title || '').indexOf('challenge') !== -1) {
        if (isVisible(hf, getRect(hf))) {
          isChallengeModal = true;
          primaryEl = hf;
          rect = getRect(hf);
          break;
        }
      }
    }

    candidates.push({
      candidate_id: nextId('hcaptcha'),
      provider: 'hcaptcha',
      challenge_kind: isChallengeModal ? 'image_grid' : 'checkbox',
      state: isPassed ? 'passed' : 'actionable',
      confidence: 'high',
      signals: [
        hcaptchaFrames.length ? 'hcaptcha_iframe' : null,
        hcaptchaContainers.length ? 'hcaptcha_container' : null,
        hcaptchaResponse ? 'hcaptcha_response_field' : null,
        hasToken ? 'token_present' : null
      ].filter(Boolean),
      visible: primaryEl ? isVisible(primaryEl, rect) : true,
      blocking: !isPassed,
      frame_ref: primaryEl && primaryEl.tagName === 'IFRAME' ? { name: primaryEl.name || '', src: primaryEl.src } : null,
      widget_ref: hcaptchaContainers[0] ? '.h-captcha' : null,
      rect_css: rect,
      sitekey: sitekey,
      response_field_ref: hcaptchaResponse ? 'textarea[name="h-captcha-response"]' : null,
      available_strategies: isChallengeModal ? ['agent_vision'] : ['checkbox', 'agent_vision']
    });
  }

  // --- 5. Image Text CAPTCHA (Chữ / số trong ảnh) ---
  var images = Array.prototype.slice.call(document.querySelectorAll('img, canvas'));
  images.forEach(function (img) {
    var idOrClass = ((img.id || '') + ' ' + (img.className || '') + ' ' + (img.getAttribute('alt') || '') + ' ' + (img.getAttribute('src') || '')).toLowerCase();
    if (idOrClass.indexOf('captcha') !== -1 || idOrClass.indexOf('sec-code') !== -1 || idOrClass.indexOf('verify-code') !== -1) {
      // Must find an associated input field within form or nearby container
      var form = img.closest('form') || img.parentElement;
      var input = form ? form.querySelector('input[type="text"], input:not([type])') : null;
      var rect = getRect(img);
      if (rect && isVisible(img, rect)) {
        candidates.push({
          candidate_id: nextId('image_text'),
          provider: 'custom',
          challenge_kind: 'image_text',
          state: 'actionable',
          confidence: input ? 'high' : 'medium',
          signals: ['captcha_image_attribute', input ? 'adjacent_input_field' : null].filter(Boolean),
          visible: true,
          blocking: true,
          frame_ref: null,
          widget_ref: img.id ? '#' + img.id : (img.className ? '.' + img.className.split(' ')[0] : 'img'),
          rect_css: rect,
          sitekey: null,
          response_field_ref: input ? (input.id ? '#' + input.id : 'input[name="' + (input.name || '') + '"]') : null,
          available_strategies: ['agent_vision', 'image_ocr']
        });
      }
    }
  });

  // --- 6. Slider Puzzle CAPTCHA ---
  var sliderEls = Array.prototype.slice.call(
    document.querySelectorAll('.geetest_slider, .puzzle-slider, .slider-handle, .captcha-slider, [class*="slider-captcha"]')
  );
  sliderEls.forEach(function (slider) {
    // Avoid false positives like input[type=range] or video/audio sliders
    if (slider.tagName === 'INPUT' && slider.type === 'range') return;
    if (slider.closest('.video-player, .audio-player, .carousel')) return;
    if (slider.matches && slider.matches('.slider-handle, .geetest_slider_btn') && slider.closest('.captcha-slider, .puzzle-slider, .geetest_slider')) return;

    var rect = getRect(slider);
    if (rect && isVisible(slider, rect)) {
      candidates.push({
        candidate_id: nextId('slider'),
        provider: 'custom',
        challenge_kind: 'slider',
        state: 'actionable',
        confidence: 'high',
        signals: ['slider_handle_class'],
        visible: true,
        blocking: true,
        frame_ref: null,
        widget_ref: slider.id ? '#' + slider.id : '.' + (slider.className || '').split(' ')[0],
        rect_css: rect,
        sitekey: null,
        response_field_ref: null,
        available_strategies: ['agent_vision', 'slider_cv']
      });
    }
  });

  return {
    ok: true,
    candidates: candidates
  };
})
