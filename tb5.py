import pymem
import pymem.process
import time
import threading
import tkinter as tk
from tkinter import ttk
from pynput import keyboard, mouse

# ----------- Memory reading setup --------------
pm = pymem.Pymem("Csgo.exe")  # Change this to your game exe name
client_dll = pymem.process.module_from_name(pm.process_handle, "client.dll").lpBaseOfDll

# Your pointer chain offsets
POINTER_BASE = 0x00DEF97C
OFFSETS = [0x24, 0x4, 0x68]

import struct

def read_ptr(address):
    data = pm.read_bytes(address, 4)
    ptr = struct.unpack("<I", data)[0]
    return ptr

def resolve_pointer(base, offsets):
    try:
        addr = read_ptr(base)
        #print(f"[DEBUG] Base pointer read from {hex(base)}: {hex(addr)}")
    except Exception as e:
        print(f"[ERROR] Could not read base pointer at {hex(base)}: {e}")
        return None

    for i, offset in enumerate(offsets[:-1]):
        try:
            read_addr = addr + offset
            addr = read_ptr(read_addr)
            #print(f"[DEBUG] Step {i+1} - Read pointer at {hex(read_addr)}: {hex(addr)}")
            if addr == 0:
                print(f"[ERROR] Null pointer detected at step {i+1}, address {hex(read_addr)}")
                return None
        except Exception as e:
            print(f"[ERROR] Could not read pointer at step {i+1} offset {hex(offset)} ({hex(read_addr)}): {e}")
            return None

    final_addr = addr + offsets[-1]
   # print(f"[DEBUG] Final address calculated: {hex(final_addr)}")
    return final_addr


def read_value(address, datatype):
    try:
        if datatype == 'INT':
            return pm.read_int(address)
        elif datatype == 'FLOAT':
            return pm.read_float(address)
        elif datatype == 'DOUBLE':
            return pm.read_double(address)
        elif datatype == 'BYTE':
            return pm.read_bytes(address, 1)[0]
        else:
            return None
    except Exception as e:
        return None

# ----------- Input Handling --------------------

# State vars
key_pressed = False
right_mouse_pressed = False
left_mouse_pressed = False

def on_key_press(key):
    global key_pressed
    try:
        if hasattr(key, 'char') and key.char is not None:
            key_name = key.char.upper()
        else:
            key_name = key.name.upper()
    except AttributeError:
        key_name = str(key).upper()

    if key_name == selected_key.get():
        key_pressed = True

def on_key_release(key):
    global key_pressed
    try:
        if hasattr(key, 'char') and key.char is not None:
            key_name = key.char.upper()
        else:
            key_name = key.name.upper()
    except AttributeError:
        key_name = str(key).upper()

    if key_name == selected_key.get():
        key_pressed = False

def on_click(x, y, button, pressed):
    global right_mouse_pressed, left_mouse_pressed
    btn = str(button).replace('Button.', '').upper()
    if btn == selected_key.get():
        if btn == 'RIGHT':
            right_mouse_pressed = pressed
        elif btn == 'LEFT':
            left_mouse_pressed = pressed

# ----------- GUI Setup ------------------------

root = tk.Tk()
root.title("Activation Key Selector & Auto Fire")

selected_key = tk.StringVar(value="CAPS_LOCK")
selected_datatype = tk.StringVar(value="FLOAT")

label_key = tk.Label(root, text="Select Activation Key:")
label_key.pack(pady=5)

# Common keys + mouse buttons
key_options = [
    'A','B','C','D','E','F','G','H','I','J','K','L','M','N','O','P','Q','R','S','T','U','V','W','X','Y','Z',
    'SPACE','TAB','CAPS_LOCK','CTRL','LEFT','RIGHT','MIDDLE'
]

combo_key = ttk.Combobox(root, values=key_options, textvariable=selected_key, state="readonly")
combo_key.pack(pady=5)

label_type = tk.Label(root, text="Select Data Type to Read:")
label_type.pack(pady=5)

datatype_options = ['INT', 'FLOAT', 'DOUBLE', 'BYTE']
combo_type = ttk.Combobox(root, values=datatype_options, textvariable=selected_datatype, state="readonly")
combo_type.pack(pady=5)

status_label = tk.Label(root, text="Status: Waiting", fg="blue")
status_label.pack(pady=5)

def update_status(text, color="blue"):
    status_label.config(text="Status: " + text, fg=color)

# ----------- Main loop -------------------------

def fire_click():
    with mouse.Controller() as m:
        time.sleep(0.02)
        m.press(mouse.Button.left)
        time.sleep(0.05)
        m.release(mouse.Button.left)
        time.sleep(0.002)

def main_loop():
    global key_pressed, right_mouse_pressed, left_mouse_pressed
    base_address = client_dll + POINTER_BASE

    # Start listeners
    keyboard_listener = keyboard.Listener(on_press=on_key_press, on_release=on_key_release)
    keyboard_listener.start()

    mouse_listener = mouse.Listener(on_click=on_click)
    mouse_listener.start()

    update_status(f"Waiting for {selected_key.get()} press...")

    previous_value = None  # Track last read value

    while True:
        try:
            pointer_addr = resolve_pointer(base_address, OFFSETS)
            datatype = selected_datatype.get()
            value = read_value(pointer_addr, datatype)

            activate = False
            sk = selected_key.get()

            if sk in ['LEFT', 'RIGHT', 'MIDDLE']:
                if sk == 'RIGHT' and right_mouse_pressed:
                    activate = True
                elif sk == 'LEFT' and left_mouse_pressed:
                    activate = True
            else:
                if key_pressed:
                    activate = True

            # Fire only if activated and value changed
            if activate:
                if previous_value is None:
                    previous_value = value  # Initialize
                elif value != previous_value:
                    fire_click()
                    update_status(f"Firing! Value changed from {previous_value} to {value}", color="green")
                    previous_value = value
                else:
                    update_status(f"No change in value: {value}")
            else:
                update_status(f"Waiting for {sk} press... Value: {value}")
                previous_value = None  # Reset when not active to detect fresh changes next time

            time.sleep(0.01)
        except Exception as e:
            update_status(f"Error: {e}", color="red")
            time.sleep(1)

# Run main loop in a thread so GUI remains responsive
threading.Thread(target=main_loop, daemon=True).start()

root.mainloop()
