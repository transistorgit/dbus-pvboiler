#!/usr/bin/env python

"""
"""
from gi.repository import GLib as gobject
import platform
import logging
import sys
import os
import dbus
import _thread as thread
import minimalmodbus
from time import sleep
from datetime import datetime as dt
from datetime import timedelta
from threading import Thread
import paho.mqtt.client as mqtt

# our own packages

sys.path.insert(1, os.path.join(os.path.dirname(__file__), "/opt/victronenergy/dbus-systemcalc-py/ext/velib_python",),)
from vedbus import VeDbusService
from dbusmonitor import DbusMonitor
from settingsdevice import SettingsDevice  # available in the velib_python repository

VERSION = 0.2
SERVER_ADDRESS_BOILER = 33  # Modbus ID of the Water Heater Device
SERVER_ADDRESS_INVERTER = 1  # Modbus ID of the PV Inverter
BAUDRATE = 9600
GRIDMETER_KEY_WORD = 'com.victronenergy.grid'
MINIMUM_SWITCH_TIME = 60  # shortest allowed time between boiler switching actions

Broker_Address = '192.168.168.112'
InverterType = 'pvboiler'
Topics = {
    'pvpower':'iot/pv/solis/ac_active_power_kW',
    'pvpowerlimit':'iot/pv/solis/powerlimit',
    'status':'iot/pv/boiler/service',
    'heaterpower':'iot/pv/boiler/power',
    'heatertemperature':'iot/pv/boiler/temperature',
    'heatertargettemperature':'iot/pv/boiler/targettemperature',
    'heartbeat':'iot/pv/boiler/heartbeat'
    }

path_UpdateIndex = '/UpdateIndex'

class UnknownDeviceException(Exception):
  '''Exception to report that no Solis S5 Type inverter was found'''


class WaterHeater:
  def __init__(self, instrument: minimalmodbus.Instrument):
    self._dbusservice = []
    self.instrument = instrument

    self.registers = {
      "Power_500W": 0,
      "Power_1000W": 1,
      "Power_2000W": 2,
      "Temperature": 0,
      "Heartbeat_Return" : 1,
      "Power_Return": 2,
      "Device_Type": 3,
      "Operation_Mode": 4,  # AUTO/FORCE ON
      "Heartbeat": 0
    }

    self.powersteps =    [(-1000000, 499), (500, 999), (1000, 1499), (1500, 1999), (2000, 2499), (2500, 2999), (3000, 3499), (3500, 1000000)]
    self.powercommands = [[0, 0, 0],       [1, 0, 0],  [0, 1, 0],    [1, 1, 0],    [0, 0, 1],    [1, 0, 1],    [0, 1, 1],    [1, 1, 1]]
    self.lasttime_switched = dt.now() - timedelta(seconds=MINIMUM_SWITCH_TIME)
    self.target_temperature = 50  # °C
    self.current_temperature = None
    self.current_power = None
    self.status = None  # 0 Auto, 1 FORCE ON
    self.heartbeat = 0
    self.Device_Type = 0xE5E1
    self.exception_counter = 0
    self.Max_Retries = 10
    self.last_grid_surplus = 0
    self.cmd_bits = [0, 0, 0]


  def check_device_type(self):
    maxtries = 3
    tried = 0
    found_type = 0
    for _ in range(maxtries):
      try:
        tried += 1
        found_type = self.instrument.read_register(self.registers["Device_Type"], 0, 4)
      except Exception as e:
        logging.warning(f'Water Heater check type: {str(e)})')
        if tried >= maxtries:
          raise e
        sleep(1)
        continue
      
      if found_type == self.Device_Type:
        logging.info(f'Found Water Heater (type: {found_type:X})')
        return
    raise UnknownDeviceException
    

  def calc_powercmd(self, grid_surplus):
    res = None
    for idx in (idx for idx, (sec, fir) in enumerate(self.powersteps) if sec <= grid_surplus <= fir):
      res = idx
    return self.powercommands[res]
  

  def operate(self, grid_surplus):
    # needs to be called regularly (e.g. 1/s) to update the heartbeat

    try:
      self.instrument.write_register(self.registers["Heartbeat"], self.heartbeat, 0, 16)
      self.heartbeat += 1
      if self.heartbeat >= 100:  # must be below 1000 for the server to work 
          self.heartbeat = 0

      # switch to apropriate power level, if last switching incident is longer than the allowed minimum time ago
      # short delay for small steps, long delay for steps>500W
      powerstep = abs(grid_surplus - self.last_grid_surplus)
      if powerstep<=500:
        if (dt.now() - self.lasttime_switched).total_seconds() >= MINIMUM_SWITCH_TIME/10:
          self.cmd_bits = self.calc_powercmd(grid_surplus)  # calculate power setting depending on energy surplus
          self.lasttime_switched = dt.now()
      else:
        if (dt.now() - self.lasttime_switched).total_seconds() >= MINIMUM_SWITCH_TIME:
          self.cmd_bits = self.calc_powercmd(grid_surplus)  # calculate power setting depending on energy surplus
          self.lasttime_switched = dt.now()

      # but stop heating if target temperature is reached
      self.current_temperature = self.instrument.read_register(self.registers["Temperature"], 2, 4)
      if self.current_temperature >= self.target_temperature:
        self.cmd_bits = [0, 0, 0]

      self.instrument.write_bits(self.registers["Power_500W"], self.cmd_bits)
      self.last_grid_surplus = grid_surplus
          
      self.current_power = self.instrument.read_register(self.registers["Power_Return"], 0, 4)
      self.status = self.instrument.read_register(self.registers["Operation_Mode"], 0, 4)     
      self.exception_counter = 0  # reset counter after successful access

    except Exception as e:
      logging.info(e)
      if self.exception_counter >= self.Max_Retries:
        self.exception_counter = 0
        logging.critical("Water Heater critical error, exiting")
        sys.exit(6)
      self.exception_counter += 1
    

