# Bàn giao: hoàn thiện CAPTCHA handling cho TabPilot

## Mục tiêu

Tiếp tục implementation để AI agent điều khiển Chrome có thể tự phát hiện, nhìn ảnh, thao tác và xác minh CAPTCHA trong workflow đã được giao. Sửa các lỗi đã tái hiện trước khi bổ sung solver mới hoặc công bố unattended readiness.

Đây là kế hoạch công việc tiếp theo, không phải xác nhận các lỗi đã được sửa.

## Đọc trước khi làm

1. [Spec và acceptance criteria](../../../superpowers/specs/2026-09-27-captcha-handling-design.md).
2. [Review: 7 lỗi P1, 4 lỗi P2](review.md).
3. [Bằng chứng Chrome/concurrency](results.json), [MCP stdio](mcp_result.json), [transport faults](fault_results.json).
4. AGENTS.md áp dụng và `/Users/ezesoft-mac-3/.codex/RTK.md`; mọi shell command phải có prefix `rtk`.

Repo: `/Users/ezesoft-mac-3/Developer/tabpilot-mcp`.

Khi tạo bàn giao, HEAD là `f194bc5` (`save`), working tree sạch trước khi thêm file này. Review trước đó thực hiện trên implementation chưa commit, khi HEAD còn `b70e330`. Dùng `source-sha256.json` để đối chiếu source đã review; không suy ra việc commit mới nghĩa là lỗi đã được sửa.

Baseline đã chạy trong review: **196 non-live + 53 live = 249 tests pass**. Live tests dùng CAPTCHA mô phỏng và đáp án biết trước, chưa phải agent vision/provider qualification. Không dùng số test xanh này làm bằng chứng hoàn thành.

## Nguyên tắc thực hiện

- Bắt đầu bằng kiểm tra trạng thái Git và source hiện tại; giữ các thay đổi của người dùng.
- Với mỗi finding: tái hiện → thêm regression test có assertion đúng → sửa → chạy lại test liên quan → ghi evidence.
- Không đổi expected result của test để chấp nhận lỗi hiện tại; không đánh dấu skip thành pass.
- Chỉ dùng Chrome profile riêng và fixture cục bộ cho các test submit/click/drag lỗi. Không chạy thử side effect trên tab nghiệp vụ.
- Giữ hai MCP tool `detect_captcha` / `solve_captcha`, base dependency chỉ MCP SDK, và error contract của repo.
- `needs_agent` là yêu cầu agent suy luận từ ảnh, không phải yêu cầu người giải hộ.
- Không thêm stealth, fingerprint spoofing, proxy rotation hoặc token injection.
- Chỉ nhấn control của challenge; không tự submit form nghiệp vụ để thử CAPTCHA.
- Không tự commit/push/deploy trong phạm vi sửa nếu chưa được yêu cầu. Không sửa hồ sơ review cũ để xoá bằng chứng lỗi; lưu kết quả mới riêng.

## Nhóm A — ngăn thao tác sai và thành công giả

### A1. F01: giới hạn Verify vào đúng challenge, chỉ dispatch một lần

- [ ] Bỏ fallback `button[type=submit]`/`input[type=submit]` toàn form/document.
- [ ] Verify chỉ nhận control đã bind với widget/observation hiện tại.
- [ ] Bỏ synthetic `btn.click()` chạy tiếp sau trusted click thành công.
- [ ] Khi không định vị được Verify: trả unsupported/observation mới, không đoán nút gần nhất.
- [ ] Test form nghiệp vụ có CAPTCHA: action Verify không submit form; control CAPTCHA hợp lệ nhận đúng một click.

File chính: `src/tabpilot/captcha_solvers/agent_vision.py`.

### A2. F02: xác minh đúng widget

- [ ] Bỏ global `[aria-checked="true"]` và marker của widget khác làm evidence pass.
- [ ] Bind response field, trạng thái UI, error/expiry với đúng widget/frame/document.
- [ ] Tách `widget_passed` và `access_verified`; không coi token DOM là business success.
- [ ] Test checkbox newsletter đã check, nhiều CAPTCHA cùng trang, token cũ, token widget khác, expired/error state: không báo pass giả.

File chính: `src/tabpilot/captcha_verify.py`, `src/tabpilot/js/captcha_detect.js`.

### A3. F03: chặn action dựa trên ảnh/document cũ

