# TabPilot — Thiết kế tính năng xử lý captcha

- **Ngày**: 2026-09-27
- **Trạng thái**: Draft — chờ người dùng review
- **Phạm vi**: Thêm khả năng phát hiện và tự giải captcha (unattended) vào TabPilot MCP
- **Repo**: `tabpilot-mcp`
- **Hướng đã chọn**: A — module `captcha.py` + 2 tool mới, triển khai theo giai đoạn

---

## 1. Mục tiêu

Cho phép agent đang điều khiển Chrome thật (qua TabPilot) **phát hiện** khi trang xuất hiện
captcha và **tự giải** captcha đó mà không cần người ngồi trước máy, phục vụ chạy 24/7 trên
server Ubuntu headless.

### Mục tiêu cụ thể

1. `detect_captcha` — nhận diện loại captcha đang hiển thị trên một tab, kèm độ tin cậy và
   thông tin định vị (iframe, toạ độ, sitekey, response field).
2. `solve_captcha` — orchestrate việc giải: chọn solver phù hợp, chạy, **verify** kết quả, và
   báo cáo trung thực khi thất bại (không báo thành công giả).
3. Hỗ trợ 5 loại captcha, chia theo giai đoạn P0–P4:
   - Cloudflare Turnstile / challenge ("Verify you are human", "Just a moment…")
   - Google reCAPTCHA v2 (checkbox + audio challenge)
   - hCaptcha (checkbox + audio challenge)
   - Captcha ảnh chữ/số (OCR)
   - Slider / puzzle kéo thả (CV)
4. Tuân thủ kiến trúc sẵn có của repo: payload `.js` là file riêng, lỗi trả về dạng
   `ERROR [CODE] … How to fix`, capability-gating, và **base install vẫn zero-dependency**.

### Phi mục tiêu (Non-goals)

- Không dùng bất kỳ dịch vụ giải captcha trả phí nào (2Captcha, CapSolver, Anti-Captcha…).
- Không spoofing fingerprint, không stealth/anti-detection để né bot detection (xem mục 11).
- Không hứa hẹn giải được mọi captcha; các managed challenge phức tạp có thể vẫn chặn.
- Không nhúng auto-detect vào các tool cũ (`click`, `fill`, `wait_for` giữ nguyên hành vi).
- Không tự động bypass captcha cho site bên thứ ba mà không có sự tham gia của agent/người dùng.

---

## 2. Bối cảnh kỹ thuật (ràng buộc)

Các ràng buộc dưới đây định hình toàn bộ thiết kế và đã được kiểm chứng trong codebase hiện tại.

1. **Captcha widget nằm trong iframe cross-origin.** Từ JS ở trang mẹ chỉ truy cập được phần tử
   `<iframe>` và lấy **bounding rect** của nó — không đọc được DOM bên trong. Do đó:
   - Click checkbox phải là **click trusted theo toạ độ** (`Input.dispatchMouseEvent`) vào tâm
     checkbox trong iframe.
   - Muốn đọc nút audio / input bên trong iframe phải **eval trong frame context** (P2, mục 5).
2. **`_command` hiện tại chỉ request/response, bỏ qua event.** `DESIGN.md` ghi rõ việc thêm
   `Network`/`Log` event capture "is a real change, not a tool". Vì vậy P2 dùng
   `Page.getFrameTree` + `Page.createIsolatedWorld` + `Runtime.evaluate(contextId=…)` — toàn bộ
   là request/response, **không cần event subscription**.
3. **AppleScript backend không có trusted input và không có screenshot.** Tính năng captcha buộc
   phải **CDP-only**, gate bằng capability.
4. **Verification là heuristic.** Click "thành công" không đồng nghĩa captcha đã được giải. Mọi
   solver phải verify trước khi báo thành công.
5. **Zero-dependency ở base install.** Solver cần ML/CV phải là optional extras + lazy import.

---

## 3. Kiến trúc

Thêm module `captcha.py` cùng cấp với `interact.py` / `evidence.py`, thêm payload
`js/captcha_detect.js`, và mở rộng lớp backend cho P2.

