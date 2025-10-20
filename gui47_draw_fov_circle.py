import pymem
import pymem.process
import math
import time
import threading
import tkinter as tk
from pynput import keyboard, mouse
import time
from tkinter import BooleanVar, Checkbutton, Scale
import time
import ctypes

# === CONFIG ===
CLIENT_DLL = "client.dll"
STD_SHADER_DLL = "stdshader_dx9.dll"

ENTITY_LIST_OFFSET = 0x04E2520C
ENTITY_STRIDE = 0x8
POS_X = 0x130
POS_Y = 0x134
POS_Z = 0x138
TEAM_OFFSET = 0xEC
CROUCH_OFFSET = 0x334

PITCH_BASE_OFFSET = 0x000EE59C
YAW_BASE_OFFSET = 0x000EE58C
PTR_OFFSET_1 = 0x10
PITCH_FINAL_OFFSET = 0x0
YAW_FINAL_OFFSET = 0x4

MAX_ENTITIES = 64
HEAD_OFFSET = 17.0
PITCH_CORRECTION = 0.3
PITCH_CORRECTION_2 = 0.1



# Modify shared dictionary to include timestamp
shared = {
    "my_team": 2,
    "recoil": 20,
    "local_index": 0,
    "entity_info": [],
    "running": True,
    "aim_active": False,
    "hotkey": "Button.right",
    "firing": False,
    "enemy_in_fov": False,
    "left_hold_start": None,
    "current_pitch_correction": 0.0,
    "correction_speed": 0.3,
    "bezier_t": 0.0,
    "bezier_speed": 0.05,  # Smaller = slower, adjust as needed
    "was_aiming": False,
    "current_pitch_correction": 0.0,  # in units of vertical offset, not angle
    "correction_speed_2": 0.05,  # units per second of vertical offset increase
    "last_target_ptr": None,
    "desired_fov": 4.0,
    "draw_fov_circle": False
}


import tkinter as tk
import math

