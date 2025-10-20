import pymem
import pymem.process
import tkinter as tk
import threading
import time
import math
import struct
import ctypes
from pymem.exception import MemoryReadError

# === Constants ===
CLIENT_DLL = "client.dll"
ENTITY_LIST_OFFSET = 0x04E2520C
ENTITY_STRIDE = 0x8

POS_X, POS_Y, POS_Z = 0x130, 0x134, 0x138
TEAM_OFFSET = 0xEC
HEALTH_OFFSET = 0xF8
VIEW_MATRIX_OFFSET = 0x4DF85A4
CROUCH_OFFSET = 0x334
HEAD_OFFSET = 18.0
MAX_ENTITIES = 64

# === EntityList_2 Name Offsets ===
NAME_BASE_PTR_OFFSET = 0x0534D810
NAME_CHAIN = [0x17C, 0xA0, 0x18, 0x34, 0x310]
NAME_ENTITY_STRIDE = 0x174
NAME_OFFSET = 0x4

# === Globals ===
shared = {
    "my_team": 2,
    "local_index": 0,
    "running": True,
    "show_teammates": True,
    "bone_map": "default"
}

bone_models = {
    "default": {
        "indices": [0,1,2,3,4,6,7,8,11,12,14,15,16,42,67,68,69,74,75],
        "connections": [
            (0, 1), (1, 2), (2, 3), (3, 4),
            (4, 11), (11, 12),
            (4, 14), (14, 15), (15, 16),
            (4, 6), (6, 7), (7, 8),
            (0, 67), (67, 68), (68, 69),
            (0, 42), (42, 74), (74, 75),
        ]
    }
}

# Build alt model by replacing specific bone IDs
alt_indices = [
    6 if b == 4 else
    12 if b == 14 else
    40 if b == 16 else b
    for b in bone_models["default"]["indices"]
]

alt_connections = [
    (6 if a == 4 else 12 if a == 14 else 40 if a == 16 else a,
     6 if b == 4 else 12 if b == 14 else 40 if b == 16 else b)
    for a, b in bone_models["default"]["connections"]
]

bone_models["alt"] = {
    "indices": alt_indices,
    "connections": alt_connections
}

def world_to_screen(pos, matrix, screen_width, screen_height):
    x, y, z = pos
    clip_x = x * matrix[0] + y * matrix[1] + z * matrix[2] + matrix[3]
    clip_y = x * matrix[4] + y * matrix[5] + z * matrix[6] + matrix[7]
    clip_w = x * matrix[12] + y * matrix[13] + z * matrix[14] + matrix[15]

    if clip_w < 0.01:
        return None

    ndc_x = clip_x / clip_w
    ndc_y = clip_y / clip_w

    screen_x = (screen_width / 2) + (ndc_x * screen_width / 2)
    screen_y = (screen_height / 2) - (ndc_y * screen_height / 2)

    return int(screen_x), int(screen_y)

def resolve_pointer_chain(pm, base, chain):
    try:
        addr = pm.read_uint(base)
        for offset in chain[:-1]:
            addr = pm.read_uint(addr + offset)
        return addr + chain[-1]
    except:
        return 0

def get_local_team(pm, client_base):
    try:
        base_ptr = pm.read_uint(client_base + 0x0523BCC4)
        ptr_1 = pm.read_uint(base_ptr + 0x8C)
        ptr_2 = pm.read_uint(ptr_1 + 0x40)
        return pm.read_int(ptr_2 + 0xB8)
    except:
        return 0

def get_bone_world_pos(pm, bonematrix_ptr, bone_id):
    base = bonematrix_ptr + bone_id * 0x30
    bone_x = pm.read_float(base + 0x0C)
    bone_y = pm.read_float(base + 0x1C)
    bone_z = pm.read_float(base + 0x2C)
    return (bone_x, bone_y, bone_z)