```
server.py              + detect_captcha, solve_captcha  (19 tools)
  ├── extract.py         read_tab, query_dom
  ├── interact.py        click, fill, select_option, fill_matrix, wait_for
  ├── evidence.py        screenshot
  └── captcha.py         detection + solver registry + orchestrator      <-- MỚI
        │
     session.py          (không đổi)
        │
     tabs.py             (không đổi)
        │
   backends/
     base.py             + Capability.FRAME_EVAL + evaluate_in_frame()   <-- SỬA (P2)
     cdp.py              + frame eval, advertise FRAME_EVAL              <-- SỬA (P2)
     applescript.py      (không hỗ trợ → refuse qua capability)
        │
   js/captcha_detect.js  <-- MỚI
```

Quy tắc "không tầng nào vượt qua tầng dưới" được giữ nguyên: `captcha.py` biết về capability và
`solve_result`, không biết về WebSocket; `backends/*` không biết về captcha.

### Vì sao không nhúng vào tool cũ (hướng B)

Tool cũ như `click` phải "làm đúng chỗ đã chỉ định". Tự động phát hiện captcha rồi chèn hành vi
(click checkbox, chờ, retry) sẽ phá vỡ nguyên tắc đó, khó test, và tạo hành vi "ma thuật" không
đoán trước. Hai tool tường minh giữ cho luồng điều khiển nằm ở phía agent.

---

## 4. Data model

### 4.1 `CaptchaDetection`

```python
@dataclass
class CaptchaDetection:
    type: str            # recaptcha_v2 | hcaptcha | turnstile | cf_interstitial | image | slider | unknown
    confidence: str      # high | medium | low
    signals: list[str]   # các dấu hiệu đã match, để giải thích/debug
    # Định vị (tuỳ loại, có thể None)
    frame_selector: str | None      # selector của <iframe> chứa widget
    anchor: dict | None             # {"x": float, "y": float} tâm checkbox (viewport coords)
    rect: dict | None               # {"x","y","w","h"} của iframe/widget
    sitekey: str | None
    response_field: str | None      # "g-recaptcha-response" | "h-captcha-response" | "cf-turnstile-response"
    image_selector: str | None      # cho captcha ảnh
    slider_selector: str | None     # cho slider
    page_marker: str | None         # ví dụ "cf_interstitial" theo URL/title/#challenge-running
```

### 4.2 `SolveResult`

```python
@dataclass
class SolveResult:
    success: bool
    solver: str                  # tên solver đã chạy (hoặc "none")
    attempts: int
    elapsed_ms: int
    detail: str                  # mô tả trung thực đã làm gì / quan sát gì
    verified_by: list[str]       # các bằng chứng verify đã đạt (token, url_change, marker_gone…)
```

### 4.3 `Solver` (giao diện)

```python
class Solver(Protocol):
    name: str
    requires: frozenset[str]                 # Capability cần có (vd TRUSTED_INPUT, SCREENSHOT, FRAME_EVAL)
    optional_deps: tuple[str, ...]           # tên import cần cho solver (rỗng nếu không cần)

    def can_handle(self, detection: CaptchaDetection) -> bool: ...

    def solve(
        self,
        session: Session,
        tab: TabInfo,
        detection: CaptchaDetection,
        *,
        max_attempts: int,
        timeout_ms: int,
        config: Config,
    ) -> SolveResult: ...
```

---

## 5. Backend: `evaluate_in_frame` (P2)

Thêm vào `Capability`:

```python
FRAME_EVAL = "frame_eval"
"""Evaluate JavaScript inside a specific (possibly cross-origin) frame."""
```

`Backend.evaluate_in_frame` mặc định raise `NotImplementedError`. CDP implement:

```
1. Page.getFrameTree                      → lấy danh sách frame {id, url, parentId}
2. Chọn frame khớp frame_url_regex (bỏ frame gốc)
3. Page.createIsolatedWorld({frameId, worldName: "tabpilot"})
   → {executionContextId}
4. Runtime.evaluate({expression, contextId, returnByValue: true,
                     awaitPromise: true, userGesture: true, timeout})
5. Xử lý exceptionDetails giống eval_js
```

Chỉ dùng request/response, không đăng ký event. CDP advertise `FRAME_EVAL`; AppleScript không →
`session.require(FRAME_EVAL)` trả `ERROR [UNSUPPORTED_BY_BACKEND]` kèm remedy hiện có.

