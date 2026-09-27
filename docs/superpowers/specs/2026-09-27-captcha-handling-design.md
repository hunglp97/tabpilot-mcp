# TabPilot — Thiết kế và kế hoạch xử lý CAPTCHA cho AI agent

- **Ngày cập nhật**: 2026-09-27
- **Trạng thái**: Spec đã rà soát; sẵn sàng triển khai theo các gate bên dưới. Chưa implement hay chứng minh tỷ lệ giải CAPTCHA thật.
- **Mục tiêu**: AI agent đang điều khiển Chrome tự phát hiện, giải và kiểm tra CAPTCHA trong workflow đã được giao, không cần người thao tác ở các loại đã hỗ trợ.
- **Quyết định kiến trúc**: Hai tool `detect_captcha` / `solve_captcha`; solver cục bộ cho trường hợp đơn giản và vòng lặp ảnh–hành động với agent có vision cho thử thách hình ảnh.
- **Phạm vi thay đổi của tài liệu này**: Thiết kế + công việc triển khai + tiêu chí nghiệm thu; không bật tính năng hay thay đổi browser hiện tại.

## 1. Những điểm đã sửa sau review

| Điểm trong bản nháp | Quyết định mới | Vì sao |
|:--|:--|:--|
| Checkbox → audio là đường giải chính cho cả reCAPTCHA/hCaptcha | Đưa agent vision vào đường chính cho image challenge; audio chỉ dùng khi thực sự có | hCaptcha mô tả accessibility bằng email/text/passive, không bảo đảm audio tương đương reCAPTCHA [S1] |
| OCR giải được CAPTCHA ảnh nói chung | Tách `image_text`, `image_grid`, `image_select`, `slider` | Đọc chữ khác với hiểu yêu cầu và chọn vật thể |
| `Page.getFrameTree` + isolated world đủ cho mọi iframe | Có target/session routing và lifecycle events cho OOPIF trước khi công bố frame support | Frame khác process có thể là target riêng [S2] |
| Tâm iframe là tâm checkbox | Tìm control thực hoặc dùng tọa độ từ observation; không đoán offset | Iframe có thể chứa cả nhãn, grid hoặc layout responsive |
| Một trong token/marker biến mất/URL đổi là thành công | Phân tầng `widget_passed` và `access_verified`; tín hiệu yếu không đủ | Token trình duyệt chưa chứng minh backend chấp nhận [S3, S4] |
| Không thấy CAPTCHA = `unknown` | Tách `absent`, `present`, `uncertain`, `inspection_failed` | Không nhìn thấy và không thể kiểm tra là hai kết quả khác nhau |
| Test key luôn pass chứng minh solver | Test key chỉ kiểm tra integration; nghiệm thu khả năng giải phải có challenge thật | Nhà cung cấp thiết kế test key để bỏ qua challenge [S5–S7] |
| `session.py` không đổi, timeout mỗi bước, retry từng solver | Session giữ solve state/lease; một deadline và ngân sách dùng chung | Tránh click trùng, tọa độ cũ và chuỗi retry vượt trần |
| Focus tab khi lỗi mặc định bật | `activate_on_fail=False`, chỉ bật theo yêu cầu và capability | Không đổi focus bất ngờ trên máy người dùng; headless không có người chờ sẵn |

## 2. Mục tiêu và cách hiểu “tự giải”

### 2.1 Hai mức tự động hóa

1. **Solver trong MCP**: chờ kiểm tra tự động, click checkbox đã định vị, OCR/STT/CV nếu đã cài extras. Một lệnh có thể kết thúc mà không cần model suy luận thêm.
2. **Agent tự giải qua MCP**: tool trả ảnh challenge và danh sách hành động hợp lệ; chính agent đang điều khiển Chrome đọc ảnh, gửi đáp án/hành động, rồi nhận observation mới. Nhiều tool call nhưng không cần người giải hộ.

Mức 2 là đường chính cho image grid/chọn vật thể. TabPilot hiện không chứa model vision, không tự gọi model chỉ vì đã thêm `solve_captcha`. Client phải nhận được MCP image content và tiếp tục vòng lặp. `needs_agent` có nghĩa là cần bước suy luận của agent, không phải yêu cầu người dùng.

Nếu chỉ chạy MCP server không có agent vision, không được quảng cáo tự giải image challenge. Model vision cục bộ bên trong server là extension tương lai, không phải dependency của bản đầu. Không sử dụng dịch vụ giải CAPTCHA trả phí; chi phí model của agent và tài nguyên OCR/STT vẫn có thể phát sinh.

### 2.2 Phạm vi loại thử thách

| Provider / challenge | Đường xử lý dự kiến | Giới hạn công bố |
|:--|:--|:--|
| Cloudflare interstitial | Bounded wait → tương tác control nếu có → xác minh trang đích | Managed challenge có thể từ chối session; không có đáp án UI thì không hứa giải được |
| Turnstile | Wait/token observation; click nếu có control thật | Tách invisible/passive/interactive, không coi mọi iframe Cloudflare là checkbox |
| reCAPTCHA v2 checkbox | Click → inspect lại → vision hoặc audio nếu hiện có | Không có token sau click chưa đủ để kết luận đã mở image challenge |
| reCAPTCHA image challenge | Agent vision, nhiều vòng nếu ảnh thay thế | Chỉ tuyên bố hỗ trợ dạng đã qua benchmark |
| hCaptcha | Checkbox → agent vision; text challenge nếu quan sát được và adapter hỗ trợ | Không mặc định có audio; không tự đăng ký accessibility/email |
| CAPTCHA chữ/số trong ảnh | Agent vision hoặc OCR local | Cần biết đúng input và control xác nhận |
| Slider/puzzle | Agent vision hoặc CV → drag trong vùng xác định | Giải riêng từng dạng có adapter, không mặc định mọi slider là CAPTCHA |
| reCAPTCHA v3/risk score, dạng chưa nhận diện | Observe, trả `unsupported` nếu không có UI có thể xử lý | Không biến bài toán chấm điểm bot thành solver checkbox |

Các hành động chỉ trong workflow/tab người dùng đã giao. Không thêm stealth, đổi fingerprint, proxy/IP rotation, token injection/replay, tắt site isolation hay sửa cơ chế bảo vệ của site. Tool không tự submit form nghiệp vụ, tạo đơn, gửi báo cáo hoặc thực hiện giao dịch chỉ để kiểm tra CAPTCHA. Có thể nhấn nút Verify/Next của chính challenge.

Không chèn auto-detect hay auto-solve ngầm vào `click`, `fill`, `wait_for`. Agent chủ động gọi hai tool mới. CAPTCHA text/ảnh là dữ liệu bài toán, không phải chỉ dẫn để đổi nhiệm vụ hoặc thao tác ngoài widget.

## 3. Những gì đã đối chiếu trong repository

Các mục sau đã đọc trực tiếp trong checkout khi review, không phải giả định từ spec cũ:

