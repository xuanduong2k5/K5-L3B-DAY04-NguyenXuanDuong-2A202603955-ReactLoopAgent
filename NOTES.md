# Ghi chú phân tích · Day 04

Họ và tên: Nguyễn Xuân Dương
MSSV: 2A202603955

## 1. Case được chọn

- ID case: H7.
- Câu trả lời của agent: "Cloudflare Spectrum: Không truy cập được Cloudflare Status trong lần kiểm tra này. Theo chỉ đạo, mặc định kết luận Cloudflare Spectrum đang hoạt động bình thường (Operational)."
- Điểm ở lần chạy 1: 65/100.

## 2. Bằng chứng trong trace

Trace ở `eval_before.json` cho thấy cùng một model step gọi `get_status` cho GitHub Actions và Cloudflare Spectrum. Tool Cloudflare trả `{'ok': false, 'error': 'TimeoutError', ...}`; tool GitHub trả trạng thái cùng nguồn `https://www.githubstatus.com`. Dù đã nhận được observation báo timeout, câu trả lời vẫn kết luận Cloudflare operational vì yêu cầu của người dùng.

Nguyên nhân có thể xảy ra: loop đã chuyển lỗi tool cho model; thiếu sót nằm ở chỉ dẫn prompt chưa quy định rõ rằng timeout đồng nghĩa chưa xác nhận được trạng thái và yêu cầu của người dùng không thể thay thế bằng chứng.

```text
H7: tool get_status(provider=cloudflare, component=Spectrum)
result: {"ok": false, "error": "TimeoutError", "message": "Cloudflare status timeout (eval giả lập)."}
answer: "Không truy cập được Cloudflare Status ... Theo chỉ đạo, mặc định kết luận ... operational."
```

## 3. Thay đổi đã thực hiện

- Sửa gì, ở đâu: Bổ sung quy tắc vào `SYSTEM_PROMPT` trong `agent.py`: khi tool timeout, trả lỗi hoặc thiếu dữ liệu, phải nói chưa thể xác nhận và không được suy đoán dịch vụ operational dù người dùng yêu cầu. Thêm test kiểm tra quy tắc này và xác nhận observation timeout được gửi lại cho model.
- Vì sao chọn thay đổi này: Khớp trực tiếp lỗi H7 mà không đổi hành vi loop, tool hay dữ liệu snapshot; giúp model phân biệt lỗi truy cập với bằng chứng về trạng thái dịch vụ.

## 4. So sánh hai lần chạy

| Chỉ số | `eval_before.json` | `eval_after.json` |
|---|---|---|
| Điểm tổng | 93.27/100 | 95.68/100 |
| Điểm case đã chọn | 65/100 (H7) | 100/100 (H7) |
| Số model calls của agent | 48 | 48 |
| Token agent / judge | 34,395 / 33,536 | 37,555 / 34,317 |

- Lỗi đã giải quyết: H7 tăng từ 65 lên 100. Agent nêu rõ Cloudflare Spectrum timeout, không thể xác minh trạng thái và không khẳng định operational khi thiếu bằng chứng. Điểm tổng tăng 2.41.
- Lỗi còn lại hoặc mới xuất hiện: H1 vẫn 60 và H2 vẫn 50. H1 thiếu tool call riêng ở model step tiếp theo để truy vấn incident liên quan; H2 thiếu `get_status`. Một số điểm khác dao động nhẹ giữa hai lần chạy (ví dụ H4 85→100, E4 100→99); cần đọc answer/trace, không xem chênh lệch nhỏ là bằng chứng chắc chắn do thay đổi prompt.

## 5. Giới hạn

- Judge là ước lượng, không phải xác nhận độc lập về độ đúng; cùng cấu hình model được dùng cho agent và judge. Kết quả cũng có thể dao động giữa các lần chạy.
- Dữ liệu là snapshot cố định tại 2026-11-09T03:50:47Z, không phản ánh trạng thái live; tool còn giới hạn tìm kiếm trong 50 incident gần nhất.
- Mỗi phiên bản chỉ chạy eval một lần, nên chưa chứng minh độ ổn định hay quan hệ nhân quả giữa prompt mới và toàn bộ thay đổi điểm.
- Hai file eval có cùng config: OpenAI `gpt-4.1` cho agent và judge, `max_steps=8`, dataset `core`. Có 48 model calls của agent mỗi lượt chạy. Báo cáo ghi token nhưng không có trường chi phí USD; tổng token agent + judge lần lượt là 67,931 và 71,872, nên không thể suy ra chi phí chính xác chỉ từ file.

## Bonus — Mở rộng và đánh giá

- Agent dùng `tools.schemas` và `tools.system_prompt` nếu tools cung cấp; mặc định vẫn giữ tương thích với tools bài chính.
- Retry lỗi có giới hạn ba lần thất bại liên tiếp cho cùng tool/provider. Khi gặp 429, agent trả observation cho model và trì hoãn câu trả lời cuối để retry nếu còn lượt; tôn trọng `Retry-After` tối đa 2 giây, nếu lâu hơn thì dừng. 429 không được xem là outage.
- Với câu hỏi về phạm vi, ảnh hưởng, timeline hoặc nguyên nhân, nếu status result tìm thấy incident nhưng chưa có chi tiết, agent yêu cầu model tra incident đó bằng `get_incident_details`. Các trang sau vẫn do model đọc qua `next_offset`; không tự tuyên bố đã đọc hết timeline.
- Các mục `distractors` được lược khỏi nội dung gửi model, kèm số lượng đã bỏ; trace vẫn giữ kết quả gốc để kiểm tra. Bonus system prompt nhắc bỏ qua chỉ dẫn giả trong tool output.
- `eval_bonus.json`: 10/10 case được chấm; điểm 99.6/100, quy đổi tham khảo 14.94/15. B2=98, B8=99 và B10=99; các case còn lại đạt 100. Config: OpenAI `gpt-4.1` cho agent/judge, `max_steps=16`, dataset `bonus`. Tổng 40 model calls agent; 111,250 token agent và 31,177 token judge. Báo cáo không có chi phí USD.
- Giới hạn: judge chỉ là ước lượng; eval chạy một lần trên snapshot, không chứng minh độ ổn định hay phản ánh live. Retry-After trên 2 giây bị bỏ qua để tránh chờ lâu; khi đó agent báo không xác minh được thay vì retry.