Dùng cho audio challenge: trong context reCAPTCHA/hCaptcha (same-origin với
`www.google.com` / `hcaptcha.com`), `fetch(audio.src).then(r => r.blob())` rồi đọc base64 — không
cần luân chuyển cookie qua Python.

---

## 6. Detection (P0)

### 6.1 Payload `js/captcha_detect.js`

Một function expression theo đúng chuẩn payload (`function (opts) { ... }`), trả envelope
`{ok: true, candidates: [...]}`. Chạy DOM thuần ở trang mẹ, **không** đọc inside iframe.

Dấu hiệu nhận diện (theo thứ tự ưu tiên, ghi vào `signals`):

| Loại | Dấu hiệu |
|:--|:--|
| `recaptcha_v2` | `iframe[src*="google.com/recaptcha"]`, `.g-recaptcha`, `textarea#g-recaptcha-response`, `.grecaptcha-badge` |
| `hcaptcha` | `iframe[src*="hcaptcha.com"]`, `.h-captcha`, `textarea[name="h-captcha-response"]` |
| `turnstile` | `iframe[src*="challenges.cloudflare.com"]`, `.cf-turnstile`, `input[name="cf-turnstile-response"]` |
| `cf_interstitial` | title/URL "Just a moment", `#challenge-running`, `#challenge-form`, `.cf-challenge` |
| `image` | `<img>` có `id/class/src` chứa `captcha\|code\|verify\|securimage`, gần đó có text input + link refresh |
| `slider` | `id/class` chứa `slider\|geetest\|nc_\|yidun\|verify-wrap`, container có handle kéo được |

Mỗi candidate kèm `rect`, `anchor` (tâm phần tử), `sitekey` (parse từ src/query hoặc
`data-sitekey`), `response_field`.

### 6.2 Chấm điểm trong `captcha.py`

- `type` = candidate có confidence cao nhất; khi nhiều loại cùng xuất hiện, ưu tiên
  `cf_interstitial` > `turnstile` > `recaptcha_v2`/`hcaptcha` > `slider` > `image`.
- `confidence`:
  - `high`: matched iframe src + (response field hoặc page marker).
  - `medium`: matched DOM marker hoặc heuristic class/attribute.
  - `low`: chỉ heuristic yếu.
- Nếu không có candidate nào → `type="unknown"`, `confidence="low"`.

Detection chỉ cần `Capability.EVAL`.

---

## 7. Solver registry (P0–P4)

Thứ tự chạy mặc định (`strategy="auto"`), rẻ/chắc nhất trước:

1. `cloudflare_wait` (P1, không dep)
2. `turnstile_click` (P1, không dep)
3. `recaptcha_checkbox` (P1, không dep)
4. `hcaptcha_checkbox` (P1, không dep)
5. `audio_challenge` (P2, STT)
6. `image_ocr` (P3, OCR)
7. `slider_cv` (P4, OpenCV)

Agent có thể ép bằng `strategy="<tên solver>"` để debug hoặc chỉ định chính xác.

### 7.0 Orchestrator

```
solve_captcha(tab, strategy="auto", max_attempts, timeout_ms, activate_on_fail):
  detection = detect(tab)
  if detection.type == "unknown":
      # cho cơ hội xuất hiện muộn (interstitial hiện sau vài trăm ms)
      detection = wait_for_captcha(tab, short_window)
  if detection.type == "unknown":
      return SolveResult(success=False, solver="none",
                         detail="Không phát hiện captcha trên tab này.")
  solver = pick(strategy, detection)
  if solver is None:
      return "không có solver phù hợp cho <type> (cần extra ...)"  # MISSING_DEP / UNSUPPORTED
  session.require(*solver.requires)          # refuse sạch nếu thiếu capability
  ensure_deps(solver)                        # lazy import; thiếu → MissingDependencyError
  result = solver.solve(tab, detection, max_attempts, timeout_ms)
  if not result.success and activate_on_fail:
      session.backend.activate_tab(tab.id)   # fallback cho người, vô hại khi unattended
  return result
```

**Verify** (chung): chạy lại detection + kiểm tra ít nhất một bằng chứng cụ thể:

