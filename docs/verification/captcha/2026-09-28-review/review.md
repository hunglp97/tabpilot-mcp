# CAPTCHA re-review — 2026-09-28

**Kết luận: đã sửa một số ca lỗi cũ, nhưng chưa đóng đủ F01–F11 và chưa đạt unattended readiness.** Báo cáo run-2 khẳng định mọi finding đã resolved rộng hơn những gì tests/evidence chứng minh.

- Revision được review: `03473de` (`save`), working tree sạch trước khi thêm artifacts review này.
- Môi trường: macOS, Python 3.14, Chrome 154.0.8037.57, headless, profile/port riêng.
- Không sửa implementation, không thay đổi evidence run-2, không dùng tab nghiệp vụ/provider thật.
- Source fingerprints: `source-sha256.json`.

## 1. Kết quả đã chạy lại

| Tầng | Kết quả hiện tại | Giới hạn |
|---|---|---|
| Non-live suite | **207 passed**, 53 deselected, 4.97s | Bao gồm 11 regression tests; nhiều tests chỉ dùng FakeBackend |
| Toàn bộ live suite | **53 passed**, 207 deselected, 20.00s | Chrome thật, nhưng CAPTCHA fixture/đáp án biết trước |
| MCP stdio + Chrome thật | 19 tools, `needs_agent`, content gồm **text + image** | Chứng minh image transport; chưa chứng minh model tự giải |
| Transport event flood | Timeout 10ms, thực tế khoảng 12ms, có TimeoutError | Fix ở một CDP command; chưa chứng minh deadline toàn solve |
| Inspection fault | Trả `unverified`, giữ evidence lỗi | F11 case cũ đã khắc phục |
| Recheck fixture nâng cao | Vẫn có click sai widget/trang/ảnh, false pass, foreign frame | Bằng chứng trong `recheck-results.json` |

**Tổng 260 tests pass**; không cộng lại regression suite hoặc 6 CAPTCHA live tests vì đã nằm trong tổng.

Các lệnh đã chạy:

```sh
rtk proxy .venv/bin/python -m pytest -q -m 'not live'
rtk proxy .venv/bin/python -m pytest -q -m live
rtk proxy .venv/bin/python docs/verification/captcha/2026-09-28-review/mcp_roundtrip.py
rtk proxy .venv/bin/python docs/verification/captcha/2026-09-28-review/transport_faults.py
rtk proxy .venv/bin/python docs/verification/captcha/2026-09-28-review/recheck.py
```

Live suite được chạy sau khi khởi động Chrome test riêng trên port 9222 và đóng process đó sau khi xong. Các script review tự tạo profile/port riêng. Không dùng evidence cũ để thay cho lần chạy hiện tại.

## 2. Những phần đã xác nhận cải thiện

- **F04:** đường MCP stdio thật đã trả ảnh mặc định khi `needs_agent`.
- **F08, ca sequential:** gọi start lại cùng candidate trả cùng active solve id, không còn orphan id như repro cũ.
- **F09:** selector-only expected chạy được trên Chrome thật, không còn lỗi JS `None` ở đường này.
- **F11:** injected inspection failure được giữ là unverified.
- **F02, ca checkbox newsletter:** không còn nhận checkbox newsletter là pass.
- **F10, transport event flood:** `_command` dừng khi hết deadline trong test flood đã chạy lại.
- **F07, happy path:** test hai caller cùng action id đã pass; đường side-effect-then-error vẫn lỗi như R07 bên dưới.

Các xác nhận này chỉ áp dụng đúng case được đo, không đóng các invariants rộng hơn trong NEXT-STEPS/spec.

## 3. Findings còn lại

### R01 — P1 / F05: `list_frames(tab A)` trả frame thuộc tab B

**Code:** `src/tabpilot/backends/cdp.py:402–464`.

Sau `Target.getTargets`, code attach mọi target có type iframe và gán `parent_id=root_id` của tab đang hỏi, không xác minh ancestry. Đây là browser-wide discovery được dùng như tab-scoped discovery.

