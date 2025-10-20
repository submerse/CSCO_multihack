import pymem
import pymem.process
import tkinter as tk
import win32gui
import win32con
import struct
import time
import math

import pymem
import pymem.process
import tkinter as tk
import threading
import time
import math
import struct
import ctypes
from pynput import mouse
from collections import defaultdict

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
    "team_mode": "auto",
    "recoil_active": False,
    "recoil_start": 0.0,
}

position_history = defaultdict(lambda: {"pos": None, "timestamp": 0})

shared = {
    "my_team": 2,
    "local_index": 0,
    "running": True,
    "ghost_positions": {},
    "debug": True  # Enable to print debug info
}

def get_local_team(pm, client):
    try:
        ptr1 = pm.read_uint(client + 0x0523BCC4)
        if not ptr1:
            return 0
        ptr2 = pm.read_uint(ptr1 + 0x8C)
        if not ptr2:
            return 0
        ptr3 = pm.read_uint(ptr2 + 0x40)
        if not ptr3:
            return 0
        return pm.read_int(ptr3 + 0xB8)
    except:
        return 0

def get_local_index(pm, entity_list, max_entities=64):
    try:
        positions = []
        for i in range(max_entities):
            ptr = pm.read_uint(entity_list + i * ENTITY_STRIDE)
            if not ptr:
                continue
            z = pm.read_float(ptr + POS_Z)
            positions.append((i, z))
        if positions:
            return max(positions, key=lambda p: p[1])[0]
    except:
        pass
    return 0

def draw_esp(canvas, pm, entity_list, entity_list_2, view_matrix, my_team):
    screen_width = canvas.winfo_width()
    screen_height = canvas.winfo_height()
    current_time = time.time()

    canvas.delete("all")

    for i in range(1, MAX_ENTITIES):
        try:
            ent_ptr = pm.read_uint(entity_list + i * ENTITY_STRIDE)
            if not ent_ptr:
                continue

            team = pm.read_int(ent_ptr + TEAM_OFFSET)
            if team == my_team:
                continue

            health = pm.read_int(ent_ptr + 0xF8)
            if health <= 0 or health > 100:
                continue

            pos_x = pm.read_float(ent_ptr + POS_X)
            pos_y = pm.read_float(ent_ptr + POS_Y)
            pos_z = pm.read_float(ent_ptr + POS_Z)
            pos = (pos_x, pos_y, pos_z)

            # Ghost box filter: skip if static over 60s
            prev = shared["ghost_positions"].get(i)
            if prev and prev[0] == pos:
                if current_time - prev[1] > 60:
                    continue
            else:
                shared["ghost_positions"][i] = (pos, current_time)

            screen_head = world_to_screen((pos_x, pos_y, pos_z + 72), view_matrix, screen_width, screen_height)
            screen_feet = world_to_screen((pos_x, pos_y, pos_z), view_matrix, screen_width, screen_height)

            if not screen_head or not screen_feet:
                continue

            x1, y1 = screen_head
            x2, y2 = screen_feet

            height = y2 - y1
            width = height / 2
            x1 = x1 - width / 2
            x2 = x1 + width

            canvas.create_rectangle(x1, y1, x2, y2, outline="red", width=2)
            canvas.create_text(x1 + width / 2, y1 - 10, text=f"{health} HP", fill="white")

            if entity_list_2:
                name_ptr = pm.read_uint(entity_list_2 + i * 4)
                if name_ptr:
                    name = pm.read_string(name_ptr, 32).split("\x00")[0]
                    canvas.create_text(x1 + width / 2, y2 + 10, text=name, fill="yellow")

        except Exception as e:
            if shared["debug"]:
                print(f"[ESP ERROR] Entity {i}: {e}")
            continue

def esp_loop(pm, client, entity_list, entity_list_2, view_matrix_addr, canvas):
    while shared["running"]:
        try:
            view_matrix = pm.read_bytes(view_matrix_addr, 64)
            shared["my_team"] = get_local_team(pm, client)
            if shared["my_team"] not in [2, 3]:
                shared["my_team"] = 2  # fallback default

            shared["local_index"] = get_local_index(pm, entity_list)
            draw_esp(canvas, pm, entity_list, entity_list_2, view_matrix, shared["my_team"])
        except Exception as e:
            if shared["debug"]:
                print(f"[LOOP ERROR]: {e}")
        time.sleep(0.03)

def main():
    pm = pymem.Pymem("csgo.exe")
    client = pymem.process.module_from_name(pm.process_handle, "client.dll").lpBaseOfDll

    entity_list = pm.read_uint(client + ENTITY_LIST_OFFSET)
    view_matrix_addr = client + VIEW_MATRIX_OFFSET
    entity_list_2 = resolve_pointer_chain(pm, client + NAME_BASE_PTR_OFFSET, NAME_CHAIN)

    if not entity_list_2 or entity_list_2 < 0x10000:
        print("[!] entity_list_2 pointer failed to resolve.")
        entity_list_2 = 0

    root = tk.Tk()
    root.attributes("-topmost", True)
    root.attributes("-transparentcolor", "black")
    root.overrideredirect(True)
    root.geometry("1920x1080+0+0")
    root.configure(bg="black")

    hwnd = win32gui.FindWindow(None, root.title())
    make_window_clickthrough(hwnd)

    canvas = tk.Canvas(root, width=1920, height=1080, bg="black", highlightthickness=0)
    canvas.pack()

    import threading
    threading.Thread(target=esp_loop, args=(pm, client, entity_list, entity_list_2, view_matrix_addr, canvas), daemon=True).start()

    root.mainloop()

if __name__ == "__main__":
    main()