def esp_loop(pm, client, entity_list, canvas, root):
    width = root.winfo_screenwidth()
    height = root.winfo_screenheight()

    while shared["running"]:
        try:
            if not canvas.winfo_exists():
                break

            canvas.delete("all")

            try:
                view_matrix = struct.unpack("16f", pm.read_bytes(client + VIEW_MATRIX_OFFSET, 64))
            except:
                time.sleep(0.1)
                continue

            # === Get local player pointer and index ===
            try:
                fixed_local_ptr = pm.read_uint(client + 0x052BF6D8)  # LOCAL_PLAYER_PTR
                if not fixed_local_ptr:
                    time.sleep(0.1)
                    continue

                local_x = pm.read_float(fixed_local_ptr + 0x2AC)  # POS_X
                local_z = pm.read_float(fixed_local_ptr + 0x2B4)  # POS_Z

                for idx in range(MAX_ENTITIES):
                    test_ptr = pm.read_uint(entity_list + idx * ENTITY_STRIDE)
                    if not test_ptr:
                        continue
                    ex = pm.read_float(test_ptr + 0x2AC)
                    ez = pm.read_float(test_ptr + 0x2B4)
                    if abs(ex - local_x) < 0.01 and abs(ez - local_z) < 0.01:
                        shared["local_index"] = idx
                        break
            except:
                time.sleep(0.1)
                continue

            mode = shared.get("team_mode", "auto")
            if mode == "manual2":
                my_team = 2
            elif mode == "manual3":
                my_team = 3
            else:
                my_team = get_local_team(pm, client)

            entity_list_2 = resolve_pointer_chain(pm, client + NAME_BASE_PTR_OFFSET, NAME_CHAIN)
            if not entity_list_2:
                continue

            for i in range(MAX_ENTITIES):
                try:
                    ent_ptr = pm.read_uint(entity_list + i * ENTITY_STRIDE)
                    if not ent_ptr:
                        continue

                    if i == shared.get("local_index", -1):
                        continue

                    ent_team = pm.read_int(ent_ptr + TEAM_OFFSET)
                    is_teammate = (ent_team == my_team)
                    if ent_team == 0:
                        continue

                    health = pm.read_int(ent_ptr + HEALTH_OFFSET)
                    if health <= 0 or health > 200:
                        continue

                    ex = pm.read_float(ent_ptr + POS_X)
                    ey = pm.read_float(ent_ptr + POS_Y)
                    ez = pm.read_float(ent_ptr + POS_Z)
                    ent_pos = (ex, ey, ez)

                    crouch = pm.read_float(ent_ptr + CROUCH_OFFSET)
                    delta = max(0.0, min(1.0, (72.0 - crouch) / 18.0))
                    head_z = ez + 70 - (HEAD_OFFSET * delta)

                    feet_pos = (ex, ey, ez)
                    head_pos = (ex, ey, head_z)

                    feet_screen = world_to_screen(feet_pos, view_matrix, width, height)
                    head_screen = world_to_screen(head_pos, view_matrix, width, height)

                    if feet_screen is None or head_screen is None:
                        continue

                    feet_x, feet_y = feet_screen
                    head_x, head_y = head_screen

                    box_height = feet_y - head_y
                    box_width = box_height // 2

                    x1 = int(feet_x - box_width // 2)
                    y1 = int(head_y)
                    x2 = int(feet_x + box_width // 2)
                    y2 = int(feet_y)

                    if is_teammate and not shared["show_teammates"]:
                        continue

                    box_color = "blue" if is_teammate else "red"
                    canvas.create_rectangle(x1, y1, x2, y2, outline=box_color, width=2)

                    bar_width = 5
                    health_percent = max(0, min(health / 100.0, 1.0))
                    filled_height = int((y2 - y1) * health_percent)

                    color = "green" if health_percent > 0.66 else "orange" if health_percent > 0.33 else "red"

                    bar_x1 = x1 - 8
                    bar_x2 = bar_x1 + bar_width
                    bar_y2 = y2
                    bar_y1 = y2 - filled_height

                    canvas.create_rectangle(bar_x1, y1, bar_x2, y2, fill="gray", outline="black")
                    canvas.create_rectangle(bar_x1, bar_y1, bar_x2, bar_y2, fill=color, outline=color)

                    try:
                        ent2_ptr = entity_list_2 + i * NAME_ENTITY_STRIDE
                        name_bytes = pm.read_bytes(ent2_ptr + NAME_OFFSET, 32)
                        name = name_bytes.split(b'\x00')[0].decode("utf-8", errors="ignore")
                    except:
                        name = "?"

                    canvas.create_text(int(feet_x), int(y1 - 15), text=name, fill="white", font=("Arial", 10, "bold"))

                    try:
                        bonematrix_ptr = pm.read_uint(ent_ptr + 0x26A0)
                        if bonematrix_ptr:
                            bones = {}
                            bone_indices = [0,1,2,3,4,6,7,8,11,12,14,15,16,42,67,68,69,74,75]

                            # Choose model
                            model = bone_models["default"]  # or "alt" if you want the alternate mapping

                            for bone_id in model["indices"]:
                                bones[bone_id] = get_bone_world_pos(pm, bonematrix_ptr, bone_id)

                            for start_bone, end_bone in model["connections"]:
                                start_pos = bones.get(start_bone)
                                end_pos = bones.get(end_bone)
                                if start_pos and end_pos:
                                    start_screen = world_to_screen(start_pos, view_matrix, width, height)
                                    end_screen = world_to_screen(end_pos, view_matrix, width, height)
                                    if start_screen and end_screen:
                                        canvas.create_line(
                                            start_screen[0], start_screen[1],
                                            end_screen[0], end_screen[1],
                                            fill="lime", width=2
                                        )
                    except Exception:
                        pass

                except Exception:
                    pass

            canvas.update()
            time.sleep(0.05)

        except Exception:
            pass

def make_window_clickthrough(hwnd):
    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x80000
    WS_EX_TRANSPARENT = 0x20
    LWA_COLORKEY = 0x1

    styles = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    styles |= WS_EX_LAYERED | WS_EX_TRANSPARENT
    ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, styles)
    ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0x000000, 0, LWA_COLORKEY)

