import minimalmodbus
from time import sleep
from typing import Tuple


'''Solis S5 Inverter Interface'''
class s5_inverter:
  def __init__(self, instrument: minimalmodbus.Instrument, rated_power=6000):
    self._dbusservice = []
    self.rated_power = rated_power
    self.bus = instrument

    #use serial number production code to detect solis inverters
    ser = self.read_serial()
    if not self.check_production_date(ser):
      raise RuntimeError("Unknown Device")

   
  #returns kWh
  def read_energy_today(self):
    try:
      return self.bus.read_register(3015,1,4)
    except minimalmodbus.ModbusException:
      return 0

  # returns V, A for phase 1-3
  def read_phase(self, phase_no: int) -> Tuple[float, float]:
    if phase_no<1 or phase_no>3:
      return 0, 0
    try:
      voltage = self.bus.read_register(3032 + phase_no,1,4)
      current = self.bus.read_register(3035 + phase_no,1,4)
      return voltage, current
    except minimalmodbus.ModbusException:
      return 0

  #returns kWh
  def read_energy_total(self):
    try:
      return self.bus.read_long(3008,4)
    except minimalmodbus.ModbusException:
      return 0

  #returns W
  def read_active_power(self):
    try:
      return self.bus.read_long(3004,4)
    except minimalmodbus.ModbusException:
      return 0

  def read_status(self):
    for _ in range(3):
      try:
        status = int(self.bus.read_register(3043, 0, 4))
        return status
      except minimalmodbus.ModbusException:
        pass # igonore sporadic checksum or noreply errors but raise others
    
    # print(f'Inverter Status: {status:04X}') # 0 waiting, 3 generating
    return 0


  def _to_little_endian(self, b):
    return (b&0xf)<<12 | (b&0xf0)<<4 | (b&0xf00)>>4 | (b&0xf000)>>12


  def read_serial(self):
    for _ in range(6):
      try:
        serial = {}
        serial["Inverter SN_1"] = self._to_little_endian(int(self.bus.read_register(3060, 0, 4)))
        serial["Inverter SN_2"] = self._to_little_endian(int(self.bus.read_register(3061, 0, 4)))
        serial["Inverter SN_3"] = self._to_little_endian(int(self.bus.read_register(3062, 0, 4)))
        serial["Inverter SN_4"] = self._to_little_endian(int(self.bus.read_register(3063, 0, 4)))
        serial_str = f'{serial["Inverter SN_1"]:04X}{serial["Inverter SN_2"]:04X}{serial["Inverter SN_3"]:04X}{serial["Inverter SN_4"]:04X}'
        return serial_str
      except minimalmodbus.ModbusException as e:
        print(e)
        sleep(1)
        pass
    return ''


  def read_type(self):
    try:
      return f'{self._to_little_endian(int(self.bus.read_register(2999, 0, 4))):04X}'
    except minimalmodbus.ModbusException:
      return ''

    
  def read_dsp_version(self):
    try:
      return f'{self._to_little_endian(int(self.bus.read_register(3000, 0, 4))):04X}'
    except minimalmodbus.ModbusException:
      return ''
    

  def read_lcd_version(self):
    try:
      return f'{self._to_little_endian(int(self.bus.read_register(3001, 0, 4))):04X}'
    except minimalmodbus.ModbusException:
      return ''


  def check_production_date(self, serial):
    try:
      year = int(serial[7:9])
      month = int(serial[9:10],16)
      day = int(serial[10:12])
      #print(f'{year}/{month}/{day}')
      if year>20 and year<30 and month<=12 and day<=31:
        return True
    except:
      return False
    return False
    
