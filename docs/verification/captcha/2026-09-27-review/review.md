# Review CAPTCHA implementation — 2026-09-27

**Kết luận: chưa đạt mục tiêu AI agent tự giải CAPTCHA unattended.** Có skeleton, hai tool và các luồng fixture hoạt động, nhưng còn lỗi có thể submit nhầm, báo pass giả hoặc hành động trên trang đã đổi. Không nghiệm thu P1/P2/P3 theo spec hiện tại.

Review trên working tree chưa commit, HEAD `b70e330`; source fingerprints trong `source-sha256.json`. Không sửa implementation. Môi trường kiểm tra: macOS, Python 3.14, Chrome 154.0.8037.57 headless, profile Chrome tạm riêng. Reproducer chỉ thao tác fixture cục bộ, không tạo giao dịch thật và không gọi provider solver trả phí.

## Kiểm thử đã chạy

| Tầng | Kết quả |
|---|---|
| `rtk proxy .venv/bin/python -m pytest -q -m 'not live'` | 196 passed, 53 deselected, 4.40s |
| CAPTCHA fixture live riêng | 6 passed, 14.95s; là subset của suite bên dưới, không cộng hai lần |
| `rtk proxy .venv/bin/python -m pytest -q -m live` với Chrome riêng trên 9222 | 53 passed, 196 deselected, 20.10s; không skip |
| MCP stdio process thật + Chrome thật | 19 tool; `needs_agent` chỉ trả text ở cấu hình mặc định local |
| Chrome fault/negative cases bổ sung | Tái hiện các vấn đề F01–F06, F08–F09 bên dưới |
| Hai caller đồng thời + FakeBackend có barrier | Cùng action id tạo 2 input side effects, F07 |
| Event flood giả lập / lỗi payload | Deadline không được thực thi cứng; inspection failure thành no_captcha |

**249 tests pass không đồng nghĩa acceptance của spec pass.** `tests/test_live_captcha.py` sử dụng DOM mô phỏng provider, chọn sẵn tile-0/4/8, nhập sẵn `K7X9`, kéo đến tọa độ biết trước. Không có model vision đọc ảnh để quyết định, provider real challenge, test-key Siteverify integration, OOPIF test thực trong suite, hay Ubuntu soak test. `tests/test_captcha_mcp.py` gọi server trong process với FakeBackend; đó không phải stdio/image roundtrip acceptance.

## Findings cần sửa

### F01 — P1: Verify có thể submit form nghiệp vụ hai lần

Vị trí: `src/tabpilot/captcha_solvers/agent_vision.py:300–332`.

Lookup chấp nhận `input[type=submit]` / `button[type=submit]` trong form hoặc cả document, không giới hạn control CAPTCHA. Sau `click_at`, code luôn chạy thêm `btn.click()` dù click đầu thành công. Fixture form có nút “Place order” và CAPTCHA text ghi nhận **submit_count=2** từ một action `verify`.

Sửa: bind control Verify của đúng observation/widget; thiếu control thì unsupported, không fallback submit toàn trang. Chỉ dispatch một lần, không synthetic-click tiếp sau trusted click. Nghiệm thu bằng event counter và form submit trap.

### F02 — P1: Checkbox không liên quan khiến solver báo thành công giả

Vị trí: `src/tabpilot/captcha_verify.py:44–61`.

`[aria-checked="true"]` và các success class được tìm trong container rộng hoặc toàn document. Trên trang có một reCAPTCHA chưa giải và checkbox newsletter đã check, start trả **widget_passed, success=true, attempts=0** dù token trống.

Sửa: evidence phải thuộc đúng widget/challenge/frame, adapter-specific; loại global checked selector và success marker của widget khác. Kiểm tra lỗi/expiry quan sát được trước khi chấp nhận token/UI state.

### F03 — P1: Action từ ảnh cũ vẫn click sau navigation, bỏ qua image_id

Vị trí: `src/tabpilot/captcha.py:354–371`; `src/tabpilot/captcha_solvers/agent_vision.py:232–243`.

Chỉ so observation id với state trong memory; không so document/frame/widget/ảnh/geometry hiện tại. `image_id` được parse nhưng không validate. Sau khi mở document khác cùng tab, gửi observation cũ và image id sai vẫn click nút không liên quan (**unrelated_clicks=1**), trả needs_agent.

