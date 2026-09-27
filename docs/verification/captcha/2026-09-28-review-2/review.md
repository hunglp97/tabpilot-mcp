# CAPTCHA re-review 2 — 2026-09-28

**Kết luận: bản `b3a98c3` sửa được nhiều ca lỗi trước, nhưng chưa đủ bằng chứng hoàn thành mục tiêu agent tự giải CAPTCHA. Còn 6 finding P1 và 1 finding P2 đã tái hiện.**

Review so với `03473de`; working tree sạch trước khi thêm artifacts này. Không sửa implementation. Các kết quả dưới đây được chạy lại trên revision hiện tại, không lấy kết quả cũ thay cho verification mới.

Môi trường: macOS, Chrome `154.0.8037.57`, headless, profile test riêng. Các trang Chrome là fixture HTTP local. Không có lần chạy model vision tự suy luận đáp án hoặc qualification với provider thật trong review này.

## Kết quả kiểm thử

| Tầng | Kết quả | Phạm vi chứng minh |
|---|---|---|
| Offline suite | **207 passed**, 53 deselected, 5.14s | Unit/integration và fault tests trong suite |
| Live suite | **53 passed**, 207 deselected, 20.44s | Chrome thật trên local fixtures |
| MCP stdio + Chrome | **19 tools**, `needs_agent`, content `text` + `image` | Ảnh tới được client MCP với cấu hình mặc định |
| Transport event flood | Timeout yêu cầu 10ms, đo 10ms, có TimeoutError | Deadline của command trong test flood |
| Inspection failure | `unverified`, giữ lỗi gốc | Không đổi inspection failure thành no_captcha |
| Recheck cũ | Nhiều lỗi cũ đã hết; chi tiết bên dưới | Negative fixtures và fault injections |
| Extended recheck | 7 vấn đề còn lại, có counters/JSON | Được tách rõ Chrome thật và FakeBackend |

Tổng suite hiện có: **260 tests pass**. Không cộng lại các test CAPTCHA đã nằm trong tổng. Script review ghi nhận hành vi; exit code 0 của script không có nghĩa các acceptance criteria đều đạt.

## Những sửa đổi đã xác nhận

Đối chiếu `recheck-results.json` và review trước:

| Finding trước | Kết quả đo ở bản mới | Kết luận có giới hạn |
|---|---|---|
| R01: foreign frame của tab khác | A chỉ trả FRAME-A; `has_foreign_B=false` | Ca lẫn tab đã sửa; còn N01/N02 |
| R02: navigation cùng selector | `stale_observation`, clicks=0 | Ca đổi document đã sửa |
| R02: đổi prompt và ảnh | `stale_observation`, clicks=0 | Ca đổi prompt đã sửa; ảnh đổi riêng vẫn lỗi N03 |
| R03: Verify của widget khác | `unsupported`, submit_count=0 | Ca Verify fallback đã sửa; action khác còn N06 |
| R04: grid nhận sibling pass | `needs_agent`, success=false | Ca grid đã sửa; text còn N04 |
| R04: interstitial thành 403 | `waiting`, success=false | Ca 403 đã sửa; 503 còn N05 |
| R04: token mới kèm error | passed=false | Ca token/error đã sửa |
| R05: crop container vs canvas | verify_clicks=1, canvas_clicks=0 | Ca sai transform cụ thể đã sửa |
| R06: slow screenshot | capture timeout nhận 0.049s; kết quả terminal `timeout` | Đã sửa truyền budget cho capture và status; chưa bao toàn action, N07 |
| R07: click rồi mất phản hồi | click_count=1; lần sau ActionOutcomeUnknownError | Không replay ở fault case cũ |
| R08: strategy sai giữ lease | StrategyIncompatibleError, active_lease=false | Ca invalid strategy đã sửa |

Repeated start vẫn trả cùng solve_id; selector-only access predicate vẫn verify được; checkbox newsletter không tạo pass giả. MCP image delivery và inspection error preservation vẫn pass.

Lưu ý slow-capture fake cố tình không tuân thủ timeout, nên thời gian 152ms của riêng fake này **không** chứng minh CDP bỏ qua timeout mới. N07 bên dưới dùng fault khác để chứng minh input vẫn được gửi sau deadline.

## Findings còn lại

### N01 — P1: CAPTCHA trong iframe chưa đi qua detection/solver

**Code:** `src/tabpilot/captcha.py:70–71,112–119`; các solver vẫn dùng `eval_js(tab.id, ...)` ở top document. `list_frames`/`evaluate_in_frame` hiện chỉ có implementation backend, chưa được CAPTCHA flow gọi.

**Chrome repro:** top document chỉ chứa cross-site iframe local; trong iframe có grid CAPTCHA. Backend list/evaluate xác nhận `child_has_challenge=true`, nhưng tool trả `detection_status=absent`, `candidate_count=0`, `solve_status=no_captcha`.

