#!/bin/bash
#

. /opt/victronenergy/serial-starter/run-service.sh

# app=$(dirname $0)/dbus-pvboiler.py

# start -x -s $tty
app="python /opt/victronenergy/dbus-pvboiler/dbus-pvboiler.py"
args="/dev/$tty"
start $args