- [ ] Thay document generation dựa trên timestamp bằng identity/lifecycle thực.
- [ ] Trước input, kiểm tra document/frame/widget, geometry và challenge revision đang còn khớp observation.
- [ ] Validate `image_id`, `target_id`, normalized point/drag bounds, input/control binding và capability.
- [ ] Không cho `select_tile` click DOM id bất kỳ; không fallback text input ngoài challenge.
- [ ] Khi stale: không có side effect, trả observation mới. Mất kết nối sau dispatch: outcome unknown, không tự replay.
- [ ] Test navigate cùng URL/khác URL, frame recreate, grid thay ảnh, scroll/resize, sai image id, target ngoài whitelist.

File chính: `captcha.py`, `captcha_state.py`, `captcha_solvers/agent_vision.py`, backend lifecycle.

**Gate A:** không submit nghiệp vụ, không false-success trong negative suite, không click từ observation đã stale. Phần lifecycle của A3 phụ thuộc B1; chưa đóng A3 chỉ bằng so sánh chuỗi observation id.

## Nhóm B — hoàn thiện đường nhìn và thao tác Chrome thật

### B1. F05: OOPIF và frame routing

- [ ] Implement related-target attach/detach, session routing và context/frame lifecycle; xử lý nested OOPIF.
- [ ] Không bỏ lifecycle events trong `_command`; mapping phải invalidate khi navigation/reconnect.
- [ ] `list_frames` trả đúng child frame refs, target/session association.
- [ ] Adapter thực sự dùng frame API cho prompt/control/input bên trong iframe.
- [ ] Không đoán checkbox offset 28px hoặc chia mọi challenge thành grid 3×3 khi thiếu bằng chứng.
- [ ] Chrome test khác site, bật isolation bình thường; assert OOPIF target thực, frame discovery và evaluate đúng child/nested child.

File chính: `backends/base.py`, `backends/cdp.py`, frame payloads, solver adapters.

### B2. F04: `needs_agent` bắt buộc có ảnh qua MCP

- [ ] Trả image content cho `needs_agent` kể cả local `return_images=auto`.
- [ ] Không có SDK image support thì trả lỗi rõ ràng; không phát needs_agent chỉ có text.
- [ ] Test **stdio process thật**, assert nhận ImageContent hợp lệ; kiểm tra SDK được tuyên bố hỗ trợ.
- [ ] Giữ `agent_vision=False` đúng nghĩa: không yêu cầu client không có vision giải ảnh.

File chính: `captcha.py::_finish_step`, `server.py`, `_sdk.py` nếu cần.

### B3. F06: ánh xạ ảnh và input đúng sau scroll/zoom

- [ ] Tách screenshot clip ở page coordinates với input ở viewport CSS coordinates.
- [ ] Dùng kích thước ảnh/viewport thực; bỏ fallback giả định 800×600.
- [ ] Lưu crop/scale/scroll metadata và invalidate khi geometry thay đổi.
- [ ] Test nội dung ảnh crop thực, không chỉ PNG signature; cover scroll, DPR, zoom và nested frame.

File chính: `captcha_solvers/agent_vision.py`, helpers capture/geometry.

**Gate B:** agent nhận đúng ảnh và action đánh đúng control trên Chrome thật, bao gồm OOPIF và trang đã scroll.

## Nhóm C — lifecycle, retry và lỗi

### C1. F07: action dedupe nguyên tử

- [ ] Reservation/check/dispatch/receipt được đồng bộ theo solve/action.
- [ ] Hai caller đồng thời cùng action id/payload chỉ tạo một side effect; khác payload phải reject.
- [ ] Giữ terminal receipt trong TTL phù hợp sau khi release lease; không replay action có outcome unknown.
- [ ] Test hai thread/caller thật với barrier; đếm input side effects, không chỉ gọi tuần tự.

### C2. F08: start trùng trả solve đang tồn tại

- [ ] Sử dụng đúng kết quả `acquire_solve`; không tạo id mới rồi trả id không nằm trong active state.
- [ ] Start cùng candidate không chạy lại click; candidate khác trên tab đang leased phải báo busy.
- [ ] Resume bind từ solve id; tab/url được truyền thêm chỉ là assertion identity, không resolve lại tab đang focus.
- [ ] Test sequential/concurrent start, chuyển focus, cancel, expiry và reconnect.

### C3. F09: JSON-encode payload và sửa access verification

- [ ] Chuyển inline JS sang file payload; truyền options bằng JSON thay vì `repr`.
- [ ] Selector-only/text-only expected chạy được; không có `ReferenceError: None is not defined`.
- [ ] Verify URL từ browser hiện tại, không dùng snapshot `tab.url` cũ.
- [ ] Content predicates kết hợp đúng, ổn định trong deadline; interstitial phải thực sự hết blocking.

### C4. F10: deadline/budget có hiệu lực thực