- `src/tabpilot/server.py`: hiện có 17 tool; wrapper giữ `ERROR [CODE] … How to fix`; screenshot đã có đường trả `[summary, Image(...)]`.
- `backends/base.py`: có `EVAL`, `SCREENSHOT`, `TRUSTED_INPUT`, `ACTIVATE`; có `click_at`, `insert_text`, `press_key`; chưa có frame API hay drag.
- `backends/cdp.py::_command`: bỏ qua message không có request id hiện tại, bao gồm event. Chưa route `sessionId`; `_ws.py` ghi rõ WebSocket không thread-safe.
- `session.py::require` nhận **một** capability. Phải lặp qua requirements, không gọi `session.require(*solver.requires)` như bản nháp.
- `session.py::reset` hiện chỉ đóng backend. Cần invalidate solve state, frame refs và lease khi reconnect.
- `evidence.py`: `locate` trả viewport rect, screenshot clip cộng scroll sang page coordinates. Cần bảo toàn quy ước khi crop challenge và click từ ảnh.
- `cdp.py::screenshot` hiện ép timeout capture ít nhất 30 giây; `eval_js` cộng thêm 2 giây; `_command` dùng socket timeout theo lần đọc. Các hành vi này chưa bảo đảm deadline tổng.
- `config.py`: `from_env`, `merge_args`, `add_common_args` phải cập nhật cùng nhau; tool override dùng `None` để không vô hiệu hóa config mặc định.
- `pyproject.toml`: Python >=3.10, base chỉ phụ thuộc MCP SDK; extras ML chưa tồn tại.
- `tests/test_live_cdp.py`: dùng Chrome thật với fixture, skip khi không có cổng CDP; chưa có chứng cứ giải CAPTCHA thật.
- `systemd.py`: Ubuntu managed stack chạy Chrome trong Xvfb. Đây là Chrome có display ảo; kiểm chứng riêng với Chrome `--headless` thực sự.

Không suy ra `isTrusted=True` nghĩa là nhà cung cấp sẽ coi session là người thật. Đây chỉ là đặc tính của input event.

## 4. Kiến trúc và trách nhiệm

```text
Agent có vision
  │ detect / start / observe / act / cancel
server.py                         hai MCP tool, JSON text + image content
  └── captcha.py                  public facade, detect + orchestrator
        ├── captcha_state.py      models, deadline, lease, observation/action identity
        ├── captcha_verify.py     evidence và các mức xác minh
        └── captcha_solvers/      registry + adapter từng provider/challenge
              │
          session.py              lifetime, backend generation, tool mutation guard
              │
          backends/base.py        FrameRef, frame inspect, pointer/keyboard/drag contracts
          backends/cdp.py         CDP routing, event lifecycle, deadline, transport lock
          backends/applescript.py top-document detection; thiếu capability thì báo rõ
              │
          js/captcha_*.js         payload thuần; JSON-encode options qua payloads.py
```

Không để solver biết WebSocket hoặc tự gửi raw CDP. Backend không biết CAPTCHA. Solver adapter chỉ định vùng challenge, control, trạng thái và evidence; orchestrator sở hữu retry/deadline/lease. `solve_step` thực hiện một bước hữu hạn, không chứa vòng retry vô hạn riêng.

Giữ hai tool công khai; thêm method backend không đồng nghĩa thêm MCP tool. Sau khi đăng ký đủ hai tool, cập nhật số lượng 17 → 19, test registry và docstring.

## 5. Data contract

Các schema dưới đây là hợp đồng triển khai; tên enum phải dùng nhất quán trong tool, tests và docs.

### 5.1 Detection: trả tất cả candidate

```python
DetectionResult = {
    "schema_version": 1,
    "status": "absent | present | uncertain | inspection_failed",
    "tab_id": str,
    "document_generation": str,
    "coverage": "top_document | frames | partial",
    "candidates": list[CaptchaCandidate],
    "selected_candidate_id": str | None,
    "limitations": list[str],
}

CaptchaCandidate = {
    "candidate_id": str,             # opaque, chỉ hợp lệ trong document generation
    "provider": str,                 # recaptcha | hcaptcha | cloudflare | custom | unknown
    "challenge_kind": str,           # checkbox | interstitial | image_grid | image_select
                                     # | image_text | audio | text | slider | score_only | unknown
    "state": str,                    # loading | actionable | passed | expired | blocked | unknown
    "confidence": str,               # high | medium | low; không phải xác suất thành công
    "signals": list[str],
    "visible": bool,
    "blocking": bool | None,
    "frame_ref": dict | None,        # opaque handle, không chọn bằng URL regex đơn lẻ
    "widget_ref": str | None,
    "rect_css": dict | None,         # x, y, width, height; top viewport CSS pixels
    "sitekey": str | None,           # metadata công khai; không dùng làm định danh duy nhất
    "response_field_ref": str | None,# field gắn với đúng widget/form
    "available_strategies": list[str],
}
```

`absent` chỉ có nghĩa không thấy candidate trong coverage đã kiểm tra. Lỗi eval/iframe inaccessible không được biến thành `absent`. Có nhiều widget tương đương → yêu cầu agent chọn `candidate_id`; không tự click tất cả.

### 5.2 Solve state và kết quả

```python
SolveResult = {
    "schema_version": 1,
    "status": str,
    "terminal": bool,
    "success": bool,                 # chỉ True cho widget_passed/access_verified
    "verification_level": "none | widget | access",
    "solve_id": str | None,
    "candidate_id": str | None,
    "observation_id": str | None,
    "provider": str | None,
    "challenge_kind": str | None,
    "solver": str,                   # none | passive_wait | checkbox | agent_vision | ...
    "attempts": int,
    "rounds": int,
    "actions_used": int,
    "elapsed_ms": int,               # tính cả thời gian agent suy luận giữa tool call
    "remaining_ms": int,
    "evidence": list[dict],          # kind, observed_at, widget/document binding, detail
    "detail": str,
    "next_action": str | None,
    "retry_after_ms": int | None,
    "observation": dict | None,
}
```

Status không terminal: `waiting`, `needs_agent`, `stale_observation`.
Status terminal: `widget_passed`, `access_verified`, `no_captcha`, `unsupported`,
`blocked`, `exhausted`, `timeout`, `unverified`, `cancelled`, `interrupted`.
`no_captcha.success=False`: chưa chứng minh có CAPTCHA nào được giải. `widget_passed` là pass ở trình duyệt, không phải xác nhận form đã gửi thành công.

State server lưu thêm: backend generation, target/document/frame identity, deadline monotonic, config snapshot, baseline token fingerprint, observation fingerprint, counter, action receipts. Không trả raw token/cookie/secret trong result hoặc log.

### 5.3 Observation và action

Observation phải chứa prompt hiển thị, ảnh crop gồm đủ hướng dẫn/challenge/control, kích thước ảnh, ánh xạ ảnh → viewport, `allowed_actions`, control/tile id do server sinh, và khả năng ảnh thay đổi sau mỗi click. Nếu không đọc được prompt bằng DOM, trả ảnh và `prompt_source="image"`, không bịa text.

