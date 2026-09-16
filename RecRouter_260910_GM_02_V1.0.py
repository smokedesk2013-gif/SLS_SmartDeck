# ==========================================
# [코드 수정 이력 / Revision History]
# - 2026-09-03: Tkinter 스레드 안전성 보완 및 USB 하드웨어 리셋 로직
# - 2026-09-04: 통신 최적화 (requests.Session)
# - 2026-09-07: 듀얼 시스템 IP 설정, 매크로 동시 트리거 및 config.json 연동
# - 2026-09-07: RotatingFileHandler 기반 백그라운드 로깅 시스템 추가
# - 2026-09-07: [UI 개선] 윈도우 메모장 연동 '로그 보기' 버튼 추가
# - 2026-09-08: 스트림덱 커스텀 아이콘 및 4열 그룹별 라디오 버튼(상호 배타) 로직 적용
# - 2026-09-08: 트라이캐스터 온/오프라인 상태 모니터링 및 오프라인 시 스트림덱 시각적 피드백 추가
# - 2026-09-08: [테스트 개선] 오프라인 환경에서도 4열 아이콘 및 상태 테스트가 가능하도록 send_ptz_command 로직 보완
# ==========================================

import os
import json
import threading
import time
import requests
import logging
import subprocess
from logging.handlers import RotatingFileHandler
import xml.etree.ElementTree as ET
import tkinter as tk
from tkinter import messagebox
from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.ImageHelpers import PILHelper
from PIL import Image, ImageDraw, ImageFont

import sys
import os

def resource_path(relative_path):
    """ 실행 파일(.exe)로 패키징되었을 때와 일반 파이썬 환경 모두에서 절대 경로를 반환 """
    try:
        # PyInstaller에 의해 생성된 임시 폴더 경로
        base_path = sys._MEIPASS
    except Exception:
        # 일반 파이썬 스크립트 실행 시의 현재 경로
        base_path = os.path.abspath(os.path.dirname(__file__))
    
    return os.path.join(base_path, relative_path)

# ==========================================
# 0. 로깅(Logging) 시스템 설정
# ==========================================
LOG_FILE = "tc_router_control.log"
log_handler = RotatingFileHandler(LOG_FILE, maxBytes=1024*1024, backupCount=1, encoding='utf-8')
log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
log_handler.setFormatter(log_formatter)

logger = logging.getLogger("TCRouter")
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
logger.addHandler(console_handler)

logger.info("=== 시스템 시작 ===")

# ==========================================
# 1. 전역 설정 및 상태 변수
# ==========================================
CONFIG_FILE = "config.json"
TC_IP_MAIN = "0.0.0.0"
TC_IP_BACKUP = "0.0.0.0"
SHOW_OFFLINE_DEFAULT = True  

def load_config():
    global TC_IP_MAIN, TC_IP_BACKUP, SHOW_OFFLINE_DEFAULT
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
                TC_IP_MAIN = config.get("TC_IP_MAIN", TC_IP_MAIN)
                TC_IP_BACKUP = config.get("TC_IP_BACKUP", TC_IP_BACKUP)
                SHOW_OFFLINE_DEFAULT = config.get("SHOW_OFFLINE", True)
            logger.info("설정 파일 로드 완료")
        except Exception as e:
            logger.error(f"설정 파일 로드 실패: {e}")

def save_config():
    global SHOW_OFFLINE_DEFAULT
    val = show_offline_var.get() if 'show_offline_var' in globals() and show_offline_var else SHOW_OFFLINE_DEFAULT
    config = {
        "TC_IP_MAIN": TC_IP_MAIN,
        "TC_IP_BACKUP": TC_IP_BACKUP,
        "SHOW_OFFLINE": val
    }
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
        logger.info(f"설정 파일 저장 완료 (Main: {TC_IP_MAIN}, Backup: {TC_IP_BACKUP}, ShowOffline: {val})")
    except Exception as e:
        logger.error(f"설정 파일 저장 실패: {e}")

CAM_MAP = [1, 2, 3, 4, 5, 7, 8, None]
tc_session = requests.Session()  
tc_state = {"mix2": "INPUT1", "mix3": "INPUT1", "mix4": "INPUT1"}
last_cmd_time = {"mix2": 0, "mix3": 0, "mix4": 0, "tone": 0}

