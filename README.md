# dbus-pvboiler Service

this is a combination of dbus-solis-s5-pvinverter and dbus-water-heater in one service. 

## Purpose

This service is meant to be run on a raspberry Pi with Venus OS from Victron.

The Python script cyclically reads data from the Solis S5 PV Inverter via Modbus RTU and publishes information on the dbus, using the service name com.victronenergy.pvinverter.solis-s5. The measured values are shown in the Remote Console and can be used by Node Red.

Surplus (feed-in) electricity is used for domestic water heating by sending the power value to a boiler device, that is also connected by modbus.


For further info see the mentioned root projects.

## Install

clone or copy files to a new folder /data/etc/dbus-pvboiler/

add driver to serial starter config:
  alias default ....:pvboiler
  alias rs485 ....:pvboiler
  service pvboiler dbus-pvboiler

run install.sh

check logs in /var/log/dbus-boiler.ttyUSBx/current

## TODO

the modbus is too slow to read all wanted registers in one update cycle -> increase baud rate or read registers alternating