class s5_inverter:
  def __init__(self, instrument: minimalmodbus.Instrument):
    self._dbusservice = []
    self.bus = instrument

    #use serial number production code to detect solis inverters
    ser = self.read_serial()
    if not self.check_production_date(ser):
      raise UnknownDeviceException

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
    
  # Set power limit absolute value. 0 or >=6000 is OFF/no limit
  def set_power_limitation_absolute(self, limit_watt=6000):
    #print(f"Power Limitation switch: {'ON' if self.bus.read_register(3069)==0xAA else 'OFF'}")
    #print(f"Power limitation before: {self.bus.read_register(3051)/100}%")
    #print(f"Limit power actual value before : {self.bus.read_register(3080)*10}W")

    current_limit = self.bus.read_register(3080)*10
    if limit_watt == current_limit:
      return # nothing to do

    if limit_watt >0 and limit_watt<6000:
      self.bus.write_register(3069, 0xAA)
      self.bus.write_register(3080, int(limit_watt / 10))
    else:
      self.bus.write_register(3080, 600) # default 6000W = limit off
      self.bus.write_register(3069, 0x55)
    #print(f"Power limitation after: {self.bus.read_register(3051)/100}%")
    #print(f"Limit power actual value after : {self.bus.read_register(3080)*10}W")