def fov_circle_overlay(master):
    canvas = tk.Canvas(master, width=300, height=300, bg='black', highlightthickness=0)
    canvas.place(x=10, y=10)

    def draw_loop():
        if not shared["draw_fov_circle"] or not master.winfo_exists():
            canvas.delete("fov")
            canvas.after(100, draw_loop)
            return

        canvas.delete("fov")
        center_x, center_y = 150, 150
        radius = shared["desired_fov"] * 5  # scale for display
        canvas.create_oval(center_x - radius, center_y - radius,
                           center_x + radius, center_y + radius,
                           outline="lime", width=2, tags="fov")
        canvas.after(50, draw_loop)

    draw_loop()



    def draw_loop():
        if not shared["running"]:
            overlay.destroy()
            return
        canvas.delete("all")
        if shared.get("draw_fov_circle", False):
            fov = shared.get("desired_fov", 4.0)
            radius = int((fov / 90.0) * (screen_width // 4))

            x = screen_width // 2
            y = screen_height // 2
            canvas.create_oval(x - radius, y - radius, x + radius, y + radius, outline="white", width=1)

        overlay.after(16, draw_loop)  # ~60 FPS

    draw_loop()


    while shared["running"]:
        canvas.delete("all")
        if shared.get("draw_fov_circle", False):
            fov = shared.get("desired_fov", 4.0)
            radius = int((fov / 90.0) * (screen_width // 4))  # Rough scale for screen resolution

            x = screen_width // 2
            y = screen_height // 2
            canvas.create_oval(x - radius, y - radius, x + radius, y + radius, outline="white", width=1)

        overlay.update()
        time.sleep(0.016)  # ~60 FPS

    overlay.destroy()


def calc_angle(src, dst):
    dx, dy, dz = dst[0] - src[0], dst[1] - src[1], dst[2] - src[2]
    hyp = math.sqrt(dx * dx + dy * dy)
    pitch = -math.degrees(math.atan2(dz, hyp))
    yaw = math.degrees(math.atan2(dy, dx))
    return pitch, yaw

def angle_diff(a1, a2):
    dp = a1[0] - a2[0]
    dy = ((a1[1] - a2[1]) + 180) % 360 - 180
    return math.sqrt(dp**2 + dy**2)

def bezier_point(p0, p1, p2, p3, t):
    """Returns the interpolated value on a cubic bezier curve at time t."""
    u = 1 - t
    return (u**3)*p0 + 3*(u**2)*t*p1 + 3*u*(t**2)*p2 + (t**3)*p3

def bezier_smooth_angle(current, target, t):
    """Smoothly interpolate pitch and yaw using a Bezier curve."""
    pitch0 = current[0]
    yaw0 = current[1]
    pitch3 = target[0]
    yaw3 = target[1]

    # Intermediate control points could be tuned more dynamically
    pitch1 = pitch0 + (pitch3 - pitch0) * 0.25
    pitch2 = pitch0 + (pitch3 - pitch0) * 0.75
    yaw1 = yaw0 + ((yaw3 - yaw0 + 180) % 360 - 180) * 0.25
    yaw2 = yaw0 + ((yaw3 - yaw0 + 180) % 360 - 180) * 0.75

    smoothed_pitch = bezier_point(pitch0, pitch1, pitch2, pitch3, t)
    smoothed_yaw = bezier_point(yaw0, yaw1, yaw2, yaw3, t)

    return smoothed_pitch, smoothed_yaw


def update_entity_info(pm, entity_list):
    while shared["running"]:
        info = []
        for i in range(MAX_ENTITIES):
            try:
                ent_ptr = pm.read_uint(entity_list + i * ENTITY_STRIDE)
                if ent_ptr == 0:
                    continue
                x = pm.read_float(ent_ptr + POS_X)
                y = pm.read_float(ent_ptr + POS_Y)
                z = pm.read_float(ent_ptr + POS_Z)
                info.append((i, x, y, z))
            except:
                continue
        shared["entity_info"] = info
        time.sleep(0.5)


def aimbot_thread(pm, entity_list, pitch_addr, yaw_addr):
    cooldown_until = 0
    target_ptr = None
    prev_enemy_positions = {}

    while shared["running"]:
        current_time = time.time()

        if current_time < cooldown_until or not shared["aim_active"]:
            shared["enemy_in_fov"] = False
            shared["left_hold_start"] = None
            shared["current_pitch_correction"] = 0.0
            time.sleep(0.01)
            continue

        try:
            local_index = shared["local_index"]
            local_ptr = pm.read_uint(entity_list + local_index * ENTITY_STRIDE)
            if local_ptr == 0:
                shared["enemy_in_fov"] = False
                shared["left_hold_start"] = None
                shared["current_pitch_correction"] = 0.0
                time.sleep(0.01)
                continue

            lx = pm.read_float(local_ptr + POS_X)
            ly = pm.read_float(local_ptr + POS_Y)
            lz = pm.read_float(local_ptr + POS_Z)
            local_pos = (lx, ly, lz)

            cp = pm.read_float(pitch_addr)
            cy = pm.read_float(yaw_addr)

            best_angle = None
            best_fov = shared.get("desired_fov", 4.0)
            my_team = shared["my_team"]
            recoil = shared["recoil"]
            new_target_ptr = None

            for i in range(1, MAX_ENTITIES):
                try:
                    ent_ptr = pm.read_uint(entity_list + i * ENTITY_STRIDE)
                    if ent_ptr == 0:
                        continue
                    if pm.read_int(ent_ptr + TEAM_OFFSET) == my_team:
                        continue
                    health = pm.read_int(ent_ptr + 0xF8)
                    if health <= 0:
                        continue

                    ex = pm.read_float(ent_ptr + POS_X)
                    ey = pm.read_float(ent_ptr + POS_Y)
                    ez = pm.read_float(ent_ptr + POS_Z)

                    # Predict head height
                    crouch = pm.read_float(ent_ptr + CROUCH_OFFSET)
                    delta = max(0.0, min(1.0, (72.0 - crouch) / 18.0))
                    adjusted_z = ez - HEAD_OFFSET * delta

                    # Apply vertical downward offset simulating recoil control
                    adjusted_z -= shared["current_pitch_correction"]

                    enemy_pos = (ex, ey)
                    prev_pos = prev_enemy_positions.get(ent_ptr)
                    strafe_dir = "none"

                    if prev_pos:
                        dx = enemy_pos[0] - prev_pos[0]
                        dy = enemy_pos[1] - prev_pos[1]
                        move_dir = math.atan2(dy, dx)
                        vec_to_enemy = math.atan2(ey - ly, ex - lx)
                        angle_diff_rad = (move_dir - vec_to_enemy + math.pi) % (2 * math.pi) - math.pi

                        if angle_diff_rad > 0.2:
                            strafe_dir = "right"
                        elif angle_diff_rad < -0.2:
                            strafe_dir = "left"

                    prev_enemy_positions[ent_ptr] = enemy_pos

                    angle = calc_angle(local_pos, (ex, ey, adjusted_z))

                    # Apply strafe prediction
                    yaw_lead_offset = 0.3  # degrees
                    if strafe_dir == "right":
                        angle = (angle[0], angle[1] + yaw_lead_offset)
                    elif strafe_dir == "left":
                        angle = (angle[0], angle[1] - yaw_lead_offset)

                    fov = angle_diff(angle, (cp, cy))

                    if fov < best_fov:
                        best_fov = fov
                        best_angle = angle
                        new_target_ptr = ent_ptr
                except:
                    continue

            # Check if previous target died
            if target_ptr is not None and target_ptr != new_target_ptr:
                try:
                    old_health = pm.read_int(target_ptr + 0xF8)
                    if old_health <= 0:
                        cooldown_until = time.time() + 0.3
                        shared["enemy_in_fov"] = False
                        shared["left_hold_start"] = None
                        shared["current_pitch_correction"] = 0.0
                        target_ptr = None
                        time.sleep(0.01)
                        continue
                except:
                    pass

            target_ptr = new_target_ptr

            if best_angle and target_ptr is not None:
                shared["enemy_in_fov"] = True
                if shared["last_target_ptr"] != target_ptr or not shared["was_aiming"]:
                    shared["bezier_t"] = 0.0  # Reset when new target or just started aiming
                    shared["last_target_ptr"] = target_ptr
                    shared["was_aiming"] = True
                else:
                    shared["bezier_t"] = min(1.0, shared["bezier_t"] + shared["correction_speed"])
                    shared["was_aiming"] = False

                smooth_pitch, smooth_yaw = bezier_smooth_angle((cp, cy), best_angle, shared["bezier_t"])

                # Apply dynamic pitch correction only when firing, with 0.1s delay before recoil kicks in
                if shared["firing"]:
                    now = time.time()
                    if shared.get("left_hold_start") is None:
                        shared["left_hold_start"] = now

                    # Start applying recoil only after 0.05 seconds of firing
                    if now - shared["left_hold_start"] >= 0.05:
                        shared["current_pitch_correction"] = min(
                            shared.get("current_pitch_correction", 0.0) + shared.get("correction_speed_2", 0.5) * recoil,
                            54.0
                        )
                else:
                    shared["left_hold_start"] = None
                    shared["current_pitch_correction"] = 0.0

                total_pitch = smooth_pitch + PITCH_CORRECTION_2
                pm.write_float(pitch_addr, total_pitch)
                pm.write_float(yaw_addr, smooth_yaw)

            else:
                shared["enemy_in_fov"] = False
                shared["left_hold_start"] = None
                shared["current_pitch_correction"] = 0.0

        except Exception as e:
            print(f"[AIMBOT ERROR]: {e}")
            shared["left_hold_start"] = None
            shared["current_pitch_correction"] = 0.0

        time.sleep(0.01)




def hotkey_listener():
    def on_click(x, y, button, pressed):
        if str(button) == shared["hotkey"]:
            shared["aim_active"] = pressed
        if str(button) == "Button.left":
            shared["firing"] = pressed

    def on_press(key):
        if str(key) == shared["hotkey"]:
            shared["aim_active"] = True

    def on_release(key):
        if str(key) == shared["hotkey"]:
            shared["aim_active"] = False

    mouse.Listener(on_click=on_click).start()
    keyboard.Listener(on_press=on_press, on_release=on_release).start()


def gui_thread(pm, entity_list):


    def update_settings():
        shared["correction_speed"] = correction_slider.get()
        shared["recoil"] = smooth_slider.get()
        root.after(300, update_settings)

    def refresh_entity_list():
        entity_box.delete(0, tk.END)
        for idx, x, y, z in shared["entity_info"]:
            entity_box.insert(tk.END, f"Entity {idx}: ({x:.1f}, {y:.1f}, {z:.1f})")
        root.after(1000, refresh_entity_list)

    def change_hotkey(event=None):
        selected = hotkey_var.get()
        shared["hotkey"] = selected
        print(f"[+] Hotkey changed to: {selected}")

    def f_key_listener():
        def on_press(key):
            try:
                if key == keyboard.Key.f1:
                    correction_slider.set(2.19)
                elif key == keyboard.Key.f2:
                    correction_slider.set(2.88)
                elif key == keyboard.Key.f3:
                    correction_slider.set(1.8)
            except Exception as e:
                print(f"F key listener error: {e}")

        with keyboard.Listener(on_press=on_press) as listener:
            listener.join()

    threading.Thread(target=f_key_listener, daemon=True).start()

    


    root = tk.Tk()
    root.title("Aimbot Config")
    fov_circle_overlay(root)

    draw_fov_var = BooleanVar(value=False)
    def toggle_draw_fov():
        shared["draw_fov_circle"] = draw_fov_var.get()

        Checkbutton(root, text="Draw FOV Circle", variable=draw_fov_var, command=toggle_draw_fov).pack()


    tk.Label(root, text="recoil:").pack()
    smooth_slider = tk.Scale(root, from_=10.0, to=100.0, resolution=0.01, orient="horizontal")
    smooth_slider.set(shared["recoil"])
    smooth_slider.pack()

    tk.Label(root, text="smoothing:").pack()
    correction_slider = tk.Scale(root, from_=0.0, to=1.0, resolution=0.01, orient="horizontal")
    correction_slider.set(shared["correction_speed"])
    correction_slider.pack()

    fov_label = tk.Label(root, text="Aimbot FOV")
    fov_label.pack()

    fov_slider = tk.Scale(root, from_=1, to=30, resolution=0.1, orient=tk.HORIZONTAL, length=200,
                          command=lambda val: shared.update({"desired_fov": float(val)}))
    fov_slider.set(shared["desired_fov"])
    fov_slider.pack()

    




    team_label = tk.Label(root, text="Team: N/A")
    team_label.pack()

    local_label = tk.Label(root, text="Local Index: N/A")
    local_label.pack()

    tk.Label(root, text="Aimbot Activation Hotkey:").pack()
    hotkey_var = tk.StringVar(value=shared["hotkey"])
    hotkey_options = [
        "Key.shift", "Key.ctrl", "Key.alt", "Key.space", "2",
        "Button.left", "Button.right", "Button.middle"
    ]
    tk.OptionMenu(root, hotkey_var, *hotkey_options, command=change_hotkey).pack()

    update_settings()
    team_label.config(text=f"Team: {shared['my_team']}")
    local_label.config(text=f"Local Index: {shared['local_index']}")

    root.protocol("WM_DELETE_WINDOW", lambda: root.quit())
    root.mainloop()
    shared["running"] = False

def detect_local_player_index_and_team(pm, client, entity_list):
    THRESHOLD_DISTANCE = 2.0  # Distance threshold for identifying local entity

    while shared["running"]:
        try:
            print("\n[DEBUG] Starting local player detection cycle...")

            # 🧠 Resolve local player base pointer (from pointer chain)
            base_ptr = pm.read_uint(client + 0x0526108C)
            if base_ptr == 0:
                print("[ERROR] Invalid base_ptr.")
                time.sleep(1.0)
                continue

            local_health = pm.read_int(base_ptr + 0x100)
            local_x = pm.read_float(base_ptr + 0x138)
            local_z = pm.read_float(base_ptr + 0x140)

            print(f"[DEBUG] Local Health: {local_health}")
            print(f"[DEBUG] Local Pos: X={local_x}, Z={local_z}")

            if math.isnan(local_x) or math.isnan(local_z):
                print("[ERROR] Local player position is NaN.")
                time.sleep(1.0)
                continue

            closest_index = None
            closest_distance = float('inf')

            print("[DEBUG] Scanning entity list for matching local player...")

            for i in range(MAX_ENTITIES):
                try:
                    ent_ptr = pm.read_uint(entity_list + i * ENTITY_STRIDE)
                    if ent_ptr == 0:
                        continue

                    ent_health = pm.read_int(ent_ptr + 0xF8)
                    ent_x = pm.read_float(ent_ptr + POS_X)
                    ent_z = pm.read_float(ent_ptr + POS_Z)

                    dx = ent_x - local_x
                    dz = ent_z - local_z
                    distance = math.sqrt(dx * dx + dz * dz)

                    if math.isnan(distance):
                        continue

                    if distance < THRESHOLD_DISTANCE and abs(ent_health - local_health) < 1:
                        if distance < closest_distance:
                            closest_distance = distance
                            closest_index = i

                except Exception as inner_e:
                    continue

            if closest_index is not None:
                print(f"[DEBUG] Matched Local Entity Index: {closest_index}")
                shared["local_index"] = closest_index

                # ✅ Update teamNum directly from matched entity
                ent_ptr = pm.read_uint(entity_list + closest_index * ENTITY_STRIDE)
                if ent_ptr:
                    team_num = pm.read_int(ent_ptr + 0xEC)  # Confirm offset is still correct
                    shared["my_team"] = team_num
                    print(f"[DEBUG] Updated TeamNum: {team_num}")
            else:
                print("[DEBUG] No matching local entity found.")

        except Exception as e:
            print(f"[ERROR] detect_local_player_index_and_team: {e}")

        time.sleep(10.0)  # ✅ Run every 10 seconds


def main():
    pm = pymem.Pymem("csgo.exe")
    client = pymem.process.module_from_name(pm.process_handle, CLIENT_DLL).lpBaseOfDll
    stdshader = pymem.process.module_from_name(pm.process_handle, STD_SHADER_DLL).lpBaseOfDll
    entity_list = client + ENTITY_LIST_OFFSET

    pitch_ptr = pm.read_uint(stdshader + PITCH_BASE_OFFSET)
    pitch_addr = pm.read_uint(pitch_ptr + PTR_OFFSET_1) + PITCH_FINAL_OFFSET

    yaw_ptr = pm.read_uint(stdshader + YAW_BASE_OFFSET)
    yaw_addr = pm.read_uint(yaw_ptr + PTR_OFFSET_1) + YAW_FINAL_OFFSET

    

    # Initial team read
    try:
        lp1 = pm.read_uint(client + 0x0523BCC4)
        lp2 = pm.read_uint(lp1 + 0x8C)
        lp3 = pm.read_uint(lp2 + 0x40)
        local_player_ptr = lp3 + 0xB8
        shared["my_team"] = pm.read_int(local_player_ptr)
        print(f"[INFO] Local player team number set to: {shared['my_team']}")
    except Exception as e:
        print(f"[ERROR] Failed to read local player team: {e}")

    # Start threads
    threading.Thread(target=detect_local_player_index_and_team, args=(pm, client, entity_list), daemon=True).start()
    threading.Thread(target=update_entity_info, args=(pm, entity_list), daemon=True).start()
    threading.Thread(target=aimbot_thread, args=(pm, entity_list, pitch_addr, yaw_addr), daemon=True).start()
    threading.Thread(target=hotkey_listener, daemon=True).start()
    threading.Thread(target=fov_circle_overlay, daemon=True).start()
    gui_thread(pm, entity_list)

if __name__ == "__main__":
    main()