```json
{
  "solve_id": "opaque-solve",
  "observation_id": "opaque-observation",
  "action_id": "unique-action",
  "action": {"kind": "select_tile", "target_id": "tile-4"}
}
```

Các kind được phép: `click_control`, `select_tile`, `click_point`, `type_answer`, `drag`, `verify`, `refresh`. Mỗi action có schema riêng; chỉ dùng target id/point trong observation đang còn hiệu lực. `type_answer` giới hạn độ dài và chỉ nhập vào input của challenge. `click_point`/`drag` bắt buộc có `image_id` và dùng tọa độ normalized `[0,1]` trong crop tương ứng; server kiểm tra vùng và đổi về CSS pixels.

Một `act` xử lý một action; không batch các ô của grid động. Sau mỗi action lấy observation mới. `verify` phải là control Verify/Next của challenge, không phải submit button bất kỳ gần đó. Action không nhận raw JS hoặc selector tuỳ ý từ agent.

## 6. MCP API và vòng lặp của agent

```python
def detect_captcha(
    tab_id: str | None = None,
    url_pattern: str | None = None,
) -> str: ...  # JSON hợp lệ trong text content, không dùng "JSON-ish"


def solve_captcha(
    tab_id: str | None = None,
    url_pattern: str | None = None,
    operation: str = "start",         # start | observe | act | cancel
    candidate_id: str | None = None,
    solve_id: str | None = None,
    observation_id: str | None = None,
    action_id: str | None = None,
    action: dict | None = None,
    strategy: str | None = None,       # None -> config; auto hoặc strategy cụ thể
    agent_vision: bool | None = None,  # start: mặc định True cho vision agent; resume bỏ trống
    timeout_ms: int | None = None,     # budget tổng cho solve, chỉ đặt ở start
    max_attempts: int | None = None,
    max_rounds: int | None = None,
    expected: dict | None = None,      # postcondition chỉ đọc, xem mục 9
    activate_on_fail: bool | None = None,
) -> Any: ...                         # JSON text + MCP Image khi needs_agent
```

- `start`: resolve tab một lần, snapshot document, chọn candidate, acquire lease, tạo deadline; chạy các bước local có giới hạn hoặc trả `needs_agent`.
- `observe`: bắt buộc `solve_id`; đọc lại state/evidence, tiếp tục bounded wait, trả observation mới. Không tạo budget mới.
- `act`: bắt buộc cả `solve_id`, `observation_id`, `action_id`, `action`; reject trường không hợp lệ trước side effect.
- `cancel`: kết thúc solve, release lease và tài nguyên; không reset CAPTCHA hay reload trang.
- Resume dùng tab đã bind trong solve; nếu vẫn truyền tab/url/candidate phải khớp. Không re-resolve URL sang tab khác khi trang điều hướng.
- Budget/strategy/expected/agent_vision/activate_on_fail bất biến sau start. Các tham số start-only dùng default `None`; resume truyền giá trị khác `None` thì reject trước side effect. Tab/url/candidate nếu truyền ở resume chỉ là assertion phải khớp identity, không cập nhật state.
- `needs_agent` bắt buộc kèm ảnh inline. Client từ xa không thể chỉ nhận đường dẫn trên server. Server kiểm tra khả năng encode image của SDK; khả năng model đọc ảnh là contract do caller khai báo, không suy ra tự động từ MCP handshake. Caller không có vision đặt `agent_vision=False`; khi đó chỉ chạy local strategy phù hợp, nếu thiếu thì trả unsupported. Không gửi `needs_agent` rồi chờ một client không có vision.
- Server hỗ trợ cả SDK 1.x/2.x qua `_sdk.py`; kiểm chứng image content thực qua MCP, không chỉ gọi hàm Python.

Luồng khuyến nghị trong `server.INSTRUCTIONS` và `docs/CAPTCHA.md`:

```text
Đang làm workflow → thấy dấu hiệu CAPTCHA → detect_captcha
  → solve_captcha(start, candidate_id, expected nếu biết)
  → waiting: observe theo retry_after_ms, vẫn trong deadline
  → needs_agent: đọc prompt + ảnh → chọn action hợp lệ → solve_captcha(act)
  → stale_observation: đọc ảnh mới, không gửi lại tọa độ/đáp án cũ
  → widget_passed: tiếp tục bước nghiệp vụ đã được giao; kiểm tra kết quả bước đó riêng
  → access_verified: tiếp tục workflow
  → blocked/unsupported/exhausted/timeout/unverified: ghi nhận lý do và dừng nhánh này
```

Agent không gọi `start` liên tục để reset retry. Không giữ tab foreground như điều kiện giải; không bắt người dùng confirm từng action CAPTCHA trong workflow đã được giao.

## 7. Orchestrator: tiến triển hữu hạn và chống thao tác trùng

### 7.1 State machine

```text
resolve → detect → select candidate → baseline → local strategy step → verify
                                        │                          │
                                        └→ needs_agent → act ──────┘
verify → widget_passed / access_verified
       → challenge mới cùng widget → observe → bước kế tiếp
       → blocked / exhausted / timeout / unverified / interrupted
```

Chọn strategy theo **challenge đang nhìn thấy**, không thử tất cả solver theo thứ tự cố định. Ví dụ checkbox đã click và grid xuất hiện → agent vision; không click checkbox lần nữa. Ép strategy không phù hợp trả `STRATEGY_INCOMPATIBLE`. Với `auto`, thiếu extra local có thể chuyển sang agent vision và ghi lý do; explicit strategy thiếu extra trả `MISSING_DEP`.

### 7.2 Budget

- Một `time.monotonic()` deadline cho toàn solve, gồm detect/frame discovery/capture/ML/wait/agent think time/retry. Mỗi tool call có ceiling nhỏ hơn deadline còn lại, ví dụ 15 giây.
- `attempt`: một lượt trả lời challenge cho đến khi bị reject/reset. `round`: một observation/challenge revision cần quyết định mới, kể cả ô ảnh thay thế. Click không reset counter.
- `max_attempts=2`, `max_rounds=8`, `max_actions=24` mặc định; chạm bất kỳ trần nào thì kết thúc. Không nhân budget qua từng solver.
- Poll có khoảng nghỉ và kiểm tra deadline; `waiting.retry_after_ms` giúp agent không busy-loop. Provider báo cooldown/blocked → dừng, không refresh liên tục.
- Sau exhausted/blocked, giữ cooldown cho cùng target/widget/document để start mới không reset ngay; reset có chủ đích là quyết định workflow, không retry tự động.
- Local inference chạy trong worker có thể timeout/terminate. Chỉ đo thời gian sau khi inference trả về không phải hard timeout.
- Không tải model trong solve. Thiếu model đã provision → `MODEL_NOT_READY` kèm cách chuẩn bị.

### 7.3 Lease, identity và recovery

