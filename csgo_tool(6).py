import pymem
import pymem.process
import pymem.exception
import tkinter as tk
from tkinter import ttk
import threading
import time
import math
import struct
import ctypes
from pymem.exception import MemoryReadError
from pynput import keyboard, mouse

# ─── Constants ───────────────────────────────────────────────────────────────

PROCESS_NAME = "csgo.exe"
CLIENT_DLL   = "client.dll"
STD_SHADER_DLL = "stdshader_dx9.dll"

ENTITY_LIST_OFFSET = 0x04E2520C
ENTITY_STRIDE      = 0x8
MAX_ENTITIES       = 64

POS_X, POS_Y, POS_Z = 0x130, 0x134, 0x138
TEAM_OFFSET          = 0xEC
HEALTH_OFFSET        = 0xF8
CROUCH_OFFSET        = 0x334

VIEW_MATRIX_OFFSET   = 0x4DF85A4
HEAD_OFFSET          = 18.0  # used in ESP box foot-to-head calc

# ESP name chain
NAME_BASE_PTR_OFFSET = 0x0534D810
NAME_CHAIN           = [0x17C, 0xA0, 0x18, 0x34, 0x310]
NAME_ENTITY_STRIDE   = 0x174
NAME_OFFSET          = 0x4

# Aimbot angle addresses (via stdshader_dx9.dll)
PITCH_BASE_OFFSET  = 0x000EE59C
YAW_BASE_OFFSET    = 0x000EE58C
PTR_OFFSET_1       = 0x10
PITCH_FINAL_OFFSET = 0x0
YAW_FINAL_OFFSET   = 0x4
PITCH_CORRECTION_2 = 0.0

# Radar: force offsets to make enemies visible on minimap
RADAR_FORCE  = {0x0934: 256, 0x0978: 1}
RADAR_TOGGLE = {0x0A20: 1,   0x0A24: 1}

# Triggerbot pointer chain (reads a game value; fires on change)
TB_POINTER_BASE = 0x00DEF97C
TB_OFFSETS      = [0x24, 0x4, 0x68]

# ─── Nord Color Palette ──────────────────────────────────────────────────────

NORD = {
    "bg":        "#2E3440",  # Polar Night 0 — darkest bg
    "bg_light":  "#3B4252",  # Polar Night 1
    "bg_mid":    "#434C5E",  # Polar Night 2
    "bg_hover":  "#4C566A",  # Polar Night 3
    "fg":        "#D8DEE9",  # Snow Storm 0
    "fg_dim":    "#81A1C1",  # Frost 3 — muted label text
    "fg_bright": "#ECEFF4",  # Snow Storm 2
    "teal":      "#8FBCBB",  # Frost 0
    "frost":     "#88C0D0",  # Frost 1 — primary accent
    "blue":      "#5E81AC",  # Frost 3 — darker accent
    "red":       "#BF616A",  # Aurora red
    "orange":    "#D08770",  # Aurora orange
    "yellow":    "#EBCB8B",  # Aurora yellow
    "green":     "#A3BE8C",  # Aurora green
    "purple":    "#B48EAD",  # Aurora purple
}

# ─── Shared state ────────────────────────────────────────────────────────────

shared = {
    # common
    "my_team":    2,
    "local_index": 0,
    "running":    True,
    # ESP
    "team_mode":  "auto",
    # aimbot
    "recoil":               20,
    "entity_info":          [],
    "aim_active":           False,
    "hotkey":               "Button.right",
    "firing":               False,
    "enemy_in_fov":         False,
    "left_hold_start":      None,
    "current_pitch_correction": 0.0,
    "correction_speed":     0.3,
    "bezier_t":             0.0,
    "was_aiming":           False,
    "correction_speed_2":   0.05,
    "last_target_ptr":      None,
    "desired_fov":          4.0,
    "fov_circle":           False,
    "aim_enabled":          True,
    "tb_enabled":           True,
    "esp_enabled":          True,
    # triggerbot
    "tb_active_key":  "CAPS_LOCK",
    "tb_datatype":    "FLOAT",
    "tb_key_pressed": False,
    "tb_right_mouse": False,
    "tb_left_mouse":  False,
    "tb_middle_mouse": False,
    "tb_status":      "Waiting",
}

# ─── Utility ─────────────────────────────────────────────────────────────────

def world_to_screen(pos, matrix, sw, sh):
    x, y, z = pos
    clip_x = x*matrix[0]  + y*matrix[1]  + z*matrix[2]  + matrix[3]
    clip_y = x*matrix[4]  + y*matrix[5]  + z*matrix[6]  + matrix[7]
    clip_w = x*matrix[12] + y*matrix[13] + z*matrix[14] + matrix[15]
    if clip_w < 0.01:
        return None
    ndc_x = clip_x / clip_w
    ndc_y = clip_y / clip_w
    return int(sw/2 + ndc_x*sw/2), int(sh/2 - ndc_y*sh/2)


def resolve_pointer_chain(pm, base, chain):
    try:
        addr = pm.read_uint(base)
        for offset in chain[:-1]:
            addr = pm.read_uint(addr + offset)
        return addr + chain[-1]
    except:
        return 0


def get_local_team(pm, client):
    try:
        p1 = pm.read_uint(client + 0x0523BCC4)
        p2 = pm.read_uint(p1 + 0x8C)
        p3 = pm.read_uint(p2 + 0x40)
        return pm.read_int(p3 + 0xB8)
    except:
        return 0


def is_valid_pointer(pm, addr):
    try:
        pm.read_bytes(addr, 4)
        return True
    except:
        return False


def calc_angle(src, dst):
    dx, dy, dz = dst[0]-src[0], dst[1]-src[1], dst[2]-src[2]
    hyp = math.sqrt(dx*dx + dy*dy)
    return -math.degrees(math.atan2(dz, hyp)), math.degrees(math.atan2(dy, dx))