def main():
    try:
        pm = pymem.Pymem("csgo.exe")  # Change if CS:CO has a different exe name
    except Exception as e:
        print(f"[ERROR] Failed to attach to process: {e}")
        return

    try:
        client = pymem.process.module_from_name(pm.process_handle, CLIENT_DLL).lpBaseOfDll
    except Exception as e:
        print(f"[ERROR] Could not find {CLIENT_DLL}: {e}")
        return

    entity_list = client + ENTITY_LIST_OFFSET

    root = tk.Tk()
    root.attributes("-topmost", True)
    root.attributes("-transparentcolor", "black")
    root.overrideredirect(True)

    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    root.geometry(f"{screen_width}x{screen_height}+0+0")

    canvas = tk.Canvas(root, width=screen_width, height=screen_height, bg="black", highlightthickness=0)
    canvas.pack()

    root.update_idletasks()
    hwnd = ctypes.windll.user32.FindWindowW(None, root.title())
    make_window_clickthrough(hwnd)

    def set_team_mode(mode):
        shared["team_mode"] = mode

    control_win = tk.Toplevel(root)
    control_win.title("Team Mode Selector")
    control_win.geometry("200x300")
    control_win.resizable(False, False)

    def toggle_show_teammates():
        shared["show_teammates"] = not shared["show_teammates"]
        btn_show_teammates.config(
            text="Show Teammates: " + ("On" if shared["show_teammates"] else "Off")
        )

    btn_show_teammates = tk.Button(control_win, text="Show Teammates: On", width=15, command=toggle_show_teammates)
    btn_show_teammates.pack(pady=5)

    def toggle_bone_map():
        if shared["bone_map"] == "default":
            shared["bone_map"] = "alt"
        else:
            shared["bone_map"] = "default"
        print("Bone map switched to:", shared["bone_map"])

    toggle_button = tk.Button(control_win, text="Toggle Bone Map", command=toggle_bone_map)
    toggle_button.pack()

    tk.Label(control_win, text="Select Team Mode:", font=("Arial", 12, "bold")).pack(pady=10)
    tk.Button(control_win, text="Auto Detect", width=15, command=lambda: set_team_mode("auto")).pack(pady=5)
    tk.Button(control_win, text="Manual: Team 2", width=15, command=lambda: set_team_mode("manual2")).pack(pady=5)
    tk.Button(control_win, text="Manual: Team 3", width=15, command=lambda: set_team_mode("manual3")).pack(pady=5)



    shared["team_mode"] = "auto"

    threading.Thread(target=esp_loop, args=(pm, client, entity_list, canvas, root), daemon=True).start()

    root.mainloop()
    shared["running"] = False

if __name__ == "__main__":
    main()