- Mỗi tab có tối đa một solve đang hoạt động trong một Session. Start đồng thời khác trả `CAPTCHA_BUSY`; start trùng cùng candidate trả solve hiện có, không click thêm.
- Lease tồn tại qua các lần `needs_agent` nhưng không giữ thread lock khi chờ model. TTL/deadline cleanup được kiểm tra trên mọi lối vào liên quan; cancel/reset/close/terminal đều release.
- Mutating tool khác trên cùng tab phải qua session guard trong thời gian lease. `eval_js` xem là có thể mutate; read/screenshot vẫn dùng được. Ngoài lease, tool cũ giữ hành vi hiện tại.
- Guard áp dụng ở public tool entry; thao tác nội bộ của solver mang solve-owner context để không tự chặn chính mình. `read_tab`/`query_dom` được phép chỉ khi đường đọc không scroll/focus; screenshot theo selector có thể scroll qua locate nên phải coi là mutation hoặc dùng capture không đổi viewport.
- Transport có lock/dispatcher riêng; lease CAPTCHA không đủ để ngăn hai reader tranh cùng WebSocket. Chỉ cam kết điều phối trong một MCP server process; nhiều server cùng điều khiển một browser cần single-writer deployment, chưa có distributed lease.
- `action_id` dedupe bằng solve + payload hash: cùng id/cùng payload trả receipt đã có; cùng id/khác payload reject. Reservation/check/dispatch/receipt phải nguyên tử đối với các caller đồng thời.
- Chụp baseline trước action; so lại document, frame, widget, ảnh/control và geometry ngay trước input. Đổi ảnh/layout → `stale_observation`, cấp observation mới và không dùng đáp án cũ.
- Nếu dispatch có thể đã xảy ra nhưng mất kết nối trước receipt: `interrupted`, outcome action là unknown; không tự replay click/drag/Verify.
- Reset/restart invalidates solve id, frame context và tọa độ. Không resume hành động từ state không chắc chắn. Cache receipt terminal ngắn hạn để xử lý delivery trùng, sau TTL chỉ báo expired, không replay.
- Không thể khóa UI khỏi người dùng hoặc script của site; giảm khoảng check→input và kiểm tra hậu điều kiện, không tuyên bố thao tác DOM nguyên tử.

## 8. Backend CDP, iframe và tọa độ

### 8.1 Frame support là công việc nền tảng

Thêm `Capability.FRAME_EVAL`, `FrameRef`, `list_frames`, `evaluate_in_frame` và helper định vị trong frame. `FrameRef` bind với tab, frame id, target/session và document/context generation. URL dùng nhận diện provider, không phải khóa chọn duy nhất.

`Page.createIsolatedWorld` vẫn có ích cho frame thuộc đúng target; không thay thế routing OOPIF. Thiết kế tối thiểu cần:

1. Route request/response theo id và CDP `sessionId`; một owner đọc socket, không bỏ response của caller khác.
2. Dùng auto-attach flat sessions cho related iframe targets, xử lý attach/detach và các frame con lồng nhau. Giữ target `iframe` nội bộ, không đưa chúng vào danh sách tab cho agent.
3. Track frame navigation, context created/destroyed/cleared; bật Page/Runtime phù hợp trên target con. Invalidate refs khi target/context thay đổi.
4. Chọn frame bằng owner/widget association và stable handle; hai widget cùng URL vẫn phân biệt được.
5. Isolated world dùng đọc DOM/geometry; không mặc định thấy biến JS/callback ở main world. Không monkeypatch callback hoặc gọi callback giả để tạo bằng chứng pass.
6. Event queue có giới hạn, teardown sạch, xử lý event xen giữa response mà không deadlock. Đây là lifecycle capture, chưa yêu cầu network/console capture tổng quát.

Nguồn [S2, S8] giải thích các primitive; cách áp dụng vào synchronous transport hiện tại phải được chứng minh bằng transport tests và OOPIF live test. Không advertise `FRAME_EVAL` hoàn chỉnh khi mới test iframe cùng process.

### 8.2 Input và crop

- `Input.dispatchMouseEvent` dùng tọa độ CSS tương đối top-frame viewport [S9]. Screenshot có kích thước ảnh riêng; không nhân DPR một cách mặc định.
- Observation lưu crop page rect, top viewport rect, scroll/visual viewport/zoom metadata và kích thước ảnh thực. Ánh xạ normalized point về crop rồi về viewport; geometry snapshot đã thay đổi thì chụp lại.
- Với nested frame phải chuyển qua chuỗi frame owner; xử lý border/scroll/scale. Transform không được hỗ trợ → báo rõ, không cộng offset x/y rồi đoán.
- Locate checkbox/control bên trong frame hoặc dùng vision xác định từ ảnh. Kiểm tra visibility, enabled, bounds và overlay/hit-test ở các frame liên quan trước click.
- Không dùng `element.click()` như phương án input trusted. `userGesture=True` của eval không biến synthetic DOM click thành trusted event.
- Focus đúng input trước `insert_text`; đọc lại trạng thái input/focus, không gõ vào trang mẹ do mất focus.
- Thêm `Capability.DRAG` khi có `Backend.drag`; press → move có `buttons=1` → release có `buttons=0`, release trong `finally` khi còn kết nối. Đường kéo deterministic theo geometry/duration; bỏ jitter “giống người”.
- Screenshot/HTTP/WS/frame discovery phải nhận remaining deadline. Sửa capture timeout tối thiểu 30 giây trên đường CAPTCHA; event flood không được kéo dài `_command` vô hạn bằng cách reset timeout mỗi recv.

Detection top-document có thể chạy trên AppleScript với coverage hạn chế. Solve bản đầu yêu cầu CDP và các capability của strategy; không quảng cáo rằng fallback AppleScript vẫn tự giải được.

## 9. Verification: không báo thành công từ dấu hiệu yếu

### 9.1 Bằng chứng và mức xác minh

| Quan sát | Kết luận tối đa |
|:--|:--|
| Click gửi thành công, checkbox tick tạm, title/URL đổi, widget biến mất, lỗi biến mất | Chưa đủ; lưu evidence nhưng không đặt `success=True` |
| Token mới của đúng widget/document, trạng thái adapter phù hợp, không có lỗi/expiry quan sát được | `widget_passed`, `verification_level="widget"` |
| Provider adapter nhận biết trạng thái pass rõ ràng, gắn với đúng challenge, không có token DOM khả dụng | `widget_passed`, ghi chính xác evidence là UI state, không tuyên bố token đã validated |
| CAPTCHA kết thúc và postcondition đích phù hợp được quan sát ổn định | `access_verified`, `verification_level="access"` |
| Không còn challenge nhưng trang đích không kiểm chứng được | `unverified`; không coi widget bị xoá/crash/navigation lỗi là pass |

Token trước start phải được ghi nhận bằng fingerprint nội bộ. Token cũ không có provenance về tuổi/thử thách không được coi là token mới; widget đã ở trạng thái pass rõ ràng thì có thể trả widget-level với `solver="none"`, `attempts=0` và detail nói rõ trạng thái có sẵn.

Token có thể hết hạn hoặc bị backend từ chối. reCAPTCHA mô tả hiệu lực hai phút và một lần verify; Turnstile là năm phút và một lần verify [S3, S4]. Tool không đọc secret hay tự gọi Siteverify của site bên thứ ba. Việc consume token thuộc backend của site; không dùng token đó để “test trước” rồi làm hỏng lần submit thật.