- [ ] Bắt đầu deadline trước detect/baseline và mọi I/O.
- [ ] Khi remaining <=0 thì dừng; event flood không được kéo dài deadline qua `max(0.05, ...)`.
- [ ] Propagate remaining time qua HTTP/WS/frame/eval/capture/wait; bỏ timeout tối thiểu lớn hơn budget còn lại.
- [ ] Thực thi call ceiling, attempts/rounds/actions và cooldown; validate tool override như config.
- [ ] Test event flood, screenshot chậm, deadline hết giữa action, resume không tăng budget và start lại không reset cooldown.

### C5. F11: giữ inspection failure

- [ ] Start branch theo detection status/coverage; không coi candidates rỗng do lỗi là no_captcha.
- [ ] Test payload failure, partial frame inspection, transport disconnect và trang thực sự không có CAPTCHA.

**Gate C:** mỗi action có kết quả/receipt nhất quán, solve resumable đúng tab, hết budget thì dừng, lỗi không bị biến thành trạng thái thành công/không có CAPTCHA.

## Nhóm D — đóng khoảng trống với spec

- [ ] Detection: domain boundary thay vì src substring; nhiều widget cùng provider; group widget/field đúng; loại false positive `.grid-container`, badge, template ẩn.
- [ ] Chuyển checkbox → image challenge giữ đúng active solver/strategy và kiểm tra lại capability; không click checkbox lặp lại khi đang chờ.
- [ ] Guard mọi tool có thể scroll/focus/mutate trong lease; read-only thực vẫn dùng được; reset/close/cancel cleanup đầy đủ.
- [ ] OCR/audio/CV đang là placeholder: implement theo P4 với extras/model provisioning/tests, hoặc công bố chưa hỗ trợ và trả unsupported rõ ràng. Không quảng cáo `pip install` extra chưa khai báo là cách khắc phục.
- [ ] Cập nhật README, DESIGN và tạo `docs/CAPTCHA.md`; phân biệt fixture support, experimental adapter và provider đã qualification.
- [ ] Kiểm tra packaged wheel có JS payload, fresh base install không yêu cầu ML, không log raw token/cookie.

OCR/audio/CV không chặn qualification của loại đã giải được bằng agent vision. Tuy nhiên không đánh dấu toàn bộ P4 complete khi vẫn còn NotImplementedError.

## Chạy kiểm chứng

Từ repository root:

```sh
rtk proxy .venv/bin/python -m pytest -q -m 'not live'
rtk proxy .venv/bin/python -m pytest -q -m live
```

Live acceptance phải dùng Chrome profile riêng và xác nhận required tests không skip. Không khởi động Chrome chồng lên profile đang dùng của người dùng.

Các script chẩn đoán sẵn có:

```sh
rtk proxy .venv/bin/python docs/verification/captcha/2026-09-27-review/reproduce.py
rtk proxy .venv/bin/python docs/verification/captcha/2026-09-27-review/mcp_roundtrip.py
rtk proxy .venv/bin/python docs/verification/captcha/2026-09-27-review/transport_faults.py
```

Lưu ý: các script này đo lỗi cũ và ghi JSON bên cạnh script. Khi chạy sau sửa, giữ bản evidence cũ, dùng bản sao trong thư mục run mới hoặc điều chỉnh output destination. Một số script sẽ dừng khi behavior mới reject action cũ; đó không thay thế regression tests cần có assertion cụ thể.

## Nghiệm thu mục tiêu cuối

- [ ] F01–F11 có regression tests và evidence sửa xong.
- [ ] Agent/model thật nhận ảnh qua MCP stdio, tự chọn đáp án/action; không đọc đáp án từ fixture source/DOM hay dùng danh sách tile viết sẵn.
- [ ] Fixture grid tĩnh, grid động và text image đạt gate P3 trong spec; oracle phía test server độc lập với solver.
- [ ] Chỉ công bố provider/challenge đã đạt P5; ghi riêng passive pass, interactive success, blocked, timeout và unsupported. Không lấy test key luôn pass làm benchmark solver.
- [ ] Ubuntu Xvfb/headless và soak test chạy thực nếu công bố hỗ trợ unattended trên các mode đó.
- [ ] Thiếu môi trường/model/provider evidence thì ghi **chưa kiểm chứng**, không chuyển sang completed chỉ vì unit/live fixture pass.

## Bàn giao sau khi sửa

Tạo report mới trong `docs/verification/captcha/<run-id>/`, gồm revision, môi trường, commands/results, mapping F01–F11 → code/test/evidence, source fingerprints, số test pass/fail/skip, benchmark agent/provider và gate chưa đạt.

Kết luận phải trả lời riêng: **đã sửa lỗi nào**, **đã chạy tầng kiểm chứng nào**, và **đã đủ bằng chứng cho unattended ở provider/runtime nào**.