- `token_present`: response field có giá trị non-empty.
- `marker_gone`: iframe/interstitial đã biến mất khỏi DOM.
- `url_changed` / `title_changed`: Cloudflare chuyển sang trang đích.
- `success_marker`: class/attribute/hidden input mà site dùng để báo pass.

Không đạt bằng chứng nào → `success=False` dù thao tác click không lỗi.

### 7.1 P1 — solver không cần dependency

**`cloudflare_wait`**
- Detect URL/title interstitial hoặc `#challenge-running`.
- Poll (mặc định 200ms, tối đa `timeout_ms`) tới khi marker biến mất và URL/title đổi.
- Không click gì. Nhiều managed challenge tự qua khi browser là Chrome thật đã đăng nhập.

**`turnstile_click`**
- Lấy `frame_selector` Turnstile, tính `anchor` = tâm checkbox (dịch vào trong iframe một khoảng
  an toàn, tránh biên).
- Click trusted tại `anchor`.
- Verify: `cf-turnstile-response` non-empty **hoặc** iframe biến mất.
- Nếu widget không hiển thị (invisible mode) → chỉ chờ token (`turnstile_wait` nhánh con).

**`recaptcha_checkbox` / `hcaptcha_checkbox`**
- Click anchor checkbox trong iframe.
- Chờ ~1.5–3s. Verify `g-recaptcha-response` / `h-captcha-response` non-empty.
- Nếu không có token → nghĩa là trang đã mở **image challenge**; trả `success=False` với
  `detail="checkbox không đủ; cần audio challenge (P2) hoặc OCR ảnh"` **thay vì** báo thành công giả.

### 7.2 P2 — `audio_challenge` (STT local)

Extras: `captcha-stt` (`faster-whisper` mặc định, hỗ trợ `vosk` như lựa chọn thay thế).

```
for attempt in range(max_attempts):
    evaluate_in_frame(frame, "mở challenge nếu chưa mở; bấm nút audio (id audio-button / aria-label)")
    audio_b64 = evaluate_in_frame(frame, "fetch(audio.src).then(r=>r.blob()).then(b=>FileReader->base64)")
    text = stt_transcribe(base64_decode(audio_b64))    # chuỗi số/chữ
    evaluate_in_frame(frame, "focus input câu trả lời")
    session.backend.insert_text(tab, text)             # trusted input
    evaluate_in_frame(frame, "bấm nút Verify")
    if verify(): return success
    evaluate_in_frame(frame, "bấm Reload để lấy audio mới")
return failure
```

- `stt_transcribe` lazy-import `faster_whisper`; thiếu → `MissingDependencyError`.
- Model mặc định `tiny` (config `captcha_stt_model`), tải model lần đầu (ghi chú trong docs).
- Chuẩn hoá kết quả STT: bỏ khoảng trắng/ký tự không phải chữ-số theo `lang` (mặc định `en`).

### 7.3 P3 — `image_ocr`

Extras: `captcha-ocr` (`ddddocr`).

```
data = screenshot element (image_selector, CDP clip)         # cần Capability.SCREENSHOT
text = ocr(data)                                             # ddddocr classification
fill input (selector gần ảnh) bằng value, submit (nút refresh/submit hoặc Enter)
verify: ảnh captcha biến mất / trang chuyển / thông báo lỗi không còn
retry tối đa max_attempts (thường captcha load lại ảnh mới)
```

- Hỗ trợ ảnh nhiều mảnh (nhiều `<img>`): OCR từng mảnh rồi ghép theo thứ tự DOM.
- `ddddocr` lazy-import; thiếu → `MissingDependencyError`.

### 7.4 P4 — `slider_cv`

Extras: `captcha-slider` (`opencv-python-headless`, `numpy`).

```
bg, puzzle = tách ảnh nền/mảnh (screenshot, hoặc đọc base64 từ DOM)
gap_x = template_match(bg, puzzle)                           # OpenCV matchTemplate + cạnh
dist  = gap_x - (puzzle_handle_x - bg_x)                     # khoảng kéo
session.backend.drag(tab, from, dist)                        # trusted, ~30 bước + jitter nhỏ
verify: class/marker thành công (vd check giảm), hoặc iframe biến mất
```