def angle_diff(a1, a2):
    dp = a1[0] - a2[0]
    dy = ((a1[1]-a2[1])+180) % 360 - 180
    return math.sqrt(dp*dp + dy*dy)


def bezier_point(p0, p1, p2, p3, t):
    u = 1 - t
    return u**3*p0 + 3*u**2*t*p1 + 3*u*t**2*p2 + t**3*p3


def bezier_smooth_angle(current, target, t):
    p0, y0 = current
    p3, y3 = target
    p1 = p0 + (p3-p0)*0.25
    p2 = p0 + (p3-p0)*0.75
    y1 = y0 + ((y3-y0+180)%360-180)*0.25
    y2 = y0 + ((y3-y0+180)%360-180)*0.75
    return bezier_point(p0,p1,p2,p3,t), bezier_point(y0,y1,y2,y3,t)


def tb_resolve_pointer(pm, base, offsets):
    try:
        addr = struct.unpack("<I", pm.read_bytes(base, 4))[0]
        for offset in offsets[:-1]:
            addr = struct.unpack("<I", pm.read_bytes(addr + offset, 4))[0]
            if addr == 0:
                return None
        return addr + offsets[-1]
    except:
        return None


def tb_read_value(pm, address, datatype):
    try:
        if datatype == "INT":    return pm.read_int(address)
        if datatype == "FLOAT":  return pm.read_float(address)
        if datatype == "DOUBLE": return pm.read_double(address)
        if datatype == "BYTE":   return pm.read_bytes(address, 1)[0]
    except:
        return None


# ─── Dormancy probe ──────────────────────────────────────────────────────────

def probe_dormancy(pm, entity_list):
    """
    Dump bytes 0xE0-0xFF for every entity that has a valid team + health.
    Run this in-game:
      - once standing right next to a visible enemy  (real entity  → dormant byte = 0x00)
      - once with only ghost boxes visible            (stale entity → dormant byte = 0x01)
    Whichever offset flips between the two runs is m_bDormant.
    """
    PROBE_START  = 0xD0
    PROBE_LEN    = 64          # covers 0xD0 – 0x10F

    print("\n" + "═" * 72)
    print("  DORMANCY PROBE  —  offsets 0xE0 … 0xFF")
    print("  Look for a byte that is 00 on real enemies and 01 on ghosts.")
    print("═" * 72)
    header = "  off →  " + "  ".join(f"{PROBE_START+j:02X}" for j in range(PROBE_LEN))
    print(f"  (team offset=0xEC, health offset=0xF8 — use these as anchors)")
    print(header)
    print("─" * 72)

    for i in range(MAX_ENTITIES):
        try:
            ent_ptr = pm.read_uint(entity_list + i * ENTITY_STRIDE)
            if not ent_ptr:
                continue
            team   = pm.read_int(ent_ptr + TEAM_OFFSET)
            health = pm.read_int(ent_ptr + HEALTH_OFFSET)
            if team not in (2, 3) or health <= 0 or health > 200:
                continue

            raw     = pm.read_bytes(ent_ptr + PROBE_START, PROBE_LEN)
            hex_row = "  ".join(f"{b:02X}" for b in raw)
            x = pm.read_float(ent_ptr + POS_X)
            y = pm.read_float(ent_ptr + POS_Y)
            print(f"  [{i:2d}] team={team} hp={health:3d}  pos=({x:7.1f},{y:7.1f})")
            print(f"         {hex_row}")
        except:
            continue

    print("═" * 72 + "\n")


# ─── Feature threads ─────────────────────────────────────────────────────────

