#!/usr/bin/python

import psutil
import logging
import os
from time import sleep
import sys

def get_pid(name):
  for proc in psutil.process_iter(['pid', 'name']):
    if proc.info['name'] == name:
      return proc.info['pid']
  return None

def get_mem_info(proc_names, format='text'):
  mem_string = ""
  for name in proc_names:
    pid = get_pid(name)

    p = psutil.Process(pid=pid)
    mem_info = p.memory_full_info()
    rss = mem_info.rss
    data = mem_info.data

    if format == 'csv':
      mem_string += f"{rss},{data},"
    else:
      mem_string += f"{name}:({rss:,} {data:,}),"

  if len(mem_string):
    return mem_string[:-1]
  return mem_string


def main():
  if len(sys.argv)>1 and sys.argv[1] == '--csv':
    logging.basicConfig(format='%(asctime)s,%(message)s',
                      datefmt='%Y-%m-%d,%H:%M:%S',
                      level=logging.INFO,
                      handlers=[
                          logging.FileHandler(
                              "%s/procmon.log" % (os.path.dirname(os.path.realpath(__file__)))),
                          logging.StreamHandler()
                      ])

  else:
    logging.basicConfig(format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
                      datefmt='%Y-%m-%d %H:%M:%S',
                      level=logging.INFO,
                      handlers=[
                          logging.FileHandler(
                              "%s/procmon.log" % (os.path.dirname(os.path.realpath(__file__)))),
                          logging.StreamHandler()
                      ])


  while True:
    load_percent = psutil.cpu_percent()
    load_1min, load_5min, load_15min = psutil.getloadavg()

    used_mem = psutil.virtual_memory().used

    if len(sys.argv)>1 and sys.argv[1] == '--csv':
      mem_string = get_mem_info(("dbus-daemon", "dbus-pvboiler.py"), 'csv')
      logging.info(f"{load_percent},{used_mem}," + mem_string)
    else:
      mem_string = get_mem_info(("dbus-daemon", "dbus-pvboiler.py"))
      logging.info(f"CPU:{load_percent}%, Load:({load_1min},{load_5min},{load_15min}), Used_Mem:{used_mem:,}, " + mem_string)

    sleep(10)

if __name__ == "__main__":
  main()