Sửa: bind generation thực từ lifecycle, fingerprint observation/geometry và kiểm tra ngay trước action. Mismatch phải reject trước input và trả observation mới. Không dựa trên hash tab/url/current time như document identity.

### F04 — P1: Agent mặc định không nhận ảnh dù tool yêu cầu giải ảnh

Vị trí: `src/tabpilot/captcha.py:412–423`.

`_finish_step` phụ thuộc `config.wants_inline_image(None)` của screenshot thông thường. Local `return_images=auto` tắt inline; result không có ảnh hay đường dẫn ảnh. Qua MCP stdio thật: **content_types=["text"], status=needs_agent, is_error=false**.

Sửa: needs_agent phải có image content bất kể screenshot preference; thiếu SDK image support trả lỗi có giải pháp. Test phải assert ImageContent qua stdio, không chỉ parse JSON status.

### F05 — P1: FRAME_EVAL được advertise nhưng OOPIF chưa được khám phá/routing

Vị trí: `src/tabpilot/backends/cdp.py:330–355` và `_command:249–251`.

`list_frames` chỉ đi Page.getFrameTree; không attach related targets, không map session, không giữ lifecycle events. Trong Chrome bật site isolation, diagnostic Target.getTargets thấy iframe targets nhưng **list_frames chỉ trả root, child_refs=0, session_ids=[null]**. Solvers cũng đọc DOM bằng `eval_js` ở trang mẹ thay vì frame API.

Sửa: implement attach/detach/context lifecycle, nested OOPIF mapping, invalidation và route đúng frame; chỉ advertise capability sau real OOPIF acceptance. Tích hợp frame lookup vào adapter, không tiếp tục đoán checkbox offset 28px hay grid 3×3 khi không đọc được DOM.

### F06 — P1: Crop dùng viewport coordinates như page coordinates

Vị trí: `src/tabpilot/captcha_solvers/agent_vision.py:88–99`; `_inspect_challenge_layout:410–418`.

getBoundingClientRect trả viewport rect nhưng được truyền thẳng vào Page.captureScreenshot clip. Repro scrollY=727, widget viewport y=281: clip y vẫn 281 thay vì page y=1008. Ảnh gửi cho agent có thể là vùng khác trong khi point action lại dùng viewport geometry. Full viewport fallback còn giả định 800×600.

Sửa: tách crop page rect và input viewport rect, dùng kích thước ảnh/viewport thực, bảo toàn mapping và invalidation sau scroll/resize. Test ảnh crop có đúng nội dung, không chỉ kiểm tra có PNG bytes.

### F07 — P1: Dedupe action không nguyên tử

Vị trí: `src/tabpilot/captcha.py:344–376`.

Hai caller có thể cùng vượt receipt check trước khi receipt được ghi sau action. Reproducer dùng hai thread thật, barrier ngay trước handle_action để tạo interleaving hợp lệ: **cùng action_id/payload tạo 2 click_at**. Lock trong từng CDP command không bảo vệ toàn check-dispatch-receipt.

Sửa: lock/reservation theo solve/action, giữ trạng thái in-flight và completed/unknown; dedupe terminal receipt vẫn hoạt động sau lease release. Test đếm side effect với concurrent callers thay vì chỉ retry tuần tự.

### F08 — P2: Start lần hai trả solve id không tồn tại trong active state

Vị trí: `src/tabpilot/captcha.py:264–270`; `session.py:69–86`.

`acquire_solve` trả solve cũ cho cùng candidate, nhưng `_handle_start` bỏ qua giá trị trả về và chạy solve mới. Repro: id lần hai khác id active; gọi observe/act bằng id mới sẽ SOLVE_EXPIRED, và có thể click lại trong start lần hai.

Sửa: trả state/observation của solve đang tồn tại, không thực thi lại solver; acquire phải đồng bộ với caller đồng thời. Resume nên bind bằng solve_id, không resolve tab đang focus lại mỗi lần.

### F09 — P2: Expected hợp lệ bị lỗi JS khi thiếu optional field

Vị trí: `src/tabpilot/captcha_verify.py:166–168`.

`repr(None)` sinh `None` trong JavaScript. Expected chỉ có visible_selector, đúng schema, luôn thất bại với **ReferenceError: None is not defined** dù element hiển thị. Các inline payload khác cũng có pattern repr trên optional widget_ref.

Sửa: payload .js riêng + JSON-encode options; test selector-only/text-only/quotes/unicode trên Chrome thật. Đồng thời đọc URL thực tại lúc verify thay vì tab.url snapshot trước navigation.

