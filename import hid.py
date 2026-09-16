import hid

print("=== PC에 연결된 모든 USB HID 장치 스캔 ===")
devices = hid.enumerate()

found_elgato = False
for d in devices:
    vid = d['vendor_id']
    pid = d['product_id']
    product = d.get('product_string', 'Unknown')
    
    # 엘가토의 Vendor ID는 0x0FD9 입니다.
    if vid == 0x0FD9:
        print(f"🟢 [발견!] 엘가토 장치 - 제품명: {product} (VID: {vid:04X}, PID: {pid:04X})")
        found_elgato = True
    else:
        # 다른 일반 USB 장치들도 참고용으로 출력 (너무 많으면 주석 처리해도 됩니다)
        print(f"기타 장치 - 제품명: {product} (VID: {vid:04X}, PID: {pid:04X})")

if not found_elgato:
    print("\n❌ 파이썬이 엘가토 장치를 찾지 못했습니다. (권한 충돌 또는 dll 연결 문제)")