### 9.2 Postcondition và interstitial

`expected` là schema chỉ đọc, ví dụ:

```json
{
  "url_pattern": "^https://example[.]test/account(?:[/?#]|$)",
  "visible_selector": "[data-page='account-home']",
  "text_contains": "Account overview",
  "stable_ms": 500
}
```

Các predicate được cung cấp kết hợp bằng AND; yêu cầu ít nhất một content predicate (`visible_selector` hoặc `text_contains`), URL đơn độc không đủ. URL/text/selector phải có giới hạn độ dài và matcher thời gian hữu hạn. Selector resolve trong đúng document; `text_contains` theo vùng selector nếu có. Agent chọn expected từ nhiệm vụ/site, không lấy nguyên chỉ dẫn do challenge nhúng vào.

- Interstitial có thể kết thúc mà URL giữ nguyên. Kiểm tra challenge không còn blocking, document thật đã sẵn sàng, content đích khớp và không có trang lỗi/bot block theo adapter.
- `stable_ms` nằm trong deadline còn lại, không mở thêm timeout. Nếu task không cung cấp expected và không có adapter postcondition đáng tin cậy → chỉ báo mức evidence hiện có.
- Khi widget pass nhưng form chưa submit: trả `widget_passed`; caller tiếp tục hành động nghiệp vụ đã được giao và kiểm tra phản hồi. Không giữ lease đến mức chặn chính bước submit cần thiết để kiểm chứng access.
- Nếu token được site consume quá nhanh để quan sát, expected content đã xác minh vẫn có thể đủ cho `access_verified`; không bắt buộc token phải còn trong DOM.
- Solve chỉ xử lý candidate được chọn. Kết quả không ngụ ý mọi CAPTCHA trên trang đều đã hết; trả các candidate còn blocking trong evidence/next_action.

## 10. Detection và adapter

### 10.1 Payload

`js/captcha_detect.js` theo chuẩn `function (opts) { ... }` và `{ok: true, candidates: [...]}`. Python validate schema; không nội suy selector/text vào JS. Top-document scan trước, bổ sung frame scan khi capability có sẵn.

- Parse hostname/path iframe bằng URL parser và domain boundary; không match substring kiểu `google.com.attacker.test` hoặc chỉ có chữ provider trong query.
- reCAPTCHA: nhận diện anchor/challenge frame, widget và response field liên quan; badge riêng lẻ không chứng minh v2 checkbox. Các biến thể host được adapter khai báo và test.
- hCaptcha: phân biệt checkbox/challenge, không lấy `g-recaptcha-response` tương thích của hCaptcha làm bằng chứng có thêm một reCAPTCHA.
- Cloudflare: tách Turnstile widget và interstitial. Title “Just a moment” đơn độc là tín hiệu yếu, không đủ auto-click.
- Ảnh chữ/số: cần kết hợp image/canvas, nhãn CAPTCHA và input/control có quan hệ rõ ràng. Không coi mọi ảnh `code`, `verify` là CAPTCHA.
- Slider: cần ngữ cảnh challenge và track/handle/puzzle; range input, carousel hoặc volume slider phải là negative cases.
- Bỏ qua template ẩn, widget cũ, badge trang trí; ghi riêng widget invisible có evidence thực. Open shadow root có thể scan; closed shadow/không đọc được frame → coverage partial, dùng ảnh nếu phù hợp.
- Group dấu hiệu cùng widget thành một candidate. Chỉ chọn tự động nếu có một blocker rõ ràng; ưu tiên interstitial blocking, nhưng không để ranking che mất ambiguous candidates.

`confidence` phản ánh bằng chứng nhận diện; action chỉ diễn ra khi target/geometry/actionability đủ chắc. Không có tọa độ chắc chắn → observation cho agent hoặc unsupported, không click theo heuristic yếu.

### 10.2 Registry và yêu cầu

| Strategy | Capability | Tài nguyên / lưu ý |
|:--|:--|:--|
| `passive_wait` | `EVAL`; frame inspection khi cần | Poll hữu hạn, không mặc định session đăng nhập sẽ được Cloudflare cho qua |
| `checkbox` | `EVAL`, `FRAME_EVAL`, `TRUSTED_INPUT` | Control đã định vị; nếu không đọc frame được thì agent vision có thể chọn point qua observation |
| `agent_vision` | `EVAL`, `SCREENSHOT`, `TRUSTED_INPUT`; `FRAME_EVAL` khi adapter cần | Client model nhận ảnh; `DRAG` nếu action là kéo |
| `image_ocr` | `EVAL`, `SCREENSHOT`, `TRUSTED_INPUT`; frame nếu ảnh/input trong frame | OCR local optional, chỉ text image |
| `recaptcha_audio` | `EVAL`, `FRAME_EVAL`, `TRUSTED_INPUT` | STT local optional; audio control/media thực sự hiện có |
| `slider_cv` | `EVAL`, `SCREENSHOT`, `TRUSTED_INPUT`, `DRAG`; frame khi cần | CV optional; adapter xác nhận geometry và dạng puzzle |

Adapter trả `can_handle`, requirements theo instance, `observe`, `solve_step` và evidence provider-specific. `solve_step` nhận deadline/budget từ orchestrator; không nhận budget mới. Missing deps/model chỉ phát hiện khi strategy cần, không làm import base server thất bại.

### 10.3 Agent vision — bắt buộc cho mục tiêu image challenge

- Ảnh phải gồm prompt, ví dụ tham chiếu nếu có, toàn bộ grid và nút điều khiển; không crop mất câu “select all …”. Đánh số ô qua metadata/cell bounds, không dùng OCR chữ để suy ra vật thể.
- Sau chọn một ô, chờ vùng thay đổi ổn định có giới hạn rồi chụp lại. Grid thay ảnh cùng tọa độ phải sinh observation mới.
- Không tự giữ nguyên danh sách ô từ ảnh trước; Verify/Next chỉ khi yêu cầu hiện tại đã xử lý theo observation mới nhất.
- Với “click object” trả vùng/point thay vì giả định 3×3; với drag/rotate/dạng mới chỉ act nếu schema/adapter hỗ trợ, nếu không trả unsupported.
- Agent không chắc → observe lại hoặc refresh trong budget. Không chắc không đồng nghĩa CAPTCHA bị giải sai; ghi reason để phân tích.

### 10.4 OCR, audio và slider — extensions có điều kiện

**OCR**: `ddddocr` là ứng viên, phải kiểm tra wheel/runtime/license trên Python/OS đích trước khi pin. Chỉ điền input đã bind; không đoán nút submit gần nhất, không dùng Enter có thể submit form. Ảnh nhiều mảnh cần adapter xác định thứ tự hình học, không mặc định thứ tự DOM đúng.