- Cần thêm `Backend.drag(tab_id, x, y, dx, dy, steps, timeout_s)` (P4) phát chuỗi
  `mousePressed → nhiều mouseMoved → mouseReleased` qua `Input.dispatchMouseEvent`.
- Jitter nhỏ ở trục y và tốc độ phi tuyến để giống người thật, nhưng **không** nhắm né detection
  (chỉ để widget chấp nhận).
- Verify thất bại → trả `success=False` kèm khoảng cách đã kéo và ảnh để debug.

---

## 8. Capability & dependency gating

### 8.1 Capability mới

```python
FRAME_EVAL = "frame_eval"   # evaluate JS trong frame cross-origin
```

`session.require()` đã có cơ chế remedy; bổ sung remedy cho `frame_eval`:

```
Frame evaluation needs the CDP backend.
Relaunch Chrome with a debugging port, then retry:
  tabpilot doctor
```

### 8.2 Requirements theo solver

| Solver | Capability cần | Import cần |
|:--|:--|:--|
| `cloudflare_wait` | `EVAL` | – |
| `turnstile_click` | `EVAL`, `TRUSTED_INPUT` | – |
| `recaptcha_checkbox` / `hcaptcha_checkbox` | `EVAL`, `TRUSTED_INPUT` | – |
| `audio_challenge` | `EVAL`, `FRAME_EVAL`, `TRUSTED_INPUT` | `faster_whisper` |
| `image_ocr` | `EVAL`, `SCREENSHOT`, `TRUSTED_INPUT` | `ddddocr` |
| `slider_cv` | `EVAL`, `SCREENSHOT`, `TRUSTED_INPUT` | `cv2`, `numpy` |

### 8.3 Errors mới (`errors.py`)

```python
class MissingDependencyError(TabPilotError):
    code = "MISSING_DEP"

class CaptchaNotSolvedError(TabPilotError):
    code = "CAPTCHA_NOT_SOLVED"
```

- `MISSING_DEP` remedy: `pip install "tabpilot-mcp[captcha]"` (hoặc extra hẹp tương ứng, ví dụ
  `tabpilot-mcp[captcha-stt]`).
- `CAPTCHA_NOT_SOLVED` kèm `detail` đã quan sát và gợi ý (tăng `max_attempts`, đổi `strategy`,
  hoặc `activate_on_fail` để người xử lý).

### 8.4 `pyproject.toml`

```toml
[project.optional-dependencies]
captcha        = ["opencv-python-headless>=4.9", "numpy>=1.26", "ddddocr>=1.5", "faster-whisper>=1.0"]
captcha-ocr    = ["ddddocr>=1.5", "numpy>=1.26"]
captcha-stt    = ["faster-whisper>=1.0"]
captcha-slider = ["opencv-python-headless>=4.9", "numpy>=1.26"]
```

Base install không thay đổi (zero-dependency ngoài MCP SDK).

---

## 9. Config

Thêm vào `Config`, `from_env`, `merge_args`, và `add_common_args` theo pattern `TABPILOT_*`:

| Field | Env | CLI | Default | Ý nghĩa |
|:--|:--|:--|:--|:--|
| `captcha_enabled` | `TABPILOT_CAPTCHA` | `--captcha / --no-captcha` | `True` | Bật/tắt tính năng |
| `captcha_timeout_ms` | `TABPILOT_CAPTCHA_TIMEOUT_MS` | `--captcha-timeout-ms` | `30000` | Trần thời gian mỗi lần giải |
| `captcha_max_attempts` | `TABPILOT_CAPTCHA_MAX_ATTEMPTS` | `--captcha-max-attempts` | `2` | Số lần thử (audio/OCR/slider) |
| `captcha_strategy` | `TABPILOT_CAPTCHA_STRATEGY` | `--captcha-strategy` | `auto` | Solver mặc định |
| `captcha_stt_model` | `TABPILOT_CAPTCHA_STT_MODEL` | `--captcha-stt-model` | `tiny` | Model faster-whisper |
| `captcha_stt_lang` | `TABPILOT_CAPTCHA_STT_LANG` | `--captcha-stt-lang` | `en` | Ngôn ngữ audio challenge |
| `captcha_ocr_model_path` | `TABPILOT_CAPTCHA_OCR_MODEL_PATH` | `--captcha-ocr-model-path` | `None` | Model ddddocr tuỳ chọn |