**Ảnh hưởng:** có API frame không đồng nghĩa agent đã xử lý được challenge trong frame. Điều này chặn mục tiêu browser CAPTCHA thực tế và có thể khiến caller tiếp tục vì tưởng không có CAPTCHA.

**Cần sửa / acceptance:**

- Traverse frames của đúng tab, bind candidate với frame/session/document identity, chạy detector trong từng context được hỗ trợ.
- Dùng cùng frame identity cho layout, input và verification. Frame không inspect được phải ghi coverage/limitation tương ứng, không suy ra chắc chắn không có challenge.
- Test cross-origin iframe chứa challenge, qua MCP detect → observe → act → independent oracle. Assert action/evidence thuộc frame đó, gồm nested-frame case theo spec.

### N02 — P1: hai OOPIF cùng URL bị nhập thành một FrameRef

**Code:** `src/tabpilot/backends/cdp.py:425–428`.

`existing` match bằng `frame_id` **hoặc URL**. URL không phải identity của frame; frame thứ hai có thể ghi đè session/target của frame thứ nhất.

**Chrome repro:** hai iframe cùng URL `/childA`, `window.name` khác nhau. DOM có 2 iframe, `list_frames` chỉ trả 1 child, evaluate chỉ đọc được `FRAME-TWO`.

**Cần sửa / acceptance:** match theo frame/target/session identity và ancestry, không gộp bằng URL. Hai iframe cùng URL phải cho hai ref độc lập; evaluate từng ref trả đúng marker. Giữ regression hai tab khác nhau để không tái phát R01.

### N03 — P1: ảnh challenge đổi nhưng prompt giữ nguyên vẫn nhận action cũ

**Code:** `src/tabpilot/captcha_solvers/agent_vision.py:257–260,290–298`.

Precheck chỉ có document id, widget existence và prompt. `image_id` chỉ đối chiếu với observation đang lưu, không xác nhận nội dung challenge hiện tại còn khớp ảnh đó.

**Chrome repro:** chụp grid có tile xanh; đổi tile sang đỏ, giữ prompt, selector và geometry; gửi `select_tile` từ observation cũ. Kết quả **clicks=1**, status `needs_agent`.

**Ảnh hưởng:** dynamic grid/refresh cùng đề bài có thể khiến agent click nội dung khác với ảnh nó đã đọc.

**Cần sửa / acceptance:** bind observation với challenge revision/content fingerprint và geometry của đúng widget/frame; invalidation phải bắt được thay đổi ảnh/canvas/tile dù prompt không đổi. Action cũ phải trả stale trước input, side-effect counter bằng 0, sau đó cấp observation mới. Áp dụng cả tile và point/drag.

### N04 — P1: text CAPTCHA vẫn lấy pass marker của widget bên cạnh

**Code:** `src/tabpilot/captcha_verify.py:120–122`.

Nhánh `image_text` vẫn ưu tiên `root.closest('form, .challenge-section')` rồi tìm marker trong toàn container. Sửa scope cho grid chưa đóng được nhánh này.

**Chrome repro:** canvas/input chưa được giải trong `.captcha-box`; cùng form có `.captcha-passed` của widget khác. `start` trả **widget_passed, success=true, attempts=0**.

**Cần sửa / acceptance:** proof phải gắn với selected widget, không dùng marker bất kỳ trong form. Thêm negative test hai text widgets cùng form; sibling pass không được kết thúc solve hiện tại.

### N05 — P1: interstitial biến mất vào trang 503 vẫn bị coi là pass

**Code:** `src/tabpilot/captcha_verify.py:76–83`.

Blacklist các từ 403/access denied/error chỉ chữa được vài ví dụ. Điều kiện cuối vẫn coi việc không còn challenge marker và body có child là đủ pass.

**Chrome repro:** interstitial → body `503 Service Unavailable`, title `Service Unavailable`. `observe` trả **widget_passed, success=true**.

**Cần sửa / acceptance:** marker biến mất chỉ là tín hiệu chuyển trạng thái, không phải proof hoàn tất. Cần bằng chứng đích/postcondition độc lập; thiếu bằng chứng thì waiting/unverified phù hợp. Test 403, 503, loading shell và success page; không đóng finding bằng cách bổ sung một từ vào blacklist.

### N06 — P1: select_tile và refresh còn chọn phần tử ngoài widget

**Code:** `src/tabpilot/captcha_solvers/agent_vision.py:315–320` và `:494–503`.

`select_tile` dùng `document.getElementById(target_id)`/document-wide selector, trong khi `tile-0` là ID do observation tự sinh. Một phần tử ngoài challenge có DOM id này sẽ được chọn trước tile thật. `refresh` cũng tìm `.refresh-btn` trên toàn document rồi gọi click.