def esp_loop(pm, client, entity_list, canvas, root):
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()

    def render(cmds):
        canvas.delete("all")
        for cmd in cmds:
            kind = cmd[0]
            if kind == "box":
                canvas.create_rectangle(cmd[1], cmd[2], cmd[3], cmd[4],
                                        outline=cmd[5], width=2)
            elif kind == "bar":
                canvas.create_rectangle(cmd[1], cmd[2], cmd[3], cmd[4],
                                        fill=cmd[5], outline=cmd[6])
            elif kind == "name":
                canvas.create_text(cmd[1]+1, cmd[2]+1, text=cmd[3],
                                   fill=NORD["bg"], font=("Arial", 10, "bold"))
                canvas.create_text(cmd[1], cmd[2], text=cmd[3],
                                   fill=NORD["fg_bright"], font=("Arial", 10, "bold"))
            elif kind == "head":
                r = cmd[3]
                canvas.create_oval(cmd[1]-r, cmd[2]-r, cmd[1]+r, cmd[2]+r,
                                   outline=NORD["frost"], width=2)
            elif kind == "fov":
                r = cmd[3]
                canvas.create_oval(cmd[1]-r, cmd[2]-r, cmd[1]+r, cmd[2]+r,
                                   outline=NORD["blue"], width=1, dash=(4, 4))

    while shared["running"]:
        t0 = time.perf_counter()
        try:
            if not canvas.winfo_exists():
                break

            if not shared["esp_enabled"]:
                root.after(0, canvas.delete, "all")
                time.sleep(0.05)
                continue

            try:
                view_matrix = struct.unpack("16f", pm.read_bytes(client + VIEW_MATRIX_OFFSET, 64))
            except:
                time.sleep(0.1)
                continue

            local_ptr = pm.read_uint(entity_list + shared["local_index"] * ENTITY_STRIDE)
            if not local_ptr:
                time.sleep(0.1)
                continue

            mode = shared["team_mode"]
            if mode == "manual2":
                my_team = 2
            elif mode == "manual3":
                my_team = 3
            else:
                my_team = get_local_team(pm, client)

            entity_list_2 = resolve_pointer_chain(pm, client + NAME_BASE_PTR_OFFSET, NAME_CHAIN)
            if not entity_list_2:
                time.sleep(0.03)
                continue

            cmds = []

            for i in range(MAX_ENTITIES):
                try:
                    ent_ptr = pm.read_uint(entity_list + i * ENTITY_STRIDE)
                    if not ent_ptr or ent_ptr == local_ptr:
                        continue

                    if pm.read_bytes(ent_ptr + 0xE5, 1)[0]:
                        continue  # entity is dormant — position data is stale

                    ent_team = pm.read_int(ent_ptr + TEAM_OFFSET)
                    if ent_team == 0:
                        continue
                    is_teammate = (ent_team == my_team)

                    health = pm.read_int(ent_ptr + HEALTH_OFFSET)
                    if health <= 0 or health > 200:
                        continue

                    ex = pm.read_float(ent_ptr + POS_X)
                    ey = pm.read_float(ent_ptr + POS_Y)
                    ez = pm.read_float(ent_ptr + POS_Z)
                    if (math.isnan(ex) or math.isnan(ey) or math.isnan(ez)
                            or abs(ex) > 16384 or abs(ey) > 16384 or abs(ez) > 4096):
                        continue
                    crouch = pm.read_float(ent_ptr + CROUCH_OFFSET)

                    delta  = max(0.0, min(1.0, (72.0 - crouch) / 18.0))
                    head_z = ez + 70 - (HEAD_OFFSET * delta)

                    feet_screen = world_to_screen((ex, ey, ez),     view_matrix, sw, sh)
                    head_screen = world_to_screen((ex, ey, head_z), view_matrix, sw, sh)
                    if feet_screen is None or head_screen is None:
                        continue

                    fx, fy = feet_screen
                    hx, hy = head_screen
                    box_h  = fy - hy
                    box_w  = box_h // 2
                    x1, y1 = fx - box_w//2, hy
                    x2, y2 = fx + box_w//2, fy

                    box_color = NORD["teal"] if is_teammate else NORD["red"]
                    cmds.append(("box", x1, y1, x2, y2, box_color))

                    hp_pct   = max(0.0, min(health / 100.0, 1.0))
                    filled_h = int((y2-y1) * hp_pct)
                    bar_color = NORD["green"] if hp_pct > 0.66 else NORD["yellow"] if hp_pct > 0.33 else NORD["red"]
                    bx1, bx2 = x1-8, x1-3
                    cmds.append(("bar", bx1, y1,          bx2, y2, NORD["bg_hover"], NORD["bg_light"]))
                    cmds.append(("bar", bx1, y2-filled_h, bx2, y2, bar_color,        bar_color))

                    try:
                        ent2_ptr   = entity_list_2 + i * NAME_ENTITY_STRIDE
                        name_bytes = pm.read_bytes(ent2_ptr + NAME_OFFSET, 32)
                        name       = name_bytes.split(b'\x00')[0].decode("utf-8", errors="ignore")
                    except:
                        name = "?"
                    cmds.append(("name", fx, y1-15, name))

                    try:
                        bm_ptr = pm.read_uint(ent_ptr + 0x26A0)
                        if bm_ptr:
                            best_bone  = None
                            highest_bz = -float("inf")
                            for bone_id in [6, 8, 12]:
                                off  = bone_id * 0x30
                                bx_  = pm.read_float(bm_ptr + off + 0x0C)
                                by_  = pm.read_float(bm_ptr + off + 0x1C)
                                bz_  = pm.read_float(bm_ptr + off + 0x2C)
                                if bz_ > highest_bz:
                                    highest_bz = bz_
                                    best_bone  = (bx_, by_, bz_)
                            if best_bone:
                                bone_screen = world_to_screen(best_bone, view_matrix, sw, sh)
                                if bone_screen:
                                    bcx, bcy = bone_screen
                                    above        = (best_bone[0], best_bone[1], best_bone[2] + 7.0)
                                    above_screen = world_to_screen(above, view_matrix, sw, sh)
                                    if above_screen:
                                        r = max(3, abs(bcy - above_screen[1]))
                                        cmds.append(("head", bcx, bcy, r))
                    except:
                        pass

                except MemoryReadError:
                    continue
                except:
                    continue

            if shared["fov_circle"]:
                fov_r = int((sw / 2) * math.tan(math.radians(shared["desired_fov"]))
                            / math.tan(math.radians(53.13)))
                cmds.append(("fov", sw // 2, sh // 2, fov_r))

            root.after(0, render, cmds)

        except Exception as e:
            print(f"[ESP ERROR]: {e}")

        elapsed = time.perf_counter() - t0
        time.sleep(max(0.0, 0.016 - elapsed))


def radar_thread(pm, entity_list):
    toggle = False
    while shared["running"]:
        try:
            my_team = shared["my_team"]
            for i in range(MAX_ENTITIES):
                try:
                    ent_ptr = pm.read_uint(entity_list + i * ENTITY_STRIDE)
                    if not ent_ptr:
                        continue
                    team   = pm.read_int(ent_ptr + TEAM_OFFSET)
                    health = pm.read_int(ent_ptr + HEALTH_OFFSET)
                    if team == 0 or health <= 0 or team == my_team:
                        continue
                    for offset, desired in RADAR_FORCE.items():
                        if pm.read_int(ent_ptr + offset) != desired:
                            pm.write_int(ent_ptr + offset, desired)
                    for offset, desired in RADAR_TOGGLE.items():
                        pm.write_int(ent_ptr + offset, desired if toggle else 0)
                except:
                    continue
            toggle = not toggle
        except Exception as e:
            print(f"[RADAR ERROR]: {e}")
        time.sleep(0.05)


def update_entity_info(pm, entity_list):
    while shared["running"]:
        info = []
        for i in range(MAX_ENTITIES):
            try:
                ent_ptr = pm.read_uint(entity_list + i * ENTITY_STRIDE)
                if not ent_ptr or not is_valid_pointer(pm, ent_ptr):
                    continue
                x = pm.read_float(ent_ptr + POS_X)
                y = pm.read_float(ent_ptr + POS_Y)
                z = pm.read_float(ent_ptr + POS_Z)
                info.append((i, x, y, z))
            except:
                continue
        shared["entity_info"] = info
        time.sleep(0.1)


def detect_local_player(pm, client, entity_list):
    while shared["running"]:
        try:
            base_ptr = pm.read_uint(client + 0x0526108C)
            if base_ptr == 0:
                time.sleep(1.0)
                continue
            local_health = pm.read_int(base_ptr + 0x100)
            local_x      = pm.read_float(base_ptr + 0x138)
            local_z      = pm.read_float(base_ptr + 0x140)
            if math.isnan(local_x) or math.isnan(local_z):
                time.sleep(1.0)
                continue

            closest_idx  = None
            closest_dist = float("inf")
            for i in range(MAX_ENTITIES):
                try:
                    ent_ptr = pm.read_uint(entity_list + i * ENTITY_STRIDE)
                    if not ent_ptr:
                        continue
                    eh = pm.read_int(ent_ptr + HEALTH_OFFSET)
                    ex = pm.read_float(ent_ptr + POS_X)
                    ez = pm.read_float(ent_ptr + POS_Z)
                    d  = math.sqrt((ex-local_x)**2 + (ez-local_z)**2)
                    if math.isnan(d):
                        continue
                    if d < 2.0 and abs(eh - local_health) < 1 and d < closest_dist:
                        closest_dist = d
                        closest_idx  = i
                except:
                    continue

            if closest_idx is not None:
                shared["local_index"] = closest_idx
                ent_ptr = pm.read_uint(entity_list + closest_idx * ENTITY_STRIDE)
                if ent_ptr:
                    shared["my_team"] = pm.read_int(ent_ptr + TEAM_OFFSET)
        except Exception as e:
            print(f"[LOCAL DETECT ERROR]: {e}")
        time.sleep(10.0)


def aimbot_thread(pm, entity_list, pitch_addr, yaw_addr):
    cooldown_until = 0
    target_ptr     = None

    while shared["running"]:
        now = time.time()
        if now < cooldown_until or not shared["aim_active"] or not shared["aim_enabled"]:
            shared["enemy_in_fov"]          = False
            shared["left_hold_start"]        = None
            shared["current_pitch_correction"] = 0.0
            time.sleep(0.01)
            continue

        try:
            local_ptr = pm.read_uint(entity_list + shared["local_index"] * ENTITY_STRIDE)
            if local_ptr == 0:
                shared["enemy_in_fov"]          = False
                shared["current_pitch_correction"] = 0.0
                time.sleep(0.01)
                continue

            lx = pm.read_float(local_ptr + POS_X)
            ly = pm.read_float(local_ptr + POS_Y)
            lz = pm.read_float(local_ptr + POS_Z)
            cp = pm.read_float(pitch_addr)
            cy = pm.read_float(yaw_addr)

            best_angle = None
            best_fov   = shared["desired_fov"]
            my_team    = shared["my_team"]
            new_target = None

            for i in range(1, MAX_ENTITIES):
                try:
                    ent_ptr = pm.read_uint(entity_list + i * ENTITY_STRIDE)
                    if not ent_ptr or not is_valid_pointer(pm, ent_ptr):
                        continue
                    if pm.read_int(ent_ptr + TEAM_OFFSET) == my_team:
                        continue
                    if pm.read_int(ent_ptr + HEALTH_OFFSET) <= 0:
                        continue

                    bm_ptr = pm.read_uint(ent_ptr + 0x26A0)
                    if not bm_ptr:
                        continue

                    best_bone  = None
                    highest_bz = -float("inf")
                    for bone_id in [6, 8, 12]:
                        off = bone_id * 0x30
                        bx_ = pm.read_float(bm_ptr + off + 0x0C)
                        by_ = pm.read_float(bm_ptr + off + 0x1C)
                        bz_ = pm.read_float(bm_ptr + off + 0x2C)
                        if bz_ > highest_bz:
                            highest_bz = bz_
                            best_bone  = (bx_, by_, bz_)
                    if not best_bone:
                        continue

                    ex, ey, ez = best_bone
                    ez -= 65.0 + shared["current_pitch_correction"]
                    angle = calc_angle((lx, ly, lz), (ex, ey, ez))
                    fov   = angle_diff(angle, (cp, cy))
                    if fov < best_fov:
                        best_fov   = fov
                        best_angle = angle
                        new_target = ent_ptr
                except:
                    continue

            # Brief cooldown when a target dies
            if target_ptr is not None and target_ptr != new_target:
                try:
                    if pm.read_int(target_ptr + HEALTH_OFFSET) <= 0:
                        cooldown_until = time.time() + 0.3
                        shared["enemy_in_fov"]          = False
                        shared["current_pitch_correction"] = 0.0
                        target_ptr = None
                        time.sleep(0.01)
                        continue
                except:
                    pass

            target_ptr = new_target

            if best_angle and target_ptr:
                shared["enemy_in_fov"] = True
                if shared["last_target_ptr"] != target_ptr or not shared["was_aiming"]:
                    shared["bezier_t"]        = 0.0
                    shared["last_target_ptr"] = target_ptr
                    shared["was_aiming"]      = True
                else:
                    shared["bezier_t"]   = min(1.0, shared["bezier_t"] + shared["correction_speed"])
                    shared["was_aiming"] = False

                sp, sy = bezier_smooth_angle((cp, cy), best_angle, shared["bezier_t"])

                if shared["firing"]:
                    if shared["left_hold_start"] is None:
                        shared["left_hold_start"] = time.time()
                    if time.time() - shared["left_hold_start"] >= 0.05:
                        shared["current_pitch_correction"] = min(
                            shared["current_pitch_correction"] + shared["correction_speed_2"] * shared["recoil"],
                            54.0
                        )
                else:
                    shared["left_hold_start"]        = None
                    shared["current_pitch_correction"] = 0.0

                pm.write_float(pitch_addr, sp - PITCH_CORRECTION_2)
                pm.write_float(yaw_addr,   sy)
            else:
                shared["enemy_in_fov"]          = False
                shared["left_hold_start"]        = None
                shared["current_pitch_correction"] = 0.0

        except Exception as e:
            print(f"[AIMBOT ERROR]: {e}")
            shared["current_pitch_correction"] = 0.0

        time.sleep(0.01)


def triggerbot_thread(pm, client):
    base_address = client + TB_POINTER_BASE
    prev_value   = None

    while shared["running"]:
        try:
            ptr   = tb_resolve_pointer(pm, base_address, TB_OFFSETS)
            value = tb_read_value(pm, ptr, shared["tb_datatype"]) if ptr else None

            sk = shared["tb_active_key"]
            if sk == "RIGHT":
                activate = shared["tb_right_mouse"]
            elif sk == "LEFT":
                activate = shared["tb_left_mouse"]
            elif sk == "MIDDLE":
                activate = shared["tb_middle_mouse"]
            else:
                activate = shared["tb_key_pressed"]

            if not shared["tb_enabled"]:
                shared["tb_status"] = "Disabled"
                prev_value = None
            elif activate:
                if prev_value is None:
                    prev_value = value
                elif value != prev_value:
                    with mouse.Controller() as m:
                        time.sleep(0.02)
                        m.press(mouse.Button.left)
                        time.sleep(0.05)
                        m.release(mouse.Button.left)
                    shared["tb_status"] = f"Fired! {prev_value} -> {value}"
                    prev_value = value
                else:
                    shared["tb_status"] = f"Held - value: {value}"
            else:
                shared["tb_status"] = f"Waiting for {sk} - value: {value}"
                prev_value = None

        except Exception as e:
            shared["tb_status"] = f"Error: {e}"
            time.sleep(1.0)

        time.sleep(0.01)


def combined_hotkey_listener(pm, entity_list):
    def on_click(x, y, button, pressed):
        btn_name = str(button).replace("Button.", "").upper()
        # Aimbot hotkey (mouse)
        if str(button) == shared["hotkey"]:
            shared["aim_active"] = pressed
        # Track left-click for recoil correction
        if str(button) == "Button.left":
            shared["firing"] = pressed
        # Triggerbot mouse keys
        sk = shared["tb_active_key"]
        if btn_name == sk:
            if btn_name == "RIGHT":
                shared["tb_right_mouse"]  = pressed
            elif btn_name == "LEFT":
                shared["tb_left_mouse"]   = pressed
            elif btn_name == "MIDDLE":
                shared["tb_middle_mouse"] = pressed

    def on_press(key):
        # Dormancy probe
        if key == keyboard.Key.f9:
            threading.Thread(target=probe_dormancy,
                             args=(pm, entity_list), daemon=True).start()
        # Aimbot hotkey (keyboard)
        if str(key) == shared["hotkey"]:
            shared["aim_active"] = True
        # Triggerbot keyboard key
        try:
            name = key.char.upper() if (hasattr(key, "char") and key.char) else key.name.upper()
        except AttributeError:
            name = str(key).upper()
        if name == shared["tb_active_key"]:
            shared["tb_key_pressed"] = True

    def on_release(key):
        if str(key) == shared["hotkey"]:
            shared["aim_active"] = False
        try:
            name = key.char.upper() if (hasattr(key, "char") and key.char) else key.name.upper()
        except AttributeError:
            name = str(key).upper()
        if name == shared["tb_active_key"]:
            shared["tb_key_pressed"] = False

    mouse.Listener(on_click=on_click).start()
    keyboard.Listener(on_press=on_press, on_release=on_release).start()


# ─── Window helpers ──────────────────────────────────────────────────────────

def make_window_clickthrough(hwnd):
    GWL_EXSTYLE      = -20
    WS_EX_LAYERED    = 0x80000
    WS_EX_TRANSPARENT = 0x20
    LWA_COLORKEY     = 0x1
    styles = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    styles |= WS_EX_LAYERED | WS_EX_TRANSPARENT
    ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, styles)
    ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0x000000, 0, LWA_COLORKEY)


