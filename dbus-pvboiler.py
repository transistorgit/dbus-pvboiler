#!/usr/bin/env python

"""
Implements a PV Boiler (domestic water heater) for Venus OS 
It consists of a modbus rtu heater (arduino nano controlling 3 solid state relays for a heating element)
and a modbus rtu pv inverter of type Solis S5. The PV production is measured and the heater is controlled to use the available PV 
energy, but not more
Both devices are connected by the same modbus, so only a single serial port is used
"""
from gi.repository import GLib as gobject
import platform
import logging
import sys
import os
import dbus
import _thread as thread
import minimalmodbus
import paho.mqtt.client as mqtt

# our own packages
sys.path.insert(
    1,
    os.path.join(
        os.path.dirname(__file__),
        "/opt/victronenergy/dbus-systemcalc-py/ext/velib_python",
    ),
)
from vedbus import VeDbusService
from dbusmonitor import DbusMonitor
from settingsdevice import SettingsDevice  # available in the velib_python repository
from water_heater import WaterHeater
from solis_s5_inverter import s5_inverter

VERSION = 0.4
SERVER_ADDRESS_BOILER = 33  # Modbus ID of the Water Heater Device
SERVER_ADDRESS_INVERTER = 1  # Modbus ID of the PV Inverter
BAUDRATE = 9600
GRIDMETER_KEY_WORD = "com.victronenergy.grid"
POWER_LIMIT = 3710  # size of our heating element + offset + control margin
SURPLUS_OFFSET = 200  # offset that must be generated more than the boiler would consume

Broker_Address = "192.168.168.112"
InverterType = "pvboiler"
Topics = {
    "pvpower": "iot/pv/solis/ac_active_power_kW",
    "pvpowerlimit": "iot/pv/solis/powerlimit",
    "status": "iot/pv/boiler/service",
    "heaterpower": "iot/pv/boiler/power",
    "heatertemperature": "iot/pv/boiler/temperature",
    "heatertargettemperature": "iot/pv/boiler/targettemperature",
    "heartbeat": "iot/pv/boiler/heartbeat",
}

path_UpdateIndex = "/UpdateIndex"