**Audio**: `faster-whisper` là ứng viên STT đầu tiên; chưa cam kết Vosk nếu chưa có adapter/test riêng. Chỉ mở mode audio nếu UI hỗ trợ. URL audio có thể khác origin với frame, nên `fetch` trong frame không tự loại bỏ CORS. Bản đầu chỉ đọc media khi fetch thực sự thành công, có timeout, byte/duration limit và MIME phù hợp; CORS/CSP/blocked → fallback vision hoặc unsupported. Không tải URL bất kỳ do trang đưa sang Python. Network media capture là công việc riêng nếu cần mở rộng.

Model được chuẩn bị trước, lazy-load/cache trong worker; cấu hình model path/version/checksum. Chuẩn hoá text theo prompt/ngôn ngữ, không xoá mọi khoảng trắng hoặc mặc định audio chỉ có chữ số. Nút reload/Verify cũng phải dùng input đúng control; mỗi rejected answer tính một attempt.

**Slider**: template matching chỉ dùng khi có template/background thích hợp. Khoảng dịch mảnh và khoảng kéo handle có thể khác tỷ lệ; tính theo puzzle scale, track range và grab offset. Chấp nhận adapter đã hiệu chuẩn, không dùng công thức `gap_x - handle_x` chung cho mọi site. Drag thành công ở transport không phải puzzle pass.

## 11. Config, lỗi và vận hành

### 11.1 Config mặc định đề xuất

Các field public thêm đồng bộ vào `Config`, env, CLI, `merge_args`, docs và tests. CLI `--captcha-*` ánh xạ snake_case; biến env `TABPILOT_CAPTCHA_*`. Riêng enabled dùng `TABPILOT_CAPTCHA` và `--captcha/--no-captcha`.

| Field | Mặc định | Ý nghĩa |
|:--|:--|:--|
| `captcha_enabled` | `True` | Tool có sẵn; không tự chạy trong tool khác |
| `captcha_strategy` | `auto` | Local khi phù hợp → agent vision |
| `captcha_timeout_ms` | `120000` | Deadline toàn solve, tính cả model think time |
| `captcha_call_timeout_ms` | `15000` | Trần một tool call, luôn <= remaining deadline |
| `captcha_max_attempts` | `2` | Tổng rejected/reset attempts |
| `captcha_max_rounds` | `8` | Tổng observation rounds cần quyết định |
| `captcha_max_actions` | `24` | Tổng action có side effect |
| `captcha_activate_on_fail` | `False` | Best-effort focus khi explicit bật và có `ACTIVATE` |
| `captcha_cooldown_ms` | `60000` | Sau exhausted/blocked; không ngắn hơn provider cooldown đã thấy |
| `captcha_stt_model_path` | `None` | Model đã provision; không auto-download trong solve |
| `captcha_stt_lang` | `en` | Override theo audio thực; không ép normalize số |
| `captcha_ocr_model_path` | `None` | Model local nếu OCR adapter yêu cầu |

Validate số nguyên dương, giới hạn cứng hợp lý (timeout tối đa 300000 ms, attempts 5, rounds 20, actions 60), enum và path khi khởi tạo/call. `None` mới dùng default; `0`, số âm, bool giả số hoặc NaN phải reject. `activate_on_fail` lỗi không che kết quả giải ban đầu.

Observation limits ban đầu: ảnh dài nhất 1600 px, 2 MiB/ảnh sau encode; giữ metadata scale khi resize; text metadata tối đa 16 KiB. Media STT tối đa 5 MiB và 30 giây. Nếu downscale khiến không đọc được, trả crop bổ sung có cùng identity/mapping hoặc unsupported; không gửi ảnh hỏng âm thầm.

Đường agent vision base dùng CDP clip/scale và PNG/JPEG encoding để tạo ảnh đúng budget, không bắt cài Pillow/OpenCV chỉ để crop. Mọi crop bổ sung phải được gắn image id rõ ràng trong observation trước khi nhận point; không để agent đoán point thuộc ảnh nào.

### 11.2 Error contract

Expected solve outcomes dùng JSON status ở mục 5, không exception cho mỗi lần không giải được. Lỗi cấu hình/transport/capability vẫn theo `ERROR [CODE] … How to fix` của repo:

- Hiện có: `BAD_ARGUMENT`, `UNSUPPORTED_BY_BACKEND`, `BRIDGE_OFF`.
- Thêm: `DISABLED`, `MISSING_DEP`, `MODEL_NOT_READY`, `CAPTCHA_BUSY`, `SOLVE_EXPIRED`, `STRATEGY_INCOMPATIBLE`, `IMAGE_OUTPUT_UNAVAILABLE`, `ACTION_OUTCOME_UNKNOWN` khi không thể trả solve result đầy đủ.
- `stale_observation` là recoverable result kèm ảnh mới, không tự replay action.
- `MISSING_DEP` ghi đúng extra hẹp cần cài. `blocked` không gợi ý tăng retry hoặc refresh vô hạn.

### 11.3 Extras và dữ liệu

Base vẫn chỉ MCP SDK. Chỉ thêm extras theo giai đoạn thực sự implement: `captcha-ocr`, `captcha-stt`, `captcha-slider`, và aggregate `captcha` khi các tổ hợp đã kiểm chứng. Chưa pin version dựa trên phỏng đoán trong spec; dependency task phải thử resolver/wheel trên Python tối thiểu và runtime triển khai, đặc biệt ONNX/NumPy/OpenCV/CTranslate2. Tránh cài nhiều bản OpenCV xung đột gián tiếp.

Screenshot chỉ crop vùng cần giải, inline cho agent. Raw token/cookie/secret/audio không ghi log. Media/observation mặc định giữ trong memory đến expiry; evidence lưu disk chỉ khi được cấu hình, tên có solve/observation id để không đè ảnh trong cùng giây. File tạm ML xoá trong finally; bounded cache và retention, không lưu ảnh chứa phiên đăng nhập vô thời hạn.

Doctor/status báo solver installed, model ready, khả năng server xuất image content và backend capabilities; không khẳng định model phía client có vision. Không chạy solve hoặc tải model chỉ để healthcheck.

## 12. Kiểm chứng và tiêu chí nghiệm thu

### 12.1 Các tầng độc lập

| Tầng | Cách kiểm chứng | Chứng minh được / không được |
|:--|:--|:--|
| Unit/FakeBackend | Schema, selection, deadlines, verification, state transitions | Logic Python; không chứng minh DOM payload hoặc provider |
| CDP transport integration | Loopback WS với event interleave, child sessions, timeout, hai caller đồng thời | Không mất event/response, không gửi input trùng; chưa phải Chrome |
| Chrome fixture thật | Payload/iframe/input/screenshot chạy qua CDP | Backend hoạt động thật; fixture không phải CAPTCHA provider |
| MCP client thật | Khởi chạy server qua stdio, client nhận JSON + image, gọi start/act/observe | Agent có đường nhìn và thao tác; gọi hàm Python trực tiếp chưa đủ |
| Provider test keys | Widget test + backend test-secret validation trong fixture kiểm soát được | Integration với SDK/provider; không chứng minh giải challenge thật [S5–S7] |
| Challenge thật trong môi trường được phép | Agent/model thực, có prompt/ảnh, nhiều vòng, outcome quan sát được | Năng lực giải trong đúng điều kiện đã đo; không suy rộng mọi site |
| Ubuntu vận hành | Chạy cùng revision/model/config trong Xvfb và `--headless` riêng | Khả năng unattended ở runtime đích; kết quả desktop không thay thế |

