#!/usr/bin/env python3

import os
import configparser
import time
import usb.core
import usb.util

CONFIG_FILE = "/etc/antec/sensors.conf"

def load_config():
    """
    Load sensor configuration from the config file if present.
    :return: Dictionary with 'cpu' and 'gpu' configurations or None if file is missing.
    """
    if not os.path.exists(CONFIG_FILE):
        return None

    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    if 'cpu' in config and 'gpu' in config:
        return {
            "cpu": {"sensor": config['cpu']['sensor'], "name": config['cpu']['name']},
            "gpu": {"sensor": config['gpu']['sensor'], "name": config['gpu']['name']}
        }
    return None

def find_temp_file(sensor_name, label_name):
    """
    Locate the temperature input file for a given sensor and label.
    :param sensor_name: Name of the sensor (e.g., 'asusec').
    :param label_name: Name of the temperature label (e.g., 'CPU').
    :return: Path to the temperature input file or None if not found.
    """
    hwmon_base = "/sys/class/hwmon"
    for hwmon in os.listdir(hwmon_base):
        sensor_path = os.path.join(hwmon_base, hwmon)
        name_file = os.path.join(sensor_path, "name")
        if os.path.exists(name_file):
            with open(name_file, "r") as f:
                if f.read().strip() == sensor_name:
                    for temp_file in os.listdir(sensor_path):
                        if temp_file.startswith("temp") and temp_file.endswith("_label"):
                            label_path = os.path.join(sensor_path, temp_file)
                            with open(label_path, "r") as f:
                                if f.read().strip() == label_name:
                                    return label_path.replace("_label", "_input")
    return None

def list_hwmon_sensors():
    """
    List all available hwmon sensors with their names, temperature labels, and current temperatures.
    :return: A dictionary with sensor paths as keys and available labels with temperatures as values.
    """
    sensors = {}
    hwmon_base = "/sys/class/hwmon"
    if not os.path.exists(hwmon_base):
        print(f"No hwmon directory found at {hwmon_base}!")
        return sensors

    for hwmon in os.listdir(hwmon_base):
        sensor_path = os.path.join(hwmon_base, hwmon)
        name_file = os.path.join(sensor_path, "name")
        if os.path.exists(name_file):
            with open(name_file, "r") as f:
                sensor_name = f.read().strip()
            labels = []
            for temp_file in os.listdir(sensor_path):
                if temp_file.startswith("temp") and temp_file.endswith("_label"):
                    label_path = os.path.join(sensor_path, temp_file)
                    temp_input_path = label_path.replace("_label", "_input")
                    try:
                        with open(label_path, "r") as f:
                            label_name = f.read().strip()
                        if os.path.exists(temp_input_path):
                            with open(temp_input_path, "r") as f:
                                temp_value = float(f.read().strip()) / 1000
                        else:
                            temp_value = None
                        labels.append((temp_file.replace("_label", ""), label_name, temp_value))
                    except Exception as e:
                        print(f"Error reading label or temperature: {e}")
            sensors[sensor_path] = {"name": sensor_name, "labels": labels}
    return sensors

def select_sensor(sensors):
    """
    Allow the user to select a sensor and a temperature label.
    :param sensors: A dictionary of available sensors and labels.
    :return: The selected temperature file path.
    """
    print("Available sensors:")
    for idx, (sensor_path, info) in enumerate(sensors.items(), start=1):
        print(f"{idx}: {info['name']} ({sensor_path})")
        for label_idx, (temp_file, label, temp) in enumerate(info["labels"], start=1):
            temp_display = f"{temp:.1f}°C" if temp is not None else "N/A"
            print(f"   {label_idx}: {label} ({temp_file}) - {temp_display}")

    sensor_idx = int(input("\nSelect a sensor (number): ")) - 1
    label_idx = int(input("Select a temperature label (number): ")) - 1
    sensor_path = list(sensors.keys())[sensor_idx]
    temp_file = sensors[sensor_path]["labels"][label_idx][0]
    return os.path.join(sensor_path, f"{temp_file}_input")

def read_temperature(path):
    """
    Read a temperature in millidegrees Celsius from a given file path and convert it to degrees Celsius.
    :param path: Path to the temperature file.
    :return: Temperature in °C (float).
    """
    try:
        with open(path, "r") as f:
            return float(f.read().strip()) / 1000
    except FileNotFoundError:
        print(f"Error: Temperature source not found at {path}!")
        return 0.0

def generate_payload(cpu_temp, gpu_temp):
    """
    Generate the HID payload for the digital display.
    :param cpu_temp: CPU temperature in °C (float).
    :param gpu_temp: GPU temperature in °C (float).
    :return: Payload as a bytes object.
    """
    def encode_temperature(temp):
        integer_part = int(temp // 10)
        tenths_part = int(temp % 10)
        hundredths_part = int((temp * 10) % 10)
        return f"{integer_part:02x}{tenths_part:02x}{hundredths_part:02x}"

    cpu_encoded = encode_temperature(cpu_temp)
    gpu_encoded = encode_temperature(gpu_temp)
    combined_encoded = bytes.fromhex(cpu_encoded + gpu_encoded)
    checksum = (sum(combined_encoded) + 7) % 256
    payload_hex = f"55aa010106{cpu_encoded}{gpu_encoded}{checksum:02x}"
    return bytes.fromhex(payload_hex)

def send_to_device(payload):
    """
    Send the generated payload to the USB device.
    :param payload: Payload as a bytes object.
    """
    VENDOR_ID = 0x2022
    PRODUCT_ID = 0x0522

    device = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if device is None:
        print("Device not found")
        return

    if device.is_kernel_driver_active(0):
        device.detach_kernel_driver(0)
    device.set_configuration()

    cfg = device.get_active_configuration()
    intf = cfg[(0, 0)]

    endpoint = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT,
    )

    if endpoint is None:
        print("Could not find OUT endpoint")
        return

    try:
        endpoint.write(payload)
    except usb.core.USBError as e:
        print(f"Failed to send payload: {e}")

    usb.util.dispose_resources(device)

def main():
    config = load_config()
    if config:
        cpu_path = find_temp_file(config["cpu"]["sensor"], config["cpu"]["name"])
        gpu_path = find_temp_file(config["gpu"]["sensor"], config["gpu"]["name"])
        if not cpu_path or not gpu_path:
            print("Error: Could not find temperature files for sensors specified in the config.")
            return
    else:
        print("No config file found. Falling back to interactive sensor selection.")
        sensors = list_hwmon_sensors()
        if not sensors:
            print("No sensors found!")
            return
        print("\nSelect CPU temperature source:")
        cpu_path = select_sensor(sensors)
        print("\nSelect GPU temperature source:")
        gpu_path = select_sensor(sensors)

    print("\nStarting temperature monitor...")
    while True:
        cpu_temp = read_temperature(cpu_path)
        gpu_temp = read_temperature(gpu_path)
        payload = generate_payload(cpu_temp, gpu_temp)
        send_to_device(payload)
        time.sleep(0.5)

if __name__ == "__main__":
    main()