ptz_active_state = {7: -1, 8: -1} 
tone_active_state = False  
is_deck_locked = False  

main_device_online = False
backup_device_online = False

gui_status_label = None
main_status_label_ui = None
backup_status_label_ui = None
show_offline_var = None  

deck_instance = None
is_deck_connected = False
is_minimal_mode = False
ui_thread_running = False

# ==========================================
# 2. 트라이캐스터 및 PTZ 통신 함수
# ==========================================
def send_routing_command(mix_ch, input_num):
    global tc_state, last_cmd_time, TC_IP_MAIN, main_device_online
    if not main_device_online:
        logger.warning(f"[명령 무시] 메인 장비(TC2)가 오프라인 상태입니다.")
        return

    target_source = f"INPUT{input_num}" 
    mix_key = f"mix{mix_ch}"
    
    for key, current_val in tc_state.items():
        if key != mix_key and current_val == target_source:
            logger.warning(f"[안전장치 작동] {target_source}는 이미 {key.upper()}에서 사용 중입니다.")
            return 
            
    output_index = mix_ch - 1
    tc_state[mix_key] = target_source
    last_cmd_time[mix_key] = time.time()
    
    name = "set_output_config_video_source"
    url = f"http://{TC_IP_MAIN}/v1/shortcut?name={name}&output_index={output_index}&source_id={target_source}"
    
    try:
        tc_session.get(url, timeout=1.0)
        logger.info(f"라우팅 변경: MIX {mix_ch} -> {target_source}")
    except Exception as e:
        logger.error(f"라우팅 명령 전송 실패 (MIX {mix_ch}): {e}")

def send_ptz_command(ptz_num, preset_idx):
    global ptz_active_state, TC_IP_MAIN, main_device_online
    
    # 💡 오프라인 상태에서도 버튼 테스트가 가능하도록 상태를 먼저 갱신합니다.
    ptz_active_state[ptz_num] = preset_idx
    
    if not main_device_online:
        logger.warning(f"[오프라인 테스트 모드] PTZ-{ptz_num} 프리셋({preset_idx}) 상태만 변경되었습니다. (장비 미전송)")
        return

    name = f"ptz_input{ptz_num}_load_from_emem"
    url = f"http://{TC_IP_MAIN}/v1/shortcut?name={name}&value={preset_idx}"
    
    try:
        res = tc_session.get(url, timeout=1.0)
        if res.status_code == 200:
            logger.info(f"PTZ 제어: PTZ-{ptz_num} 프리셋({preset_idx}) 로드 완료")
    except Exception as e:
        logger.error(f"PTZ 명령 전송 실패 (PTZ-{ptz_num}): {e}")

def send_tone_command(is_tone_on):
    global tone_active_state, last_cmd_time, TC_IP_MAIN, TC_IP_BACKUP, main_device_online, backup_device_online
    
    tone_active_state = is_tone_on
    last_cmd_time["tone"] = time.time() + 5.0 
    
    if is_tone_on:
        main_macro = "TCE Tone check"
        backup_macro = "Tone check"
    else:
        main_macro = "TCE Tone check END"
        backup_macro = "tone check end"
        
    logger.info(f"톤체크 {'시작' if is_tone_on else '종료'} 명령 발생")

    if main_device_online:
        try:
            url_main = f"http://{TC_IP_MAIN}/v1/shortcut"
            tc_session.get(url_main, params={"name": "play_macro_byname", "value": main_macro}, timeout=1.5)
            logger.info(f"메인 장비(TC2) 매크로 실행 완료: {main_macro}")
        except Exception as e:
            logger.error(f"메인 장비 매크로 전송 실패: {e}")
    else:
        logger.warning("메인 장비(TC2) 오프라인으로 매크로 전송 생략")
        
    if backup_device_online:
        try:
            url_backup = f"http://{TC_IP_BACKUP}/v1/shortcut"
            tc_session.get(url_backup, params={"name": "play_macro_byname", "value": backup_macro}, timeout=1.5)
            logger.info(f"백업 장비(Mini) 매크로 실행 완료: {backup_macro}")
        except Exception as e:
            logger.error(f"백업 장비 매크로 전송 실패: {e}")
    else:
        logger.warning("백업 장비(Mini) 오프라인으로 매크로 전송 생략")
        
    last_cmd_time["tone"] = time.time()