Khi `captcha_enabled=False`, cả hai tool trả `ERROR [DISABLED]` kèm hướng dẫn bật.

---

## 10. Tools (server.py)

### 10.1 `detect_captcha`

```python
def detect_captcha(
    tab_id: str | None = None,
    url_pattern: str | None = None,
) -> str:
    """Detect a captcha on a tab and report its type, confidence and location.

    Use this before solve_captcha when you want to inspect what is on the page,
    or after a solve attempt to confirm the captcha is gone.

    Args:
        tab_id: Exact tab id from list_tabs. Prefer url_pattern.
        url_pattern: Regex matched against tab URLs.
    """
```

Trả về dạng text dễ đọc (kèm JSON-ish cho agent): type, confidence, signals, anchor/rect,
response field, và hướng dẫn tiếp theo.

### 10.2 `solve_captcha`

```python
def solve_captcha(
    tab_id: str | None = None,
    url_pattern: str | None = None,
    strategy: str = "auto",
    max_attempts: int | None = None,
    timeout_ms: int | None = None,
    activate_on_fail: bool = True,
) -> str:
    """Detect and solve a captcha, verify the result, and report honestly.

    Tries solvers cheapest-first. Never reports success without evidence: a
    click that does not produce a response token is reported as a failure with
    what was actually observed.

    Args:
        tab_id: Exact tab id. Prefer url_pattern.
        url_pattern: Regex matched against tab URLs.
        strategy: 'auto', or a solver name ('turnstile_click', 'audio_challenge', ...).
        max_attempts: Retries for solvers that can reload a challenge.
        timeout_ms: Ceiling for the whole attempt.
        activate_on_fail: Bring the tab to the front when giving up, so a human
            can finish. Harmless when unattended.
    """
```

### 10.3 Cập nhật `INSTRUCTIONS` và README

- `INSTRUCTIONS` thêm đoạn: khi gặp captcha, gọi `detect_captcha`; muốn tự giải gọi
  `solve_captcha`; không dùng `eval_js` để đọc inside iframe.
- README: 17 → 19 tools; thêm mục "Captcha handling"; ghi rõ extras và giới hạn.

---

## 11. Quyết định thiết kế & đánh đổi

### 11.1 Đi ngược "No anti-detection" trong `DESIGN.md`

`DESIGN.md` hiện ghi:

> **No anti-detection.** Trusted input is a consequence of using CDP correctly, not a feature
> aimed at evading anything.

Tính năng captcha tự động **xung đột** với tuyên bố này. Quyết định: chấp nhận đánh đổi, và
**sửa lại** `DESIGN.md` để phạm vi rõ ràng, không mâu thuẫn âm thầm:

> **No stealth or fingerprint spoofing.** TabPilot does not disguise the browser or its identity.
> Captcha handling is an explicit, tool-driven capability the caller invokes knowingly; it never
> hides from the user and never runs implicitly inside other tools.

### 11.2 ToS

Tự động giải captcha có thể vi phạm điều khoản của một số site và của nhà cung cấp captcha.
Tool sẽ ghi chú trong docs rằng người dùng chịu trách nhiệm về việc sử dụng. Không có biện pháp
kỹ thuật nào để lách ToS được thêm vào.

### 11.3 Ưu tiên giai đoạn

- **P0 + P1** ship trước: giá trị cao, không dependency, tự động được nhiều case thực tế
  (Turnstile/Cloudflare checkbox, reCAPTCHA/hCaptcha checkbox ăn ngay).
- P2 (audio) là đường unattended khả thi nhất khi rơi vào image challenge.
- P3 (OCR ảnh) và P4 (slider) là các solver độc lập, thêm sau.

### 11.4 Các lựa chọn đã chốt (có thể đổi khi review)

- **(a) Tên tool**: giữ `detect_captcha` / `solve_captcha`.
- **(b) `activate_on_fail`**: có, mặc định `True`.
- **(c) Thứ tự ưu tiên**: P0 → P1 → P2 → P3 → P4; ship P0+P1 trước.