**Repro Chrome thật:** mở hai tab fixture, mỗi tab có một cross-site iframe riêng. Gọi list_frames(A), sau đó evaluate theo FrameRef trả về:

```json
{"children_returned_for_A":[{"url_path":"childB","value":"FRAME-B"},{"url_path":"childA","value":"FRAME-A"}],"has_foreign_B":true}
```

Hậu quả: đọc hoặc thao tác frame không thuộc tab được giao. Số child_refs tăng trong run-2 không chứng minh mapping đúng. Solver adapters hiện cũng chưa gọi `list_frames`/`evaluate_in_frame`, nên việc backend có API chưa hoàn thành provider frame handling.

**Cần sửa:** chỉ attach/traverse frame trong ancestry của target đã bind; giữ parent/frame/session mapping thực, gồm nested targets và invalidation. Test hai tab cùng provider/URL, assert không trả frame của tab khác và evaluate đúng marker. Không dựng synthetic parent mapping cho mọi iframe trong browser.

### R02 — P1 / F03: navigation và challenge đổi ảnh vẫn không làm action cũ mất hiệu lực

**Code:** `src/tabpilot/captcha_solvers/agent_vision.py:211–242`.

Sau navigation, document mới chưa có `window.__tabpilot_doc_id`; điều kiện `if current_doc_id and ...` bỏ qua mismatch. Nếu selector widget tồn tại ở trang mới, action cũ được chạy. Trên cùng document, code chỉ kiểm tra widget tồn tại, không so ảnh/prompt/challenge revision/geometry. Exception trong kiểm tra cũng bị nuốt rồi tiếp tục input.

**Repro Chrome thật:**

- Document mới có cùng `#grid`, `document_id_before_action=null`: action ảnh cũ vẫn tạo **1 click**, trả needs_agent.
- Thay prompt và đổi tile từ xanh sang đỏ, giữ DOM id/geometry: action từ ảnh trước vẫn tạo **1 click**, trả needs_agent.

**Cần sửa:** fail closed khi identity thiếu/lỗi; lifecycle generation thực và challenge/geometry fingerprint; bind action với observation hiện tại. Sau stale phải cung cấp observation mới có identity nhất quán. `image_id` phải bắt buộc với point/drag, không chỉ kiểm tra khi caller tình cờ truyền.

### R03 — P1 / F01: Verify vẫn có fallback sang widget khác

**Code:** `src/tabpilot/captcha_solvers/agent_vision.py:392–399`, đồng thời layout discovery ở `:563–564`.

Đã bỏ generic submit và double click, nhưng vẫn có `document.querySelector('#captcha-verify-btn', ...)` fallback. Với widget A không có Verify và widget B có nút mang selector đó, solver A click nút của B.

**Repro Chrome thật:** widget A là CAPTCHA canvas; widget B nằm trong form khác, nút `id=captcha-verify-btn` là submit. Một action Verify của A tạo **submit_count=1** trên form B.

**Cần sửa:** control id được server bind với đúng widget/frame/observation; không có global selector fallback hoặc fallback parent rộng. Không định vị được control của A thì unsupported/observation mới, không mượn control B. Áp dụng cùng invariant cho type_answer/refresh/select_tile.

### R04 — P1 / F02: vẫn báo pass giả ở ba đường

**Code:** `src/tabpilot/captcha_verify.py:50–56`, `:62–76`, `:136–144`.

1. Scope mở rộng sang form cho phép `.captcha-passed` của widget bên cạnh chứng minh widget hiện tại pass.
2. Interstitial root biến mất và title hết “Just a moment” được coi là pass, kể cả trang mới là lỗi.
3. Token mới được chấp nhận trước khi kiểm tra explicit error/expiry; error check chỉ nằm trong nhánh UI fallback.

**Repro Chrome thật:**

- Grid chưa giải, sibling marker trong cùng form: **widget_passed, success=true, attempts=0**.
- Challenge chuyển thành **403 Access denied**: **widget_passed, success=true**.
- Token có giá trị mới cùng `.rc-anchor-error`: verifier trả **passed=true**.

