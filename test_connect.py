import os
import sys
import time

os.environ["CYCLONEDDS_URI"] = "file://config/dds.xml"
sys.path.append(os.path.abspath("./build"))

from highcontrol_py import HighController

ctrl = HighController.instance()
ok = ctrl.init()
print("init:", ok)

received = []

def on_status(status):
    if len(received) < 3:
        bms_attrs = [a for a in dir(status.bms_data) if not a.startswith("_")]
        received.append((
            status.workmode,
            {k: getattr(status.bms_data, k) for k in bms_attrs},
            len(status.motor_data),
        ))

ctrl.subscribe_robot_hardware_status(on_status)
time.sleep(3)
print("received_count:", len(received), received[:3])
