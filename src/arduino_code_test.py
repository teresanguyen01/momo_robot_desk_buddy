import serial
import time

arduino = serial.Serial('/dev/ttyACM0', 9600, timeout=1)
time.sleep(2)

arduino.write(b"45\n")
time.sleep(1)

arduino.write(b"90\n")
time.sleep(1)

arduino.write(b"135\n")
time.sleep(1)

arduino.close()