class DbusPvBoilerService:
  def __init__(self, port, servicename, deviceinstance=288, productname='PV Boiler', connection='unknown', 
               topics='/my/pv/inverter', broker_address = '127.0.0.1'):
    try:
      self.broker_address = broker_address
      self.is_connected = False
      self.is_online = False
      self.topics = topics
      self.client = mqtt.Client('Venus_PV_Boiler') 
      self.client.on_disconnect = self.on_disconnect
      self.client.on_connect = self.on_connect
      self.client.on_message = self.on_message
      self.client.connect(broker_address)  # connect to broker
      self.client.will_set(Topics['status'], 'offline', retain=True)

      self.client.loop_start()
      self._dbusservice = VeDbusService(servicename)

      logging.debug("%s /DeviceInstance = %d" % (servicename, deviceinstance))
      
      self.instrument_inverter = minimalmodbus.Instrument(port, SERVER_ADDRESS_INVERTER)
      self.instrument_inverter.serial.baudrate = BAUDRATE
      self.instrument_inverter.serial.timeout = 0.2
      self.inverter = s5_inverter(self.instrument_inverter)

      self.instrument_boiler = minimalmodbus.Instrument(port, SERVER_ADDRESS_BOILER)
      self.boiler = WaterHeater(self.instrument_boiler)
      self.boiler.check_device_type()

      # Create the management objects, as specified in the ccgx dbus-api document
      self._dbusservice.add_path('/Mgmt/ProcessName', __file__)
      self._dbusservice.add_path('/Mgmt/ProcessVersion', 'Unkown version, and running on Python ' + platform.python_version())
      self._dbusservice.add_path('/Mgmt/Connection', connection)

      # Create the mandatory objects
      self._dbusservice.add_path('/DeviceInstance', deviceinstance)
      self._dbusservice.add_path('/ProductId', self.boiler.Device_Type) 
      self._dbusservice.add_path('/ProductName', productname)
      self._dbusservice.add_path('/FirmwareVersion', f'DSP:{self.inverter.read_dsp_version()}_LCD:{self.inverter.read_lcd_version()}')
      self._dbusservice.add_path('/HardwareVersion', self.inverter.read_type())
      self._dbusservice.add_path('/Connected', 1)

      self._dbusservice.add_path('/Ac/Power', None, writeable=True, gettextcallback=lambda a, x: "{:.0f}W".format(x), onchangecallback=self._handlechangedvalue)
      self._dbusservice.add_path('/Ac/Current', None, writeable=True, gettextcallback=lambda a, x: "{:.1f}A".format(x), onchangecallback=self._handlechangedvalue)
      self._dbusservice.add_path('/Ac/MaxPower', None, writeable=True, gettextcallback=lambda a, x: "{:.0f}W".format(x), onchangecallback=self._handlechangedvalue)
      self._dbusservice.add_path('/Ac/Energy/Forward', None, writeable=True, gettextcallback=lambda a, x: "{:.0f}kWh".format(x), onchangecallback=self._handlechangedvalue)
      self._dbusservice.add_path('/Ac/L1/Voltage', None, writeable=True, gettextcallback=lambda a, x: "{:.1f}V".format(x), onchangecallback=self._handlechangedvalue)
      self._dbusservice.add_path('/Ac/L2/Voltage', None, writeable=True, gettextcallback=lambda a, x: "{:.1f}V".format(x), onchangecallback=self._handlechangedvalue)
      self._dbusservice.add_path('/Ac/L3/Voltage', None, writeable=True, gettextcallback=lambda a, x: "{:.1f}V".format(x), onchangecallback=self._handlechangedvalue)
      self._dbusservice.add_path('/Ac/L1/Current', None, writeable=True, gettextcallback=lambda a, x: "{:.1f}A".format(x), onchangecallback=self._handlechangedvalue)
      self._dbusservice.add_path('/Ac/L2/Current', None, writeable=True, gettextcallback=lambda a, x: "{:.1f}A".format(x), onchangecallback=self._handlechangedvalue)
      self._dbusservice.add_path('/Ac/L3/Current', None, writeable=True, gettextcallback=lambda a, x: "{:.1f}A".format(x), onchangecallback=self._handlechangedvalue)
      self._dbusservice.add_path('/Ac/L1/Power', None, writeable=True, gettextcallback=lambda a, x: "{:.0f}W".format(x), onchangecallback=self._handlechangedvalue)
      self._dbusservice.add_path('/Ac/L2/Power', None, writeable=True, gettextcallback=lambda a, x: "{:.0f}W".format(x), onchangecallback=self._handlechangedvalue)
      self._dbusservice.add_path('/Ac/L3/Power', None, writeable=True, gettextcallback=lambda a, x: "{:.0f}W".format(x), onchangecallback=self._handlechangedvalue)

      self._dbusservice.add_path('/Heater/Power', None, writeable=False, gettextcallback=lambda a, x: "{:.0f}W".format(x))
      self._dbusservice.add_path('/Heater/Temperature', None, writeable=False, gettextcallback=lambda a, x: "{:.1f}°C".format(x))
      self._dbusservice.add_path('/Heater/SurplusPower', None, writeable=False, gettextcallback=lambda a, x: "{:.0f}W".format(x))
      self._dbusservice.add_path('/Heater/TargetTemperature', None, writeable=True, gettextcallback=lambda a, x: "{:.0f}°C".format(x), onchangecallback=self._handlechangedvalue)
      self._dbusservice.add_path('/Heater/PowerLimit', None, writeable=True, gettextcallback=lambda a, x: "{:.0f}W".format(x), onchangecallback=self._handlechangedvalue)

      self._dbusservice.add_path('/ErrorCode', 0, writeable=True, onchangecallback=self._handlechangedvalue)
      self._dbusservice.add_path('/StatusCode', 0, writeable=True, onchangecallback=self._handlechangedvalue)
      self._dbusservice.add_path('/Position', 0, writeable=True, onchangecallback=self._handlechangedvalue)
      self._dbusservice.add_path(path_UpdateIndex, 0, writeable=True, onchangecallback=self._handlechangedvalue)

      logging.info('Searching Gridmeter on VEBus')
      dummy = {'code': None, 'whenToLog': 'configChange', 'accessLevel': None}
      self.monitor = DbusMonitor({'com.victronenergy.grid': {'/Ac/Power': dummy}})

      # TODO settings are not working yet. they were only accepted on restart
      self.settings = SettingsDevice(
      bus=dbus.SystemBus() if (platform.machine() == 'armv7l') else dbus.SessionBus(),
      supportedSettings={'targettemperature': ['/Settings/Boiler/TargetTemperature', 50, 0, 80],
                         'powerlimit': ['/Settings/Boiler/PowerLimit', 6000, 0, 6000],}, # 0 - use grid surplus only, 1-5999 - actual limit in W, 6000 - no limit
      eventCallback=self._handlechangedvalue)
      self.boiler.target_temperature = self.settings['targettemperature'] if not None else 50
      self.power_limit = self.settings['powerlimit'] if not None else 6000

      gobject.timeout_add(1000, self._update) # pause 300ms before the next request
    
    
      # TODO implement specific messages for failing devices
    except UnknownDeviceException:
      logging.warning('No Solis Inverter detected, exiting')
      sys.exit(1)
    except minimalmodbus.NoResponseError:
      logging.critical('No Response, exiting')
      sys.exit(2)
    except Exception as e:
      logging.critical("Fatal error at %s", 'DbusPvBoilerService.__init', exc_info=e)
      sys.exit(2)

  def on_disconnect(self, client, userdata, rc):
    if rc != 0:
      logging.info('Unexpected MQTT disconnect. Will auto-reconnect')
    try:
      client.connect(self.broker_address)
      self.is_connected = True
    except Exception as e:
      logging.error("Failed to Reconnect to " + self.broker_address + " " + str(e))
      self.is_connected = False

  def on_connect(self, client, userdata, flags, rc):
    if rc == 0:
      logging.info("Connected to MQTT Broker " + self.broker_address)
      self.is_connected = True
    else:
      logging.error("Failed to connect, return code %d\n", rc)

  def on_message(self, client, userdata, msg):
    try:
      self.is_online = True
      # print(str(msg.payload.decode("utf-8")))
    except Exception as e:
      logging.warning("Message parsing error " + str(e))
      print(e)


  def _update(self):
    try:

      self.inverter.read_registers()

      self._dbusservice['/Ac/Power']          = self.inverter.registers["Active Power"][4]
      self._dbusservice['/Ac/Current']        = self.inverter.registers["A phase Current"][4]+self.inverter.registers["B phase Current"][4]+self.inverter.registers["C phase Current"][4]
      self._dbusservice['/Ac/MaxPower']       = 6000
      self._dbusservice['/Ac/Energy/Forward'] = self.inverter.registers["Energy Total"][4]
      self._dbusservice['/Ac/L1/Voltage']     = self.inverter.registers["A phase Voltage"][4]
      self._dbusservice['/Ac/L2/Voltage']     = self.inverter.registers["B phase Voltage"][4]
      self._dbusservice['/Ac/L3/Voltage']     = self.inverter.registers["C phase Voltage"][4]
      self._dbusservice['/Ac/L1/Current']     = self.inverter.registers["A phase Current"][4]
      self._dbusservice['/Ac/L2/Current']     = self.inverter.registers["B phase Current"][4]
      self._dbusservice['/Ac/L3/Current']     = self.inverter.registers["C phase Current"][4]
      self._dbusservice['/Ac/L1/Power']       = self.inverter.registers["A phase Current"][4]*self.inverter.registers["A phase Voltage"][4]
      self._dbusservice['/Ac/L2/Power']       = self.inverter.registers["B phase Current"][4]*self.inverter.registers["B phase Voltage"][4]
      self._dbusservice['/Ac/L3/Power']       = self.inverter.registers["C phase Current"][4]*self.inverter.registers["C phase Voltage"][4]
      self._dbusservice['/ErrorCode']         = 0 # TODO
      self._dbusservice['/StatusCode']        = self.inverter.read_status()

      try:
        self.inverter.set_power_limitation_absolute(self.power_limit) # limit inverter to what we can consume

        # serviceNames = self.monitor.get_service_list('com.victronenergy.grid')

        #for serviceName in serviceNames:
        #  surplus = -self.monitor.get_value(serviceName, "/Ac/Power", 0) 
        # surplus = -self.monitor.get_value(serviceName, "/Ac/Power", 0)  
        surplus = self.inverter.registers["Active Power"][4] # currently we use the current pv production, not the grid surplus
        self._dbusservice['/Heater/SurplusPower']= surplus
        self.boiler.operate(surplus)

        self._dbusservice['/Heater/Power']      = self.boiler.current_power
        self._dbusservice['/Heater/Temperature']= self.boiler.current_temperature
        self._dbusservice['/Heater/TargetTemperature']= self.boiler.target_temperature
        self._dbusservice['/Heater/PowerLimit']= self.power_limit
        self._dbusservice['/ErrorCode']         = 0
        self._dbusservice['/StatusCode']        = self.boiler.status
      except minimalmodbus.NoResponseError:
        logging.critical('Connection to Water Heater lost, exiting')
        try:
          self._dbusservice['/Heater/Power']      = None
          self._dbusservice['/Heater/Temperature']= None
          self._dbusservice['/ErrorCode']         = 2
          self._dbusservice['/StatusCode']        = None
        except Exception:
          pass
      except Exception as e:
        logging.critical("Error in Water Heater", exc_info=sys.exc_info()[0])

      try:
        self.client.publish(self.topics['pvpower'], self.inverter.registers["Active Power"][4])
        self.client.publish(self.topics['pvpowerlimit'], self.power_limit)
        self.client.publish(self.topics['status'], self.boiler.status)
        self.client.publish(self.topics['heaterpower'], self.boiler.current_power)
        self.client.publish(self.topics['heatertemperature'], self.boiler.current_temperature)
        self.client.publish(self.topics['heatertargettemperature'], self.boiler.target_temperature)
        self.client.publish(self.topics['heartbeat'], self.boiler.heartbeat)
        
      except Exception as e:
        logging.warning(f"Error on MQTT: {e}")

    except Exception as e:
      logging.info("WARNING: Could not read from Solis S5 Inverter", exc_info=sys.exc_info()[0])
      self._dbusservice['/Ac/Power']          = None
      self._dbusservice['/Ac/Current']        = None
      self._dbusservice['/Ac/MaxPower']       = None
      self._dbusservice['/Ac/Energy/Forward'] = None
      self._dbusservice['/Ac/L1/Voltage']     = None
      self._dbusservice['/Ac/L2/Voltage']     = None
      self._dbusservice['/Ac/L3/Voltage']     = None
      self._dbusservice['/Ac/L1/Current']     = None
      self._dbusservice['/Ac/L2/Current']     = None
      self._dbusservice['/Ac/L3/Current']     = None
      self._dbusservice['/Ac/L1/Power']       = None
      self._dbusservice['/Ac/L2/Power']       = None
      self._dbusservice['/Ac/L3/Power']       = None
      self._dbusservice['/ErrorCode']         = None
      self._dbusservice['/StatusCode']        = None

    # increment UpdateIndex - to show that new data is available
    self._dbusservice[path_UpdateIndex] = (self._dbusservice[path_UpdateIndex] + 1) % 255  # increment index
    return True

  def _handlechangedvalue(self, path, value):
    logging.info("someone else updated %s to %s" % (path, value))
    if path == '/Boiler/TargetTemperature':
      self.boiler.target_temperature = value if value <= 80 else 80
      return True # accept the change
    if path == '/Boiler/PowerLimit':
      self.power_limit = value if value>0 and value<=6000 else 6000
      return True
    return False