# ─── Config GUI (Toplevel with tabs) ─────────────────────────────────────────

def build_config_window(root):
    win = tk.Toplevel(root)
    win.title("CS:GO Tool")
    win.geometry("380x560")
    win.resizable(False, False)
    win.configure(bg=NORD["bg"])

    # ttk Style
    style = ttk.Style(win)
    style.theme_use("clam")
    style.configure("TNotebook",
                    background=NORD["bg_light"], borderwidth=0,
                    tabmargins=[0, 2, 0, 0])
    style.configure("TNotebook.Tab",
                    background=NORD["bg_light"], foreground=NORD["fg_dim"],
                    padding=[14, 7], font=("Segoe UI", 9, "bold"), borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", NORD["bg"]), ("active", NORD["bg_mid"])],
              foreground=[("selected", NORD["frost"]), ("active", NORD["fg"])])
    style.configure("TFrame", background=NORD["bg"])
    style.configure("TCombobox",
                    fieldbackground=NORD["bg_mid"], background=NORD["bg_light"],
                    foreground=NORD["fg"], selectbackground=NORD["blue"],
                    selectforeground=NORD["fg_bright"], arrowcolor=NORD["frost"],
                    borderwidth=1, relief="flat")
    style.map("TCombobox",
              fieldbackground=[("readonly", NORD["bg_mid"]), ("focus", NORD["bg_mid"])],
              background=[("active", NORD["bg_light"])],
              foreground=[("disabled", NORD["bg_mid"])])
    win.option_add("*TCombobox*Listbox.background",       NORD["bg_mid"])
    win.option_add("*TCombobox*Listbox.foreground",       NORD["fg"])
    win.option_add("*TCombobox*Listbox.selectBackground", NORD["blue"])
    win.option_add("*TCombobox*Listbox.selectForeground", NORD["fg_bright"])

    # Title bar
    hdr = tk.Frame(win, bg=NORD["bg_light"], height=44)
    hdr.pack(fill="x")
    hdr.pack_propagate(False)
    tk.Frame(hdr, bg=NORD["frost"], width=4).pack(side="left", fill="y")
    tk.Label(hdr, text="  CS:GO TOOL", bg=NORD["bg_light"], fg=NORD["fg_bright"],
             font=("Segoe UI", 11, "bold")).pack(side="left", padx=6, pady=10)
    tk.Label(hdr, text="v1.0  ", bg=NORD["bg_light"], fg=NORD["bg_hover"],
             font=("Segoe UI", 8)).pack(side="right", pady=14)

    nb = ttk.Notebook(win)
    nb.pack(fill="both", expand=True)

    # ── Widget helpers ────────────────────────────────────────────────────
    def _section(parent, text):
        f = tk.Frame(parent, bg=NORD["bg"])
        tk.Label(f, text=text, bg=NORD["bg"], fg=NORD["frost"],
                 font=("Segoe UI", 7, "bold")).pack(side="left")
        tk.Frame(f, bg=NORD["bg_mid"], height=1).pack(
            side="left", fill="x", expand=True, padx=(6, 0), pady=6)
        return f

    def _slider(parent, from_, to, res, cmd=None):
        return tk.Scale(parent, from_=from_, to=to, resolution=res,
                        orient="horizontal", length=310,
                        bg=NORD["bg"], fg=NORD["fg"], troughcolor=NORD["bg_mid"],
                        activebackground=NORD["frost"], highlightthickness=0,
                        sliderrelief="flat", font=("Segoe UI", 8), bd=0,
                        command=cmd)

    def _btn(parent, text, cmd, accent=NORD["blue"]):
        b = tk.Button(parent, text=text, command=cmd,
                      bg=NORD["bg_mid"], fg=NORD["fg"],
                      activebackground=accent, activeforeground=NORD["fg_bright"],
                      relief="flat", bd=0, font=("Segoe UI", 9, "bold"),
                      cursor="hand2", pady=8, padx=12)
        b.bind("<Enter>", lambda e: b.config(bg=accent, fg=NORD["fg_bright"]))
        b.bind("<Leave>", lambda e: b.config(bg=NORD["bg_mid"], fg=NORD["fg"]))
        return b

    PX = {"padx": 16}

    # ── Aimbot tab ────────────────────────────────────────────────────────
    tab_aim = ttk.Frame(nb)
    nb.add(tab_aim, text=" Aimbot ")

    aim_enabled_var = tk.BooleanVar(value=shared["aim_enabled"])
    tk.Checkbutton(tab_aim, text="Enable Aimbot",
                   variable=aim_enabled_var,
                   command=lambda: shared.update({"aim_enabled": aim_enabled_var.get()}),
                   bg=NORD["bg"], fg=NORD["fg"], selectcolor=NORD["bg_mid"],
                   activebackground=NORD["bg"], activeforeground=NORD["fg_bright"],
                   font=("Segoe UI", 10, "bold"), bd=0, highlightthickness=0,
                   cursor="hand2").pack(**PX, pady=(10, 4), anchor="w")
    tk.Frame(tab_aim, bg=NORD["bg_mid"], height=1).pack(fill="x", **PX, pady=(0, 4))

    _section(tab_aim, "RECOIL CONTROL").pack(fill="x", **PX, pady=(12, 0))
    recoil_slider = _slider(tab_aim, 0.0, 80.0, 0.01)
    recoil_slider.set(shared["recoil"])
    recoil_slider.pack(**PX)

    _section(tab_aim, "SMOOTHING").pack(fill="x", **PX, pady=(8, 0))
    smooth_slider = _slider(tab_aim, 0.0, 1.0, 0.01)
    smooth_slider.set(shared["correction_speed"])
    smooth_slider.pack(**PX)

    _section(tab_aim, "FIELD OF VIEW").pack(fill="x", **PX, pady=(8, 0))
    fov_slider = _slider(tab_aim, 1, 30, 0.1,
                         cmd=lambda v: shared.update({"desired_fov": float(v)}))
    fov_slider.set(shared["desired_fov"])
    fov_slider.pack(**PX)

    fov_circle_var = tk.BooleanVar(value=shared["fov_circle"])
    tk.Checkbutton(tab_aim, text="Show FOV Circle",
                   variable=fov_circle_var,
                   command=lambda: shared.update({"fov_circle": fov_circle_var.get()}),
                   bg=NORD["bg"], fg=NORD["fg_dim"], selectcolor=NORD["bg_mid"],
                   activebackground=NORD["bg"], activeforeground=NORD["fg_bright"],
                   font=("Segoe UI", 9), bd=0, highlightthickness=0,
                   cursor="hand2").pack(**PX, pady=(2, 0), anchor="w")

    _section(tab_aim, "HOTKEY").pack(fill="x", **PX, pady=(8, 0))
    hotkey_var     = tk.StringVar(value=shared["hotkey"])
    hotkey_options = ["Key.shift", "Key.ctrl", "Key.alt", "Key.space", "2",
                      "Button.left", "Button.right", "Button.middle"]
    om = tk.OptionMenu(tab_aim, hotkey_var, *hotkey_options,
                       command=lambda v: shared.update({"hotkey": v}))
    om.config(bg=NORD["bg_mid"], fg=NORD["fg"],
              activebackground=NORD["blue"], activeforeground=NORD["fg_bright"],
              highlightthickness=0, relief="flat", font=("Segoe UI", 9), width=24, bd=0)
    om["menu"].config(bg=NORD["bg_mid"], fg=NORD["fg"],
                      activebackground=NORD["blue"], activeforeground=NORD["fg_bright"],
                      relief="flat", bd=0)
    om.pack(**PX, pady=(2, 0), anchor="w")

    tk.Frame(tab_aim, bg=NORD["bg_mid"], height=1).pack(fill="x", **PX, pady=10)

    info_row = tk.Frame(tab_aim, bg=NORD["bg"])
    info_row.pack(**PX, fill="x")
    team_label  = tk.Label(info_row, text=f"Team: {shared['my_team']}",
                           bg=NORD["bg_mid"], fg=NORD["frost"],
                           font=("Segoe UI", 9, "bold"), padx=10, pady=4)
    index_label = tk.Label(info_row, text=f"Index: {shared['local_index']}",
                           bg=NORD["bg_mid"], fg=NORD["teal"],
                           font=("Segoe UI", 9, "bold"), padx=10, pady=4)
    team_label.pack(side="left", padx=(0, 6))
    index_label.pack(side="left")

    def update_aim_labels():
        shared["recoil"]           = recoil_slider.get()
        shared["correction_speed"] = smooth_slider.get()
        team_label.config(text=f"Team: {shared['my_team']}")
        index_label.config(text=f"Index: {shared['local_index']}")
        win.after(300, update_aim_labels)

    update_aim_labels()

    # F1-F6 preset keys
    def f_key_listener():
        def on_press(key):
            try:
                if   key == keyboard.Key.f1: smooth_slider.set(0.2)
                elif key == keyboard.Key.f2: smooth_slider.set(0.3)
                elif key == keyboard.Key.f3: smooth_slider.set(0.5)
                elif key == keyboard.Key.f4: recoil_slider.set(40.0)
                elif key == keyboard.Key.f5: recoil_slider.set(55.0)
                elif key == keyboard.Key.f6: recoil_slider.set(20.0)
            except Exception as e:
                print(f"[F KEY ERROR]: {e}")
        with keyboard.Listener(on_press=on_press) as listener:
            listener.join()

    threading.Thread(target=f_key_listener, daemon=True).start()

    # ── Triggerbot tab ────────────────────────────────────────────────────
    tab_tb = ttk.Frame(nb)
    nb.add(tab_tb, text=" Triggerbot ")

    tb_enabled_var = tk.BooleanVar(value=shared["tb_enabled"])
    tk.Checkbutton(tab_tb, text="Enable Triggerbot",
                   variable=tb_enabled_var,
                   command=lambda: shared.update({"tb_enabled": tb_enabled_var.get()}),
                   bg=NORD["bg"], fg=NORD["fg"], selectcolor=NORD["bg_mid"],
                   activebackground=NORD["bg"], activeforeground=NORD["fg_bright"],
                   font=("Segoe UI", 10, "bold"), bd=0, highlightthickness=0,
                   cursor="hand2").pack(**PX, pady=(10, 4), anchor="w")
    tk.Frame(tab_tb, bg=NORD["bg_mid"], height=1).pack(fill="x", **PX, pady=(0, 4))

    _section(tab_tb, "ACTIVATION KEY").pack(fill="x", **PX, pady=(12, 0))
    tb_key_var = tk.StringVar(value=shared["tb_active_key"])
    tb_key_options = [
        "A","B","C","D","E","F","G","H","I","J","K","L","M",
        "N","O","P","Q","R","S","T","U","V","W","X","Y","Z",
        "SPACE","TAB","CAPS_LOCK","CTRL","LEFT","RIGHT","MIDDLE",
    ]
    tb_key_combo = ttk.Combobox(tab_tb, values=tb_key_options,
                                textvariable=tb_key_var, state="readonly",
                                font=("Segoe UI", 9), width=30)
    tb_key_combo.pack(**PX, pady=(4, 0), anchor="w")
    tb_key_combo.bind("<<ComboboxSelected>>",
                      lambda e: shared.update({"tb_active_key": tb_key_var.get()}))

    _section(tab_tb, "DATA TYPE").pack(fill="x", **PX, pady=(12, 0))
    tb_type_var = tk.StringVar(value=shared["tb_datatype"])
    tb_type_combo = ttk.Combobox(tab_tb, values=["INT", "FLOAT", "DOUBLE", "BYTE"],
                                 textvariable=tb_type_var, state="readonly",
                                 font=("Segoe UI", 9), width=30)
    tb_type_combo.pack(**PX, pady=(4, 0), anchor="w")
    tb_type_combo.bind("<<ComboboxSelected>>",
                       lambda e: shared.update({"tb_datatype": tb_type_var.get()}))

    tk.Frame(tab_tb, bg=NORD["bg_mid"], height=1).pack(fill="x", **PX, pady=14)

    status_bar = tk.Frame(tab_tb, bg=NORD["bg_mid"])
    status_bar.pack(**PX, fill="x")
    tk.Label(status_bar, text=" STATUS ", bg=NORD["blue"], fg=NORD["fg_bright"],
             font=("Segoe UI", 7, "bold"), padx=4).pack(side="left")
    tb_status_label = tk.Label(status_bar, text="Waiting",
                               bg=NORD["bg_mid"], fg=NORD["frost"],
                               font=("Segoe UI", 9), padx=8, pady=4, wraplength=260)
    tb_status_label.pack(side="left", fill="x", expand=True)

    def update_tb_status():
        status = shared["tb_status"]
        color  = NORD["green"] if "Fired" in status else NORD["red"] if "Error" in status else NORD["frost"]
        tb_status_label.config(text=status, fg=color)
        win.after(100, update_tb_status)

    update_tb_status()

    # ── ESP / Team tab ────────────────────────────────────────────────────
    tab_esp = ttk.Frame(nb)
    nb.add(tab_esp, text=" ESP / Team ")

    esp_enabled_var = tk.BooleanVar(value=shared["esp_enabled"])
    tk.Checkbutton(tab_esp, text="Enable ESP",
                   variable=esp_enabled_var,
                   command=lambda: shared.update({"esp_enabled": esp_enabled_var.get()}),
                   bg=NORD["bg"], fg=NORD["fg"], selectcolor=NORD["bg_mid"],
                   activebackground=NORD["bg"], activeforeground=NORD["fg_bright"],
                   font=("Segoe UI", 10, "bold"), bd=0, highlightthickness=0,
                   cursor="hand2").pack(**PX, pady=(10, 4), anchor="w")
    tk.Frame(tab_esp, bg=NORD["bg_mid"], height=1).pack(fill="x", **PX, pady=(0, 4))

    _section(tab_esp, "TEAM MODE").pack(fill="x", **PX, pady=(12, 0))

    _btn(tab_esp, "Auto Detect",
         lambda: shared.update({"team_mode": "auto"}),
         accent=NORD["teal"]).pack(**PX, fill="x", pady=(8, 4))
    _btn(tab_esp, "Manual: Team 2 (CT)",
         lambda: shared.update({"team_mode": "manual2"}),
         accent=NORD["blue"]).pack(**PX, fill="x", pady=4)
    _btn(tab_esp, "Manual: Team 3 (T)",
         lambda: shared.update({"team_mode": "manual3"}),
         accent=NORD["red"]).pack(**PX, fill="x", pady=4)

    # Prevent closing the config window independently
    win.protocol("WM_DELETE_WINDOW", lambda: None)


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    try:
        pm = pymem.Pymem(PROCESS_NAME)
    except Exception as e:
        print(f"[ERROR] Failed to attach to {PROCESS_NAME}: {e}")
        return

    try:
        client = pymem.process.module_from_name(pm.process_handle, CLIENT_DLL).lpBaseOfDll
    except Exception as e:
        print(f"[ERROR] Could not find {CLIENT_DLL}: {e}")
        return

    try:
        stdshader = pymem.process.module_from_name(pm.process_handle, STD_SHADER_DLL).lpBaseOfDll
    except Exception as e:
        print(f"[WARN] {STD_SHADER_DLL} not found – aimbot disabled: {e}")
        stdshader = None

    entity_list = client + ENTITY_LIST_OFFSET

    # Resolve pitch/yaw write addresses for aimbot
    pitch_addr = yaw_addr = None
    if stdshader:
        try:
            pitch_ptr  = pm.read_uint(stdshader + PITCH_BASE_OFFSET)
            pitch_addr = pm.read_uint(pitch_ptr + PTR_OFFSET_1) + PITCH_FINAL_OFFSET
            yaw_ptr    = pm.read_uint(stdshader + YAW_BASE_OFFSET)
            yaw_addr   = pm.read_uint(yaw_ptr   + PTR_OFFSET_1) + YAW_FINAL_OFFSET
        except Exception as e:
            print(f"[WARN] Could not resolve pitch/yaw addresses: {e}")

    # Initial team read
    try:
        lp1 = pm.read_uint(client + 0x0523BCC4)
        lp2 = pm.read_uint(lp1 + 0x8C)
        lp3 = pm.read_uint(lp2 + 0x40)
        shared["my_team"] = pm.read_int(lp3 + 0xB8)
        print(f"[INFO] Initial team: {shared['my_team']}")
    except Exception as e:
        print(f"[WARN] Could not read initial team: {e}")

    # Build ESP overlay (main transparent fullscreen window)
    root = tk.Tk()
    root.attributes("-topmost", True)
    root.attributes("-transparentcolor", "black")
    root.overrideredirect(True)
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{sw}x{sh}+0+0")

    canvas = tk.Canvas(root, width=sw, height=sh, bg="black", highlightthickness=0)
    canvas.pack()
    root.update_idletasks()

    hwnd = ctypes.windll.user32.FindWindowW(None, root.title())
    make_window_clickthrough(hwnd)

    build_config_window(root)

    # Start all background threads
    threading.Thread(target=detect_local_player,
                     args=(pm, client, entity_list),           daemon=True).start()
    threading.Thread(target=update_entity_info,
                     args=(pm, entity_list),                   daemon=True).start()
    threading.Thread(target=radar_thread,
                     args=(pm, entity_list),                   daemon=True).start()
    threading.Thread(target=esp_loop,
                     args=(pm, client, entity_list, canvas, root), daemon=True).start()
    threading.Thread(target=triggerbot_thread,
                     args=(pm, client),                        daemon=True).start()

    if pitch_addr and yaw_addr:
        threading.Thread(target=aimbot_thread,
                         args=(pm, entity_list, pitch_addr, yaw_addr), daemon=True).start()
    else:
        print("[WARN] Aimbot thread not started – pitch/yaw addresses unavailable")

    combined_hotkey_listener(pm, entity_list)  # starts pynput listeners (non-blocking)

    root.mainloop()
    shared["running"] = False


if __name__ == "__main__":
    main()
