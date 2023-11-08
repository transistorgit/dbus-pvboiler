import minimalmodbus
from time import sleep


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

    self.registers = {
      # name        : nr , format, factor, unit
      "Active Power": [3004, 'U32', 1, 'W', 0],
      "Energy Today": [3015, 'U16', 1, 'kWh', 0],
      "Energy Total": [3008, 'U32', 1, 'kWh', 0],
      "A phase Voltage": [3033, 'U16', 1, 'V', 0],
      "B phase Voltage": [3034, 'U16', 1, 'V', 0],
      "C phase Voltage": [3035, 'U16', 1, 'V', 0],
      "A phase Current": [3036, 'U16', 1, 'A', 0],
      "B phase Current": [3037, 'U16', 1, 'A', 0],
      "C phase Current": [3038, 'U16', 1, 'A', 0],
    }


  def read_registers(self):
    for key, value in self.registers.items():
        factor = value[2]
        for _ in range(3):
          try:
            if value[1] == 'U32':
              value[4]= self.bus.read_long(value[0],4) * factor
            else:
              value[4] = self.bus.read_register(value[0],1,4) * factor
            break
          except minimalmodbus.ModbusException:
            value[4]= 0
            pass # igonore sporadic checksum or noreply errors but raise others
        sleep(0.004)  # modbus delay

        # print(f"{key}: {value[-1]} {value[-2]}")
    return self.registers


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
    
  # Set power limit absolute value. 0 or >=rated_power is OFF/no limit
  def set_power_limitation_absolute(self, limit_watt=0):
    #print(f"Power Limitation switch: {'ON' if self.bus.read_register(3069)==0xAA else 'OFF'}")
    #print(f"Power limitation before: {self.bus.read_register(3051)/100}%")
    #print(f"Limit power actual value before : {self.bus.read_register(3080)*10}W")

    current_limit = self.bus.read_register(3080)*10
    if limit_watt == current_limit:
      return # nothing to do

    if limit_watt >0 and limit_watt<self.rated_power:
      self.bus.write_register(3069, 0xAA)
      self.bus.write_register(3080, int(limit_watt / 10))
    else:
      self.bus.write_register(3080, self.rated_power / 10) # set to rated power = limit off
      self.bus.write_register(3069, 0x55)
    #print(f"Power limitation after: {self.bus.read_register(3051)/100}%")
    #print(f"Limit power actual value after : {self.bus.read_register(3080)*10}W")