---

## 12. Testing

| Tầng | Cách | Vì sao |
|:--|:--|:--|
| Detection ranking + solver selection + capability gating | unit, `FakeBackend` (mở rộng `conftest.py`) | thuần logic, không I/O |
| `captcha_detect.js` | fixture HTML giả (iframe src khớp, ảnh captcha giả, slider giả) | kiểm tra payload thật chạy trong trang |
| Luồng checkbox thật | live `-m live` với **test sitekey công khai** của Google/hCaptcha/Cloudflare | sitekey test luôn pass, không cần dịch vụ trả phí |
| OCR / STT / CV | unit với input tĩnh (ảnh mẫu, audio mẫu, ảnh slider mẫu) | tách ML khỏi browser, chạy được trong CI |
| Verify logic | unit + live | đảm bảo không báo thành công giả |

**Không test nào gọi dịch vụ trả phí.** Live suite vẫn skip (không fail) khi không có CDP port.

Bổ sung:
- `tests/test_captcha.py` — detection, orchestration, gating, selection.
- `tests/fixtures/captcha_*.html` — fixture cho từng loại.
- `tests/test_live_cdp.py` — thêm case detection + checkbox với test sitekey.

---

## 13. Docs

- **Mới**: `docs/CAPTCHA.md` — loại hỗ trợ, luồng, cài extras, config, giới hạn, ghi chú ToS,
  cách debug khi solver thất bại.
- **Sửa**: `docs/DESIGN.md` — mục 11.1 (phạm vi anti-detection) + ghi lại quyết định captcha.
- **Sửa**: `README.md` — số tool, mục captcha, extras.

---

## 14. Rủi ro & giới hạn đã biết

1. **Image challenge của reCAPTCHA/hCaptcha**: checkbox không đủ; đường unattended khả thi là
   audio (P2). Độ chính xác STT phụ thuộc chất lượng audio, ngôn ngữ, và tần suất thay đổi.
2. **Managed challenge (Cloudflare/Datadome/PerimeterX)** có thể vẫn chặn; chỉ có `cloudflare_wait`
   và click là chưa đủ cho mọi cấu hình.
3. **Click theo toạ độ** phụ thuộc DPR/scale của màn hình và widget phải hiển thị; widget ẩn một
   phần có thể làm lệch toạ độ.
4. **Verification là heuristic** — site có thể dùng cơ chế pass riêng mà ta không biết; khi đó
   solver báo thất bại dù đã pass (an toàn hơn báo pass giả).
5. **Dependency nặng** (faster-whisper/opencv/ddddocr) làm tăng kích thước extras; tải model lần
   đầu cần mạng.
6. **Thay đổi của nhà cung cấp captcha** có thể làm hỏng selector/solver; cần cập nhật định kỳ.

---

## 15. Kế hoạch giai đoạn (tóm tắt)

| Giai đoạn | Nội dung | Deliverable | Dep |
|:--|:--|:--|:--|
| **P0** | Detection + orchestrator + `detect_captcha` + config/errors skeleton | `captcha.py`, `js/captcha_detect.js`, tool `detect_captcha`, `tests/test_captcha.py` | – |
| **P1** | `cloudflare_wait`, `turnstile_click`, `recaptcha_checkbox`, `hcaptcha_checkbox` + tool `solve_captcha` + verify | 4 solver, verify chung, test live test-sitekey | – |
| **P2** | `Capability.FRAME_EVAL` + `evaluate_in_frame` (CDP) + `audio_challenge` + STT | backend method, solver, extras `captcha-stt` | faster-whisper |
| **P3** | `image_ocr` | solver, extras `captcha-ocr` | ddddocr |
| **P4** | `Backend.drag` + `slider_cv` | drag trusted, solver, extras `captcha-slider` | opencv |

Mỗi giai đoạn có test + docs riêng và có thể ship độc lập.

---

## 16. Câu hỏi mở

Không còn câu hỏi chặn. Các lựa chọn ở mục 11.4 sẽ được chốt trong buổi review spec; nếu đổi,
chỉ ảnh hưởng tên/giá trị mặc định, không ảnh hưởng kiến trúc.
