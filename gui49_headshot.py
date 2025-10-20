import pymem
import pymem.process
import math
import time
import keyboard
import threading
import ctypes
from tkinter import *

PROCESS_NAME = "csgo.exe"
CLIENT_DLL = "client.dll"

# Offsets
ENTITY_LIST = 0x04E051DC
VIEW_ANGLE_X = 0x106C
VIEW_ANGLE_Y = 0x1070
LOCAL_PLAYER_PTR = 0x052BF6D8
TEAM_NUM = 0xF4
HEALTH = 0x100
DORMANT = 0xED
POS_X = 0x2AC
POS_Y = 0x2B0
POS_Z = 0x2B4
BONE_MATRIX = 0x26A8  # Bone matrix offset (test)

# Config
AIM_KEY = "shift"
SMOOTHING = 3.5
FOV = 4.0

# Bone index for head
HEAD_BONE_INDEX = 8

# Helper functions
def get_distance(x1, y1, z1, x2, y2, z2):
    return math.sqrt((x1 - x2)**2 + (y1 - y2)**2 + (z1 - z2)**2)

def calc_angle(src, dst):
    dx = dst[0] - src[0]
    dy = dst[1] - src[1]
    dz = dst[2] - src[2]
    hyp = math.sqrt(dx * dx + dy * dy)
    pitch = math.degrees(math.atan2(-dz, hyp))
    yaw = math.degrees(math.atan2(dy, dx))
    return pitch, yaw

def smooth_angle(current, target, smoothing):
    pitch_diff = target[0] - current[0]
    yaw_diff = target[1] - current[1]
    return (
        current[0] + pitch_diff / smoothing,
        current[1] + yaw_diff / smoothing
    )

def get_bone_position(pm, ent_ptr, bone_index):
    bone_matrix_base = pm.read_uint(ent_ptr + BONE_MATRIX)
    if not bone_matrix_base:
        return None

    x = pm.read_float(bone_matrix_base + bone_index * 0x30 + 0x0C)
    y = pm.read_float(bone_matrix_base + bone_index * 0x30 + 0x1C)
    z = pm.read_float(bone_matrix_base + bone_index * 0x30 + 0x2C)
    return (x, y, z)

def get_best_target(pm, client, local_player):
    local_team = pm.read_int(local_player + TEAM_NUM)
    local_x = pm.read_float(local_player + POS_X)
    local_y = pm.read_float(local_player + POS_Y)
    local_z = pm.read_float(local_player + POS_Z)
    local_pos = (local_x, local_y, local_z)

    best_fov = FOV
    best_target = None

    for i in range(1, 32):
        ent_ptr = pm.read_uint(client + ENTITY_LIST + i * 0x10)
        if not ent_ptr:
            continue

        team = pm.read_int(ent_ptr + TEAM_NUM)
        health = pm.read_int(ent_ptr + HEALTH)
        dormant = pm.read_bool(ent_ptr + DORMANT)

        if team == local_team or health <= 0 or dormant:
            continue

        bone_pos = get_bone_position(pm, ent_ptr, HEAD_BONE_INDEX)
        if not bone_pos:
            continue

        dx = bone_pos[0] - local_x
        dy = bone_pos[1] - local_y
        dz = bone_pos[2] - local_z
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)

        pitch, yaw = calc_angle(local_pos, bone_pos)
        fov_to_enemy = math.sqrt((pitch)**2 + (yaw)**2)

        if fov_to_enemy < best_fov:
            best_fov = fov_to_enemy
            best_target = (pitch, yaw)

    return best_target

def aimbot_loop():
    pm = pymem.Pymem(PROCESS_NAME)
    client = pymem.process.module_from_name(pm.process_handle, CLIENT_DLL).lpBaseOfDll

    while True:
        if not keyboard.is_pressed(AIM_KEY):
            time.sleep(0.01)
            continue

        local_player = pm.read_uint(client + LOCAL_PLAYER_PTR)
        if not local_player:
            continue

        view_angle_pitch = pm.read_float(local_player + VIEW_ANGLE_X)
        view_angle_yaw = pm.read_float(local_player + VIEW_ANGLE_Y)
        current_angle = (view_angle_pitch, view_angle_yaw)

        target_angle = get_best_target(pm, client, local_player)
        if target_angle:
            smoothed = smooth_angle(current_angle, target_angle, SMOOTHING)
            pm.write_float(local_player + VIEW_ANGLE_X, smoothed[0])
            pm.write_float(local_player + VIEW_ANGLE_Y, smoothed[1])

        time.sleep(0.005)

# Start aimbot in thread
aimbot_thread = threading.Thread(target=aimbot_loop)
aimbot_thread.start()

# GUI placeholder
root = Tk()
root.title("Aimbot Running")
label = Label(root, text="Aimbot is running. Hold ALT to aim.")
label.pack(padx=20, pady=20)
root.mainloop()
