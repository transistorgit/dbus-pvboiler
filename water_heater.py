import minimalmodbus
from time import sleep
from datetime import datetime as dt
from datetime import timedelta
import logging
import sys

MINIMUM_SWITCH_TIME = 60  # shortest allowed time between boiler switching actions


class WaterHeater:
    def __init__(self, instrument: minimalmodbus.Instrument):
        self._dbusservice = []
        self.instrument = instrument

        self.registers = {
            "Power_500W": 0,
            "Power_1000W": 1,
            "Power_2000W": 2,
            "Temperature": 0,
            "Heartbeat_Return": 1,
            "Power_Return": 2,
            "Device_Type": 3,
            "Operation_Mode": 4,  # AUTO/FORCE ON
            "Heartbeat": 0,
        }

        self.powersteps = [
            (-1000000, 499),
            (500, 999),
            (1000, 1499),
            (1500, 1999),
            (2000, 2499),
            (2500, 2999),
            (3000, 3499),
            (3500, 1000000),
        ]
        self.powercommands = [
            [0, 0, 0],
            [1, 0, 0],
            [0, 1, 0],
            [1, 1, 0],
            [0, 0, 1],
            [1, 0, 1],
            [0, 1, 1],
            [1, 1, 1],
        ]
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
                found_type = self.instrument.read_register(
                    self.registers["Device_Type"], 0, 4
                )
            except Exception as e:
                logging.warning(f"Water Heater check type: {str(e)})")
                if tried >= maxtries:
                    raise e
                sleep(1)
                continue

            if found_type == self.Device_Type:
                logging.info(f"Found Water Heater (type: {found_type:X})")
                return
        raise RuntimeError("No Device found")

    def calc_powercmd(self, grid_surplus):
        res = None
        for idx in (
            idx
            for idx, (sec, fir) in enumerate(self.powersteps)
            if sec <= grid_surplus <= fir
        ):
            res = idx
        return self.powercommands[res]

    def operate(self, grid_surplus):
        # needs to be called regularly (e.g. 1/s) to update the heartbeat

        try:
            self.instrument.write_register(
                self.registers["Heartbeat"], self.heartbeat, 0, 16
            )
            self.heartbeat += 1
            if self.heartbeat >= 100:  # must be below 1000 for the server to work
                self.heartbeat = 0

            # switch to apropriate power level, if last switching incident is longer than the allowed minimum time ago
            # short delay for small steps, long delay for steps>500W
            powerstep = abs(grid_surplus - self.last_grid_surplus)
            if powerstep <= 500:
                if (
                    dt.now() - self.lasttime_switched
                ).total_seconds() >= MINIMUM_SWITCH_TIME / 10:
                    self.cmd_bits = self.calc_powercmd(
                        grid_surplus
                    )  # calculate power setting depending on energy surplus
                    self.lasttime_switched = dt.now()
            else:
                if (
                    dt.now() - self.lasttime_switched
                ).total_seconds() >= MINIMUM_SWITCH_TIME:
                    self.cmd_bits = self.calc_powercmd(
                        grid_surplus
                    )  # calculate power setting depending on energy surplus
                    self.lasttime_switched = dt.now()

            # but stop heating if target temperature is reached
            self.current_temperature = self.instrument.read_register(
                self.registers["Temperature"], 2, 4
            )
            if self.current_temperature >= self.target_temperature:
                self.cmd_bits = [0, 0, 0]

            self.instrument.write_bits(self.registers["Power_500W"], self.cmd_bits)
            self.last_grid_surplus = grid_surplus

            self.current_power = self.instrument.read_register(
                self.registers["Power_Return"], 0, 4
            )
            self.status = self.instrument.read_register(
                self.registers["Operation_Mode"], 0, 4
            )
            self.exception_counter = 0  # reset counter after successful access

        except minimalmodbus.NoResponseError:
            logging.info(e)  # TODO remove later
            self.exception_counter = 0
        except Exception as e:
            logging.info(e)
            if self.exception_counter >= self.Max_Retries:
                raise RuntimeError(f"Water Heater critical error, exiting {e}")
            self.exception_counter += 1