**Cần sửa:** evidence thuộc đúng challenge; trạng thái lỗi/expiry phủ định token/UI pass; interstitial cần postcondition đích và hết blocking, nếu chỉ marker mất thì unverified. Giữ phân biệt widget-level/access-level nhưng không dùng nó để chấp nhận tín hiệu pass sai.

### R05 — P1 / F06: normalized point bị ánh xạ sang phần tử khác với ảnh

**Code:** `src/tabpilot/captcha_solvers/agent_vision.py:296–318`, so với layout/capture `:499–518`.

Ảnh crop có thể là toàn bộ `.captcha-box`/container, còn action `click_point` tính lại rect từ `candidate.widget_ref` là canvas nhỏ bên trong. Agent chọn đúng điểm trên ảnh nhưng server nhân tọa độ với một rect khác.

**Repro Chrome thật:** crop box 400×200, canvas 100×40, điểm ảnh nằm đúng nút Verify. Kết quả: **verify_clicks=0, canvas_clicks=1**.

**Cần sửa:** point luôn dùng transform của image_id/observation tương ứng. Nếu target/crop/geometry đổi thì stale và chụp lại; không đổi sang rect phần tử con để tính điểm. Bản sửa cộng scroll đúng chưa đủ để đóng F06.

### R06 — P2 / F10: deadline tổng vẫn có thể hết mà trả needs_agent

**Code:** `src/tabpilot/captcha_solvers/agent_vision.py:93–99`; call chain trong `captcha.py`.

Timeout cho capture vẫn bị ép ít nhất 3 giây và nhiều bước dùng timeout cố định. `captcha_call_timeout_ms`/cooldown chưa được orchestrator thực thi. Deadline transport đã sửa không làm deadline toàn solve tự động đúng.

**Fault injection:** capture chậm 150ms, yêu cầu solve timeout 50ms. Kết quả **elapsed=156ms, capture_timeout=3.0s, status=needs_agent, remaining_ms=0**.

**Cần sửa:** propagate remaining deadline vào mọi bước; check lại sau I/O, trả terminal timeout khi hết hạn. Test call ceiling, detect/resolve/capture chậm, model wait và cooldown qua start/resume.

### R07 — P1 / F07: retry action có outcome unknown vẫn click lại

**Code:** `src/tabpilot/captcha.py:444–475`.

Receipt chỉ ghi khi handle_action thành công. Nếu input đã được gửi rồi bước sau ném lỗi, finally xoá in-flight entry mà không giữ unknown receipt/tombstone. Gửi lại cùng action id trở thành action mới.

**Fault injection:** FakeBackend ghi click_at thật vào recorder rồi injected JSError mô phỏng lỗi sau dispatch. Hai lần cùng id/payload tạo **click_count=2**, cả hai trả JSError.

**Cần sửa:** lưu phase và outcome unknown trước khi bỏ reservation; không tự replay side effect khi chưa xác định. Terminal receipt phải còn truy vấn được sau release lease. Test cả concurrent happy path, post-dispatch exception và duplicate delivery sau terminal.

### R08 — P2: invalid strategy/capability để lại lease khóa tab

**Code:** `src/tabpilot/captcha.py:315`, `:340–348`.

Start acquire lease trước khi get_solver_adapter/session.require; các lệnh validation này nằm ngoài try/except release. Vì vậy input không hợp lệ hoặc thiếu capability để lại active solve dù start thất bại.

**Repro FakeBackend:** strategy không tồn tại → StrategyIncompatibleError và **active_lease=true**. Mutating tool tiếp theo bị CAPTCHA_BUSY tới khi cancel/expiry.

**Cần sửa:** validate trước acquire hoặc bao toàn bộ lifecycle sau acquire trong cleanup; test invalid strategy/missing capability không giữ lease và không có side effect.

### R09 — P2: docs/report công bố hỗ trợ và acceptance vượt evidence

**Code/docs:** `docs/verification/captcha/run-2/verification_report.md`, `docs/CAPTCHA.md`.