Test key có loại always-pass/always-fail/interactive tùy provider; không gom tất cả thành “luôn pass”. Fixture Siteverify chỉ dùng test secrets. Với site thật, không yêu cầu/extract secret của site.

`false-success` được tính theo mức xác minh đã công bố: token/UI pass hợp lệ vẫn chỉ là widget-level khi backend chưa chấp nhận. Backend reject phải cấm `access_verified`; không đánh đồng mọi `widget_passed` với business success. Verifier benchmark dùng oracle độc lập phía test server/site outcome, không dùng chính kết luận solver làm đáp án chuẩn.

### 12.2 Regression và fault cases bắt buộc

1. False positive: carousel/range slider, QR/verification image thường, title giả, provider URL trong query, badge v3, widget ẩn.
2. Không thấy candidate, frame scan partial, eval fail; không trường hợp nào trở thành thành công giả.
3. Hai widget cùng sitekey/URL; chọn đúng response field, không dùng token của widget khác.
4. Cross-origin khác process, nested OOPIF, frame detach/recreate và navigation cùng URL. Khác port chỉ tạo cross-origin, chưa bảo đảm khác site/process; test phải assert target/session OOPIF thật, không tắt isolation.
5. Grid thay ảnh sau click, ô mới giữ tọa độ cũ, animation/loading, ảnh crop thiếu prompt, model trả point ngoài bounds hoặc action ngoài whitelist.
6. Scroll, nested scroll, DPR 1/2, zoom, iframe border, overlay che checkbox, target resize giữa observe/act; reject stale thay vì click lệch.
7. Token tồn tại trước start, expired, token widget khác, widget bị remove mà backend reject, redirect sang trang lỗi, title đổi, reload about:blank; **0 false-success** trong negative suite.
8. Challenge biến mất nhưng expected content không có → unverified; challenge kết thúc cùng URL và đúng content → access_verified.
9. Hai caller thật đồng thời cùng tab, hai tab khác nhau, event xen kẽ response; đếm chính xác số input side effects. Sequential retry không chứng minh atomic dedupe.
10. Cùng action id gọi lại, payload conflict, disconnect sau dispatch, browser restart, cancel, lease TTL hết khi agent không quay lại, generic mutating tool chạy trong lease.
11. Deadline khi recv liên tục event, screenshot chậm, model kẹt, media fetch treo; không tăng budget ở solver kế tiếp hoặc resume.
12. Thiếu extra/model, máy offline, config disabled, SDK image unavailable; base install vẫn start và tool cũ hoạt động.
13. Audio CORS fail, prompt dạng từ có khoảng trắng, wrong answer/reload; OCR đúng text nhưng sai input không được submit; drag cancel luôn cố release.
14. Ảnh/prompt chứa chỉ dẫn ngoài CAPTCHA không sinh action ngoài challenge; result/log không có raw token/cookie.

### 12.3 Definition of Done theo mức

**Foundation (P0–P2)**: toàn bộ negative suite liên quan pass; Chrome/OOPIF và MCP image roundtrip đã chạy thật, không có test bắt buộc bị skip. Không tuyên bố đã tự giải CAPTCHA ảnh nếu mới làm xong foundation.

**Agent vision MVP (P3)**: agent thực đọc ảnh từ MCP và giải end-to-end trên fixture browser có đáp án ẩn phía test server, gồm grid tĩnh, grid thay ảnh và nhập text. Agent không được đọc đáp án trong DOM/fixture source; test recorder chứng minh ảnh → action → outcome. Tối thiểu 20 seed mỗi dạng và báo tỷ lệ hoàn thành. Gate đề xuất: >=90% cho từng dạng fixture được hỗ trợ, 0 false-success, không người click/chọn ô hộ.

**Provider-qualified (P5)**: mỗi provider/challenge được quảng cáo phải có tối thiểu 20 challenge thật được trình bày và thử trong scope được phép, qua ít nhất hai phiên; ghi số start tổng, passive pass, interactive challenge, unsupported, blocked, timeout, success và verification level. Báo `k/n` riêng cho interactive, thời gian p50/p95 và tỷ lệ giải trên tất cả start; không loại ca khó khỏi mẫu. Mẫu nhỏ chỉ cho bằng chứng ban đầu, không cam kết SLA. Ngưỡng release đầu tiên: >=80% trên interactive subset đã tuyên bố hỗ trợ, 0 observed false-success; nếu không đạt thì gắn experimental, không đổi denominator để đạt gate.

Không có môi trường/challenge thật phù hợp thì đánh dấu tầng provider **chưa kiểm chứng**. Unit/test-key pass không thay thế gate đó. Những tỷ lệ trên là mục tiêu nghiệm thu đề xuất, không phải kết quả hiện có hay bảo đảm thống kê.

**Unattended readiness**: ít nhất một phiên 60 phút trên Ubuntu cho mỗi display mode được tuyên bố hỗ trợ, không can thiệp người, không lease/worker/socket/file leak sau cancel/timeout. Dùng fixture lặp lại cho soak test; challenge thật là subset có giới hạn, không tạo lưu lượng thử vô hạn lên provider.

Live suite thông thường vẫn có thể skip ở máy dev. Acceptance command phải kiểm tra Chrome/MCP/model prereq trước; required suite thiếu prereq hoặc skip → kết quả không đủ điều kiện release, không green giả.

Report đặt tại `docs/verification/captcha/<run-id>.md`, kèm revision, Chrome version, OS/display mode, model/config, provider/challenge, fixture/test-key/real label, counts, failure taxonomy, latency, evidence paths đã redact và các gate chưa đạt.

## 13. Kế hoạch triển khai theo thứ tự phụ thuộc

Không dùng P0–P4 cũ: vision và frame foundation được đưa lên trước extras audio/OCR.

| Phase | Deliverable và file chính | Gate trước khi chuyển |
|:--|:--|:--|
| **P0 — contract + detection** | `captcha.py`, `captcha_state.py`, `js/captcha_detect.js`; tool detect, config/enum/error skeleton, tests schema/detection | Phân biệt absent/uncertain/error; multi-widget/negative fixtures; base import không có ML |
| **P1 — CDP/frame/input foundation** | `backends/base.py`, `cdp.py`, `_ws.py` nếu cần; lifecycle routing, deadline, locks, frame payloads, geometry | WS concurrency + real OOPIF/nested frame + tọa độ; 17 tool cũ không regression |
| **P2 — solve lifecycle + verification** | `captcha_verify.py`, registry passive/checkbox, Session lease/guard/reset, solve tool, receipts | Không false-success; timeout/dedupe/recovery; MCP schema và 19 tool |
| **P3 — agent vision MVP** | Observation/crop/image output, bounded action executor, agent-loop instructions; thêm drag nếu support puzzle | Agent/model thật qua MCP; fixture grid động/text; Ubuntu image roundtrip |
| **P4 — local extensions** | OCR → audio → CV theo nhu cầu benchmark; worker/model provisioning; optional extras | Dataset ML + browser end-to-end đúng input/control; dependency matrix/offline readiness |
| **P5 — provider và unattended qualification** | Real-challenge benchmark, Ubuntu runs, report, `docs/CAPTCHA.md`, README/DESIGN cập nhật | Gate mục 12 đạt cho từng loại được công bố; mọi gap ghi rõ |