class DbusPvBoilerService:
    def __init__(
        self,
        port,
        servicename,
        deviceinstance=288,
        productname="PV Boiler",
        connection="unknown",
        topics="/my/pv/inverter",
        broker_address="127.0.0.1",
    ):
        try:
            self.boiler_is_optional = True  # optionally, use this driver just as a inverter monitor. TODO make this configurable
            self.broker_address = broker_address
            self.is_connected = False
            self.is_online = False
            self.topics = topics
            self.client = mqtt.Client("Venus_PV_Boiler")
            self.client.on_disconnect = self.on_disconnect
            self.client.on_connect = self.on_connect
            self.client.on_message = self.on_message
            self.client.connect(broker_address)  # connect to broker
            self.client.will_set(Topics["status"], "offline", retain=True)

            self.client.loop_start()
            self._dbusservice = VeDbusService(servicename)

            logging.debug("%s /DeviceInstance = %d" % (servicename, deviceinstance))

            self.instrument_inverter = minimalmodbus.Instrument(
                port, SERVER_ADDRESS_INVERTER
            )
            self.instrument_inverter.serial.baudrate = BAUDRATE
            self.instrument_inverter.serial.timeout = 0.2
            self.inverter = s5_inverter(self.instrument_inverter)
            self.inverter.set_power_limitation_absolute(
                POWER_LIMIT
            )  # limit inverter to what we can consume technically

            self.instrument_boiler = minimalmodbus.Instrument(
                port, SERVER_ADDRESS_BOILER
            )
            self.boiler = WaterHeater(self.instrument_boiler)

            try:
                self.boiler.check_device_type()
            except Exception as e:
                if self.boiler_is_optional:
                    pass
                else:
                    raise e

            # Create the management objects, as specified in the ccgx dbus-api document
            self._dbusservice.add_path("/Mgmt/ProcessName", __file__)
            self._dbusservice.add_path(
                "/Mgmt/ProcessVersion",
                "Unkown version, and running on Python " + platform.python_version(),
            )
            self._dbusservice.add_path("/Mgmt/Connection", connection)

            # Create the mandatory objects
            self._dbusservice.add_path("/DeviceInstance", deviceinstance)
            self._dbusservice.add_path("/ProductId", self.boiler.Device_Type)
            self._dbusservice.add_path("/ProductName", productname)
            self._dbusservice.add_path(
                "/FirmwareVersion",
                f"DSP:{self.inverter.read_dsp_version()}_LCD:{self.inverter.read_lcd_version()}",
            )
            self._dbusservice.add_path("/HardwareVersion", self.inverter.read_type())
            self._dbusservice.add_path("/Connected", 1)

            self._dbusservice.add_path(
                "/Ac/Power",
                None,
                writeable=True,
                gettextcallback=lambda a, x: "{:.0f}W".format(x),
                onchangecallback=self._handlechangedvalue,
            )
            self._dbusservice.add_path(
                "/Ac/Current",
                None,
                writeable=True,
                gettextcallback=lambda a, x: "{:.1f}A".format(x),
                onchangecallback=self._handlechangedvalue,
            )
            self._dbusservice.add_path(
                "/Ac/MaxPower",
                None,
                writeable=True,
                gettextcallback=lambda a, x: "{:.0f}W".format(x),
                onchangecallback=self._handlechangedvalue,
            )
            self._dbusservice.add_path(
                "/Ac/Energy/Forward",
                None,
                writeable=True,
                gettextcallback=lambda a, x: "{:.0f}kWh".format(x),
                onchangecallback=self._handlechangedvalue,
            )
            self._dbusservice.add_path(
                "/Ac/L1/Voltage",
                None,
                writeable=True,
                gettextcallback=lambda a, x: "{:.1f}V".format(x),
                onchangecallback=self._handlechangedvalue,
            )
            self._dbusservice.add_path(
                "/Ac/L2/Voltage",
                None,
                writeable=True,
                gettextcallback=lambda a, x: "{:.1f}V".format(x),
                onchangecallback=self._handlechangedvalue,
            )
            self._dbusservice.add_path(
                "/Ac/L3/Voltage",
                None,
                writeable=True,
                gettextcallback=lambda a, x: "{:.1f}V".format(x),
                onchangecallback=self._handlechangedvalue,
            )
            self._dbusservice.add_path(
                "/Ac/L1/Current",
                None,
                writeable=True,
                gettextcallback=lambda a, x: "{:.1f}A".format(x),
                onchangecallback=self._handlechangedvalue,
            )
            self._dbusservice.add_path(
                "/Ac/L2/Current",
                None,
                writeable=True,
                gettextcallback=lambda a, x: "{:.1f}A".format(x),
                onchangecallback=self._handlechangedvalue,
            )
            self._dbusservice.add_path(
                "/Ac/L3/Current",
                None,
                writeable=True,
                gettextcallback=lambda a, x: "{:.1f}A".format(x),
                onchangecallback=self._handlechangedvalue,
            )
            self._dbusservice.add_path(
                "/Ac/L1/Power",
                None,
                writeable=True,
                gettextcallback=lambda a, x: "{:.0f}W".format(x),
                onchangecallback=self._handlechangedvalue,
            )
            self._dbusservice.add_path(
                "/Ac/L2/Power",
                None,
                writeable=True,
                gettextcallback=lambda a, x: "{:.0f}W".format(x),
                onchangecallback=self._handlechangedvalue,
            )
            self._dbusservice.add_path(
                "/Ac/L3/Power",
                None,
                writeable=True,
                gettextcallback=lambda a, x: "{:.0f}W".format(x),
                onchangecallback=self._handlechangedvalue,
            )

            self._dbusservice.add_path(
                "/Heater/Power",
                None,
                writeable=False,
                gettextcallback=lambda a, x: "{:.0f}W".format(x),
            )
            self._dbusservice.add_path(
                "/Heater/Temperature",
                None,
                writeable=False,
                gettextcallback=lambda a, x: "{:.1f}°C".format(x),
            )
            self._dbusservice.add_path(
                "/Heater/SurplusPower",
                None,
                writeable=False,
                gettextcallback=lambda a, x: "{:.0f}W".format(x),
            )
            self._dbusservice.add_path(
                "/Heater/TargetTemperature",
                None,
                writeable=True,
                gettextcallback=lambda a, x: "{:.0f}°C".format(x),
                onchangecallback=self._handlechangedvalue,
            )
            self._dbusservice.add_path(
                "/Heater/PowerLimit",
                None,
                writeable=True,
                gettextcallback=lambda a, x: "{:.0f}W".format(x),
                onchangecallback=self._handlechangedvalue,
            )

            self._dbusservice.add_path(
                "/ErrorCode",
                0,
                writeable=True,
                onchangecallback=self._handlechangedvalue,
            )
            self._dbusservice.add_path(
                "/StatusCode",
                0,
                writeable=True,
                onchangecallback=self._handlechangedvalue,
            )
            self._dbusservice.add_path(
                "/Position",
                0,
                writeable=True,
                onchangecallback=self._handlechangedvalue,
            )
            self._dbusservice.add_path(
                path_UpdateIndex,
                0,
                writeable=True,
                onchangecallback=self._handlechangedvalue,
            )

            logging.info("Searching Gridmeter on VEBus")
            dummy = {"code": None, "whenToLog": "configChange", "accessLevel": None}
            self.monitor = DbusMonitor({"com.victronenergy.grid": {"/Ac/Power": dummy}})

            # changing settings in dbus-spy triggers a restart. is this intended?
            self.settings = SettingsDevice(
                bus=dbus.SystemBus()
                if (platform.machine() == "armv7l")
                else dbus.SessionBus(),
                supportedSettings={
                    "targettemperature": [
                        "/Settings/Heater/TargetTemperature",
                        50,
                        0,
                        80,
                    ],
                    "powerlimit": [
                        "/Settings/Heater/PowerLimit",
                        self.inverter.rated_power,
                        0,
                        self.inverter.rated_power,
                    ],
                },  # 0 - use grid surplus only, 1-5999 - actual limit in W, 6000 - no limit
                eventCallback=self._handlechangedvalue,
            )
            self.boiler.target_temperature = (
                self.settings["targettemperature"] if not None else 50
            )

            gobject.timeout_add(
                1000, self._update
            )  # pause 300ms before the next request

        except RuntimeError:
            logging.warning("Critical Error, exiting")
            sys.exit(1)
        except minimalmodbus.NoResponseError:
            logging.critical("No Response, exiting")
            sys.exit(2)
        except Exception as e:
            logging.critical(
                "Fatal error at %s", "DbusPvBoilerService.__init", exc_info=e
            )
            sys.exit(3)

    def on_disconnect(self, client, userdata, rc):
        if rc != 0:
            logging.info("Unexpected MQTT disconnect. Will auto-reconnect")
        try:
            client.connect(self.broker_address)
            self.is_connected = True
        except Exception as e:
            logging.error(
                "Failed to Reconnect to " + self.broker_address + " " + str(e)
            )
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
            # step 1: fetch energy data
            self.inverter.read_registers()
            self._dbusservice["/Ac/Power"] = self.inverter.registers["Active Power"][4]
            self._dbusservice["/Ac/Current"] = (
                self.inverter.registers["A phase Current"][4]
                + self.inverter.registers["B phase Current"][4]
                + self.inverter.registers["C phase Current"][4]
            )
            self._dbusservice["/Ac/MaxPower"] = self.inverter.rated_power
            self._dbusservice["/Ac/Energy/Forward"] = self.inverter.registers[
                "Energy Total"
            ][4]
            self._dbusservice["/Ac/L1/Voltage"] = self.inverter.registers[
                "A phase Voltage"
            ][4]
            self._dbusservice["/Ac/L2/Voltage"] = self.inverter.registers[
                "B phase Voltage"
            ][4]
            self._dbusservice["/Ac/L3/Voltage"] = self.inverter.registers[
                "C phase Voltage"
            ][4]
            self._dbusservice["/Ac/L1/Current"] = self.inverter.registers[
                "A phase Current"
            ][4]
            self._dbusservice["/Ac/L2/Current"] = self.inverter.registers[
                "B phase Current"
            ][4]
            self._dbusservice["/Ac/L3/Current"] = self.inverter.registers[
                "C phase Current"
            ][4]
            self._dbusservice["/Ac/L1/Power"] = (
                self.inverter.registers["A phase Current"][4]
                * self.inverter.registers["A phase Voltage"][4]
            )
            self._dbusservice["/Ac/L2/Power"] = (
                self.inverter.registers["B phase Current"][4]
                * self.inverter.registers["B phase Voltage"][4]
            )
            self._dbusservice["/Ac/L3/Power"] = (
                self.inverter.registers["C phase Current"][4]
                * self.inverter.registers["C phase Voltage"][4]
            )
            self._dbusservice["/ErrorCode"] = 0  # TODO
            self._dbusservice["/StatusCode"] = self.inverter.read_status()

        except Exception as e:
            logging.info(
                "WARNING: Could not read from Solis S5 Inverter",
                exc_info=sys.exc_info()[0],
            )
            self._dbusservice["/Ac/Power"] = None
            self._dbusservice["/Ac/Current"] = None
            self._dbusservice["/Ac/MaxPower"] = None
            self._dbusservice["/Ac/Energy/Forward"] = None
            self._dbusservice["/Ac/L1/Voltage"] = None
            self._dbusservice["/Ac/L2/Voltage"] = None
            self._dbusservice["/Ac/L3/Voltage"] = None
            self._dbusservice["/Ac/L1/Current"] = None
            self._dbusservice["/Ac/L2/Current"] = None
            self._dbusservice["/Ac/L3/Current"] = None
            self._dbusservice["/Ac/L1/Power"] = None
            self._dbusservice["/Ac/L2/Power"] = None
            self._dbusservice["/Ac/L3/Power"] = None
            self._dbusservice["/ErrorCode"] = None
            self._dbusservice["/StatusCode"] = None
            sys.exit(4)

        # step 2: control boiler to use that energy
        try:
            serviceNames = self.monitor.get_service_list('com.victronenergy.grid')
            # for serviceName in serviceNames:
            #  surplus = -self.monitor.get_value(serviceName, "/Ac/Power", 0)

            # grid feed-in is counted negative. so we negate it to get the actual surplus value as positive number.
            # use max() to clamp it to positive range
            surplus = max(0,-self.monitor.get_value(serviceName, "/Ac/Power", 0)) 
            self._dbusservice["/Heater/SurplusPower"] = surplus
            self.boiler.operate(surplus - SURPLUS_OFFSET)

            self._dbusservice["/Heater/Power"] = self.boiler.current_power
            self._dbusservice["/Heater/Temperature"] = self.boiler.current_temperature
            self._dbusservice[
                "/Heater/TargetTemperature"
            ] = self.boiler.target_temperature
            # self._dbusservice["/ErrorCode"] = 0
            # self._dbusservice["/StatusCode"] = self.boiler.status # is already written by inverter
        except Exception as e:
            try:
                self._dbusservice["/Heater/Power"] = None
                self._dbusservice["/Heater/Temperature"] = None
                self._dbusservice["/ErrorCode"] = 5
                self._dbusservice["/StatusCode"] = None
            except Exception:
                pass
            logging.critical("Error in Water Heater", exc_info=sys.exc_info()[0])
            if self.boiler_is_optional:
                pass
            else:
                sys.exit(5)

        try:
            self.client.publish(
                self.topics["pvpower"], self.inverter.registers["Active Power"][4]
            )
            self.client.publish(self.topics["status"], self.boiler.status)
            self.client.publish(self.topics["heaterpower"], self.boiler.current_power)
            self.client.publish(
                self.topics["heatertemperature"], self.boiler.current_temperature
            )
            self.client.publish(
                self.topics["heatertargettemperature"], self.boiler.target_temperature
            )
            self.client.publish(self.topics["heartbeat"], self.boiler.heartbeat)
        except Exception as e:
            logging.warning(f"MQTT failure: {e}")
            pass  #  mqtt is optional

        # increment UpdateIndex - to show that new data is available
        self._dbusservice[path_UpdateIndex] = (
            self._dbusservice[path_UpdateIndex] + 1
        ) % 255  # increment index
        return True

    def _handlechangedvalue(self, path, value):
        logging.info("someone else updated %s to %s" % (path, value))
        if path == "/Heater/TargetTemperature":
            self.boiler.target_temperature = value if value <= 80 else 80
            return True  # accept the change
        return False