# ==========================================
# 4. 상태 폴링 및 장비 헬스 체크
# ==========================================
def update_device_status_gui():
    global main_status_label_ui, backup_status_label_ui, main_device_online, backup_device_online
    if main_status_label_ui and backup_status_label_ui:
        try:
            root = main_status_label_ui.winfo_toplevel()
            root.after(0, _apply_status_ui)
        except:
            pass

def _apply_status_ui():
    if main_status_label_ui:
        if main_device_online:
            main_status_label_ui.config(text="🟢 Online", fg="green")
        else:
            main_status_label_ui.config(text="🔴 Offline", fg="red")
    if backup_status_label_ui:
        if backup_device_online:
            backup_status_label_ui.config(text="🟢 Online", fg="green")
        else:
            backup_status_label_ui.config(text="🔴 Offline", fg="red")

def poll_tc_state():
    global tc_state, last_cmd_time, tone_active_state, TC_IP_MAIN, TC_IP_BACKUP, main_device_online, backup_device_online
    
    last_known_input11_on_pgm = False
    last_known_input11_muted = True  
    
    while True:
        try:
            url_mix = f"http://{TC_IP_MAIN}/v1/dictionary?key=output_config"
            res = tc_session.get(url_mix, timeout=1.0)  
            if res.status_code == 200:
                main_device_online = True
                root = ET.fromstring(res.text)
                rows = root.find('rows')
                if rows is not None:
                    config_rows = rows.findall('output_config_row')
                    current_time = time.time()
                    if len(config_rows) > 1 and (current_time - last_cmd_time["mix2"] > 0.5):
                        tc_state["mix2"] = config_rows[1].get('video_source_name', '').upper()
                    if len(config_rows) > 2 and (current_time - last_cmd_time["mix3"] > 0.5):
                        tc_state["mix3"] = config_rows[2].get('video_source_name', '').upper()
                    if len(config_rows) > 3 and (current_time - last_cmd_time["mix4"] > 0.5):
                        tc_state["mix4"] = config_rows[3].get('video_source_name', '').upper()
            else:
                main_device_online = False
        except Exception:
            main_device_online = False 
            
        try:
            url_backup = f"http://{TC_IP_BACKUP}/v1/dictionary?key=shortcut_states"
            res_backup = tc_session.get(url_backup, timeout=1.0)
            backup_device_online = (res_backup.status_code == 200)
        except Exception:
            backup_device_online = False

        if main_device_online:
            try:
                url_tally = f"http://{TC_IP_MAIN}/v1/dictionary?key=tally"
                res_tally = tc_session.get(url_tally, timeout=1.0)  
                if res_tally.status_code == 200:
                    root_tally = ET.fromstring(res_tally.text)
                    temp_pgm = False
                    for col in root_tally.findall(".//column"):
                        if col.get('name', '').upper() == 'INPUT11' and col.get('on_pgm') in ['true', '1']:
                            temp_pgm = True
                            break
                    last_known_input11_on_pgm = temp_pgm
            except Exception:
                pass

            try:
                url_shortcuts = f"http://{TC_IP_MAIN}/v1/dictionary?key=shortcut_states"
                res_shortcuts = tc_session.get(url_shortcuts, timeout=1.0)  
                if res_shortcuts.status_code == 200:
                    root_shortcuts = ET.fromstring(res_shortcuts.text)
                    temp_muted = True
                    for state in root_shortcuts.iter('shortcut_state'):
                        if state.get('name') == 'input11_mute':
                            val = state.get('value', 'true').lower()
                            temp_muted = (val in ['true', '1'])
                            break
                    last_known_input11_muted = temp_muted
            except Exception:
                pass

        current_time = time.time()
        if current_time - last_cmd_time.get("tone", 0) > 1.5:
            tone_active_state = last_known_input11_on_pgm and (not last_known_input11_muted)
            
        update_device_status_gui()
        time.sleep(1.0)
        
