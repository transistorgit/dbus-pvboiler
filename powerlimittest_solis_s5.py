#!/usr/bin/env python3

"""
"""
import logging
import sys
import os
import minimalmodbus


path_UpdateIndex = '/UpdateIndex'

class s5_inverter:
  def __init__(self, port="/dev/ttyUSB0"):
    self._dbusservice = []
    self.bus = minimalmodbus.Instrument(port, slaveaddress=1)
    self.bus.serial.baudrate = 9600
    self.bus.serial.timeout = 0.3

    print(f"Modbus on port {port}")

    '''
       "DC Voltage 1": [3021, 'U16', 1, 'V'],
      "DC Current 1": [3022, 'U16', 1, 'A'],
      "DC Voltage 2": [3023, 'U16', 1, 'V'],
      "DC Current 2": [3024, 'U16', 1, 'A'],
      "DC Voltage 3": [3025, 'U16', 1, 'V'],
      "DC Current 3": [3026, 'U16', 1, 'A'],
      "DC Voltage 4": [3027, 'U16', 1, 'V'],
      "DC Current 4": [3028, 'U16', 1, 'A'],
      "Inverter temperature": [3041, 'U16', 1, '°C'],
      "Grid frequency": [3042, 'U16', 0.1, 'Hz'],
      "Reactive Power": [3055, 'U32', 1, 'Var'],
      "Apparent Power": [3057, 'U32', 1, 'VA'],
      
      '''

    self.registers = {
      # name        : nr , format, factor, unit
      "Active Power": [3004, 'U32', 1, 'W', None],
      "Energy Today": [3015, 'U16', 1, 'kWh', None],
      "Energy Total": [3008, 'U32', 1, 'kWh', None],
      "A phase Voltage": [3033, 'U16', 1, 'V', None],
      "B phase Voltage": [3034, 'U16', 1, 'V', None],
      "C phase Voltage": [3035, 'U16', 1, 'V', None],
      "A phase Current": [3036, 'U16', 1, 'A', None],
      "B phase Current": [3037, 'U16', 1, 'A', None],
      "C phase Current": [3038, 'U16', 1, 'A', None],
      "Solis Type": [2999, 'U16', 1, '', None],
      "Limit power actual value": [3080, 'S16', 0.1, 'W', None],
      "Power limitation switch": [3069, 'U16', 1, '', None],
      "Power limitation": [3051, 'U16', 0.01, '%', None],
      "Night ON/OFF": [3006, 'U16', 1, '', None],
    }


  def read_registers(self):
    for key, value in self.registers.items():
        factor = value[2]
        if value[1] == 'U32':
          value[4] =  self.bus.read_long(value[0],4) * factor
        else:
          value[4] = self.bus.read_register(value[0],1,4) * factor
        print(f"{key}: {value[-1]} {value[-2]}")
    return self.registers

  def read_status(self):
    status = int(self.bus.read_register(3043, 0, 4))
    print(f'Inverter Status: {status:04X}')
    return status

  def read_power_limitation(self):
    print(f"Power Limitation switch: {'ON' if self.bus.read_register(3069)==0xAA else 'OFF'}")
    print(f"Limit power actual value: {self.bus.read_register(3080)*10}W")
    print(f"Power limitation: {self.bus.read_register(3051)/100}%")

  # Set power limit percentual value. 0 or >=110 is OFF/no limit 
  # absolute limit is only adjusted if limit is set to 110%/disabled
  def set_power_limitation_percent(self, limit_percent):
    #print(f"Power Limitation switch: {'ON' if self.bus.read_register(3069)==0xAA else 'OFF'}")
    #print(f"Limit power actual value: {self.bus.read_register(3080)*10}W")
    #print(f"Power limitation before: {self.bus.read_register(3051)/100}%")
    #print(f"Power limitation before: 0x{self.bus.read_register(3051):02X}")
    if limit_percent >0 and limit_percent<110:
      self.bus.write_register(3069, 0xAA)
      self.bus.write_register(3051, int(limit_percent * 100))
    else:
      self.bus.write_register(3051, 0x2AF8) # 110% default = limit off
      self.bus.write_register(3069, 0x55)

    #reset absolute limit
    #self.bus.write_register(3080, 6000) # default = limit off
    #print(f"Power limitation after: 0x{self.bus.read_register(3051):02X}")
    print(f"Power limitation after: {self.bus.read_register(3051)/100}%")
    print(f"Limit power actual value after : {self.bus.read_register(3080)*10}W")
    #print(f"Power Limitation switch: {'ON' if self.bus.read_register(3069)==0xAA else 'OFF'}")

  # Set power limit absolute value. 0 or >=6000 is OFF/no limit
  def set_power_limitation_absolute(self, limit_watt):
    #print(f"Power Limitation switch: {'ON' if self.bus.read_register(3069)==0xAA else 'OFF'}")
    #print(f"Power limitation before: {self.bus.read_register(3051)/100}%")
    #print(f"Limit power actual value before : {self.bus.read_register(3080)*10}W")
    #print(f"Limit power actual value before: 0x{self.bus.read_register(3080):02X} *10W")
    if limit_watt >0 and limit_watt<6000:
      self.bus.write_register(3069, 0xAA)
      self.bus.write_register(3080, int(limit_watt / 10))
    else:
      self.bus.write_register(3080, 600) # default 6000W = limit off
      self.bus.write_register(3069, 0x55)

    # reset percentual limit
    #self.bus.write_register(3051, 0x2AF8) # 110% default = limit off
    print(f"Limit power actual value after : {self.bus.read_register(3080)*10}W")
    #print(f"Limit power actual value after: 0x{self.bus.read_register(3080):02X} *10W")
    print(f"Power limitation after: {self.bus.read_register(3051)/100}%")
    #print(f"Power Limitation switch: {'ON' if self.bus.read_register(3069)==0xAA else 'OFF'}")

  def set_night_mode(self, on=True):
    print(f"Night Mode reg: {self.bus.read_register(3006):02X}")
    self.bus.write_register(3006, 0xBE)
    print(f"Night Mode reg: {self.bus.read_register(3006):02X}")

  def _to_little_endian(self, b):
    return (b&0xf)<<12 | (b&0xf0)<<4 | (b&0xf00)>>4 | (b&0xf000)>>12


  def read_serial(self):
    serial = {}
    serial["Inverter SN_1"] = self._to_little_endian(int(self.bus.read_register(3060, 0, 4)))
    serial["Inverter SN_2"] = self._to_little_endian(int(self.bus.read_register(3061, 0, 4)))
    serial["Inverter SN_3"] = self._to_little_endian(int(self.bus.read_register(3062, 0, 4)))
    serial["Inverter SN_4"] = self._to_little_endian(int(self.bus.read_register(3063, 0, 4)))
    serial_str = f'{serial["Inverter SN_1"]:04X}{serial["Inverter SN_2"]:04X}{serial["Inverter SN_3"]:04X}{serial["Inverter SN_4"]:04X}'
    return serial_str
    

  def read_type(self):
    return f'{self._to_little_endian(int(self.bus.read_register(2999, 0, 4))):04X}'
    
  def read_dsp_version(self):
    return f'{self._to_little_endian(int(self.bus.read_register(3000, 0, 4))):04X}'
    
  def read_lcd_version(self):
    return f'{self._to_little_endian(int(self.bus.read_register(3001, 0, 4))):04X}'
    
  def check_prodcution_date(self, serial):
    try:
      year = int(serial[7:9])
      month = int(serial[9:10],16)
      day = int(serial[10:12])
      print(f'{year}/{month}/{day}')
      if year>20 and year<30 and month<=12 and day<=31:
        return True
    except:
      return False


def main():
  logging.basicConfig(level=logging.DEBUG) # use .INFO for less logging

inv = s5_inverter(sys.argv[1] if len(sys.argv)>1 else "/dev/ttyUSB0")
#inv.read_registers()
#inv.read_status()
print("Serial: " + inv.read_serial())
#print("Type: " + inv.read_type())
#print(f"Date check {inv.check_prodcution_date(inv.read_serial())}")
#inv.set_night_mode()

#inv.read_power_limitation()
print("Limit to 90%")
inv.set_power_limitation_percent(90)
print("Limit to 6000W")
inv.set_power_limitation_absolute(6000)
print("Limit to 100%")
inv.set_power_limitation_percent(100)
print("Limit to 5900W")
inv.set_power_limitation_absolute(5900)
print("No Limit %")
inv.set_power_limitation_percent(0)
print("Limit to 100%")
inv.set_power_limitation_percent(100)
#inv.read_power_limitation()


if __name__ == "__main__":
  main()