- Run-2 nói tất cả F01–F11 resolved và invariants đã đạt, nhưng R01–R08 tái hiện được trên revision hiện tại.
- Docs nói detection kiểm tra attached frames nhưng detect hiện chỉ run payload ở top document.
- Docs liệt kê operation `status`; tool chỉ nhận start/observe/act/cancel.
- Docs mô tả observe không click, nhưng observe vẫn gọi CheckboxSolver.solve_step có thể click lại.
- Một số test được dẫn không tồn tại, ví dụ `test_live_solve_turnstile_interstitial_wait`, `test_live_solve_recaptcha_grid`. Suite hiện không chứng minh provider-qualified/agent vision benchmark/Ubuntu soak.

**Cần sửa:** công bố đúng phạm vi fixture/experimental; map từng claim với test/evidence thực. Khi viết report mới giữ lịch sử run-2, ghi rõ claim nào đã bị review này bác bỏ.

## 4. F01–F11 status sau re-review

| Finding cũ | Kết luận hiện tại |
|---|---|
| F01 | Partial: bỏ double/generic click, còn cross-widget Verify (R03) |
| F02 | Partial: newsletter case đã hết, vẫn false pass (R04) |
| F03 | Partial: sai image_id/thiếu widget có thể chặn, nhưng new document cùng selector và dynamic challenge vẫn sai (R02) |
| F04 | Reverified fixed cho MCP stdio + SDK đang cài, local auto image |
| F05 | Partial: đã có attach/session, nhưng mapping vượt tab scope và adapters chưa dùng frame API (R01) |
| F06 | Partial: cộng scroll, nhưng crop/input dùng khác rect (R05) |
| F07 | Partial: happy-path dedupe, nhưng post-dispatch failure có thể replay (R07) |
| F08 | Reverified fixed cho repeated start tuần tự cùng candidate; chưa chứng minh toàn lifecycle/identity |
| F09 | Reverified fixed cho optional expected fields; chưa đóng mọi postcondition/race |
| F10 | Partial: transport flood fixed, solve deadline/call ceiling/cooldown còn thiếu (R06) |
| F11 | Reverified fixed cho injected inspection failure |

## 5. Công việc tiếp theo có thể giao trực tiếp

- [ ] Sửa R01: frame ancestry theo tab, tests hai tab/nested frame với marker độc lập.
- [ ] Sửa R02/R05: một observation identity + image transform xuyên suốt từ capture đến input; new document/changed image phải reject trước side effect.
- [ ] Sửa R03/R04: control và evidence chỉ thuộc selected widget; negative tests cross-widget và 403/expired token.
- [ ] Sửa R07: unknown outcome receipt và terminal dedupe; side-effect counter ở fault/concurrency tests.
- [ ] Sửa R06/R08: budget propagation và lease cleanup tất cả error paths.
- [ ] Sửa R09: docs/support matrix/report chỉ mô tả khả năng đã kiểm chứng.
- [ ] Chạy lại toàn suite và các negative cases mới; không chỉnh assertions để chấp nhận lỗi.
- [ ] Sau khi blocker hết: model vision thật qua MCP với nhiều seed/dynamic grids, oracle độc lập phía fixture server. Không hardcode đáp án hay đọc source/DOM chứa đáp án.
- [ ] Provider thật/Ubuntu qualification vẫn là gate riêng theo spec; nếu chưa có môi trường thì ghi chưa kiểm chứng, không đóng completed.

## 6. Bằng chứng

- `recheck.py` / `recheck-results.json`: local Chrome negative cases và các fault injections được gắn nhãn rõ.
- `mcp_roundtrip.py` / `mcp_result.json`: process MCP stdio thật và image content.
- `transport_faults.py` / `fault_results.json`: command deadline và inspection failure.
- `source-sha256.json`: fingerprints của source đã kiểm tra.

Các script ghi JSON bên cạnh script; sao chép sang thư mục run mới trước lần review sau để giữ evidence này. Chúng đo hành vi và không thay thế regression suite có assertions. Các case FakeBackend không được tính là Chrome-real; Chrome-real trong report cũng chỉ là local fixture, chưa phải live provider challenge.