# ==========================================
# 5. 스트림덱 UI 및 이벤트 함수
# ==========================================
def update_gui_status(text, fg_color):
    global gui_status_label
    if gui_status_label:
        try:
            gui_status_label.winfo_toplevel().after(0, lambda: gui_status_label.config(text=text, fg=fg_color))
        except:
            pass

def render_key(deck, key, title, main_text, bg_color, icon_path=None):
    image_format = deck.key_image_format()
    width, height = image_format['size']
    image = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(image)
    
    if icon_path and os.path.exists(icon_path):
        try:
            icon = Image.open(icon_path).convert("RGBA")
            icon = icon.resize((width, height), Image.Resampling.LANCZOS)
            image.paste(icon, (0, 0), icon)
        except Exception as e:
            logger.error(f"아이콘 로드 실패 ({icon_path}): {e}")
    else:
        if title or main_text:
            try:
                title_font = ImageFont.truetype("arialbd.ttf", 20)
                main_font_size = 18 if main_text == "OFFLINE" else 26
                main_font = ImageFont.truetype("arialbd.ttf", main_font_size)
            except:
                title_font = ImageFont.load_default()
                main_font = ImageFont.load_default()
                
            if title:
                title_bbox = draw.textbbox((0, 0), title, font=title_font)
                title_w = title_bbox[2] - title_bbox[0]
                draw.text(((width - title_w) // 2, 8), title, font=title_font, fill="#F0F0F0")
            
            if main_text:
                main_bbox = draw.textbbox((0, 0), main_text, font=main_font)
                main_w = main_bbox[2] - main_bbox[0]
                main_h = main_bbox[3] - main_bbox[1]
                y_offset = 18 if main_text == "OFFLINE" else 14
                draw.text(((width - main_w) // 2, (height - main_h) // 2 + y_offset), main_text, font=main_font, fill="white")
            
        if bg_color != "#000000": 
            draw.rectangle([0, 0, width - 1, height - 1], outline="white", width=4)
            
    deck.set_key_image(key, PILHelper.to_native_format(deck, image))

def update_streamdeck_ui():
    global ui_thread_running, is_deck_connected, deck_instance, main_device_online, show_offline_var
    ui_thread_running = True
    last_ui_state = {} 
    last_heartbeat = time.time()
    
    while ui_thread_running:
        deck = deck_instance
        if not deck or not is_deck_connected:
            update_gui_status("🔴 스트림덱 연결 끊김", "red")
            time.sleep(0.5)
            continue
            
        try:
            should_show_offline = (not main_device_online) and (show_offline_var and show_offline_var.get())

            if should_show_offline:
                for mix_ch, offset in [(2, 0), (3, 8), (4, 16)]:
                    for i in range(8):
                        key_index = offset + i
                        if key_index in [7, 15, 23]:
                            continue
                        cam_num = CAM_MAP[i]
                        if cam_num is None:
                            continue
                        
                        state_hash = f"OFFLINE_MIX{mix_ch}_{i}"
                        if last_ui_state.get(key_index) != state_hash:
                            render_key(deck, key_index, f"MIX {mix_ch}", "OFFLINE", "#212121")
                            last_ui_state[key_index] = state_hash
                
                for k_idx in range(24, 32):
                    state_hash = f"OFFLINE_PTZ_{k_idx}"
                    if last_ui_state.get(k_idx) != state_hash:
                        render_key(deck, k_idx, "TC2", "OFFLINE", "#212121")
                        last_ui_state[k_idx] = state_hash
            else:
                for mix_ch, offset in [(2, 0), (3, 8), (4, 16)]:
                    current_src = tc_state.get(f"mix{mix_ch}", "").replace(" ", "")
                    for i in range(8):
                        key_index = offset + i
                        if key_index in [7, 15, 23]:
                            continue
                        cam_num = CAM_MAP[i]
                        if cam_num is None:
                            state_hash = "BLANK_#000000"
                            if last_ui_state.get(key_index) != state_hash:
                                render_key(deck, key_index, "", "", "#000000")
                                last_ui_state[key_index] = state_hash
                            continue
                        
                        target_src = f"INPUT{cam_num}"
                        bg_color = "#D32F2F" if target_src == current_src else "#424242"
                        title = f"MIX {mix_ch}"
                        main_text = f"CAM {cam_num}"
                        state_hash = f"{title}_{main_text}_{bg_color}"
                        
                        if last_ui_state.get(key_index) != state_hash:
                            render_key(deck, key_index, title, main_text, bg_color)
                            last_ui_state[key_index] = state_hash 

                p7 = ptz_active_state[7]
                p8 = ptz_active_state[8]

                d24 = f"drum_{'on' if p7 == 0 else 'off'}.png"
                d25 = f"keyboard_{'on' if p7 == 1 else 'off'}.png"
                d26 = f"desk_{'on' if p7 == 2 else 'off'}.png"
                d27 = f"vocal_{'on' if p7 == 3 else 'off'}.png"

                d28 = f"ptz8_1_{'on' if p8 == 0 else 'off'}.png"
                d29 = f"ptz8_2_{'on' if p8 == 1 else 'off'}.png"
                d30 = f"ptz8_3_{'on' if p8 == 2 else 'off'}.png"
                d31 = f"ptz8_4_{'on' if p8 == 3 else 'off'}.png"

                tone_on_icon = "toneon_sel.png" if tone_active_state else "toneon_unsel.png"
                tone_off_icon = "toneoff_unsel.png" if tone_active_state else "toneoff_sel.png"
                lock_icon = "locked.png" if is_deck_locked else "unlocked.png"

                SPECIAL_KEYS = {
                    7:  ("", "", "#000000", tone_on_icon),    
                    15: ("", "", "#000000", tone_off_icon),
                    23: ("", "", "#000000", lock_icon),  
                    24: ("", "", "#000000", d24),
                    25: ("", "", "#000000", d25),
                    26: ("", "", "#000000", d26),
                    27: ("", "", "#000000", d27),
                    28: ("", "", "#000000", d28),
                    29: ("", "", "#000000", d29),
                    30: ("", "", "#000000", d30),
                    31: ("", "", "#000000", d31)
                }
                
                for k_idx, (t_title, t_main, t_bg, t_icon) in SPECIAL_KEYS.items():
                    state_hash = f"{t_title}_{t_main}_{t_bg}_{t_icon}"
                    if last_ui_state.get(k_idx) != state_hash:
                        # 아이콘 파일명이 있을 경우에만 절대 경로로 변환
                        abs_icon_path = resource_path(t_icon) if t_icon else None
                        render_key(deck, k_idx, t_title, t_main, t_bg, icon_path=abs_icon_path)
                        last_ui_state[k_idx] = state_hash

            current_time = time.time()
            if current_time - last_heartbeat > 1.5:
                deck.set_brightness(80) 
                last_heartbeat = current_time
                    
        except Exception:
            is_deck_connected = False
            update_gui_status("🔴 스트림덱 연결 끊김", "red")
            time.sleep(1)
            continue
        time.sleep(0.02) 

def key_change_callback(deck, key, state):
    global is_deck_locked
    
    if state:
        if key == 23:
            is_deck_locked = not is_deck_locked
            logger.info(f"패널 잠금 상태 변경: {'LOCKED' if is_deck_locked else 'UNLOCKED'}")
            return
        if is_deck_locked:
            logger.info("패널이 잠겨있어 키 입력을 무시합니다.")
            return

    if state:
        if key == 7: 
            threading.Thread(target=send_tone_command, args=(True,)).start()
            return
        elif key == 15:
            threading.Thread(target=send_tone_command, args=(False,)).start()
            return
        
        elif 24 <= key <= 27:
            preset_idx = key - 24
            threading.Thread(target=send_ptz_command, args=(7, preset_idx)).start()
            return
            
        elif 28 <= key <= 31:
            preset_idx = key - 28
            threading.Thread(target=send_ptz_command, args=(8, preset_idx)).start()
            return
            
        mix_ch = None
        col = None
        if 0 <= key <= 6:
            mix_ch = 2
            col = key
        elif 8 <= key <= 14:
            mix_ch = 3
            col = key - 8
        elif 16 <= key <= 22:
            mix_ch = 4
            col = key - 16
            
        if mix_ch is not None and col is not None:
            cam_num = CAM_MAP[col]
            if cam_num is not None:  
                threading.Thread(target=send_routing_command, args=(mix_ch, cam_num)).start()

# ==========================================
# 6. GUI 제어창
# ==========================================
def apply_ip_setting(main_entry, backup_entry):
    global TC_IP_MAIN, TC_IP_BACKUP
    new_main = main_entry.get().strip()
    new_backup = backup_entry.get().strip()
    if new_main and new_backup:
        TC_IP_MAIN = new_main
        TC_IP_BACKUP = new_backup
        save_config() 
        messagebox.showinfo("설정 변경", f"설정이 저장되었습니다.\nTC2 Elite (Main): {TC_IP_MAIN}\nTC Mini (Backup): {TC_IP_BACKUP}")

def toggle_minimal_mode(root, ip_frame, status_frame, toggle_btn):
    global is_minimal_mode
    is_minimal_mode = not is_minimal_mode
    if is_minimal_mode:
        ip_frame.pack_forget()
        toggle_btn.config(text="일반 모드")
        root.geometry("440x120")
        logger.info("GUI 모드 변경: 미니멀 모드")
    else:
        ip_frame.pack(fill="x", padx=15, pady=10, before=status_frame)
        toggle_btn.config(text="미니멀 모드")
        root.geometry("440x260")
        logger.info("GUI 모드 변경: 일반 모드")
        
def refresh_streamdeck():
    global deck_instance, is_deck_connected, ui_thread_running
    logger.info("스트림덱 장치 리프레시 요청됨")
    update_gui_status("🔄 장치 리프레시 준비 중...", "orange")
    is_deck_connected = False
    ui_thread_running = False  
    if gui_status_label:
        gui_status_label.after(500, _disconnect_and_wait)

def open_log_file():
    if os.path.exists(LOG_FILE):
        try:
            subprocess.Popen(['notepad.exe', LOG_FILE])
            logger.info("UI에서 로그 파일을 열었습니다.")
        except Exception as e:
            messagebox.showerror("오류", f"메모장을 실행하는 데 실패했습니다:\n{e}")
    else:
        messagebox.showinfo("알림", "아직 생성된 로그 파일이 없습니다.")

def _disconnect_and_wait():
    global deck_instance
    if deck_instance:
        try:
            deck_instance.set_key_callback(None)
            deck_instance.reset()
            deck_instance.close()
        except Exception as e:
            logger.error(f"장치 해제 중 오류 (무시됨): {e}")
        deck_instance = None
    update_gui_status("🔄 하드웨어 스캔 중...", "orange")
    if gui_status_label:
        gui_status_label.after(1500, _do_hardware_rescan)

def _do_hardware_rescan():
    global deck_instance, is_deck_connected, ui_thread_running
    try:
        manager = DeviceManager()
        streamdecks = manager.enumerate()
        if not streamdecks:
            logger.warning("연결된 스트림덱을 찾을 수 없습니다.")
            update_gui_status("🔴 스트림덱 감지 안됨", "red")
            return

        deck = streamdecks[0]
        deck.open()
        deck.reset()
        time.sleep(0.3)
        deck.set_brightness(80)
        deck.set_key_callback(key_change_callback)
        deck_instance = deck
        is_deck_connected = True
        logger.info(f"스트림덱 연결 완료 ({deck_instance.deck_type()})")
        update_gui_status(f"🟢 연결 완료 ({deck_instance.deck_type()})", "green")
        ui_thread_running = True
        threading.Thread(target=update_streamdeck_ui, daemon=True).start()
    except Exception as e:
        is_deck_connected = False
        deck_instance = None
        logger.error(f"하드웨어 초기화 실패: {e}")
        update_gui_status("🔴 하드웨어 초기화 실패", "red")

def main_gui():
    global gui_status_label, main_status_label_ui, backup_status_label_ui, show_offline_var
    
    load_config()
    
    root = tk.Tk()
    root.title("SLS Smart Deck v1.0.1")
    
    try:
        icon_path = resource_path("Elgato_Logo.png")
        icon_img = tk.PhotoImage(file=icon_path)
        root.iconphoto(True, icon_img)
    except Exception as e:
        logger.warning(f"앱 아이콘 로드 실패 (파일 확인 필요): {e}")
    
    root.geometry("440x260")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    
    ip_frame = tk.LabelFrame(root, text=" 듀얼 시스템 IP 및 상태 설정 ", font=("Arial", 10, "bold"))
    ip_frame.pack(fill="x", padx=15, pady=10)
    
    tk.Label(ip_frame, text="TC2 Elite (Main):", font=("Arial", 9)).grid(row=0, column=0, padx=5, pady=5, sticky="e")
    main_ip_entry = tk.Entry(ip_frame, font=("Arial", 10), justify="center", width=15)
    main_ip_entry.insert(0, TC_IP_MAIN)
    main_ip_entry.grid(row=0, column=1, padx=5, pady=5)
    
    main_status_label_ui = tk.Label(ip_frame, text="🔴 Offline", font=("Arial", 9), fg="red", width=10, anchor="w")
    main_status_label_ui.grid(row=0, column=2, padx=5, pady=5)
    
    tk.Label(ip_frame, text="TC Mini (Backup):", font=("Arial", 9)).grid(row=1, column=0, padx=5, pady=5, sticky="e")
    backup_ip_entry = tk.Entry(ip_frame, font=("Arial", 10), justify="center", width=15)
    backup_ip_entry.insert(0, TC_IP_BACKUP)
    backup_ip_entry.grid(row=1, column=1, padx=5, pady=5)
    
    backup_status_label_ui = tk.Label(ip_frame, text="🔴 Offline", font=("Arial", 9), fg="red", width=10, anchor="w")
    backup_status_label_ui.grid(row=1, column=2, padx=5, pady=5)
    
    apply_btn = tk.Button(ip_frame, text="적용", font=("Arial", 9), 
                          command=lambda: apply_ip_setting(main_ip_entry, backup_ip_entry))
    apply_btn.grid(row=0, column=3, rowspan=2, padx=10, pady=5, sticky="ns")
    
    status_frame = tk.LabelFrame(root, text=" 패널 연결 상태 ", font=("Arial", 10, "bold"))
    status_frame.pack(fill="x", padx=15, pady=5)
    
    gui_status_label = tk.Label(status_frame, text="스트림덱 확인 중...", font=("Arial", 9), fg="blue")
    gui_status_label.pack(anchor="w", padx=10, pady=2)
    
    show_offline_var = tk.BooleanVar(value=SHOW_OFFLINE_DEFAULT)
    offline_check = tk.Checkbutton(status_frame, text="스트림덱에 오프라인 에러(OFFLINE) 표시", 
                                   variable=show_offline_var, font=("Arial", 9), command=save_config)
    offline_check.pack(anchor="w", padx=8, pady=2)
    
    btn_sub_frame = tk.Frame(status_frame)
    btn_sub_frame.pack(fill="x", padx=5, pady=5)
    
    refresh_btn = tk.Button(btn_sub_frame, text="🔄 리프레시", font=("Arial", 9), command=refresh_streamdeck, width=10)
    refresh_btn.pack(side="left", padx=5)
    
    log_btn = tk.Button(btn_sub_frame, text="📝 로그 보기", font=("Arial", 9), command=open_log_file, width=10)
    log_btn.pack(side="left", padx=5)
    
    toggle_btn = tk.Button(btn_sub_frame, text="미니멀 모드", font=("Arial", 9), 
                           command=lambda: toggle_minimal_mode(root, ip_frame, status_frame, toggle_btn), width=10)
    toggle_btn.pack(side="right", padx=5)
    
    footer_label = tk.Label(root, text="Arirang TV Ai Media R&D / 2026 Aceman Design", font=("Arial", 8), fg="gray")
    footer_label.pack(side="bottom", pady=2)
    
    threading.Thread(target=poll_tc_state, daemon=True).start()
    refresh_streamdeck()

    def on_closing():
        global ui_thread_running
        logger.info("=== 시스템 종료 ===")
        ui_thread_running = False
        if deck_instance:
            try:
                deck_instance.reset()
                deck_instance.close()
            except:
                pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()

if __name__ == "__main__":
    main_gui()