def main():
  thread.daemon = True # allow the program to quit
  logging.basicConfig(format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
                      datefmt='%Y-%m-%d %H:%M:%S',
                      level=logging.INFO,
                      handlers=[
                          logging.FileHandler(
                              "%s/current.log" % (os.path.dirname(os.path.realpath(__file__)))),
                          logging.StreamHandler()
                      ])

  try:
    logging.info("Start PV Boiler modbus service v" + str(VERSION))

    if len(sys.argv) > 1:
        port = sys.argv[1]
    else:
        logging.error("Error: no port given")
        sys.exit(4)

    from dbus.mainloop.glib import DBusGMainLoop
    # Have a mainloop, so we can send/receive asynchronous calls to and from dbus
    DBusGMainLoop(set_as_default=True)

    portname = port.split('/')[-1]
    portnumber = int(portname[-1]) if portname[-1].isdigit() else 0
    pvac_output = DbusPvBoilerService(
      port = port,
      servicename = 'com.victronenergy.pvinverter.' + portname,
      deviceinstance = 288 + portnumber,
      connection = 'Modbus RTU on ' + port,
      topics=Topics, broker_address=Broker_Address)

    logging.info('Connected to dbus, and switching over to gobject.MainLoop() (= event based)')
    mainloop = gobject.MainLoop()
    mainloop.run()

  except Exception as e:
    logging.critical('Error at %s', 'main', exc_info=e)
    sys.exit(3)

if __name__ == "__main__":
  main()
