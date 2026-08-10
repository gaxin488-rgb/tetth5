# UX/UI CineZero

## Luồng người dùng
1. Trang chủ → chọn phim nổi bật hoặc duyệt hàng phim.
2. Trang chi tiết → xem metadata, mô tả và lưu vào danh sách.
3. Trang xem → player 16:9, lưu tiến độ bằng localStorage.
4. Tìm kiếm → gợi ý ngay khi nhập, kết quả bằng hash route.

## Breakpoint
- Mobile: dưới 520 px, poster 2 cột.
- Tablet: dưới 820 px, menu chuyển sang dạng bật/tắt.
- Desktop: hàng phim cuộn ngang, hero toàn chiều rộng.

## Design system
- Nền: #090B10
- Surface: #121620
- Accent: #E5484D
- Poster: 2:3
- Backdrop/player: 16:9
- Bo góc chính: 14–18 px

## Nguyên tắc
- Không autoplay có âm thanh.
- Không phụ thuộc hover trên mobile.
- Có trạng thái rỗng và lỗi video.
- Hỗ trợ bàn phím, focus và prefers-reduced-motion.
- Ảnh dùng lazy loading; frontend không có dependency ngoài.