**Chrome repro:**

- Grid có một tile được observe là `tile-0`, DOM id tile thật là `actual-grid-tile`; ngoài grid có button id `tile-0`: **foreign_clicks=1, grid_clicks=0**.
- Grid không có refresh control, trang có button `.refresh-btn` không liên quan: action refresh tạo **foreign_clicks=1**.

**Cần sửa / acceptance:** bind opaque action target/control IDs với phần tử thuộc selected widget/frame khi observe; resolve lại trong đúng scope trước dispatch. Không dùng synthetic observation ID như DOM ID toàn trang. Không thấy control của candidate thì unsupported/stale, không fallback ra ngoài. Assert mọi foreign counter bằng 0 cho tất cả action kinds.

### N07 — P2: hết deadline trong precheck nhưng vẫn gửi input

**Code:** `src/tabpilot/captcha_solvers/agent_vision.py:264,384–385`; các nhánh action khác cũng dùng timeout/sleep cố định.

Đã check budget đầu action và sửa screenshot timeout. Tuy nhiên precheck vẫn có timeout 3s, input 5s, không check lại deadline ngay trước dispatch.

**Fault injection, FakeBackend:** để active solve còn 50ms, cho precheck mất 100ms (vẫn ngắn hơn timeout 3s được truyền). Kết quả **clicked_after_deadline=true** rồi mới trả `timeout`.

**Cần sửa / acceptance:** truyền remaining budget xuyên resolve/detect/precheck/layout/input/verification; kiểm tra lại trước từng side effect và giới hạn các khoảng đợi. Test phải assert **không có input sau deadline**, không chỉ assert status cuối là timeout. Call ceiling/cooldown theo spec cũng cần evidence riêng trước khi công bố hoàn tất.

## Thứ tự làm tiếp

- [ ] N04/N05: loại false pass, bổ sung negative verification tests cho text và interstitial.
- [ ] N03/N06: observation freshness và widget-scoped target mapping cho mọi action.
- [ ] N02 rồi N01: sửa identity backend trước, sau đó nối frame-aware flow xuyên detector/solver/verifier.
- [ ] N07: deadline đi xuyên mọi bước và kiểm tra counter tại thời điểm dispatch.
- [ ] Chuyển các repro trên thành regression tests có assertions về status, identity và số side effects. Giữ nguyên các case cũ đã pass.
- [ ] Sửa claim trong `docs/CAPTCHA.md` theo evidence mới: “every step propagates remaining budget” và pass scoping chưa đúng toàn bộ. Docs đã bỏ operation `status` sai và sửa một số test links; không cần lặp lại các sửa đó.
- [ ] Chạy lại offline suite, Chrome fixture suite, MCP stdio và negative/fault cases. Ghi source revision/hash ở run mới.
- [ ] Sau khi blocker hết: model vision thật nhận ảnh qua MCP, tự quyết định nhiều vòng trên fixture nhiều seed; dùng oracle độc lập. Không dùng script biết sẵn đáp án thay cho agent acceptance.
- [ ] Qualification provider thật và môi trường Ubuntu theo spec là tầng riêng, chưa được review này xác nhận.

## Chạy lại và evidence

Các script dưới đây tự tạo Chrome profile/port riêng; **copy sang thư mục run mới trước khi chạy lại** vì JSON output nằm cạnh script.

```sh
rtk proxy .venv/bin/python -m pytest -q -m 'not live'
rtk proxy .venv/bin/python docs/verification/captcha/2026-09-28-review-2/recheck.py
rtk proxy .venv/bin/python docs/verification/captcha/2026-09-28-review-2/extended.py
rtk proxy .venv/bin/python docs/verification/captcha/2026-09-28-review-2/mcp_roundtrip.py
rtk proxy .venv/bin/python docs/verification/captcha/2026-09-28-review-2/transport_faults.py
```

Live suite được chạy bằng `rtk proxy .venv/bin/python -m pytest -q -m live` dưới Chrome test riêng trên port 9222, đã kiểm tra port trống trước khi khởi động và đóng process sau khi xong. Không dùng Chrome profile/tab nghiệp vụ.

Evidence trong thư mục này:

- `recheck.py` / `recheck-results.json`: đối chiếu ca cũ.
- `extended.py` / `extended-results.json`: N01–N07; chỉ case deadline cuối là FakeBackend, các case trước chạy Chrome thật.
- `mcp_roundtrip.py` / `mcp_result.json`: MCP image transport qua process thật.
- `transport_faults.py` / `fault_results.json`: command timeout và inspection fault.
- `live-suite.txt`, `validation.json`: kết quả suite và metadata lần chạy.
- `source-sha256.json`: revision và fingerprints source/tests được review.

Đây là review và handoff tiếp tục implementation; chưa phải xác nhận agent tự giải CAPTCHA end-to-end.
