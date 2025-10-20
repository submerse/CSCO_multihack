import pymem
import pymem.process
import time

PROCESS_NAME = "csgo.exe"
CLIENT_DLL = "client.dll"

ENTITY_LIST_PTR = 0x04E2520C
ENTITY_STRIDE = 0x8
ENTITY_COUNT = 64
TEAM_OFFSET = 0xEC
HEALTH_OFFSET = 0xF8

# Offset behaviors hardcoded:
FORCE_IF_WRONG = {
    0x0934: 256,
    0x0978: 1,
}
TOGGLE_OFFSETS = {
    0x0A20: 1,
    0x0A24: 1,
}

# Pointer chain for local player team reading (example from your context)
LOCAL_PLAYER_BASE = 0x0523BCC4
LOCAL_PLAYER_OFFSETS = [0x8C, 0x40, 0xB8]


def get_local_team_num(pm, client):
    try:
        local_player = pm.read_uint(client + LOCAL_PLAYER_BASE)
        for offset in LOCAL_PLAYER_OFFSETS[:-1]:
            local_player = pm.read_uint(local_player + offset)
        team_num = pm.read_int(local_player + LOCAL_PLAYER_OFFSETS[-1])
        return team_num
    except:
        return None


def read_entity_list(pm, client):
    base = client + ENTITY_LIST_PTR
    entity_list = []
    for i in range(ENTITY_COUNT):
        try:
            entity_ptr = pm.read_uint(base + i * ENTITY_STRIDE)
            if entity_ptr:
                entity_list.append(entity_ptr)
        except:
            continue
    return entity_list


def force_offsets(pm, entity_list, toggle, my_team_num):
    if my_team_num is None:
        return

    for entity_ptr in entity_list:
        try:
            team = pm.read_int(entity_ptr + TEAM_OFFSET)
            health = pm.read_int(entity_ptr + HEALTH_OFFSET)

            if team == 0 or health <= 0 or team == my_team_num:
                continue

            # Force if wrong offsets:
            for offset, desired_val in FORCE_IF_WRONG.items():
                current_val = pm.read_int(entity_ptr + offset)
                if current_val != desired_val:
                    pm.write_int(entity_ptr + offset, desired_val)

            # Toggle offsets:
            for offset, desired_val in TOGGLE_OFFSETS.items():
                value = desired_val if toggle else 0
                pm.write_int(entity_ptr + offset, value)

        except:
            continue


def radar_loop():
    try:
        pm = pymem.Pymem(PROCESS_NAME)
        client = pymem.process.module_from_name(pm.process_handle, CLIENT_DLL).lpBaseOfDll
    except Exception as e:
        print(f"[ERROR] {e}")
        return

    toggle = False
    while True:
        try:
            my_team_num = get_local_team_num(pm, client)
            entity_list = read_entity_list(pm, client)
            force_offsets(pm, entity_list, toggle, my_team_num)
            toggle = not toggle
            time.sleep(0.05)
        except Exception as e:
            print(f"[ERROR] {e}")
            break


if __name__ == "__main__":
    radar_loop()