def main():
    thread.daemon = True  # allow the program to quit
    logging.basicConfig(
        format="%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=logging.INFO,
        handlers=[
            logging.FileHandler(
                "%s/current.log" % (os.path.dirname(os.path.realpath(__file__)))
            ),
            logging.StreamHandler(),
        ],
    )

    try:
        logging.info("+++++ Start PV Boiler modbus service v" + str(VERSION))

        if len(sys.argv) > 1:
            port = sys.argv[1]
        else:
            logging.error("Error: no port given")
            sys.exit(6)

        from dbus.mainloop.glib import DBusGMainLoop

        # Have a mainloop, so we can send/receive asynchronous calls to and from dbus
        DBusGMainLoop(set_as_default=True)

        portname = port.split("/")[-1]
        portnumber = int(portname[-1]) if portname[-1].isdigit() else 0
        pvac_output = DbusPvBoilerService(
            port=port,
            servicename="com.victronenergy.pvinverter." + portname,
            deviceinstance=288 + portnumber,
            connection="Modbus RTU on " + port,
            topics=Topics,
            broker_address=Broker_Address,
        )

        logging.info(
            "Connected to dbus, and switching over to gobject.MainLoop() (= event based)"
        )
        mainloop = gobject.MainLoop()
        mainloop.run()

    except Exception as e:
        logging.critical("Error at %s", "main", exc_info=e)
        sys.exit(7)


if __name__ == "__main__":
    main()