### F10 — P2: Deadline/budget chưa bao trùm hoạt động

Vị trí: `src/tabpilot/backends/cdp.py:245–249`; `captcha.py:187–190,240–246`; `agent_vision.py:93`.

`max(0.05, deadline-now)` không bao giờ throw khi quá deadline nếu event cứ đến. Event flood giả lập: timeout 10ms vẫn trả success sau **102ms**. Solve bắt đầu deadline sau detect/baseline; nhiều bước dùng timeout 3/5/10 giây cố định, screenshot ép tối thiểu 3 giây. `captcha_call_timeout_ms` và cooldown chưa được tiêu thụ bởi orchestrator; giới hạn override ở tool không được validate như config.

Sửa: kiểm tra deadline <=0 trước mỗi read/step, propagate remaining budget; bắt đầu budget trước mọi I/O; thực thi call ceiling, cooldown và validate override. Không coi cấu hình tồn tại là tính năng đã hoạt động.

### F11 — P2: Inspection failure bị đổi thành no_captcha

Vị trí: `src/tabpilot/captcha.py:196–207`.

detect giữ inspection_failed, nhưng start chỉ kiểm tra candidates rỗng. Inject JSError trong captcha_detect rồi start trả **no_captcha**, khiến caller tưởng trang không có CAPTCHA trong khi kiểm tra đã lỗi.

Sửa: branch theo detection status/coverage trước khi chọn candidate; trả lỗi/inspection outcome có thể phục hồi, không biến thất bại I/O thành absent.

## Khoảng trống triển khai/acceptance còn lại

- OCR/audio/CV hiện chỉ import optional module rồi `raise NotImplementedError`; `pyproject.toml` chưa khai báo các extra được thông báo trong remedy. P4 chưa implement.
- Detection vẫn dùng src substring, gộp nhiều widget cùng provider thành phần tử đầu, generic `.grid-container` là reCAPTCHA, thiếu frame coverage và trạng thái uncertain thực. Cần regression theo spec trước khi auto-select đáng tin cậy.
- Action target_id có thể là bất kỳ DOM id qua `document.getElementById`, không bắt buộc thuộc obs.tiles; drag không validate normalized bounds/DRAG capability; type_answer có thể fallback input không liên quan và dùng synthetic value assignment. Các action cần whitelist/control binding cùng bản sửa F01/F03.
- Một số đường kiểm tra/solve nuốt lỗi transport; chưa bảo đảm outcome unknown và cleanup receipt/lease nhất quán. Sau provider transition, explicit strategy checkbox được chọn lại trên candidate đã thành image_grid và có thể STRATEGY_INCOMPATIBLE.
- Chưa có docs/CAPTCHA.md, provider-specific real evidence, agent vision benchmark nhiều seed, Ubuntu Xvfb/headless qualification hoặc soak test. README/DESIGN cần phân biệt rõ fixture simulation với provider support đã kiểm chứng.

## Bằng chứng và cách chạy lại

- `results.json`: Chrome fixture reproductions và concurrent duplicate action.
- `mcp_result.json`: MCP stdio + Chrome result.
- `fault_results.json`: event flood và inspection error.
- `source-sha256.json`: source snapshot fingerprint.

Từ repository root:

```sh
rtk proxy .venv/bin/python docs/verification/captcha/2026-09-27-review/reproduce.py
rtk proxy .venv/bin/python docs/verification/captcha/2026-09-27-review/mcp_roundtrip.py
rtk proxy .venv/bin/python docs/verification/captcha/2026-09-27-review/transport_faults.py
```

Repro scripts dùng Chrome macOS path, port/profile tạm và cập nhật JSON bên cạnh script. Chúng đo hành vi hiện tại, không thay thế regression assertions sau khi sửa. Concurrency test dùng FakeBackend có barrier; event flood dùng transport double. Các case còn lại được ghi là Chrome/MCP thật, nhưng vẫn trên fixture cục bộ.

Thứ tự sửa đề nghị: F01/F02/F03 → F04/F05/F06 → F07/F08 → F09/F10/F11. Sau đó thêm tests negative tương ứng, chạy model vision qua MCP trên challenge nhiều vòng, rồi mới đo provider thật. Chưa nên chốt completed hoặc bật unattended với browser nghiệp vụ hiện tại.