P4 không chặn P5 cho các loại đã giải được bằng agent vision. P0–P2 có thể phát hành khả năng phát hiện/checkbox, nhưng mục tiêu “AI tự giải CAPTCHA ảnh” chỉ đạt khi P3 và phần P5 tương ứng có bằng chứng.

### Checklist công việc có thể giao triển khai

- [ ] **T01 / P0**: Viết enums/models và JSON schema tests trước; xác định `success`, terminal và invalid argument behavior.
- [ ] **T02 / P0**: Detection payload + provider grouping + visibility/coverage + fixtures negative; đăng ký detect tool.
- [ ] **T03 / P1**: CDP demultiplex/event lifecycle + transport serialization/deadline; real loopback race tests.
- [ ] **T04 / P1**: FrameRef/discovery/eval + invalidation + OOPIF real test; capability remedies và FakeBackend contract.
- [ ] **T05 / P1**: Crop/viewport mapping + target hit-test + focus/input; scroll/DPR/zoom/nested frame acceptance.
- [ ] **T06 / P2**: Solve state machine/lease/cooldown/dedupe; wire guards vào server/Session, reset/close cleanup; concurrent callers.
- [ ] **T07 / P2**: Provider evidence + expected postcondition + verification negative suite; passive/checkbox strategies.
- [ ] **T08 / P2**: Tool start/observe/act/cancel, schema validation và mixed content; update EXPECTED_TOOLS + SDK 1/2 compatibility.
- [ ] **T09 / P3**: Observation/actions/image roundtrip, dynamic grid invalidation, agent instructions; real agent fixture benchmark.
- [ ] **T10 / P3–P4**: Drag capability/executor khi cần; puzzle geometry tests; không gộp mọi slider thành một solver.
- [ ] **T11 / P4**: Resolve extras/runtime compatibility, provision model ngoài solve, worker cancellation, ML fixtures rồi browser E2E.
- [ ] **T12 / P5**: Provider test-key integrations, real challenge qualification, Ubuntu Xvfb/headless reports, failure matrix.
- [ ] **T13 / mỗi phase**: Docs chính xác với khả năng đã implement; wheel bao gồm JS payload; kiểm tra fresh base install và packaged extras.

### Các file test dự kiến

`tests/test_captcha.py`, `test_captcha_verify.py`, `test_captcha_state.py`,
`test_cdp_frames.py`, `test_cdp_dispatch.py`, `test_captcha_mcp.py`,
`test_live_captcha.py`, `test_live_captcha_provider.py`, cùng fixture HTML/JS/media có provenance.
Bổ sung cases vào `test_config.py`, `test_server.py`, `test_payloads.py`, `test_ws.py` khi hợp lý;
không bắt provider live tests chạy trong unit suite mặc định.

### Docs cần cập nhật khi implement

- `docs/CAPTCHA.md`: support matrix, agent-loop usage, result semantics, extras/models, limits, evidence, troubleshooting.
- `README.md` và `server.py`: số tool, mô tả năng lực theo phase, client vision requirement, hướng dẫn unattended.
- `docs/DESIGN.md`: bổ sung lifecycle events/flat sessions và lý do có state/lease. Làm rõ giới hạn “No anti-detection”: có explicit CAPTCHA UI handling, vẫn không stealth/fingerprint spoofing. Không sửa thành lời hứa vượt mọi bot protection.
- Hướng dẫn người vận hành dùng trong workflow/site được phép và tôn trọng giới hạn site; không thêm quy trình confirm thủ công mỗi CAPTCHA.

## 14. Những gì còn phải xác nhận bằng thực nghiệm

Các quyết định kiến trúc đã đủ để bắt đầu P0/P1. Những điểm sau là gate thực nghiệm, không được đóng bằng suy đoán:

1. Tỷ lệ giải từng loại CAPTCHA trên provider/site thực tế mà workflow cần.
2. Model vision của client có đọc đủ tốt ảnh challenge ở cấu hình remote thực tế không.
3. OOPIF attach/geometry trên các Chrome version và display mode sẽ hỗ trợ.
4. Khả năng tải audio qua CORS và model STT phù hợp với ngôn ngữ challenge thực.
5. Các extras ML cài được cùng nhau trên Python/OS đích và giới hạn tài nguyên chấp nhận được.

Không cần đợi các số liệu này mới implement foundation; chưa có số liệu thì các adapter tương ứng ở trạng thái experimental/unverified. Phạm vi “hoàn thành” luôn gắn với provider, challenge kind, runtime và verification level cụ thể.

## 15. Nguồn kỹ thuật đã đối chiếu

Đã tra tài liệu chính thức trong lần review 2026-09-27; selector/provider UI vẫn có thể đổi, cần kiểm chứng lại khi implement.

- **[S1]** [hCaptcha FAQ — accessibility](https://docs.hcaptcha.com/faq): email/text/passive accommodations; không giả định audio chung với reCAPTCHA.
- **[S2]** [Chrome — work with frames and related targets](https://developer.chrome.com/docs/extensions/reference/api/debugger#work-with-frames): phân biệt frame/context/target và attach frame con. Nguồn giải thích mô hình CDP qua extension API; TabPilot vẫn dùng WebSocket transport hiện có.
- **[S3]** [Google — verifying the response](https://developers.google.com/recaptcha/docs/verify): backend validation, hiệu lực và single-use.
- **[S4]** [Cloudflare — server-side validation](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/): token phải được backend validate, có expiry/single-use.
- **[S5]** [Google — testing reCAPTCHA](https://developers.google.com/recaptcha/docs/faq): test keys và khác biệt test v2/v3.
- **[S6]** [hCaptcha — integration testing](https://docs.hcaptcha.com/#integration-testing-test-keys): test keys/passcodes không có giá trị đo khả năng giải thật.
- **[S7]** [Cloudflare — Turnstile testing](https://developers.cloudflare.com/turnstile/troubleshooting/testing/): dummy keys và các kết quả test.
- **[S8]** [CDP Target](https://chromedevtools.github.io/devtools-protocol/tot/Target/) và [CDP Page](https://chromedevtools.github.io/devtools-protocol/tot/Page/): session/target và isolated world primitives.
- **[S9]** [CDP Input — dispatchMouseEvent](https://chromedevtools.github.io/devtools-protocol/tot/Input/#method-dispatchMouseEvent): hệ tọa độ và trạng thái pointer.

Các budget, schema, stage order và ngưỡng benchmark trong tài liệu là quyết định thiết kế đề xuất của TabPilot, không phải bảo đảm hay khuyến nghị tỷ lệ thành công từ các nhà cung cấp.
